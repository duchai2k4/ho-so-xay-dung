"""Diem khoi chay ung dung Ho So Xay Dung."""
import asyncio
import logging
import re
from urllib.request import Request, urlopen

import flet as ft

from core.database import (
    Session,
    db_conn,
    setup_local_database,
    start_background_sync,
)
from core.utils import show_message
from ui.auth import build_auth_view
from ui.app import start_main_app


async def wake_render_server():
    def send_health_request():
        request = Request(
            "https://ho-so-xay-dung.onrender.com/healthz",
            headers={"User-Agent": "HoSoXayDung/1.0"},
            method="GET",
        )
        with urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(
                    f"Render health endpoint trả về HTTP {response.status}."
                )

    try:
        await asyncio.to_thread(send_health_request)
        logging.info("Đã gửi yêu cầu đánh thức Render server.")
    except Exception:
        logging.warning(
            "Không thể đánh thức Render server; ứng dụng vẫn tiếp tục khởi động.",
            exc_info=True,
        )


def _startup_error_detail(error):
    cause = error
    while cause.__cause__ is not None:
        cause = cause.__cause__

    message = " ".join(str(cause).split())
    message = re.sub(
        r"(?i)(postgres(?:ql)?(?:\+pg8000)?://)[^\s@]+@",
        r"\1[ẩn]@",
        message,
    )
    if len(message) > 240:
        message = message[:237] + "..."
    return f"{type(cause).__name__}: {message or 'Không có thông tin chi tiết.'}"


def main(page: ft.Page):
    page.title = "Ho So Xay Dung - Quan Ly Moi Du An"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.bgcolor = ft.Colors.GREY_100
    page.padding = 0
    try:
        page.run_task(wake_render_server)
    except Exception:
        logging.exception("Không thể khởi chạy tác vụ đánh thức Render server.")

    platform_name = str(page.platform).lower().rsplit(".", 1)[-1]
    if platform_name in {"windows", "macos", "linux"}:
        try:
            page.window.width = 460
            page.window.height = 820
            page.window.resizable = False
            page.window.maximizable = False
        except AttributeError:
            page.window_width = 460
            page.window_height = 820

    preferences = ft.SharedPreferences()
    storage_paths = ft.StoragePaths()
    saved_user_id_key = "signed_in_user_id"

    def reset_page_state():
        page.clean()
        page.appbar = None
        page.floating_action_button = None

        if hasattr(page, "overlay") and hasattr(page.overlay, "clear"):
            page.overlay.clear()
        if hasattr(page, "services"):
            page.services[:] = [
                service
                for service in page.services
                if service is preferences or service is storage_paths
            ]

    def show_login():
        reset_page_state()
        page.add(build_auth_view(page, on_login_success))
        page.update()

    def start_main_view():
        reset_page_state()
        start_main_app(page, on_logout)

    async def save_session_and_start():
        user = Session.user
        if user is None:
            raise RuntimeError("Không thể lưu phiên đăng nhập khi chưa có người dùng.")

        saved = False
        try:
            saved = await preferences.set(saved_user_id_key, user["id"])
        except Exception:
            logging.exception("Không thể lưu phiên đăng nhập trên thiết bị.")

        start_main_view()
        if not saved:
            logging.error("Flet SharedPreferences không lưu được phiên đăng nhập.")
            show_message(
                page,
                "Không thể lưu phiên đăng nhập trên thiết bị này.",
                ft.Colors.RED_700,
            )

    def on_login_success():
        page.run_task(save_session_and_start)

    def on_logout():
        page.run_task(clear_session_and_show_login)

    async def clear_session_and_show_login():
        removed = await preferences.remove(saved_user_id_key)
        if not removed:
            logging.warning(
                "Không tìm thấy phiên đăng nhập đã lưu khi người dùng đăng xuất."
            )
        Session.clear()
        show_login()

    async def restore_session():
        saved_user_id = await preferences.get(saved_user_id_key)
        if isinstance(saved_user_id, int) and not isinstance(saved_user_id, bool):
            row = await asyncio.to_thread(
                lambda: db_conn.execute(
                    "SELECT id, username, email FROM users WHERE id = ?",
                    (saved_user_id,),
                ).fetchone()
            )
            if row:
                Session.user = {"id": row[0], "username": row[1], "email": row[2]}
                Session.role = None
                Session.project = None
                start_main_view()
                return

        if saved_user_id is not None:
            removed = await preferences.remove(saved_user_id_key)
            if not removed:
                logging.warning("Không thể xóa phiên đăng nhập không còn hợp lệ.")
        show_login()

    async def initialize_database_and_restore():
        try:
            platform_name = str(page.platform).lower().rsplit(".", 1)[-1]
            storage_directory = None
            if platform_name == "android":
                storage_directory = (
                    await storage_paths.get_application_support_directory()
                )
                if not storage_directory:
                    raise RuntimeError(
                        "Flet không cung cấp thư mục lưu trữ nội bộ cho Android."
                    )
            connection = await asyncio.to_thread(
                setup_local_database, storage_directory
            )
            db_conn.set_connection(connection)
            start_background_sync()
        except Exception as exc:
            db_conn.set_error(exc)
            logging.exception("Không thể khởi tạo cơ sở dữ liệu khi khởi động.")
            show_message(
                page,
                "Không thể tải dữ liệu. Chi tiết lỗi: "
                + _startup_error_detail(exc),
                ft.Colors.RED_700,
            )
            return

        try:
            await restore_session()
        except Exception:
            logging.exception("Không thể khôi phục phiên đăng nhập.")
            show_login()
            show_message(
                page,
                "Không thể khôi phục phiên đăng nhập. Vui lòng đăng nhập lại.",
                ft.Colors.RED_700,
            )

    show_login()
    page.run_task(initialize_database_and_restore)


if __name__ == "__main__":
    ft.run(main)
