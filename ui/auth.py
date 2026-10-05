"""Man hinh Dang nhap / Dang ky."""
import asyncio
import logging
import flet as ft
import secrets
from core.database import db_conn, Session
from core.utils import (
    send_otp_email,
    send_account_password_email,
    show_message,
)

def build_auth_view(page: ft.Page, on_login_success):
    txt_user = ft.TextField(label="Email đăng kí", prefix_icon=ft.Icons.EMAIL, width=300)
    txt_pass = ft.TextField(label="Mật khẩu", password=True, can_reveal_password=True, prefix_icon=ft.Icons.LOCK, width=300)

    reg_user = ft.TextField(label="Tên tài khoản mới", prefix_icon=ft.Icons.PERSON, width=300)
    reg_email = ft.TextField(label="Email", prefix_icon=ft.Icons.EMAIL, width=300)
    reg_pass = ft.TextField(label="Mật khẩu", password=True, can_reveal_password=True, prefix_icon=ft.Icons.LOCK, width=300)

    field_width = min(300, page.width - 32) if page.width and page.width > 32 else 300
    for field in (txt_user, txt_pass, reg_user, reg_email, reg_pass):
        field.width = field_width
    txt_user.keyboard_type = ft.KeyboardType.EMAIL
    txt_user.autocorrect = False
    txt_user.enable_suggestions = False
    reg_email.keyboard_type = ft.KeyboardType.EMAIL
    reg_email.autocorrect = False
    reg_email.enable_suggestions = False
    login_button = ft.FilledButton(
        "ĐĂNG NHẬP",
        width=field_width,
        style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_800, color=ft.Colors.WHITE),
    )

    def btn_login_clicked(e):
        if login_button.disabled:
            return

        email = (txt_user.value or "").strip().lower()
        password = txt_pass.value or ""
        if not email or not password:
            show_message(
                page,
                "Vui lòng nhập email và mật khẩu.",
                ft.Colors.RED_700,
            )
            return

        login_button.disabled = True
        login_button.text = "ĐANG ĐĂNG NHẬP..."
        page.update()
        try:
            page.run_task(handle_login, email, password)
        except Exception:
            login_button.disabled = False
            login_button.text = "ĐĂNG NHẬP"
            page.update()
            logging.exception("Không thể khởi chạy tác vụ đăng nhập.")
            show_message(
                page,
                "Không thể bắt đầu đăng nhập. Vui lòng thử lại.",
                ft.Colors.RED_700,
            )

    async def handle_login(email, password):
        def authenticate():
            cursor = db_conn.cursor()
            cursor.execute(
                "SELECT id, username, email FROM users WHERE email = ? AND password = ?",
                (email, password),
            )
            return cursor.fetchone()

        try:
            result = await asyncio.to_thread(authenticate)
            if result:
                Session.user = {
                    "id": result[0],
                    "username": result[1],
                    "email": result[2],
                }
                Session.role = None
                Session.project = None
                on_login_success()
            else:
                show_message(
                    page,
                    "Sai tài khoản hoặc mật khẩu!",
                    ft.Colors.RED_700,
                )
        except Exception:
            logging.exception("Lỗi khi xác thực đăng nhập.")
            show_message(
                page,
                "Không thể kiểm tra đăng nhập. Hãy kiểm tra kết nối dữ liệu và thử lại.",
                ft.Colors.RED_700,
            )
        finally:
            login_button.disabled = False
            login_button.text = "ĐĂNG NHẬP"
            page.update()

    def close_modal(dialog=None):
        try:
            while page.pop_dialog() is not None:
                pass
        except Exception:
            logging.exception("Không thể đóng dialog bằng ngăn xếp của Flet.")

        if dialog is not None:
            dialog.open = False
        if reg_dlg is not None:
            reg_dlg.open = False
        page.dialog = None
        page.overlay.clear()
        try:
            page.update()
        except Exception:
            logging.debug("Không thể cập nhật giao diện sau khi đóng dialog.", exc_info=True)

    def open_modal(dialog):
        close_modal()
        page.show_dialog(dialog)
        page.update()

    def set_button_processing(
        button, processing, label, processing_text="Đang xử lý..."
    ):
        button.disabled = processing
        button.text = processing_text if processing else label
        page.update()

    def btn_register_clicked(e):
        if not reg_user.value or not reg_email.value or not reg_pass.value:
            show_message(page, "Vui lòng điền đầy đủ thông tin!", ft.Colors.RED_700)
            return

        if register_button.disabled:
            return

        username = reg_user.value.strip()
        email = reg_email.value.strip().lower()
        password = reg_pass.value
        set_button_processing(
            register_button,
            True,
            "Đăng ký",
            processing_text="Đang gửi mã OTP...",
        )
        page.run_task(
            check_email_and_send_otp,
            username,
            email,
            password,
            register_button,
            reg_dlg,
        )

    async def check_email_and_send_otp(
        username, email, password, active_register_button, reg_dialog
    ):
        try:
            email_exists = await asyncio.to_thread(
                lambda: db_conn.execute(
                    "SELECT 1 FROM users WHERE email=?", (email,)
                ).fetchone()
            )
            if email_exists:
                show_message(page, "Email này đã được sử dụng!", ft.Colors.RED_700)
                return

            otp_code = str(secrets.randbelow(1000000)).zfill(6)
            otp_input = ft.TextField(
                label="Nhập mã OTP 6 chữ số",
                prefix_icon=ft.Icons.LOCK,
                autofocus=True,
            )
            sent, error = await asyncio.to_thread(send_otp_email, email, otp_code)
            if not sent:
                show_message(
                    page,
                    "Không thể gửi mã OTP. " + error,
                    ft.Colors.RED_700,
                )
                return

            async def confirm_otp():
                feedback = None
                error_message = None
                try:
                    entered_otp = otp_input.value or ""

                    def db_tasks(
                        otp_value,
                        expected_otp,
                        account_username,
                        account_password,
                        account_email,
                    ):
                        if otp_value.strip() != expected_otp:
                            return False

                        db_conn.run_transaction(
                            lambda connection: connection.execute(
                                "INSERT INTO users (username, password, email) "
                                "VALUES (?, ?, ?)",
                                (
                                    account_username,
                                    account_password,
                                    account_email,
                                ),
                            )
                        )
                        return True

                    account_created = await asyncio.to_thread(
                        db_tasks,
                        entered_otp,
                        otp_code,
                        username,
                        password,
                        email,
                    )
                    if account_created:
                        reg_user.value = ""
                        reg_email.value = ""
                        reg_pass.value = ""
                        otp_input.value = ""
                        feedback = (
                            "Đăng ký thành công! Vui lòng đăng nhập.",
                            ft.Colors.GREEN_700,
                        )
                    else:
                        feedback = ("Mã OTP không đúng!", ft.Colors.RED_700)
                except Exception as ex:
                    logging.exception("Không thể tạo tài khoản sau khi xác nhận OTP.")
                    error_message = f"Không thể tạo tài khoản: {ex}"
                finally:
                    confirm_button.disabled = False
                    confirm_button.text = "Xác nhận"
                    close_modal(otp_dialog)

                if error_message is not None:
                    show_message(page, error_message, ft.Colors.RED_700)
                elif feedback is not None:
                    show_message(page, feedback[0], feedback[1])

            def on_confirm_clicked(e):
                if confirm_button.disabled:
                    return
                confirm_button.disabled = True
                confirm_button.text = "Đang xử lý..."
                try:
                    page.update()
                    page.run_task(confirm_otp)
                except Exception as ex:
                    logging.exception("Không thể khởi chạy tác vụ xác nhận OTP.")
                    confirm_button.disabled = False
                    confirm_button.text = "Xác nhận"
                    close_modal(otp_dialog)
                    show_message(
                        page,
                        f"Không thể xác nhận OTP: {ex}",
                        ft.Colors.RED_700,
                    )

            confirm_button = ft.FilledButton(
                "Xác nhận",
                on_click=on_confirm_clicked,
            )
            otp_dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Xác nhận email", weight=ft.FontWeight.BOLD),
                content=ft.Column(
                    [
                        ft.Text("Đã gửi mã đến " + email),
                        otp_input,
                    ],
                    tight=True,
                ),
                actions=[
                    ft.TextButton("Hủy", on_click=lambda ev: close_modal(otp_dialog)),
                    confirm_button,
                ],
            )
            close_modal(reg_dialog)
            page.show_dialog(otp_dialog)
            page.update()
        finally:
            set_button_processing(active_register_button, False, "Đăng ký")

    register_button = None
    reg_dlg = None

    def open_registration(e):
        nonlocal register_button, reg_dlg
        reg_user.value = ""
        reg_email.value = ""
        reg_pass.value = ""
        register_button = ft.TextButton(
            "Đăng ký",
            on_click=btn_register_clicked,
        )
        reg_dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Đăng kí tài khoản", weight=ft.FontWeight.BOLD),
            content=ft.Column([reg_user, reg_email, reg_pass], tight=True),
            actions=[
                ft.TextButton("Hủy", on_click=lambda event: close_modal(reg_dlg)),
                register_button,
            ],
        )
        open_modal(reg_dlg)

    login_button.on_click = btn_login_clicked
    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.ENGINEERING, size=80, color=ft.Colors.BLUE_800),
            ft.Text("HỆ THỐNG QUẢN LÝ THI CÔNG", size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
            ft.Container(height=10),
            txt_user,
            txt_pass,
            login_button,
            ft.TextButton(
                "Chưa có tài khoản? Đăng ký ngay",
                on_click=open_registration,
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        scroll=ft.ScrollMode.AUTO,
        expand=True,
    )
