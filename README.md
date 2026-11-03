# Stock Liquidity Intelligence

Dashboard thanh khoản cổ phiếu, gồm spread, độ sâu sổ lệnh, queue imbalance, bar 1 phút, cảnh báo theo baseline và mô phỏng book-walk. Backend FastAPI lưu dữ liệu vào SQLite; dữ liệu DEMO luôn được tách khỏi LIVE.

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
2. Sao chép `.env.example` thành `.env`, rồi điền `VPBS_USERNAME` và `VPBS_PASSWORD`. Khai báo `WATCHLIST` và sàn của từng mã trong `SYMBOL_MARKETS` (ví dụ `FPT:HOSE,ABC:HNX`).
3. Khởi động ứng dụng, mở tab **Dữ liệu & phương pháp** và chọn **Bắt đầu luồng NEO**.
4. Chỉ xem dữ liệu là LIVE khi trạng thái kết nối thành công và có snapshot mới.

Giữ `.env` riêng tư; không gửi thông tin đăng nhập/token và không commit file này. Mã chỉ được xem là LIVE khi có xác thực thành công và snapshot mới. Nếu kết nối thất bại, kiểm tra quyền API, IP whitelist và trạng thái collector.

## Backend

- `GET /api/status`, `/api/quality`, `/api/collector/runs` — trạng thái, chất lượng dữ liệu và lịch sử collector.
- `GET /api/bars` — bar 1 phút; hỗ trợ lọc mã, thời gian và bucket `OPEN/MID/CLOSE`.
- `GET /api/rankings`, `/api/alerts` — xếp hạng và cảnh báo.
- `POST /api/bookwalk/{symbol}?side=buy&quantity=100` — mô phỏng khớp theo sổ lệnh hiển thị; không gửi lệnh thật.
- `GET/PUT /api/calendar/{YYYY-MM-DD}?market=HOSE` — xem/đặt lịch nghỉ cho ngày giao dịch. Ngày lễ cần được cấu hình từ lịch chính thức.

Các tham số baseline, số bar xác nhận cảnh báo và cooldown có thể chỉnh trong file `.env` sau khi sao chép từ `.env.example`. Quy tắc tick và giờ phiên trong code là cấu hình theo sàn; xác minh đơn vị giá và mapping sàn của NEO trước khi phân tích LIVE.

## Cấu trúc chính

- `app.py` — khởi chạy web server
- `src/sli/` — API, collector, phân tích và SQLite
- `frontend/` — giao diện dashboard
- `database/` — cơ sở dữ liệu cục bộ
