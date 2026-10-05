import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import ssl
import threading
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import parse_qs, unquote, urlsplit

import pg8000.dbapi

DATABASE_URL = "postgresql://neondb_owner:npg_douv1lEHbm9q@ep-dawn-unit-b4ea65ij-pooler.c-6.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
PROJECT_ROLES = ("kysu", "qs", "giamsat", "chudautu")
CONNECTION_TIMEOUT_SECONDS = 30
CONNECTION_ATTEMPTS = 3

IntegrityError = sqlite3.IntegrityError

LOCAL_TABLES = (
    "users",
    "projects",
    "project_members",
    "email_inbox",
    "project_invitations",
    "work_standards",
    "acceptance_records",
    "acceptance_settings",
    "project_progress",
    "log_settings",
    "nhat_ky",
)
SERIAL_TABLES = (
    "users",
    "projects",
    "email_inbox",
    "project_invitations",
    "work_standards",
    "acceptance_records",
    "project_progress",
    "nhat_ky",
)
LOCAL_DATABASE_PATH = Path("du_lieu_thi_cong.db")
SYNC_DATABASE_PATH = Path("du_lieu_thi_cong.db.sync")
_cloud_schema_initialized = False
_sync_thread = None


def _convert_placeholders(query):
    converted = []
    index = 0
    quote = None
    line_comment = False
    block_comment_depth = 0
    dollar_quote = None

    while index < len(query):
        char = query[index]
        next_chars = query[index:index + 2]

        if line_comment:
            converted.append(char)
            if char == "\n":
                line_comment = False
        elif block_comment_depth:
            if next_chars == "/*":
                converted.append(next_chars)
                index += 1
                block_comment_depth += 1
            elif next_chars == "*/":
                converted.append(next_chars)
                index += 1
                block_comment_depth -= 1
            else:
                converted.append(char)
        elif dollar_quote:
            if query.startswith(dollar_quote, index):
                converted.append(dollar_quote)
                index += len(dollar_quote) - 1
                dollar_quote = None
            else:
                converted.append(char)
        elif quote:
            converted.append(char)
            if char == "\\" and index + 1 < len(query):
                index += 1
                converted.append(query[index])
            elif char == quote:
                if index + 1 < len(query) and query[index + 1] == quote:
                    index += 1
                    converted.append(query[index])
                else:
                    quote = None
        elif next_chars == "--":
            converted.append(next_chars)
            index += 1
            line_comment = True
        elif next_chars == "/*":
            converted.append(next_chars)
            index += 1
            block_comment_depth = 1
        elif char in ("'", '"'):
            quote = char
            converted.append(char)
        elif char == "$":
            tag_end = query.find("$", index + 1)
            if tag_end != -1:
                tag = query[index:tag_end + 1]
                tag_body = tag[1:-1]
                if not tag_body or (
                    (tag_body[0].isalpha() or tag_body[0] == "_")
                    and all(c.isalnum() or c == "_" for c in tag_body)
                ):
                    dollar_quote = tag
                    converted.append(tag)
                    index = tag_end
                else:
                    converted.append(char)
            else:
                converted.append(char)
        elif char == "?":
            converted.append("%s")
        else:
            converted.append(char)
        index += 1

    return "".join(converted)


class DatabaseCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, vars=None):
        self._cursor.execute(_convert_placeholders(query), vars or ())
        return self

    def executemany(self, query, vars_list):
        self._cursor.executemany(_convert_placeholders(query), vars_list or [])
        return self

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class DatabaseConnection:
    def __init__(self, connection):
        self._connection = connection

    def run_transaction(self, operation):
        try:
            result = operation(self)
            self._connection.commit()
            return result
        except Exception:
            try:
                self._connection.rollback()
            except Exception:
                logging.exception("Không thể rollback transaction database.")
            raise

    def cursor(self, *args, **kwargs):
        return DatabaseCursor(self._connection.cursor(*args, **kwargs))

    def execute(self, query, vars=None):
        cursor = self.cursor()
        return cursor.execute(query, vars)

    def __getattr__(self, name):
        return getattr(self._connection, name)


class DeferredDatabaseConnection:
    def __init__(self):
        self._ready = threading.Event()
        self._connection = None
        self._error = None

    def set_connection(self, connection):
        self._connection = connection
        self._ready.set()

    def set_error(self, error):
        self._error = error
        self._ready.set()

    def _get_connection(self):
        self._ready.wait()
        if self._error is not None:
            raise self._error
        return self._connection

    def cursor(self, *args, **kwargs):
        return self._get_connection().cursor(*args, **kwargs)

    def execute(self, query, vars=None):
        return self._get_connection().execute(query, vars)

    def __getattr__(self, name):
        return getattr(self._get_connection(), name)


