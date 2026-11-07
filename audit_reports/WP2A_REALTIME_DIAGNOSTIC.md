# WP2a — Chẩn đoán realtime chỉ đọc (2026-11-06)

## Phạm vi

Không sửa mã sản phẩm, không khởi động backend/collector, không gọi SSI API và không ghi DB. Probe mở SQLite bằng immutable/read-only; chỉ xuất aggregate, không in dòng log thô hoặc credential.

Chạy lại: python scripts/rt_probe.py --session-date 2026-11-05

Probe kết thúc exit code 0. PRAGMA quick_check trả ok. SQLite build hiện tại không bật dbstat nên không thể lấy page allocation chính xác từng bảng/index.

## 1. Run collector running nhưng không có message

- DB có 25 collector_run_details: 19 running, 6 stopped. Cả 19 hàng running đều ended_at NULL, messages_received=0, last_message_at NULL, không error/reconnect/drop. Bắt đầu trong ngày 2026-11-05 từ 07:58:32 đến 18:56:22 +07.
- Khi probe, không có listener cổng 8000; PID 38872 và 38724 đọc từ log không còn tồn tại. Do đó 19 hàng running là bản ghi mồ côi/cũ, không phải bằng chứng có 19 collector đang chạy hiện tại.
- Nguyên nhân lưu trạng thái được xác nhận trong code: src/sli/database.py:32 tạo hàng ban đầu; src/sli/database.py:46-55 mới cập nhật ended_at/counters/status. src/sli/ssi_collector.py:211 tạo run_id và :306 gọi hàm kết thúc trong finally. Schema không có PID/heartbeat, observations không liên kết run_id. Nếu process bị kill/đóng không graceful, record nằm running với counters mặc định 0.
- Có 569,663 observations SSI ngày 2026-11-05, từ 09:00:00.821 đến 15:00:04.944. Không có run_id nên không gán được dữ liệu trong phiên về run 7–21; số 0 trong các record đó không chứng minh chúng không nhận message.
- logs/backend.stdout.log:1 ghi auth SSI thành công 18:41:52.227; dòng 3 ghi WebSocket connected 18:41:54.470. Thời điểm khớp run ID 23 bắt đầu 18:41:51. Không có log quote/trade/ACK. Đây là sau giờ liên tục; không có quote khi đó không chứng minh socket lỗi.
- Không có auth/connect log tương ứng ID 22, 24, 25. Do thiếu PID/run_id và log không đầy đủ theo lần chạy, không xác định được chúng từng nhận message khi process sống hay không.
- logs/backend.stderr.log:1,4 cho biết server PID 38872 khởi động. PID hiện không còn. database/server-error.log:1,4,6 cho biết PID 38724 gặp lỗi bind cổng 8000 rồi shutdown; đây là lỗi web bind, không chứng minh trực tiếp lỗi stream.
- Sáu run stopped ngày 2026-11-04 có lỗi NEO từ phiên bản cũ; không dùng làm bằng chứng cho SSI.

### Nguyên nhân gốc

**Đã xác định:** running bị stale vì không có heartbeat/PID và counters chỉ lưu khi run kết thúc. Trạng thái DB hiện tại không thể phân biệt một run còn sống nhưng chưa nhận dữ liệu với process đã chết.

**Chưa xác minh:** số message của từng run 7–21; run 22,24,25 có kết nối hay nhận message không. DB không có run_id trong observations/trades và log không ghi sự kiện nhận message.

Không đóng/sửa record trong WP2a. Quyết định worker riêng, heartbeat, khóa liên tiến trình và đóng run bỏ quên dành cho WP2b.

## 2. Số mức sổ lệnh quan sát được

Một mức đang có dữ liệu nghĩa là giá > 0 và khối lượng > 0 đồng thời; đơn vị SSI chưa xác minh.

