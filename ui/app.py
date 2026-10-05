import asyncio
import logging
import flet as ft
import os
import subprocess
import threading
import traceback
from io import BytesIO
from datetime import datetime
from hashlib import sha256

try:
    import flet_camera as fc
except ImportError:  # Optional device integrations for Android camera flow
    fc = None

try:
    import flet_geolocator as ftg
except ImportError:
    ftg = None

try:
    import flet_permission_handler as fph
except ImportError:
    fph = None

from core.database import IntegrityError, db_conn, Session
from core.utils import (
    launch_camera_app,
    send_project_invitation_email,
    show_message,
)


def start_main_app(page: ft.Page, on_logout):
    current_edit_id = [None]
    selected_photo_path = ""

    # ------------------------------------------------------------------
    # Dọn sạch mọi FilePicker rác
    # ------------------------------------------------------------------
    if hasattr(page, "services"):
        for p in [c for c in page.services if isinstance(c, ft.FilePicker)]:
            page.services.remove(p)
    for p in [c for c in page.overlay if isinstance(c, ft.FilePicker)]:
        page.overlay.remove(p)
    try:
        page.update()
    except Exception:
        pass

    def _register_file_picker(picker: ft.FilePicker):
        if hasattr(page, "services"):
            page.services.append(picker)
        else:
            page.overlay.append(picker)

    def _unregister_file_picker(picker: ft.FilePicker):
        if hasattr(page, "services") and picker in page.services:
            page.services.remove(picker)
        if picker in page.overlay:
            page.overlay.remove(picker)

    def set_appbar(title, actions=None):
        page.appbar = ft.AppBar(
            title=ft.Text(title, color=ft.Colors.WHITE, overflow=ft.TextOverflow.ELLIPSIS),
            leading=ft.IconButton(icon=ft.Icons.ARROW_BACK, icon_color=ft.Colors.WHITE,
                                  on_click=lambda e: show_dashboard()),
            bgcolor=ft.Colors.BLUE_800,
            toolbar_height=65,
            actions=actions or []
        )

    # ==========================================
    # 1. TRANG CHỦ (DASHBOARD)
    # ==========================================
    def show_dashboard(e=None):
        if Session.project is None:
            project = db_conn.execute(
                "SELECT p.id, p.ten_du_an FROM projects p JOIN project_members pm ON pm.project_id = p.id WHERE pm.user_id = ? ORDER BY p.id DESC LIMIT 1",
                (Session.user["id"],),
            ).fetchone()
            if project:
                Session.project = {"id": project[0], "name": project[1]}

        Session.refresh_project_role(db_conn)

        project_info = None
        if Session.project:
            project_info = db_conn.execute(
                "SELECT ten_du_an, hang_muc, dia_diem, chu_dau_tu, nha_thau FROM projects WHERE id = ?",
                (Session.project["id"],)
            ).fetchone()

        project_name = project_info[0] if project_info else "Chưa thiết lập công trình"
        details = [
            f"Hạng mục: {project_info[1] or 'Chưa cập nhật'}" if project_info else "Hạng mục: Chưa cập nhật",
            f"Địa điểm: {project_info[2] or 'Chưa cập nhật'}" if project_info else "Địa điểm: Chưa cập nhật",
            f"Chủ đầu tư: {project_info[3] or 'Chưa cập nhật'}" if project_info else "Chủ đầu tư: Chưa cập nhật",
            f"Nhà thầu: {project_info[4] or 'Chưa cập nhật'}" if project_info else "Nhà thầu: Chưa cập nhật",
        ]

        available_projects = db_conn.execute(
            "SELECT p.id, p.ten_du_an FROM projects p JOIN project_members pm ON pm.project_id = p.id WHERE pm.user_id = ? ORDER BY p.id DESC",
            (Session.user["id"],),
        ).fetchall()

        def select_project(event):
            selected = db_conn.execute(
                "SELECT id, ten_du_an FROM projects WHERE id = ? AND EXISTS (SELECT 1 FROM project_members WHERE project_id = ? AND user_id = ?)",
                (event.control.value, event.control.value, Session.user["id"]),
            ).fetchone()
            if selected:
                Session.project = {"id": selected[0], "name": selected[1]}
                Session.refresh_project_role(db_conn)
                show_dashboard()

        project_selector = None
        if available_projects:
            project_selector = ft.Dropdown(
                label="Dự án đang mở",
                value=str(Session.project["id"]) if Session.project else str(available_projects[0][0]),
                options=[ft.dropdown.Option(str(project_id), p_name) for project_id, p_name in available_projects]
            )
            project_selector.on_change = select_project

        menu_items = [
            ("Thư viện công trình", ft.Icons.DOMAIN, lambda e: show_project_library()),
            ("Thông tin công trình", ft.Icons.CONSTRUCTION, open_project_settings),
            ("Hồ sơ đã lưu", ft.Icons.FOLDER_COPY, lambda e: show_logs()),
            ("Tiến độ công việc", ft.Icons.TASK_ALT, lambda e: show_progress()),
            ("Nhật ký thi công", ft.Icons.MENU_BOOK, lambda e: show_logs()),
            ("Biên bản nghiệm thu", ft.Icons.ASSIGNMENT_TURNED_IN, lambda e: show_acceptance()),
            ("Thư viện công việc", ft.Icons.PERM_MEDIA, lambda e: show_work_library()),
        ]
        grid = ft.GridView(expand=False, height=390, max_extent=180, child_aspect_ratio=1.35, spacing=10, run_spacing=10)
        for title, icon, handler in menu_items:
            grid.controls.append(ft.Container(
                content=ft.Column(
                    [ft.Icon(icon, size=30, color=ft.Colors.BLUE_700),
                     ft.Text(title, weight=ft.FontWeight.BOLD, text_align=ft.TextAlign.CENTER)],
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
                bgcolor=ft.Colors.WHITE, border_radius=10, padding=10, on_click=handler))

        page.clean()
        page.appbar = ft.AppBar(
            title=ft.Text("Trang chủ", color=ft.Colors.WHITE),
            bgcolor=ft.Colors.BLUE_800,
            actions=[
                ft.IconButton(icon=ft.Icons.ACCOUNT_CIRCLE, tooltip="Quản lý tài khoản",
                              icon_color=ft.Colors.WHITE, on_click=show_account_management),
                ft.IconButton(icon=ft.Icons.EMAIL, tooltip="Hộp thư email",
                              icon_color=ft.Colors.WHITE, on_click=show_inbox)
            ]
        )
        page.floating_action_button = None

        dashboard_controls = [ft.Text(f"Xin chào, {Session.user['username']}", size=22, weight=ft.FontWeight.BOLD)]
        if project_selector:
            dashboard_controls.append(project_selector)
        dashboard_controls.extend([
            ft.Container(content=ft.Column(
                [ft.Text(project_name, size=20, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                 *[ft.Text(item, color=ft.Colors.WHITE_70) for item in details]],
                spacing=5),
                bgcolor=ft.Colors.BLUE_800, border_radius=12, padding=18),
            ft.Text("DANH MỤC CHỨC NĂNG", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_700),
            grid,
        ])
        page.add(ft.Container(content=ft.Column(dashboard_controls, scroll=ft.ScrollMode.AUTO, spacing=14),
                              padding=14, expand=True))
        page.update()
        
    def show_project_library(e=None):
        projects = db_conn.execute(
            "SELECT p.id, p.ten_du_an, p.hang_muc, p.dia_diem, p.chu_dau_tu, p.nha_thau, "
            "p.tu_van_giam_sat, p.goi_thau, p.hop_dong_so, p.ngay_bat_dau, "
            "p.ngay_ket_thuc_du_kien, p.mo_ta, pm.role "
            "FROM projects p JOIN project_members pm ON pm.project_id = p.id "
            "WHERE pm.user_id = ? ORDER BY p.id DESC",
            (Session.user["id"],),
        ).fetchall()

        project_list = ft.ListView(expand=True, spacing=8, padding=4)
        for project in projects:
            project_id, name, item, location, owner, contractor, supervisor, package_name, contract, start_date, end_date, description, role = project[:13]
            project_details = [
                f"Hạng mục: {item}" if item else None,
                f"Địa điểm: {location}" if location else None,
                f"Chủ đầu tư: {owner}" if owner else None,
                f"Nhà thầu: {contractor}" if contractor else None,
                f"Tư vấn giám sát: {supervisor}" if supervisor else None,
                f"Gói thầu: {package_name}" if package_name else None,
                f"Hợp đồng: {contract}" if contract else None,
                f"Thời gian: {start_date or '...'} - {end_date or '...'}" if start_date or end_date else None,
                f"Mô tả: {description}" if description else None,
                f"Vai trò: {role.upper()}" if role else None,
            ]
            project_list.controls.append(ft.Container(
                content=ft.Row([
                    ft.Icon(ft.Icons.CONSTRUCTION, color=ft.Colors.BLUE_700),
                    ft.Column([
                        ft.Text(name or "Chưa đặt tên công trình", weight=ft.FontWeight.BOLD),
                        ft.Text("\n".join(detail for detail in project_details if detail), size=13,
                                color=ft.Colors.GREY_700),
                    ], expand=True, spacing=4),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=ft.Colors.GREY_600),
                ], vertical_alignment=ft.CrossAxisAlignment.START),
                bgcolor=ft.Colors.WHITE,
                padding=12,
                border_radius=8,
                on_click=lambda event, selected_id=project_id, selected_name=name: open_project(selected_id, selected_name),
            ))

        if not projects:
            project_list.controls.append(ft.Text("Chưa có công trình nào.", color=ft.Colors.GREY_700))

        page.clean()
        set_appbar("Thư viện công trình", [
            ft.IconButton(icon=ft.Icons.ADD, tooltip="Tạo công trình", icon_color=ft.Colors.WHITE,
                          on_click=lambda event: open_project_settings(new_project=True)),
        ])
        page.floating_action_button = None
        page.add(ft.Container(content=project_list, padding=12, expand=True))
        page.update()

    def open_project(project_id, project_name):
        Session.project = {"id": project_id, "name": project_name}
        Session.refresh_project_role(db_conn)
        open_project_settings()

    # ==========================================
    # 2. THÔNG TIN CÔNG TRÌNH
    # ==========================================
    def open_project_settings(e=None, new_project=False):
        project_id = Session.project["id"] if Session.project and not new_project else None
        if project_id:
            Session.refresh_project_role(db_conn)

        project_data = None
        if project_id:
            project_data = db_conn.execute(
                "SELECT ten_du_an, hang_muc, dia_diem, chu_dau_tu, nha_thau, tu_van_giam_sat, goi_thau, hop_dong_so, ngay_bat_dau, ngay_ket_thuc_du_kien, mo_ta FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()

        txt_project = ft.TextField(label="Tên dự án / công trình", value=project_data[0] if project_data else "", prefix_icon=ft.Icons.CONSTRUCTION)
        txt_item = ft.TextField(label="Hạng mục công trình", value=project_data[1] if project_data else "")
        txt_location = ft.TextField(label="Địa điểm xây dựng", value=project_data[2] if project_data else "")
        txt_owner = ft.TextField(label="Chủ đầu tư", value=project_data[3] if project_data else "", expand=True)
        txt_contractor = ft.TextField(label="Nhà thầu thi công", value=project_data[4] if project_data else "")
        txt_supervisor = ft.TextField(label="Tư vấn giám sát", value=project_data[5] if project_data else "", expand=True)
        txt_package = ft.TextField(label="Gói thầu", value=project_data[6] if project_data else "", expand=True)
        txt_contract = ft.TextField(label="Hợp đồng số", value=project_data[7] if project_data else "", expand=True)
        txt_start = ft.TextField(label="Ngày bắt đầu", value=project_data[8] if project_data else "", expand=True)
        txt_end = ft.TextField(label="Ngày kết thúc dự kiến", value=project_data[9] if project_data else "", expand=True)
        txt_description = ft.TextField(label="Mô tả dự án", value=project_data[10] if project_data else "", multiline=True, min_lines=3)

        member_username = ft.TextField(label="Email thành viên được mời", expand=True)
        member_role = ft.Dropdown(
            label="Phân quyền", value="kysu",
            options=[ft.dropdown.Option("kysu", "Kỹ sư"),
                     ft.dropdown.Option("giamsat", "Tư vấn giám sát"),
                     ft.dropdown.Option("chudautu", "Chủ đầu tư"),
                     ft.dropdown.Option("qs", "Kỹ sư QS")], width=145,
        )
        member_list = ft.Column(spacing=4)

        def refresh_members():
            member_list.controls.clear()
            if not project_id:
                member_list.controls.append(ft.Text("Lưu công trình trước khi thêm thành viên.", color=ft.Colors.GREY_700))
                return
            members = db_conn.execute(
                "SELECT u.id, u.username, u.email, pm.role FROM project_members pm JOIN users u ON u.id = pm.user_id WHERE pm.project_id = ? ORDER BY u.username",
                (project_id,)
            ).fetchall()
            for user_id, username, email, role in members:
                member_list.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.PERSON_OUTLINE, color=ft.Colors.BLUE_700),
                            ft.Column([ft.Text(username, weight=ft.FontWeight.BOLD),
                                       ft.Text(email or "Chưa có email", size=12)], expand=True, spacing=0),
                            ft.Text(role.upper(), size=12, color=ft.Colors.BLUE_700),
                            ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_400,
                                          data=user_id, tooltip="Xóa thành viên", on_click=remove_member),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.GREY_100, padding=6, border_radius=6,
                    )
                )

        def remove_member(e):
            if Session.role not in ["kysu"] or not project_id:
                show_message(page, "Chỉ kỹ sư quản lý mới được xóa thành viên.", ft.Colors.RED_700)
                return
            db_conn.execute("DELETE FROM project_members WHERE project_id = ? AND user_id = ?", (project_id, e.control.data))
            db_conn.commit()
            refresh_members()
            page.update()

        def add_member(e):
            if Session.role not in ["kysu"]:
                show_message(page, "Chỉ kỹ sư quản lý mới được thêm thành viên.", ft.Colors.RED_700)
                return
            if not project_id:
                show_message(page, "Hãy lưu thông tin công trình trước.", ft.Colors.RED_700)
                return
            invitee_email = member_username.value.strip().lower()
            if not invitee_email:
                show_message(page, "Vui lòng nhập email thành viên.", ft.Colors.RED_700)
                return
            role_label = {
                "kysu": "Kỹ sư",
                "giamsat": "Tư vấn giám sát",
                "chudautu": "Chủ đầu tư",
                "qs": "Kỹ sư QS",
            }.get(member_role.value, member_role.value)
            project_name = txt_project.value.strip() or "Dự án xây dựng"
            role_value = member_role.value
            inviter_user_id = Session.user["id"]
            inviter_name = Session.user["username"]
            page.run_task(
                save_and_send_invitation,
                invitee_email,
                role_value,
                role_label,
                project_name,
                project_id,
                inviter_user_id,
                inviter_name,
            )

        async def save_and_send_invitation(
            invitee_email,
            role_value,
            role_label,
            project_name,
            invited_project_id,
            inviter_user_id,
            inviter_name,
        ):
            try:
                def save_invitation():
                    def insert_invitation(connection):
                        invitation = connection.execute(
                            "INSERT INTO project_invitations "
                            "(project_id, inviter_user_id, invitee_email, role) "
                            "VALUES (?, ?, ?, ?) RETURNING id",
                            (
                                invited_project_id,
                                inviter_user_id,
                                invitee_email,
                                role_value,
                            ),
                        )
                        invitation_id = invitation.fetchone()[0]
                        connection.execute(
                            "INSERT INTO email_inbox "
                            "(recipient, sender, subject, body, project_id, invitation_id) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                invitee_email,
                                inviter_name,
                                "Lời mời tham gia dự án",
                                f"Bạn được mời tham gia dự án với vai trò {role_label}.",
                                invited_project_id,
                                invitation_id,
                            ),
                        )

                    db_conn.run_transaction(insert_invitation)

                await asyncio.to_thread(save_invitation)
                member_username.value = ""
                email_sent, email_error = await asyncio.to_thread(
                    send_project_invitation_email,
                    invitee_email,
                    invited_project_id,
                    project_name,
                    inviter_name,
                    role_label,
                )
                if email_sent:
                    show_message(page, "Đã lưu lời mời và gửi email mời tham gia dự án.")
                else:
                    show_message(
                        page,
                        "Lời mời đã được lưu trong hộp thư ứng dụng nhưng email chưa gửi được. "
                        + email_error,
                        ft.Colors.RED_700,
                    )
                refresh_members()
                page.update()
            except IntegrityError:
                show_message(page, "Lỗi thêm thành viên.", ft.Colors.RED_700)
            except Exception:
                logging.exception("Không thể lưu hoặc gửi lời mời tham gia dự án.")
                show_message(
                    page,
                    "Không thể lưu lời mời tham gia dự án. Vui lòng thử lại.",
                    ft.Colors.RED_700,
                )

        def save_project(e):
            nonlocal project_id
            if project_id and Session.role != "kysu":
                show_message(page, "Chỉ kỹ sư quản lý mới được cập nhật công trình.", ft.Colors.RED_700)
                return
            values = (txt_project.value.strip(), txt_item.value.strip(), txt_location.value.strip(),
                      txt_owner.value.strip(), txt_contractor.value.strip(), txt_supervisor.value.strip(),
                      txt_package.value.strip(), txt_contract.value.strip(), txt_start.value.strip(),
                      txt_end.value.strip(), txt_description.value.strip())
            if project_id:
                db_conn.execute(
                    "UPDATE projects SET ten_du_an=?, hang_muc=?, dia_diem=?, chu_dau_tu=?, nha_thau=?, tu_van_giam_sat=?, goi_thau=?, hop_dong_so=?, ngay_bat_dau=?, ngay_ket_thuc_du_kien=?, mo_ta=? WHERE id=?",
                    values + (project_id,))
            else:
                cursor = db_conn.execute(
                    "INSERT INTO projects (ten_du_an, hang_muc, dia_diem, chu_dau_tu, nha_thau, tu_van_giam_sat, goi_thau, hop_dong_so, ngay_bat_dau, ngay_ket_thuc_du_kien, mo_ta) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                    values)
                project_id_local = cursor.fetchone()[0]
                Session.project = {"id": project_id_local, "name": txt_project.value.strip()}
                db_conn.execute("INSERT INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)",
                                (project_id_local, Session.user["id"], "kysu"))
                project_id = project_id_local
            db_conn.commit()
            if project_id:
                Session.project = {"id": project_id, "name": txt_project.value.strip()}
            Session.refresh_project_role(db_conn)
            show_project_library()
            show_message(page, "Đã lưu thông tin công trình.")

        def delete_project(e):
            if Session.role not in ["kysu"]:
                show_message(page, "Chỉ kỹ sư quản lý mới được xóa công trình.", ft.Colors.RED_700)
                return
            db_conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            db_conn.commit()
            Session.project = None
            show_message(page, "Đã xóa dữ liệu công trình.")
            show_dashboard()

        refresh_members()
        page.clean()
        set_appbar("Thông tin công trình", [ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                                                          icon_color=ft.Colors.WHITE, on_click=show_dashboard)])
        page.floating_action_button = None
        form_controls = [
            ft.Text("THÔNG TIN DỰ ÁN", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
            txt_project, txt_item, txt_location,
            ft.Row([txt_owner, txt_supervisor], spacing=8), txt_contractor,
            ft.Row([txt_package, txt_contract], spacing=8),
            ft.Row([txt_start, txt_end], spacing=8), txt_description,
            ft.Row([ft.FilledButton("Lưu dữ liệu", icon=ft.Icons.SAVE, on_click=save_project),
                    ft.OutlinedButton("Xóa dữ liệu", icon=ft.Icons.DELETE_OUTLINE, on_click=delete_project)],
                   alignment=ft.MainAxisAlignment.END),
            ft.Divider(),
            ft.Text("QUẢN LÝ THÀNH VIÊN", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
            ft.Row([member_username, member_role,
                    ft.IconButton(icon=ft.Icons.PERSON_ADD, tooltip="Thêm thành viên", on_click=add_member)], spacing=6),
            member_list,
        ]
        page.add(ft.Container(content=ft.Column(form_controls, scroll=ft.ScrollMode.AUTO, spacing=10),
                              padding=16, bgcolor=ft.Colors.WHITE, expand=True))
        page.update()

    # ==========================================
    # 3. NHẬT KÝ THI CÔNG
    # ==========================================
    def open_log_settings(e=None):
        if Session.role not in ["kysu"]:
            show_message(page, "Chỉ Kỹ sư quản lý mới được cài đặt biểu mẫu.", ft.Colors.RED_700)
            return

        settings = db_conn.execute(
            "SELECT tieu_de, hien_thi_ky_1, don_vi_1, nguoi_ky_1, hien_thi_ky_2, don_vi_2, nguoi_ky_2 FROM log_settings WHERE project_id=?",
            (Session.project["id"],)).fetchone()
        if not settings:
            db_conn.execute("INSERT INTO log_settings (project_id) VALUES (?)", (Session.project["id"],))
            db_conn.commit()
            settings = ("NHẬT KÝ THI CÔNG", 1, "NHÀ THẦU THI CÔNG", "", 1, "TƯ VẤN GIÁM SÁT", "")

        t_title = ft.TextField(label="Tiêu đề", value=settings[0])
        chk_1 = ft.Checkbox(label="Hiển thị đơn vị ký 1", value=bool(settings[1]))
        t_dv1 = ft.TextField(label="Tên đơn vị ký 1", value=settings[2])
        t_nk1 = ft.TextField(label="Tên người ký 1", value=settings[3])
        chk_2 = ft.Checkbox(label="Hiển thị đơn vị ký 2", value=bool(settings[4]))
        t_dv2 = ft.TextField(label="Tên đơn vị ký 2", value=settings[5])
        t_nk2 = ft.TextField(label="Tên người ký 2", value=settings[6])

        def save_settings(dlg):
            db_conn.execute(
                "UPDATE log_settings SET tieu_de=?, hien_thi_ky_1=?, don_vi_1=?, nguoi_ky_1=?, hien_thi_ky_2=?, don_vi_2=?, nguoi_ky_2=? WHERE project_id=?",
                (t_title.value, int(chk_1.value), t_dv1.value, t_nk1.value,
                 int(chk_2.value), t_dv2.value, t_nk2.value, Session.project["id"]))
            db_conn.commit()
            if hasattr(page, "close"): page.close(dlg)
            else: dlg.open = False; page.update()
            show_message(page, "Đã lưu cài đặt Nhật ký.")

        dlg = ft.AlertDialog(
            title=ft.Text("Cài đặt nhật ký", weight=ft.FontWeight.BOLD),
            content=ft.Column([
                t_title, ft.Divider(),
                ft.Text("Đơn vị ký", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
                chk_1, t_dv1, t_nk1, ft.Divider(),
                chk_2, t_dv2, t_nk2
            ], scroll=ft.ScrollMode.AUTO, tight=True),
            actions=[ft.FilledButton(content=ft.Text("Lưu cài đặt"), on_click=lambda e: save_settings(dlg))]
        )
        if hasattr(page, "open"): page.open(dlg)
        else: page.overlay.append(dlg); dlg.open = True; page.update()

    async def export_pdf_log(log_id):
        from core.export_pdf import generate_nhat_ky_pdf

        def load_pdf_rows():
            set_row = db_conn.execute(
                "SELECT tieu_de, hien_thi_ky_1, don_vi_1, nguoi_ky_1, "
                "hien_thi_ky_2, don_vi_2, nguoi_ky_2 FROM log_settings "
                "WHERE project_id=?",
                (Session.project["id"],),
            ).fetchone()
            log_row = db_conn.execute(
                """
                SELECT ngay_thi_cong, thoi_tiet_sang, thoi_tiet_chieu,
                thoi_tiet_toi, nhiet_do_sang, nhiet_do_chieu, so_cong_nhan,
                cb_ky_thuat, may_moc, hang_muc, mo_ta, cong_viec_nghiem_thu,
                ve_sinh_mt, an_toan_ld, su_co, kien_nghi
                FROM nhat_ky WHERE id=?
                """,
                (log_id,),
            ).fetchone()
            return set_row, log_row

        set_row, log_row = await asyncio.to_thread(load_pdf_rows)
        if log_row is None:
            show_message(page, "Không tìm thấy nhật ký cần xuất.", ft.Colors.RED_700)
            return

        if not set_row:
            set_row = ("NHẬT KÝ THI CÔNG", 1, "NHÀ THẦU THI CÔNG", "", 1, "TƯ VẤN GIÁM SÁT", "")
        settings = {"tieu_de": set_row[0], "hien_thi_ky_1": set_row[1], "don_vi_1": set_row[2],
                    "nguoi_ky_1": set_row[3], "hien_thi_ky_2": set_row[4], "don_vi_2": set_row[5],
                    "nguoi_ky_2": set_row[6]}

        data = {
            "ngay_thi_cong": log_row[0], "tt_sang": log_row[1], "tt_chieu": log_row[2], "tt_toi": log_row[3],
            "nd_sang": log_row[4], "nd_chieu": log_row[5], "so_cong_nhan": log_row[6], "cb_ky_thuat": log_row[7],
            "may_moc": log_row[8], "hang_muc": log_row[9], "mo_ta": log_row[10], "cong_viec_nghiem_thu": log_row[11],
            "ve_sinh_mt": log_row[12], "an_toan_ld": log_row[13], "su_co": log_row[14], "kien_nghi": log_row[15]
        }

        filename = f"NhatKy_{log_row[0].replace('/', '')}_{log_id}.pdf"
        try:
            platform_name = str(page.platform).lower().rsplit(".", 1)[-1]
            is_android = platform_name == "android"
            if is_android:
                documents_dir = await ft.StoragePaths().get_application_documents_directory()
                filepath = os.path.join(documents_dir, filename)
            else:
                filepath = os.path.join(os.getcwd(), filename)

            await asyncio.to_thread(
                generate_nhat_ky_pdf, filepath, data, settings
            )
            if is_android:
                with open(filepath, "rb") as pdf_file:
                    pdf_bytes = pdf_file.read()
                pdf_picker = ft.FilePicker()
                _register_file_picker(pdf_picker)
                try:
                    saved_path = await pdf_picker.save_file(
                        file_name=filename,
                        allowed_extensions=["pdf"],
                        src_bytes=pdf_bytes,
                    )
                finally:
                    _unregister_file_picker(pdf_picker)

                if saved_path:
                    show_message(page, f"Đã lưu PDF: {saved_path}")
                else:
                    show_message(
                        page,
                        "Đã hủy lưu PDF.",
                        ft.Colors.ORANGE_700,
                    )
            else:
                show_message(page, f"Đã xuất PDF: {filename}")
                if os.name == "nt":
                    os.startfile(filepath)
                else:
                    opener = "open" if platform_name == "macos" else "xdg-open"
                    subprocess.Popen([opener, filepath])
        except Exception as ex:
            show_message(page, f"Lỗi tạo PDF: {ex}", ft.Colors.RED_700)

    def show_logs(e=None):
        if not Session.project:
            show_message(page, "Hãy tạo hoặc chọn công trình trước.", ft.Colors.RED_700)
            return

        page.clean()
        list_view = ft.ListView(expand=True, spacing=12, padding=4)

        async def load_logs_from_db(keyword=""):
            list_view.controls.clear()
            keyword = (keyword or "").strip()
            query = ("SELECT id, hang_muc, mo_ta, duong_dan_anh, so_cong_nhan, may_moc, ngay_thi_cong, "
                     "ca_thi_cong, thoi_tiet_sang, thoi_tiet_chieu, thoi_tiet_toi, nhiet_do_sang, nhiet_do_chieu, "
                     "nhiet_do_toi, thoi_tiet FROM nhat_ky WHERE project_id = ? AND (hang_muc LIKE ? OR mo_ta LIKE ? OR ngay_thi_cong LIKE ?) ORDER BY id DESC")
            rows = await asyncio.to_thread(
                lambda: db_conn.execute(
                    query,
                    (
                        Session.project["id"],
                        f"%{keyword}%",
                        f"%{keyword}%",
                        f"%{keyword}%",
                    ),
                ).fetchall()
            )

            if not rows:
                list_view.controls.append(
                    ft.Container(content=ft.Text("Chưa có nhật ký nào. Bấm dấu cộng (+) phía trên để thêm mới hoặc dùng Menu chức năng ở góc dưới bên phải.",
                                                  color=ft.Colors.GREY_700), padding=10))
                page.update()
                return

            for row in rows:
                id_log, hang_muc, mo_ta, anh, cong_nhan, may_moc, ngay, ca, tt_sang, tt_chieu, tt_toi, n_sang, n_chieu, n_toi, old_tt = row
                actions = ft.Row(spacing=0, alignment=ft.MainAxisAlignment.END)
                actions.controls.append(ft.IconButton(icon=ft.Icons.PICTURE_AS_PDF, icon_color=ft.Colors.RED,
                                                       tooltip="In PDF", on_click=lambda e, lid=id_log: page.run_task(export_pdf_log, lid)))
                if Session.role in ["kysu", "qs"]:
                    actions.controls.extend([
                        ft.IconButton(icon=ft.Icons.EDIT, icon_color=ft.Colors.BLUE, tooltip="Sửa",
                                      on_click=lambda e, lid=id_log: open_form(lid)),
                        ft.IconButton(icon=ft.Icons.DELETE, icon_color=ft.Colors.RED_400,
                                      on_click=lambda e, lid=id_log: [
                                          db_conn.execute("DELETE FROM nhat_ky WHERE id=?", (lid,)),
                                          db_conn.commit(),
                                          page.run_task(load_logs_from_db, search_field.value or "")
                                      ])
                    ])
                list_view.controls.append(ft.Container(
                    content=ft.Row([
                        ft.Container(content=ft.Icon(ft.Icons.DESCRIPTION, color=ft.Colors.BLUE), width=40),
                        ft.Column([ft.Text(hang_muc or "Nhật ký thi công", weight=ft.FontWeight.BOLD),
                                   ft.Text(f"Ngày: {ngay}", size=12, color=ft.Colors.GREY_700)], expand=True),
                        actions]),
                    border_radius=10, padding=10, bgcolor=ft.Colors.WHITE,
                    border=ft.Border.all(1, ft.Colors.GREY_300)
                ))
            page.update()

        search_field = ft.TextField(
            hint_text="Tìm nhật ký (hạng mục, nội dung, ngày)...",
            prefix_icon=ft.Icons.SEARCH,
            border=ft.OutlineInputBorder(border_radius=8),
            bgcolor=ft.Colors.WHITE,
            expand=True,
            on_change=lambda ev: page.run_task(load_logs_from_db, ev.control.value)
        )

        def confirm_delete_all_logs():
            def perform_delete(e):
                db_conn.execute("DELETE FROM nhat_ky WHERE project_id = ?", (Session.project["id"],))
                db_conn.commit()
                close_dialog(dlg_confirm)
                page.run_task(load_logs_from_db)
                show_message(page, "Đã xóa toàn bộ nhật ký thi công!")

            def close_dialog(dlg):
                if hasattr(page, "close"): page.close(dlg)
                else: dlg.open = False; page.update()

            dlg_confirm = ft.AlertDialog(
                title=ft.Text("Cảnh báo", weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                content=ft.Text("Bạn có chắc chắn muốn XÓA TOÀN BỘ nhật ký thi công của dự án này? Hành động này không thể hoàn tác!"),
                actions=[
                    ft.TextButton("Hủy", on_click=lambda e: close_dialog(dlg_confirm)),
                    ft.FilledButton("Xóa tất cả", style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700), on_click=perform_delete)
                ]
            )
            if hasattr(page, "open"): page.open(dlg_confirm)
            else: page.overlay.append(dlg_confirm); dlg_confirm.open = True; page.update()

        def close_menu_sheet(e=None):
            if hasattr(page, "close"): page.close(menu_sheet)
            else: menu_sheet.open = False; page.update()

        def open_menu_sheet(e):
            if hasattr(page, "open"): page.open(menu_sheet)
            else: menu_sheet.open = True; page.update()

        menu_sheet = ft.BottomSheet(
            content=ft.Container(
                padding=10,
                bgcolor=ft.Colors.WHITE,
                content=ft.Column(
                    tight=True,
                    controls=[
                        ft.ListTile(title=ft.Text("Cài đặt mẫu in", size=15), leading=ft.Icon(ft.Icons.SETTINGS), on_click=lambda e: [close_menu_sheet(), open_log_settings(e)]),
                        ft.Divider(height=1),
                        ft.ListTile(title=ft.Text("Xóa toàn bộ nhật ký", color=ft.Colors.RED_700, size=15), leading=ft.Icon(ft.Icons.DELETE_OUTLINE, color=ft.Colors.RED_700), on_click=lambda e: [close_menu_sheet(), confirm_delete_all_logs()]),
                        ft.Container(height=20)
                    ]
                )
            )
        )
        if not hasattr(page, "open"):
            page.overlay.append(menu_sheet)

        actions = [
            ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                          icon_color=ft.Colors.WHITE, on_click=show_dashboard)
        ]
        set_appbar("Hồ sơ nhật ký thi công", actions)

        page.floating_action_button = (
            ft.FloatingActionButton(icon=ft.Icons.MENU, tooltip="Menu chức năng",
                                    on_click=open_menu_sheet)
            if Session.role in ["kysu", "qs"] else None
        )

        page.add(ft.Container(
            content=ft.Column([
                ft.Row([
                    search_field,
                    ft.IconButton(icon=ft.Icons.ADD, icon_color=ft.Colors.GREEN_700,
                                  on_click=lambda ev: open_form(None), tooltip="Lập nhật ký mới")
                ], spacing=4),
                list_view
            ], expand=True, spacing=10),
            padding=12, expand=True
        ))
        page.run_task(load_logs_from_db)

    def open_form(edit_id=None):
        if Session.role not in ["kysu", "qs"]:
            show_message(page, "Chỉ được xem nhật ký.", ft.Colors.RED_700)
            return
        page.clean()
        set_appbar("Chỉnh Sửa Nhật Ký" if edit_id else "Lập Nhật Ký Mới")

        # Danh sách chứa nhiều đường dẫn ảnh hiện trường
        selected_photo_paths = []

        txt_ngay = ft.TextField(label="Ngày thi công", value=datetime.now().strftime("%d/%m/%Y"), expand=True)
        dd_tt_sang = ft.Dropdown(label="Thời tiết Sáng", options=[ft.dropdown.Option("Nắng"), ft.dropdown.Option("Mưa"), ft.dropdown.Option("Râm mát")], value="Nắng", expand=4)
        txt_nd_sang = ft.TextField(label="NĐ(°C)", expand=2)
        dd_tt_chieu = ft.Dropdown(label="Thời tiết Chiều", options=[ft.dropdown.Option("Nắng"), ft.dropdown.Option("Mưa"), ft.dropdown.Option("Râm mát")], value="Nắng", expand=4)
        txt_nd_chieu = ft.TextField(label="NĐ(°C)", expand=2)
        txt_workers = ft.TextField(label="Số công nhân", expand=True)
        txt_cb_ky_thuat = ft.TextField(label="Số lượng CB Kỹ thuật", expand=True)

        # Khu vực hiển thị danh sách nhiều ảnh thumbnail dạng lưới ngang
        photos_row = ft.Row(wrap=True, spacing=8)

        def refresh_photos_ui():
            photos_row.controls.clear()
            for path in selected_photo_paths:
                if os.path.exists(path):
                    def remove_img(e, p=path):
                        if p in selected_photo_paths:
                            selected_photo_paths.remove(p)
                            refresh_photos_ui()

                    photos_row.controls.append(
                        ft.Stack([
                            ft.Image(src=path, width=90, height=90, fit="cover", border_radius=8),
                            ft.IconButton(
                                icon=ft.Icons.CANCEL, icon_color=ft.Colors.RED_400,
                                icon_size=20, on_click=remove_img,
                                top=0, right=0
                            )
                        ], width=90, height=90)
                    )
            photos_row.update()

        image_picker = ft.FilePicker()
        _register_file_picker(image_picker)

        def add_camera_photos(paths):
            added_count = 0
            for path in paths:
                if path and os.path.isfile(path) and path not in selected_photo_paths:
                    selected_photo_paths.append(path)
                    added_count += 1
            if added_count:
                refresh_photos_ui()
            return added_count

        def open_desktop_camera():
            def run_camera_task():
                try:
                    added_count = add_camera_photos(launch_camera_app())
                    if added_count:
                        show_message(page, f"Đã nhận {added_count} ảnh từ camera.")
                    else:
                        show_message(page, "Chưa có ảnh nào được chụp.", ft.Colors.ORANGE_700)
                except Exception as ex:
                    show_message(page, f"Lỗi mở camera: {ex}", ft.Colors.RED_700)

            page.run_thread(run_camera_task)

        async def pick_image_click(e):
            try:
                files = await image_picker.pick_files(
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=["png", "jpg", "jpeg"],
                    allow_multiple=True,
                    with_data=True,
                )
            except (OSError, RuntimeError, ValueError, ft.FletException) as ex:
                show_message(page, f"Lỗi chọn ảnh: {ex}", ft.Colors.RED_700)
                return

            if not files:
                return

            added_count = 0
            skipped_count = 0
            save_errors = []
            photo_dir = None
            for picked_file in files:
                extension = os.path.splitext(picked_file.name)[1].lower()
                if extension not in {".png", ".jpg", ".jpeg"}:
                    skipped_count += 1
                    continue

                path = None
                file_bytes = picked_file.bytes
                try:
                    if file_bytes:
                        if photo_dir is None:
                            documents_dir = await ft.StoragePaths().get_application_documents_directory()
                            photo_dir = os.path.join(documents_dir, "camera_images")
                            os.makedirs(photo_dir, exist_ok=True)

                        digest = sha256(file_bytes).hexdigest()
                        path = os.path.join(photo_dir, f"selected_{digest}{extension}")
                        if not os.path.isfile(path):
                            with open(path, "wb") as image_file:
                                image_file.write(file_bytes)
                    elif picked_file.path and os.path.isfile(picked_file.path):
                        path = picked_file.path
                except (OSError, RuntimeError, ValueError, ft.FletException) as ex:
                    skipped_count += 1
                    save_errors.append(f"{picked_file.name}: {ex}")
                    continue

                if path is None:
                    skipped_count += 1
                elif path not in selected_photo_paths:
                    selected_photo_paths.append(path)
                    added_count += 1

            if added_count:
                refresh_photos_ui()
            if save_errors:
                show_message(
                    page,
                    f"Đã thêm {added_count} ảnh; không thể lưu {len(save_errors)} ảnh. {save_errors[0]}",
                    ft.Colors.RED_700,
                )
            elif added_count and not skipped_count:
                show_message(page, f"Đã thêm {added_count} ảnh từ thiết bị.")
            elif added_count:
                show_message(
                    page,
                    f"Đã thêm {added_count} ảnh; bỏ qua {skipped_count} tệp không đọc được.",
                    ft.Colors.ORANGE_700,
                )
            elif skipped_count:
                show_message(
                    page,
                    f"Không thể thêm {skipped_count} tệp đã chọn.",
                    ft.Colors.ORANGE_700,
                )

        async def capture_camera_click(e):
            platform_name = str(page.platform).lower().rsplit(".", 1)[-1]
            is_android = platform_name == "android"
            is_desktop = platform_name in {"windows", "macos", "linux"}

            if not is_android:
                if not is_desktop:
                    show_message(
                        page,
                        "Không hỗ trợ mở camera trực tiếp trên nền tảng này. Hãy chọn ảnh từ thiết bị.",
                        ft.Colors.ORANGE_700,
                    )
                    return
                open_desktop_camera()
                return

            if fc is None or fph is None:
                show_message(
                    page,
                    "Thiếu plugin camera hoặc quyền camera trên Android. Hãy chọn ảnh từ thiết bị.",
                    ft.Colors.ORANGE_700,
                )
                return

            if is_android:
                try:
                    permission_handler = fph.PermissionHandler()
                    permission_status = await permission_handler.request(fph.Permission.CAMERA)
                    if permission_status != fph.PermissionStatus.GRANTED:
                        show_message(page, "Cần cấp quyền camera để chụp ảnh.", ft.Colors.RED_700)
                        return

                    geolocator = None
                    if ftg is not None:
                        geolocator = ftg.Geolocator()
                        if geolocator not in page.services:
                            page.services.append(geolocator)

                    camera = fc.Camera(expand=True, preview_enabled=True)
                    camera_status = ft.Text("Đang khởi tạo camera...", color=ft.Colors.WHITE)
                    live_preview = ft.Container(
                        content=camera,
                        expand=True,
                        bgcolor=ft.Colors.BLACK,
                    )
                    image_review = ft.Container(
                        expand=True,
                        bgcolor=ft.Colors.BLACK,
                        visible=False,
                    )
                    preview_stack = ft.Stack(
                        controls=[live_preview, image_review],
                        expand=True,
                    )
                    captured_photo_path = [None]

                    def close_camera(e=None):
                        photo_path = captured_photo_path[0]
                        if photo_path and photo_path not in selected_photo_paths:
                            selected_photo_paths.append(photo_path)
                            refresh_photos_ui()
                        close_dialog_mac(camera_sheet)

                    def use_captured_photo(e=None):
                        photo_path = captured_photo_path[0]
                        if photo_path and photo_path not in selected_photo_paths:
                            selected_photo_paths.append(photo_path)
                            refresh_photos_ui()
                        close_dialog_mac(camera_sheet)

                    def retake_photo(e=None):
                        photo_path = captured_photo_path[0]
                        if photo_path and os.path.exists(photo_path):
                            os.remove(photo_path)
                        captured_photo_path[0] = None
                        image_review.visible = False
                        live_preview.visible = True
                        capture_button.visible = True
                        retake_button.visible = False
                        use_photo_button.visible = False
                        camera_status.value = "Camera sẵn sàng."
                        page.update()

                    capture_button = ft.FilledButton(
                        "Chụp ảnh",
                        icon=ft.Icons.CAMERA_ALT,
                        disabled=True,
                        on_click=None,
                    )
                    retake_button = ft.OutlinedButton(
                        "Chụp lại",
                        icon=ft.Icons.REPLAY,
                        visible=False,
                        on_click=retake_photo,
                    )
                    use_photo_button = ft.FilledButton(
                        "Dùng ảnh",
                        icon=ft.Icons.CHECK,
                        visible=False,
                        on_click=use_captured_photo,
                    )

                    async def get_location_label():
                        if geolocator is None or ftg is None:
                            return "GPS: plugin chưa được cài đặt"
                        try:
                            permission = await geolocator.get_permission_status(timeout=10)
                            if permission not in (
                                ftg.GeolocatorPermissionStatus.WHILE_IN_USE,
                                ftg.GeolocatorPermissionStatus.ALWAYS,
                            ):
                                permission = await geolocator.request_permission(timeout=30)
                            if permission not in (
                                ftg.GeolocatorPermissionStatus.WHILE_IN_USE,
                                ftg.GeolocatorPermissionStatus.ALWAYS,
                            ):
                                return "GPS: không được cấp quyền"

                            position = await geolocator.get_current_position(timeout=15)
                            if position is None:
                                return "GPS: không nhận được vị trí"
                            return f"GPS: {position.latitude:.6f}, {position.longitude:.6f}"
                        except Exception as ex:
                            return f"GPS: không khả dụng ({ex})"

                    def stamp_photo(image_data, timestamp, location_label):
                        from io import BytesIO
                        from PIL import Image, ImageDraw, ImageFont

                        with Image.open(BytesIO(image_data)) as source_image:
                            image = source_image.convert("RGB")

                        font_size = max(18, image.width // 45)
                        try:
                            font = ImageFont.truetype(
                                "/system/fonts/Roboto-Regular.ttf", font_size
                            )
                        except OSError:
                            font = ImageFont.load_default()

                        lines = [timestamp, location_label]
                        draw = ImageDraw.Draw(image)
                        line_sizes = [
                            draw.textbbox((0, 0), line, font=font)
                            for line in lines
                        ]
                        line_heights = [bounds[3] - bounds[1] for bounds in line_sizes]
                        line_spacing = max(6, font_size // 4)
                        box_width = max(bounds[2] - bounds[0] for bounds in line_sizes)
                        box_height = sum(line_heights) + line_spacing * (len(lines) - 1)
                        left = 20
                        top = image.height - box_height - 42
                        overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
                        overlay_draw = ImageDraw.Draw(overlay)
                        overlay_draw.rounded_rectangle(
                            (left - 12, top - 10, left + box_width + 12, image.height - 18),
                            radius=8,
                            fill=(0, 0, 0, 165),
                        )
                        text_y = top
                        for line, line_height in zip(lines, line_heights):
                            overlay_draw.text(
                                (left, text_y), line, font=font, fill=(255, 255, 255, 255)
                            )
                            text_y += line_height + line_spacing

                        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
                        output = BytesIO()
                        image.save(output, format="JPEG", quality=95)
                        return output.getvalue()

                    async def take_native_photo(e):
                        capture_button.disabled = True
                        camera_status.value = "Đang lấy vị trí và chụp ảnh..."
                        page.update()
                        try:
                            location_label = await get_location_label()
                            captured_at = datetime.now().astimezone()
                            timestamp = captured_at.strftime("%d/%m/%Y %H:%M:%S %z")
                            image_data = await camera.take_picture()
                            try:
                                image_data = stamp_photo(image_data, timestamp, location_label)
                            except Exception as ex:
                                camera_status.value = f"Không thể đóng dấu ảnh ({ex}); vẫn lưu ảnh gốc."
                            documents_dir = await ft.StoragePaths().get_application_documents_directory()
                            photo_dir = os.path.join(documents_dir, "camera_images")
                            os.makedirs(photo_dir, exist_ok=True)
                            filename = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
                            photo_path = os.path.join(photo_dir, filename)
                            with open(photo_path, "wb") as image_file:
                                image_file.write(image_data)
                            captured_photo_path[0] = photo_path
                            image_review.content = ft.Image(
                                src=photo_path,
                                fit="contain",
                                expand=True,
                            )
                            live_preview.visible = False
                            image_review.visible = True
                            capture_button.visible = False
                            retake_button.visible = True
                            use_photo_button.visible = True
                            if not camera_status.value.startswith("Không thể đóng dấu ảnh"):
                                camera_status.value = f"{timestamp}  |  {location_label}"
                        except Exception as ex:
                            capture_button.disabled = False
                            camera_status.value = f"Lỗi chụp ảnh: {ex}"
                        page.update()

                    capture_button.on_click = take_native_photo
                    camera_sheet = ft.BottomSheet(
                        fullscreen=True,
                        use_safe_area=False,
                        dismissible=False,
                        draggable=False,
                        content=ft.Container(
                            expand=True,
                            padding=0,
                            bgcolor=ft.Colors.BLACK,
                            content=ft.Column(
                                [
                                    ft.Container(
                                        height=56,
                                        padding=ft.Padding.symmetric(horizontal=8),
                                        content=ft.Row(
                                            [
                                                ft.IconButton(
                                                    icon=ft.Icons.CLOSE,
                                                    icon_color=ft.Colors.WHITE,
                                                    tooltip="Thoát camera",
                                                    on_click=close_camera,
                                                ),
                                                ft.Text(
                                                    "Camera hiện trường",
                                                    color=ft.Colors.WHITE,
                                                    weight=ft.FontWeight.BOLD,
                                                    expand=True,
                                                ),
                                            ]
                                        ),
                                    ),
                                    ft.Container(content=camera_status, padding=8),
                                    preview_stack,
                                    ft.Container(
                                        padding=12,
                                        content=ft.Row(
                                            [capture_button, retake_button, use_photo_button],
                                            alignment=ft.MainAxisAlignment.CENTER,
                                            spacing=12,
                                        ),
                                    ),
                                ],
                                expand=True,
                                spacing=0,
                            ),
                        ),
                    )
                    open_dialog_mac(camera_sheet)
                    try:
                        cameras = await camera.get_available_cameras()
                        if not cameras:
                            camera_status.value = "Không tìm thấy camera trên thiết bị."
                        else:
                            selected_camera = next(
                                (item for item in cameras if item.lens_direction == fc.CameraLensDirection.BACK),
                                cameras[0],
                            )
                            await camera.initialize(
                                selected_camera,
                                fc.ResolutionPreset.MEDIUM,
                                enable_audio=False,
                                image_format_group=fc.ImageFormatGroup.JPEG,
                            )
                            camera_status.value = "Camera sẵn sàng. Nhấn Chụp ảnh để lưu thời gian và GPS."
                            capture_button.disabled = False
                    except Exception as ex:
                        camera_status.value = (
                            f"Không mở được camera: {ex}. Đóng màn hình này và chọn ảnh từ thiết bị."
                        )
                    page.update()
                except Exception as ex:
                    show_message(page, f"Lỗi mở camera: {ex}", ft.Colors.RED_700)
                return

        if not hasattr(Session, "machine_list"):
            Session.machine_list = ["Máy đào", "Máy ủi", "Máy lu", "Máy san gạt", "Cẩu tháp",
                                    "Máy trộn bê tông", "Máy bơm bê tông", "Máy hàn", "Máy phát điện"]

        machine_list_container = ft.Column(spacing=8)

        def close_dialog_mac(dlg):
            if hasattr(page, "close"): page.close(dlg)
            else: dlg.open = False; page.update()

        def open_dialog_mac(dlg):
            if hasattr(page, "open"): page.open(dlg)
            else: page.overlay.append(dlg); dlg.open = True; page.update()

        txt_new_machine = ft.TextField(label="Tên máy móc mới")

        def confirm_new_machine(e):
            new_mac = txt_new_machine.value.strip()
            if new_mac and new_mac not in Session.machine_list:
                Session.machine_list.append(new_mac)
            close_dialog_mac(dlg_new_machine)
            if new_mac:
                open_form(edit_id)

        dlg_new_machine = ft.AlertDialog(
            title=ft.Text("Thêm máy móc khác", weight=ft.FontWeight.BOLD),
            content=txt_new_machine,
            actions=[ft.TextButton(content=ft.Text("Hủy"), on_click=lambda e: close_dialog_mac(dlg_new_machine)),
                     ft.FilledButton(content=ft.Text("Thêm"), on_click=confirm_new_machine)]
        )

        def prompt_new(e):
            txt_new_machine.value = ""
            open_dialog_mac(dlg_new_machine)

        def create_machine_row(machine_name="", machine_qty="0"):
            t_name = ft.TextField(label="Tên máy móc", value=machine_name, expand=3)
            t_qty = ft.TextField(label="SL", value=machine_qty, keyboard_type=ft.KeyboardType.NUMBER, expand=1)

            def pick_mac(m):
                t_name.value = m
                t_name.update()

            menu_items = [ft.PopupMenuItem(content=ft.Text(mac), on_click=lambda e, m=mac: pick_mac(m)) for mac in Session.machine_list]
            menu_items.append(ft.PopupMenuItem(content=ft.Row([ft.Icon(ft.Icons.ADD, color=ft.Colors.BLUE),
                                                               ft.Text("Thêm máy khác...", color=ft.Colors.BLUE)]),
                                               on_click=prompt_new))
            popup_btn = ft.PopupMenuButton(icon=ft.Icons.ARROW_DROP_DOWN_CIRCLE_OUTLINED, tooltip="Chọn nhanh", items=menu_items)

            def remove_row(row_container):
                if len(machine_list_container.controls) > 1:
                    machine_list_container.controls.remove(row_container)
                    machine_list_container.update()
                else:
                    show_message(page, "Phải giữ lại ít nhất một dòng thiết bị.", ft.Colors.ORANGE_700)

            row_container = ft.Container(
                content=ft.Row([t_name, popup_btn, t_qty,
                                ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_400,
                                              on_click=lambda e: remove_row(row_container))],
                               vertical_alignment=ft.CrossAxisAlignment.CENTER))
            return row_container

        add_machine_btn = ft.TextButton(
            content=ft.Row([ft.Icon(ft.Icons.ADD, color=ft.Colors.PURPLE_700),
                            ft.Text("Thêm máy móc", color=ft.Colors.PURPLE_700, weight=ft.FontWeight.BOLD)]),
            on_click=lambda e: (machine_list_container.controls.append(create_machine_row()),
                                machine_list_container.update())
        )

        txt_name = ft.TextField(label="Công việc thực hiện", multiline=True)
        txt_nghiem_thu = ft.TextField(label="Công việc nghiệm thu trong ngày", multiline=True)
        dd_vsmt = ft.Dropdown(label="Vệ sinh MT", options=[ft.dropdown.Option("Tốt"), ft.dropdown.Option("Bình thường"), ft.dropdown.Option("Kém")], value="Tốt", expand=True)
        dd_atld = ft.Dropdown(label="An toàn LĐ", options=[ft.dropdown.Option("Tốt"), ft.dropdown.Option("Bình thường"), ft.dropdown.Option("Kém")], value="Tốt", expand=True)
        txt_su_co = ft.TextField(label="Sự cố phát sinh", value="Không", multiline=True)
        txt_kien_nghi = ft.TextField(label="Kiến nghị", value="Không", multiline=True)

        if edit_id:
            old = db_conn.execute(
                "SELECT ngay_thi_cong, thoi_tiet_sang, thoi_tiet_chieu, nhiet_do_sang, nhiet_do_chieu, so_cong_nhan, cb_ky_thuat, may_moc, hang_muc, cong_viec_nghiem_thu, ve_sinh_mt, an_toan_ld, su_co, kien_nghi, duong_dan_anh FROM nhat_ky WHERE id=?",
                (edit_id,)).fetchone()
            if old:
                txt_ngay.value, dd_tt_sang.value, dd_tt_chieu.value, txt_nd_sang.value, txt_nd_chieu.value, txt_workers.value, txt_cb_ky_thuat.value, raw_may_moc, txt_name.value, txt_nghiem_thu.value, dd_vsmt.value, dd_atld.value, txt_su_co.value, txt_kien_nghi.value, img_path = old
                if img_path:
                    raw_paths = img_path.replace("\n", "|").split("|")
                    selected_photo_paths = [p.strip() for p in raw_paths if p.strip() and os.path.exists(p.strip())]
                    refresh_photos_ui()
                if raw_may_moc:
                    for item in raw_may_moc.split(","):
                        if ":" in item:
                            m_name, m_qty = item.split(":", 1)
                            machine_list_container.controls.append(create_machine_row(m_name.strip(), m_qty.strip()))
                        else:
                            machine_list_container.controls.append(create_machine_row(item.strip(), "0"))

        if not machine_list_container.controls:
            machine_list_container.controls.append(create_machine_row("", "0"))

        def save_log(e):
            # Nối các đường dẫn ảnh lại bằng dấu '|' để lưu vào cơ sở dữ liệu
            combined_image_paths = "|".join(selected_photo_paths)

            machine_entries = []
            for ctrl in machine_list_container.controls:
                row_row = ctrl.content
                t_name = row_row.controls[0]
                t_qty = row_row.controls[2]
                m_name = t_name.value.strip()
                m_qty = t_qty.value.strip() or "0"
                if m_name:
                    machine_entries.append(f"{m_name}: {m_qty}")
            combined_machines = ", ".join(machine_entries)
            
            values = (Session.project["id"], Session.user["username"], txt_name.value, "", txt_workers.value,
                      combined_machines, combined_image_paths, txt_ngay.value, "Ngày", dd_tt_sang.value, dd_tt_chieu.value, "Nghỉ",
                      txt_nd_sang.value, txt_nd_chieu.value, "", txt_cb_ky_thuat.value, txt_nghiem_thu.value,
                      dd_vsmt.value, dd_atld.value, txt_su_co.value, txt_kien_nghi.value)
            if edit_id is None:
                db_conn.execute(
                    "INSERT INTO nhat_ky (project_id, created_by, hang_muc, mo_ta, so_cong_nhan, may_moc, duong_dan_anh, ngay_thi_cong, ca_thi_cong, thoi_tiet_sang, thoi_tiet_chieu, thoi_tiet_toi, nhiet_do_sang, nhiet_do_chieu, nhiet_do_toi, cb_ky_thuat, cong_viec_nghiem_thu, ve_sinh_mt, an_toan_ld, su_co, kien_nghi) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    values)
            else:
                db_conn.execute(
                    "UPDATE nhat_ky SET project_id=?, created_by=?, hang_muc=?, mo_ta=?, so_cong_nhan=?, may_moc=?, duong_dan_anh=?, ngay_thi_cong=?, ca_thi_cong=?, thoi_tiet_sang=?, thoi_tiet_chieu=?, thoi_tiet_toi=?, nhiet_do_sang=?, nhiet_do_chieu=?, nhiet_do_toi=?, cb_ky_thuat=?, cong_viec_nghiem_thu=?, ve_sinh_mt=?, an_toan_ld=?, su_co=?, kien_nghi=? WHERE id=?",
                    values + (edit_id,))
            db_conn.commit()
            show_message(page, "Đã lưu nhật ký thành công!")
            show_logs()

        form = ft.Container(
            content=ft.Column([
                ft.Text("THỜI TIẾT", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800), txt_ngay,
                ft.Row([dd_tt_sang, txt_nd_sang]), ft.Row([dd_tt_chieu, txt_nd_chieu]),
                ft.Divider(),
                ft.Text("HÌNH ẢNH HIỆN TRƯỜNG", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
                ft.Row([
                    ft.FilledButton("Chụp ảnh", icon=ft.Icons.CAMERA_ALT, on_click=capture_camera_click),
                    ft.OutlinedButton("Chọn từ thiết bị", icon=ft.Icons.PHOTO_LIBRARY, on_click=pick_image_click),
                ], spacing=10),
                photos_row, # Hiển thị dải nhiều ảnh thumbnail
                ft.Divider(),
                ft.Text("MÁY MÓC THIẾT BỊ", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
                ft.Row([txt_workers, txt_cb_ky_thuat]),
                machine_list_container,
                add_machine_btn,
                ft.Divider(),
                ft.Text("NỘI DUNG CÔNG VIỆC", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
                txt_name, txt_nghiem_thu,
                ft.Divider(),
                ft.Text("ĐÁNH GIÁ & KIẾN NGHỊ", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
                ft.Row([dd_vsmt, dd_atld]), txt_su_co, txt_kien_nghi,
                ft.FilledButton("LƯU NHẬT KÝ", on_click=save_log, style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_700, color=ft.Colors.WHITE))
            ], spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=20, expand=True
        )
        page.add(form)
        page.update()

    # ==========================================
    # 4. TIẾN ĐỘ CÔNG VIỆC
    # ==========================================
    def show_progress(e=None):
        if not Session.project:
            show_message(page, "Hãy tạo hoặc chọn công trình trước.", ft.Colors.RED_700)
            return

        page.clean()
        try:
            page.update()
        except Exception:
            pass

        if hasattr(page, "services"):
            for p in [c for c in page.services if isinstance(c, ft.FilePicker)]:
                page.services.remove(p)
        for p in [c for c in page.overlay if isinstance(c, ft.FilePicker)]:
            page.overlay.remove(p)

        progress_list = ft.ListView(expand=True, spacing=10, padding=4)

        def _parse_excel(file_source):
            try:
                from openpyxl import load_workbook
            except ImportError as ie:
                raise ImportError(
                    f"openpyxl chưa cài. Chạy: pip install openpyxl ({ie})"
                )

            wb = load_workbook(file_source, data_only=True, read_only=True)
            ws = wb.active

            rows_iter = ws.iter_rows(values_only=True)
            try:
                header = next(rows_iter)
            except StopIteration:
                wb.close()
                raise ValueError("File Excel trống, không có dữ liệu.")

            cols_lower = [
                str(c).strip().lower() if c is not None else ""
                for c in header
            ]

            def find_col_index(candidates, fallback_index=None):
                for i, c in enumerate(cols_lower):
                    for kw in candidates:
                        if kw in c:
                            return i
                if fallback_index is not None and fallback_index < len(cols_lower):
                    return fallback_index
                return None

            idx_name = find_col_index(
                ['tên công việc', 'ten cong viec', 'cong viec', 'công việc',
                 'hạng mục', 'hang muc'], 0)
            idx_bd = find_col_index(
                ['bắt đầu', 'bat dau', 'từ ngày', 'tu ngay', 'start'], 1)
            idx_kt = find_col_index(
                ['kết thúc', 'ket thuc', 'đến ngày', 'den ngay', 'end'], 2)
            idx_nc = find_col_index(
                ['nhân công', 'nhan cong', 'số lượng', 'so luong',
                 'công nhân', 'cong nhan'], 3)

            if idx_name is None:
                wb.close()
                raise ValueError("Không tìm thấy cột 'Tên công việc' trong file Excel.")

            def to_date_str(val):
                if val is None:
                    return ""
                if hasattr(val, "strftime"):
                    return val.strftime("%d/%m/%Y")
                s = str(val).strip()
                if s.lower() == "nan" or s == "":
                    return ""
                return s

            inserted = 0
            for row_idx, row in enumerate(rows_iter, start=2):
                try:
                    if row is None or len(row) == 0:
                        continue

                    raw_name = row[idx_name] if idx_name < len(row) else None
                    if raw_name is None:
                        continue
                    ten_cv = str(raw_name).strip()
                    if not ten_cv or ten_cv.lower() == "nan":
                        continue

                    ngay_bd = to_date_str(row[idx_bd]) if (idx_bd is not None and idx_bd < len(row)) else ""
                    ngay_kt = to_date_str(row[idx_kt]) if (idx_kt is not None and idx_kt < len(row)) else ""

                    raw_nc = row[idx_nc] if (idx_nc is not None and idx_nc < len(row)) else 0
                    nhan_cong = str(raw_nc).strip() if raw_nc is not None else "0"
                    if nhan_cong.lower() == "nan" or nhan_cong == "":
                        nhan_cong = "0"

                    ngay_cap_nhat = datetime.now().strftime("%d/%m/%Y %H:%M")
                    db_conn.execute(
                        "INSERT INTO project_progress "
                        "(project_id, ten_hang_muc, ngay_bat_dau, ngay_ket_thuc, "
                        "nhan_cong, trang_thai, ngay_cap_nhat) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (Session.project["id"], ten_cv, ngay_bd, ngay_kt,
                         nhan_cong, "Đang thi công", ngay_cap_nhat)
                    )
                    inserted += 1
                except Exception:
                    continue

            wb.close()
            db_conn.commit()
            return inserted

        async def _process_excel(file_path=None, file_bytes=None):
            if file_bytes is not None:
                file_source = BytesIO(file_bytes)
            elif file_path:
                file_source = file_path
            else:
                show_message(page, "Không đọc được nội dung file.", ft.Colors.RED_700)
                return
            try:
                count = await asyncio.to_thread(_parse_excel, file_source)
                if count > 0:
                    show_message(page, f"Đã nhập thành công {count} công việc từ Excel!")
                else:
                    show_message(page, "File không có dòng dữ liệu hợp lệ nào.", ft.Colors.ORANGE_800)
                page.run_task(load_progress)
            except ImportError as ie:
                show_message(page, f"Thiếu thư viện: {ie}", ft.Colors.RED_700)
            except Exception as ex:
                show_message(page, f"Lỗi đọc file Excel: {ex}", ft.Colors.RED_700)

        excel_picker = ft.FilePicker()
        _register_file_picker(excel_picker)

        async def pick_excel_click(e=None):
            try:
                files = await excel_picker.pick_files(
                    allowed_extensions=["xlsx"],
                    with_data=True,
                )
                if files:
                    selected_file = files[0]
                    await _process_excel(
                        getattr(selected_file, "path", None),
                        getattr(selected_file, "bytes", None),
                    )
            except Exception as ex:
                show_message(page, f"Lỗi mở hộp thoại chọn file: {ex}", ft.Colors.RED_700)

        async def load_progress(keyword=""):
            progress_list.controls.clear()
            keyword = (keyword or "").strip()
            rows = await asyncio.to_thread(
                lambda: db_conn.execute(
                    "SELECT id, ten_hang_muc, ngay_bat_dau, ngay_ket_thuc, "
                    "nhan_cong, ngay_nghiem_thu, may_thi_cong, tcvn "
                    "FROM project_progress WHERE project_id=? "
                    "AND ten_hang_muc LIKE ? ORDER BY id DESC",
                    (Session.project["id"], f"%{keyword}%"),
                ).fetchall()
            )

            if not rows:
                progress_list.controls.append(
                    ft.Container(content=ft.Text("Chưa có công việc nào. Bấm dấu cộng (+) phía trên để thêm mới.",
                                                  color=ft.Colors.GREY_700), padding=10))
                page.update()
                return

            for idx, (p_id, name, bd, kt, nc, nt, may, tcvn) in enumerate(rows, 1):
                actions = ft.Row(spacing=0)
                if Session.role in ["kysu", "qs"]:
                    actions.controls.extend([
                        ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Sửa",
                                      icon_color=ft.Colors.BLUE_700,
                                      on_click=lambda ev, item_id=p_id: open_progress_dialog(item_id)),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Xóa",
                                      icon_color=ft.Colors.RED_400,
                                      on_click=lambda ev, item_id=p_id: [
                                          db_conn.execute("DELETE FROM project_progress WHERE id=?", (item_id,)),
                                          db_conn.commit(),
                                          page.run_task(load_progress)
                                      ])
                    ])

                progress_list.controls.append(
                    ft.Container(
                        content=ft.Column([
                            ft.Row([
                                ft.Text(f"{idx}. {name}", weight=ft.FontWeight.BOLD, size=15, expand=True),
                                actions
                            ]),
                            ft.Row([
                                ft.Text(f"{bd or '...'} → {kt or '...'}", size=13, color=ft.Colors.GREY_700, expand=True),
                                ft.Text(f"Nhân công: {nc or '0'}", size=13, color=ft.Colors.BLUE_800, weight=ft.FontWeight.BOLD)
                            ])
                        ], spacing=4),
                        bgcolor=ft.Colors.WHITE, border_radius=8, padding=12,
                        border=ft.Border.all(1, ft.Colors.GREY_300)
                    )
                )
            page.update()

        search_field = ft.TextField(
            hint_text="Tìm công việc...",
            prefix_icon=ft.Icons.SEARCH,
            border=ft.OutlineInputBorder(border_radius=8),
            bgcolor=ft.Colors.WHITE,
            expand=True,
            on_change=lambda ev: page.run_task(load_progress, ev.control.value)
        )

        def open_progress_dialog(record_id=None):
            old = None
            if record_id:
                old = db_conn.execute(
                    "SELECT ten_hang_muc, ngay_bat_dau, ngay_ket_thuc, nhan_cong, tao_bien_ban, "
                    "ngay_nghiem_thu, gio_nghiem_thu, may_thi_cong, tcvn FROM project_progress WHERE id=?",
                    (record_id,)).fetchone()

            txt_name = ft.TextField(label="Tên công việc", value=old[0] if old else "", autofocus=True)
            txt_bd = ft.TextField(label="Từ ngày", value=old[1] if old else datetime.now().strftime("%d/%m/%Y"), expand=True)
            txt_kt = ft.TextField(label="Đến ngày", value=old[2] if old else datetime.now().strftime("%d/%m/%Y"), expand=True)
            txt_nc = ft.TextField(label="Nhân công", value=str(old[3]) if old and old[3] is not None else "7", width=95)
            chk_bb = ft.Checkbox(label="Tạo biên bản nghiệm thu", value=bool(old[4]) if old and old[4] is not None else True)
            txt_ngay_nt = ft.TextField(label="Ngày nghiệm thu", value=old[5] if old else datetime.now().strftime("%d/%m/%Y"), expand=True)
            txt_gio_nt = ft.TextField(label="Giờ nghiệm thu", value=old[6] if old else "08:00", width=110)
            txt_may = ft.TextField(label="Máy thi công", value=old[7] if old else "", expand=True)
            txt_tcvn = ft.TextField(label="TCVN", value=old[8] if old else "", expand=True)

            def close_dlg(dlg):
                if hasattr(page, "close"): page.close(dlg)
                else: dlg.open = False; page.update()

            def save_prog(dlg):
                name_val = txt_name.value.strip()
                if not name_val:
                    show_message(page, "Vui lòng nhập tên công việc.", ft.Colors.RED_700)
                    return
                vals = (name_val, txt_bd.value.strip(), txt_kt.value.strip(), txt_nc.value.strip(),
                        int(chk_bb.value), txt_ngay_nt.value.strip(), txt_gio_nt.value.strip(),
                        txt_may.value.strip(), txt_tcvn.value.strip())
                if record_id:
                    db_conn.execute(
                        "UPDATE project_progress SET ten_hang_muc=?, ngay_bat_dau=?, ngay_ket_thuc=?, "
                        "nhan_cong=?, tao_bien_ban=?, ngay_nghiem_thu=?, gio_nghiem_thu=?, may_thi_cong=?, tcvn=? WHERE id=?",
                        vals + (record_id,))
                else:
                    db_conn.execute(
                        "INSERT INTO project_progress (project_id, ten_hang_muc, ngay_bat_dau, ngay_ket_thuc, "
                        "nhan_cong, tao_bien_ban, ngay_nghiem_thu, gio_nghiem_thu, may_thi_cong, tcvn, trang_thai, ngay_cap_nhat) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (Session.project["id"],) + vals + ("Đang thi công", datetime.now().strftime("%d/%m/%Y %H:%M")))
                db_conn.commit()
                close_dlg(dlg)
                page.run_task(load_progress, search_field.value or "")
                show_message(page, "Đã lưu thông tin công việc.")

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Thêm/Sửa công việc", weight=ft.FontWeight.BOLD),
                content=ft.Column([
                    txt_name,
                    ft.Text("Tiến độ", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800, size=13),
                    ft.Row([txt_bd, txt_kt, txt_nc], spacing=8),
                    chk_bb,
                    ft.Row([txt_ngay_nt, txt_gio_nt], spacing=8),
                    ft.Row([txt_may], spacing=8),
                    ft.Row([txt_tcvn], spacing=8),
                ], tight=True, scroll=ft.ScrollMode.AUTO, spacing=10),
                actions=[
                    ft.TextButton(content=ft.Text("HỦY"), on_click=lambda e: close_dlg(dialog)),
                    ft.FilledButton(content=ft.Text("LƯU"), on_click=lambda e: save_prog(dialog))
                ]
            )
            if hasattr(page, "open"): page.open(dialog)
            else: page.overlay.append(dialog); dialog.open = True; page.update()

        def auto_create_logs():
            if not Session.project:
                return
            page.run_task(
                create_logs_from_progress,
                Session.project["id"],
                Session.user["username"],
            )

        async def create_logs_from_progress(project_id, username):
            def create_logs():
                def create_transaction(connection):
                    progress_items = connection.execute(
                        "SELECT ten_hang_muc, ngay_bat_dau, nhan_cong, may_thi_cong "
                        "FROM project_progress WHERE project_id = ?",
                        (project_id,),
                    ).fetchall()
                    if not progress_items:
                        return None

                    created_count = 0
                    for hang_muc, ngay_bd, nhan_cong, may_moc in progress_items:
                        ngay_thi_cong = ngay_bd or datetime.now().strftime("%d/%m/%Y")
                        existing = connection.execute(
                            "SELECT id FROM nhat_ky WHERE project_id = ? "
                            "AND hang_muc = ? AND ngay_thi_cong = ?",
                            (project_id, hang_muc, ngay_thi_cong),
                        ).fetchone()
                        if existing:
                            continue

                        connection.execute(
                            """
                            INSERT INTO nhat_ky
                            (project_id, created_by, hang_muc, ngay_thi_cong,
                            so_cong_nhan, may_moc, thoi_tiet_sang, thoi_tiet_chieu,
                            nhiet_do_sang, nhiet_do_chieu, cb_ky_thuat, ve_sinh_mt,
                            an_toan_ld, su_co, kien_nghi)
                            VALUES (?, ?, ?, ?, ?, ?, 'Nắng', 'Nắng', '32',
                            '34', '1', 'Tốt', 'Tốt', 'Không', 'Không')
                            """,
                            (
                                project_id,
                                username,
                                hang_muc,
                                ngay_thi_cong,
                                nhan_cong or "7",
                                may_moc or "Máy trộn bê tông: 1",
                            ),
                        )
                        created_count += 1
                    return created_count

                return db_conn.run_transaction(create_transaction)

            try:
                created_count = await asyncio.to_thread(create_logs)
            except Exception:
                logging.exception("Không thể tạo nhật ký từ tiến độ.")
                show_message(
                    page,
                    "Không thể tạo nhật ký từ tiến độ. Vui lòng thử lại.",
                    ft.Colors.RED_700,
                )
                return

            if created_count is None:
                show_message(
                    page,
                    "Không có công việc nào trong tiến độ để tạo nhật ký.",
                    ft.Colors.ORANGE_800,
                )
            elif created_count:
                show_message(
                    page,
                    f"Đã tự động tạo thành công {created_count} nhật ký thi công!",
                )
            else:
                show_message(
                    page,
                    "Tất cả các nhật ký theo tiến độ đã tồn tại từ trước.",
                    ft.Colors.BLUE_700,
                )

        def auto_create_acceptances():
            if not Session.project:
                return
            page.run_task(
                create_acceptances_from_progress,
                Session.project["id"],
                Session.user["username"],
            )

        async def create_acceptances_from_progress(project_id, username):
            def create_acceptances():
                def create_transaction(connection):
                    progress_items = connection.execute(
                        "SELECT ten_hang_muc, ngay_nghiem_thu, ngay_bat_dau, "
                        "may_thi_cong, tcvn FROM project_progress WHERE project_id = ? "
                        "AND (tao_bien_ban = 1 OR tao_bien_ban IS NULL)",
                        (project_id,),
                    ).fetchall()
                    if not progress_items:
                        return None

                    created_count = 0
                    for hang_muc, ngay_nt, ngay_bd, may_moc, tcvn in progress_items:
                        ngay_nghiem_thu_val = (
                            ngay_nt
                            or ngay_bd
                            or datetime.now().strftime("%d/%m/%Y")
                        )
                        tieu_de_bb = f"Nghiệm thu công việc: {hang_muc}"
                        existing = connection.execute(
                            "SELECT id FROM acceptance_records "
                            "WHERE project_id = ? AND hang_muc = ?",
                            (project_id, hang_muc),
                        ).fetchone()
                        if existing:
                            continue

                        noi_dung_chi_tiet = (
                            f"Thi công hoàn thành hạng mục {hang_muc}. "
                            f"Máy móc: {may_moc or 'Theo yêu cầu'}. "
                            f"Tiêu chuẩn: {tcvn or 'TCVN 5574:2018'}."
                        )
                        connection.execute(
                            """
                            INSERT INTO acceptance_records
                            (project_id, loai_phieu, tieu_de, hang_muc,
                            ngay_yeu_cau, ngay_nghiem_thu, trang_thai,
                            thanh_phan_tham_du, noi_dung, ghi_chu, created_by)
                            VALUES (?, 'Biên bản nghiệm thu công việc', ?, ?, ?, ?,
                            'Chờ nghiệm thu',
                            'Đại diện Tư vấn giám sát, Đại diện Nhà thầu thi công',
                            ?, 'Đạt yêu cầu kỹ thuật', ?)
                            """,
                            (
                                project_id,
                                tieu_de_bb,
                                hang_muc,
                                ngay_bd or datetime.now().strftime("%d/%m/%Y"),
                                ngay_nghiem_thu_val,
                                noi_dung_chi_tiet,
                                username,
                            ),
                        )
                        created_count += 1
                    return created_count

                return db_conn.run_transaction(create_transaction)

            try:
                created_count = await asyncio.to_thread(create_acceptances)
            except Exception:
                logging.exception("Không thể tạo biên bản nghiệm thu từ tiến độ.")
                show_message(
                    page,
                    "Không thể tạo biên bản nghiệm thu từ tiến độ. Vui lòng thử lại.",
                    ft.Colors.RED_700,
                )
                return

            if created_count is None:
                show_message(
                    page,
                    "Không có công việc nào đủ điều kiện tạo biên bản nghiệm thu.",
                    ft.Colors.ORANGE_800,
                )
            elif created_count:
                show_message(
                    page,
                    f"Đã tự động tạo thành công {created_count} biên bản nghiệm thu!",
                )
            else:
                show_message(
                    page,
                    "Tất cả các biên bản nghiệm thu theo tiến độ đã tồn tại từ trước.",
                    ft.Colors.BLUE_700,
                )

        def confirm_delete_all():
            def perform_delete(e):
                db_conn.execute("DELETE FROM project_progress WHERE project_id = ?", (Session.project["id"],))
                db_conn.commit()
                close_dialog(dlg_confirm)
                page.run_task(load_progress)
                show_message(page, "Đã xóa toàn bộ công việc!")

            def close_dialog(dlg):
                if hasattr(page, "close"): page.close(dlg)
                else: dlg.open = False; page.update()

            dlg_confirm = ft.AlertDialog(
                title=ft.Text("Cảnh báo", weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                content=ft.Text("Bạn có chắc chắn muốn XÓA TOÀN BỘ công việc? Hành động này không thể hoàn tác!"),
                actions=[
                    ft.TextButton("Hủy", on_click=lambda e: close_dialog(dlg_confirm)),
                    ft.FilledButton("Xóa tất cả", style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700), on_click=perform_delete)
                ]
            )
            if hasattr(page, "open"): page.open(dlg_confirm)
            else: page.overlay.append(dlg_confirm); dlg_confirm.open = True; page.update()

        def close_menu_sheet(e=None):
            if hasattr(page, "close"): page.close(menu_sheet)
            else: menu_sheet.open = False; page.update()

        def open_menu_sheet(e):
            if hasattr(page, "open"): page.open(menu_sheet)
            else: menu_sheet.open = True; page.update()

        menu_sheet = ft.BottomSheet(
            content=ft.Container(
                padding=10,
                bgcolor=ft.Colors.WHITE,
                content=ft.Column(
                    tight=True,
                    controls=[
                        ft.ListTile(
                            title=ft.Text("Import công việc bằng file Excel", size=15),
                            on_click=lambda e: (
                                close_menu_sheet(),
                                page.run_task(pick_excel_click),
                            ),
                        ),
                        ft.Divider(height=1),
                        ft.ListTile(title=ft.Text("Tạo nhật ký theo tiến độ", size=15), on_click=lambda e: [close_menu_sheet(), auto_create_logs()]),
                        ft.Divider(height=1),
                        ft.ListTile(title=ft.Text("Tạo biên bản nghiệm thu theo tiến độ", size=15), on_click=lambda e: [close_menu_sheet(), auto_create_acceptances()]),
                        ft.Divider(height=1),
                        ft.ListTile(title=ft.Text("Xóa toàn bộ công việc", color=ft.Colors.RED_700, size=15), on_click=lambda e: [close_menu_sheet(), confirm_delete_all()]),
                        ft.Container(height=20)
                    ]
                )
            )
        )
        if not hasattr(page, "open"):
            page.overlay.append(menu_sheet)

        progress_actions = [
            ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                          icon_color=ft.Colors.WHITE, on_click=show_dashboard)
        ]
        
        set_appbar("Tiến độ công việc", progress_actions)
        
        page.floating_action_button = (
            ft.FloatingActionButton(icon=ft.Icons.MENU, tooltip="Menu chức năng",
                                    on_click=open_menu_sheet)
            if Session.role in ["kysu", "qs"] else None
        )

        page.add(ft.Container(
            content=ft.Column([
                ft.Row([
                    search_field,
                    ft.IconButton(icon=ft.Icons.ADD, icon_color=ft.Colors.GREEN_700,
                                  on_click=lambda ev: open_progress_dialog(), tooltip="Thêm thủ công")
                ], spacing=4),
                progress_list
            ], expand=True, spacing=10),
            padding=12, expand=True
        ))
        page.run_task(load_progress)
        page.update()

    # ==========================================
    # 5. THƯ VIỆN CÔNG VIỆC
    # ==========================================
    def show_work_library(e=None):
        standards_list = ft.ListView(expand=True, spacing=8, padding=4)
        suggested_task_names = [
            "Gia công cốt thép cột",
            "Lắp dựng cốt thép cột",
            "Gia công cốt thép dầm, sàn",
            "Lắp dựng cốt thép dầm, sàn",
            "Gia công cốt thép vách, lõi thang máy",
            "Lắp dựng cốt thép vách, lõi thang máy",
            "Lắp dựng cốt thép cầu thang",
            "Lắp dựng ván khuôn cột",
            "Tháo dỡ ván khuôn cột",
            "Lắp dựng ván khuôn dầm, sàn",
            "Tháo dỡ ván khuôn dầm, sàn",
            "Lắp dựng ván khuôn vách, lõi",
            "Lắp dựng ván khuôn cầu thang",
            "Đổ bê tông cột",
            "Đổ bê tông dầm, sàn",
            "Đổ bê tông cầu thang",
            "Đổ bê tông vách, lõi thang máy",
            "Bảo dưỡng ẩm bê tông kết cấu",
            "Xây tường gạch block/gạch đất nung",
            "Trát tường trong/ngoài nhà",
            "Láng nền tạo dốc, chống thấm sàn mái",
            "Nghiệm thu cốt thép cột, dầm, sàn",
            "Nghiệm thu ván khuôn cột, dầm, sàn",
            "Nghiệm thu trước khi đổ bê tông",
            "Nghiệm thu sau khi đổ bê tông (tháo cốp pha)",
            "Vệ sinh, thu dọn và bàn giao khu vực",
        ]
        suggested_machines = [
            "Xe bơm bê tông tĩnh",
            "Xe bơm bê tông cần",
            "Xe trộn bê tông (Transit Mixer)",
            "Máy đầm dùi bê tông (chạy điện/xăng)",
            "Máy đầm thước bê tông",
            "Máy xoa nền bê tông",
            "Máy cắt thép xây dựng",
            "Máy uốn thép xây dựng",
            "Máy hàn điện hồ quang / Máy hàn đối đầu",
            "Máy cắt plasma",
            "Cần cẩu tháp (Tower Crane)",
            "Cần cẩu bánh lốp / bánh xích",
            "Vận thăng nâng hàng",
            "Ô tô tải ben",
            "Máy đào bánh xích",
            "Máy xúc lật",
            "Máy ép cọc thủy lực",
            "Máy khoan nhồi",
            "Máy phát điện dự phòng",
            "Xe bơm bê tông",
            "Máy đầm dùi",
            "Cần cẩu tháp",
            "Máy cắt/uốn thép",
        ]
        suggested_tcvn = [
            "TCVN 4453:1995 Kết cấu bê tông và bê tông cốt thép toàn khối. Quy phạm thi công và nghiệm thu",
            "TCVN 9346:2012 Kết cấu bê tông và bê tông cốt thép - Bảo dưỡng ẩm nhân tạo",
            "TCVN 9342:2012 Công trình bê tông cốt thép - Đánh giá độ bền bằng phương pháp không phá hủy",
            "TCVN 9343:2012 Kết cấu bê tông và bê tông cốt thép - Hướng dẫn đánh giá cường độ bê tông",
            "TCVN 1651-1:2018 Thép cốt bê tông - Phần 1: Thép thanh tròn trơn",
            "TCVN 1651-2:2018 Thép cốt bê tông - Phần 2: Thép thanh vằn",
            "TCVN 6288:1997 Dây thép vuốt nguội dùng để cốt bê tông và sản xuất lưới thép hàn",
            "TCVN 4453:1995 (Mục Ván khuôn và đà giáo trong thi công bê tông)",
            "TCVN 8798:2011 Giàn giáo - Yêu cầu về kỹ thuật thi công và nghiệm thu",
            "TCVN 5308:1991 Quy phạm kỹ thuật an toàn trong xây dựng",
            "TCVN 4447:2012 Công tác đất. Thi công và nghiệm thu",
            "TCVN 4085:2011 Công tác trắc địa trong xây dựng",
            "TCVN 4314:2003 Vữa xây dựng - Yêu cầu kỹ thuật",
            "TCVN 7957:2008 Thoát nước - Mạng lưới bên ngoài công trình - Tiêu chuẩn thiết kế",
        ]
        suggested_acceptance = [
            "Cốt thép: Kiểm tra đường kính, số lượng, khoảng cách đai, chiều dài neo và lớp bê tông bảo vệ.",
            "Ván khuôn: Kiểm tra độ phẳng, kích thước, độ cứng vững và độ kín khít.",
            "Cột: Kiểm tra độ thẳng đứng, gông chống phình và vệ sinh chân cột.",
            "Dầm, sàn: Kiểm tra cao độ, độ vồng, thép mũ sàn và hệ chống đỡ.",
            "Trước khi đổ bê tông: Kiểm tra thép, cốp pha, MEP âm và vệ sinh khu vực.",
            "Sau khi đổ bê tông: Kiểm tra bề mặt, kích thước và kết quả thí nghiệm mẫu.",
            "Vệ sinh, bàn giao: Thu gom phế thải, vệ sinh khu vực, bảo đảm an toàn và bàn giao hồ sơ.",
        ]
        search_field = ft.TextField(
            hint_text="Tìm nhanh tên công việc, nhóm hoặc đơn vị...",
            prefix_icon=ft.Icons.SEARCH,
            border=ft.OutlineInputBorder(border_radius=8),
            bgcolor=ft.Colors.WHITE,
            on_change=lambda ev: page.run_task(load_standards, ev.control.value),
        )

        def close_dialog(dialog):
            if hasattr(page, "close"): page.close(dialog)
            else: dialog.open = False; page.update()

        def make_choice_group(label, hint, initial_value, suggestions, line_separated=False):
            rows = ft.Column(spacing=6)
            value_fields = []
            if line_separated:
                initial_items = [item.strip() for item in (initial_value or "").splitlines() if item.strip()]
            else:
                initial_items = [item.strip() for item in (initial_value or "").replace("\n", ",").split(",") if item.strip()]

            def add_choice_row(value=""):
                choice = ft.TextField(hint_text=hint, value=value, expand=True,
                                      multiline=line_separated, min_lines=2 if line_separated else 1,
                                      max_lines=4 if line_separated else 1)

                def select_option(option):
                    choice.value = option
                    choice.update()

                menu = ft.PopupMenuButton(
                    icon=ft.Icons.ARROW_DROP_DOWN_CIRCLE_OUTLINED,
                    tooltip=f"Gợi ý {label.lower()}",
                    items=[ft.PopupMenuItem(content=ft.Text(option),
                                            on_click=lambda e, item=option: select_option(item))
                           for option in suggestions],
                )
                row = ft.Row(spacing=4)
                value_fields.append(choice)

                def remove_row(e):
                    if len(value_fields) > 1:
                        value_fields.remove(choice)
                        rows.controls.remove(row)
                        page.update()

                row.controls = [choice, menu, ft.IconButton(
                    icon=ft.Icons.DELETE_OUTLINE, tooltip="Xóa dòng", icon_color=ft.Colors.RED_400,
                    on_click=remove_row)]
                rows.controls.append(row)

            for item in initial_items or [""]:
                add_choice_row(item)

            def add_row(e):
                add_choice_row()
                page.update()

            section = ft.Column([
                ft.Row([ft.Text(label, weight=ft.FontWeight.BOLD),
                        ft.IconButton(icon=ft.Icons.ADD, tooltip=f"Thêm {label.lower()}",
                                      on_click=add_row)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                rows,
            ], spacing=4)
            separator = "\n" if line_separated else ", "
            get_value = lambda: separator.join(field.value.strip() for field in value_fields if field.value and field.value.strip())
            return section, get_value

        def save_standard(dialog, fields_data, standard_id=None, machine_value=None, tcvn_value=None, acceptance_value=None):
            name, group, unit, value, description = fields_data
            if not name.value.strip():
                show_message(page, "Vui lòng nhập tên công việc.", ft.Colors.RED_700)
                return
            duplicate = db_conn.execute(
                "SELECT id FROM work_standards WHERE LOWER(TRIM(ten_cong_viec)) = LOWER(TRIM(?)) "
                "AND id != COALESCE(?, -1) LIMIT 1",
                (name.value.strip(), standard_id),
            ).fetchone()
            if duplicate:
                show_message(page, "Tên công việc đã có trong thư viện. Hãy mở mục hiện có để chỉnh sửa.", ft.Colors.ORANGE_800)
                return
            values = (name.value.strip(), group.value.strip(), unit.value.strip(), value.value.strip(),
                      description.value.strip(), machine_value(), tcvn_value(), acceptance_value())
            if standard_id:
                db_conn.execute("UPDATE work_standards SET ten_cong_viec=?, nhom_cong_viec=?, don_vi=?, dinh_muc=?, mo_ta=?, may_thi_cong=?, tcvn=?, tieu_chi_nghiem_thu=? WHERE id=?",
                                values + (standard_id,))
            else:
                db_conn.execute("INSERT INTO work_standards (ten_cong_viec, nhom_cong_viec, don_vi, dinh_muc, mo_ta, may_thi_cong, tcvn, tieu_chi_nghiem_thu, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                values + (Session.user["username"],))
            db_conn.commit()
            close_dialog(dialog)
            page.run_task(load_standards, search_field.value or "")
            show_message(page, "Đã lưu định mức công việc.")

        def open_standard_dialog(standard_id=None):
            old = db_conn.execute(
                "SELECT ten_cong_viec, nhom_cong_viec, don_vi, dinh_muc, mo_ta, may_thi_cong, tcvn, tieu_chi_nghiem_thu FROM work_standards WHERE id=?",
                (standard_id,),
            ).fetchone() if standard_id else None

            # 1. Tên công việc
            task_name = ft.TextField(value=old[0] if old else "", autofocus=True, expand=True)
            task_name_header = ft.Row([ft.Text("Tên công việc (*)", weight=ft.FontWeight.BOLD)])

            # 2. Xử lý "Nhóm công việc" (Lấy danh sách nhóm sẵn có)
            existing_groups = db_conn.execute("SELECT DISTINCT nhom_cong_viec FROM work_standards WHERE nhom_cong_viec IS NOT NULL AND TRIM(nhom_cong_viec) != '' ORDER BY nhom_cong_viec").fetchall()
            group_options = [row[0] for row in existing_groups]
            if not group_options: # Dữ liệu mẫu nếu DB trống
                group_options = ["Phần móng", "Phần thân", "Phần hoàn thiện", "Cơ điện (MEP)", "Khác"]

            txt_group = ft.TextField(label="Nhóm công việc", value=old[1] if old else "", expand=True)
            
            def select_group(e, g_name):
                txt_group.value = g_name
                txt_group.update()

            group_menu = ft.PopupMenuButton(
                icon=ft.Icons.ARROW_DROP_DOWN_CIRCLE_OUTLINED,
                tooltip="Chọn nhóm công việc có sẵn",
                items=[ft.PopupMenuItem(content=ft.Text(g), on_click=lambda e, name=g: select_group(e, name)) for g in group_options]
            )
            group_row = ft.Row([txt_group, group_menu], alignment=ft.MainAxisAlignment.START, spacing=4)

            # 3. Các thành phần nhập liệu khác
            txt_unit = ft.TextField(label="Đơn vị tính (m2, m3...)", value=old[2] if old else "")
            txt_value = ft.TextField(label="Định mức / Yêu cầu kỹ thuật", value=old[3] if old else "")
            txt_desc = ft.TextField(label="Mô tả chi tiết", value=old[4] if old else "", multiline=True, min_lines=2)

            machine_section, machine_value = make_choice_group(
                "Máy thi công", "Nhập hoặc chọn máy thi công...", old[5] if old else "", suggested_machines)
            tcvn_section, tcvn_value = make_choice_group(
                "Tiêu chuẩn TCVN", "Nhập hoặc chọn TCVN...", old[6] if old else "", suggested_tcvn)
            acceptance_section, acceptance_value = make_choice_group(
                "Tiêu chí nghiệm thu", "Nhập hoặc chọn tiêu chí nghiệm thu...",
                old[7] if old else "", suggested_acceptance, line_separated=True)
            
            # Đóng gói dữ liệu để truyền vào hàm lưu
            fields_data = [task_name, txt_group, txt_unit, txt_value, txt_desc]
            
            # Đóng gói layout hiển thị
            layout_controls = [
                task_name_header,
                task_name,
                group_row, # Sử dụng group_row thay vì chỉ TextField
                txt_unit,
                txt_value,
                txt_desc,
                machine_section,
                tcvn_section,
                acceptance_section,
            ]

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text("Sửa định mức" if standard_id else "Thêm định mức mới", weight=ft.FontWeight.BOLD),
                content=ft.Column(layout_controls, tight=True, scroll=ft.ScrollMode.AUTO),
                actions=[
                    ft.TextButton(content=ft.Text("Hủy bỏ"), on_click=lambda ev: close_dialog(dialog)),
                    ft.FilledButton(content=ft.Text("Lưu thông tin"), on_click=lambda ev: save_standard(
                        dialog, fields_data, standard_id, machine_value, tcvn_value, acceptance_value)),
                ],
            )
            if hasattr(page, "open"): page.open(dialog)
            else: page.overlay.append(dialog); dialog.open = True; page.update()

        def confirm_delete(standard_id):
            def perform_delete(ev):
                db_conn.execute("DELETE FROM work_standards WHERE id=?", (standard_id,))
                db_conn.commit()
                close_dialog(dlg)
                page.run_task(load_standards, search_field.value or "")
                show_message(page, "Đã xóa định mức khỏi thư viện.")

            dlg = ft.AlertDialog(
                title=ft.Text("Xác nhận xóa", weight=ft.FontWeight.BOLD),
                content=ft.Text("Bạn có chắc chắn muốn xóa định mức này khỏi thư viện không?"),
                actions=[
                    ft.TextButton(content=ft.Text("Hủy"), on_click=lambda e: close_dialog(dlg)),
                    ft.FilledButton(content=ft.Text("Xóa"), on_click=perform_delete, style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700))
                ]
            )
            if hasattr(page, "open"): page.open(dlg)
            else: page.overlay.append(dlg); dlg.open = True; page.update()

        async def load_standards(keyword=""):
            standards_list.controls.clear()
            keyword = (keyword or "").strip()
            
            rows = await asyncio.to_thread(
                lambda: db_conn.execute(
                    "SELECT id, ten_cong_viec, nhom_cong_viec, don_vi, dinh_muc, "
                    "mo_ta, may_thi_cong, tcvn, tieu_chi_nghiem_thu "
                    "FROM work_standards WHERE ten_cong_viec LIKE ? "
                    "OR nhom_cong_viec LIKE ? OR don_vi LIKE ? "
                    "OR may_thi_cong LIKE ? OR tcvn LIKE ? "
                    "OR tieu_chi_nghiem_thu LIKE ? "
                    "ORDER BY nhom_cong_viec, ten_cong_viec",
                    (
                        f"%{keyword}%",
                        f"%{keyword}%",
                        f"%{keyword}%",
                        f"%{keyword}%",
                        f"%{keyword}%",
                        f"%{keyword}%",
                    ),
                ).fetchall()
            )

            if not rows:
                standards_list.controls.append(ft.Container(content=ft.Text("Không tìm thấy định mức phù hợp.", color=ft.Colors.GREY_700), padding=10))
                page.update()
                return

            current_group = None
            for standard_id, name, group, unit, value, description, equipment, tcvn, acceptance in rows:
                display_group = group.strip() if group else "Danh mục khác"
                if display_group != current_group:
                    standards_list.controls.append(
                        ft.Container(
                            content=ft.Text(f"📁 {display_group.upper()}", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_900),
                            bgcolor=ft.Colors.BLUE_50, padding=10, border_radius=5,
                            margin=10
                        )
                    )
                    current_group = display_group

                actions = ft.Row(spacing=0)
                if Session.role in ["kysu", "qs"]:
                    actions.controls.extend([
                        ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Sửa", icon_color=ft.Colors.BLUE_700,
                                      on_click=lambda ev, item_id=standard_id: open_standard_dialog(item_id)),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Xóa", icon_color=ft.Colors.RED_400,
                                      on_click=lambda ev, item_id=standard_id: confirm_delete(item_id)),
                    ])

                standards_list.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Container(content=ft.Icon(ft.Icons.CONSTRUCTION, color=ft.Colors.BLUE_700), width=38),
                            ft.Column([
                                ft.Text(name, weight=ft.FontWeight.BOLD),
                                ft.Text(f"Đơn vị: {unit or '-'} | {value or 'Theo thiết kế'}", size=12, color=ft.Colors.BLUE_700),
                                ft.Text(f"Máy thi công: {equipment or '-'} | TCVN: {tcvn or '-'}", size=12, color=ft.Colors.GREY_700),
                                ft.Text(f"Tiêu chí nghiệm thu: {acceptance or '-'}", size=12, color=ft.Colors.GREY_700),
                                ft.Text(description or "Chưa có mô tả chi tiết", size=12, color=ft.Colors.GREY_700, italic=True)
                            ], expand=True, spacing=3),
                            actions,
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.WHITE, border_radius=8, padding=10,
                    )
                )
            page.update()

        page.clean()
        set_appbar("Thư viện công việc", [ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                                                       icon_color=ft.Colors.WHITE, on_click=show_dashboard)])
        page.floating_action_button = ft.FloatingActionButton(icon=ft.Icons.ADD, tooltip="Thêm định mức",
                                                              on_click=lambda ev: open_standard_dialog()) if Session.role in ["kysu", "qs"] else None
        page.add(ft.Container(content=ft.Column([search_field, standards_list], expand=True, spacing=10),
                              padding=12, expand=True))
        page.run_task(load_standards)
        page.update()
    # ==========================================
    # 6. BIÊN BẢN NGHIỆM THU
    # ==========================================
    def show_acceptance(e=None):
        if not Session.project:
            show_message(page, "Hãy tạo hoặc chọn công trình trước.", ft.Colors.RED_700)
            return

        records_list = ft.ListView(expand=True, spacing=8, padding=4)
        search_field = ft.TextField(
            hint_text="Tìm phiếu theo tiêu đề hoặc hạng mục...",
            prefix_icon=ft.Icons.SEARCH,
            border=ft.OutlineInputBorder(border_radius=8),
            bgcolor=ft.Colors.WHITE,
            expand=True,
            on_change=lambda ev: page.run_task(load_records, ev.control.value)
        )

        def close_dialog(dialog):
            if hasattr(page, "close"): page.close(dialog)
            else: dialog.open = False; page.update()

        def open_settings(ev=None):
            if Session.role not in ["kysu", "qs"]:
                show_message(page, "Chỉ được xem cài đặt biên bản.", ft.Colors.RED_700)
                return
            settings = db_conn.execute(
                "SELECT mau_bien_ban, tieu_de, don_vi_nghiem_thu, nguoi_ky_1, nguoi_ky_2 FROM acceptance_settings WHERE project_id=?",
                (Session.project["id"],)).fetchone()
            template = ft.Dropdown(label="Mẫu biên bản", value=settings[0] if settings else "Mẫu 1 - 2 trang",
                                   options=[ft.dropdown.Option("Mẫu 1 - 2 trang"), ft.dropdown.Option("Mẫu 2 - 1 trang")])
            title = ft.TextField(label="Tiêu đề biên bản", value=settings[1] if settings else "BIÊN BẢN NGHIỆM THU CÔNG VIỆC")
            unit = ft.TextField(label="Đơn vị nghiệm thu", value=settings[2] if settings else "")
            signer1 = ft.TextField(label="Người ký 1", value=settings[3] if settings else "")
            signer2 = ft.TextField(label="Người ký 2", value=settings[4] if settings else "")
            fields = [template, title, unit, signer1, signer2]

            dialog = ft.AlertDialog(modal=True, title=ft.Text("Cài đặt biên bản", weight=ft.FontWeight.BOLD),
                                    content=ft.Column(fields, tight=True),
                                    actions=[ft.TextButton(content=ft.Text("Hủy"), on_click=lambda x: close_dialog(dialog)),
                                             ft.FilledButton(content=ft.Text("Lưu cài đặt"), on_click=lambda x: save_settings(dialog, fields))])
            if hasattr(page, "open"): page.open(dialog)
            else: page.overlay.append(dialog); dialog.open = True; page.update()

        def save_settings(dialog, fields):
            values = tuple(field.value.strip() if index else field.value for index, field in enumerate(fields))
            db_conn.execute(
                "INSERT INTO acceptance_settings (project_id, mau_bien_ban, tieu_de, don_vi_nghiem_thu, nguoi_ky_1, nguoi_ky_2) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET mau_bien_ban=excluded.mau_bien_ban, tieu_de=excluded.tieu_de, don_vi_nghiem_thu=excluded.don_vi_nghiem_thu, nguoi_ky_1=excluded.nguoi_ky_1, nguoi_ky_2=excluded.nguoi_ky_2",
                (Session.project["id"],) + values)
            db_conn.commit()
            close_dialog(dialog)
            show_message(page, "Đã lưu cài đặt biên bản.")

        def open_record_dialog(record_id=None):
            old = db_conn.execute(
                "SELECT loai_phieu, tieu_de, hang_muc, vi_tri, ngay_yeu_cau, ngay_nghiem_thu, trang_thai, thanh_phan_tham_du, noi_dung, ghi_chu FROM acceptance_records WHERE id=? AND project_id=?",
                (record_id, Session.project["id"])).fetchone() if record_id else None
            kind = ft.Dropdown(label="Loại phiếu", value=old[0] if old else "Phiếu yêu cầu nghiệm thu",
                               options=[ft.dropdown.Option("Phiếu yêu cầu nghiệm thu"),
                                        ft.dropdown.Option("Biên bản nghiệm thu công việc"),
                                        ft.dropdown.Option("Biên bản nghiệm thu thiết bị")])
            title = ft.TextField(label="Tiêu đề", value=old[1] if old else "")
            item = ft.TextField(label="Hạng mục / thiết bị", value=old[2] if old else "")
            location = ft.TextField(label="Vị trí nghiệm thu", value=old[3] if old else "")
            requested = ft.TextField(label="Ngày yêu cầu", value=old[4] if old else datetime.now().strftime("%d/%m/%Y"))
            accepted = ft.TextField(label="Ngày nghiệm thu", value=old[5] if old else "")
            status = ft.Dropdown(label="Trạng thái", value=old[6] if old else "Chờ nghiệm thu",
                                 options=[ft.dropdown.Option("Chờ nghiệm thu"),
                                          ft.dropdown.Option("Đã nghiệm thu"),
                                          ft.dropdown.Option("Không đạt")])
            participants = ft.TextField(label="Thành phần tham dự", value=old[7] if old else "", multiline=True, min_lines=2)
            content = ft.TextField(label="Nội dung / kết luận", value=old[8] if old else "", multiline=True, min_lines=3)
            notes = ft.TextField(label="Ghi chú", value=old[9] if old else "", multiline=True, min_lines=2)
            fields = [kind, title, item, location, requested, accepted, status, participants, content, notes]

            dialog = ft.AlertDialog(modal=True, title=ft.Text("Sửa phiếu" if record_id else "Thêm phiếu nghiệm thu", weight=ft.FontWeight.BOLD),
                                    content=ft.Column(fields, tight=True, scroll=ft.ScrollMode.AUTO),
                                    actions=[ft.TextButton(content=ft.Text("Hủy"), on_click=lambda x: close_dialog(dialog)),
                                             ft.FilledButton(content=ft.Text("Lưu"), on_click=lambda x: save_record(dialog, fields, record_id))])
            if hasattr(page, "open"): page.open(dialog)
            else: page.overlay.append(dialog); dialog.open = True; page.update()

        def save_record(dialog, fields, record_id=None):
            if not fields[1].value.strip():
                show_message(page, "Vui lòng nhập tiêu đề phiếu.", ft.Colors.RED_700)
                return
            values = tuple(field.value.strip() for field in fields)
            if record_id:
                db_conn.execute(
                    "UPDATE acceptance_records SET loai_phieu=?, tieu_de=?, hang_muc=?, vi_tri=?, ngay_yeu_cau=?, ngay_nghiem_thu=?, trang_thai=?, thanh_phan_tham_du=?, noi_dung=?, ghi_chu=? WHERE id=? AND project_id=?",
                    values + (record_id, Session.project["id"]))
            else:
                db_conn.execute(
                    "INSERT INTO acceptance_records (project_id, loai_phieu, tieu_de, hang_muc, vi_tri, ngay_yeu_cau, ngay_nghiem_thu, trang_thai, thanh_phan_tham_du, noi_dung, ghi_chu, created_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (Session.project["id"],) + values + (Session.user["username"],))
            db_conn.commit()
            close_dialog(dialog)
            page.run_task(load_records, search_field.value or "")
            show_message(page, "Đã lưu phiếu nghiệm thu.")

        def delete_record(record_id):
            db_conn.execute("DELETE FROM acceptance_records WHERE id=? AND project_id=?", (record_id, Session.project["id"]))
            db_conn.commit()
            page.run_task(load_records, search_field.value or "")
            show_message(page, "Đã xóa phiếu nghiệm thu.")

        def confirm_delete_all_records():
            def perform_delete(e):
                db_conn.execute("DELETE FROM acceptance_records WHERE project_id = ?", (Session.project["id"],))
                db_conn.commit()
                close_dialog(dlg_confirm)
                page.run_task(load_records)
                show_message(page, "Đã xóa toàn bộ biên bản nghiệm thu!")

            dlg_confirm = ft.AlertDialog(
                title=ft.Text("Cảnh báo", weight=ft.FontWeight.BOLD, color=ft.Colors.RED_700),
                content=ft.Text("Bạn có chắc chắn muốn XÓA TOÀN BỘ biên bản nghiệm thu của dự án này? Hành động này không thể hoàn tác!"),
                actions=[
                    ft.TextButton("Hủy", on_click=lambda e: close_dialog(dlg_confirm)),
                    ft.FilledButton("Xóa tất cả", style=ft.ButtonStyle(bgcolor=ft.Colors.RED_700), on_click=perform_delete)
                ]
            )
            if hasattr(page, "open"): page.open(dlg_confirm)
            else: page.overlay.append(dlg_confirm); dlg_confirm.open = True; page.update()

        def close_menu_sheet(e=None):
            if hasattr(page, "close"): page.close(menu_sheet)
            else: menu_sheet.open = False; page.update()

        def open_menu_sheet(e):
            if hasattr(page, "open"): page.open(menu_sheet)
            else: menu_sheet.open = True; page.update()

        menu_sheet = ft.BottomSheet(
            content=ft.Container(
                padding=10,
                bgcolor=ft.Colors.WHITE,
                content=ft.Column(
                    tight=True,
                    controls=[
                        ft.ListTile(title=ft.Text("Cài đặt biên bản", size=15), leading=ft.Icon(ft.Icons.SETTINGS), on_click=lambda e: [close_menu_sheet(), open_settings(e)]),
                        ft.Divider(height=1),
                        ft.ListTile(title=ft.Text("Xóa toàn bộ biên bản nghiệm thu", color=ft.Colors.RED_700, size=15), leading=ft.Icon(ft.Icons.DELETE_OUTLINE, color=ft.Colors.RED_700), on_click=lambda e: [close_menu_sheet(), confirm_delete_all_records()]),
                        ft.Container(height=20)
                    ]
                )
            )
        )
        if not hasattr(page, "open"):
            page.overlay.append(menu_sheet)

        async def load_records(keyword=""):
            records_list.controls.clear()
            keyword = (keyword or "").strip()
            rows = await asyncio.to_thread(
                lambda: db_conn.execute(
                    "SELECT id, loai_phieu, tieu_de, hang_muc, vi_tri, "
                    "ngay_yeu_cau, ngay_nghiem_thu, trang_thai "
                    "FROM acceptance_records WHERE project_id=? "
                    "AND (tieu_de LIKE ? OR hang_muc LIKE ?) ORDER BY id DESC",
                    (
                        Session.project["id"],
                        f"%{keyword}%",
                        f"%{keyword}%",
                    ),
                ).fetchall()
            )
            if not rows:
                records_list.controls.append(ft.Container(content=ft.Text("Chưa có phiếu nghiệm thu nào.", color=ft.Colors.GREY_700), padding=10))

            for record_id, kind, title, item, location, requested, accepted, status in rows:
                actions = ft.Row(spacing=0)
                if Session.role in ["kysu", "qs"]:
                    actions.controls.extend([
                        ft.IconButton(icon=ft.Icons.EDIT_OUTLINED, tooltip="Sửa", icon_color=ft.Colors.BLUE_700,
                                      on_click=lambda ev, item_id=record_id: open_record_dialog(item_id)),
                        ft.IconButton(icon=ft.Icons.DELETE_OUTLINE, tooltip="Xóa", icon_color=ft.Colors.RED_400,
                                      on_click=lambda ev, item_id=record_id: delete_record(item_id))
                    ])

                status_color = ft.Colors.GREY_700
                status_weight = ft.FontWeight.NORMAL
                if status == "Đã nghiệm thu":
                    status_color = ft.Colors.GREEN_700
                    status_weight = ft.FontWeight.BOLD
                elif status in ["Không đạt", "Chưa đạt"]:
                    status_color = ft.Colors.RED_600
                    status_weight = ft.FontWeight.BOLD
                elif status == "Chờ nghiệm thu":
                    status_color = ft.Colors.ORANGE_800
                    status_weight = ft.FontWeight.BOLD

                records_list.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Icon(ft.Icons.ASSIGNMENT_TURNED_IN, color=ft.Colors.BLUE_700),
                            ft.Column([
                                ft.Text(title, weight=ft.FontWeight.BOLD),
                                ft.Text(f"{kind} | {item or 'Chưa có hạng mục'}", size=12),
                                ft.Text(f"Vị trí: {location or '-'} | Yêu cầu: {requested or '-'} | Nghiệm thu: {accepted or '-'}",
                                        size=12, color=ft.Colors.GREY_700),
                                ft.Text(f"Trạng thái: {status}", size=13, color=status_color, weight=status_weight)
                            ], expand=True, spacing=3),
                            actions
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=ft.Colors.WHITE, border_radius=8, padding=10
                    )
                )
            page.update()

        page.clean()
        acceptance_actions = [ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                                            icon_color=ft.Colors.WHITE, on_click=show_dashboard)]
        
        set_appbar("Biên bản nghiệm thu", acceptance_actions)
        
        page.floating_action_button = (
            ft.FloatingActionButton(icon=ft.Icons.MENU, tooltip="Menu chức năng",
                                    on_click=open_menu_sheet)
            if Session.role in ["kysu", "qs"] else None
        )

        page.add(ft.Container(
            content=ft.Column([
                ft.Row([
                    search_field,
                    ft.IconButton(icon=ft.Icons.ADD, icon_color=ft.Colors.GREEN_700,
                                  on_click=lambda ev: open_record_dialog(), tooltip="Thêm phiếu")
                ], spacing=4),
                records_list
            ], expand=True, spacing=10),
            padding=12, expand=True
        ))
        page.run_task(load_records)
        page.update()

    # ==========================================
    # 7. HỘP THƯ EMAIL VÀ TÀI KHOẢN
    # ==========================================
    def show_inbox(e=None):
        page.clean()
        set_appbar("Hộp thư email", [ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                                                   icon_color=ft.Colors.WHITE, on_click=show_dashboard)])
        inbox_view = ft.Column(
            controls=[ft.ProgressRing()],
            scroll=ft.ScrollMode.AUTO,
            spacing=10,
            expand=True,
        )
        page.add(
            ft.Container(content=inbox_view, padding=16, expand=True)
        )
        page.run_task(load_inbox, Session.user["email"], inbox_view)

    async def load_inbox(recipient_email, inbox_view):
        messages = await asyncio.to_thread(
            lambda: db_conn.execute(
                "SELECT e.id, e.subject, e.body, e.sender, e.created_at, "
                "e.is_read, e.invitation_id, i.project_id, i.role, i.status "
                "FROM email_inbox e LEFT JOIN project_invitations i "
                "ON i.id = e.invitation_id AND i.invitee_email = e.recipient "
                "WHERE e.recipient = ? ORDER BY e.id DESC",
                (recipient_email,),
            ).fetchall()
        )
        inbox_controls = [ft.Text("HỘP THƯ EMAIL MÔ PHỎNG", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800)]

        for message in messages:
            (
                message_id,
                subject,
                body,
                sender,
                created_at,
                is_read,
                invitation_id,
                invitation_project_id,
                invitation_role,
                invitation_status,
            ) = message
            created_at_text = (
                created_at.isoformat(sep=" ", timespec="seconds")
                if isinstance(created_at, datetime)
                else str(created_at or "")
            )
            actions = []

            def accept_invitation(event, inv_id=invitation_id):
                page.run_task(accept_invitation_async, inv_id)

            async def accept_invitation_async(inv_id):
                user_id = Session.user["id"]

                def accept():
                    def accept_transaction(connection):
                        inv_row = connection.execute(
                            "SELECT project_id, role, status FROM project_invitations "
                            "WHERE id = ? AND invitee_email = ?",
                            (inv_id, recipient_email),
                        ).fetchone()
                        if not inv_row or inv_row[2] != "pending":
                            return False
                        connection.execute(
                            "INSERT INTO project_members (project_id, user_id, role) "
                            "VALUES (?, ?, ?) ON CONFLICT (project_id, user_id) "
                            "DO UPDATE SET role = EXCLUDED.role",
                            (inv_row[0], user_id, inv_row[1]),
                        )
                        connection.execute(
                            "UPDATE project_invitations SET status='accepted' WHERE id=?",
                            (inv_id,),
                        )
                        connection.execute(
                            "UPDATE email_inbox SET is_read=1 WHERE invitation_id=?",
                            (inv_id,),
                        )
                        return True

                    return db_conn.run_transaction(accept_transaction)

                try:
                    accepted = await asyncio.to_thread(accept)
                    if not accepted:
                        show_message(page, "Lời mời không còn hiệu lực.", ft.Colors.RED_700)
                        return
                    show_message(page, "Đã chấp nhận lời mời tham gia dự án.")
                    page.run_task(load_inbox, recipient_email, inbox_view)
                except Exception:
                    logging.exception("Không thể chấp nhận lời mời tham gia dự án.")
                    show_message(
                        page,
                        "Không thể chấp nhận lời mời. Vui lòng thử lại.",
                        ft.Colors.RED_700,
                    )

            if invitation_id and invitation_status == "pending":
                actions.append(ft.FilledButton(content=ft.Text("Chấp nhận"), icon=ft.Icons.CHECK,
                                               on_click=accept_invitation))

            inbox_controls.append(ft.Container(
                content=ft.Column([ft.Text(subject, weight=ft.FontWeight.BOLD), ft.Text(body),
                                   ft.Text(f"Từ: {sender} | {created_at_text}", size=12, color=ft.Colors.GREY_700),
                                   ft.Row(actions)], spacing=4),
                bgcolor=ft.Colors.BLUE_50 if not is_read else ft.Colors.GREY_100, border_radius=8, padding=12))

        inbox_view.controls = inbox_controls
        await asyncio.to_thread(
            lambda: db_conn.execute(
                "UPDATE email_inbox SET is_read = 1 WHERE recipient = ?",
                (recipient_email,),
            )
        )
        page.update()

    def show_account_management(e=None):
        page.clean()
        set_appbar("Quản lý tài khoản", [ft.IconButton(icon=ft.Icons.HOME, tooltip="Trang chủ",
                                                       icon_color=ft.Colors.WHITE, on_click=show_dashboard)])

        old_password = ft.TextField(label="Mật khẩu cũ", password=True, can_reveal_password=True, prefix_icon=ft.Icons.LOCK)
        new_password = ft.TextField(label="Mật khẩu mới", password=True, can_reveal_password=True, prefix_icon=ft.Icons.LOCK_RESET)
        confirm_password = ft.TextField(label="Xác nhận mật khẩu", password=True, can_reveal_password=True, prefix_icon=ft.Icons.LOCK_RESET)

        def update_password(ev):
            if not old_password.value or not new_password.value or not confirm_password.value:
                show_message(page, "Vui lòng nhập đủ thông tin.", ft.Colors.RED_700)
                return
            if new_password.value != confirm_password.value:
                show_message(page, "Mật khẩu mới không khớp.", ft.Colors.RED_700)
                return
            stored = db_conn.execute("SELECT password FROM users WHERE id=?", (Session.user["id"],)).fetchone()
            if not stored or stored[0] != old_password.value:
                show_message(page, "Mật khẩu cũ không chính xác.", ft.Colors.RED_700)
                return
            db_conn.execute("UPDATE users SET password=? WHERE id=?", (new_password.value, Session.user["id"]))
            db_conn.commit()
            old_password.value = new_password.value = confirm_password.value = ""
            page.update()
            show_message(page, "Đã cập nhật mật khẩu.")

        account_card = ft.Container(content=ft.Column([
            ft.Text("THÔNG TIN CÁ NHÂN", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
            ft.ListTile(leading=ft.Icon(ft.Icons.PERSON, color=ft.Colors.BLUE_700), title=ft.Text("Tên tài khoản"),
                        subtitle=ft.Text(Session.user["username"])),
            ft.ListTile(leading=ft.Icon(ft.Icons.EMAIL, color=ft.Colors.BLUE_700), title=ft.Text("Email đăng ký"),
                        subtitle=ft.Text(Session.user["email"])),
            ft.ListTile(leading=ft.Icon(ft.Icons.BADGE, color=ft.Colors.BLUE_700), title=ft.Text("Vai trò hiện tại"),
                        subtitle=ft.Text(Session.role or "-"))
        ], spacing=2), bgcolor=ft.Colors.WHITE, border_radius=10, padding=12)

        password_card = ft.Container(content=ft.Column([
            ft.Text("ĐỔI MẬT KHẨU", size=16, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800),
            old_password, new_password, confirm_password,
            ft.FilledButton(content=ft.Text("Cập nhật mật khẩu"), icon=ft.Icons.SAVE, on_click=update_password)
        ], spacing=10), bgcolor=ft.Colors.WHITE, border_radius=10, padding=12)

        page.add(ft.Container(content=ft.Column([
            account_card, password_card,
            ft.OutlinedButton(content=ft.Text("Đăng xuất tài khoản"), icon=ft.Icons.LOGOUT,
                              on_click=lambda e: on_logout(), style=ft.ButtonStyle(color=ft.Colors.RED_700))
        ], scroll=ft.ScrollMode.AUTO, spacing=12), padding=12, expand=True))
        page.update()

    show_dashboard()