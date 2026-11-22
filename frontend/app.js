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
  if (Number.isNaN(date.valueOf())) return escapeHtml(value);
  const time = date.toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${time} · ${window.formatVnDate(value)}`;
}
function fmtDateTime(value) {
  if (!value) return "chưa có bản ghi";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return escapeHtml(value);
  const time = date.toLocaleTimeString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  return `${window.formatVnDate(value)} ${time}`;
}
window.formatVnDate = function formatVnDate(value) {
  if (!value) return "—";
  const raw = String(value).trim();
  const ymd = raw.match(/^(\d{4})[-/](\d{2})[-/](\d{2})/);
  if (ymd) return `${ymd[3]}/${ymd[2]}/${ymd[1]}`;
  const dmy = raw.match(/^(\d{2})[-/](\d{2})[-/](\d{4})/);
  if (dmy) return `${dmy[1]}/${dmy[2]}/${dmy[3]}`;
  const date = new Date(raw);
  return Number.isNaN(date.valueOf()) ? raw : date.toLocaleDateString("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", year: "numeric" });
};
function fmtDate(value) {
  if (!value) return "chưa có dữ liệu";
  return escapeHtml(window.formatVnDate(value));
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
  if (route === "project") route = "intro";
  window.dispatchEvent(new CustomEvent("sli-route-start", { detail: { route } }));
  state.route = route;
  window.sliRoute = route;
  $$(".view").forEach((view) => view.classList.toggle("hidden", view.id !== "view-" + route));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.route === route));
  let pageLoad = Promise.resolve();
  try {
    if (route === "overview") pageLoad = Promise.resolve((window.loadOverview || loadOverview)());
    if (route === "watchlist") pageLoad = Promise.resolve((window.loadWatchlist || (()=>{}))());
    if (route === "detail") pageLoad = Promise.resolve((window.loadDetail || loadDetail)());
    if (route === "rankings") pageLoad = Promise.resolve((window.loadRankings || loadRankings)());
    if (route === "alerts") pageLoad = Promise.resolve((window.loadAlerts || loadAlerts)());
    if (route === "methodology") pageLoad = Promise.resolve((window.loadMethodology || loadMethodology)());
  } catch (error) { pageLoad = Promise.reject(error); }
  window.dispatchEvent(new CustomEvent("sli-route-loading", { detail: { route, promise: pageLoad } }));
  window.scrollTo({ top: 0, behavior: "smooth" });
  return pageLoad;
}
window.setRoute = setRoute;
function enterApp(route = "overview") {
  localStorage.setItem("sli-onboarding-done", "1");
  $("#onboarding").classList.add("hidden");
  $("#application").classList.remove("hidden");
  const pageLoad=setRoute(route);
  refreshStatus().then(showConnectionDialog);
  return pageLoad;
}
function showConnectionDialog() {
  if(window.productTourPending||window.productTourActive)return;
  const dialog = $("#connection-dialog");
  if (dialog && !dialog.open) dialog.showModal();
  updateConnectionDialog();
}
function feedStateText(data) {
  const age = data.quote_age_seconds == null ? "" : " · quote mới nhất cách đây " + fmt(data.quote_age_seconds, 0) + " giây";
  const dbLag = data.persistence_lag_seconds == null ? "" : "; DB lưu trễ " + fmt(data.persistence_lag_seconds, 0) + " giây";
  const savedAt = data.last_persisted_at ? "; đã lưu đến " + fmtDateTime(data.last_persisted_at) : "";
  const workerAlive = data.worker_process_alive === true || ["running", "heartbeat_delayed"].includes(data.worker_status);
  if (workerAlive && ["delayed", "stale"].includes(data.feed_state) && data.quote_age_seconds != null && data.quote_age_seconds <= (data.stale_after_seconds || 120))
    return "SSI vẫn nhận quote mới" + age + dbLag + savedAt + ". Kết nối đang chạy; nút kết nối không cần bấm lại.";
  if (workerAlive && ["delayed", "stale"].includes(data.feed_state))
    return "Tiến trình collector còn chạy nhưng quote hoặc dữ liệu lưu đã quá hạn" + age + dbLag + savedAt + ". Bấm Kết nối không tạo worker mới.";
  if (data.feed_state === "live") return "Đang kết nối và nhận dữ liệu trực tiếp (LIVE)" + age;
  if (data.feed_state === "subscription_pending") return "Đã nối SSI, đang xác nhận đăng ký kênh báo giá";
  if (data.feed_state === "subscription_partial") return "SSI mới xác nhận một phần kênh báo giá; dữ liệu có thể thiếu mã/sàn";
  if (data.feed_state === "subscription_unverified") return "Socket đã nối nhưng SSI chưa xác nhận đăng ký kênh báo giá; chưa thể xác nhận realtime";
  if (data.feed_state === "connecting") return "Đang kết nối SSI FastConnect";
  if (data.feed_state === "delayed") return "Đang kết nối, dữ liệu đến chậm" + age + dbLag;
  if (data.feed_state === "stale") return "Dữ liệu đã quá hạn" + age + dbLag;
  if (data.feed_state === "awaiting_persistence") return "Đã nhận message SSI nhưng chưa xác nhận ghi snapshot hợp lệ vào database" + age;
  if (data.feed_state === "persistence_error") return "Đã nhận dữ liệu nhưng tiến trình ghi database báo lỗi" + age;
  if (data.feed_state === "persistence_stale") return "Quote mới đã nhận nhưng mốc ghi database đã quá hạn" + age + dbLag;
  if (data.feed_state === "degraded") return "Đang nhận dữ liệu nhưng có message bị rớt; độ phủ chưa được đảm bảo" + age;
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
  const active = data.worker_process_alive === true || ["running", "heartbeat_delayed"].includes(data.worker_status);
  const feedLagging = ["delayed", "stale", "awaiting_persistence", "persistence_error", "persistence_stale", "degraded", "subscription_partial", "subscription_unverified"].includes(data.feed_state);
  button.disabled = active;
  button.textContent = active ? (feedLagging ? "Collector đang chạy" : "Tiến trình đang hoạt động") : "Kết nối dữ liệu";
  if (disconnect) disconnect.hidden = !active || feedLagging;
  const headerButton = $("#open-connection-dialog");
  if (headerButton) headerButton.textContent = active ? "Trạng thái dữ liệu" : "Kết nối dữ liệu";
  const connectionStatus = String(data.connection_status || "").toLowerCase();
  if (title) title.textContent = !active ? "Kết nối SSI FastConnect?" : connectionStatus === "connected"
    ? "SSI đã kết nối" : ["error", "disconnected"].includes(connectionStatus)
      ? "SSI mất kết nối" : "SSI đang kết nối";
  if (copy) copy.textContent = active
    ? feedLagging
      ? "Collector vẫn chạy. Trạng thái quá hạn ở đây nghĩa là dữ liệu lưu trong SQLite đang chậm; bấm Kết nối sẽ không khởi động lại hoặc tạo worker mới."
      : connectionStatus === "connected"
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
  const xTicks=Array.from({length:5},(_,pos)=>{const index=Math.round(pos*(rows.length-1)/4),x=left+pos/4*(width-left-right),row=rows[index],raw=row.trading_date||row.minute||row.source_ts_vn||row.received_at_vn||row.source_timestamp||row.received_at_utc||row.timestamp||'—',d=new Date(String(raw).replaceAll('/','-')+(String(raw).length===10?'T00:00:00+07:00':'')),stamp=Number.isNaN(d.valueOf())?String(raw).slice(0,10):row.trading_date?window.formatVnDate(raw):d.toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit'}),anchor=pos===0?'start':pos===4?'end':'middle';return '<line x1="'+x+'" y1="'+(height-bottom)+'" x2="'+x+'" y2="'+(height-bottom+4)+'" stroke="#9caf9f"/><text x="'+x+'" y="'+(height-9)+'" text-anchor="'+anchor+'" fill="#526b5d" font-size="10">'+escapeHtml(stamp)+'</text>';}).join('');
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
let collectorEnsureInFlight = false;
let collectorEnsureAttemptAt = 0;
function ensureCollectorOnPageLoad(data) {
  if (!["live", "ssi"].includes(data.source)) return;
  if (data.status_db_busy) return;
  if (data.worker_process_alive) return;
  if (localStorage.getItem("sli-collector-manually-stopped") === "1") return;
  const heartbeatFresh = data.worker_status === "running" &&
    Number(data.worker_heartbeat_age_seconds ?? Infinity) <= 15;
  if (heartbeatFresh || collectorEnsureInFlight || Date.now() - collectorEnsureAttemptAt < 60000) return;
  collectorEnsureInFlight = true;
  collectorEnsureAttemptAt = Date.now();
  api("/api/collector/start", { method: "POST" })
    .then((result) => {
      if (result?.ok) {
        window.setTimeout(refreshStatus, 1200);
      } else {
        connectionStatusTimer = window.setTimeout(refreshStatus, 10000);
      }
    })
    .catch(() => {
      connectionStatusTimer = window.setTimeout(refreshStatus, 10000);
    })
    .finally(() => { collectorEnsureInFlight = false; });
}
async function refreshStatus() {
  try {
    const data = await api("/api/status");
    state.status = data;
    ensureCollectorOnPageLoad(data);
    clearTimeout(connectionStatusTimer);
    window.MarketClock.update(data.meta||data);
    state.source = data.source;
    const live = ["live", "ssi"].includes(data.source);
    const liveReady = live && data.feed_state === "live" && Boolean(data.last_data_at);
    const historyReady = live && Number(data.historical_prices_rows || 0) > 0;
    const liveLabel = "SSI FASTCONNECT";
    $("#mode-badge").textContent = live ? ({live:liveLabel+" · LIVE ĐÃ XÁC NHẬN",delayed:liveLabel+" · TRỄ",stale:liveLabel+" · QUÁ HẠN",outside_session:historyReady?liveLabel+" · GIÁ LỊCH SỬ":liveLabel+" · NGOÀI PHIÊN",awaiting_quote:liveLabel+" · ĐANG CHỜ QUOTE",awaiting_persistence:liveLabel+" · ĐANG CHỜ LƯU DB",persistence_error:liveLabel+" · LỖI GHI DB",persistence_stale:liveLabel+" · DB QUÁ HẠN",degraded:liveLabel+" · CÓ RỚT MESSAGE",subscription_pending:liveLabel+" · ĐANG XÁC NHẬN KÊNH",subscription_partial:liveLabel+" · THIẾU KÊNH",subscription_unverified:liveLabel+" · CHƯA XÁC NHẬN KÊNH",connecting:liveLabel+" · ĐANG KẾT NỐI",disconnected:historyReady?liveLabel+" · ĐÃ NGẮT KẾT NỐI":liveLabel+" · CHƯA KẾT NỐI",error:liveLabel+" · LỖI KẾT NỐI"}[data.feed_state] || liveLabel+" · CHƯA CÓ DATA") : "MÔ PHỎNG (DEMO)";
    $("#mode-badge").className = "badge " + (liveReady ? "badge-live" : "badge-demo");
    $("#connection-label").textContent = viStatus(data.connection_status);
    $("#connection-dot").className = "status-dot " + (
      data.connection_status === "connected" ? "connected" :
      ["error", "disconnected"].includes(data.connection_status) ? "error" : ""
    );
    $("#last-update").textContent = fmtDateTime(data.data_as_of || data.meta?.data_as_of || data.last_data_at);
    const notice = $("#global-notice");
    const feedWarning = ["disconnected", "error", "delayed", "stale", "awaiting_persistence", "persistence_error", "persistence_stale", "degraded", "subscription_partial", "subscription_unverified"].includes(data.feed_state);
    notice.className = "notice " + (live && feedWarning ? "notice-error" : live && (liveReady || data.feed_state === "outside_session") ? "notice-live" : "notice-demo");
    const clock=data.meta||data;
    const latestOhlcDate=clock.latest_historical_date||data.latest_historical_date||window.SLIOverviewStore?.bounds?.latest_date||clock.session_date;
    const marketState=clock.state||clock.market_state||data.market_state;
    const ohlcSync=clock.session_sync||data.session_sync||{};
    const syncCoverage=ohlcSync.ohlc_expected?` · ${fmt(ohlcSync.ohlc_symbols||0,0)}/${fmt(ohlcSync.ohlc_expected,0)} mã`:"";
    const syncLabel=(ohlcSync.status&&ohlcSync.status!=='complete'&&ohlcSync.status!=='not_started')?` · OHLC chưa đồng bộ${syncCoverage}`:"";
    const marketMessage=marketState==="IN_SESSION"?`Trong phiên · ${fmtDate(latestOhlcDate)} · cập nhật mỗi ${clock.refresh_seconds||5} giây`:
      marketState==="LUNCH_BREAK"?`Nghỉ trưa · OHLC gần nhất ${fmtDate(latestOhlcDate)} · dữ liệu đến 11:30`:
      `Ngoài phiên · OHLC gần nhất ${fmtDate(latestOhlcDate)}${syncLabel} · mở phiên lúc ${fmtTime(clock.next_open_at)}`;
    const liveSummary=data.feed_state==="live"?"Realtime đã được xác nhận":
      data.feed_state==="outside_session"?"Đang xem dữ liệu đã lưu; chờ phiên tiếp theo":
      ["delayed","stale","persistence_stale"].includes(data.feed_state)?"Dữ liệu đang trễ; xem chi tiết trong Trạng thái dữ liệu":
      ["persistence_error","awaiting_persistence"].includes(data.feed_state)?"Chưa xác nhận ghi dữ liệu mới vào cơ sở dữ liệu":
      data.feed_state==="degraded"?"Luồng đang có message bị rớt; độ phủ có thể thiếu":`Trạng thái: ${viStatus(data.connection_status)}`;
    let message = live
      ? `${liveSummary} · SSI FastConnect`
      : "MÔ PHỎNG (DEMO) · Dữ liệu tổng hợp để xem trước, không phải dữ liệu thị trường thật.";
    if(live)message=marketMessage+" · "+message;
    if (live && !data.last_data_at) message += historyReady ? " Chưa có báo giá sổ lệnh trực tiếp; đang hiển thị giá OHLC lịch sử SSI." : " Chưa nhận được snapshot nào nên chưa có dữ liệu sổ lệnh để hiển thị.";
    if (data.processing_delayed || data.is_stale || data.last_error) message += " · Mở Trạng thái dữ liệu để xem nguyên nhân kỹ thuật.";
    $("#notice-text").textContent = message;
    updateConnectionDialog(data);
    if (state.route === "methodology") renderCollectorStatus(data);
    // A worker may take a few seconds to move from connecting to connected.
    // Keep the badge/dialog fresh while it is negotiating the SSI stream.
    if ((data.worker_status === "running" || data.worker_process_alive) && !document.hidden) {
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
  const workerState = ({running:"Tiến trình đang chạy",heartbeat_delayed:"Tiến trình còn chạy · heartbeat database trễ",heartbeat_lost:"Mất nhịp cập nhật",stopped:"Đã dừng",
    abandoned:"Bị dừng đột ngột",failed:"Dừng do lỗi",connecting:"Đang khởi tạo"}[data.worker_status]) ||
    (data.worker_status ? "Không hoạt động" : "Chưa có tiến trình hoạt động");
  const heartbeatAge = data.worker_heartbeat_age_seconds == null ? "—" : fmt(data.worker_heartbeat_age_seconds, 0) + " giây trước";
  const items = [
    ["Nguồn dữ liệu", "SSI FastConnect"],
    ["Tiến trình thu thập", workerState],
    ["Tình trạng dữ liệu", feedStateText(data)],
    ["Trạng thái kết nối", viStatus(data.connection_status)],
    ["Xác nhận kênh quote / sàn", (data.subscription_status || "chưa xác nhận") + " · " + ((data.subscribed_boards || []).join(", ") || "chưa có danh sách")],
    ["SSI xác nhận đăng ký", fmt(data.subscription_ack_count, 0) + (data.subscription_error ? " · " + data.subscription_error : "")],
    ["Ghi quote / snapshot vào DB", (data.quote_persistence_status || "chưa xác nhận") + " · hàng đợi " + fmt(data.queue_depth, 0) + " · rớt " + fmt(data.queue_dropped, 0)],
    ["Trade: xác nhận / ghi DB", (data.trade_subscription_status || "chưa xác nhận") + " / " + (data.trade_persistence_status || "chưa xác nhận")],
    ["Realtime đã xác minh", data.realtime_verified === true ? "Có · kênh + message + DB" : "Chưa · " + (data.feed_state || "chưa rõ")],
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
    // enhancements.js owns the overview metrics once its unified loader is installed.
    // Do not let this legacy request overwrite those cards if it finishes later.
    if (window.enhancedOverviewInstalled && $("#kpi-spread").textContent.trim() !== "—") return;
    $("#kpi-symbols").textContent = fmt(data.symbols_count, 0);
    $("#kpi-spread").textContent = fmt(data.median_spread_bps);
    $("#kpi-depth").textContent = fmt(data.median_total_depth_value, 0);
    $("#kpi-valid").textContent = data.valid_snapshot_rate == null ? "—" : (data.valid_snapshot_rate * 100).toFixed(1) + "%";
    $("#kpi-valid-count").textContent = fmt(data.valid_observation_count, 0) + " / " + fmt(data.observation_count, 0) + " snapshot hợp lệ";
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
let rankingPayload = null;
let rankingTab = "spread";
let rankingVisibleCount = 10;
function renderRankings() {
  const host = $("#ranking-table-container");
  if (!host) return;
  if (!rankingPayload) { host.innerHTML = '<div class="empty-state">Đang tải xếp hạng…</div>'; return; }
  const map = {spread:"lowest_spread",depth:"highest_depth",widen:"spread_widening",fall:"depth_falling"};
  const sourceRows = rankingPayload[map[rankingTab]] || [];
  const query = $("#ranking-search")?.value.trim().toUpperCase() || "";
  const rows = query ? sourceRows.filter(row => String(row.symbol || "").toUpperCase().includes(query)) : sourceRows;
  const shown = rows.slice(0, rankingVisibleCount);
  const metric = row => {
    if (rankingTab === "spread") return `${fmt(row.median_spread_bps, 2)} bps <small>${fmt(row.median_spread_ticks, 1)} bước giá</small>`;
    if (rankingTab === "depth") return `${fmt(row.median_total_depth_value, 0)} đồng <small>Giá trị niêm yết top 3</small>`;
    if (rankingTab === "widen") return `${fmt(row.spread_expansion_bps, 2)} bps <small>Spread mới nhất ${fmt(row.latest_spread_bps, 2)} bps</small>`;
    return `${fmt(row.depth_change_pct, 1)}% <small>Độ sâu mới nhất ${fmt(row.latest_total_depth_value, 0)} đồng</small>`;
  };
  const body = shown.map((row, index) => {
    const change = row.rank_change == null ? null : Number(row.rank_change);
    const movement = change == null || !Number.isFinite(change) ? '<span class="rank-change new">Chưa có hạng trước</span>' : change > 0 ? `<span class="rank-change up">↑ ${change} hạng</span>` : change < 0 ? `<span class="rank-change down">↓ ${Math.abs(change)} hạng</span>` : '<span class="rank-change same">0 hạng</span>';
    const tie = row.one_tick_tie ? '<span class="rank-tie">1 bước giá</span>' : '';
    const rankClass=change>0?' moved-up':change<0?' moved-down':'';
    return `<tr class="ranking-row${rankClass}"><td><span class="rank-number">${index + 1}</span>${movement}</td><td><button type="button" class="rank-symbol" aria-label="Mã ${escapeHtml(row.symbol)}">${escapeHtml(row.symbol)}</button><small class="rank-exchange">${escapeHtml(row.board || "—")}</small><button type="button" class="quick-watch-button" data-quick-watch-symbol="${escapeHtml(row.symbol)}">＋ Danh mục</button></td><td><strong>${metric(row)}</strong>${tie}</td><td><strong>${fmt(row.median_spread_bps, 2)} bps</strong><small>${fmt(row.median_spread_ticks, 1)} bước giá</small></td><td><strong>${fmt(row.median_total_depth_value, 0)} đồng</strong><small>Giá trị niêm yết top 3</small></td><td><strong>${fmt(row.bar_count || row.sample_count || row.snapshot_count, 0)}</strong><small>bar hợp lệ</small></td></tr>`;
  }).join("");
  const labels={spread:"Spread thấp nhất",depth:"Độ sâu top 3 cao nhất",widen:"Spread nới rộng nhiều nhất",fall:"Độ sâu suy giảm nhiều nhất"};
  const empty=rankingPayload.symbols_with_window_bars===0
    ? `Không có bar SSI trong cửa sổ ${rankingPayload.sample_window_minutes} phút đã chọn. Dữ liệu sổ lệnh mới nhất lưu đến ${escapeHtml(rankingPayload.window_end_exclusive || "—")}.`
    : query ? `Không tìm thấy mã “${escapeHtml(query)}” trong nhóm ${labels[rankingTab].toLocaleLowerCase("vi-VN")}.` : `Có ${fmt(rankingPayload.symbols_with_window_bars,0)} mã có bar trong cửa sổ, nhưng chưa mã nào đạt tối thiểu ${fmt(rankingPayload.minimum_eligible_samples,0)} bar hợp lệ.`;
  const clock=window.MarketClock?.get?.()||{},live=clock.market_state==="IN_SESSION",comparison=rankingPayload.comparison_session_date?`So với phiên ${fmtDate(rankingPayload.comparison_session_date)}, cùng khung giờ`:rankingPayload.rank_change_basis||"Chưa có dữ liệu phiên trước để so sánh";
  host.innerHTML = shown.length ? `<div class="ranking-board"><div class="ranking-board-meta"><span class="ranking-live-dot${live?' live':''}"></span><b>${fmt(rankingPayload.eligible_symbols,0)} mã đủ điều kiện</b><span>${live?'Thứ hạng cập nhật theo SSI':'Thứ hạng từ bar SSI đã lưu'} · ${escapeHtml(comparison)}</span></div><table class="ranking-table ranking-full-table"><thead><tr><th>HẠNG · THAY ĐỔI</th><th>MÃ / SÀN</th><th>${labels[rankingTab].toLocaleUpperCase("vi-VN")}</th><th>SPREAD TRUNG VỊ</th><th>ĐỘ SÂU TOP 3</th><th>SỐ BAR</th></tr></thead><tbody>${body}</tbody></table><div class="ranking-board-note">↑ tăng hạng · ↓ giảm hạng · 0 hạng không đổi. Thiếu dữ liệu phiên trước thì không suy đoán thứ hạng.</div></div>` : `<div class="empty-state ranking-empty-state">${empty}</div>`;
  $("#ranking-count").textContent = `Hiển thị ${shown.length} / ${rows.length} mã · ${fmt(rankingPayload.eligible_symbols,0)} mã đủ điều kiện`;
  const more=$("#ranking-more"); if(more){more.hidden=shown.length>=rows.length;more.textContent=`Xem thêm ${Math.min(25,rows.length-shown.length)}`;}
  const note=$("#ranking-live-note"); if(note) note.textContent=`${rankingPayload.note||""} Cửa sổ ${rankingPayload.sample_window_minutes} phút · từ ${fmtTime(rankingPayload.window_start)} đến ${fmtTime(rankingPayload.window_end_exclusive)} · bar phiên ${fmtDate(rankingPayload.comparison_session_date||String(rankingPayload.window_end_exclusive||"").slice(0,10))}. Spread thấp và độ sâu cao là hai chiều quan sát riêng, không phải khuyến nghị giao dịch.`;
}
async function loadRankings() {
  const host=$("#ranking-table-container");
  if(host)host.innerHTML='<div class="empty-state">Đang tải dữ liệu xếp hạng SSI…</div>';
  try {
    const windowMinutes=Number($("#ranking-limit")?.value||60),board=$("#ranking-board")?.value||"";
    const params=new URLSearchParams({limit:"2000",window_minutes:String(windowMinutes)});if(board)params.set("board",board);
    rankingPayload=await api("/api/rankings?"+params.toString());
    rankingVisibleCount=Number($("#ranking-size")?.value||10);
    renderRankings();
    const updated=$("#ranking-updated");if(updated)updated.textContent=rankingPayload.comparison_session_date?`Đối chiếu phiên ${fmtDate(rankingPayload.comparison_session_date)}`:`Dữ liệu SSI · ${fmtTime(rankingPayload.window_end_exclusive)}`;
  } catch(error) {
    rankingPayload=null;
    if(host)host.innerHTML=`<div class="empty-state ranking-empty-state">Không tải được dữ liệu xếp hạng: ${escapeHtml(error.message)}<br><button class="button button-secondary" type="button" id="ranking-retry">Thử tải lại</button></div>`;
    const updated=$("#ranking-updated");if(updated)updated.textContent="Lỗi tải dữ liệu";
  }
}
let alertsLoadedCount = 0;
let alertsTotalCount = 0;
async function loadAlerts(offset = 0) {
  void initAlertSymbolSearch();
  try {
    const pageSize = 100;
    const symbol = document.querySelector("#alert-symbol-search")?.value.trim().toUpperCase() || "";
    const data = await api(`/api/alerts?limit=${pageSize}&offset=${offset}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`);
    const alerts = data.alerts || [];
    alertsTotalCount = Number(data.alert_count ?? data.total_count ?? alerts.length);
    alertsLoadedCount = offset + alerts.length;
    $("#alert-summary").innerHTML = '<span class="notice-icon">i</span><span>' +
      (alertsTotalCount ? `${symbol ? `Mã ${escapeHtml(symbol)} · ` : ""}${alertsLoadedCount.toLocaleString("vi-VN")} / ${alertsTotalCount.toLocaleString("vi-VN")} cảnh báo đã tải.` :
        "Chưa có cảnh báo được lưu. Mốc tham chiếu (baseline) cần tối thiểu " + data.baseline_min_observations + " quan sát hợp lệ.") + '</span>';
    const rendered = table(
      ["Mã", "Loại cảnh báo", "Mức độ", "Chỉ số (metric)", "Ngưỡng", "Số mẫu", "Thời điểm", "Giải thích"],
      alerts.map((row) => [
        '<strong>' + escapeHtml(row.symbol) + '</strong>', escapeHtml(viAlert(row.alert_type)),
        escapeHtml(viStatus(row.severity)), fmt(row.metric_value), fmt(row.threshold_value),
        fmt(row.baseline_window, 0), fmtTime(row.triggered_at), escapeHtml(row.explanation)
      ]),
      "Chưa có cảnh báo cho nguồn dữ liệu này."
    );
    const host = $("#alerts-table");
    if (offset > 0 && host.querySelector("tbody")) {
      const temp = document.createElement("div"); temp.innerHTML = rendered;
      host.querySelector("tbody").insertAdjacentHTML("beforeend", temp.querySelector("tbody")?.innerHTML || "");
    } else host.innerHTML = rendered;
    let pager = document.querySelector("#alerts-pager");
    if (!pager) { pager = document.createElement("div"); pager.id = "alerts-pager"; pager.className = "alerts-pager"; host.after(pager); }
    pager.innerHTML = alertsLoadedCount < alertsTotalCount
      ? `<span>Đang hiển thị ${alertsLoadedCount.toLocaleString("vi-VN")} trên ${alertsTotalCount.toLocaleString("vi-VN")} cảnh báo</span><button class="button button-secondary" type="button" id="alerts-load-more">Tải thêm 100 cảnh báo</button>`
      : `<span>Đã hiển thị tất cả ${alertsTotalCount.toLocaleString("vi-VN")} cảnh báo</span>`;
    document.querySelector("#alerts-load-more")?.addEventListener("click", () => loadAlerts(alertsLoadedCount), { once: true });
  } catch (error) {
    $("#alerts-table").innerHTML = '<div class="empty-state">' + escapeHtml(error.message) + '</div>';
  }
}

async function initAlertSymbolSearch() {
  const list = document.querySelector("#alert-symbol-options");
  if (!list || list.dataset.loaded) return;
  list.dataset.loaded = "1";
  try {
    const data = await api("/api/symbols");
    list.innerHTML = (data.symbols || []).map((symbol) => `<option value="${escapeHtml(symbol)}"></option>`).join("");
  } catch (_) { list.dataset.loaded = ""; }
}
document.addEventListener("submit", (event) => {
  if (event.target?.id !== "alert-symbol-filter") return;
  event.preventDefault();
  loadAlerts(0);
});
document.addEventListener("click", (event) => {
  if (event.target?.id !== "alert-symbol-clear") return;
  const input = document.querySelector("#alert-symbol-search");
  if (input) input.value = "";
  loadAlerts(0);
});

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
$("#restart-product-tour")?.addEventListener("click",()=>window.startProductTour?.({force:true}));
window.addEventListener("sli-product-tour-finished",()=>{window.productTourPending=false;refreshStatus().then(showConnectionDialog);});
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
  localStorage.removeItem("sli-collector-manually-stopped");
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
    localStorage.setItem("sli-collector-manually-stopped", "1");
    $("#connection-dialog-status").textContent = "Đã gửi yêu cầu ngắt kết nối SSI.";
    window.setTimeout(refreshStatus, 1000);
  } catch (_) {
    $("#connection-dialog-status").textContent = "Chưa gửi được yêu cầu ngắt kết nối. Hãy thử lại.";
  } finally {
    button.disabled = false;
  }
});

$("#detail-symbol").addEventListener("change", () => (window.loadDetail || loadDetail)());
$("#ranking-limit").addEventListener("change", () => (window.loadRankings || loadRankings)());
$("#ranking-size")?.addEventListener("change", () => { rankingVisibleCount=Number($("#ranking-size").value||10);renderRankings(); });
$("#ranking-search")?.addEventListener("input", () => renderRankings());
$("#ranking-more")?.addEventListener("click", () => { rankingVisibleCount+=25;renderRankings(); });
document.addEventListener("click", event => {
  const tab=event.target.closest("[data-ranking-tab]");
  if(tab){rankingTab=tab.dataset.rankingTab;document.querySelectorAll("[data-ranking-tab]").forEach(button=>button.setAttribute("aria-selected",String(button===tab)));rankingVisibleCount=Number($("#ranking-size")?.value||10);renderRankings();}
  if(event.target?.id==="ranking-retry")(window.loadRankings||loadRankings)();
});
// enhancements.js replaces the legacy overview loader. Defer the first route
// until all deferred scripts have initialized so the legacy loader cannot
// finish later and overwrite the SSI-specific charts with empty placeholders.
window.setTimeout(() => {
  const needsTour=localStorage.getItem("sli-product-tour-done")!=="1";
  window.productTourPending=needsTour;
  const initialLoad=enterApp("overview");
  if(needsTour){
    let started=false;
    const begin=()=>{if(started)return;started=true;window.setTimeout(()=>window.startProductTour?.(),650);};
    Promise.race([Promise.resolve(initialLoad).catch(()=>{}),new Promise(resolve=>window.setTimeout(resolve,10000))]).then(begin);
  }
}, 0);
refreshStatus();
const marketEvents=new EventSource('/api/stream');
window.sliMarketStream=marketEvents;
let observedHistoricalDate=null,lastOverviewStreamRefreshAt=0;
for(const eventName of ['market_state','data_diff','heartbeat'])marketEvents.addEventListener(eventName,event=>{
  try{
    const payload=JSON.parse(event.data),meta=payload.meta||payload;
    window.MarketClock.update(meta);
    const latestDate=meta.latest_historical_date;
    const historyDateChanged=Boolean(latestDate&&observedHistoricalDate&&latestDate!==observedHistoricalDate);
    if(latestDate)observedHistoricalDate=latestDate;
    if(!document.hidden&&(eventName==='heartbeat'||eventName==='market_state'))refreshStatus();
    if(eventName==='market_state')lastOverviewStreamRefreshAt=Date.now();
    if(!document.hidden&&(eventName==='data_diff'||historyDateChanged)){
      if(historyDateChanged)window.invalidateOverviewDateBounds?.();
      const now=Date.now(),refreshDue=historyDateChanged||now-lastOverviewStreamRefreshAt>=60000;
      if(refreshDue){lastOverviewStreamRefreshAt=now;window.enhancedRefresh?.();}
    }
  }catch(_){ }
});
document.addEventListener('visibilitychange',()=>{window.MarketClock.visibilityChanged();if(!document.hidden){clearTimeout(connectionStatusTimer);refreshStatus();}});
