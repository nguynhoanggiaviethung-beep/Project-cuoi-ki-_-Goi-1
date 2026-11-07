# Kế hoạch sửa SLI — FIX_PLAN

## Phạm vi và giả định

- Đây là kế hoạch, không sửa source trong lượt này.
- Giả định SSI API được giảng viên chấp nhận thay NEO theo lời xác nhận trong prompt. Tài liệu/email có ngày chưa được cung cấp; cần bổ sung vào hồ sơ.
- Repository không có docs/ hoặc docs/ssi/. DOCX SLI v1.1 ngoài repo là tài liệu chuẩn gần nhất; không có đề cập nhật để kiểm tra nhóm API tối thiểu/quota.
- Dùng lại bằng chứng audit 2026-11-06: 17 tests pass; DB integrity trả ok; chỉ một phiên order-book SSI; OHLC ngày 63 phiên không tính là phiên order-book; audit năm mã cho 418 bars khớp DB/API.
- Chỉ coi quote/order-book streaming là đầu vào spread/depth. Trade ticks/OHLC là dữ liệu bổ trợ; không suy spread/depth từ chúng.
- Ước lượng: S = nhỏ (vài giờ), M = khoảng 1–2 ngày, L = trên 2 ngày hoặc phụ thuộc tích lũy dữ liệu. Không gồm thời gian chờ đủ phiên.

## Thứ tự phụ thuộc

WP1 chuẩn hóa contract SSI → WP2 collector tin cậy + thu thập liên tục → WP3 cách ly demo và WP4 chất lượng → WP5 công thức/baseline → WP6 service/as_of/API chung → WP7 giao diện → WP8 kiểm thử nghiệm thu → WP9 báo cáo/đóng gói.

## Gói công việc

### WP1 — Chuẩn hóa nguồn SSI

- **Mục tiêu:** tạo adapter DataSource độc lập; chuẩn hóa nhãn DATA_SOURCE_LABEL; tài liệu trường nguồn→schema nội bộ, kiểu, đơn vị, thời gian, số mức, trần/sàn/tham chiếu/tổng giá trị. Phân định rõ quote là nguồn thanh khoản, trade/OHLC là bổ trợ.
- **Tiêu chí:** G-01, D-01–D-03, D-07–D-09, AC-02, DM-02, DM-05, T-01/T-03.
- **File dự kiến:** src/sli/ssi_collector.py, src/sli/analytics.py, src/sli/config.py, src/sli/api.py, README.md, .env.example; thêm docs/data_dictionary.md, data_sample/ssi_payload_sample.json, src/sli/datasource.py (nếu cần).
- **Rủi ro:** SDK typed models/serialized payload có thể không giữ field names nguyên trạng; giá SSI có thể biểu diễn theo đơn vị khác giá UI; không thể suy trường trần/sàn/reference/value nếu QuoteMessage không mang chúng. Không được điền tên field/đơn vị theo phỏng đoán.
- **Kiểm tra:** lập mapping từ model và payload DB đã khử nhạy cảm; fixture round-trip mapper; xác minh field bắt buộc/optional; so giá/khối lượng với bảng SSI cùng thời điểm; test contract adapter và nhãn config.
- **Công sức:** M.
- **Cần bạn làm tay:** cung cấp tài liệu SSI chính thức hoặc export SDK schema; xác nhận giá hiển thị và đơn vị; cung cấp email/ngày giảng viên chấp thuận SSI; duyệt mẫu đã ẩn thông tin.
- **Cần quyết định:** có trường hợp nào SSI không trả đủ 3 mức/metadata? Nhãn hiển thị mong muốn là “SSI FastConnect” hay tên ngắn khác?

### WP2 — Collector tin cậy

