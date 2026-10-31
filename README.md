# Stock Liquidity Intelligence

Ứng dụng cục bộ để quan sát spread và độ sâu sổ lệnh cổ phiếu. Backend FastAPI phục vụ frontend tĩnh, collector NEO chạy độc lập với dashboard, dữ liệu được lưu trong SQLite.

## Chạy chế độ demo

Mở PowerShell tại thư mục project và chạy:
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    .\.venv\Scripts\python.exe .\app.py

Mở http://127.0.0.1:8000. Lần đầu sẽ thấy màn hình chào mừng với preview demo. Dữ liệu demo là dữ liệu tổng hợp, có nhãn DEMO.

## Chạy luồng NEO

1. Đảm bảo tài khoản đã được cấp quyền và IP public của mạng chạy collector đã whitelist.
2. Đặt VPBS_USERNAME, VPBS_PASSWORD và các cấu hình trong file .env (không commit file này).
3. Mở tab Dữ liệu & phương pháp, chọn Bắt đầu luồng NEO.
4. Chỉ chuyển sang LIVE khi trạng thái collector là Connected và có snapshot mới.

Collector dùng WSS và không tự hạ xuống WS không mã hóa. Nếu endpoint không phân giải được hoặc token bị từ chối, giao diện hiển thị trạng thái/lỗi để kiểm tra quyền, IP và endpoint với VPBankS.

## Thành phần

- src/sli/collector.py: token, WebSocket, subscribe, reconnect và trạng thái collector.
- src/sli/analytics.py: mapping payload, validation, spread/depth/imbalance.
- src/sli/database.py: schema SQLite, observations, metrics, alerts và dữ liệu demo.
- src/sli/api.py: REST API cho dashboard và thao tác collector.
- frontend/: website, onboarding, tab điều hướng và biểu đồ SVG.

Dashboard chỉ đọc dữ liệu đã lưu; nó không mở WebSocket riêng. Snapshot demo/live được tách nguồn trong database. Payload thực tế cần được xác minh trước khi coi mapping là hoàn chỉnh.