"""Minimal WSGI mail API for mobile clients."""
import json
import logging
import re
import threading
import time
from collections import deque
from email.utils import parseaddr
from wsgiref.simple_server import make_server

from core.utils import (
    _project_invitation_content,
    _send_mail,
    smtp_is_configured,
)

MAX_REQUEST_BYTES = 4096
RATE_WINDOW_SECONDS = 3600
EMAIL_LIMIT_PER_WINDOW = 3
IP_LIMIT_PER_WINDOW = 20
GLOBAL_LIMIT_PER_WINDOW = 200

_rate_lock = threading.Lock()
_rate_events = {}


def _json_response(start_response, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ],
    )
    return [body]


def _is_valid_email(value):
    if not isinstance(value, str) or len(value) > 254:
        return False
    if "\r" in value or "\n" in value:
        return False
    display_name, address = parseaddr(value)
    return not display_name and address == value and "@" in address


def _allow_request(client_ip, recipient, now=None):
    current_time = time.monotonic() if now is None else now
    keys = (
        ("global", "all", GLOBAL_LIMIT_PER_WINDOW),
        ("ip", client_ip, IP_LIMIT_PER_WINDOW),
        ("email", recipient.lower(), EMAIL_LIMIT_PER_WINDOW),
    )
    with _rate_lock:
        for key, events in list(_rate_events.items()):
            while events and current_time - events[0] >= RATE_WINDOW_SECONDS:
                events.popleft()
            if not events:
                del _rate_events[key]

        if any(
            len(_rate_events.get((scope, value), ())) >= limit
            for scope, value, limit in keys
        ):
            return False
        for scope, value, _ in keys:
            _rate_events.setdefault((scope, value), deque()).append(current_time)
        return True


def _read_payload(environ):
    if environ.get("CONTENT_TYPE", "").split(";", 1)[0].strip().lower() != "application/json":
        raise ValueError("Content-Type phải là application/json.")
    try:
        content_length = int(environ.get("CONTENT_LENGTH", ""))
    except ValueError as exc:
        raise ValueError("Content-Length không hợp lệ.") from exc
    if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
        raise ValueError("Kích thước yêu cầu không hợp lệ.")
    body = environ["wsgi.input"].read(content_length)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Nội dung JSON không hợp lệ.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Nội dung yêu cầu phải là một đối tượng JSON.")
    return payload


def _send_otp(payload):
    recipient = payload.get("recipient")
    otp = payload.get("otp")
    if not _is_valid_email(recipient):
        raise ValueError("Email người nhận không hợp lệ.")
    if not isinstance(otp, str) or re.fullmatch(r"\d{6}", otp) is None:
        raise ValueError("Mã OTP phải gồm 6 chữ số.")
    return _send_mail(
        recipient,
        "Xác thực tài khoản - Hệ thống quản lý thi công",
        "Mã xác nhận đăng ký tài khoản của bạn là: " + otp,
    )


def _send_invitation(payload):
    recipient = payload.get("recipient")
    project_id = payload.get("project_id")
    project_name = payload.get("project_name")
    inviter_name = payload.get("inviter_name")
    role = payload.get("role")
    if not _is_valid_email(recipient):
        raise ValueError("Email người nhận không hợp lệ.")
    if isinstance(project_id, bool) or not isinstance(project_id, int) or project_id <= 0:
        raise ValueError("Mã công trình không hợp lệ.")
    for field, value, maximum in (
        ("Tên công trình", project_name, 200),
        ("Tên người mời", inviter_name, 120),
        ("Vai trò", role, 80),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise ValueError(f"{field} không hợp lệ.")

    subject, body, html_body = _project_invitation_content(
        recipient, project_id, project_name.strip(), inviter_name.strip(), role.strip()
    )
    return _send_mail(recipient, subject, body, html_body)


def application(environ, start_response):
    method = environ.get("REQUEST_METHOD", "GET").upper()
    path = environ.get("PATH_INFO", "")
    if method == "GET" and path == "/healthz":
        return _json_response(start_response, "200 OK", {"status": "ok"})
    if method != "POST" or path not in (
        "/v1/email/otp",
        "/v1/email/invitation",
    ):
        return _json_response(start_response, "404 Not Found", {"error": "Không tìm thấy."})
    if not smtp_is_configured():
        return _json_response(
            start_response,
            "503 Service Unavailable",
            {"error": "Dịch vụ gửi email chưa được cấu hình SMTP."},
        )

    try:
        payload = _read_payload(environ)
        recipient = payload.get("recipient")
        if not _is_valid_email(recipient):
            raise ValueError("Email người nhận không hợp lệ.")
        client_ip = environ.get("REMOTE_ADDR", "unknown")
        if not _allow_request(client_ip, recipient):
            return _json_response(
                start_response,
                "429 Too Many Requests",
                {"error": "Đã vượt giới hạn gửi email. Vui lòng thử lại sau."},
            )
        sent, error = (
            _send_otp(payload)
            if path == "/v1/email/otp"
            else _send_invitation(payload)
        )
    except ValueError as exc:
        return _json_response(start_response, "400 Bad Request", {"error": str(exc)})

    if not sent:
        logging.error("Mail backend không gửi được email: %s", error)
        return _json_response(
            start_response,
            "502 Bad Gateway",
            {"error": "Máy chủ không gửi được email. Vui lòng thử lại sau."},
        )
    return _json_response(start_response, "200 OK", {"status": "sent"})


def main():
    if not smtp_is_configured():
        raise RuntimeError(
            "Hãy cấu hình SMTP_SENDER_EMAIL và SMTP_APP_PASSWORD trên máy chủ mail."
        )
    with make_server("0.0.0.0", 8080, application) as server:
        logging.info("Mail API đang lắng nghe cổng 8080.")
        server.serve_forever()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