- **Mục tiêu:** trạng thái collector phản ánh socket/ACK/message/persistence; reconnect/backoff, heartbeat, stale-run recovery, graceful shutdown, hàng đợi không làm mất quote/trade im lặng; lưu độc lập và truy vết từng lần chạy.
- **Tiêu chí:** G-02, D-02, D-04, D-11/D-12, AC-01/03/04, AC-09, S-05.
- **File dự kiến:** src/sli/ssi_collector.py, src/sli/database.py, src/sli/config.py, src/sli/api.py, tests/test_backend_core.py; thêm tests/test_ssi_collector.py.
- **Rủi ro:** SDK có auto_reconnect nhưng sự kiện reconnect và xác nhận subscribe có thể khác giữa phiên bản; quote coalescing có thể làm mất các trạng thái trung gian (phù hợp snapshot mới nhất nhưng không phù hợp tick audit). Collector hiện chạy trong thread app.
- **Kiểm tra:** fake stream/clock cho connect, ACK thiếu, mất kết nối, reconnect, queue full, shutdown và run crash; kiểm tra DB read-only sau phiên chạy; test không thể báo LIVE khi chỉ connected mà chưa có quote mới/persist.
- **Công sức:** L.
- **Cần bạn làm tay:** giữ backend chạy qua ít nhất 5 phiên giao dịch có quote thật; lưu log trạng thái đã khử secrets và thời điểm đầu/cuối từng phiên.
- **Cần quyết định:** collector nên chạy cùng web process hay thành lệnh worker riêng? Bao lâu không nhận quote thì chuyển stale? Giữ hay bỏ gộp snapshot mỗi mã?

### WP3 — Cách ly dữ liệu demo

- **Mục tiêu:** đảm bảo demo không thể lọt vào KPI/ranking/chart/alerts nguồn SSI; chọn nguồn minh bạch, không fallback tự động sang dữ liệu giả; hiển thị nhãn dữ liệu rõ ràng.
- **Tiêu chí:** D-04, B-07/B-08, AC-09/11, OV-02/04, CB-01, DM-04, S-01/S-06.
- **File dự kiến:** src/sli/database.py, src/sli/api.py, src/sli/detail_service.py, src/sli/ranking.py, frontend/app.js, frontend/enhancements.js, tests/test_backend_core.py; có thể thêm tests/test_source_isolation.py.
- **Rủi ro:** dữ liệu cũ dùng source='live' trong khi hiện nguồn ssi; cần định nghĩa migration/compatibility trước khi gộp hoặc tách.
- **Kiểm tra:** seed SSI và demo với số khác nhau; assert từng endpoint chỉ đọc source được chọn, không xuất hiện demo khi chọn SSI; thử DB rỗng và source missing.
- **Công sức:** M.
- **Cần bạn làm tay:** xác nhận có cần giữ trang/chế độ demo để trình diễn hay loại bỏ khỏi sản phẩm nộp.
- **Cần quyết định:** source live cũ có được chuyển thành SSI sau khi truy nguyên, hay giữ tách biệt?

### WP4 — Chất lượng dữ liệu

- **Mục tiêu:** đo và hiển thị các lý do thiếu/crossed/locked/auction/one-sided/off_tick/late/duplicate/type error; timestamp VN nhất quán; không đếm cờ chồng lấn thành nhóm loại trừ cộng gộp; xử lý khoảng trống và partial data.
- **Tiêu chí:** G-03, D-09–D-12, AC-06/09, OV-09, DM-01/03, S-05.
- **File dự kiến:** src/sli/analytics.py, src/sli/database.py, src/sli/config.py, src/sli/api.py, frontend/app.js, frontend/enhancements.js, tests/test_backend_core.py; thêm tests/test_data_quality.py.
- **Rủi ro:** status flags hiện có thể chồng lấn; chuyển thành phân rã mutually-exclusive có thể làm số cũ khác. Nguồn timestamp thiếu trong khoảng 1.07% SSI records.
- **Kiểm tra:** property tests cho timestamp ISO/+07, duplicate/out-of-order, null/wrong types, crossed/locked/one-sided/auction/off_tick; reconciliation test có nhóm riêng không chồng và tổng raw; đảm bảo unknown không bị ép thành 0.
- **Công sức:** M.
- **Cần bạn làm tay:** cung cấp ít nhất 2–3 mẫu message lỗi hoặc ảnh status thật nếu còn lưu; xác nhận định nghĩa session calendar.
- **Cần quyết định:** ngưỡng stale khi thị trường mở/đóng và cách hiển thị source timestamp thiếu?

### WP5 — Logic tài chính

