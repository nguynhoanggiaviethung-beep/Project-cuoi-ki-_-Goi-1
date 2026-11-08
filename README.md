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
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe app.py
```

Khi Terminal hiện server đang chạy, mở <http://127.0.0.1:8000/>. Giữ Terminal mở khi sử dụng web; nhấn `Ctrl+C` trong Terminal để dừng. Nếu báo cổng `8000` đang được sử dụng, đóng Terminal đang chạy server cũ rồi chạy lại.

## Kết nối dữ liệu SSI

Khi mở web, hộp thoại cho phép bạn kết nối SSI FastConnect hoặc tiếp tục xem mà chưa kết nối. Chọn **Kết nối dữ liệu** để mở collector dưới dạng tiến trình riêng; không cần mở Terminal thứ hai. Nút ngắt kết nối trong hộp thoại gửi yêu cầu dừng collector. Web không tự kết nối nếu bạn chưa chọn.

- Điền `SSI_API_KEY` và `SSI_API_SECRET` vào `.env` bằng thông tin SSI FastConnect. Không chia sẻ hai khóa này trong chat.
- Để khởi động collector thủ công khi cần xử lý sự cố, mở Terminal thứ hai và chạy:

```powershell
cd C:\Project-cuoi-ki-_-Goi-1
.\.venv\Scripts\python.exe scripts\ssi_worker.py
```

Worker tải danh mục cổ phiếu thường HOSE/HNX/UPCoM, kết nối SSI FastConnect và đồng bộ OHLC ngày. Khóa tiến trình ngăn mở hai worker cùng lúc; heartbeat được lưu để web hiển thị trạng thái. Nếu worker bị đóng đột ngột, lần chạy kế tiếp sẽ đóng run cũ trước khi bắt đầu.

Giá đóng cửa lịch sử hiển thị riêng với quote realtime. Spread và độ sâu chỉ được tính khi SSI gửi dữ liệu sổ lệnh; hệ thống không tự tạo dữ liệu thay thế.

Không chia sẻ hoặc commit file `.env`; file này chứa thông tin đăng nhập.


## Thu thập và kiểm chứng dữ liệu

- Worker dùng SSI FastConnect WebSocket cho quote; snapshot, chỉ số và trạng thái lần chạy được lưu trong SQLite.
- Đồng bộ OHLC ngày dùng REST SSI theo từng mã từ `HISTORY_START_DATE` (mặc định 01/01/2000), phân trang tối đa 10 trang × 1.000 dòng theo giới hạn tài liệu SSI; mỗi yêu cầu được giãn theo `HISTORY_REST_MIN_INTERVAL_SECONDS` và lỗi tạm thời được retry. Mã chỉ được đánh dấu backfill hoàn tất khi API trả trang cuối ngắn hơn page size; chạm giới hạn trang sẽ được báo là chưa xác nhận hết lịch sử.
- Lịch sử OHLC đầy đủ hơn được tải nền cùng worker; lần đầu có thể mất nhiều thời gian và chưa chạy cho tới khi collector được khởi chạy. Các lần sau chỉ cập nhật cửa sổ gần đây cho mã đã backfill.
- Để phân tích spread/depth theo phiên, giữ backend chạy trong ít nhất 5 phiên giao dịch có quote thật. OHLC ngày có thể có nhiều phiên nhưng không thay thế lịch sử sổ lệnh.
- Kiểm tra `/api/status`, `/api/quality` và `/api/collector/runs` để xem heartbeat, quote đã nhận, message trễ/trùng/bị rơi, lỗi và lịch sử phiên chạy. Trạng thái `connected` chỉ phản ánh kết nối; cần kiểm tra thời điểm quote mới nhất để xác nhận dữ liệu.
- Mọi mốc nhận/tính toán hiển thị theo `Asia/Ho_Chi_Minh`; cột UTC được lưu kèm để xử lý kỹ thuật. Dữ liệu gốc nhạy cảm được lọc trước khi lưu.
- `requirements.lock` khóa phiên bản thư viện để cài lại cùng môi trường; `requirements.txt` giữ dải phiên bản tương thích.

## Thành phần chính

- `app.py` — chạy web server.
- `src/sli/` — API, collector, phân tích và lưu SQLite.
- `frontend/` — giao diện dashboard.
- `database/` — cơ sở dữ liệu cục bộ.
# Đồng hồ phiên thị trường

- Mặc định dùng giờ thực Asia/Ho_Chi_Minh; lịch giờ theo sàn khai báo trong `src/sli/market_clock.py`.
- Có thể khai báo ngày nghỉ đã xác nhận bằng `MARKET_HOLIDAYS=YYYY-MM-DD,YYYY-MM-DD` hoặc bảng `session_calendar`.
- `MARKET_CLOCK_OVERRIDE` và `MARKET_CLOCK_SPEED` chỉ dùng thử giao diện trạng thái; đồng hồ này không tạo dữ liệu thị trường. Để trống `MARKET_CLOCK_OVERRIDE` trong môi trường thật và bản nộp.
- `/api/market-clock` trả trạng thái chung; `/api/stream` phát trạng thái, dữ liệu và heartbeat.
