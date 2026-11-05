# Stock Liquidity Intelligence

Dashboard theo dõi thanh khoản cổ phiếu: spread, độ sâu sổ lệnh, queue imbalance, xếp hạng, cảnh báo và mô phỏng sổ lệnh. Backend dùng FastAPI và SQLite.

## Chạy web bằng PowerShell

Mở Terminal trong VS Code, chọn PowerShell, rồi chạy lần lượt:

```powershell
cd C:\Project-cuoi-ki-_-Goi-1
.\.venv\Scripts\python.exe app.py
```

Để chạy lần đầu nếu chưa có thư mục `.venv`, tạo môi trường và cài thư viện trước:

```powershell
cd C:\Project-cuoi-ki-_-Goi-1
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Khi Terminal hiện server đang chạy, mở <http://127.0.0.1:8000/>. Giữ Terminal mở khi sử dụng web; nhấn `Ctrl+C` trong Terminal để dừng. Nếu báo cổng `8000` đang được sử dụng, đóng Terminal đang chạy server cũ rồi chạy lại.

## Chế độ dữ liệu

Ứng dụng khởi động với nguồn SSI FastConnect; không tự thay bằng dữ liệu mô phỏng:

- Điền `SSI_API_KEY` và `SSI_API_SECRET` vào `.env` bằng thông tin SSI FastConnect. Không chia sẻ hai khóa này trong chat.
- Khởi chạy web. Backend tự kết nối SSI, tải danh mục cổ phiếu thường HOSE/HNX/UPCoM và đồng bộ OHLC ngày lịch sử cho từng mã và lưu vào SQLite. Trong tab **Dữ liệu & phương pháp**, nút **Kết nối SSI FastConnect** dùng để kết nối lại khi cần.

Giá đóng cửa lịch sử hiển thị riêng với quote realtime. Spread và độ sâu chỉ được tính khi SSI gửi dữ liệu sổ lệnh; hệ thống không tự tạo dữ liệu thay thế.

Không chia sẻ hoặc commit file `.env`; file này chứa thông tin đăng nhập.

## Thành phần chính

- `app.py` — chạy web server.
- `src/sli/` — API, collector, phân tích và lưu SQLite.
- `frontend/` — giao diện dashboard.
- `database/` — cơ sở dữ liệu cục bộ.