- **Mục tiêu:** rà tick size/lot size/session, spread_ticks, depth, imbalance, book walk, ranking eligibility, baseline theo pha phiên; thêm fallback pooled theo spec khi đủ điều kiện; không composite liquidity score và không nhìn trước.
- **Tiêu chí:** F-01–F-16, AC-05/06/08, OV-03/05/07/08, CT-03/04/05/06, XH-02/03/05, CB-01–06.
- **File dự kiến:** src/sli/analytics.py, src/sli/market_rules.py, src/sli/bookwalk.py, src/sli/ranking.py, src/sli/database.py, src/sli/detail_service.py, tests/test_backend_core.py, tests/test_ranking.py, tests/test_detail_service.py; thêm fixture tests Appendix A.
- **Rủi ro:** quy tắc tick/lot và phiên có thể cập nhật; baseline 60 bar/pha chưa có đủ 5 phiên; pooled fallback phải tuân đúng DOCX và không tạo kết luận khi thiếu dữ liệu.
- **Kiểm tra:** fixtures Appendix A cho Qmax, VWAP, fill, partial; boundary tick/price/volume; random 5 mã raw→bar→API; no-lookahead synthetic timeline; phân biệt min sample với snapshot count; Amihud chỉ từ OHLC/trade nếu định nghĩa quy định.
- **Công sức:** L.
- **Cần bạn làm tay:** phê duyệt bộ ví dụ thủ công; cung cấp nguồn xác nhận tick/lot/ATO/ATC mới nhất nếu docx không còn hiện hành.
- **Cần quyết định:** ngưỡng mẫu tối thiểu cho ranking và baseline; nếu pooled baseline chưa đủ 60 bar thì ẩn hay ghi “chưa đủ dữ liệu”?

### WP6 — Service và API dùng chung

- **Mục tiêu:** một service và một as_of cho mọi widget trong một lần tải; time-window nhất quán; request chỉ đọc aggregations đã lưu, không tự tính từ snapshot thô; mỗi widget lỗi độc lập.
- **Tiêu chí:** B-02–B-08/B-10, OV-04–OV-06/OV-12, CT-01/02/06, DM-01/05.
- **File dự kiến:** src/sli/api.py, src/sli/detail_service.py, src/sli/database.py, src/sli/ranking.py; có thể thêm src/sli/market_service.py; tests/test_detail_service.py, tests/test_ranking.py, tests/test_backend_core.py.
- **Rủi ro:** hiện routes riêng có thể dùng cửa sổ/clock khác nhau; service chung có thể tạo query nặng nếu whole market.
- **Kiểm tra:** freeze as_of trong tests, assert response meta/window đồng nhau; fault injection mỗi widget; compare KPI to bars; performance query with indexes/read-only DB.
- **Công sức:** L.
- **Cần bạn làm tay:** chọn cửa sổ mặc định (phiên hiện tại, 5 phiên) theo mục tiêu luận văn.
- **Cần quyết định:** response envelope chung cho mọi widget hay endpoint dashboard aggregate?

### WP7 — Giao diện từng trang

- **Mục tiêu:** mỗi widget chỉ dùng service chung; trạng thái loading/empty/stale/error riêng; units/axes, sample count, time window/source/as_of rõ; demo không lẫn SSI; accessibility và layout 1366×768.
- **Tiêu chí:** AC-07/09, OV-01/02/10–15, CT-01/02/07, XH-01/04/06/07, CB-02/07, DM-01–04, HD-01–04.
- **File dự kiến:** frontend/index.html, frontend/app.js, frontend/enhancements.js, frontend/styles.css; API contract chỉnh ở src/sli/api.py.
- **Rủi ro:** chart widget có thể nuốt lỗi chung hoặc dùng endpoint cũ; không có browser automation sẵn theo audit trước.
- **Kiểm tra:** browser screenshots 1366×768; kiểm tra Overview, Detail, Ranking, Alerts, Data/Method, Guide; network offline/empty/stale; axis labels, units, sample counts, retry và scroll stability.
- **Công sức:** L.
- **Cần bạn làm tay:** duyệt bằng mắt trên máy bạn sau khi chụp trạng thái mở/đóng phiên; cung cấp ảnh kỳ vọng nếu có.
- **Cần quyết định:** giữ ảnh nền động hay ưu tiên đồ thị/độ tương phản? Cửa sổ mặc định trên overview là phiên hay 5 phiên?

### WP8 — Kiểm thử và kiểm chứng