| Sàn | Snapshot SSI | Bid L1 | Bid L2 | Bid L3 | Ask L1 | Ask L2 | Ask L3 |
|---|---:|---:|---:|---:|---:|---:|---:|
| HOSE | 491,067 | 489,044 | 484,774 | 477,932 | 486,736 | 476,917 | 462,947 |
| HNX | 44,727 | 43,392 | 41,579 | 39,622 | 44,223 | 43,462 | 42,189 |
| UPCOM | 33,869 | 32,688 | 31,166 | 28,641 | 32,349 | 30,864 | 28,970 |

Tối đa có 3 mức mỗi phía, nhưng không phải snapshot nào cũng đủ ba mức dương.

## 3. Phân rã 34,190 dòng off_tick

| Sàn | Loại theo catalog SSI | Giá trị số đang lưu | Dòng |
|---|---|---|---:|
| HOSE | Không khớp catalog | 10,000 đến dưới 20,000 | 15,828 |
| HOSE | Không khớp catalog | 20,000 đến dưới 50,000 | 18,362 |
| HNX/UPCOM | — | — | 0 |

Các nhóm chỉ dùng numeric stored values, không khẳng định đơn vị. Tập off_tick có min/mean/max mid hoặc giá phía đang có lần lượt 11,465 / 21,096.42 / 34,160. Cả 34,190 dòng đều không match catalog nên loại chứng khoán là unknown trong DB. Một vài mã giống ETF/CW theo dạng mã là dấu hiệu cần đối chiếu SSI instrument master, không phải phân loại đã xác nhận.

**Có bị tính là hợp lệ không:** 34,015 (99.49%) mang validation_status=valid; 175 (0.51%) là auction_phase. analytics.validate_observation() thêm cờ off_tick nhưng nhánh flags chỉ đổi status sang auction_phase nếu có cờ đấu giá; riêng off_tick vẫn trả valid. database._refresh_bar() lọc cờ off_tick khi lập bar, nên quality status valid và “được dùng phân tích” không đồng nghĩa.

## 4. Độ phủ bar theo phút phiên 2026-11-05

Phút dự kiến lấy từ giờ phiên đang cấu hình ở src/sli/market_rules.py, chưa xác minh giờ giao dịch theo tài liệu SSI/sàn. Probe chuẩn hóa minute_vn về giờ địa phương đến phút để tránh định dạng có/không offset làm đếm trùng.

| Sàn / pha | Bars | Phút có bar | Mã/phút min–max | Phút dự kiến theo config | Phút dự kiến có bar | Ghi chú |
|---|---:|---:|---:|---:|---:|---|
| HOSE sáng | 35,741 | 110 | 5–423 | 135 | 109 | Một bucket 11:30 ngoài cửa sổ config |
| HNX sáng | 4,456 | 110 | 1–64 | 150 | 109 | Một bucket 11:30 ngoài cửa sổ config |
| UPCOM sáng | 4,719 | 109 | 14–68 | 150 | 109 | — |
| HOSE chiều | 18,251 | 55 | 211–429 | 90 | 55 | 13:00–13:29 không có bar |
| HNX chiều | 2,287 | 55 | 24–61 | 90 | 55 | 13:00–13:29 không có bar |
| UPCOM chiều | 2,215 | 55 | 15–55 | 120 | 55 | Không có bar sau 14:29 dù config đến 15:00 |

Có 494 nhóm sàn-phút và 67,669 bar. Probe có số liệu từng phút trong JSON stdout trường minute_coverage. Thiếu bar không tự chứng minh mất kết nối; có thể do không có snapshot hợp lệ/bị lọc/thiếu data. Cần raw observations + heartbeat ở WP2b để phân biệt.
Trong 67,669 SSI bars ngày đó, 3,915 minute_vn thiếu offset múi giờ; 63,754 có offset. received_at_vn observations SSI có +07:00.

## 5. Dung lượng DB, bảng và raw payload

