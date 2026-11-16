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

Khi Terminal hiện `Application startup complete` và không có lỗi bind cổng, mở <http://127.0.0.1:8000/>. Giữ Terminal đó mở khi sử dụng web; nhấn `Ctrl+C` tại đúng Terminal này để dừng server.

### Nếu gặp `WinError 10048` (cổng 8000 đang được sử dụng)

Lỗi này có nghĩa là một chương trình khác — thường là một cửa sổ Terminal đang chạy `app.py` — đã chiếm `127.0.0.1:8000`. Lần chạy mới sẽ tự thoát; đây không phải lỗi môi trường Python. Thường bạn chỉ cần mở <http://127.0.0.1:8000/> vì server cũ vẫn đang chạy.

Nếu muốn khởi động lại server:

1. Tìm cửa sổ Terminal VS Code đang chạy web và nhấn `Ctrl+C` ở đó.
2. Nếu không tìm thấy Terminal cũ, kiểm tra tiến trình đang giữ cổng:

   ```powershell
   netstat -ano | findstr :8000
   ```

   Lấy PID ở cột cuối của dòng `LISTENING`, rồi xác minh tiến trình:

   ```powershell
   tasklist /FI "PID eq <PID>"
   ```

3. Chỉ khi đã xác nhận PID đó là tiến trình Python của dự án, dừng đúng PID trong PowerShell:

   ```powershell
   Stop-Process -Id <PID>
   ```

4. Chạy lại `python.exe app.py` một lần. Không mở thêm server thứ hai trên cổng 8000.

Muốn chạy song song để thử nghiệm, dùng một cổng khác trong cùng cửa sổ PowerShell:

```powershell
$env:SLI_PORT = "8001"
.\.venv\Scripts\python.exe app.py
```

Sau đó mở <http://127.0.0.1:8001/>. Mỗi cửa sổ Terminal có thể giữ giá trị `SLI_PORT` riêng.

## Kết nối dữ liệu SSI

Worker SSI FastConnect chạy riêng với web để lưu quote, khớp lệnh và đồng bộ OHLC vào SQLite. Có thể cài tác vụ Windows để worker tự chạy khi đăng nhập và lúc 08:45 từ thứ Hai đến thứ Sáu; worker giữ kết nối qua các phiên và cập nhật OHLC hằng ngày. Mở PowerShell trong thư mục dự án và cài một lần:

```powershell
.\scripts\install_daily_collector_task.ps1
```

Tác vụ đọc khóa từ `.env`, ghi log tại `logs\ssi_worker.log`, tự thử lại tối đa 3 lần nếu worker thoát lỗi và khóa tiến trình chống chạy trùng. Máy cần bật và có mạng; hiện cấu hình tác vụ chạy trong phiên người dùng Windows. Có thể xem trạng thái trong web hoặc Task Scheduler với tên **Stock Liquidity Intelligence - SSI Collector**. Để gỡ tác vụ: `Unregister-ScheduledTask -TaskName "Stock Liquidity Intelligence - SSI Collector" -Confirm:$false`.

Khi không cài tác vụ tự động, hộp thoại web vẫn có nút **Kết nối dữ liệu** để chạy worker thủ công.

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
- Lịch sử OHLC ban đầu và phần bổ sung được tải nền cùng worker. Worker kiểm tra lại dữ liệu OHLC mỗi ngày giao dịch, đồng bộ tăng dần theo từng mã và thử bù dữ liệu sau khi đóng phiên; dữ liệu sổ lệnh không thể suy ra từ OHLC.
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

