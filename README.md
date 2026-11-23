# Stock Liquidity Intelligence

> Dashboard nghiên cứu thanh khoản cổ phiếu Việt Nam từ dữ liệu SSI: theo dõi sổ lệnh, spread, độ sâu, giao dịch khớp và OHLC lịch sử.

![Sơ đồ tổng quan Stock Liquidity Intelligence](docs/images/project-architecture.svg)

## Dự án làm gì?

Stock Liquidity Intelligence (SLI) giúp nhà đầu tư quan sát điều kiện thanh khoản của từng mã và toàn thị trường qua nhiều góc nhìn:

- **Tổng quan:** độ rộng thị trường, giá trị/khối lượng giao dịch, bối cảnh OHLC và các biểu đồ thanh khoản.
- **Danh mục của tôi:** theo dõi mã, spread, độ sâu, trạng thái dữ liệu và ghi chú cục bộ.
- **Chi tiết cổ phiếu:** sổ lệnh gần nhất, spread, độ sâu, lịch sử trong phiên và mô phỏng đi bộ sổ lệnh trên snapshot.
- **Xếp hạng và cảnh báo:** sàng lọc theo dữ liệu đã lưu và baseline lịch sử của mã.
- **Dữ liệu & phương pháp:** trạng thái collector, độ phủ, thời điểm dữ liệu và cách tính chỉ tiêu.

Đây là công cụ quan sát và phân tích, **không dự báo giá, không đưa khuyến nghị mua/bán và không gửi lệnh giao dịch**.

## Dữ liệu và giới hạn

- Quote, sổ lệnh và giao dịch khớp được nhận từ **SSI FastConnect WebSocket**; OHLC ngày được đồng bộ từ **SSI Market Data REST**.
- Dữ liệu được chuẩn hóa và lưu trong SQLite cục bộ. `database/sli.sqlite3` là database truy vấn chính; các file `database/YYYY-MM-DD.sqlite` là bản lưu thô theo ngày.
- WebSocket gửi dữ liệu theo sự kiện. Mã không phát sinh cập nhật có thể không có quote mới trong một khoảng thời gian; SLI không tự điền dữ liệu giả để tạo độ phủ 100%.
- `connected` chỉ xác nhận kết nối. Hãy xem thêm thời điểm quote nhận/lưu gần nhất và trạng thái trong `/api/status`.
- OHLC lịch sử không thay thế dữ liệu sổ lệnh. Độ sâu và spread chỉ có khi SSI gửi snapshot tương ứng.
- Toàn bộ thời gian hiển thị dùng múi giờ `Asia/Ho_Chi_Minh`. Lịch phiên theo sàn được khai báo tập trung tại `src/sli/config.py`.

## Cấu trúc dự án

![Sơ đồ cấu trúc repository](docs/images/project-structure.svg)

```text
.
├── app.py                   # Backend FastAPI/Uvicorn và API dữ liệu
├── streamlit_app.py         # Giao diện người dùng Streamlit
├── src/sli/                 # API, lịch phiên, collector, phân tích và SQLite
├── frontend/                # Dashboard HTML, CSS, JavaScript và tài nguyên tĩnh
├── scripts/                 # Worker SSI, tác vụ Windows, launcher Streamlit và công cụ dữ liệu
├── tests/                   # Kiểm thử các logic backend
├── database/                # SQLite cục bộ (không đưa dữ liệu runtime lên GitHub)
├── docs/images/             # Hình minh họa kiến trúc và repository
├── .env.example             # Mẫu cấu hình; không chứa khóa thật
├── requirements.txt         # Dải phiên bản thư viện tương thích
└── requirements.lock       # Phiên bản thư viện đã khóa để cài lại ổn định
```

## Chạy trên Windows

### 1. Yêu cầu

- Python 3.10 trở lên
- PowerShell
- Khóa `SSI_API_KEY` và `SSI_API_SECRET` để nhận dữ liệu SSI thật. Có thể mở web khi chưa có khóa, nhưng collector SSI sẽ không kết nối.

### 2. Tạo môi trường và cài thư viện

