"""Tien ich dung chung: thong bao, email SMTP, launcher camera."""
import os
import sys
import subprocess
import smtplib
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from html import escape
from email.message import EmailMessage
from urllib.parse import urlsplit

import flet as ft

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ------------------------------------------------------------------
# Cau hinh SMTP. Thong tin dang nhap duoc doc tu bien moi truong.
# ------------------------------------------------------------------
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
MAIL_API_CONFIG_PATH = os.path.join(PROJECT_ROOT, "app_config.json")


def _smtp_credentials() -> tuple[str, str]:
    sender_email = os.getenv("SMTP_SENDER_EMAIL", "").strip()
    app_password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    return sender_email, app_password


def _mail_api_url() -> str:
    configured_url = os.getenv("MAIL_API_URL", "").strip()
    if configured_url:
        return configured_url.rstrip("/")

    if not os.path.exists(MAIL_API_CONFIG_PATH):
        return ""
    with open(MAIL_API_CONFIG_PATH, encoding="utf-8") as config_file:
        config = json.load(config_file)
    if not isinstance(config, dict):
        raise ValueError("app_config.json phải chứa một đối tượng JSON.")
    configured_url = config.get("mail_api_url", "")
    if not isinstance(configured_url, str):
        raise ValueError("mail_api_url trong app_config.json phải là chuỗi.")
    return configured_url.strip().rstrip("/")


