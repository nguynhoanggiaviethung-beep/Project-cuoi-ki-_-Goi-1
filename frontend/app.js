const state = { route: "overview", symbols: [], source: "ssi", status: null };
let connectionStatusTimer = null;
window.MarketClock = (() => {
  let meta=null,timer=null,lastAsOf=null,lastSyncState=null;const listeners=new Set();
  const notify=()=>listeners.forEach(fn=>{try{fn(meta);}catch(_){}});
  const schedule=()=>{
    clearTimeout(timer);if(document.hidden||!meta)return;
    if(meta.next_change_at){const delay=Math.max(250,(Date.parse(meta.next_change_at)-Date.now())/(meta.clock_speed||1));timer=setTimeout(()=>refreshStatus(),delay);}
  };
  const update=value=>{
    if(!value)return;const prior=meta;meta=value;
    const banner=document.querySelector('#market-clock-simulation');
    if(value.clock_simulated){if(!banner){const node=document.createElement('div');node.id='market-clock-simulation';node.textContent='ĐANG GIẢ LẬP GIỜ';document.body.appendChild(node);}}
    else banner?.remove();
    const syncState=JSON.stringify(value.session_sync||null);
    const clockChanged=prior&&(prior.state!==value.state||prior.phase!==value.phase||prior.session_date!==value.session_date||JSON.stringify(prior.exchange_states)!==JSON.stringify(value.exchange_states));
    const outsideDataChanged=prior&&((lastAsOf!==null&&lastAsOf!==value.data_as_of)||lastSyncState!==syncState)&&value.state!=="IN_SESSION";
    if(clockChanged||outsideDataChanged){
      document.dispatchEvent(new Event('sli-market-data-changed'));
      if(!document.hidden)notify();
    }
    lastAsOf=value.data_as_of;lastSyncState=syncState;schedule();
  };
  const subscribe=fn=>{listeners.add(fn);return()=>listeners.delete(fn);};
  const get=()=>meta;
  const visibilityChanged=()=>{if(document.hidden)clearTimeout(timer);else refreshStatus();};
  return {update,subscribe,get,visibilityChanged};
})();
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
function attachChartTooltip(node, svg, points, viewWidth, viewHeight = null) {
  if (!node || !svg || !points || !points.length) return;
  let tip = node._chartTooltip;
  if (!tip) { tip = document.createElement("div"); tip.className = "chart-tooltip"; tip.setAttribute("role", "status"); document.body.appendChild(tip); node._chartTooltip = tip; }
  const hide = () => { tip.style.display = "none"; };
  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const pointerX = (event.clientX - rect.left) / rect.width * viewWidth;
    const pointerY = viewHeight ? (event.clientY - rect.top) / rect.height * viewHeight : null;
    const distanceTo = (point) => {
      const dx = point.x - pointerX;
      const dy = pointerY !== null && Number.isFinite(point.y) ? point.y - pointerY : 0;
      return dx * dx + dy * dy;
    };
    let closest = points.find((point) => point.visible !== false) || points[0];
    let distance = distanceTo(closest);
    for (const point of points) { if (point.visible === false) continue; const nextDistance = distanceTo(point); if (nextDistance < distance) { closest = point; distance = nextDistance; } }
    tip.textContent = closest.text;
    tip.style.display = "block";
    const tipWidth = tip.offsetWidth, tipHeight = tip.offsetHeight, gap = 12, edge = 8;
    const left = Math.max(edge, Math.min(event.clientX + gap, window.innerWidth - tipWidth - edge));
    let top = event.clientY - tipHeight - gap;
    if (top < edge) top = event.clientY + gap;
    if (top + tipHeight > window.innerHeight - edge) top = window.innerHeight - tipHeight - edge;
    tip.style.left = left + "px";
    tip.style.top = Math.max(edge, top) + "px";
  });
  svg.addEventListener("pointerleave", hide);
}
function fmtTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? escapeHtml(value) :
    date.toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", second: "2-digit", day: "2-digit", month: "2-digit" });
}
function fmtDateTime(value) {
  if (!value) return "chưa có bản ghi";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? escapeHtml(value) :
    date.toLocaleString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function fmtDate(value) {
  if(!value)return "chưa có dữ liệu";
  const day=String(value).replaceAll("/","-").slice(0,10);
  const date=new Date(day+"T00:00:00+07:00");
  return Number.isNaN(date.valueOf())?escapeHtml(value):date.toLocaleDateString("vi-VN",{timeZone:"Asia/Ho_Chi_Minh",day:"2-digit",month:"2-digit",year:"numeric"});
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
  window.sliRoute = route;
  $$(".view").forEach((view) => view.classList.toggle("hidden", view.id !== "view-" + route));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.route === route));
  if (route === "overview") (window.loadOverview || loadOverview)();
  if (route === "watchlist") (window.loadWatchlist || (()=>{}))();
  if (route === "detail") (window.loadDetail || loadDetail)();
  if (route === "rankings") (window.loadRankings || loadRankings)();
  if (route === "alerts") (window.loadAlerts || loadAlerts)();
  if (route === "methodology") (window.loadMethodology || loadMethodology)();
  window.scrollTo({ top: 0, behavior: "smooth" });
}
window.setRoute = setRoute;
function enterApp(route = "overview") {
  localStorage.setItem("sli-onboarding-done", "1");
  $("#onboarding").classList.add("hidden");
  $("#application").classList.remove("hidden");
  setRoute(route);
  refreshStatus().then(showConnectionDialog);
}
function showConnectionDialog() {
  const dialog = $("#connection-dialog");
  if (dialog && !dialog.open) dialog.showModal();
  updateConnectionDialog();
}
function feedStateText(data) {
  const age = data.quote_age_seconds == null ? "" : " · trễ " + fmt(data.quote_age_seconds, 0) + " giây";
  if (data.feed_state === "live") return "Đang kết nối và nhận dữ liệu trực tiếp (LIVE)" + age;
  if (data.feed_state === "connecting") return "Đang kết nối SSI FastConnect";
  if (data.feed_state === "delayed") return "Đang kết nối, dữ liệu đến chậm" + age;
  if (data.feed_state === "stale") return "Tiến trình còn chạy nhưng dữ liệu đã quá hạn" + age;
  if (data.feed_state === "outside_session") {
    const latest=data.last_quote_received_at||data.last_persisted_at||data.last_data_at;
    const saved=latest?" Bản ghi SSI gần nhất lúc "+fmtDateTime(latest)+"; dữ liệu đang giữ là dữ liệu đã lưu, không phải báo giá trực tiếp mới.":" Chưa có bản ghi SSI được lưu trong phiên hiện tại.";
    if ((data.meta||data).state === "LUNCH_BREAK" || data.market_phase === "BREAK") return "Thị trường đang nghỉ trưa; WebSocket SSI vẫn kết nối và chờ báo giá khi phiên chiều mở lại."+saved;
    return "Thị trường ngoài giờ giao dịch liên tục; WebSocket SSI vẫn kết nối và chờ báo giá khi phiên mở lại."+saved;
  }
  if (data.feed_state === "awaiting_quote") return "Đã kết nối, đang chờ báo giá đầu tiên";
  if (data.feed_state === "error") return "Kết nối dữ liệu gặp lỗi";
  return "Dữ liệu đã ngắt kết nối. Vui lòng khởi động kết nối.";
}
function updateConnectionDialog(data = state.status) {
  if (!data) return;
  const message = $("#connection-dialog-status");
  const button = $("#connect-now");
  const disconnect = $("#disconnect-data");
  const skipButton = $("#connection-dialog-skip");
  const title = $("#connection-dialog-title");
  const copy = $("#connection-dialog-copy");
  if (!message || !button) return;
  message.textContent = feedStateText(data);
  const active = data.worker_status === "running";
  button.disabled = active;
  button.textContent = active ? "Tiến trình đang hoạt động" : "Kết nối dữ liệu";
  if (disconnect) disconnect.hidden = !active;
  const connectionStatus = String(data.connection_status || "").toLowerCase();
  if (title) title.textContent = !active ? "Kết nối SSI FastConnect?" : connectionStatus === "connected"
    ? "SSI đã kết nối" : ["error", "disconnected"].includes(connectionStatus)
      ? "SSI mất kết nối" : "SSI đang kết nối";
  if (copy) copy.textContent = active
    ? connectionStatus === "connected"
      ? "WebSocket SSI đã kết nối. Ngoài giờ giao dịch, hệ thống giữ dữ liệu gần nhất và chờ quote khi phiên mở lại."
      : "Tiến trình SSI đang hoạt động; hệ thống đang cập nhật trạng thái kết nối."
    : "Dữ liệu báo giá (quote) và khớp lệnh sẽ được thu thập bằng một tiến trình riêng. Kết nối chỉ bắt đầu khi bạn chọn nút bên dưới.";
  if (skipButton) skipButton.textContent = active ? "Vào trang web" : "Tiếp tục không kết nối";
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
  const width=800,height=260,left=86,right=14,top=20,bottom=46;let min=Math.min(...data),max=Math.max(...data);if(min===max){const pad=Math.abs(max)*.05||1;min-=pad;max+=pad;}const raw=(max-min)/4,power=10**Math.floor(Math.log10(raw)),ratio=raw/power,step=(ratio<=1?1:ratio<=2?2:ratio<=2.5?2.5:ratio<=5?5:10)*power,axisMin=Math.floor(min/step)*step,axisMax=Math.ceil(max/step)*step,span=axisMax-axisMin||1,ticks=[];for(let value=axisMin;value<=axisMax+step*.0001;value+=step)ticks.push(Number(value.toPrecision(12)));
  const points=data.map((value,index)=>[left+index/(data.length-1)*(width-left-right),top+(axisMax-value)/span*(height-top-bottom)]);
  const path=points.map((point,index)=>(index?'L':'M')+point[0].toFixed(1)+' '+point[1].toFixed(1)).join(' '),grid=[];
  for(const v of ticks){const y=top+(axisMax-v)/span*(height-top-bottom),labelValue=key.includes('depth')&&Math.abs(v)>=1e9?fmt(v/1e9,1)+' tỷ':fmt(v,key.includes('imbalance')?2:key.includes('spread')?1:0);grid.push('<line x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'" stroke="#e2ebe4"/><text x="'+(left-9)+'" y="'+(y+4)+'" text-anchor="end" fill="#526b5d" font-size="11">'+labelValue+'</text>');}
  const unit=key.includes('spread')?' điểm cơ bản (bps)':key.includes('depth')?' giá trị':'',hover=points.map((point,index)=>{const row=rows[index],stamp=row.minute||row.source_ts_vn||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'Thời điểm không có trong dữ liệu';return {x:point[0],text:stamp+' · '+label+': '+fmt(data[index],key.includes('imbalance')?3:2)+unit};});
  const xTicks=Array.from({length:5},(_,pos)=>{const index=Math.round(pos*(rows.length-1)/4),x=left+pos/4*(width-left-right),row=rows[index],raw=row.trading_date||row.minute||row.source_ts_vn||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'—',d=new Date(String(raw).replaceAll('/','-')+(String(raw).length===10?'T00:00:00+07:00':'')),stamp=Number.isNaN(d.valueOf())?String(raw).slice(0,10):row.trading_date?d.toLocaleDateString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit'}):d.toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit'}),anchor=pos===0?'start':pos===4?'end':'middle';return '<line x1="'+x+'" y1="'+(height-bottom)+'" x2="'+x+'" y2="'+(height-bottom+4)+'" stroke="#9caf9f"/><text x="'+x+'" y="'+(height-9)+'" text-anchor="'+anchor+'" fill="#526b5d" font-size="10">'+escapeHtml(stamp)+'</text>';}).join('');
  const marks=points.map((point,index)=>'<circle class="chart-hover-point" cx="'+point[0]+'" cy="'+point[1]+'" r="8" fill="transparent" tabindex="0"><title>'+escapeHtml(hover[index].text)+'</title></circle>').join('');
  node.innerHTML='<svg viewBox="0 0 '+width+' '+height+'" role="img" aria-label="'+escapeHtml(label)+'">'+grid.join('')+'<path d="'+path+'" fill="none" stroke="'+color+'" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'+marks+xTicks+'<text x="'+left+'" y="12" fill="#587264" font-size="10">'+escapeHtml(label+unit)+'</text></svg>';
  attachChartTooltip(node,node.querySelector('svg'),hover,width);
}
function renderDepthChart(target, series) {
  const node=document.querySelector(target);if(!node)return;
  const rows=(series||[]).filter(row=>row.bid_depth_value!==null&&row.bid_depth_value!==undefined&&row.ask_depth_value!==null&&row.ask_depth_value!==undefined&&Number.isFinite(Number(row.bid_depth_value))&&Number.isFinite(Number(row.ask_depth_value))),bids=rows.map(row=>Number(row.bid_depth_value)),asks=rows.map(row=>Number(row.ask_depth_value));
  if(bids.length<2||asks.length<2){node.innerHTML='<div class="empty-state">Chưa đủ dữ liệu độ sâu sổ lệnh (depth) hai phía.</div>';return;}
  const width=800,height=240,left=86,right=14,top=18,bottom=44;let min=Math.min(...bids,...asks),max=Math.max(...bids,...asks);if(min===max){const pad=Math.abs(max)*.05||1;min-=pad;max+=pad;}const raw=(max-min)/4,power=10**Math.floor(Math.log10(raw)),ratio=raw/power,step=(ratio<=1?1:ratio<=2?2:ratio<=2.5?2.5:ratio<=5?5:10)*power,axisMin=Math.floor(min/step)*step,axisMax=Math.ceil(max/step)*step,span=axisMax-axisMin||1,ticks=[];for(let value=axisMin;value<=axisMax+step*.0001;value+=step)ticks.push(Number(value.toPrecision(12)));
  const coords=values=>values.map((value,index)=>({value,x:left+index/Math.max(values.length-1,1)*(width-left-right),y:top+(axisMax-value)/span*(height-top-bottom)})),bp=coords(bids),ap=coords(asks),pathFor=points=>points.map((p,i)=>(i?'L':'M')+p.x.toFixed(1)+' '+p.y.toFixed(1)).join(' '),grid=[];
  for(const v of ticks){const y=top+(axisMax-v)/span*(height-top-bottom),labelValue=Math.abs(v)>=1e9?fmt(v/1e9,1)+' tỷ':fmt(v,0);grid.push('<line x1="'+left+'" y1="'+y+'" x2="'+(width-right)+'" y2="'+y+'" stroke="#e2ebe4"/><text x="'+(left-9)+'" y="'+(y+4)+'" text-anchor="end" fill="#526b5d" font-size="10">'+labelValue+'</text>');}
  const stampAt=i=>{const row=rows[i],raw=row.minute||row.source_ts_vn||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'—',d=new Date(raw);return Number.isNaN(d.valueOf())?String(raw).slice(0,8):d.toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit',second:'2-digit'});};
  const xTicks=Array.from({length:5},(_,pos)=>{const index=Math.round(pos*(rows.length-1)/4),x=left+pos/4*(width-left-right),anchor=pos===0?'start':pos===4?'end':'middle';return '<line x1="'+x+'" y1="'+(height-bottom)+'" x2="'+x+'" y2="'+(height-bottom+4)+'" stroke="#9caf9f"/><text x="'+x+'" y="'+(height-8)+'" text-anchor="'+anchor+'" fill="#526b5d" font-size="10">'+escapeHtml(stampAt(index))+'</text>';}).join('');
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
    clearTimeout(connectionStatusTimer);
    window.MarketClock.update(data.meta||data);
    state.source = data.source;
    const live = ["live", "ssi"].includes(data.source);
    const liveReady = live && data.feed_state === "live" && Boolean(data.last_data_at);
    const historyReady = live && Number(data.historical_prices_rows || 0) > 0;
    const liveLabel = "SSI FASTCONNECT";
    $("#mode-badge").textContent = live ? ({live:liveLabel+" · LIVE",delayed:liveLabel+" · TRỄ",stale:liveLabel+" · QUÁ HẠN",outside_session:historyReady?liveLabel+" · GIÁ LỊCH SỬ":liveLabel+" · NGOÀI PHIÊN",awaiting_quote:liveLabel+" · ĐANG CHỜ QUOTE",connecting:liveLabel+" · ĐANG KẾT NỐI",disconnected:historyReady?liveLabel+" · ĐÃ NGẮT KẾT NỐI":liveLabel+" · CHƯA KẾT NỐI",error:liveLabel+" · LỖI KẾT NỐI"}[data.feed_state] || liveLabel+" · CHƯA CÓ DATA") : "MÔ PHỎNG (DEMO)";
    $("#mode-badge").className = "badge " + (liveReady ? "badge-live" : "badge-demo");
    $("#connection-label").textContent = viStatus(data.connection_status);
    $("#connection-dot").className = "status-dot " + (
      data.connection_status === "connected" ? "connected" :
      ["error", "disconnected"].includes(data.connection_status) ? "error" : ""
    );
    $("#last-update").textContent = fmtDateTime(data.data_as_of || data.meta?.data_as_of || data.last_data_at);
    const notice = $("#global-notice");
    const feedWarning = ["disconnected", "error", "delayed", "stale"].includes(data.feed_state);
    notice.className = "notice " + (live && feedWarning ? "notice-error" : live && (liveReady || data.feed_state === "outside_session") ? "notice-live" : "notice-demo");
    const clock=data.meta||data;
    const latestOhlcDate=clock.latest_historical_date||data.latest_historical_date||clock.session_date;
    const marketMessage=clock.state==="IN_SESSION"?`Trong phiên · cập nhật mỗi ${clock.refresh_seconds||5} giây`:
      clock.state==="LUNCH_BREAK"?`Nghỉ trưa · dữ liệu đến 11:30`:
      `Ngoài phiên · OHLC SSI mới nhất đang có: ${fmtDate(latestOhlcDate)} · mở phiên lúc ${fmtTime(clock.next_open_at)}`;
    let message = live
      ? feedStateText(data) + ". Nguồn SSI FastConnect. Trạng thái kết nối: " + viStatus(data.connection_status) + "."
      : "MÔ PHỎNG (DEMO) · Dữ liệu tổng hợp để xem trước, không phải dữ liệu thị trường thật.";
    if(live)message=marketMessage+" · "+message;
    if (live && !data.last_data_at) message += historyReady ? " Chưa có báo giá sổ lệnh trực tiếp; đang hiển thị giá OHLC lịch sử SSI." : " Chưa nhận được snapshot nào nên chưa có dữ liệu sổ lệnh để hiển thị.";
    if (data.processing_delayed) message += ` SSI vẫn nhận quote lúc ${fmtTime(data.last_quote_received_at)}, nhưng snapshot lưu gần nhất cách đây ${fmt(data.persistence_lag_seconds, 0)} giây; dữ liệu hiển thị chưa theo kịp.`;
    else if (data.is_stale) message += " Không nhận quote hợp lệ mới trong phiên liên tục; dữ liệu hiển thị đã cũ và không được xem là trực tiếp (LIVE).";
    if (data.last_error) message += " Worker ghi nhận lỗi trong phiên gần nhất; hãy kiểm tra nhật ký kết nối.";
    $("#notice-text").textContent = message;
    updateConnectionDialog(data);
    if (state.route === "methodology") renderCollectorStatus(data);
    // A worker may take a few seconds to move from connecting to connected.
    // Keep the badge/dialog fresh while it is negotiating the SSI stream.
    if (data.worker_status === "running" && !document.hidden) {
      const interval = ["connecting", "reconnecting"].includes(data.connection_status) ? 2000 : 5000;
      connectionStatusTimer = window.setTimeout(refreshStatus, interval);
    }
  } catch (error) {
    $("#connection-label").textContent = "Backend không phản hồi";
    $("#notice-text").textContent = "Không lấy được trạng thái kết nối dữ liệu. Hãy kiểm tra web backend.";
    const dialogStatus = $("#connection-dialog-status");
    if (dialogStatus) dialogStatus.textContent = "Chưa liên hệ được web backend nên chưa thể yêu cầu kết nối SSI.";
    $("#global-notice").className = "notice notice-error";
    if (!document.hidden) connectionStatusTimer = window.setTimeout(refreshStatus, 5000);
  }
}
function renderCollectorStatus(data) {
  const workerState = ({running:"Tiến trình đang chạy",heartbeat_lost:"Mất nhịp cập nhật",stopped:"Đã dừng",
    abandoned:"Bị dừng đột ngột",failed:"Dừng do lỗi",connecting:"Đang khởi tạo"}[data.worker_status]) ||
    (data.worker_status ? "Không hoạt động" : "Chưa có tiến trình hoạt động");
  const heartbeatAge = data.worker_heartbeat_age_seconds == null ? "—" : fmt(data.worker_heartbeat_age_seconds, 0) + " giây trước";
  const items = [
    ["Nguồn dữ liệu", "SSI FastConnect"],
    ["Tiến trình thu thập", workerState],
    ["Tình trạng dữ liệu", feedStateText(data)],
    ["Trạng thái kết nối", viStatus(data.connection_status)],
    ["Nhịp hoạt động (heartbeat)", fmtTime(data.worker_heartbeat_at) + (heartbeatAge !== "—" ? " · " + heartbeatAge : "")],
    ["Mã lượt thu thập", data.collector_run_id ?? "—"],
    ["Thông điệp nhận được (message)", fmt(data.messages_received, 0)],
    ["Ảnh chụp dữ liệu hợp lệ (snapshot)", fmt(data.rows_valid, 0)],
    ["Lỗi / trùng", fmt(data.rows_invalid, 0) + " / " + fmt(data.duplicate_messages, 0)],
    ["Kết nối lại (reconnect)", fmt(data.reconnect_count, 0)],
    ["Báo giá SSI nhận gần nhất", fmtTime(data.last_quote_received_at)],
    ["Snapshot lưu gần nhất / độ trễ", fmtTime(data.last_persisted_at) + " / " + fmt(data.persistence_lag_seconds, 0) + " giây"],
    ["Lỗi phiên gần nhất", data.last_error ? "Tiến trình gặp lỗi; hãy kiểm tra nhật ký kết nối" : "Không ghi nhận lỗi"]
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
  let overviewCoreRendered = false;
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
    overviewCoreRendered = true;
    try {
      const rankings = await api("/api/rankings?limit=5&window_minutes=" + limit);
      if (window.renderOverviewSpreadRanking) {
        window.renderOverviewSpreadRanking(rankings, Number($("#overview-limit")?.value || 60), window.MarketClock?.get() || {});
      } else {
        const preview = (rankings.lowest_spread || []).slice(0, 5).map((row, index) => [
          '<strong>' + (index + 1) + '</strong>',
          '<strong>' + escapeHtml(row.symbol) + '</strong>',
          fmt(row.median_spread_bps) + " điểm cơ bản (bps)",
          fmt(row.snapshot_count, 0)
        ]);
        $("#overview-ranking").innerHTML = table(["Hạng", "Mã", "Trung vị chênh lệch giá (spread)", "Số ảnh chụp dữ liệu (snapshots)"], preview);
      }
    } catch {
      const ranking = $("#overview-ranking");
      if (ranking) ranking.innerHTML = '<div class="empty-state">Chưa tải được bảng xếp hạng; các biểu đồ vẫn hiển thị độc lập.</div>';
    }
  } catch (error) {
    if (!overviewCoreRendered) $("#spread-chart").innerHTML = '<div class="empty-state">Chưa tải được biểu đồ thanh khoản trong cửa sổ đã chọn.</div>';
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
$("#open-connection-dialog").addEventListener("click", () => {
  showConnectionDialog();
  refreshStatus();
});
$("#connect-now").addEventListener("click", async () => {
  const button = $("#connect-now");
  const message = $("#connection-dialog-status");
  button.disabled = true;
  button.textContent = "Đang khởi chạy…";
  message.textContent = "Đang yêu cầu kết nối SSI FastConnect…";
  try {
    const result = await api("/api/collector/start", { method: "POST" });
    if (!result.ok) {
      if (result.worker_command) {
        message.textContent = "Web backend đang dùng phiên bản cũ. Hãy khởi động lại web rồi thử kết nối lại.";
      } else {
        message.textContent = result.message || "Chưa khởi chạy được kết nối SSI.";
      }
      button.disabled = false;
      button.textContent = "Thử kết nối lại";
      return;
    }
    message.textContent = result.message || "Đã gửi yêu cầu; trạng thái kết nối sẽ tự cập nhật.";
    await refreshStatus();
    window.setTimeout(refreshStatus, 1500);
  } catch (_) {
    message.textContent = "Chưa khởi chạy được tiến trình thu thập. Hãy kiểm tra cấu hình SSI rồi thử lại.";
    button.disabled = false;
    button.textContent = "Thử kết nối lại";
  }
});
$("#disconnect-data").addEventListener("click", async () => {
  const button = $("#disconnect-data");
  button.disabled = true;
  $("#connection-dialog-status").textContent = "Đang gửi yêu cầu ngắt kết nối…";
  try {
    await api("/api/collector/stop", { method: "POST" });
    $("#connection-dialog-status").textContent = "Đã gửi yêu cầu ngắt kết nối SSI.";
    window.setTimeout(refreshStatus, 1000);
  } catch (_) {
    $("#connection-dialog-status").textContent = "Chưa gửi được yêu cầu ngắt kết nối. Hãy thử lại.";
  } finally {
    button.disabled = false;
  }
});
$("#overview-symbol").addEventListener("change", () => (window.loadOverview || loadOverview)());
$("#overview-limit").addEventListener("change", () => (window.loadOverview || loadOverview)());
$("#detail-symbol").addEventListener("change", () => (window.loadDetail || loadDetail)());
$("#ranking-limit").addEventListener("change", () => (window.loadRankings || loadRankings)());
// enhancements.js replaces the legacy overview loader. Defer the first route
// until all deferred scripts have initialized so the legacy loader cannot
// finish later and overwrite the SSI-specific charts with empty placeholders.
window.setTimeout(() => {
  if (localStorage.getItem("sli-onboarding-done") === "1") enterApp("overview");
  else onboardingStage(0);
}, 0);
refreshStatus();
const marketEvents=new EventSource('/api/stream');
window.sliMarketStream=marketEvents;
for(const eventName of ['market_state','data_diff','heartbeat'])marketEvents.addEventListener(eventName,event=>{
  try{const payload=JSON.parse(event.data),meta=payload.meta||payload;window.MarketClock.update(meta);if(!document.hidden&&(eventName==='heartbeat'||eventName==='market_state'))refreshStatus();if(!document.hidden&&eventName==='data_diff')window.enhancedRefresh?.();}catch(_){ }
});
document.addEventListener('visibilitychange',()=>{window.MarketClock.visibilityChanged();if(!document.hidden){clearTimeout(connectionStatusTimer);refreshStatus();}});
