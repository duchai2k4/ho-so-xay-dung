import os
from pathlib import Path
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

def register_fonts():
    windows_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    android_fonts = Path("/system/fonts")
    font_sets = (
        (
            windows_fonts / "times.ttf",
            windows_fonts / "timesbd.ttf",
            windows_fonts / "timesi.ttf",
        ),
        (
            android_fonts / "Roboto-Regular.ttf",
            android_fonts / "Roboto-Bold.ttf",
            android_fonts / "Roboto-Italic.ttf",
        ),
    )

    for normal, bold, italic in font_sets:
        if all(path.is_file() for path in (normal, bold, italic)):
            pdfmetrics.registerFont(TTFont("Times", str(normal)))
            pdfmetrics.registerFont(TTFont("Times-Bold", str(bold)))
            pdfmetrics.registerFont(TTFont("Times-Italic", str(italic)))
            return True
    return False

def draw_dotted_lines(c, start_x, start_y, width, num_lines, line_height):
    c.setDash(1, 2)
    c.setLineWidth(0.5)
    for i in range(num_lines):
        y = start_y - (i * line_height)
        c.line(start_x, y, start_x + width, y)
    c.setDash(1, 0)

def draw_checkbox(c, x, y, checked=False, label=""):
    c.rect(x, y, 8, 8, stroke=1, fill=0)
    if checked:
        c.line(x, y, x+8, y+8)
        c.line(x, y+8, x+8, y)
    if label:
        c.drawString(x + 12, y, label)

def draw_multiline_text(c, text, x, y, max_width, font_name, font_size, line_height):
    if not text:
        return y
    words = str(text).split(' ')
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + word + " "
        width = pdfmetrics.stringWidth(test_line, font_name, font_size)
        if width <= max_width:
            current_line = test_line
        else:
            lines.append(current_line)
            current_line = word + " "
    lines.append(current_line)
    
    curr_y = y
    for line in lines:
        c.drawString(x, curr_y, line.strip())
        curr_y -= line_height
    return curr_y + line_height