class LocalDatabaseCursor:
    def __init__(self, connection, cursor):
        self._connection = connection
        self._cursor = cursor
        self._returning_rows = None
        self._returning_row_index = 0

    def execute(self, query, vars=None):
        with self._connection._lock:
            try:
                self._returning_rows = None
                self._returning_row_index = 0
                self._cursor.execute(query, () if vars is None else vars)
                self._connection._capture(query, vars)
                if re.search(r"\bRETURNING\b", query, re.IGNORECASE):
                    self._returning_rows = self._cursor.fetchall()
                self._connection._commit_write_if_needed(query)
            except Exception:
                if not self._connection._transaction_depth:
                    self._connection.rollback()
                raise
        return self

    def executemany(self, query, vars_list):
        values = list(vars_list or [])
        with self._connection._lock:
            try:
                self._cursor.executemany(query, values)
                self._connection._capture(query, values, many=True)
                self._connection._commit_write_if_needed(query)
            except Exception:
                if not self._connection._transaction_depth:
                    self._connection.rollback()
                raise
        return self

    def fetchone(self):
        with self._connection._lock:
            if self._returning_rows is not None:
                if self._returning_row_index >= len(self._returning_rows):
                    return None
                row = self._returning_rows[self._returning_row_index]
                self._returning_row_index += 1
                return row
            return self._cursor.fetchone()

    def fetchmany(self, size=None):
        with self._connection._lock:
            if self._returning_rows is not None:
                if size is None:
                    size = self._cursor.arraysize
                start = self._returning_row_index
                end = min(start + size, len(self._returning_rows))
                self._returning_row_index = end
                return self._returning_rows[start:end]
            if size is None:
                return self._cursor.fetchmany()
            return self._cursor.fetchmany(size)

    def fetchall(self):
        with self._connection._lock:
            if self._returning_rows is not None:
                rows = self._returning_rows[self._returning_row_index:]
                self._returning_row_index = len(self._returning_rows)
                return rows
            return self._cursor.fetchall()

    def __getattr__(self, name):
        return getattr(self._cursor, name)


