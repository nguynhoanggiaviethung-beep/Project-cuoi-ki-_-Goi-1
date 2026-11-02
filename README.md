# Stock Liquidity Intelligence

Dashboard thanh khoản cổ phiếu, gồm dữ liệu spread, độ sâu sổ lệnh và queue imbalance. Backend FastAPI lưu snapshot vào SQLite; website có chế độ DEMO để xem trước.

## Yêu cầu

- Python 3.10 trở lên
- Windows PowerShell (các lệnh dưới đây)

## Cài đặt và chạy

Mở PowerShell tại thư mục dự án:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Nếu PowerShell chặn kích hoạt môi trường, chạy lệnh sau trong cửa sổ hiện tại rồi kích hoạt lại:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Mở <http://127.0.0.1:8000/>. Nhấn `Ctrl+C` trong Terminal để dừng server.

## Dữ liệu DEMO và dữ liệu VPBankS

Ứng dụng khởi chạy với dữ liệu **DEMO** tổng hợp. Để nhận dữ liệu NEO thật:

1. Đảm bảo tài khoản được cấp quyền NeoAPI và IP public đã được VPBankS whitelist.
2. Sao chép `.env.example` thành `.env`, rồi điền `VPBS_USERNAME` và `VPBS_PASSWORD`.
3. Khởi động ứng dụng, mở tab **Dữ liệu & phương pháp** và chọn **Bắt đầu luồng NEO**.
4. Chỉ xem dữ liệu là LIVE khi trạng thái kết nối thành công và có snapshot mới.

Giữ `.env` riêng tư; không gửi thông tin đăng nhập/token và không commit file này. Nếu kết nối thất bại, kiểm tra quyền API, IP whitelist và trạng thái collector trong ứng dụng.

## Cấu trúc chính

- `app.py` — khởi chạy web server
- `src/sli/` — API, collector, phân tích và SQLite
- `frontend/` — giao diện dashboard
- `database/` — cơ sở dữ liệu cục bộ