File 535,691,264 byte; page size 4,096; 130,784 pages; freelist 0. SQLite build thiếu dbstat nên không có exact allocated bytes theo bảng/index. Bảng dưới là tổng độ dài bytes của giá trị đã render qua SQL, dùng làm ước lượng nội dung logic, không phải dung lượng page vật lý.

| Bảng | Dòng | Logical value bytes xấp xỉ |
|---|---:|---:|
| observations | 607,323 | 216,065,361 |
| metrics | 607,323 | 97,591,784 |
| trade_ticks | 136,002 | 33,485,416 |
| bars_1m | 67,768 | 10,325,115 |
| historical_prices | 55,049 | 5,073,712 |
| alerts | 7,093 | 1,128,295 |
| ingestion_errors | 522 | 490,813 |
| securities_catalog | 1,517 | 217,276 |
| collector_run_details | 25 | 1,772 |

Observations + metrics chiếm khoảng 313.7 MB logical values. Tổng logical-rendered values tất cả bảng khoảng 364.4 MB; chênh với file vật lý gồm SQLite encoding, row/page và index overhead, không thể gán chính xác từng bảng khi thiếu dbstat. File không có free page.

### Có lưu raw payload đầy đủ cho mọi observation không?

Không. Cột raw_payload có mặt nhưng {} là placeholder:

- SSI observations: 569,663.
- Raw payload đầy đủ: 106,761 (18.74%); gồm các trạng thái khác valid và một mẫu valid.
- Placeholder {}: 462,902 (81.26%), đều valid.
- Trong 467,566 dòng valid, chỉ 4,664 (khoảng 1%) còn raw payload đầy đủ; 462,902 còn lại là placeholder.
- Raw payload bytes SSI: 26,914,611, bằng 5.02% kích thước DB.

src/sli/database.py ghi chú giữ invalid và deterministic khoảng 1% valid. Do đó raw payload không phải nguyên nhân chính gây DB 535MB; kích thước lớn chủ yếu do số dòng observations/metrics/trade và chỉ mục/SQLite overhead. Phần lớn raw feed của dòng valid không thể dựng lại.

## 6. Tỷ lệ loại chứng khoán trong 1,106 mã có bar

| Đối chiếu SSI catalog | Mã | Tỷ lệ |
|---|---:|---:|
| Có catalog, stock_type=Stock | 785 | 70.98% |
| Không có catalog để phân loại | 321 | 29.02% |
| Catalog xác nhận warrant/bond/non-stock | 0 | 0% xác nhận |

Tỷ lệ chứng quyền/trái phiếu thực trong 321 mã chưa xác minh được. Catalog trong DB chỉ có 1,517 dòng Stock, không có metadata type cho mã ngoài danh mục. Một số mã chưa map như CACB…, CFPT…, CVPB… có vẻ giống code chứng quyền, nhưng không kết luận loại nếu thiếu SSI instrument metadata.

Nguyên nhân code có thể để quote ngoài universe lọt vào: _load_catalog() chỉ đưa Stock vào board_by_symbol; nhánh TradeMessage lọc theo map này, còn nhánh QuoteMessage chỉ kiểm tra kiểu message trước khi lưu. Vì vậy quote ngoài catalog có thể tạo observations/bars (src/sli/ssi_collector.py:230-247).

## 7. Diff, kiểm tra và mục còn lại

- Thêm scripts/rt_probe.py: probe read-only cho DB/log, collector runs, book levels, off_tick, minute coverage, catalog type và kích thước logical.
- Thêm audit_reports/WP2A_REALTIME_DIAGNOSTIC.md.
- Không sửa mã sản phẩm, không migration/xóa/cập nhật bất kỳ record nào, không khởi động collector.
- Chạy script theo lệnh trên: exit code 0. Không chạy pytest vì không sửa product code.
- Còn chưa xác minh: run-level live proof, đủ 5 phiên, đơn vị giá/lô/quota SSI, phân loại chính thức của 321 mã không map, và physical size theo bảng/index do dbstat unavailable.
