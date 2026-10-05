# Ho So Xay Dung

Ung dung Flet quan ly nhat ky thi cong, tien do, bien ban nghiem thu va thu vien cong viec.

## Cai dat

### Windows (cai dat va chay bang mot cu nhap doi)

1. Cai Python 3.10 tro len va danh dau **Add Python to PATH** trong qua trinh cai dat.
2. Dat `setup.bat` va `requirements.txt` o thu muc cung cap voi `main.py`.
3. Nhap doi `setup.bat`. Script se tao `.venv` neu chua co, cai cac goi trong
   `requirements.txt`, sau do chay ung dung. Lan dau can ket noi Internet de cai goi.

Neu `requirements.txt` chua ton tai, script se cai `flet` lam goi mac dinh.

### Cap nhat requirements.txt

Sau khi cai them goi vao `.venv` va da xac nhan ung dung chay duoc, xuat danh sach
goi dang cai dat bang PowerShell tu thu muc du an:

    .\.venv\Scripts\python.exe -m pip freeze > requirements.txt

Lenh nay ghi lai ca cac goi phu thuoc va phien ban hien tai; hay xem lai ket qua
truoc khi chia se de loai bo goi khong lien quan neu can. `requirements.txt` hien
tai duoc tao theo cac phu thuoc cua `pyproject.toml`, gom ca cac goi cho tinh nang camera.

### PostgreSQL va cache SQLite

Ung dung dung driver PostgreSQL pure-Python `pg8000` de tuong thich voi Android,
ket noi truc tiep bang `pg8000.dbapi.connect` (khong can SQLAlchemy). Dien URL
PostgreSQL dang `postgresql://...` vao `DATABASE_URL` trong `core/database.py`;
cac tham so SSL trong URL duoc ap dung khi tao ket noi. Khong commit hoac chia se
URL co mat khau. Moi thanh vien can duoc cap quyen truy cap database. Khi chay
lan dau, ung dung tu tao cac bang va du lieu mau tren database PostgreSQL.

Luu y phat hanh mobile: `DATABASE_URL` hien dang duoc khai bao trong ma nguon va
se duoc dong goi cung APK; nguoi dung co the trich xuat thong tin ket noi.
Khong phat hanh APK cho nguoi dung ben ngoai truoc khi chuyen ket noi NeonCloud
sang backend/API do ban kiem soat. Bien moi truong tren may build khong tu dong
duoc truyen vao tien trinh Python ben trong APK.


Sau khi khoi tao, `du_lieu_thi_cong.db` la cache SQLite cho cac thao tac doc/ghi;
tren Android, cache va hang doi dong bo duoc luu trong thu muc ho tro rieng cua ung dung,
khong luu canh APK; tren may tinh, cache van nam trong thu muc du an.
lan chay dau can ket noi NeonCloud de nap snapshot chuan. Cac lan khoi dong sau,
snapshot moi duoc nap neu khong co thay doi dang cho dong bo; neu offline, cache
da khoi tao van dung duoc. Ghi SQLite duoc dua vao hang doi ben
`du_lieu_thi_cong.db.sync` va gui len NeonCloud trong nen, co thu lai neu ket noi
loi. Schema cac bang nghiep vu khong doi; file sao luu
`du_lieu_thi_cong.db.pre-neon-sync.bak` duoc tao truoc lan nap snapshot dau tien.
Khong xoa file `.sync` khi hang doi chua dong bo xong.

### Build APK Android

Chay lenh sau tu thu muc du an:

    flet build apk

SQLite va hang doi dong bo tren Android duoc dat trong thu muc application
support do Flet cung cap; cac ket noi NeonCloud va thao tac khoi tao database
chay ngoai luong giao dien. Gui email OTP va loi moi cung khong chay dong bo tren
luong giao dien. PDF tren Android su dung font Roboto he thong neu co.


De cac ID tu tang khop giua cache va NeonCloud, moi du an chi nen duoc ghi tren
mot thiet bi tai mot thoi diem. Hay cho thiet bi truoc dong bo xong truoc khi
chuyen sang thiet bi khac. Khi khoi dong lai, cache se nhan snapshot moi nhat tu
NeonCloud neu khong con thay doi dang cho.

Neu muon cai dat thu cong, co the dung:

    python -m venv .venv
    .\.venv\Scripts\activate
    python -m pip install -r requirements.txt

### Cau hinh email SMTP Gmail

Ung dung dung Gmail SMTP de gui OTP, email khoi phuc mat khau va loi moi du an.
Tao Google App Password (tai khoan Gmail phai bat xac minh 2 buoc); khong dung
mat khau Gmail thong thuong.

