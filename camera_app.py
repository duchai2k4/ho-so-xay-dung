import os
import cv2
import json
import sys
import urllib.request
import numpy as np
import tkinter as tk
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont, ImageTk
import threading

try:
    import geocoder
except ImportError:
    geocoder = None

def save_photo_to_log(path, title="Hình ảnh hiện trường", street="", district="", city="", coordinates=""):
    folder = os.path.join(os.getcwd(), "project_logs")
    os.makedirs(folder, exist_ok=True)
    meta_file = os.path.join(folder, "photo_meta.txt")

    with open(meta_file, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')} | {title} | {street}, {district}, {city} | {coordinates} | {path}\n")
    return path


class CameraApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Timestamp Camera - Tự Động Cập Nhật GPS (5s)")
        self.root.geometry("480x920")
        self.root.minsize(400, 800)
        self.root.configure(bg="#111827")

        self.cap = None
        self.running = False
        self.current_frame = None
        self.session_photos = []
        self.image_dir = os.path.join(os.getcwd(), "camera_images")
        os.makedirs(self.image_dir, exist_ok=True)

        # Biến lưu thông tin vị trí GPS
        self.coords_var = tk.StringVar(value="Đang lấy tọa độ GPS...")
        self.street_var = tk.StringVar(value="Đang xác định tên đường...")
        self.district_var = tk.StringVar(value="Đang xác định quận/huyện...")
        self.city_var = tk.StringVar(value="Đang xác định thành phố...")
        self.status_var = tk.StringVar(value="Sẵn sàng")

        # 1. KHUNG THÔNG TIN GPS TRÊN GIAO DIỆN
        self.top_bar = tk.Frame(root, bg="#1f2937", bd=1, relief="solid")
        self.top_bar.pack(fill=tk.X, padx=10, pady=(10, 4))

        loc_header = tk.Frame(self.top_bar, bg="#1f2937")
        loc_header.pack(fill=tk.X, padx=8, pady=(6, 2))

        tk.Label(loc_header, text="📍 THÔNG TIN ĐỊNH VỊ GPS (TỰ ĐỘNG 5S)", bg="#1f2937", fg="#38bdf8", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT)

        # Dòng 1: Tọa độ
        f1 = tk.Frame(self.top_bar, bg="#1f2937")
        f1.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(f1, text="🌐 Tọa độ:", bg="#1f2937", fg="#9ca3af", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(f1, textvariable=self.coords_var, bg="#1f2937", fg="#4ade80", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(4, 0))

        # Dòng 2: Tên đường
        f2 = tk.Frame(self.top_bar, bg="#1f2937")
        f2.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(f2, text="🛣️ Tên đường:", bg="#1f2937", fg="#9ca3af", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(f2, textvariable=self.street_var, bg="#1f2937", fg="#f9fafb", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(4, 0))

        # Dòng 3: Quận/Huyện
        f3 = tk.Frame(self.top_bar, bg="#1f2937")
        f3.pack(fill=tk.X, padx=8, pady=1)
        tk.Label(f3, text="🏙️ Quận/Huyện:", bg="#1f2937", fg="#9ca3af", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(f3, textvariable=self.district_var, bg="#1f2937", fg="#f9fafb", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(4, 0))

        # Dòng 4: Thành phố/Tỉnh
        f4 = tk.Frame(self.top_bar, bg="#1f2937")
        f4.pack(fill=tk.X, padx=8, pady=(1, 6))
        tk.Label(f4, text="🏢 Thành phố/Tỉnh:", bg="#1f2937", fg="#9ca3af", font=("Segoe UI", 8)).pack(side=tk.LEFT)
        tk.Label(f4, textvariable=self.city_var, bg="#1f2937", fg="#f9fafb", font=("Segoe UI", 8, "bold")).pack(side=tk.LEFT, padx=(4, 0))

        # 2. KHUNG PREVIEW CAMERA
        self.preview_frame = tk.Frame(root, bg="#000000", bd=0)
        self.preview_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        self.image_label = tk.Label(self.preview_frame, bg="#1f2937")
        self.image_label.pack(fill=tk.BOTH, expand=True)

        status_bar = tk.Label(root, textvariable=self.status_var, bg="#111827", fg="#9ca3af", font=("Segoe UI", 8))
        status_bar.pack(fill=tk.X, padx=12)

        # 3. NÚT CHỤP
        self.control_bar = tk.Frame(root, bg="#111827", height=90)
        self.control_bar.pack(fill=tk.X, padx=10, pady=(4, 12))
        self.control_bar.columnconfigure(0, weight=1)
        self.control_bar.columnconfigure(1, weight=1)
        self.control_bar.columnconfigure(2, weight=1)

        self.gallery_thumb_btn = tk.Button(self.control_bar, text="🖼️ (0)", bg="#374151", fg="#ffffff", 
                                           width=8, height=3, bd=0, font=("Segoe UI", 9, "bold"),
                                           command=self.open_gallery_drawer)
        self.gallery_thumb_btn.grid(row=0, column=0, sticky="w", padx=10)

        self.shutter_btn = tk.Button(self.control_bar, text="📷", bg="#ffffff", fg="#000000", 
                                     font=("Segoe UI", 16, "bold"), width=4, height=1, bd=3, relief="solid", 
                                     cursor="hand2", command=self.capture_photo)
        self.shutter_btn.grid(row=0, column=1, sticky="")

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        
        # Bắt đầu tự động cập nhật GPS định kỳ 5s
        self.auto_fetch_location()
        self.start_camera()

    # HÀM TỰ ĐỘNG CẬP NHẬT GPS MỖI 5 GIÂY (5000 MS)
    def auto_fetch_location(self):
        self.fetch_location_async()
        if self.root.winfo_exists():
            self.root.after(5000, self.auto_fetch_location)

    # BÓC TÁCH CHI TIẾT GPS NGẦM (XỬ LÝ ĐA TẦNG HTTPS CHO MẠNG DI ĐỘNG/ĐIỆN THOẠI)
    def fetch_location_async(self):
        def task():
            lat, lng = None, None
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

            # 1. Thử dịch vụ ipapi.co (HTTPS)
            try:
                req = urllib.request.Request("https://ipapi.co/json/", headers=headers)
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                    if 'latitude' in data and 'longitude' in data:
                        lat, lng = float(data['latitude']), float(data['longitude'])
            except Exception:
                pass

            # 2. Thử dịch vụ ipwho.is (HTTPS dự phòng - hỗ trợ mạng 3G/4G/5G rất tốt)
            if lat is None or lng is None:
                try:
                    req = urllib.request.Request("https://ipwho.is/", headers=headers)
                    with urllib.request.urlopen(req, timeout=4) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        if data.get('success') is True:
                            lat, lng = float(data['latitude']), float(data['longitude'])
                except Exception:
                    pass

            # 3. Thử geocoder mặc định
            if (lat is None or lng is None) and geocoder is not None:
                try:
                    g_ip = geocoder.ip('me')
                    if g_ip and g_ip.ok and g_ip.latlng:
                        lat, lng = g_ip.latlng
                except Exception:
                    pass

            # Bóc tách tên đường/quận/thành phố từ OpenStreetMap
            if lat is not None and lng is not None:
                coord_str = f"{lat:.7f}N {lng:.7f}E"
                self.coords_var.set(coord_str)
                
                try:
                    osm_url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lng}&zoom=18&addressdetails=1"
                    osm_req = urllib.request.Request(osm_url, headers={'User-Agent': 'TimestampCameraApp_Mobile/1.0'})
                    with urllib.request.urlopen(osm_req, timeout=4) as resp:
                        data = json.loads(resp.read().decode('utf-8'))
                        if 'address' in data:
                            addr = data['address']
                            house_no = addr.get('house_number', '')
                            road = addr.get('road', addr.get('pedestrian', addr.get('footway', '')))
                            street = f"{house_no} {road}".strip() if house_no else road
                            district = addr.get('suburb', addr.get('district', addr.get('city_district', addr.get('county', ''))))
                            city = addr.get('city', addr.get('state', addr.get('province', '')))

                            self.street_var.set(street if street else "Không rõ tên đường")
                            self.district_var.set(district if district else "Không rõ quận/huyện")
                            self.city_var.set(city if city else "Không rõ thành phố")
                except Exception:
                    pass

                self.status_var.set("✅ GPS cập nhật tự động (mỗi 5s)")
            else:
                self.status_var.set("⚠️ Không thể xác định tọa độ GPS qua IP/Mạng.")

        threading.Thread(target=task, daemon=True).start()

    def get_times_font(self, size):
        candidate_paths = [
            r"C:\Windows\Fonts\times.ttf",
            r"C:\Windows\Fonts\timesbd.ttf",
            r"C:\Windows\Fonts\arial.ttf",
            "DejaVuSerif.ttf",
        ]
        for path in candidate_paths:
            if path and os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def add_live_overlay(self, bgr_image):
        if bgr_image is None:
            return None

        image = bgr_image.copy()
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        
        f_size = 15
        font = self.get_times_font(f_size)
        color = (255, 255, 255)

        street = self.street_var.get()
        district = self.district_var.get()
        city = self.city_var.get()
        
        address_line = ", ".join([p for p in [street, district, city] if p and "Không rõ" not in p and "Đang xác định" not in p])
        if not address_line:
            address_line = "Chưa có dữ liệu địa chỉ"

        lines = [
            datetime.now().strftime('%d/%m/%Y %H:%M:%S'),
            self.coords_var.get(),
            address_line
        ]

        img_w, img_h = pil_image.size
        line_height = f_size + 6
        total_height = len(lines) * line_height

        y_start = max(10, img_h - total_height - 30)

        draw_temp = ImageDraw.Draw(pil_image)
        line_widths = []
        max_w = 0
        for line in lines:
            try:
                w = draw_temp.textlength(line, font=font)
            except AttributeError:
                w = font.getsize(line)[0]
            line_widths.append(w)
            if w > max_w:
                max_w = w

        x_right = img_w - 20

        overlay_box = Image.new('RGBA', pil_image.size, (0, 0, 0, 0))
        draw_box = ImageDraw.Draw(overlay_box)
        draw_box.rectangle([(x_right - max_w - 12, y_start - 6), (x_right + 10, y_start + total_height + 4)], fill=(0, 0, 0, 150))
        pil_image = Image.alpha_composite(pil_image.convert('RGBA'), overlay_box).convert('RGB')
        draw = ImageDraw.Draw(pil_image)

        for idx, (line, w) in enumerate(zip(lines, line_widths)):
            x_pos = x_right - w
            draw.text((x_pos, y_start + idx * line_height), line, font=font, fill=color)

        return cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)

    def show_image_in_preview(self, image_array):
        if image_array is None:
            return
        image_with_overlay = self.add_live_overlay(image_array)
        if image_with_overlay is not None:
            image_array = image_with_overlay

        rgb = cv2.cvtColor(image_array, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image = image.resize((410, 500), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image=image)
        self.image_label.configure(image=photo)
        self.image_label.image = photo
        self.current_frame = rgb.copy()

    def start_camera(self):
        if self.cap is None:
            if sys.platform == "win32":
                backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
            elif sys.platform == "darwin":
                backends = [cv2.CAP_AVFOUNDATION, cv2.CAP_ANY]
            else:
                backends = [cv2.CAP_V4L2, cv2.CAP_ANY]

            for backend in dict.fromkeys(backends):
                capture = cv2.VideoCapture(0, backend)
                if capture.isOpened():
                    self.cap = capture
                    break
                capture.release()

        if self.cap is None or not self.cap.isOpened():
            self.status_var.set("❌ Không mở được camera. Kiểm tra camera và quyền truy cập.")
            return
        self.running = True
        self.update_frame()

    def update_frame(self):
        if not self.running or self.cap is None or not self.cap.isOpened():
            return
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.flip(frame, 1)
            self.show_image_in_preview(frame)
        self.root.after(30, self.update_frame)

    def capture_photo(self):
        if self.cap is None or not self.cap.isOpened() or self.current_frame is None:
            self.status_var.set("❌ Camera chưa sẵn sàng, không thể chụp ảnh.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_path = os.path.join(self.image_dir, f"photo_{timestamp}.png")
        try:
            bgr = cv2.cvtColor(self.current_frame, cv2.COLOR_RGB2BGR)
            final_image = self.add_live_overlay(bgr)
            if final_image is None or not cv2.imwrite(file_path, final_image):
                self.status_var.set("❌ Không thể lưu ảnh. Kiểm tra quyền ghi thư mục.")
                return
        except (cv2.error, OSError) as exc:
            self.status_var.set(f"❌ Lỗi chụp hoặc lưu ảnh: {exc}")
            return

        abs_path = os.path.abspath(file_path)
        output_file = os.environ.get("CAMERA_OUTPUT_FILE")
        if output_file:
            try:
                with open(output_file, "a", encoding="utf-8") as f:
                    f.write(f"{abs_path}\n")
            except OSError as exc:
                self.status_var.set(f"Ảnh đã lưu nhưng không gửi được về ứng dụng: {exc}")
                return

        self.session_photos.append(abs_path)
        try:
            save_photo_to_log(
                abs_path,
                street=self.street_var.get(),
                district=self.district_var.get(),
                city=self.city_var.get(),
                coordinates=self.coords_var.get(),
            )
        except OSError as exc:
            self.status_var.set(f"Ảnh đã lưu; không thể ghi nhật ký ảnh: {exc}")
        else:
            self.status_var.set(f"📸 Đã lưu: {os.path.basename(file_path)}")
        self.refresh_gallery()

    def refresh_gallery(self):
        self.gallery_thumb_btn.config(text=f"🖼️ ({len(self.session_photos)})")

    def open_gallery_drawer(self, event=None):
        gal_win = tk.Toplevel(self.root)
        gal_win.title("Thư viện ảnh")
        gal_win.geometry("350x450")
        listbox = tk.Listbox(gal_win, font=("Segoe UI", 10))
        listbox.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        if os.path.exists(self.image_dir):
            for f in sorted(os.listdir(self.image_dir), reverse=True):
                if f.lower().endswith((".png", ".jpg", ".jpeg")):
                    listbox.insert(tk.END, f)

    def on_close(self):
        self.running = False
        if self.cap is not None and self.cap.isOpened():
            self.cap.release()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = CameraApp(root)
    root.mainloop()