- **Mục tiêu:** gom tests thành regression suite; audit tính toán độc lập; xác minh source, session, API limit, UI và lỗi collector; kiểm tra riêng 4 verification methods theo đặc tả.
- **Tiêu chí:** V-01–V-05, AC-05/10/12, OV-06/07/11, CT-03/04, XH-05, S-05.
- **File dự kiến:** tests/test_backend_core.py, tests/test_detail_service.py, tests/test_ranking.py, thêm tests/test_ssi_collector.py, tests/test_data_quality.py; audit_reports/audit_recompute.py, scripts/.
- **Rủi ro:** API key/SSI access không có trong test sandbox; kiểm thử live cần tài khoản và giờ thị trường.
- **Kiểm tra:** pytest sạch; deterministic fixtures; audit raw→bars→API 5 mã; 4 verification checks; manual live compare 2–3 mã/time; retry/rate-limit test. Không coi TestClient 200 là bằng chứng stream live.
- **Công sức:** L.
- **Cần bạn làm tay:** chạy đối chiếu live trong giờ giao dịch và ghi thời gian/ảnh đã che ID; duy trì dữ liệu đủ 5 phiên.
- **Cần quyết định:** xác nhận mức sai số cho giá/khối lượng so với bảng SSI (ngoài phép so sánh nội bộ vốn yêu cầu <=1e-6).

### WP9 — Báo cáo và đóng gói

- **Mục tiêu:** tài liệu nguồn SSI, đặc tả cập nhật, hướng dẫn cài mới, biến môi trường, data provenance, test/audit commands, kết quả xác minh và evidence sạch secrets.
- **Tiêu chí:** T-01–T-04, AC-12, DM-02–DM-04, HD-03/04, S-01–S-04.
- **File dự kiến:** README.md, .env.example, requirements.txt, requirements.lock, .gitignore, docs/data_dictionary.md, docs/ssi/ (tài liệu SSI do người dùng cung cấp), audit_reports/, thư mục evidence được chốt.
- **Rủi ro:** khóa/secret/consumer ID có thể nằm trong ảnh/log; quota/API docs thay đổi; không đưa dữ liệu có điều khoản hạn chế vào repo công khai.
- **Kiểm tra:** cài sạch trong venv test; chạy pytest và audit script; secret scan source/history/reports/media; checklist file package; link tài liệu và trích dẫn/phiên bản rõ.
- **Công sức:** M.
- **Cần bạn làm tay:** cung cấp thông báo chấp nhận SSI, tài liệu SSI có quyền chia sẻ, ảnh/video đã che nhạy cảm.
- **Cần quyết định:** repo nộp có chứa DB mẫu không, hay chỉ schema + sample đã ẩn? Có yêu cầu video demo/bản tiếng Anh không?

## Công việc không thể hoàn tất chỉ bằng sửa mã

| Việc | Lý do | Người thực hiện / bằng chứng |
|---|---|---|
| Thu đủ ít nhất 5 phiên order-book SSI | Cần thời gian và thị trường có quote thực; OHLC không thay order-book. | Bạn giữ collector chạy qua phiên; lưu ngày/số message/status. |
| Chứng minh LIVE và đối chiếu bảng SSI | Không thể xác nhận từ dữ liệu đã lưu cũ/TestClient. | Bạn đối chiếu 2–3 mã cùng timestamp trong giờ thị trường, lưu ảnh/log đã che thông tin. |
| Xác nhận SSI được chấp nhận | Prompt nêu nhưng nội dung/ngày email chưa có. | Bạn gửi hoặc lưu thông báo để đính kèm hồ sơ. |
| Xác nhận đơn vị/quota/giới hạn subscribe | Repo không có tài liệu SSI chính thức. | Bạn cung cấp docs/SSI account docs; không gửi secrets. |
| Kiểm tra visual cuối | Môi trường audit không có browser automation sẵn; trạng thái màu/layout cần màn hình thật. | Bạn xác nhận trang trên màn hình 1366×768 và gửi lỗi còn lại. |
| Kiểm định out-of-sample / Amihud | Cần đủ dữ liệu nhiều phiên và phương án nghiên cứu. | Thực hiện sau WP2/WP5 với dataset có nguồn/ngày rõ. |

## Cổng nghiệm thu trước khi đóng dự án

1. SSI được chấp thuận có bằng chứng lưu trong hồ sơ.
2. Tối thiểu 5 phiên order-book thật, timestamp Asia/Ho_Chi_Minh và có đối chiếu độc lập.
3. Không còn lẫn demo/SSI; không có synthetic history thay dữ liệu thiếu.
4. Tests công thức/edge cases/baseline/no-lookahead đều pass; mọi widget dùng chung as_of/window.
5. UI từng trang qua empty/error/stale/live và kiểm tra bằng mắt.
6. Báo cáo tái lập được, không chứa secrets hoặc thông tin nội bộ.