class LocalDatabaseConnection:
    def __init__(self, connection, sync_path):
        self._connection = connection
        self._sync_path = sync_path
        self._lock = threading.RLock()
        self._pending_statements = []
        self._pending_batch_id = None
        self._transaction_depth = 0

    def run_transaction(self, operation):
        with self._lock:
            if self._transaction_depth:
                raise RuntimeError("Không hỗ trợ transaction SQLite lồng nhau.")
            self._transaction_depth = 1
            try:
                result = operation(self)
            except Exception:
                self._transaction_depth = 0
                try:
                    self.rollback()
                except Exception:
                    logging.exception("Không thể rollback transaction SQLite.")
                raise
            self._transaction_depth = 0
            try:
                self.commit()
            except Exception:
                try:
                    self.rollback()
                except Exception:
                    logging.exception("Không thể rollback transaction SQLite.")
                raise
            return result

    def _commit_write_if_needed(self, query):
        command = query.lstrip().split(None, 1)[0].upper() if query.strip() else ""
        if command in ("INSERT", "UPDATE", "DELETE", "REPLACE"):
            if not self._transaction_depth:
                self.commit()

    def _capture(self, query, parameters, many=False):
        command = query.lstrip().split(None, 1)[0].upper() if query.strip() else ""
        if command not in ("INSERT", "UPDATE", "DELETE", "REPLACE"):
            return
        if many:
            if not parameters:
                return
            values = [[_json_value(value) for value in row] for row in parameters]
        else:
            values = [_json_value(value) for value in (parameters or ())]
        self._pending_statements.append(
            {"query": query, "parameters": values, "many": many}
        )

    def cursor(self, *args, **kwargs):
        with self._lock:
            return LocalDatabaseCursor(
                self, self._connection.cursor(*args, **kwargs)
            )

    def execute(self, query, vars=None):
        return self.cursor().execute(query, vars)

    def commit(self):
        with self._lock:
            if self._pending_statements:
                if self._pending_batch_id is None:
                    self._pending_batch_id = str(uuid.uuid4())
                payload = json.dumps(
                    self._pending_statements, ensure_ascii=False, separators=(",", ":")
                )
                self._connection.execute(
                    "INSERT OR IGNORE INTO sync_store.pending_batches "
                    "(batch_id, payload) VALUES (?, ?)",
                    (self._pending_batch_id, payload),
                )
            self._connection.commit()
            self._pending_statements.clear()
            self._pending_batch_id = None

    def rollback(self):
        with self._lock:
            self._connection.rollback()
            self._pending_statements.clear()
            self._pending_batch_id = None

    def _outbox_connection(self):
        connection = sqlite3.connect(self._sync_path, timeout=30)
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def next_pending_batch(self):
        outbox = self._outbox_connection()
        try:
            return outbox.execute(
                "SELECT batch_id, payload FROM pending_batches ORDER BY id LIMIT 1"
            ).fetchone()
        finally:
            outbox.close()

    def has_pending_batches(self):
        outbox = self._outbox_connection()
        try:
            return outbox.execute(
                "SELECT 1 FROM pending_batches LIMIT 1"
            ).fetchone() is not None
        finally:
            outbox.close()

    def has_cloud_snapshot(self):
        outbox = self._outbox_connection()
        try:
            row = outbox.execute(
                "SELECT value FROM sync_meta "
                "WHERE key = 'cloud_snapshot_initialized'"
            ).fetchone()
            return row is not None and row[0] == "1"
        finally:
            outbox.close()

    def acknowledge_batch(self, batch_id):
        outbox = self._outbox_connection()
        try:
            outbox.execute(
                "DELETE FROM pending_batches WHERE batch_id = ?", (batch_id,)
            )
            outbox.commit()
        finally:
            outbox.close()

    def refresh_from_remote(self, remote_connection):
        remote_rows = {}
        remote_sequences = {}
        for table in LOCAL_TABLES:
            local_columns = [
                row[1]
                for row in self._connection.execute(
                    f'PRAGMA table_info("{table}")'
                ).fetchall()
            ]
            if not local_columns:
                raise RuntimeError(f"Bảng SQLite cục bộ không tồn tại: {table}")
            cursor = remote_connection.execute(f'SELECT * FROM "{table}"')
            remote_columns = [column[0] for column in cursor.description]
            if set(local_columns) != set(remote_columns):
                raise RuntimeError(
                    f"Cấu trúc bảng {table} trên SQLite và NeonCloud không khớp."
                )
            indexes = [remote_columns.index(name) for name in local_columns]
            remote_rows[table] = [
                tuple(_sqlite_value(row[index]) for index in indexes)
                for row in cursor.fetchall()
            ]

        for table in SERIAL_TABLES:
            sequence = remote_connection.execute(
                f'SELECT last_value, is_called FROM "{table}_id_seq"'
            ).fetchone()
            remote_sequences[table] = (
                max(sequence[0] - 1, 0) if not sequence[1] else sequence[0]
            )

        with self._lock:
            if self._pending_statements or self.has_pending_batches():
                return False
            self._connection.execute("BEGIN")
            try:
                for table in reversed(LOCAL_TABLES):
                    self._connection.execute(f'DELETE FROM "{table}"')
                for table in LOCAL_TABLES:
                    columns = [
                        row[1]
                        for row in self._connection.execute(
                            f'PRAGMA table_info("{table}")'
                        ).fetchall()
                    ]
                    quoted_columns = ", ".join(f'"{name}"' for name in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    self._connection.executemany(
                        f'INSERT INTO "{table}" ({quoted_columns}) '
                        f"VALUES ({placeholders})",
                        remote_rows[table],
                    )
                for table, sequence in remote_sequences.items():
                    updated = self._connection.execute(
                        "UPDATE sqlite_sequence SET seq = ? WHERE name = ?",
                        (sequence, table),
                    )
                    if updated.rowcount == 0:
                        self._connection.execute(
                            "INSERT INTO sqlite_sequence (name, seq) VALUES (?, ?)",
                            (table, sequence),
                        )
                self._connection.execute(
                    "INSERT OR REPLACE INTO sync_store.sync_meta (key, value) "
                    "VALUES ('cloud_snapshot_initialized', '1')"
                )
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise
        return True

    def __getattr__(self, name):
        return getattr(self._connection, name)


def _json_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__bytes__": bytes(value).hex()}
    if hasattr(value, "isoformat"):
        return {"__datetime__": value.isoformat()}
    raise TypeError(f"Không thể đưa kiểu tham số {type(value).__name__} vào hàng đợi đồng bộ.")


def _sqlite_value(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, memoryview):
        return bytes(value)
    return value


def _from_json_value(value):
    if isinstance(value, dict):
        if set(value) == {"__bytes__"}:
            return bytes.fromhex(value["__bytes__"])
        if set(value) == {"__datetime__"}:
            return datetime.fromisoformat(value["__datetime__"])
    return value


def _retry_safe_insert(query):
    if not re.match(r"\s*INSERT\s+INTO\b", query, re.IGNORECASE):
        return query
    if re.search(r"\bON\s+CONFLICT\b", query, re.IGNORECASE):
        return query
    returning = re.search(r"\bRETURNING\b", query, re.IGNORECASE)
    if returning:
        return (
            query[:returning.start()].rstrip()
            + " ON CONFLICT DO NOTHING "
            + query[returning.start():]
        )
    return query.rstrip() + " ON CONFLICT DO NOTHING"


def _ensure_local_schema(connection):
    tables = (
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            password TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ten_du_an TEXT NOT NULL,
            hang_muc TEXT,
            dia_diem TEXT,
            chu_dau_tu TEXT,
            nha_thau TEXT,
            tu_van_giam_sat TEXT,
            goi_thau TEXT,
            hop_dong_so TEXT,
            ngay_bat_dau TEXT,
            ngay_ket_thuc_du_kien TEXT,
            mo_ta TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS project_members (
            project_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (project_id, user_id),
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS email_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipient TEXT,
            sender TEXT,
            subject TEXT,
            body TEXT,
            project_id INTEGER,
            is_read INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            invitation_id INTEGER
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS project_invitations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            inviter_user_id INTEGER,
            invitee_email TEXT,
            role TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS work_standards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ten_cong_viec TEXT,
            nhom_cong_viec TEXT,
            don_vi TEXT,
            dinh_muc TEXT,
            mo_ta TEXT,
            created_by TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            may_thi_cong TEXT DEFAULT '',
            tcvn TEXT DEFAULT '',
            pccc TEXT DEFAULT '',
            tieu_chi_nghiem_thu TEXT DEFAULT ''
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS acceptance_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            loai_phieu TEXT,
            tieu_de TEXT,
            hang_muc TEXT,
            vi_tri TEXT,
            ngay_yeu_cau TEXT,
            ngay_nghiem_thu TEXT,
            trang_thai TEXT DEFAULT 'Chờ nghiệm thu',
            thanh_phan_tham_du TEXT,
            noi_dung TEXT,
            ghi_chu TEXT,
            created_by TEXT,
            created_at TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS acceptance_settings (
            project_id INTEGER PRIMARY KEY,
            mau_bien_ban TEXT,
            tieu_de TEXT,
            don_vi_nghiem_thu TEXT,
            nguoi_ky_1 TEXT,
            nguoi_ky_2 TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS project_progress (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            ten_hang_muc TEXT,
            phan_tram REAL DEFAULT 0,
            trang_thai TEXT DEFAULT 'Đang thi công',
            ngay_cap_nhat TEXT,
            ngay_bat_dau TEXT,
            ngay_ket_thuc TEXT,
            nhan_cong TEXT,
            tao_bien_ban INTEGER,
            ngay_nghiem_thu TEXT,
            gio_nghiem_thu TEXT,
            may_thi_cong TEXT,
            tcvn TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS log_settings (
            project_id INTEGER PRIMARY KEY,
            tieu_de TEXT DEFAULT 'NHẬT KÝ THI CÔNG',
            hien_thi_ky_1 INTEGER DEFAULT 1,
            don_vi_1 TEXT DEFAULT 'NHÀ THẦU THI CÔNG',
            nguoi_ky_1 TEXT DEFAULT '',
            hien_thi_ky_2 INTEGER DEFAULT 1,
            don_vi_2 TEXT DEFAULT 'TƯ VẤN GIÁM SÁT',
            nguoi_ky_2 TEXT DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS nhat_ky (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER,
            created_by TEXT,
            hang_muc TEXT,
            mo_ta TEXT,
            duong_dan_anh TEXT,
            thoi_gian TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ca_thi_cong TEXT,
            so_cong_nhan TEXT,
            may_moc TEXT,
            ngay_thi_cong TEXT,
            thoi_tiet TEXT,
            nhiet_do_sang TEXT,
            nhiet_do_chieu TEXT,
            nhiet_do_toi TEXT,
            thoi_tiet_sang TEXT,
            thoi_tiet_chieu TEXT,
            thoi_tiet_toi TEXT,
            cb_ky_thuat TEXT DEFAULT '',
            cong_viec_nghiem_thu TEXT DEFAULT '',
            ve_sinh_mt TEXT DEFAULT 'Tốt',
            an_toan_ld TEXT DEFAULT 'Tốt',
            su_co TEXT DEFAULT 'Không',
            kien_nghi TEXT DEFAULT 'Không'
        )
        """,
    )
    for statement in tables:
        connection.execute(statement)

    additive_columns = {
        "work_standards": {
            "may_thi_cong": "TEXT DEFAULT ''",
            "tcvn": "TEXT DEFAULT ''",
            "pccc": "TEXT DEFAULT ''",
            "tieu_chi_nghiem_thu": "TEXT DEFAULT ''",
        },
        "project_progress": {
            "ngay_bat_dau": "TEXT",
            "ngay_ket_thuc": "TEXT",
            "nhan_cong": "TEXT",
            "tao_bien_ban": "INTEGER",
            "ngay_nghiem_thu": "TEXT",
            "gio_nghiem_thu": "TEXT",
            "may_thi_cong": "TEXT",
            "tcvn": "TEXT",
        },
        "nhat_ky": {
            "cb_ky_thuat": "TEXT DEFAULT ''",
            "cong_viec_nghiem_thu": "TEXT DEFAULT ''",
            "ve_sinh_mt": "TEXT DEFAULT 'Tốt'",
            "an_toan_ld": "TEXT DEFAULT 'Tốt'",
            "su_co": "TEXT DEFAULT 'Không'",
            "kien_nghi": "TEXT DEFAULT 'Không'",
        },
    }
    for table, columns in additive_columns.items():
        existing_columns = {
            row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')
        }
        for column, definition in columns.items():
            if column not in existing_columns:
                connection.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'
                )


def _initialize_local_database():
    connection = sqlite3.connect(
        LOCAL_DATABASE_PATH, timeout=30, check_same_thread=False
    )
    connection.execute("PRAGMA busy_timeout = 30000")
    _ensure_local_schema(connection)
    connection.execute(
        "ATTACH DATABASE ? AS sync_store", (str(SYNC_DATABASE_PATH),)
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS sync_store.pending_batches ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "batch_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS sync_store.sync_meta ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.commit()
    return LocalDatabaseConnection(connection, SYNC_DATABASE_PATH)


def setup_local_database(storage_directory=None):
    global _cloud_schema_initialized, LOCAL_DATABASE_PATH, SYNC_DATABASE_PATH

    if storage_directory is not None:
        storage_path = Path(storage_directory)
        storage_path.mkdir(parents=True, exist_ok=True)
        LOCAL_DATABASE_PATH = storage_path / "du_lieu_thi_cong.db"
        SYNC_DATABASE_PATH = Path(str(LOCAL_DATABASE_PATH) + ".sync")

    if os.path.exists(LOCAL_DATABASE_PATH):
        backup_path = Path(str(LOCAL_DATABASE_PATH) + ".pre-neon-sync.bak")
        if not os.path.exists(backup_path):
            source = sqlite3.connect(LOCAL_DATABASE_PATH, timeout=30)
            backup = sqlite3.connect(backup_path)
            try:
                source.backup(backup)
            finally:
                backup.close()
                source.close()

    local_connection = _initialize_local_database()
    remote_connection = None
    try:
        remote_connection = setup_database()
        _cloud_schema_initialized = True
        if not local_connection.refresh_from_remote(remote_connection):
            if not local_connection.has_cloud_snapshot():
                raise RuntimeError(
                    "Cache SQLite chưa có snapshot NeonCloud ban đầu."
                )
            logging.info(
                "Giữ nguyên cache SQLite vì còn dữ liệu đang chờ đồng bộ lên NeonCloud."
            )
    except Exception as exc:
        logging.exception(
            "Không thể tải snapshot NeonCloud khi khởi động."
        )
        if not local_connection.has_cloud_snapshot():
            raise RuntimeError(
                "Cần kết nối NeonCloud ít nhất một lần để khởi tạo cache SQLite."
            ) from exc
    finally:
        if remote_connection is not None:
            remote_connection.close()
    return local_connection


def _apply_sync_batch(remote_connection, payload):
    for statement in json.loads(payload):
        query = _retry_safe_insert(statement["query"])
        parameters = statement["parameters"]
        if statement["many"]:
            values = [
                [_from_json_value(value) for value in row] for row in parameters
            ]
            remote_connection.cursor().executemany(query, values)
        else:
            values = [_from_json_value(value) for value in parameters]
            remote_connection.execute(query, values)
    remote_connection.commit()


def _background_sync_worker(local_connection):
    global _cloud_schema_initialized

    retry_delay = 1
    while True:
        remote_connection = None
        try:
            if _cloud_schema_initialized:
                remote_connection = _connect_database(DATABASE_URL)
            else:
                remote_connection = setup_database()
                _cloud_schema_initialized = True

            local_connection.refresh_from_remote(remote_connection)
            retry_delay = 1
            while True:
                batch = local_connection.next_pending_batch()
                if batch is None:
                    time.sleep(1)
                    continue
                batch_id, payload = batch
                _apply_sync_batch(remote_connection, payload)
                local_connection.acknowledge_batch(batch_id)
                logging.info("Đã đồng bộ một lô thay đổi SQLite lên NeonCloud.")
        except RuntimeError:
            logging.exception(
                "Đồng bộ nền dừng do cấu trúc dữ liệu SQLite/NeonCloud không tương thích."
            )
            if remote_connection is not None:
                try:
                    remote_connection.close()
                except Exception:
                    logging.exception("Không thể đóng kết nối NeonCloud lỗi.")
            return
        except Exception:
            logging.exception(
                "Đồng bộ nền SQLite lên NeonCloud thất bại; sẽ giữ hàng đợi và thử lại."
            )
            if remote_connection is not None:
                try:
                    remote_connection.rollback()
                except Exception:
                    logging.exception("Không thể rollback phiên đồng bộ NeonCloud.")
                try:
                    remote_connection.close()
                except Exception:
                    logging.exception("Không thể đóng kết nối NeonCloud lỗi.")
            time.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)


def start_background_sync():
    global _sync_thread

    if _sync_thread is not None and _sync_thread.is_alive():
        return
    local_connection = db_conn._get_connection()
    if not isinstance(local_connection, LocalDatabaseConnection):
        raise RuntimeError("Không thể chạy đồng bộ nền khi chưa có cache SQLite.")
    _sync_thread = threading.Thread(
        target=_background_sync_worker,
        args=(local_connection,),
        name="neon-background-sync",
        daemon=True,
    )
    _sync_thread.start()


def _connect_with_retry(**connection_options):
    for attempt in range(1, CONNECTION_ATTEMPTS + 1):
        try:
            return pg8000.dbapi.connect(**connection_options)
        except (pg8000.dbapi.InterfaceError, TimeoutError, OSError):
            if attempt == CONNECTION_ATTEMPTS:
                raise
            logging.warning(
                "Kết nối PostgreSQL thất bại, sẽ thử lại (%d/%d).",
                attempt,
                CONNECTION_ATTEMPTS,
                exc_info=True,
            )
            time.sleep(attempt)


def _connect_database(database_url):
    parsed_url = urlsplit(database_url)
    if parsed_url.scheme not in ("postgresql", "postgresql+pg8000", "postgres"):
        raise ValueError("DATABASE_URL phải là URL PostgreSQL hợp lệ.")

    query = parse_qs(parsed_url.query)
    sslmode = query.get("sslmode", ["require"])[0].lower()
    if sslmode == "allow":
        raise ValueError("pg8000 không hỗ trợ sslmode=allow.")
    if sslmode not in ("disable", "prefer", "require", "verify-ca", "verify-full"):
        raise ValueError(f"sslmode PostgreSQL không được hỗ trợ: {sslmode}")
    if not parsed_url.hostname or not parsed_url.path.strip("/"):
        raise ValueError("DATABASE_URL phải bao gồm host và tên database.")

    try:
        requested_timeout = int(
            query.get("connect_timeout", [CONNECTION_TIMEOUT_SECONDS])[0]
        )
    except ValueError as exc:
        raise ValueError("connect_timeout phải là số nguyên dương.") from exc
    if requested_timeout <= 0:
        raise ValueError("connect_timeout phải là số nguyên dương.")
    timeout = max(requested_timeout, CONNECTION_TIMEOUT_SECONDS)

    if sslmode == "disable":
        ssl_context = False
    elif sslmode == "prefer":
        ssl_context = None
    elif sslmode == "require":
        ssl_context = True
    else:
        ssl_context = ssl.create_default_context()
        if sslmode == "verify-ca":
            ssl_context.check_hostname = False

    connection = _connect_with_retry(
        user=unquote(parsed_url.username or ""),
        password=unquote(parsed_url.password or ""),
        host=parsed_url.hostname,
        port=parsed_url.port or 5432,
        database=unquote(parsed_url.path.lstrip("/")),
        timeout=timeout,
        ssl_context=ssl_context,
    )
    return DatabaseConnection(connection)


# Class quản lý dữ liệu phiên đăng nhập xuyên suốt app (Thay thế các biến global cũ)
class Session:
    user = None
    role = None
    project = None

    @classmethod
    def refresh_project_role(cls, db_conn):
        if not cls.project or not cls.user:
            cls.role = None
            return None
        row = db_conn.execute(
            "SELECT role FROM project_members WHERE project_id = ? AND user_id = ?",
            (cls.project["id"], cls.user["id"]),
        ).fetchone()
        cls.role = row[0] if row else None
        return cls.role

    @classmethod
    def clear(cls):
        cls.user = None
        cls.role = None
        cls.project = None

def setup_database():
    if DATABASE_URL == "CHUA_DIEN_URL":
        raise RuntimeError(
            "Hãy điền DATABASE_URL trong core/database.py bằng URL PostgreSQL hợp lệ."
        )

    conn = _connect_database(DATABASE_URL)
    cursor = conn.cursor()
    
    # 1. Các bảng cơ bản
    cursor.execute("CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, username TEXT NOT NULL, password TEXT NOT NULL, email TEXT UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS projects (id SERIAL PRIMARY KEY, ten_du_an TEXT NOT NULL, hang_muc TEXT, dia_diem TEXT, chu_dau_tu TEXT, nha_thau TEXT, tu_van_giam_sat TEXT, goi_thau TEXT, hop_dong_so TEXT, ngay_bat_dau TEXT, ngay_ket_thuc_du_kien TEXT, mo_ta TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS project_members (project_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (project_id, user_id), FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE)")
    cursor.execute("CREATE TABLE IF NOT EXISTS email_inbox (id SERIAL PRIMARY KEY, recipient TEXT, sender TEXT, subject TEXT, body TEXT, project_id INTEGER, is_read INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, invitation_id INTEGER)")
    cursor.execute("CREATE TABLE IF NOT EXISTS project_invitations (id SERIAL PRIMARY KEY, project_id INTEGER, inviter_user_id INTEGER, invitee_email TEXT, role TEXT, status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS work_standards (id SERIAL PRIMARY KEY, ten_cong_viec TEXT, nhom_cong_viec TEXT, don_vi TEXT, dinh_muc TEXT, mo_ta TEXT, created_by TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, may_thi_cong TEXT DEFAULT '', tcvn TEXT DEFAULT '', pccc TEXT DEFAULT '', tieu_chi_nghiem_thu TEXT DEFAULT '')")
    cursor.execute("CREATE TABLE IF NOT EXISTS acceptance_records (id SERIAL PRIMARY KEY, project_id INTEGER, loai_phieu TEXT, tieu_de TEXT, hang_muc TEXT, vi_tri TEXT, ngay_yeu_cau TEXT, ngay_nghiem_thu TEXT, trang_thai TEXT DEFAULT 'Chờ nghiệm thu', thanh_phan_tham_du TEXT, noi_dung TEXT, ghi_chu TEXT, created_by TEXT, created_at TIMESTAMP)")
    cursor.execute("CREATE TABLE IF NOT EXISTS acceptance_settings (project_id INTEGER PRIMARY KEY, mau_bien_ban TEXT, tieu_de TEXT, don_vi_nghiem_thu TEXT, nguoi_ky_1 TEXT, nguoi_ky_2 TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS project_progress (id SERIAL PRIMARY KEY, project_id INTEGER, ten_hang_muc TEXT, phan_tram REAL DEFAULT 0, trang_thai TEXT DEFAULT 'Đang thi công', ngay_cap_nhat TEXT, ngay_bat_dau TEXT, ngay_ket_thuc TEXT, nhan_cong TEXT, tao_bien_ban INTEGER, ngay_nghiem_thu TEXT, gio_nghiem_thu TEXT, may_thi_cong TEXT, tcvn TEXT)")

    for col_def in [
        "may_thi_cong TEXT DEFAULT ''",
        "tcvn TEXT DEFAULT ''",
        "pccc TEXT DEFAULT ''",
        "tieu_chi_nghiem_thu TEXT DEFAULT ''",
    ]:
        column_name = col_def.split()[0]
        cursor.execute(
            f"ALTER TABLE work_standards ADD COLUMN IF NOT EXISTS {column_name} {col_def[len(column_name):].strip()}"
        )

    for column_name, column_type in [
        ("ngay_bat_dau", "TEXT"),
        ("ngay_ket_thuc", "TEXT"),
        ("nhan_cong", "TEXT"),
        ("tao_bien_ban", "INTEGER"),
        ("ngay_nghiem_thu", "TEXT"),
        ("gio_nghiem_thu", "TEXT"),
        ("may_thi_cong", "TEXT"),
        ("tcvn", "TEXT"),
    ]:
        cursor.execute(
            f"ALTER TABLE project_progress ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
        )

    default_work_standards = [
        ("Khảo sát hiện trạng công trình", "Chuẩn bị mặt bằng", "lần", "Theo hồ sơ thiết kế", "Kiểm tra hiện trạng, cao độ và điều kiện thi công."),
        ("Định vị tim trục và cao độ", "Chuẩn bị mặt bằng", "lần", "Theo bản vẽ được duyệt", "Bàn giao mốc tọa độ, tim trục và cao độ thi công."),
        ("Dọn dẹp và chuẩn bị mặt bằng", "Chuẩn bị mặt bằng", "m2", "Theo phạm vi thi công", "Thu dọn chướng ngại, bố trí khu vực tập kết vật tư."),
        ("Đào đất hố móng", "Phần móng", "m3", "Theo bản vẽ được duyệt", "Kiểm tra kích thước, cao độ đáy móng và biện pháp chống sạt."),
        ("Đổ bê tông lót móng", "Phần móng", "m3", "Theo thiết kế", "Vệ sinh đáy móng và kiểm tra chiều dày lớp bê tông lót."),
        ("Gia công, lắp dựng cốt thép móng", "Phần móng", "kg", "Theo bản vẽ kết cấu", "Kiểm tra chủng loại, đường kính, khoảng cách và lớp bảo vệ cốt thép."),
        ("Lắp dựng cốp pha móng", "Phần móng", "m2", "Theo bản vẽ kết cấu", "Kiểm tra kích thước, cao độ, độ kín khít và độ ổn định cốp pha."),
        ("Đổ bê tông móng", "Phần móng", "m3", "Theo cấp phối được duyệt", "Kiểm tra độ sụt, lấy mẫu và bảo dưỡng bê tông theo quy định."),
        ("Thi công chống thấm phần ngầm", "Phần móng", "m2", "Theo chỉ dẫn kỹ thuật", "Kiểm tra bề mặt, lớp chống thấm và thử nước trước khi lấp đất."),
        ("Lấp đất và đầm chặt hố móng", "Phần móng", "m3", "Theo chỉ dẫn kỹ thuật", "Lấp theo lớp và kiểm tra độ chặt theo yêu cầu thiết kế."),
        ("Gia công, lắp dựng cốt thép cột, dầm, sàn", "Kết cấu bê tông", "kg", "Theo bản vẽ kết cấu", "Kiểm tra nối thép, con kê, khoảng cách và vị trí thép chờ."),
        ("Lắp dựng cốp pha cột, dầm, sàn", "Kết cấu bê tông", "m2", "Theo bản vẽ kết cấu", "Kiểm tra kích thước, cao độ, chống đỡ và độ ổn định trước khi đổ."),
        ("Đổ bê tông cột, dầm, sàn", "Kết cấu bê tông", "m3", "Theo cấp phối được duyệt", "Kiểm tra độ sụt, đầm bê tông, lấy mẫu và bảo dưỡng."),
        ("Tháo dỡ cốp pha kết cấu", "Kết cấu bê tông", "m2", "Theo quy định kỹ thuật", "Chỉ tháo khi bê tông đạt điều kiện và không gây ảnh hưởng kết cấu."),
        ("Xây tường gạch", "Xây, trát", "m2", "Theo bản vẽ kiến trúc", "Kiểm tra liên kết, mạch vữa, độ thẳng đứng và vị trí ô chờ."),
        ("Tô trát tường, trần", "Xây, trát", "m2", "Theo chỉ dẫn kỹ thuật", "Kiểm tra chiều dày, độ phẳng, độ bám dính và bảo dưỡng bề mặt."),
        ("Cán nền tạo phẳng", "Xây, trát", "m2", "Theo cao độ thiết kế", "Kiểm tra cao độ, độ dốc thoát nước và độ phẳng nền."),
        ("Thi công chống thấm khu vệ sinh, ban công", "Hoàn thiện", "m2", "Theo chỉ dẫn kỹ thuật", "Thi công đúng lớp và thử nước trước khi hoàn thiện."),
        ("Ốp lát gạch nền, tường", "Hoàn thiện", "m2", "Theo bản vẽ hoàn thiện", "Kiểm tra chủng loại, mạch gạch, cao độ và độ rỗng bộp."),
        ("Bả và sơn hoàn thiện", "Hoàn thiện", "m2", "Theo bảng màu được duyệt", "Kiểm tra xử lý bề mặt, số lớp sơn và độ đồng đều màu sắc."),
        ("Lắp đặt cửa và phụ kiện", "Hoàn thiện", "bộ", "Theo hồ sơ được duyệt", "Kiểm tra kích thước, vận hành, độ kín khít và phụ kiện."),
        ("Lắp đặt đường ống cấp thoát nước", "Hệ thống cơ điện", "m", "Theo bản vẽ MEP", "Thử áp lực, kiểm tra độ dốc và nghiệm thu trước khi che khuất."),
        ("Lắp đặt ống luồn và dây điện", "Hệ thống cơ điện", "m", "Theo bản vẽ MEP", "Kiểm tra tuyến ống, tiết diện dây và đo kiểm cách điện."),
        ("Lắp đặt thiết bị điện, nước", "Hệ thống cơ điện", "bộ", "Theo hồ sơ được duyệt", "Kiểm tra đấu nối, vận hành và an toàn trước khi bàn giao."),
        ("Vệ sinh, thu dọn và bàn giao khu vực", "Nghiệm thu, bàn giao", "khu vực", "Theo yêu cầu bàn giao", "Thu gom phế thải, vệ sinh và bàn giao hồ sơ chất lượng."),
    ]
    cursor.executemany(
        "INSERT INTO work_standards (ten_cong_viec, nhom_cong_viec, don_vi, dinh_muc, mo_ta, created_by) "
        "SELECT ?, ?, ?, ?, ?, 'Thư viện mặc định' "
        "WHERE NOT EXISTS (SELECT 1 FROM work_standards WHERE LOWER(TRIM(ten_cong_viec)) = LOWER(TRIM(?)))",
        [standard + (standard[0],) for standard in default_work_standards],
    )
    supplied_work_standards = [
        ("Đào bóc hữu cơ, đất phong hóa", "Công tác đất", "Máy đào, Ô tô tải", "TCVN 4447:2012", ""),
        ("Vận chuyển đất đổ thải", "Công tác đất", "Ô tô tải", "TCVN 4447:2012", ""),
        ("Đào đất", "Công tác đất", "Máy đào", "TCVN 4447:2012", ""),
        ("Đào đất hố móng", "Phần móng", "Máy đào, Thủ công", "TCVN 4447:2012", ""),
        ("Đắp đất nền K90", "Công tác đất", "Máy lu, Máy ủi", "TCVN 4447:2012", ""),
        ("Đắp đất nền K95", "Công tác đất", "Máy lu, Máy ủi", "TCVN 4447:2012", ""),
        ("Đắp đất nền K98", "Công tác đất", "Máy lu trọng tải lớn", "TCVN 4447:2012", ""),
        ("Lắp dựng cửa, vách kính", "Cửa và vách kính", "Dụng cụ cầm tay", "TCVN 7455:2013", ""),
        ("Gia công lắp dựng kết cấu thép", "Kết cấu thép", "Cần cẩu, máy hàn", "TCVN 5575:2012", ""),
        ("Lợp mái tôn", "Mái", "Máy khoan, máy bắn vít", "TCVN 8575:2010", ""),
        ("Thi công chống thấm", "Chống thấm", "Dụng cụ chuyên dụng", "TCVN 9363:2012", ""),
        ("Lắp đặt hệ thống PCCC", "Phòng cháy chữa cháy", "Dụng cụ lắp đặt", "TCVN 3890:2023", "TCVN 3890:2023 - Trang bị, bố trí phương tiện phòng cháy và chữa cháy cho nhà và công trình."),
        ("Lắp đặt hệ thống chống sét", "Hệ thống chống sét", "Dụng cụ chuyên dụng", "TCVN 9385:2012", "TCVN 9385:2012 - Chống sét cho công trình xây dựng."),
        ("Sơn trong nhà", "Hoàn thiện", "Dụng cụ lăn, phun sơn", "TCVN 9377:2012", "Yêu cầu PCCC áp dụng theo hồ sơ thiết kế và quy định hiện hành."),
        ("Trắc đạc công trình", "Chuẩn bị mặt bằng", "Máy toàn đạc, Máy thủy bình", "TCVN 9398:2012", ""),
    ]
    cursor.executemany(
        "INSERT INTO work_standards (ten_cong_viec, nhom_cong_viec, don_vi, dinh_muc, mo_ta, created_by, may_thi_cong, tcvn, pccc) "
        "SELECT ?, ?, '', '', '', 'Thư viện mặc định', ?, ?, ? "
        "WHERE NOT EXISTS (SELECT 1 FROM work_standards WHERE LOWER(TRIM(ten_cong_viec)) = LOWER(TRIM(?)))",
        [item[:2] + item[2:5] + (item[0],) for item in supplied_work_standards],
    )
    cursor.executemany(
        "UPDATE work_standards SET "
        "may_thi_cong = CASE WHEN COALESCE(may_thi_cong, '') = '' THEN ? ELSE may_thi_cong END, "
        "tcvn = CASE WHEN COALESCE(tcvn, '') = '' THEN ? ELSE tcvn END, "
        "pccc = CASE WHEN COALESCE(pccc, '') = '' THEN ? ELSE pccc END "
        "WHERE LOWER(TRIM(ten_cong_viec)) = LOWER(TRIM(?))",
        [(item[2], item[3], item[4], item[0]) for item in supplied_work_standards],
    )

    # 2. Bảng Cài đặt Nhật ký (MỚI)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS log_settings (
            project_id INTEGER PRIMARY KEY, tieu_de TEXT DEFAULT 'NHẬT KÝ THI CÔNG',
            hien_thi_ky_1 INTEGER DEFAULT 1, don_vi_1 TEXT DEFAULT 'NHÀ THẦU THI CÔNG', nguoi_ky_1 TEXT DEFAULT '',
            hien_thi_ky_2 INTEGER DEFAULT 1, don_vi_2 TEXT DEFAULT 'TƯ VẤN GIÁM SÁT', nguoi_ky_2 TEXT DEFAULT '',
            FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
        )
    """)

    # 3. Bảng Nhật ký (Thêm các cột mới an toàn)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS nhat_ky (
            id SERIAL PRIMARY KEY, project_id INTEGER, created_by TEXT, hang_muc TEXT, mo_ta TEXT,
            duong_dan_anh TEXT, thoi_gian TIMESTAMP DEFAULT CURRENT_TIMESTAMP, ca_thi_cong TEXT, so_cong_nhan TEXT,
            may_moc TEXT, ngay_thi_cong TEXT, thoi_tiet TEXT, nhiet_do_sang TEXT, nhiet_do_chieu TEXT, nhiet_do_toi TEXT,
            thoi_tiet_sang TEXT, thoi_tiet_chieu TEXT, thoi_tiet_toi TEXT
        )
    """)
    
    # Cập nhật thêm các cột mới cho form Nhật ký đầy đủ
    new_columns = {
        "cb_ky_thuat": "TEXT DEFAULT ''",
        "cong_viec_nghiem_thu": "TEXT DEFAULT ''",
        "ve_sinh_mt": "TEXT DEFAULT 'Tốt'",
        "an_toan_ld": "TEXT DEFAULT 'Tốt'",
        "su_co": "TEXT DEFAULT 'Không'",
        "kien_nghi": "TEXT DEFAULT 'Không'"
    }
    for col, dtype in new_columns.items():
        cursor.execute(f"ALTER TABLE nhat_ky ADD COLUMN IF NOT EXISTS {col} {dtype}")

    cursor.execute("INSERT INTO users (username, password, email) VALUES ('kysu1', '123', 'kysu1@example.com') ON CONFLICT (email) DO NOTHING")
    cursor.execute("INSERT INTO users (username, password, email) VALUES ('chudautu1', '123', 'chudautu1@example.com') ON CONFLICT (email) DO NOTHING")
    conn.commit()
    return conn


db_conn = DeferredDatabaseConnection()