def _send_via_mail_api(path: str, payload: dict) -> tuple[bool, str]:
    api_url = _mail_api_url()
    if not api_url:
        return False, (
            "Thiết bị này chưa có SMTP. Cần triển khai mail backend, "
            "đặt MAIL_API_URL trong app_config.json rồi build lại APK."
        )
    parsed_url = urlsplit(api_url)
    if (
        parsed_url.scheme != "https"
        or not parsed_url.hostname
        or parsed_url.username
        or parsed_url.password
        or parsed_url.query
        or parsed_url.fragment
    ):
        return False, "MAIL_API_URL phải là URL HTTPS hợp lệ."

    request = Request(
        api_url + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            if response.status != 200:
                return False, f"Mail backend trả về HTTP {response.status}."
        return True, ""
    except HTTPError as exc:
        try:
            response = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False, f"Mail backend trả về HTTP {exc.code}."
        detail = response.get("error") if isinstance(response, dict) else None
        return False, detail or f"Mail backend trả về HTTP {exc.code}."
    except (OSError, URLError, ValueError) as exc:
        return False, f"Không thể kết nối mail backend: {exc}"


def show_message(page: ft.Page, msg_text, color=ft.Colors.GREEN_700):
    """Hien thi SnackBar tuong thich ca Flet cu va moi."""
    snack = ft.SnackBar(content=ft.Text(msg_text, color=ft.Colors.WHITE), bgcolor=color)
    if hasattr(page, "open"):
        page.open(snack)
    else:
        page.overlay.append(snack)
        snack.open = True
        page.update()


def smtp_is_configured() -> bool:
    sender_email, app_password = _smtp_credentials()
    return bool(sender_email and app_password)


def _send_mail(
    recipient_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> tuple[bool, str]:
    sender_email, app_password = _smtp_credentials()
    if not sender_email or not app_password:
        return False, (
            "Chưa cấu hình SMTP_SENDER_EMAIL và SMTP_APP_PASSWORD trong biến môi trường."
        )

    try:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender_email
        message["To"] = recipient_email
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")

        smtp_server = os.getenv("SMTP_SERVER", SMTP_SERVER).strip() or SMTP_SERVER
        smtp_port = int(os.getenv("SMTP_PORT", str(SMTP_PORT)))
        with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(sender_email, app_password)
            smtp.send_message(message)
        return True, ""
    except (OSError, smtplib.SMTPException, ValueError) as exc:
        return False, str(exc)


def send_otp_email(recipient_email, otp_code):
    sender_email, app_password = _smtp_credentials()
    if not sender_email or not app_password:
        return _send_via_mail_api(
            "/v1/email/otp",
            {"recipient": recipient_email, "otp": otp_code},
        )
    return _send_mail(
        recipient_email,
        "Xác thực tài khoản - Hệ thống quản lý thi công",
        "Mã xác nhận đăng ký tài khoản của bạn là: " + otp_code,
    )


def send_account_password_email(recipient_email, password):
    return _send_mail(
        recipient_email,
        "Khôi phục mật khẩu",
        "Mật khẩu tài khoản của bạn là: " + password + "\n\nVui lòng đổi mật khẩu sau khi truy cập.",
    )


def _project_invitation_content(
    project_id: int,
    project_name: str,
    inviter_name: str,
    role: str,
):
    deep_link = f"hosoxaydung://project?id={project_id}"
    safe_project_name = escape(project_name)
    safe_inviter_name = escape(inviter_name)
    safe_role = escape(role)
    safe_deep_link = escape(deep_link, quote=True)
    subject = f"Lời mời tham gia dự án: {project_name}"
    body = (
        f"{inviter_name} đã mời bạn tham gia dự án {project_name} "
        f"với vai trò {role}.\n\n"
        f"Mở dự án trong ứng dụng: {deep_link}"
    )
    html_body = f"""\
<!doctype html>
<html lang="vi">
  <body style="margin:0;padding:32px 12px;background:#f1f5f9;font-family:Arial,sans-serif;color:#172033;">
    <table role="presentation" style="width:100%;max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;border-collapse:separate;border-spacing:0;overflow:hidden;">
      <tr>
        <td style="padding:28px 32px;background:#123b69;color:#ffffff;">
          <div style="font-size:13px;letter-spacing:1px;text-transform:uppercase;opacity:.8;">Hồ Sơ Xây Dựng</div>
          <h1 style="margin:12px 0 0;font-size:24px;line-height:1.3;">Bạn được mời tham gia dự án</h1>
        </td>
      </tr>
      <tr>
        <td style="padding:28px 32px;">
          <p style="margin:0 0 12px;font-size:16px;line-height:1.6;">Xin chào,</p>
          <p style="margin:0 0 20px;font-size:16px;line-height:1.6;">
            <strong>{safe_inviter_name}</strong> đã mời bạn tham gia
            <strong>{safe_project_name}</strong>.
          </p>
          <p style="margin:0 0 24px;color:#475569;font-size:14px;">
            Vai trò được mời: <strong>{safe_role}</strong>
          </p>
          <p style="margin:0 0 24px;">
            <a href="{safe_deep_link}" style="display:inline-block;padding:14px 22px;background:#1674d1;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:bold;">
              Mở dự án trong ứng dụng
            </a>
          </p>
          <p style="margin:0;color:#64748b;font-size:13px;line-height:1.6;">
            Nếu nút không mở được ứng dụng, hãy sao chép liên kết này vào điện thoại:
            <br><a href="{safe_deep_link}" style="color:#1674d1;word-break:break-all;">{safe_deep_link}</a>
          </p>
        </td>
      </tr>
    </table>
  </body>
</html>
"""
    return subject, body, html_body


def send_project_invitation_email(
    recipient_email: str,
    project_id: int,
    project_name: str,
    inviter_name: str,
    role: str,
):
    sender_email, app_password = _smtp_credentials()
    if not sender_email or not app_password:
        return _send_via_mail_api(
            "/v1/email/invitation",
            {
                "recipient": recipient_email,
                "project_id": project_id,
                "project_name": project_name,
                "inviter_name": inviter_name,
                "role": role,
            },
        )

    subject, body, html_body = _project_invitation_content(
        project_id, project_name, inviter_name, role
    )
    return _send_mail(recipient_email, subject, body, html_body)


def launch_camera_app():
    """Mo camera_app.py nhu subprocess va tra ve danh sach anh da chup."""
    camera_script = os.path.join(PROJECT_ROOT, "camera_app.py")
    result_file = os.path.join(PROJECT_ROOT, "camera_last_photo.txt")
    try:
        if os.path.exists(result_file):
            os.remove(result_file)

        env = os.environ.copy()
        env["CAMERA_OUTPUT_FILE"] = result_file

        proc = subprocess.run(
            [sys.executable, camera_script],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode:
            output = proc.stderr.strip() or proc.stdout.strip()
            detail = output.splitlines()[-1] if output else ""
            raise RuntimeError("Ung dung camera thoat voi ma " + str(proc.returncode) + ". " + detail)

        if os.path.exists(result_file):
            with open(result_file, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f.readlines() if line.strip()]
            valid_paths = [p for p in lines if os.path.exists(p)]
            return valid_paths
        return []
    except OSError as exc:
        raise RuntimeError("Không thể khởi chạy ứng dụng camera: " + str(exc)) from exc
