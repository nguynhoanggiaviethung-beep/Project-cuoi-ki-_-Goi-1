const state = { route: "overview", symbols: [], source: "demo", status: null };
const viStatus = (value) => ({connected:"Đã kết nối (connected)",connecting:"Đang kết nối (connecting)",reconnecting:"Đang kết nối lại (reconnecting)",disconnected:"Đã ngắt kết nối (disconnected)",error:"Có lỗi (error)",stopped:"Đã dừng (stopped)",demo:"Mô phỏng (DEMO)",live:"Trực tiếp (LIVE)",valid:"Hợp lệ (valid)",invalid:"Không hợp lệ (invalid)",full:"Đủ khối lượng (full)",partial:"Khớp một phần (partial)",resolved:"Đã xử lý (resolved)",active:"Đang có hiệu lực (active)",warning:"Cảnh báo (warning)",medium:"Trung bình (medium)",high:"Cao (high)",low:"Thấp (low)"}[String(value ?? "").toLowerCase()] || value || "—");
const viAlert = (value) => ({spread_widening:"Chênh lệch giá mua – bán nới rộng (spread widening)",depth_drop:"Độ sâu sổ lệnh suy giảm (depth drop)",combined_liquidity_stress:"Căng thẳng thanh khoản kết hợp (combined liquidity stress)",strong_queue_imbalance:"Mất cân bằng khối lượng chờ mạnh (strong queue imbalance)",stale_data:"Dữ liệu quá hạn (stale data)"}[value] || value || "—");
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
function attachChartTooltip(node, svg, points, viewWidth) {
  if (!node || !svg || !points || !points.length) return;
  node.style.position = "relative";
  let tip = node.querySelector(".chart-tooltip");
  if (!tip) { tip = document.createElement("div"); tip.className = "chart-tooltip"; tip.setAttribute("role", "status"); node.appendChild(tip); }
  const hide = () => { tip.style.display = "none"; };
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const pointerX = (event.clientX - rect.left) / rect.width * viewWidth;
    let closest = points[0], distance = Math.abs(points[0].x - pointerX);
    for (const point of points) { const nextDistance = Math.abs(point.x - pointerX); if (nextDistance < distance) { closest = point; distance = nextDistance; } }
    const box = node.getBoundingClientRect();
    tip.textContent = closest.text;
    tip.style.display = "block";
    tip.style.left = Math.max(6, Math.min(event.clientX - box.left + 12, box.width - 270)) + "px";
    tip.style.top = Math.max(4, event.clientY - box.top - 42) + "px";
  });
  svg.addEventListener("pointerleave", hide);
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
window.attachChartTooltip = attachChartTooltip;
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
    "Theo dõi chênh lệch giá mua – bán (spread) và độ sâu sổ lệnh (depth) cổ phiếu trong một màn hình rõ ràng, có giải thích và nguồn dữ liệu minh bạch.",
    "SLI thu thập ảnh chụp dữ liệu (snapshot) bên mua/bán (bid/ask), chuẩn hóa dữ liệu rồi tính các chỉ tiêu để bạn quan sát thay đổi theo thời gian.",
    "Kiểm tra trạng thái dữ liệu, chọn mã và khoảng thời gian, đọc chỉ số chính (KPI) rồi xem biểu đồ hoặc cảnh báo."
  ];
  const nextLabels = ["Xem giới thiệu", "Xem hướng dẫn", "Vào bảng tổng quan"];
  $("#welcome-step-label").textContent = labels[stage];
  $("#welcome-title").textContent = titles[stage];
  $("#welcome-text").textContent = texts[stage];
  $("#welcome-next").textContent = nextLabels[stage] + " →";
  $$(".step-dots i").forEach((dot, index) => dot.classList.toggle("active", index === stage));
  $("#welcome-extra").innerHTML = stage === 0
    ? '<span class="inline-note">Bản xem trước (preview) dùng dữ liệu tổng hợp và luôn được đánh dấu mô phỏng (DEMO).</span>'
    : stage === 1
      ? '<div class="onboarding-pills"><span>Chênh lệch giá (spread)</span><span>Độ sâu ba mức giá đầu (top 3 depth)</span><span>Mất cân bằng khối lượng chờ (queue imbalance)</span></div>'
      : '<ol class="onboarding-list"><li>Xem nhãn mô phỏng/trực tiếp (DEMO/LIVE) và thời điểm cập nhật.</li><li>Chọn mã, đọc chênh lệch giá (spread) và độ sâu sổ lệnh (depth).</li><li>Kiểm tra mốc tham chiếu (baseline) trước khi diễn giải cảnh báo.</li></ol>';
  $("#welcome-skip").textContent = stage === 2 ? "Bỏ qua, vào bảng tổng quan" : "Bỏ qua phần giới thiệu và hướng dẫn";
  $("#welcome-next").dataset.stage = String(stage);
}
function renderLineChart(target, series, key, color, label) {
  const node = document.querySelector(target); if (!node) return;
  const rows=(series||[]).filter(row=>row[key]!==null&&row[key]!==undefined&&Number.isFinite(Number(row[key]))),data=rows.map(row=>Number(row[key]));
  if(data.length<2){node.innerHTML='<div class="empty-state">Chưa đủ dữ liệu để vẽ biểu đồ.</div>';return;}
  const width=800,height=260,left=72,right=14,top=18,bottom=44;let min=Math.min(...data),max=Math.max(...data);if(min===max){const pad=Math.abs(max)*.05||1;min-=pad;max+=pad;}const span=max-min;
  const points=data.map((value,index)=>[left+index/(data.length-1)*(width-left-right),top+(max-value)/span*(height-top-bottom)]);
  const path=points.map((point,index)=>(index?'L':'M')+point[0].toFixed(1)+' '+point[1].toFixed(1)).join(' '),grid=[];
  for(let i=0;i<=4;i++){const y=top+i*(height-top-bottom)/4,v=max-i*span/4;grid.push('<line x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'" stroke="#e2ebe4"/><text x="'+(left-9)+'" y="'+(y+4)+'" text-anchor="end" fill="#71857a" font-size="11">'+fmt(v,key.includes('imbalance')?3:2)+'</text>');}
  const unit=key.includes('spread')?' điểm cơ bản (bps)':key.includes('depth')?' giá trị':'',hover=points.map((point,index)=>{const row=rows[index],stamp=row.minute||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'Thời điểm không có trong dữ liệu';return {x:point[0],text:stamp+' · '+label+': '+fmt(data[index],key.includes('imbalance')?3:2)+unit};});
  const xTicks=[0,Math.floor((rows.length-1)/2),rows.length-1].map((index,pos)=>{const x=left+index/Math.max(rows.length-1,1)*(width-left-right),row=rows[index],raw=row.minute||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'—',d=new Date(raw),stamp=Number.isNaN(d.valueOf())?String(raw).slice(0,8):d.toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit'}),anchor=pos===0?'start':pos===2?'end':'middle';return '<line x1="'+x+'" y1="'+(height-bottom)+'" x2="'+x+'" y2="'+(height-bottom+4)+'" stroke="#9caf9f"/><text x="'+x+'" y="'+(height-9)+'" text-anchor="'+anchor+'" fill="#71857a" font-size="10">'+escapeHtml(stamp)+'</text>';}).join('');
  const marks=points.map((point,index)=>'<circle class="chart-hover-point" cx="'+point[0]+'" cy="'+point[1]+'" r="8" fill="transparent" tabindex="0"><title>'+escapeHtml(hover[index].text)+'</title></circle>').join('');
  node.innerHTML='<svg viewBox="0 0 '+width+' '+height+'" role="img" aria-label="'+escapeHtml(label)+'">'+grid.join('')+'<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'+marks+xTicks+'<text x="'+left+'" y="12" fill="#587264" font-size="10">'+escapeHtml(label+unit)+'</text></svg>';
  attachChartTooltip(node,node.querySelector('svg'),hover,width);
}
function renderDepthChart(target, series) {
  const node=document.querySelector(target);if(!node)return;
  const rows=(series||[]).filter(row=>row.bid_depth_value!==null&&row.bid_depth_value!==undefined&&row.ask_depth_value!==null&&row.ask_depth_value!==undefined&&Number.isFinite(Number(row.bid_depth_value))&&Number.isFinite(Number(row.ask_depth_value))),bids=rows.map(row=>Number(row.bid_depth_value)),asks=rows.map(row=>Number(row.ask_depth_value));
  if(bids.length<2||asks.length<2){node.innerHTML='<div class="empty-state">Chưa đủ dữ liệu độ sâu sổ lệnh (depth) hai phía.</div>';return;}
  const width=800,height=240,left=72,right=14,top=16,bottom=40;let min=Math.min(...bids,...asks),max=Math.max(...bids,...asks);if(min===max){const pad=Math.abs(max)*.05||1;min-=pad;max+=pad;}const span=max-min;
  const coords=values=>values.map((value,index)=>({value,x:left+index/Math.max(values.length-1,1)*(width-left-right),y:top+(max-value)/span*(height-top-bottom)})),bp=coords(bids),ap=coords(asks),pathFor=points=>points.map((p,i)=>(i?'L':'M')+p.x.toFixed(1)+' '+p.y.toFixed(1)).join(' '),grid=[];
  for(let i=0;i<=4;i++){const y=top+i*(height-top-bottom)/4,v=max-i*span/4;grid.push('<line x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'" stroke="#e2ebe4"/><text x="'+(left-9)+'" y="'+(y+4)+'" text-anchor="end" fill="#71857a" font-size="10">'+fmt(v,0)+'</text>');}
  const stampAt=i=>{const row=rows[i],raw=row.minute||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'—',d=new Date(raw);return Number.isNaN(d.valueOf())?String(raw).slice(0,8):d.toLocaleTimeString('vi-VN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});};
  const xTicks=[0,Math.floor((rows.length-1)/2),rows.length-1].map((index,pos)=>{const x=bp[index].x,anchor=pos===0?'start':pos===2?'end':'middle';return '<line x1="'+x+'" y1="'+(height-bottom)+'" x2="'+x+'" y2="'+(height-bottom+4)+'" stroke="#9caf9f"/><text x="'+x+'" y="'+(height-8)+'" text-anchor="'+anchor+'" fill="#71857a" font-size="10">'+escapeHtml(stampAt(index))+'</text>';}).join('');
  const marks=(points,side)=>points.map((p,i)=>'<circle class="chart-hover-point" cx="'+p.x+'" cy="'+p.y+'" r="8" fill="transparent" tabindex="0"><title>'+escapeHtml(stampAt(i)+' · '+(side==='Bid'?'Bên mua (bid)':'Bên bán (ask)')+' · Độ sâu sổ lệnh (depth): '+fmt(p.value,0)+' giá trị (giá × khối lượng)')+'</title></circle>').join('');
  const hover=rows.map((row,i)=>({x:bp[i].x,text:stampAt(i)+' · Bên mua (bid): '+fmt(bids[i],0)+' · Bên bán (ask): '+fmt(asks[i],0)+' · giá trị độ sâu (depth), tính bằng giá × khối lượng'}));
  node.innerHTML='<svg viewBox="0 0 '+width+' '+height+'" role="img" aria-label="Độ sâu sổ lệnh bên mua và bên bán theo thời gian (bid và ask depth)">'+grid.join('')+'<path d="'+pathFor(bp)+'" fill="none" stroke="#006B3F" stroke-width="3"/><path d="'+pathFor(ap)+'" fill="none" stroke="#E2231A" stroke-width="3"/>'+marks(bp,'Bid')+marks(ap,'Ask')+xTicks+'<text x="'+left+'" y="11" fill="#587264" font-size="10">Giá trị độ sâu (depth) · giá × khối lượng</text></svg>';
  attachChartTooltip(node,node.querySelector('svg'),hover,width);
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
    const live = ["live", "ssi"].includes(data.source);
    const liveReady = live && data.connection_status === "connected" && Boolean(data.last_data_at);
    const historyReady = live && Number(data.historical_prices_rows || 0) > 0;
    const liveLabel = "SSI FASTCONNECT";
    $("#mode-badge").textContent = live ? liveReady ? liveLabel + " · LIVE" : historyReady ? liveLabel + " · GIÁ LỊCH SỬ" : liveLabel + " · CHƯA CÓ DATA" : "MÔ PHỎNG (DEMO)";
    $("#mode-badge").className = "badge " + (liveReady ? "badge-live" : "badge-demo");
    $("#connection-label").textContent = viStatus(data.connection_status);
    $("#connection-dot").className = "status-dot " + (
      data.connection_status === "connected" ? "connected" :
      ["error", "disconnected"].includes(data.connection_status) ? "error" : ""
    );
    $("#last-update").textContent = fmtTime(data.last_data_at);
    const notice = $("#global-notice");
    notice.className = "notice " + (live && (data.connection_status === "error" || data.connection_status === "disconnected") ? "notice-error" : liveReady ? "notice-live" : "notice-demo");
    let message = live
      ? "TRỰC TIẾP (LIVE) · Nguồn SSI FastConnect. Trạng thái kết nối: " + viStatus(data.connection_status) + "."
      : "MÔ PHỎNG (DEMO) · Dữ liệu tổng hợp để xem trước, không phải dữ liệu thị trường thật.";
    if (live && !data.last_data_at) message += historyReady ? " Chưa có quote sổ lệnh realtime; đang hiển thị giá OHLC lịch sử SSI." : " Chưa nhận được snapshot nào nên chưa có dữ liệu live để hiển thị.";
    if (data.is_stale) message += " Dữ liệu đã cũ; không xem là trực tiếp (LIVE).";
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
    ["Nguồn dữ liệu", data.provider || (data.source === "ssi" ? "SSI FastConnect · quote WebSocket" : "Mô phỏng (DEMO)")],
    ["Trạng thái", viStatus(data.connection_status)],
    ["Đăng ký quote SSI", data.subscription_status === "confirmed" ? "Đã xác nhận · " + (data.subscribed_boards || []).join(", ") : ({pending:"Đang chờ đăng ký",subscribing:"Đang gửi đăng ký",partial:"Đã nhận một phần",unconfirmed:"Chưa xác nhận",unverified:"Chưa kiểm tra"}[data.subscription_status] || data.subscription_status || "Chưa có")],
    ["ACK đăng ký", fmt(data.subscription_ack_count, 0)],
    ["Thông điệp nhận được (message)", fmt(data.messages_received, 0)],
    ["Ảnh chụp dữ liệu hợp lệ (snapshot)", fmt(data.rows_valid, 0)],
    ["Lỗi / trùng", fmt(data.rows_invalid, 0) + " / " + fmt(data.duplicate_messages, 0)],
    ["Kết nối lại (reconnect)", fmt(data.reconnect_count, 0)],
    ["Thông điệp gần nhất (message)", fmtTime(data.last_data_at)]
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
    const allOption = selector === "#overview-symbol" ? '<option value="__all__">Tất cả mã</option>' : "";
    select.innerHTML = allOption + state.symbols.map((symbol) => '<option value="' + escapeHtml(symbol) + '">' + escapeHtml(symbol) + '</option>').join("");
    if (previous === "__all__" && selector === "#overview-symbol") select.value = "__all__";
    else if (state.symbols.includes(previous)) select.value = previous;
    else if (selector === "#overview-symbol") select.value = "__all__";
  });
}
async function loadOverview() {
  try {
    await loadSymbols();
    const selected = $("#overview-symbol").value;
    const symbol = selected && selected !== "__all__" ? selected : null;
    const limit = Number($("#overview-limit").value || 120);
    const data = await api("/api/overview?limit=" + limit + (symbol ? "&symbol=" + encodeURIComponent(symbol) : ""));
    $("#kpi-symbols").textContent = fmt(data.symbols_count, 0);
    $("#kpi-spread").textContent = fmt(data.median_spread_bps);
    $("#kpi-depth").textContent = fmt(data.median_total_depth_value, 0);
    $("#kpi-imbalance").textContent = fmt(data.median_queue_imbalance, 3);
    $("#kpi-valid").textContent = data.valid_snapshot_rate == null ? "—" : (data.valid_snapshot_rate * 100).toFixed(1) + "%";
    $("#kpi-valid-count").textContent = fmt(data.valid_observation_count, 0) + " / " + fmt(data.observation_count, 0) + " ảnh chụp dữ liệu (snapshot)";
    $("#overview-symbol-label").textContent = symbol || "Tất cả mã";
    renderLineChart("#spread-chart", data.series, "spread_bps", "#008A45", "Chênh lệch giá mua – bán (spread) theo thời gian");
      renderDepthChart("#depth-chart", data.series);
    const rankings = await api("/api/rankings?limit=" + limit);
    const preview = (rankings.lowest_spread || []).slice(0, 5).map((row, index) => [
      '<strong>' + (index + 1) + '</strong>',
      '<strong>' + escapeHtml(row.symbol) + '</strong>',
      fmt(row.median_spread_bps) + " điểm cơ bản (bps)",
      fmt(row.snapshot_count, 0)
    ]);
    $("#overview-ranking").innerHTML = table(["Hạng", "Mã", "Trung vị chênh lệch giá (spread)", "Số ảnh chụp dữ liệu (snapshots)"], preview);
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
    $("#detail-snapshots").textContent = fmt(data.snapshot_count, 0) + " ảnh chụp dữ liệu (snapshot)";
    renderLineChart("#detail-spread-chart", data.series, "spread_bps", "Chênh lệch giá (spread) của " + symbol);
    renderDepthChart("#detail-depth-chart", data.series);
    $("#book-time").textContent = latest ? fmtTime(latest.received_at_utc) : "Chưa có dữ liệu";
      $("#book-validity").textContent = latest ? viStatus(latest.validation_status) : "—";
    if (!latest) {
      $("#orderbook").innerHTML = '<div class="empty-state">Chưa có ảnh chụp dữ liệu (snapshot) cho mã này.</div>';
      return;
    }
    const bookRows = [];
    for (let level = 1; level <= 3; level++) {
      bookRows.push([
        '<span class="side-bid">Giá mua (bid) ' + level + '</span>',
        fmt(latest["bid_price_" + level], 2), fmt(latest["bid_volume_" + level], 0),
        fmt(latest["ask_price_" + level], 2), fmt(latest["ask_volume_" + level], 0),
        '<span class="side-ask">Giá bán (ask) ' + level + '</span>'
      ]);
    }
    $("#orderbook").innerHTML = table(["", "Giá mua (bid)", "Khối lượng mua (bid)", "Giá bán (ask)", "Khối lượng bán (ask)", ""], bookRows);
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
      fmt(row.median_spread_bps) + " điểm cơ bản (bps)", fmt(row.snapshot_count, 0)
    ]);
    const depthRows = (data.highest_depth || []).map((row, index) => [
      '<strong>' + (index + 1) + '</strong>', '<strong>' + escapeHtml(row.symbol) + '</strong>',
      fmt(row.median_total_depth_value, 0), fmt(row.snapshot_count, 0)
    ]);
    $("#ranking-spread").innerHTML = table(["Hạng", "Mã", "Trung vị chênh lệch giá (spread)", "Số ảnh chụp dữ liệu (snapshots)"], spreadRows);
    $("#ranking-depth").innerHTML = table(["Hạng", "Mã", "Trung vị giá trị độ sâu (depth)", "Số ảnh chụp dữ liệu (snapshots)"], depthRows);
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
        "Chưa có cảnh báo được lưu. Mốc tham chiếu (baseline) cần tối thiểu " + data.baseline_min_observations + " quan sát hợp lệ.") + '</span>';
    $("#alerts-table").innerHTML = table(
      ["Mã", "Loại cảnh báo", "Mức độ", "Chỉ số (metric)", "Ngưỡng", "Số mẫu", "Thời điểm", "Giải thích"],
      alerts.map((row) => [
        '<strong>' + escapeHtml(row.symbol) + '</strong>', escapeHtml(viAlert(row.alert_type)),
        escapeHtml(viStatus(row.severity)), fmt(row.metric_value), fmt(row.threshold_value),
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
      $("#collector-status").insertAdjacentHTML("afterend", '<p class="small-note">' + escapeHtml(data.message || "Đang mở WebSocket SSI…") + '</p>');
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
$("#overview-symbol").addEventListener("change", () => (window.loadOverview || loadOverview)());
$("#overview-limit").addEventListener("change", () => (window.loadOverview || loadOverview)());
$("#detail-symbol").addEventListener("change", () => (window.loadDetail || loadDetail)());
$("#ranking-limit").addEventListener("change", () => (window.loadRankings || loadRankings)());
$("#start-live").addEventListener("click", () => collectorAction(
  "/api/collector/start",
  "Khởi động WebSocket SSI FastConnect bằng API Key và API Secret trong .env?"
));
$("#stop-live").addEventListener("click", () => collectorAction("/api/collector/stop"));


if (localStorage.getItem("sli-onboarding-done") === "1") enterApp("overview");
else onboardingStage(0);
refreshStatus();
window.setInterval(refreshStatus, 5000);
window.setInterval(() => {
  if (state.route === "overview") (window.loadOverview || loadOverview)();
  if (state.route === "detail") (window.loadDetail || loadDetail)();
}, 10000);