Mở PowerShell tại thư mục gốc repository (thư mục có `app.py`).

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-streamlit.txt
```

Nếu `.venv` đã tồn tại và hoạt động, bỏ qua lệnh tạo môi trường. Có thể dùng `requirements.txt` nếu muốn cài các phiên bản tương thích thay vì các phiên bản đã khóa.

### 3. Cấu hình SSI

Tạo file cấu hình cục bộ:

```powershell
Copy-Item .env.example .env
notepad .env
```

Điền khóa vào `SSI_API_KEY` và `SSI_API_SECRET`. Giữ `DB_PATH=database/sli.sqlite3` nếu muốn dùng database mặc định. **Không commit `.env`, khóa SSI hoặc các file SQLite lên GitHub.** Các file này đã được đưa vào `.gitignore`.

### 4. Khởi chạy hệ thống (giao diện Streamlit)

Mở PowerShell tại thư mục dự án:

```powershell
cd C:\Project-cuoi-ki-_-Goi-1
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\scripts\run_streamlit.ps1
```

Launcher kiểm tra sức khỏe FastAPI ở cổng 8000. Nếu backend chưa chạy, launcher khởi động FastAPI ở chế độ nền; backend không tự khởi động hay dừng collector. Sau đó launcher mở giao diện Streamlit tại <http://127.0.0.1:8501/>. Nếu API hoặc Streamlit đã chạy khỏe mạnh, launcher dùng lại tiến trình đó, không tạo bản trùng.

Để thu thập SSI độc lập với web, mở **một PowerShell riêng** và chạy:

```powershell
cd C:\Project-cuoi-ki-_-Goi-1
.\.venv\Scripts\python.exe run_collector.py
```

Giữ tiến trình này chạy trong phiên giao dịch; đóng tab hoặc restart FastAPI không dừng worker. Nút **Kết nối dữ liệu** trong giao diện vẫn có thể khởi chạy worker theo yêu cầu rõ ràng. Worker dùng khóa chống chạy trùng.

**Chỉ dùng một giao diện và một lệnh chạy:** mở <http://127.0.0.1:8501/> bằng Streamlit, khởi chạy qua `scripts\run_streamlit.ps1`. Không chạy `app.py` riêng. Cổng 8000 là API nội bộ và dashboard được Streamlit nhúng để giữ nguyên giao diện; mở trực tiếp `http://127.0.0.1:8000/` sẽ chuyển về Streamlit. API vẫn có thể được kiểm tra tại:

- <http://127.0.0.1:8000/api/status>
- <http://127.0.0.1:8000/api/market-clock>
- <http://127.0.0.1:8000/api/quality>
- <http://127.0.0.1:8000/api/collector/runs>

Nút **Làm mới dữ liệu** xóa cache giao diện và tải lại từ API. Trạng thái backend:

- <http://127.0.0.1:8000/api/status>
- <http://127.0.0.1:8000/api/market-clock>
- <http://127.0.0.1:8000/api/quality>
- <http://127.0.0.1:8000/api/collector/runs>

Dừng giao diện bằng `Ctrl+C` trong cửa sổ Streamlit. Backend được launcher quản lý ở chế độ nền; collector là tiến trình riêng. Dừng giao diện hoặc backend không dừng collector.

Nếu `/api/status` báo worker chưa chạy dù `.env` có đủ khóa, kiểm tra log `logs\ssi_worker.log` và trạng thái tại `/api/collector/runs` trước. Chỉ khởi động thủ công khi đã xác nhận không có collector đang chạy:

```powershell
.\.venv\Scripts\python.exe scripts\ssi_worker.py
```

Worker có khóa chống chạy trùng. Không chạy lệnh worker chỉ vì trang hoặc tab tải chậm. Nhấn `Ctrl+C` trong terminal chạy Streamlit chỉ dừng giao diện Streamlit; FastAPI và collector worker vẫn tiếp tục. Đóng tab trình duyệt cũng không dừng hai tiến trình này. Mở/restart FastAPI không tự sinh collector mới.

### Tự khởi chạy collector khi đăng nhập Windows