Tren Windows, mo PowerShell va khai bao bien moi truong sau (thay cac gia tri
mau bang thong tin cua ban):

    setx SMTP_SENDER_EMAIL "you@gmail.com"
    setx SMTP_APP_PASSWORD "your-16-character-app-password"

Dong va mo lai PowerShell/VS Code sau khi dung `setx`, sau do chay lai ung dung.
De chi cau hinh tam thoi trong cua so PowerShell hien tai, co the dung:

    $env:SMTP_SENDER_EMAIL = "you@gmail.com"
    $env:SMTP_APP_PASSWORD = "your-16-character-app-password"

May chu va cong SMTP mac dinh la `smtp.gmail.com:587` (STARTTLS). Neu can, co
the ghi de bang `SMTP_SERVER` va `SMTP_PORT`. Khong commit App Password vao ma
nguon, README, hoac repository.

**Cac bien moi truong tren may build khong duoc dua vao APK.** Vi vay, khong
nhung Gmail App Password vao ung dung Android. De gui OTP va loi moi tu dien
thoai, can trien khai dich vu mail backend tai `backend/mail_api.py` tren may
chu do ban quan ly:

1. Dat `SMTP_SENDER_EMAIL` va `SMTP_APP_PASSWORD` trong environment/secrets cua
   may chu, khong dat trong APK.
2. Cau hinh HTTPS va gioi han request/anti-abuse o reverse proxy hoac nen tang
   host truoc khi mo API ra Internet. API co gioi han co ban theo IP, email va
   toan cuc tren moi tien trinh; gioi han nay khong thay the bao ve tai proxy
   va se reset khi dich vu khoi dong lai.
3. Sau khi deploy, dien URL goc HTTPS cua API vao `mail_api_url` trong
   `app_config.json`, roi build va cai lai APK.

Backend co endpoint `/healthz`, `/v1/email/otp` va `/v1/email/invitation`.
Chay `python -m backend.mail_api` chi de kiem tra trong mang noi bo; WSGI server
tich hop khong co HTTPS va khong nen mo truc tiep ra Internet. Desktop van gui
SMTP truc tiep theo cac bien moi truong nhu truoc. Neu chua co backend hoac SMTP
tren thiet bi, app hien thong bao cau hinh mail backend thay vi dua Gmail secret
vao APK.

Email loi moi du an co nut mo lien `hosoxaydung://project?id={project_id}`.
De Android mo ung dung khi bam nut email, build app mot lan bang `flet build apk`,
sau do them intent filter ben duoi vao ben trong the `<activity>` trong
`build\flutter\android\app\src\main\AndroidManifest.xml`:

    <intent-filter>
        <action android:name="android.intent.action.VIEW" />
        <category android:name="android.intent.category.DEFAULT" />
        <category android:name="android.intent.category.BROWSABLE" />
        <data android:scheme="hosoxaydung" android:host="project" />
    </intent-filter>

Build lai APK bang `flet build apk` va cai ban APK moi tren dien thoai. Neu chay
`flet clean` hoac xoa thu muc `build`, Flet se tao lai Flutter project va can
them intent filter lai. Du an hien chua co cau hinh native dang ky scheme; chi
chen lien ket vao email khong tu dong dang ky deep link voi he dieu hanh. Neu
phat hanh iOS, cung can khai bao URL scheme trong `Info.plist`.

Bien moi truong tren may Windows chi co tac dung voi tien trinh Python chay tren
may do; APK tren dien thoai khong tu dong nhan cac bien nay. Khong dong goi Gmail
App Password vao APK, vi co the bi trich xuat. Neu ung dung chay truc tiep tren
thiet bi cua nguoi dung, hay su dung backend mail do ban kiem soat.
Neu App Password tung duoc commit/chia se truoc day, hay thu hoi no trong tai
khoan Google va tao App Password moi.

## Chay ung dung

    python main.py

## Cau truc

- core/   : logic nghiep vu (DB, PDF, tien ich)
- ui/     : giao dien Flet (auth + app chinh)
- camera_app.py : tool camera desktop chay nhu subprocess

## Chuc nang chinh

- Dang nhap / Dang ky: xac thuc OTP qua email SMTP.
- Dashboard: chon du an, xem thong tin tom tat, dieu huong.
- Thong tin cong trinh: quan ly du an va thanh vien.
- Nhat ky thi cong: lap nhat ky, chup anh hien truong co timestamp + GPS, xuat PDF.
- Tien do: import Excel, tao nhat ky va bien ban tu dong.
- Thu vien cong viec: quan ly dinh muc, TCVN, PCCC.
- Bien ban nghiem thu: quan ly phieu nghiem thu.
- Hop thu / Tai khoan: nhan loi moi du an, doi mat khau.
