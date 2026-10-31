const state = { route: "overview", symbols: [], source: "demo", status: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
  }[char]));
}
function fmt(value, digits = 2) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "—";
  return Number(value).toLocaleString("vi-VN", { maximumFractionDigits: digits });
}
function fmtTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? escapeHtml(value) :
    date.toLocaleString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" });
}
async function api(path, options = {}) {
  const response = await fetch(path, { headers: { "Accept": "application/json" }, ...options });
  if (!response.ok) {
    let message = "HTTP " + response.status;
    try { message = (await response.json()).detail || message; } catch (_) {}
    throw new Error(message);
  }
  return response.json();
}
function setRoute(route) {
  state.route = route;
  $$(".view").forEach((view) => view.classList.toggle("hidden", view.id !== "view-" + route));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.route === route));
  if (route === "overview") loadOverview();
  if (route === "detail") loadDetail();
  if (route === "rankings") loadRankings();
  if (route === "alerts") loadAlerts();
  if (route === "methodology") loadMethodology();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function enterApp(route = "overview") {
  localStorage.setItem("sli-onboarding-done", "1");
  $("#onboarding").classList.add("hidden");
  $("#application").classList.remove("hidden");
  setRoute(route);
  refreshStatus();
}
function onboardingStage(stage) {
  const labels = ["XEM TRƯỚC", "GIỚI THIỆU", "HƯỚNG DẪN"];
  const titles = ["Thanh khoản nhìn rõ hơn.", "Đọc điều kiện giao dịch đang niêm yết.", "Bắt đầu trong vài bước."];
  const texts = [
    "Theo dõi spread và độ sâu sổ lệnh cổ phiếu trong một màn hình rõ ràng, có giải thích và nguồn dữ liệu minh bạch.",
    "SLI thu thập snapshot bid/ask, chuẩn hóa dữ liệu rồi tính các chỉ tiêu để bạn quan sát thay đổi theo thời gian.",
    "Kiểm tra trạng thái dữ liệu, chọn mã và khoảng thời gian, đọc KPI rồi xem biểu đồ hoặc cảnh báo."
  ];
  const nextLabels = ["Xem giới thiệu", "Xem hướng dẫn", "Vào dashboard"];
  $("#welcome-step-label").textContent = labels[stage];
  $("#welcome-title").textContent = titles[stage];
  $("#welcome-text").textContent = texts[stage];
  $("#welcome-next").textContent = nextLabels[stage] + " →";
  $$(".step-dots i").forEach((dot, index) => dot.classList.toggle("active", index === stage));
  $("#welcome-extra").innerHTML = stage === 0
    ? '<span class="inline-note">Preview dùng dữ liệu tổng hợp và luôn được đánh dấu DEMO.</span>'
    : stage === 1
      ? '<div class="onboarding-pills"><span>Spread</span><span>Top 3 depth</span><span>Queue imbalance</span></div>'
      : '<ol class="onboarding-list"><li>Xem nhãn DEMO/LIVE và thời điểm cập nhật.</li><li>Chọn mã, đọc spread và depth.</li><li>Kiểm tra baseline trước khi diễn giải cảnh báo.</li></ol>';
  $("#welcome-skip").textContent = stage === 2 ? "Bỏ qua, vào dashboard" : "Bỏ qua giới thiệu và hướng dẫn";
  $("#welcome-next").dataset.stage = String(stage);
}
function renderLineChart(target, series, key, color, label) {
  const node = document.querySelector(target);
  if (!node) return;
  const data = (series || []).map((row) => row[key]).filter((value) =>
    value !== null && value !== undefined && Number.isFinite(Number(value))
  ).map(Number);
  if (data.length < 2) {
    node.innerHTML = '<div class="empty-state">Chưa đủ dữ liệu để vẽ biểu đồ.</div>';
    return;
  }
  const width = 800, height = 220, left = 48, right = 14, top = 16, bottom = 28;
  const min = Math.min(...data), max = Math.max(...data);
  const span = max - min || Math.max(Math.abs(max) * 0.05, 1);
  const points = data.map((value, index) => {
    const x = left + (index / (data.length - 1)) * (width - left - right);
    const y = top + ((max - value) / span) * (height - top - bottom);
    return [x, y];
  });
  const path = points.map((point, index) => (index ? "L" : "M") + point[0].toFixed(1) + " " + point[1].toFixed(1)).join(" ");
  const grid = [0, 1, 2, 3].map((line) => {
    const y = top + line * (height - top - bottom) / 3;
    return '<line x1="' + left + '" y1="' + y + '" x2="' + (width - right) + '" y2="' + y + '" stroke="#e8eeec"/>';
  }).join("");
  node.innerHTML = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="' + escapeHtml(label) + '">' +
    grid + '<path d="' + path + '" fill="none" stroke="' + color + '" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>' +
    '<circle cx="' + points[points.length - 1][0] + '" cy="' + points[points.length - 1][1] + '" r="5" fill="' + color + '"/>' +
    '<text x="' + left + '" y="' + (height - 5) + '" fill="#8a9995" font-size="11">0</text>' +
    '<text x="' + (width - right) + '" y="' + (height - 5) + '" fill="#8a9995" font-size="11" text-anchor="end">' + data.length + ' snapshot</text></svg>';
}
function renderDepthChart(target, series) {
  const node = document.querySelector(target);
  if (!node) return;
  const bids = (series || []).map((row) => row.bid_depth_value).filter((value) =>
    value !== null && value !== undefined && Number.isFinite(Number(value))
  ).map(Number);
  const asks = (series || []).map((row) => row.ask_depth_value).filter((value) =>
    value !== null && value !== undefined && Number.isFinite(Number(value))
  ).map(Number);
  if (bids.length < 2 || asks.length < 2) {
    node.innerHTML = '<div class="empty-state">Chưa đủ dữ liệu depth hai phía.</div>';
    return;
  }
  const width = 800, height = 200, pad = 16;
  const max = Math.max(...bids, ...asks), min = Math.min(...bids, ...asks), span = max - min || 1;
  function pathFor(values) {
    return values.map((value, index) => {
      const x = pad + index / Math.max(values.length - 1, 1) * (width - pad * 2);
      const y = pad + (max - value) / span * (height - pad * 2);
      return (index ? "L" : "M") + x.toFixed(1) + " " + y.toFixed(1);
    }).join(" ");
  }
  node.innerHTML = '<svg viewBox="0 0 ' + width + ' ' + height + '" role="img" aria-label="Bid và ask depth theo thời gian">' +
    '<path d="' + pathFor(bids) + '" fill="none" stroke="#006B3F" stroke-width="3"/>' +
    '<path d="' + pathFor(asks) + '" fill="none" stroke="#E2231A" stroke-width="3"/></svg>';
}
function table(headers, rows, emptyText = "Chưa có dữ liệu.") {
  if (!rows || !rows.length) return '<div class="empty-state">' + escapeHtml(emptyText) + '</div>';
  return '<div class="table-wrap"><table><thead><tr>' + headers.map((header) => '<th>' + escapeHtml(header) + '</th>').join("") +
    '</tr></thead><tbody>' + rows.map((row) => '<tr>' + row.map((cell) => '<td>' + cell + '</td>').join("") + '</tr>').join("") +
    '</tbody></table></div>';
}
function statusCell(label, value) {
  return '<div class="status-cell"><small>' + escapeHtml(label) + '</small><strong>' + escapeHtml(value ?? "—") + '</strong></div>';
}
async function refreshStatus() {
  try {
    const data = await api("/api/status");
    state.status = data;
    state.source = data.source;
    const live = data.source === "live";
    $("#mode-badge").textContent = live ? "LIVE" : "DEMO";
    $("#mode-badge").className = "badge " + (live ? "badge-live" : "badge-demo");
    $("#connection-label").textContent = data.connection_status || "—";
    $("#connection-dot").className = "status-dot " + (
      data.connection_status === "connected" ? "connected" :
      ["error", "disconnected"].includes(data.connection_status) ? "error" : ""
    );
    $("#last-update").textContent = fmtTime(data.last_data_at);
    const notice = $("#global-notice");
    notice.className = "notice " + (live ? (data.connection_status === "error" ? "notice-error" : "notice-live") : "notice-demo");
    let message = live
      ? "LIVE · Nguồn NEO API. Trạng thái collector: " + data.connection_status + "."
      : "DEMO · Dữ liệu tổng hợp để xem trước, không phải dữ liệu thị trường thật.";
    if (data.is_stale) message += " Dữ liệu đã cũ; không xem là live.";
    if (data.last_error) message += " " + data.last_error;
    $("#notice-text").textContent = message;
    if (state.route === "methodology") renderCollectorStatus(data);
  } catch (error) {
    $("#connection-label").textContent = "Backend không phản hồi";
    $("#notice-text").textContent = "Không gọi được backend: " + error.message;
    $("#global-notice").className = "notice notice-error";
  }
}
function renderCollectorStatus(data) {
  const items = [
    ["Nguồn dữ liệu", data.source === "live" ? "NEO LIVE" : "DEMO"],
    ["Trạng thái", data.connection_status],
    ["Mã theo dõi", (data.configured_watchlist || []).join(", ")],
    ["Message nhận", fmt(data.messages_received, 0)],
    ["Snapshot hợp lệ", fmt(data.rows_valid, 0)],
    ["Lỗi / trùng", fmt(data.rows_invalid, 0) + " / " + fmt(data.duplicate_messages, 0)],
    ["Reconnect", fmt(data.reconnect_count, 0)],
    ["Message cuối", fmtTime(data.last_data_at)]
  ];
  $("#collector-status").innerHTML = items.map((item) => statusCell(item[0], item[1])).join("");
}
async function loadSymbols() {
  const data = await api("/api/symbols");
  state.symbols = data.symbols || [];
  ["#overview-symbol", "#detail-symbol"].forEach((selector) => {
    const select = $(selector);
    if (!select) return;
    const previous = select.value;
    select.innerHTML = state.symbols.map((symbol) => '<option value="' + escapeHtml(symbol) + '">' + escapeHtml(symbol) + '</option>').join("");
    if (state.symbols.includes(previous)) select.value = previous;
  });
}
async function loadOverview() {
  try {
    await loadSymbols();
    const symbol = $("#overview-symbol").value || null;
    const limit = Number($("#overview-limit").value || 120);
    const data = await api("/api/overview?limit=" + limit + (symbol ? "&symbol=" + encodeURIComponent(symbol) : ""));
    $("#kpi-symbols").textContent = fmt(data.symbols_count, 0);
    $("#kpi-spread").textContent = fmt(data.median_spread_bps);
    $("#kpi-depth").textContent = fmt(data.median_total_depth_value, 0);
    $("#kpi-imbalance").textContent = fmt(data.median_queue_imbalance, 3);
    $("#kpi-valid").textContent = data.valid_snapshot_rate == null ? "—" : (data.valid_snapshot_rate * 100).toFixed(1) + "%";
    $("#kpi-valid-count").textContent = fmt(data.valid_observation_count, 0) + " / " + fmt(data.observation_count, 0) + " snapshot";
    $("#overview-symbol-label").textContent = symbol || "Tất cả mã";
    renderLineChart("#spread-chart", data.series, "spread_bps", "#008A45", "Spread theo thời gian");
    renderDepthChart("#depth-chart", data.series);
    const rankings = await api("/api/rankings?limit=" + limit);
    const preview = (rankings.lowest_spread || []).slice(0, 5).map((row, index) => [
      '<strong>' + (index + 1) + '</strong>',
      '<strong>' + escapeHtml(row.symbol) + '</strong>',
      fmt(row.median_spread_bps) + " bps",
      fmt(row.snapshot_count, 0)
    ]);
    $("#overview-ranking").innerHTML = table(["#", "Mã", "Median spread", "Snapshots"], preview);
  } catch (error) {
    $("#spread-chart").innerHTML = '<div class="empty-state">Lỗi tải dữ liệu: ' + escapeHtml(error.message) + '</div>';
  }
}
async function loadDetail() {
  try {
    await loadSymbols();
    const symbol = $("#detail-symbol").value || state.symbols[0];
    if (!symbol) {
      $("#orderbook").innerHTML = '<div class="empty-state">Chưa có mã nào trong dữ liệu.</div>';
      return;
    }
    const data = await api("/api/symbol/" + encodeURIComponent(symbol) + "?limit=300");
    const latest = data.latest;
    $("#detail-spread").textContent = latest ? fmt(latest.spread_bps) : "—";
    $("#detail-bid-depth").textContent = latest ? fmt(latest.bid_depth_value, 0) : "—";
    $("#detail-ask-depth").textContent = latest ? fmt(latest.ask_depth_value, 0) : "—";
    $("#detail-imbalance").textContent = latest ? fmt(latest.queue_imbalance, 3) : "—";
    $("#detail-valid").textContent = data.valid_snapshot_rate == null ? "—" : (data.valid_snapshot_rate * 100).toFixed(1) + "%";
    $("#detail-snapshots").textContent = fmt(data.snapshot_count, 0) + " snapshot";
    renderLineChart("#detail-spread-chart", data.series, "spread_bps", "#008A45", "Spread của " + symbol);
    renderDepthChart("#detail-depth-chart", data.series);
    $("#book-time").textContent = latest ? fmtTime(latest.received_at_utc) : "Chưa có dữ liệu";
    $("#book-validity").textContent = latest ? latest.validation_status : "—";
    if (!latest) {
      $("#orderbook").innerHTML = '<div class="empty-state">Chưa có snapshot cho mã này.</div>';
      return;
    }
    const bookRows = [];
    for (let level = 1; level <= 3; level++) {
      bookRows.push([
        '<span class="side-bid">Bid ' + level + '</span>',
        fmt(latest["bid_price_" + level], 2), fmt(latest["bid_volume_" + level], 0),
        fmt(latest["ask_price_" + level], 2), fmt(latest["ask_volume_" + level], 0),
        '<span class="side-ask">Ask ' + level + '</span>'
      ]);
    }
    $("#orderbook").innerHTML = table(["", "Giá bid", "KL bid", "Giá ask", "KL ask", ""], bookRows);
  } catch (error) {
    $("#orderbook").innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>';
  }
}
async function loadRankings() {
  try {
    const limit = Number($("#ranking-limit").value || 120);
    const data = await api("/api/rankings?limit=" + limit);
    const spreadRows = (data.lowest_spread || []).map((row, index) => [
      '<strong>' + (index + 1) + '</strong>', '<strong>' + escapeHtml(row.symbol) + '</strong>',
      fmt(row.median_spread_bps) + " bps", fmt(row.snapshot_count, 0)
    ]);
    const depthRows = (data.highest_depth || []).map((row, index) => [
      '<strong>' + (index + 1) + '</strong>', '<strong>' + escapeHtml(row.symbol) + '</strong>',
      fmt(row.median_total_depth_value, 0), fmt(row.snapshot_count, 0)
    ]);
    $("#ranking-spread").innerHTML = table(["#", "Mã", "Median spread", "Snapshots"], spreadRows);
    $("#ranking-depth").innerHTML = table(["#", "Mã", "Median depth value", "Snapshots"], depthRows);
  } catch (error) {
    $("#ranking-spread").innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>';
  }
}
async function loadAlerts() {
  try {
    const data = await api("/api/alerts");
    const alerts = data.alerts || [];
    $("#alert-summary").innerHTML = '<span class="notice-icon">i</span><span>' +
      (alerts.length ? alerts.length + " cảnh báo trong dữ liệu đang chọn." :
        "Chưa có cảnh báo được lưu. Baseline cần tối thiểu " + data.baseline_min_observations + " quan sát hợp lệ.") + '</span>';
    $("#alerts-table").innerHTML = table(
      ["Mã", "Loại", "Mức", "Metric", "Ngưỡng", "Số mẫu", "Thời điểm", "Giải thích"],
      alerts.map((row) => [
        '<strong>' + escapeHtml(row.symbol) + '</strong>', escapeHtml(row.alert_type),
        escapeHtml(row.severity), fmt(row.metric_value), fmt(row.threshold_value),
        fmt(row.baseline_window, 0), fmtTime(row.triggered_at), escapeHtml(row.explanation)
      ]),
      "Chưa có cảnh báo cho nguồn dữ liệu này."
    );
  } catch (error) {
    $("#alerts-table").innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>';
  }
}
async function loadMethodology() {
  try {
    const data = await api("/api/methodology");
    const formulas = data.metrics.map((metric) =>
      '<article class="method-card"><h3>' + escapeHtml(metric.name) + '</h3><div class="formula">' +
      escapeHtml(metric.formula) + '</div></article>'
    ).join("");
    const limitations = data.limitations.map((item) => '<li>' + escapeHtml(item) + '</li>').join("");
    $("#method-content").innerHTML = formulas +
      '<article class="method-card"><h3>Nguồn dữ liệu</h3><p>' + escapeHtml(data.source) +
      '</p><p>Mapping sẽ được xác nhận bằng payload thật trước khi bật phân tích LIVE.</p></article>' +
      '<article class="method-card"><h3>Giới hạn</h3><ul>' + limitations + '</ul></article>';
    if (state.status) renderCollectorStatus(state.status);
  } catch (error) {
    $("#method-content").innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>';
  }
}
async function collectorAction(path, confirmMessage) {
  if (confirmMessage && !window.confirm(confirmMessage)) return;
  try {
    const data = await api(path, { method: "POST" });
    await refreshStatus();
    if (state.route === "methodology") renderCollectorStatus(state.status);
    if (path.endsWith("/start")) {
      $("#collector-status").insertAdjacentHTML("afterend", '<p class="small-note">' + escapeHtml(data.message || "Đang kết nối…") + '</p>');
    }
  } catch (error) {
    window.alert("Không thực hiện được: " + error.message);
  }
}