def generate_nhat_ky_pdf(file_path, data, settings):
    has_fonts = register_fonts()
    font_norm = 'Times' if has_fonts else 'Helvetica'
    font_bold = 'Times-Bold' if has_fonts else 'Helvetica-Bold'
    font_ital = 'Times-Italic' if has_fonts else 'Helvetica-Oblique'

    c = canvas.Canvas(file_path, pagesize=A4)
    width, height = A4
    margin = 50

    c.setFont(font_bold, 16)
    title = settings.get("tieu_de", "NHẬT KÝ THI CÔNG").upper()
    c.drawCentredString(width / 2, height - 70, title)

    c.setFont(font_ital, 11)
    ngay_parts = data.get('ngay_thi_cong', '').split('/')
    if len(ngay_parts) == 3:
        date_str = f"Ngày {ngay_parts[0]} tháng {ngay_parts[1]} năm {ngay_parts[2]}"
    else:
        date_str = f"Ngày {data.get('ngay_thi_cong', '')}"
    c.drawRightString(width - margin, height - 90, date_str)

    # 1. Thời tiết
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 120, "1. Thời tiết:")
    c.setFont(font_norm, 11)
    weather_str = f"Sáng: {data.get('tt_sang','')} | Chiều: {data.get('tt_chieu','')} | Tối: {data.get('tt_toi','')}"
    c.drawString(margin + 100, height - 120, weather_str)
    draw_dotted_lines(c, margin + 100, height - 122, width - margin*2 - 100, 1, 15)

    # 2. Nhiệt độ
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 140, "2. Nhiệt độ:")
    c.setFont(font_norm, 11)
    temp_str = f"{data.get('nd_sang','-')}°C ~ {data.get('nd_chieu','-')}°C"
    c.drawString(margin + 100, height - 140, temp_str)
    draw_dotted_lines(c, margin + 100, height - 142, width - margin*2 - 100, 1, 15)

    # 3. Nhân công
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 160, "3. Nhân công:")
    c.setFont(font_norm, 11)
    c.drawString(margin + 100, height - 160, f"{data.get('so_cong_nhan','')} người")
    draw_dotted_lines(c, margin + 100, height - 162, 120, 1, 15)
    
    c.setFont(font_bold, 11)
    c.drawString(margin + 240, height - 160, "CB Kỹ thuật:")
    c.setFont(font_norm, 11)
    c.drawString(margin + 320, height - 160, f"{data.get('cb_ky_thuat','')} người")
    draw_dotted_lines(c, margin + 320, height - 162, width - margin - 320, 1, 15)

    # 4. Máy móc
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 180, "4. Máy móc, thiết bị:")
    c.setFont(font_norm, 11)
    draw_multiline_text(c, data.get('may_moc', ''), margin + 20, height - 200, width - margin*2 - 20, font_norm, 11, 15)
    draw_dotted_lines(c, margin + 20, height - 202, width - margin*2 - 20, 3, 15)

    # 5. Công việc thực hiện
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 260, "5. Công việc thực hiện trong ngày:")
    c.setFont(font_norm, 11)
    cv_thuc_hien = f"{data.get('hang_muc', '')}. {data.get('mo_ta', '')}"
    draw_multiline_text(c, cv_thuc_hien, margin + 20, height - 280, width - margin*2 - 20, font_norm, 11, 18)
    draw_dotted_lines(c, margin + 20, height - 282, width - margin*2 - 20, 6, 18)

    # 6. Công việc nghiệm thu
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 400, "6. Công việc nghiệm thu trong ngày:")
    c.setFont(font_norm, 11)
    draw_multiline_text(c, data.get('cong_viec_nghiem_thu', ''), margin + 20, height - 420, width - margin*2 - 20, font_norm, 11, 18)
    draw_dotted_lines(c, margin + 20, height - 422, width - margin*2 - 20, 4, 18)

    # 7. Vệ sinh môi trường
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 510, "7. Công tác vệ sinh môi trường:")
    c.setFont(font_norm, 11)
    vs = data.get('ve_sinh_mt', 'Tốt')
    draw_checkbox(c, margin + 220, height - 510, vs == 'Tốt', "Tốt")
    draw_checkbox(c, margin + 280, height - 510, vs == 'Bình thường', "Bình thường")
    draw_checkbox(c, margin + 380, height - 510, vs == 'Kém', "Kém")

    # 8. An toàn lao động
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 530, "8. Công tác an toàn lao động:")
    c.setFont(font_norm, 11)
    at = data.get('an_toan_ld', 'Tốt')
    draw_checkbox(c, margin + 220, height - 530, at == 'Tốt', "Tốt")
    draw_checkbox(c, margin + 280, height - 530, at == 'Bình thường', "Bình thường")
    draw_checkbox(c, margin + 380, height - 530, at == 'Kém', "Kém")

    # 9. Sự cố
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 560, "9. Các sự cố, hư hỏng, tai nạn LĐ phát sinh và khắc phục:")
    c.setFont(font_norm, 11)
    draw_multiline_text(c, data.get('su_co', 'Không'), margin + 20, height - 580, width - margin*2 - 20, font_norm, 11, 18)
    draw_dotted_lines(c, margin + 20, height - 582, width - margin*2 - 20, 2, 18)

    # 10. Kiến nghị
    c.setFont(font_bold, 11)
    c.drawString(margin, height - 630, "10. Các kiến nghị của nhà thầu thi công, TVGS:")
    c.setFont(font_norm, 11)
    draw_multiline_text(c, data.get('kien_nghi', 'Không'), margin + 20, height - 650, width - margin*2 - 20, font_norm, 11, 18)
    draw_dotted_lines(c, margin + 20, height - 652, width - margin*2 - 20, 2, 18)

    # CHỮ KÝ
    c.setFont(font_bold, 11)
    if settings.get("hien_thi_ky_1", 1):
        c.drawCentredString(width / 3.5, height - 720, settings.get("don_vi_1", "NHÀ THẦU THI CÔNG").upper())
        c.setFont(font_norm, 11)
        c.drawCentredString(width / 3.5, height - 790, settings.get("nguoi_ky_1", ""))
    
    c.setFont(font_bold, 11)
    if settings.get("hien_thi_ky_2", 1):
        c.drawCentredString(width - (width / 3.5), height - 720, settings.get("don_vi_2", "TƯ VẤN GIÁM SÁT").upper())
        c.setFont(font_norm, 11)
        c.drawCentredString(width - (width / 3.5), height - 790, settings.get("nguoi_ky_2", ""))

    c.setFont(font_ital, 9)
    c.drawRightString(width - margin, 30, "Trang: 1")

    c.save()
    return file_path