Tùy chọn này dùng Task Scheduler để chạy collector khi đăng nhập và lúc 08:45 từ thứ Hai đến thứ Sáu. Máy cần bật, đăng nhập và có kết nối mạng.

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\scripts\install_daily_collector_task.ps1
```

Task Scheduler không lưu khóa SSI; worker đọc `.env`. Gỡ tác vụ:

```powershell
Unregister-ScheduledTask -TaskName "Stock Liquidity Intelligence - SSI Collector" -Confirm:$false
```

## Cổng chạy

Chỉ mở giao diện tại <http://127.0.0.1:8501/> và chỉ khởi chạy bằng `scripts\run_streamlit.ps1`. Launcher kiểm tra sức khỏe, tái sử dụng FastAPI/Streamlit nếu đang chạy, và chỉ khởi tạo phần còn thiếu; không tạo thêm bản chạy song song. Cổng 8000 là backend nội bộ cần cho dashboard được nhúng, không phải địa chỉ giao diện thứ hai. Nếu một cổng bị chiếm nhưng dịch vụ không khỏe mạnh, kiểm tra tiến trình thay vì chạy thêm lệnh `app.py` hoặc `streamlit run`.

## Đồng bộ lịch sử và vận hành dữ liệu

- Worker đồng bộ OHLC theo từng mã từ `HISTORY_START_DATE` (mặc định `2000/01/01`), phân trang và giãn cách yêu cầu theo cấu hình SSI.
- Đồng bộ OHLC đầy đủ chạy sau khi các sàn đóng cửa để tránh tranh chấp ghi với collector realtime.
- Quote và trade được lưu trong database chính và kho SQLite theo ngày. Các biểu đồ đọc dữ liệu đã chuẩn hóa trong database chính.
- Khi database không có bar cho một mã/phút, điều đó không tự động có nghĩa là lỗi ghi: nguồn có thể không gửi cập nhật cho mã đó trong phút ấy.
- Lịch sử xếp hạng, spread và độ sâu phụ thuộc vào dữ liệu thực tế đã thu thập. Không thể khôi phục dữ liệu WebSocket của những phiên máy không chạy collector.

### Đối soát độ phủ bar của các phiên đã lưu

Nếu số bar_count/symbol_count trong bảng sessions không khớp với bars_1m sau khi nhập kho ngày hoặc dựng lại bar, chạy lệnh bảo trì sau trong PowerShell. Lệnh chỉ cập nhật bảng tổng hợp phiên, không xóa bar/OHLC và không dừng collector:

~~~powershell
.\.venv\Scripts\python.exe scripts\reconcile_session_summaries.py
~~~

Độ phủ sổ lệnh (bar) và độ phủ OHLC là hai phép đo riêng. Nguồn SSI gửi quote theo sự kiện nên ứng dụng không thể bảo đảm 100% mọi mã/phút nếu nhà cung cấp không gửi dữ liệu; dashboard phải báo tỷ lệ thực tế thay vì nội suy dữ liệu thiếu.

### Xác minh luồng realtime và ghi database

`/api/status` và `/api/quality` báo riêng trạng thái quote và trade. Chỉ trạng thái `live` / `realtime_verified=true` mới có nghĩa là worker còn sống, socket đã nối, SSI xác nhận đăng ký đủ kênh, đã nhận message mới, đã ghi ít nhất một bản ghi hợp lệ vào SQLite, database không bận và hàng đợi không có message bị rớt. `connected` một mình chỉ xác nhận socket, không chứng minh dữ liệu đang đến hoặc đã được lưu. Các trạng thái `subscription_unverified`, `awaiting_quote`, `awaiting_persistence`, `persistence_error`, `persistence_stale` và `degraded` chỉ rõ mắt xích cần xử lý; quote và trade được đánh giá độc lập. Ngoài giờ giao dịch liên tục, hệ thống báo `outside_session` và hiển thị mốc dữ liệu gần nhất thay vì gọi dữ liệu cũ là LIVE.

Trang **Dữ liệu & phương pháp** hiển thị đăng ký sàn/kênh, ACK, message, mốc ghi DB, độ dài hàng đợi và số message rớt. Cơ chế health đọc snapshot heartbeat đã lấy nền, không quét lịch sử hay chờ truy vấn biểu đồ trong request giao diện. Các cột health mới được thêm bằng migration khi collector/backend khởi động lần kế tiếp; không tự chạy migration giữa một phiên thu dữ liệu.

Schema INV-C `portfolio_positions` được tạo bằng migration bổ sung khi FastAPI backend khởi động; migration không chạy full bootstrap hoặc đổi chế độ WAL của database lịch sử. Worker SSI chạy tách tiến trình, nên cập nhật backend để nạp route không yêu cầu khởi chạy lại worker. Hồ sơ vị thế được lưu cục bộ và bắt đầu rỗng; người dùng tự thêm vị thế thật, ứng dụng không tạo dữ liệu danh mục mẫu.

### Quy tắc baseline phân vị

Nhãn thuận lợi/căng thẳng dùng phân vị lịch sử riêng theo mã và cùng pha trong ngày. Chỉ tính khi có ít nhất 120 phiên hoàn tất và tối thiểu 120 bar hợp lệ; nếu cùng pha chưa đủ mẫu, hệ thống gộp các pha hợp lệ của chính mã đó nhưng vẫn yêu cầu đủ 120 phiên. Dưới ngưỡng này, giao diện phải báo thiếu baseline và không gán nhãn. Đây là quy ước sàng lọc của đồ án, không phải ngưỡng bắt buộc của HOSE/HNX/UPCoM.

### Phân tích vị thế INV-C

Trang Danh mục của tôi → Vị thế đang nắm giữ lưu số cổ phiếu, giá vốn, ngày mua và ghi chú trong SQLite cục bộ. Số phiên thoát ước tính được tính bằng số cổ phiếu / (ADV20 × tỷ lệ tham gia); cần đủ 20 phiên OHLC có khối lượng. Tỷ lệ tham gia mặc định 10%, người dùng có thể chọn 5–30%. Mô phỏng bán theo bid dùng snapshot SSI và tối đa ba mức hiển thị; kết quả không phải cam kết khớp lệnh và hệ thống không gửi lệnh.

## Cấu hình thường dùng

Các giá trị có thể đặt trong `.env`; xem đầy đủ tên và chú thích trong `.env.example`.

| Biến | Mặc định | Ý nghĩa |
|---|---:|---|
| `DB_PATH` | `database/sli.sqlite3` | Đường dẫn database SQLite chính |
| `SLI_HOST` | `127.0.0.1` | Địa chỉ web server lắng nghe |
| `SLI_PORT` | `8000` | Cổng web server |
| `HISTORY_START_DATE` | `2000/01/01` | Ngày bắt đầu yêu cầu OHLC lịch sử từ SSI |
| `MARKET_HOLIDAYS` | *(trống)* | Ngày nghỉ đã xác nhận, phân cách bằng dấu phẩy |
| `MARKET_CLOCK_OVERRIDE` | *(trống)* | Chỉ dùng giả lập đồng hồ khi kiểm tra giao diện; để trống khi chạy thật |

## Phát triển và kiểm thử

Cài thêm công cụ phát triển nếu cần:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

Các kiểm thử không cần khóa SSI; việc lấy dữ liệu thật cần thông tin xác thực FastConnect hợp lệ trong `.env`.

## Giao diện Streamlit

Streamlit tại cổng 8501 là giao diện duy nhất dành cho người dùng. `streamlit_app.py` nhúng dashboard để giữ nguyên giao diện và tương tác; FastAPI tại cổng 8000 chỉ phục vụ nội bộ cho iframe và API. Chạy dự án bằng `scripts\run_streamlit.ps1`; không khởi chạy `app.py` riêng hoặc mở cổng 8000 làm giao diện.

Các trang Streamlit gồm tổng quan, danh mục cục bộ, chi tiết mã, xếp hạng, lịch sử cảnh báo và trạng thái/chất lượng dữ liệu. Những phép tính nặng chỉ tải khi người dùng mở hoặc yêu cầu trang tương ứng. Trong phiên, phần biểu đồ được làm mới định kỳ và cache ngắn; ngoài phiên, giao diện đọc lịch sử đã lưu với cache dài hơn. Nút làm mới chủ động xóa cache.

Tắt Streamlit không dừng FastAPI hay collector. Backend và database không bị thay thế; Streamlit đọc/ghi hồ sơ người dùng qua API hiện có và không tự ghi vào SQLite.