$$(".nav-item").forEach((button) => button.addEventListener("click", () => setRoute(button.dataset.route)));
$$("[data-route]").forEach((button) => {
  if (!button.classList.contains("nav-item")) button.addEventListener("click", () => setRoute(button.dataset.route));
});
$("#welcome-next").addEventListener("click", () => {
  const stage = Number($("#welcome-next").dataset.stage || 0);
  if (stage >= 2) enterApp("overview");
  else onboardingStage(stage + 1);
});
$("#welcome-skip").addEventListener("click", () => enterApp("overview"));
$("#overview-symbol").addEventListener("change", loadOverview);
$("#overview-limit").addEventListener("change", loadOverview);
$("#detail-symbol").addEventListener("change", loadDetail);
$("#ranking-limit").addEventListener("change", loadRankings);
$("#start-live").addEventListener("click", () => collectorAction(
  "/api/collector/start",
  "Bắt đầu LIVE sẽ gửi thông tin đăng nhập từ .env tới API token VPBankS và access token tới WebSocket NEO đã cấu hình. Tiếp tục?"
));
$("#stop-live").addEventListener("click", () => collectorAction("/api/collector/stop"));
$("#use-demo").addEventListener("click", () => collectorAction("/api/collector/demo"));

if (localStorage.getItem("sli-onboarding-done") === "1") enterApp("overview");
else onboardingStage(0);
refreshStatus();
window.setInterval(refreshStatus, 5000);
window.setInterval(() => {
  if (state.route === "overview") loadOverview();
  if (state.route === "detail") loadDetail();
}, 10000);