(() => {
  const profiles = {
    FPT: { name: 'Công ty Cổ phần FPT', sector: 'Công nghệ thông tin' },
    HPG: { name: 'Công ty Cổ phần Tập đoàn Hòa Phát', sector: 'Thép và công nghiệp' },
    VPB: { name: 'Ngân hàng TMCP Việt Nam Thịnh Vượng', sector: 'Ngân hàng' }
  };
  const colors = ['#008A45', '#E2231A', '#68A92E', '#A7BF36', '#00592E'];
  const routeOrder = ['overview','watchlist','detail','rankings','alerts','methodology','guide','intro'];
  let marketHistoryCache=null,marketHistoryFetchedAt=0,marketHistoryCacheKey='';
  let turnoverMetric='value',turnoverPayload=null,turnoverResizeObserver=null,turnoverSignature='';
  let symbolsCache=null,symbolsFetchedAt=0,securitiesDirectory=[],detailSearchDirty=false;
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sourceName = (s) => String(s||'').toLowerCase()==='live'?'Trực tiếp (LIVE) · SSI FastConnect':String(s||'').toLowerCase()==='ssi'?'Dữ liệu SSI đã lưu':'Mô phỏng (DEMO)';
  const statusName = (s) => ({connected:'Đã kết nối (connected)',connecting:'Đang kết nối (connecting)',reconnecting:'Đang kết nối lại (reconnecting)',disconnected:'Đã ngắt kết nối (disconnected)',error:'Có lỗi (error)',stopped:'Đã dừng (stopped)',valid:'Hợp lệ (valid)',invalid:'Không hợp lệ (invalid)',full:'Đủ lượng hiển thị (full)',partial:'Khớp một phần (partial)',active:'Đang có hiệu lực (active)',resolved:'Đã xử lý (resolved)',high:'Cao (high)',medium:'Trung bình (medium)',low:'Thấp (low)'}[String(s||'').toLowerCase()]||s||'—');
  const alertName = (s) => ({spread_widening:'Chênh lệch giá mua – bán nới rộng (spread widening)',depth_drop:'Độ sâu sổ lệnh suy giảm (depth drop)',combined_liquidity_stress:'Căng thẳng thanh khoản kết hợp (combined liquidity stress)',strong_queue_imbalance:'Mất cân bằng khối lượng chờ mạnh (strong queue imbalance)',stale_data:'Dữ liệu quá hạn (stale data)'}[s]||s||'—');
  const localizeTheory = (s) => String(s||'').replaceAll('Bid–ask spread','Chênh lệch giá mua – bán (bid–ask spread)').replaceAll('quoted spread','chênh lệch theo báo giá (quoted spread)').replaceAll('mid-price','giá giữa (mid-price)').replaceAll('effective spread','chênh lệch thực tế (effective spread)').replaceAll('Depth top 3','Độ sâu ba mức giá đầu (top-3 depth)').replaceAll('depth value','giá trị độ sâu (depth value)').replaceAll('Queue imbalance','Mất cân bằng khối lượng chờ (queue imbalance)').replaceAll('queue imbalance','mất cân bằng khối lượng chờ (queue imbalance)').replaceAll('order-flow imbalance','mất cân bằng dòng lệnh (order-flow imbalance)').replaceAll('snapshot','ảnh chụp dữ liệu (snapshot)').replaceAll('Baseline','Mốc tham chiếu (baseline)').replaceAll('baseline','mốc tham chiếu (baseline)').replaceAll('percentile','phân vị (percentile)').replaceAll('payload','gói dữ liệu (payload)').replaceAll('timestamp','mốc thời gian (timestamp)');
  const num = (v, n=2) => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('vi-VN',{maximumFractionDigits:n});
  const overviewDataStore = window.SLIOverviewStore = {
    filters:{symbol:'__all__',dateFrom:'',dateTo:'',preset:'1m',chartWindow:60},
    historical:{},realtime:{},status:{},bounds:null,boundsFetchedAt:0,boundsPromise:null,cache:new Map(),inflight:new Map(),filterKey:'',generation:0
  };
  function overviewFilterKey(){const f=overviewDataStore.filters;return [f.symbol,f.dateFrom,f.dateTo,f.preset,f.chartWindow].join('|');}
  function overviewSourceKey(url){if(url.includes('/api/market-turnover')||url.includes('/api/market-breadth')||url.includes('/api/market-leaders')||url.includes('/api/price-history')||url.includes('/api/market-history-range')||url.includes('/api/symbols'))return 'historical';if(url.includes('/api/overview')||url.includes('/api/trades')||url.includes('/api/rankings')||url.includes('/api/market-depth')||url.includes('/api/intraday-profile')||url.includes('/api/liquidity-map')||url.includes('/api/status'))return 'realtime';return 'other';}
  function overviewWidgetNodes(url){if(url.includes('/api/market-turnover'))return ['#market-turnover-chart'];if(url.includes('/api/market-depth'))return ['#depth-chart'];if(url.includes('/api/liquidity-map'))return ['#liquidity-map'];if(url.includes('/api/trades'))return ['#live-trade-tape'];if(url.includes('/api/rankings'))return ['#overview-ranking'];if(url.includes('/api/intraday-profile'))return ['#intraday-profile'];if(url.includes('/api/market-leaders'))return ['#spread-compare','#coverage-chart'];if(url.includes('/api/market-breadth'))return ['#spread-chart','#imbalance-chart','#historical-price-chart'];if(url.includes('/api/overview'))return ['#spread-chart','#overview-data-context'];return [];}
  function setOverviewWidgetState(url,state,message=''){for(const selector of overviewWidgetNodes(url)){const node=document.querySelector(selector);if(!node)continue;node.dataset.widgetState=state;node.setAttribute('aria-busy',String(state==='loading'));let stale=node.querySelector(':scope > .widget-stale-badge');if(state==='stale'){if(!stale){stale=document.createElement('span');stale.className='widget-stale-badge';stale.setAttribute('role','status');stale.textContent='Chưa làm mới được · đang giữ dữ liệu đã tải';node.appendChild(stale);}node.setAttribute('aria-description','Đang hiển thị dữ liệu SSI đã tải gần nhất; lượt làm mới mới nhất chưa thành công.');}else{stale?.remove();node.removeAttribute('aria-description');}if(state==='loading'&&!node.dataset.widgetReady)node.innerHTML='<div class="widget-skeleton" aria-label="Đang tải dữ liệu"></div>';else if(state==='empty'&&!node.dataset.widgetReady)node.innerHTML='<div class="empty-state">'+esc(message||'Chưa có dữ liệu SSI trong phạm vi đã chọn.')+'</div>';else if(state==='error'&&!node.dataset.widgetReady)node.innerHTML='<div class="empty-state research-retry"><span>Khối này chưa tải được dữ liệu SSI.</span><button type="button" class="button button-secondary" data-retry-overview>Thử lại</button></div>';}}
  async function call(url,options={}){
    const historicalRoute=/^\/api\/(market-turnover|market-breadth|price-history|market-leaders)/.test(url),
      heavyAggregateRoute=/^\/api\/(market-depth|liquidity-map|rankings|intraday-profile)/.test(url),
      requestTimeout=historicalRoute||heavyAggregateRoute?90000:15000;
    const isOverview=window.sliRoute==='overview'&&!options.noStore;
    if(!isOverview){const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),requestTimeout);try{const r=await fetch(url,{...options,signal:options.signal||controller.signal});if(!r.ok)throw Error('HTTP '+r.status);return await r.json();}finally{clearTimeout(timeout);}}
    const currentKey=overviewFilterKey();
    if(overviewDataStore.filterKey&&overviewDataStore.filterKey!==currentKey){for(const entry of overviewDataStore.inflight.values())entry.controller.abort();overviewDataStore.inflight.clear();overviewDataStore.generation++;}
    overviewDataStore.filterKey=currentKey;
    const key=url,now=Date.now(),previous=overviewDataStore.cache.get(key),ttl=overviewSourceKey(url)==='historical'?60000:2500;
    if(previous&&now-previous.at<ttl){overviewDataStore.status[key]={state:'ready',at:previous.at};return previous.data;}
    if(overviewDataStore.inflight.has(key))return overviewDataStore.inflight.get(key).promise;
    const controller=new AbortController(),generation=overviewDataStore.generation,group=overviewSourceKey(url);overviewDataStore.status[key]={state:'loading',previous:previous?.data||null};setOverviewWidgetState(url,'loading');
    const promise=(async()=>{const timeout=setTimeout(()=>controller.abort(),requestTimeout);try{const response=await fetch(url,{...options,signal:controller.signal,cache:'no-store'});if(!response.ok)throw Error('HTTP '+response.status);const data=await response.json();if(generation!==overviewDataStore.generation)throw new DOMException('Đã hủy yêu cầu cũ','AbortError');overviewDataStore.cache.set(key,{data,at:Date.now()});overviewDataStore[group][key]=data;const empty=(Array.isArray(data.rows)&&!data.rows.length)||(Array.isArray(data.trades)&&!data.trades.length)||(Array.isArray(data.profile)&&!data.profile.length)||(Array.isArray(data.points)&&!data.points.length);overviewDataStore.status[key]={state:empty?'empty':'ready',at:Date.now()};setOverviewWidgetState(url,empty?'empty':'ready',data.message||data.note);for(const selector of overviewWidgetNodes(url)){const node=document.querySelector(selector);if(node)node.dataset.widgetReady='1';}if(url.includes('/api/status'))overviewDataStore.realtime.status=data;if(url.includes('/api/trades'))overviewDataStore.realtime.trades=data;if(url.includes('/api/overview'))overviewDataStore.realtime.quotes=data;return data;}catch(error){overviewDataStore.status[key]={state:error.name==='AbortError'?'loading':'error',error:error.message,previous:previous?.data||null};setOverviewWidgetState(url,previous?'stale':'error');if(previous)return previous.data;throw error;}finally{clearTimeout(timeout);overviewDataStore.inflight.delete(key);}})();
    overviewDataStore.inflight.set(key,{controller,promise});return promise;
  }
  let depthPayload=null,depthObserver=null,depthHoverIndex=-1;
  function renderOverviewDepth(){
    const host=document.querySelector('#depth-chart'),label=document.querySelector('#depth-session-label'),tip=document.querySelector('#depth-tooltip');
    if(!host||!depthPayload)return;
    const rows=depthPayload.rows||[];
    const marketState=depthPayload.meta?.state;
    if(label){const date=depthPayload.session_date?depthPayload.session_date.split('-').reverse().join('/'):'—',history=depthPayload.session_count?`${num(depthPayload.session_count,0)} phiên · `:'';label.textContent=marketState==='IN_SESSION'&&depthPayload.session_date===depthPayload.meta?.session_date?`${history}Trong phiên · dữ liệu đến ${String(depthPayload.as_of||'').slice(11,16)}`:marketState==='IN_SESSION'?`${history}Chưa có bar hôm nay · phiên gần nhất ${date}`:marketState==='LUNCH_BREAK'&&depthPayload.session_date===depthPayload.meta?.session_date?`${history}Nghỉ trưa · dữ liệu đến 11:30`:`${history}Phiên mới nhất ${date} · đã đóng cửa`;}
    const panelCount=document.querySelector('#depth-panel-count');if(panelCount)panelCount.textContent=`N3: ${num(depthPayload.panel_symbols||0,0)} mã${depthPayload.coverage_relaxed?' · nới ngưỡng phủ':''}`;
    if(!rows.length){host.style.height='120px';host.innerHTML=`<div class="depth-empty">${esc(depthPayload.message||'Chưa có dữ liệu độ sâu đã lưu. Vui lòng kiểm tra trạng thái bộ thu thập.')}</div>`;return;}
    host.style.height='278px';
    const width=Math.max(300,host.clientWidth),height=278,pad={l:66,r:16,t:26,b:38};
    const maxValue=Math.max(...rows.flatMap(r=>[Number(r.bid_depth_value)||0,Number(r.ask_depth_value)||0]));
    const divisor=maxValue>=1e9?1e9:1e6,unit=divisor===1e9?'tỷ đồng':'triệu đồng';
    const absMax=Math.max(.01,maxValue/divisor),rawStep=absMax/4,power=10**Math.floor(Math.log10(rawStep)),fraction=rawStep/power;
    const step=(fraction<=1?1:fraction<=2?2:fraction<=5?5:10)*power,axisMax=step*4,midY=pad.t+(height-pad.t-pad.b)/2,half=(height-pad.t-pad.b)/2;
    const x=i=>pad.l+(rows.length<=1?0:i*(width-pad.l-pad.r)/(rows.length-1));
    const y=v=>midY-(v/divisor/axisMax)*half;
    const axis=[];for(let i=-4;i<=4;i++){const val=i*axisMax/4,yy=midY-(val/axisMax)*half;axis.push(`<line x1="${pad.l}" y1="${yy}" x2="${width-pad.r}" y2="${yy}" class="depth-grid"/><text x="${pad.l-9}" y="${yy+4}" text-anchor="end">${num(Math.abs(val),1)}</text>`)}
    const splitSegments=(key,sign)=>{let seg=[],out=[];rows.forEach((r,i)=>{if(i&&new Date(rows[i].minute)-new Date(rows[i-1].minute)>90_000){if(seg.length)out.push(seg);seg=[];}const v=Number(r[key]);if(Number.isFinite(v))seg.push({x:x(i),y:y(sign*v),i});else if(seg.length){out.push(seg);seg=[];}});if(seg.length)out.push(seg);return out;};
    const area=(segments,base,cls)=>segments.map(seg=>`<path class="${cls}" d="M${seg[0].x},${base} ${seg.map(p=>`L${p.x},${p.y}`).join(' ')} L${seg.at(-1).x},${base} Z"/>`).join('');
    const paths=(segments,cls)=>segments.map(seg=>`<path class="${cls}" d="${seg.map((p,i)=>`${i?'L':'M'}${p.x},${p.y}`).join(' ')}"/>`).join('');
    const bid=splitSegments('bid_depth_value',1),ask=splitSegments('ask_depth_value',-1),labelCount=Math.min(7,rows.length),labelIndices=labelCount===1?[0]:Array.from({length:labelCount},(_,i)=>Math.round(i*(rows.length-1)/(labelCount-1)));
    host.innerHTML=`<svg class="depth-svg" width="${width}" height="${height}" style="width:${width}px;height:${height}px" aria-label="Độ sâu mua dương phía trên và độ sâu bán âm phía dưới trục không; số trên trục là độ lớn tuyệt đối"><g>${axis.join('')}<line x1="${pad.l}" y1="${midY}" x2="${width-pad.r}" y2="${midY}" class="depth-zero"/>${area(bid,midY,'depth-bid-area')}${area(ask,midY,'depth-ask-area')}${paths(bid,'depth-bid-line')}${paths(ask,'depth-ask-line')}${labelIndices.map((i,index)=>{const tick=depthPayload.session_count>1?formatVnDate(String(rows[i].minute).slice(0,10)):axisTickTime(rows[i],index===0?0:index===labelIndices.length-1?2:1);return `<text x="${x(i)}" y="${height-12}" text-anchor="${i===0?'start':i===rows.length-1?'end':'middle'}">${esc(tick)}</text>`;}).join('')}<text x="${pad.l}" y="18" class="depth-unit">${esc(unit)} · phía trên: mua (+) · phía dưới: bán (−)</text>${rows.flatMap((r,i)=>[r.bid_depth_value!=null&&Number.isFinite(Number(r.bid_depth_value))?`<circle class="depth-hit" data-i="${i}" data-side="bid" cx="${x(i)}" cy="${y(Number(r.bid_depth_value))}" r="7"/>`:'' ,r.ask_depth_value!=null&&Number.isFinite(Number(r.ask_depth_value))?`<circle class="depth-hit" data-i="${i}" data-side="ask" cx="${x(i)}" cy="${y(-Number(r.ask_depth_value))}" r="7"/>`:'' ]).join('')}</g></svg>`;
    host.querySelectorAll('.depth-hit').forEach(dot=>{const show=e=>{const i=Number(dot.dataset.i),r=rows[i],side=dot.dataset.side,rect=host.getBoundingClientRect();if(tip){tip.hidden=false;const sideLabel=side==='bid'?'Độ sâu mua (bid)':'Độ sâu bán (ask)',value=side==='bid'?r.bid_depth_value:r.ask_depth_value;tip.innerHTML=`<b>${esc(axisTime({minute:r.minute}))}</b><span>${sideLabel}: ${num(Number(value)/divisor,3)} ${esc(unit)}</span><span>${num(r.symbols_count,0)} mã tham gia</span>`;const cx=e.clientX??rect.left+x(i),cy=e.clientY??rect.top+(side==='bid'?y(Number(value)):y(-Number(value)));tip.style.left=`${Math.min(window.innerWidth-270,cx+12)}px`;tip.style.top=`${Math.max(8,cy-80)}px`;}};dot.addEventListener('pointerenter',show);dot.addEventListener('pointermove',show);dot.addEventListener('pointerleave',()=>{if(tip)tip.hidden=true;});});
  }
  async function loadOverviewDepth(windowMinutes){
    const host=document.querySelector('#depth-chart');if(!host)return;
    try{const range=overviewDateParams();depthPayload=await call(`/api/market-depth?window_minutes=${encodeURIComponent(windowMinutes)}&source=ssi${range.query}`);renderOverviewDepth();
      if(depthObserver)depthObserver.disconnect();depthObserver=new ResizeObserver(()=>renderOverviewDepth());depthObserver.observe(host);
    }catch(_){host.style.height='120px';host.innerHTML='<div class="depth-empty">Chưa tải được dữ liệu độ sâu. Vui lòng thử tải lại khối này.</div>';}
  }
  function renderOrderbookLadder(book, stamp, state){
    const target=document.querySelector('#orderbook');if(!target)return;
    if(!book){target.innerHTML='<div class="empty-state">Chưa có sổ lệnh đã lưu cho mã này.</div>';return;}
    const max=Math.max(1,...[1,2,3].flatMap(i=>[Number(book[`bid_volume_${i}`])||0,Number(book[`ask_volume_${i}`])||0]));
    const mid=(Number(book.bid_price_1)+Number(book.ask_price_1))/2;
    const used=new Set((book.__fills||[]).map(item=>`${item.side}:${Number(item.level_price)}`));
    target.innerHTML=`<div class="book-ladder"><div class="book-ladder-head"><span>Khối lượng mua</span><span>Giá mua</span><span>Giá bán</span><span>Khối lượng bán</span></div>${[1,2,3].map(i=>{const bq=Number(book[`bid_volume_${i}`])||0,aq=Number(book[`ask_volume_${i}`])||0,bp=Number(book[`bid_price_${i}`])||0,ap=Number(book[`ask_price_${i}`])||0;return `<div class="book-ladder-row"><div class="book-size bid-size${used.has(`sell:${bp}`)?' consumed':''}"><span style="width:${100*bq/max}%"></span><b>${num(bq,0)}</b></div><strong class="book-price bid-price${used.has(`sell:${bp}`)?' consumed-price':''}">${num(bp,0)}</strong><strong class="book-price ask-price${used.has(`buy:${ap}`)?' consumed-price':''}">${num(ap,0)}</strong><div class="book-size ask-size${used.has(`buy:${ap}`)?' consumed':''}"><span style="width:${100*aq/max}%"></span><b>${num(aq,0)}</b></div></div>`;}).join('')}<div class="book-ladder-summary"><span>Chênh lệch mua – bán: ${num(book.spread_bps,2)} bps · ${num(book.spread_ticks,0)} bước giá</span><span>Giá giữa: ${num(mid,2)}</span></div><small>${state==='outside_session'?'Sổ lệnh cuối phiên · ':''}${esc(stamp||'')}</small></div>`;
  }
  function renderCumulativeDepthCurve(host,curve){
    if(!host)return;const bid=curve?.bid||[],ask=curve?.ask||[],all=[...bid,...ask];
    if(!all.length){host.innerHTML='<div class="empty-state">Chưa có mức giá và khối lượng để lập đường độ sâu tích lũy.</div>';return;}
    const w=Math.max(300,host.clientWidth),h=Math.max(220,host.clientHeight||250),p={l:62,r:14,t:20,b:36};
    const minPrice=Math.min(...all.map(d=>d.price)),maxPrice=Math.max(...all.map(d=>d.price)),maxVolume=Math.max(...all.map(d=>d.cumulative_volume),1),mid=(Number(bid.at(-1)?.price)+Number(ask[0]?.price))/2;
    const x=v=>p.l+(Number(v)-minPrice)/(maxPrice-minPrice||1)*(w-p.l-p.r),y=v=>h-p.b-Number(v)/maxVolume*(h-p.t-p.b);
    const stepPath=points=>{if(!points.length)return '';let path=`M${x(points[0].price)},${y(points[0].cumulative_volume)}`;for(let i=1;i<points.length;i++)path+=` H${x(points[i].price)} V${y(points[i].cumulative_volume)}`;return path;};
    const grids=Array.from({length:5},(_,i)=>{const value=maxVolume*i/4,yy=y(value);return `<line x1="${p.l}" x2="${w-p.r}" y1="${yy}" y2="${yy}" class="depth-grid"/><text x="${p.l-8}" y="${yy+4}" text-anchor="end">${num(value,0)}</text>`}).join('');
    host.innerHTML=`<svg class="depth-svg cumulative-depth-svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="Độ sâu tích lũy theo giá; ${curve.levels_used} mức giá được dùng">${grids}<path d="${stepPath(bid)}" class="depth-bid-line"/><path d="${stepPath(ask)}" class="depth-ask-line"/><line x1="${x(mid)}" x2="${x(mid)}" y1="${p.t}" y2="${h-p.b}" class="depth-zero" stroke-dasharray="4 4"/><text x="${p.l}" y="13" class="depth-unit">Khối lượng tích lũy · cổ phiếu · ${curve.levels_used} mức</text><text x="${p.l}" y="${h-9}" text-anchor="start">${num(minPrice,0)}</text><text x="${x(mid)}" y="${h-9}" text-anchor="middle">${num(mid,0)} · giá giữa</text><text x="${w-p.r}" y="${h-9}" text-anchor="end">${num(maxPrice,0)}</text></svg>`;
  }
  const baseRankings=window.loadRankings,baseAlerts=window.loadAlerts,baseMethodology=window.loadMethodology;
  const enhanceWatchButtons=()=>window.watchAddQuickButtons?.();
  const enhancedRankings=async()=>{await baseRankings?.();enhanceWatchButtons();},enhancedAlerts=async(...args)=>{await baseAlerts?.(...args);enhanceWatchButtons();},enhancedMethodology=()=>baseMethodology?.();

  function setupMarketBackground() {
    if (document.querySelector('#market-background')) return;
    const video = document.createElement('video');
    video.id = 'market-video-background';
    video.setAttribute('aria-hidden', 'true');
    video.setAttribute('playsinline', '');
    video.muted = true; video.loop = true; video.autoplay = true; video.preload = 'auto';
    video.src = '/assets/assets/stock-market-background.mp4';
    document.body.prepend(video);
    const shade = document.createElement('div');
    shade.id = 'market-video-shade'; shade.setAttribute('aria-hidden', 'true');
    document.body.insertBefore(shade, video.nextSibling);
    const canvas = document.createElement('canvas');
    canvas.id = 'market-background';
    canvas.setAttribute('aria-hidden', 'true');
    document.body.prepend(canvas);
    const ctx = canvas.getContext('2d', { alpha: true });
    if (!ctx) return;
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    let width = 0, height = 0, scale = 1, frame = 0, previous = 0, lastCandle = 0;
    let candles = [];
    let sparkles = [];
    const resize = () => {
      scale = Math.min(window.devicePixelRatio || 1, 1.5);
      width = window.innerWidth; height = window.innerHeight;
      canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
      canvas.style.width = width + 'px'; canvas.style.height = height + 'px';
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      const count = Math.ceil(width / 29) + 4;
      candles = []; sparkles = [];
      let price = height * .46;
      for (let i = 0; i < count; i++) {
        const open = price;
        price = Math.max(height * .16, Math.min(height * .77, price + (Math.random() - .49) * Math.min(height * .055, 42)));
        const close = price;
        const wick = 9 + Math.random() * Math.min(height * .045, 32);
        candles.push({ open, close, high: Math.max(height * .09, Math.min(open, close) - wick * Math.random()), low: Math.min(height * .84, Math.max(open, close) + wick * Math.random()), volume: .2 + Math.random() * .8 });
      }
      for (let i = 0; i < Math.max(28, Math.round(width / 24)); i++) sparkles.push({ x: Math.random() * width, y: Math.random() * height, phase: Math.random() * Math.PI * 2, size: 1 + Math.random() * 2.4, drift: .15 + Math.random() * .55 });
      draw(performance.now());
    };
    const draw = (now) => {
      if (!width || !height) return;
      ctx.clearRect(0, 0, width, height);
      const wash = ctx.createLinearGradient(0, 0, width, height);
      wash.addColorStop(0, '#052c1c'); wash.addColorStop(.46, '#031b12'); wash.addColorStop(1, '#062718');
      ctx.fillStyle = wash; ctx.fillRect(0, 0, width, height);
      const glow = ctx.createRadialGradient(width * .72, height * .36, 5, width * .72, height * .36, Math.max(width, height) * .74);
      glow.addColorStop(0, 'rgba(0,153,75,.20)'); glow.addColorStop(.56, 'rgba(0,107,63,.075)'); glow.addColorStop(1, 'rgba(0,0,0,0)');
      ctx.fillStyle = glow; ctx.fillRect(0, 0, width, height);
      ctx.strokeStyle = 'rgba(139,221,169,.105)'; ctx.lineWidth = 1;
      const stepX = 82, stepY = 66, offset = (now * .021) % stepX;
      for (let x = -stepX + offset; x < width; x += stepX) { ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, height); ctx.stroke(); }
      for (let y = 22; y < height; y += stepY) { ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(width, y); ctx.stroke(); }
      const spacing = 29, bodyW = 12, shift = ((now % 900) / 900) * spacing;
      const line = [];
      candles.forEach((c, i) => {
        const x = width - (candles.length - i) * spacing + shift;
        const up = c.close <= c.open;
        const color = up ? '43,255,145' : '255,76,88';
        ctx.save(); ctx.shadowColor = up ? 'rgba(43,255,145,.72)' : 'rgba(255,76,88,.62)'; ctx.shadowBlur = 17;
        ctx.strokeStyle = `rgba(${color},.73)`; ctx.lineWidth = 1.8;
        ctx.beginPath(); ctx.moveTo(x + bodyW / 2, c.high); ctx.lineTo(x + bodyW / 2, c.low); ctx.stroke();
        const top = Math.min(c.open, c.close), bodyH = Math.max(4, Math.abs(c.close - c.open));
        const candleGlow = ctx.createLinearGradient(x, top, x + bodyW, top + bodyH);
        candleGlow.addColorStop(0, `rgba(${color},.48)`); candleGlow.addColorStop(.5, `rgba(${color},.88)`); candleGlow.addColorStop(1, `rgba(${color},.5)`);
        ctx.fillStyle = candleGlow; ctx.fillRect(x, top, bodyW, bodyH);
        ctx.restore();
        ctx.fillStyle = up ? 'rgba(43,255,145,.21)' : 'rgba(255,76,88,.19)'; ctx.fillRect(x + 2, height * .91 - c.volume * 31, bodyW - 4, c.volume * 31);
        line.push([x + bodyW / 2, c.close]);
      });
      if (line.length > 1) {
        ctx.beginPath(); line.forEach((point, i) => i ? ctx.lineTo(point[0], point[1]) : ctx.moveTo(point[0], point[1]));
        ctx.save(); ctx.shadowColor = 'rgba(65,255,157,.92)'; ctx.shadowBlur = 23;
        ctx.strokeStyle = 'rgba(90,255,170,.54)'; ctx.lineWidth = 3.2; ctx.stroke(); ctx.restore();
      }
      sparkles.forEach((s) => {
        const twinkle = .18 + (Math.sin(now * .0032 * s.drift + s.phase) + 1) * .31;
        const x = (s.x + now * .006 * s.drift) % width, y = s.y + Math.sin(now * .0008 + s.phase) * 7, r = s.size * (.72 + twinkle);
        ctx.save(); ctx.globalAlpha = twinkle; ctx.shadowColor = 'rgba(175,255,212,.95)'; ctx.shadowBlur = 12;
        ctx.fillStyle = s.phase > 3 ? '#d7ffd0' : '#8dffc2';
        ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
        if (twinkle > .39) { ctx.strokeStyle = 'rgba(205,255,220,.72)'; ctx.lineWidth = .75; ctx.beginPath(); ctx.moveTo(x-r*2.3,y); ctx.lineTo(x+r*2.3,y); ctx.moveTo(x,y-r*2.3); ctx.lineTo(x,y+r*2.3); ctx.stroke(); }
        ctx.restore();
      });
    };
    const animate = (now) => {
      if (reduced.matches || document.visibilityState !== 'visible') { draw(now); return; }
      if (now - previous > 32) { draw(now); previous = now; }
      if (now - lastCandle > 900) {
        lastCandle = now;
        const tail = candles[candles.length - 1] || { close: height * .5 };
        const open = tail.close;
        const close = Math.max(height * .16, Math.min(height * .77, open + (Math.random() - .49) * Math.min(height * .055, 42)));
        const wick = 9 + Math.random() * Math.min(height * .045, 32);
        candles.push({ open, close, high: Math.max(height * .09, Math.min(open, close) - Math.random() * wick), low: Math.min(height * .84, Math.max(open, close) + Math.random() * wick), volume: .2 + Math.random() * .8 });
        candles.shift();
      }
      frame = requestAnimationFrame(animate);
    };
    resize(); window.addEventListener('resize', resize, { passive: true });
    video.addEventListener('playing', () => {
      if (!reduced.matches) { document.body.classList.add('video-background-active'); cancelAnimationFrame(frame); ctx.clearRect(0, 0, width, height); }
    });
    video.addEventListener('error', () => {
      document.body.classList.remove('video-background-active');
      if (!reduced.matches && document.visibilityState === 'visible') frame = requestAnimationFrame(animate);
    });
    document.addEventListener('visibilitychange', () => {
      cancelAnimationFrame(frame);
      if (document.visibilityState === 'visible' && !reduced.matches) video.play().catch(() => { frame = requestAnimationFrame(animate); });
      else if (document.visibilityState === 'visible') draw(performance.now());
    });
    reduced.addEventListener?.('change', () => {
      cancelAnimationFrame(frame);
      if (reduced.matches) { video.pause(); document.body.classList.remove('video-background-active'); draw(performance.now()); }
      else if (document.visibilityState === 'visible') video.play().catch(() => { frame = requestAnimationFrame(animate); });
    });
    if (!reduced.matches) video.play().catch(() => { frame = requestAnimationFrame(animate); });
  }

  function addBrandAndNavigation() {
    const header=document.querySelector('.app-header');
    if(header && !document.querySelector('.project-masthead')) header.insertAdjacentHTML('afterbegin','<div class="project-masthead"><span class="masthead-kicker">VI CẤU TRÚC THỊ TRƯỜNG · BẢNG ĐIỀU KHIỂN NGHIÊN CỨU</span><h1>Stock Liquidity Intelligence</h1><p>Đo lường và diễn giải thanh khoản niêm yết theo thời gian</p></div>');
    const nav=document.querySelector('.main-nav');
    if(nav && !document.querySelector('.swipe-hint')) nav.insertAdjacentHTML('afterend','<div class="swipe-hint">Vuốt ngang hoặc dùng phím Alt + ← / → để chuyển tab</div>');
    const overview=document.querySelector('#view-overview .content-grid');
    const overviewFilters=document.querySelector('#view-overview .filter-bar');
    if(overviewFilters&&!document.querySelector('#overview-history-controls')){overviewFilters.insertAdjacentHTML('afterend','<div id="overview-history-controls" class="history-range-controls overview-history-controls"><strong>Khoảng lịch sử SSI (OHLC và bar trong phiên)</strong><div class="history-range-presets" role="group" aria-label="Khoảng lịch sử tổng quan"><button type="button" data-overview-range="1m">1 tháng</button><button type="button" data-overview-range="3m">3 tháng</button><button type="button" data-overview-range="6m">6 tháng</button><button type="button" data-overview-range="all">Tất cả</button></div><label>Từ ngày<input id="overview-history-from" type="date"></label><label>Đến ngày<input id="overview-history-to" type="date"></label><button id="overview-history-apply" class="button button-primary" type="button">Áp dụng</button><small id="overview-history-error" class="filter-error" role="alert" hidden></small></div>');const params=new URLSearchParams(location.search),preset=normalizeOverviewPreset(params.get('preset'));if(['1m','3m','6m','all'].includes(preset))setOverviewRange(preset);else if(params.get('from')||params.get('to')){document.querySelector('#overview-history-from').value=params.get('from')||'';document.querySelector('#overview-history-to').value=params.get('to')||'';overviewDataStore.filters.preset='custom';}else setOverviewRange('1m');}
    if(overview && !document.querySelector('#market-turnover-chart')) overview.insertAdjacentHTML('afterbegin',
      '<article class="panel panel-wide market-turnover-panel"><div class="panel-heading"><div><h2>Bối cảnh thị trường · OHLC ngày SSI</h2><p>Dữ liệu giá theo ngày, không phải thanh khoản sổ lệnh.</p></div><div class="turnover-metric-switch" role="group" aria-label="Chỉ số hiển thị"><button type="button" class="active" aria-pressed="true" data-turnover-metric="value">Giá trị khớp</button><button type="button" aria-pressed="false" data-turnover-metric="volume">Khối lượng</button></div></div><div id="market-turnover-kpis" class="turnover-kpis"></div><div class="turnover-legend"><span><i class="normal"></i>Phiên thường</span><span><i class="spike"></i>Phiên đột biến (từ 1,4 lần trung bình 20 phiên)</span><span><i class="average"></i>Trung bình 20 phiên</span></div><div id="market-turnover-chart" class="market-turnover-chart" role="img" aria-label="Biểu đồ giá trị giao dịch toàn thị trường theo phiên"></div><p id="market-turnover-note" class="analysis-note"></p></article>');
    if(overview && !document.querySelector('#historical-price-chart')) overview.insertAdjacentHTML('afterbegin',
      '<article class="panel panel-wide"><div class="panel-heading"><div><h2>Danh mục cổ phiếu và giá lịch sử (SSI)</h2><p>Cổ phiếu thường trên HOSE, HNX và UPCoM · lịch sử đồng bộ dần từ SSI</p></div><span class="chip">SSI · DỮ LIỆU THẬT</span></div><div class="history-controls"><label>Tìm mã hoặc tên doanh nghiệp<input id="history-symbol-search" type="search" placeholder="Ví dụ: FPT hoặc ABB"></label><span id="history-progress" class="chip">Đang tải danh mục SSI…</span></div><div id="historical-price-summary" class="historical-price-summary"></div><section class="live-trades-section"><div class="panel-heading"><div><h3>Khớp lệnh mới nhất từ SSI FastConnect</h3><p>Giá và khối lượng giao dịch đã khớp (TradeMessage), tách biệt với lượng đang chờ mua/bán trong sổ lệnh.</p></div><span id="live-trades-updated" class="chip">Đang chờ dữ liệu khớp lệnh…</span></div><div id="live-trade-tape"></div><p class="analysis-note">“KL khớp” là khối lượng của message giao dịch; “Tổng KL SSI báo” là trường total_volume của SSI. Dữ liệu cập nhật theo message, không phải OHLC lịch sử.</p></section><h3 id="history-chart-heading" class="history-chart-heading">Độ rộng thị trường SSI · số mã tăng / giảm theo phiên</h3><div id="historical-price-chart" class="chart-box"></div><div class="history-pagination"><button id="history-prev" class="button button-secondary" type="button">← Trang trước</button><span id="history-page-status"></span><button id="history-next" class="button button-secondary" type="button">Trang sau →</button></div><p id="historical-price-note" class="analysis-note">Giá đóng cửa là giá lịch sử SSI, không phải báo giá realtime hay dữ liệu sổ lệnh.</p></article>');
    const historyHeading=document.querySelector('#history-chart-heading');
    if(historyHeading&&!document.querySelector('#history-range-controls'))historyHeading.insertAdjacentHTML('afterend','<div id="history-range-controls" class="history-range-controls" hidden><div class="history-range-presets" role="group" aria-label="Khoảng thời gian lịch sử OHLC"><button type="button" data-history-range="1m">1 tháng</button><button type="button" data-history-range="3m">3 tháng</button><button type="button" data-history-range="6m">6 tháng</button><button type="button" data-history-range="all">Tất cả</button></div><label>Từ ngày<input id="history-from-date" type="date"></label><label>Đến ngày<input id="history-to-date" type="date"></label><button id="history-apply-dates" class="button button-secondary" type="button">Áp dụng</button></div>');
    const overviewKpis=document.querySelector('#view-overview .kpi-grid');if(overviewKpis&&!document.querySelector('#overview-data-context'))overviewKpis.insertAdjacentHTML('afterend','<div id="overview-data-context" class="data-context" role="status"></div>');
    if(overview && !document.querySelector('#imbalance-chart')) overview.insertAdjacentHTML('beforeend',
      '<article class="panel"><div class="panel-heading"><div><h2>Cân bằng khối lượng mua / bán (bid / ask)</h2><p>Mất cân bằng khối lượng chờ (queue imbalance) từ −1 đến +1</p></div></div><div id="imbalance-chart" class="chart-box chart-short"></div><p id="imbalance-reading" class="analysis-note"></p></article>'+
      '<article class="panel"><div class="panel-heading"><div><h2>Top 10 mã theo giá trị giao dịch</h2><p>OHLC ngày SSI · sắp xếp theo giá trị khớp</p></div></div><div id="spread-compare" class="chart-box chart-short"></div><p id="turnover-chart-note" class="analysis-note"></p></article>'+
      '<article class="panel panel-wide"><div class="panel-heading"><div><h2>5 mã tăng và 5 mã giảm mạnh</h2><p>Trong nhóm mã dẫn đầu theo giá trị giao dịch OHLC ngày SSI</p></div></div><div id="coverage-chart" class="chart-box chart-short"></div><p id="coverage-note" class="analysis-note"></p><p id="overview-analysis" class="analysis-note"></p></article>');
    if(overview && !document.querySelector('#liquidity-map')) overview.insertAdjacentHTML('beforeend',
      '<article class="panel panel-wide liquidity-map-panel"><div class="panel-heading"><div><h2>Bản đồ thanh khoản</h2><p>So sánh spread và độ sâu sổ lệnh theo mã cổ phiếu</p></div><span class="chip">SO SÁNH TƯƠNG ĐỐI</span></div><div class="liquidity-map-toolbar"><label>Tìm mã cổ phiếu<input id="liquidity-map-search" type="search" placeholder="Nhập mã, ví dụ FPT" autocomplete="off"></label><button type="button" class="button button-secondary" data-map-zoom>Phóng to</button><button type="button" class="button button-secondary" data-map-reset>Đặt lại</button><details class="liquidity-map-info"><summary aria-label="Thông tin biểu đồ" title="Cách đọc biểu đồ">ⓘ</summary><p>Spread thấp hơn thường thuận lợi hơn về chi phí theo báo giá; độ sâu cao hơn cho thấy giá trị niêm yết ba mức đầu lớn hơn. Vạch đứt là trung vị của nhóm đang chọn. Đây là so sánh tương đối, không phải dự báo hay điểm thanh khoản tổng hợp. Giá trong chú giải là giá khớp gần nhất SSI lưu được.</p></details></div><div id="liquidity-map-legend" class="liquidity-map-legend" aria-label="Lọc nhóm mã"></div><div class="liquidity-map-layout"><div id="liquidity-map" class="chart-box liquidity-map-canvas"></div><aside id="liquidity-map-highlights" class="liquidity-map-highlights"></aside></div><p id="liquidity-map-note" class="analysis-note liquidity-map-note"></p><div id="liquidity-map-modal" class="liquidity-map-modal" hidden><section class="liquidity-map-modal-card" role="dialog" aria-modal="true" aria-label="Bản đồ thanh khoản phóng to"><div class="liquidity-map-modal-heading"><h2>Bản đồ thanh khoản</h2><button type="button" class="button button-secondary" data-map-close>Đóng</button></div><div id="liquidity-map-expanded" class="chart-box liquidity-map-canvas"></div></section></div></article>'+ 
      '<article class="panel panel-wide"><div class="panel-heading"><div><h2>Khối lượng giao dịch toàn thị trường</h2><p>Tổng khối lượng khớp lệnh theo ngày từ OHLC SSI</p></div></div><div id="intraday-profile" class="chart-box"></div><p id="intraday-profile-note" class="analysis-note"></p></article>');    if(overview&&!document.querySelector('#overview-market-data')){
      const details=document.createElement('details');details.id='overview-market-data';details.className='overview-market-data';
      details.innerHTML='<summary>Dữ liệu giá lịch sử và giao dịch khớp từ SSI</summary><div class="overview-market-data-content"></div>';
      const content=details.querySelector('.overview-market-data-content');
      const panels=['#historical-price-chart','#spread-compare','#coverage-chart'].map(id=>document.querySelector(id)?.closest('.panel')).filter(Boolean);
      if(panels.length){overview.after(details);for(const panel of panels)content.appendChild(panel);}
      details.addEventListener('toggle',()=>{if(details.open)historicalPriceChart(document.querySelector('#overview-symbol')?.value||'__all__',10000);});
    }
    const detail=document.querySelector('#view-detail .kpi-grid');
    if(detail && !document.querySelector('#company-profile')) detail.insertAdjacentHTML('beforebegin','<article id="company-profile" class="company-profile"><div><span class="eyebrow">DOANH NGHIỆP ĐANG CHỌN</span><h2>—</h2><p></p></div><span class="company-sector"></span><button type="button" class="button button-secondary" data-quick-watch-current>＋ Thêm vào danh mục</button></article><article id="detail-analysis" class="analysis-panel"></article>');
    const detailGrid=document.querySelector('#view-detail .content-grid');
    if(detailGrid && !document.querySelector('#bookwalk-panel')) detailGrid.insertAdjacentHTML('beforeend','<article id="bookwalk-panel" class="panel detail-bookwalk-panel"><div class="panel-heading"><div><h2>Mô phỏng đi bộ sổ lệnh</h2><p>Ước lượng tĩnh trên cùng ảnh chụp dữ liệu (snapshot), không gửi lệnh</p></div></div><div class="bookwalk-layout"><div><div class="filter-bar bookwalk-controls"><label>Chiều<select id="bookwalk-side"><option value="buy">Mua theo giá bán (ask)</option><option value="sell">Bán theo giá mua (bid)</option></select></label><label>Khối lượng (cổ phiếu)<input id="bookwalk-quantity" type="number" min="100" step="100" value="10000"></label><label>Trượt giá tối đa (điểm cơ bản, bps)<input id="bookwalk-slippage" type="number" min="0" step="1" value="10"></label><button id="bookwalk-run" class="button button-primary" type="button">Mô phỏng</button></div><div id="bookwalk-result" class="analysis-note">Nhập khối lượng rồi chọn Mô phỏng. Tỷ lệ chỉ đo phần lượng được sổ lệnh ba mức giá đầu đáp ứng trong ảnh chụp dữ liệu (snapshot), không phải xác suất khớp lệnh thực tế.</div></div><aside class="bookwalk-formula"><h3>Cách tính trên sổ lệnh đang hiển thị</h3><p><b>Giá giữa</b> = (giá mua mức 1 + giá bán mức 1) ÷ 2</p><p><b>VWAP</b> = tổng (giá × lượng lấy ở từng mức) ÷ tổng lượng khớp</p><p><b>Trượt giá</b> = chênh VWAP với giá giữa ÷ giá giữa × 10.000 bps</p><p><b>Tỷ lệ khớp mô phỏng</b> = lượng đáp ứng ÷ lượng yêu cầu</p><small>Mua đi qua ask mức 1→3; bán đi qua bid mức 1→3. Phương pháp dùng sổ lệnh hiển thị để ước lượng tác động giá; xem <a href="https://arxiv.org/abs/1011.6402" target="_blank" rel="noreferrer">Cont, Kukanov &amp; Stoikov (2014)</a>. Đây là phép tính tĩnh, không bao gồm lệnh ẩn, hủy lệnh, ưu tiên hàng đợi hay phản ứng thị trường.</small></aside></div></article>');
    const bookwalkPanel=document.querySelector('#bookwalk-panel');
    const bookwalkControls=bookwalkPanel?.querySelector('.bookwalk-controls');bookwalkControls?.classList.remove('filter-bar');
    const formula=bookwalkPanel?.querySelector('aside.bookwalk-formula');
    if(formula){const disclosure=document.createElement('details'),heading=formula.querySelector('h3');disclosure.className='bookwalk-formula';disclosure.innerHTML=`<summary>${esc(heading?.textContent||'Cách tính')}</summary>${[...formula.children].filter(child=>child!==heading).map(child=>child.outerHTML).join('')}`;formula.replaceWith(disclosure);}
    const detailSpreadPanel=document.querySelector('#detail-spread-chart')?.closest('.panel');
    if(detailSpreadPanel&&!document.querySelector('#detail-history-range-controls')){detailSpreadPanel.querySelector('.panel-heading')?.insertAdjacentHTML('afterend','<div id="detail-history-range-controls" class="history-range-controls"><div class="history-range-presets" role="group" aria-label="Khoảng lịch sử spread"><button type="button" data-detail-range="1y">1 năm</button><button type="button" data-detail-range="3y">3 năm</button><button type="button" data-detail-range="5y">5 năm</button><button type="button" data-detail-range="all">Tất cả</button></div><label>Từ ngày<input id="detail-history-from" type="date"></label><label>Đến ngày<input id="detail-history-to" type="date"></label><button id="detail-history-apply" class="button button-secondary" type="button">Áp dụng</button><small>Spread chỉ có trong giai đoạn SSI đã lưu quote; không suy ra từ OHLC ngày.</small></div>');setDetailRange('1y');}
    const methodology=document.querySelector('#view-methodology .content-grid');
    if(methodology && !document.querySelector('#quality-summary')){const quality=document.createElement('article');quality.className='panel panel-wide methodology-quality';quality.innerHTML='<div class="panel-heading"><div><p class="eyebrow">KIỂM TRA DỮ LIỆU</p><h2>Chất lượng và lịch sử bộ thu thập dữ liệu (collector)</h2><p>Đếm ảnh chụp dữ liệu (snapshot) đã lưu và các lần chạy nhận dữ liệu.</p></div><span class="method-icon" aria-hidden="true">◉</span></div><div id="quality-summary" class="kpi-grid"></div><div id="collector-runs"></div>';const methodPanel=methodology.querySelector('#method-content')?.closest('.panel');if(methodPanel)methodPanel.before(quality);else methodology.appendChild(quality);}
    const methodHeading=document.querySelector('#view-methodology .page-heading');
    if(methodHeading&&!document.querySelector('#methodology-hero'))methodHeading.insertAdjacentHTML('afterend','<article id="methodology-hero" class="methodology-hero"><div class="methodology-hero-mark" aria-hidden="true">↗</div><div><p class="eyebrow">NGUỒN DỮ LIỆU</p><h2>Biết dữ liệu đến từ đâu trước khi đọc chỉ số</h2><p id="methodology-source">SSI FastConnect · kênh báo giá (quote) → máy chủ kiểm tra và lưu ảnh chụp dữ liệu (snapshot) → bảng tổng quan tính toán, biểu đồ và cảnh báo.</p><div class="method-pills"><span>Mô phỏng (DEMO) = dữ liệu tổng hợp</span><span>Trực tiếp (LIVE) = bộ thu thập đã nhận dữ liệu</span><span>Giao diện không tự mở kết nối WebSocket</span></div></div></article>');
    methodology?.classList.add('methodology-grid');
    const alerts=document.querySelector('#view-alerts .page-heading');
    if(alerts && !document.querySelector('#alert-explainer')) alerts.insertAdjacentHTML('afterend','<div id="alert-explainer" class="alert-explainer"><h2>Diễn giải giám sát</h2><p>Cảnh báo được cập nhật cùng ảnh chụp dữ liệu mới (snapshot). Mỗi cảnh báo cần đọc kèm mã, chỉ số, ngưỡng tham chiếu, số mẫu trong mốc tham chiếu (baseline) và thời điểm. Đây là tín hiệu bất thường thống kê, không phải khuyến nghị giao dịch.</p><div id="alert-live-counts"></div></div>');
  }

  function formatVnDate(raw){const value=String(raw||'').replaceAll('/','-').slice(0,10),d=new Date(value+'T00:00:00+07:00');return Number.isNaN(d.valueOf())?String(raw||'—'):d.toLocaleDateString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit',year:'numeric'});}
  function formatVnTime(raw,seconds=false){const d=new Date(raw);return Number.isNaN(d.valueOf())?String(raw||'—').slice(11,16):d.toLocaleTimeString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit',...(seconds?{second:'2-digit'}:{})});}
  function axisTime(row){const raw=row?.trading_date||row?.minute||row?.source_ts_vn||row?.received_at_vn||row?.source_timestamp||row?.received_at_utc||row?.timestamp;if(!raw)return '—';if(row?.trading_date)return formatVnDate(raw);return `${formatVnDate(String(raw).slice(0,10))} ${formatVnTime(raw,true)}`;}
  function axisTickTime(row,index=0){const raw=row?.trading_date||row?.minute||row?.source_ts_vn||row?.received_at_vn||row?.source_timestamp||row?.received_at_utc||row?.timestamp;if(!raw)return '—';if(row?.trading_date)return formatVnDate(raw);const time=formatVnTime(raw),date=formatVnDate(String(raw).slice(0,10));return index===2?`${time} · ${date}`:index===0?`${date} ${time}`:time;}
  function niceTicks(min,max,count=4){
    if(!Number.isFinite(min)||!Number.isFinite(max))return [];
    if(min===max){const pad=Math.abs(max)*.05||1;min-=pad;max+=pad;}
    const raw=(max-min)/count,power=Math.pow(10,Math.floor(Math.log10(raw))),fraction=raw/power;
    const unit=fraction<=1?1:fraction<=2?2:fraction<=5?5:10,step=unit*power;
    const low=Math.floor(min/step)*step,high=Math.ceil(max/step)*step,ticks=[];
    for(let value=low;value<=high+step*.0001;value+=step)ticks.push(Number(value.toPrecision(12)));
    return ticks;
  }
  function axisNumber(value,key){
    if(key.includes('imbalance'))return num(value,2);
    if(key.includes('depth')&&Math.abs(value)>=1e9)return `${num(value/1e9,1)} tỷ`;
    if(key.includes('depth')&&Math.abs(value)>=1e6)return `${num(value/1e6,1)} triệu`;
    if(key==='count'&&Math.abs(value)>=1e6)return num(value/1e6,1)+' tr';
    return num(value,key.includes('spread')?1:0);
  }
  function aggregateByMinute(rows,key){
    const minutes=new Map();
    for(const row of rows){
      const raw=row.received_at_vn||row.source_ts_vn||row.received_at_utc;
      const minute=raw?String(raw).slice(0,16):null,value=Number(row[key]);
      if(!minute||!Number.isFinite(value))continue;
      if(!minutes.has(minute))minutes.set(minute,new Map());
      const bySymbol=minutes.get(minute),ticker=row.symbol||'UNKNOWN';
      if(!bySymbol.has(ticker))bySymbol.set(ticker,[]);
      bySymbol.get(ticker).push(value);
    }
    const median=values=>{const sorted=values.slice().sort((a,b)=>a-b),m=sorted.length>>1;return sorted.length%2?sorted[m]:(sorted[m-1]+sorted[m])/2;};
    return [...minutes.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([minute,bySymbol])=>{
      const perTicker=[...bySymbol.values()].map(median);
      return {minute:minute+':00',[key]:median(perTicker),sample_count:perTicker.length};
    });
  }
  function setChartNote(id,text){
    const node=document.querySelector('#'+id);if(!node)return;
    const noteId=id+'-definition';let note=document.getElementById(noteId);
    if(!note){note=document.createElement('p');note.id=noteId;note.className='analysis-note chart-definition';node.insertAdjacentElement('afterend',note);}
    note.textContent=text;
  }
  function renderTradeTape(data){
    const node=document.querySelector('#live-trade-tape'),stamp=document.querySelector('#live-trades-updated');
    if(!node)return;
    const trades=data?.trades||[];
    if(stamp)stamp.textContent=trades.length?`${num(trades.length,0)} khớp lệnh gần nhất · mới nhất ${window.fmtTime(trades[0].received_at_vn)}`:'SSI chưa gửi TradeMessage';
    node.innerHTML=window.table(['Mã','Giá khớp (đồng/cổ phiếu)','KL khớp (cổ phiếu)','Tổng KL SSI báo','Bên (SSI)','Thời điểm nguồn'],trades.map(t=>[
      `<b>${esc(t.symbol)}</b>`,num(t.price,0),num(t.quantity,0),num(t.total_volume,0),esc(t.side||'—'),window.fmtTime(t.source_ts_vn||t.received_at_vn)
    ]),`Chưa nhận được TradeMessage${data?.symbol?` cho ${esc(data.symbol)}`:''} từ SSI. Bảng này không thay bằng OHLC lịch sử.`);
  }
  function lineChart(node, series, key, label) {
    if(!node)return;
    const entries=series.map(s=>({name:s.name,rows:(s.rows||[]).filter(r=>r[key]!=null&&Number.isFinite(Number(r[key]))),color:s.color})).filter(s=>s.rows.length>0);
    if(!entries.length){node.innerHTML='<div class="empty-state"><b>Chưa có snapshot quote SSI để vẽ chỉ số sổ lệnh.</b><br>Biểu đồ này cần giá và khối lượng bid/ask. OHLC lịch sử được hiển thị ở các biểu đồ giao dịch bên dưới, không dùng để thay thế.</div>';return;}
    const W=900,H=286,L=92,R=18,T=24,B=60,vals=entries.flatMap(e=>e.rows.map(r=>Number(r[key]))),lo=Math.min(...vals),hi=Math.max(...vals),ticks=niceTicks(lo,hi,4),axisLo=ticks[0],axisHi=ticks[ticks.length-1],span=axisHi-axisLo||1;
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">`,hover=[];
    ticks.forEach(v=>{const y=T+(axisHi-v)/span*(H-T-B);svg+=`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#e2ebe4"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#526b5d" font-size="11">${axisNumber(v,key)}</text>`;});
    const unit=key.includes('spread')?'điểm cơ bản (bps)':key.includes('depth')?'đồng × cổ phiếu':key==='close_price'?'đồng/cổ phiếu':key==='count'?'mã cổ phiếu':key==='value'?'cổ phiếu':key.includes('imbalance')?'tỷ lệ':'';
    entries.forEach((e,seriesIndex)=>{const points=e.rows.map((r,i)=>({r,v:Number(r[key]),x:L+i/(e.rows.length-1)*(W-L-R)}));points.forEach(p=>p.y=T+(axisHi-p.v)/span*(H-T-B));const path=points.map((p,i)=>`${i?'L':'M'}${p.x},${p.y}`).join(' ');svg+=`<g class="chart-series" data-series-index="${seriesIndex}"><path d="${path}" fill="none" stroke="${e.color}" stroke-width="2.7" stroke-linecap="round"/>`;for(const p of points){const sample=p.r.sample_count?` · ${num(p.r.sample_count,0)} mã`:'';const tip=`${e.name} · ${axisTime(p.r)} · ${label}: ${num(p.v,key.includes('imbalance')?3:0)} ${unit}${sample}`;hover.push({x:p.x,y:p.y,text:tip,seriesIndex,visible:true});svg+=`<circle class="chart-hover-point" cx="${p.x}" cy="${p.y}" r="8" fill="transparent" tabindex="0"><title>${esc(tip)}</title></circle>`;}svg+='</g>';});
    const base=entries[0].rows,tickCount=Math.min(5,base.length),multiSession=new Set(base.map(row=>String(row.minute||'').slice(0,10))).size>1;for(let tick=0;tick<tickCount;tick++){const idx=base.length===1?0:Math.round(tick*(base.length-1)/(tickCount-1)),x=L+(base.length===1?.5:idx/(base.length-1))*(W-L-R),anchor=tick===0?'start':tick===tickCount-1?'end':'middle',raw=base[idx]?.minute||base[idx]?.trading_date,labelText=multiSession?formatVnDate(String(raw).slice(0,10)):base[idx]?.minute&&tick===tickCount-1?`${formatVnTime(raw)} · ${formatVnDate(String(raw).slice(0,10))}`:axisTickTime(base[idx],tick===0?0:1);svg+=`<line x1="${x}" y1="${H-B}" x2="${x}" y2="${H-B+4}" stroke="#9caf9f"/><text x="${x}" y="${H-B+17}" text-anchor="${anchor}" fill="#40594a" font-size="10">${esc(labelText)}</text>`;}
    const firstStamp=base[0]?.minute||base[0]?.trading_date,sessionDate=firstStamp?formatVnDate(String(firstStamp).slice(0,10)):'';const timeContext=multiSession?'Thời gian · các phiên giao dịch · giờ Việt Nam':base[0]?.minute?`Thời gian · phiên ${sessionDate} · giờ Việt Nam`:'Thời gian · giờ Việt Nam';
    svg+=`<text x="${L}" y="12" fill="#40594a" font-size="10">${esc(label)} · đơn vị: ${unit||'giá trị'}</text><text x="${L+(W-L-R)/2}" y="${H-5}" text-anchor="middle" fill="#40594a" font-size="10">${esc(timeContext)}</text></svg><div class="chart-legend">${entries.map((e,i)=>`<button type="button" data-series-index="${i}" aria-pressed="true" aria-label="Bật hoặc tắt ${esc(e.name)}"><i style="background:${e.color}"></i>${esc(e.name)}</button>`).join('')}</div>`;node.innerHTML=svg;if(window.attachChartTooltip)window.attachChartTooltip(node,node.querySelector('svg'),hover,W,H);node.querySelectorAll('.chart-legend button').forEach(button=>button.addEventListener('click',()=>{const index=Number(button.dataset.seriesIndex),visible=button.getAttribute('aria-pressed')==='true';button.setAttribute('aria-pressed',String(!visible));const group=node.querySelector(`.chart-series[data-series-index="${index}"]`);if(group)group.style.display=visible?'none':'';hover.forEach(point=>{if(point.seriesIndex===index)point.visible=!visible;});}));
  }
  function advanceDeclineChart(node,rows){
    if(!node)return;
    const data=(rows||[]).map(r=>({...r,net:Number(r.advancing||0)-Number(r.declining||0)})).filter(r=>Number.isFinite(r.net));
    if(!data.length){node.innerHTML='<div class="empty-state">Chưa có dữ liệu OHLC SSI để so sánh số mã tăng và giảm.</div>';return;}
    const W=900,H=286,L=76,R=18,T=25,B=60,peak=Math.max(1,...data.map(r=>Math.abs(r.net))),ticks=niceTicks(-peak,peak,4),lo=Math.min(0,ticks[0]??-peak),hi=Math.max(0,ticks.at(-1)??peak),span=hi-lo||1,plotW=W-L-R,plotH=H-T-B,zeroY=T+(hi/span)*plotH,slot=plotW/data.length,barW=Math.max(2,Math.min(14,slot*.62)),hover=[];
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Biểu đồ cột chênh lệch số mã tăng trừ số mã giảm theo phiên">`;
    for(const value of ticks){const y=T+(hi-value)/span*plotH,zero=value===0;svg+=`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="${zero?'#496453':'#e2ebe4'}" stroke-width="${zero?2:1}"/><text x="${L-10}" y="${y+4}" text-anchor="end" fill="#526b5d" font-size="11">${num(value,0)}</text>`;}
    data.forEach((r,i)=>{const cx=L+(i+.5)*slot,y=T+(hi-r.net)/span*plotH,top=Math.min(zeroY,y),height=Math.max(1,Math.abs(zeroY-y)),color=r.net<0?'#d93632':'#008a45',date=String(r.trading_date||'').slice(5).replace('-','/'),label=r.net>0?`+${num(r.net,0)}`:num(r.net,0),tip=`${r.trading_date||'Phiên'} · Chênh lệch tăng − giảm: ${label} mã · ${num(r.advancing,0)} mã tăng, ${num(r.declining,0)} mã giảm`;svg+=`<rect x="${cx-barW/2}" y="${top}" width="${barW}" height="${height}" rx="2" fill="${color}" opacity=".9"><title>${esc(tip)}</title></rect><circle class="chart-hover-point" cx="${cx}" cy="${y}" r="9" fill="transparent" tabindex="0"><title>${esc(tip)}</title></circle>`;hover.push({x:cx,y,text:tip,seriesIndex:0,visible:true});});
    const labels=Array.from(new Set([0,Math.round((data.length-1)/4),Math.round((data.length-1)/2),Math.round((data.length-1)*3/4),data.length-1]));labels.forEach((idx,j)=>{const x=L+(idx+.5)*slot,anchor=j===0?'start':j===labels.length-1?'end':'middle',date=formatVnDate(data[idx].trading_date);svg+=`<line x1="${x}" y1="${H-B}" x2="${x}" y2="${H-B+4}" stroke="#9caf9f"/><text x="${x}" y="${H-B+17}" text-anchor="${anchor}" fill="#40594a" font-size="10">${esc(date)}</text>`;});
    svg+=`<text x="${L}" y="12" fill="#40594a" font-size="10">Số mã tăng trừ số mã giảm · đơn vị: mã cổ phiếu</text><text x="${L+plotW/2}" y="${H-5}" text-anchor="middle" fill="#40594a" font-size="10">Thời gian · giờ Việt Nam</text></svg><div class="chart-legend chart-legend-static"><span><i style="background:#008a45"></i>Mã tăng nhiều hơn</span><span><i style="background:#d93632"></i>Mã giảm nhiều hơn</span></div>`;
    node.innerHTML=svg;if(window.attachChartTooltip)window.attachChartTooltip(node,node.querySelector('svg'),hover,W,H);
  }
  function bars(node, items, labelKey, valueKey, color='#008A45', format=(v)=>num(v)) {
    if(!node)return;if(!items.length){node.innerHTML='<div class="empty-state">Chưa có dữ liệu.</div>';return;}
    const values=items.map(x=>Number(x[valueKey])||0),signed=valueKey==='change_pct',minValue=signed?Math.min(...values,0):0,maxValue=Math.max(...values,1),ticks=niceTicks(minValue,maxValue,4),axisMin=ticks[0]??minValue,axisMax=ticks[ticks.length-1]??maxValue,range=axisMax-axisMin||1,zeroPct=(0-axisMin)/range*100,unit=valueKey.includes('spread')?'điểm cơ bản (bps)':valueKey==='count'?'mã':valueKey==='turnover_bil'?'tỷ đồng':valueKey==='change_pct'?'% thay đổi':'giá trị';
    const tickText=value=>`${num(value,value<10?1:0)}${signed?'%':''}`;
    const rows=items.map(item=>{const value=Number(item[valueKey])||0,width=Math.max(1,Math.abs(value)/range*100),left=signed?(value<0?zeroPct-width:zeroPct):0,barColor=signed?(value<0?'#D84A42':'#008A45'):color,trackStyle=signed?`background:linear-gradient(90deg,#eef3f1 0%,#eef3f1 ${zeroPct}%,#98aaa0 ${zeroPct}%,#98aaa0 calc(${zeroPct}% + 1px),#eef3f1 calc(${zeroPct}% + 1px),#eef3f1 100%)`:'';return `<div class="bar-row${signed&&value<0?' down':''}" title="${esc(item[labelKey])}: ${esc(format(value))}"><span>${esc(item[labelKey])}</span><div class="bar-track" style="${trackStyle}"><i style="left:${left}%;width:${width}%;background:${barColor}"></i></div><b>${format(value)}</b></div>`;}).join('');
    node.innerHTML=`<div class="bar-axis"><span>Mã cổ phiếu</span><div>${ticks.map(value=>`<b>${tickText(value)}</b>`).join('')}</div><span>${unit}</span></div><div class="bar-chart">${rows}</div><div class="bar-axis-caption">${unit} · cột bắt đầu từ 0${signed?' ở vạch giữa; xanh là tăng, đỏ là giảm':''}; nhãn cuối mỗi hàng là giá trị nguồn</div>`;
  }
  const liquidityMapState={data:null,selected:'',hidden:new Set(),hovering:false,observer:null,tooltip:null,points:[],touchTap:null};
  const mapSymbol=value=>String(value||'').trim().toUpperCase();
  function liquidityMapGroup(point){if(point.quadrant==='spread_narrow_depth_thick')return'good';if(point.quadrant==='spread_wide_depth_thin')return'bad';return'mixed';}
  function ensureLiquidityMapTooltip(){if(!liquidityMapState.tooltip){const el=document.createElement('div');el.className='liquidity-map-tooltip';el.setAttribute('role','status');el.setAttribute('aria-live','polite');document.body.appendChild(el);liquidityMapState.tooltip=el;}return liquidityMapState.tooltip;}
  function showLiquidityMapTooltip(point,event){const tip=ensureLiquidityMapTooltip(),price=point.price==null?'Chưa có giá SSI':`${num(point.price,2)} đ`,priceLabel=point.price_source==='trade'?'Giá khớp gần nhất SSI':'Giá quote SSI',priceTime=point.price_as_of?` · ${esc(point.price_as_of)}`:'',tick=point.tick_size==null?'—':`${num(point.tick_size,4)} đ`,ticks=point.median_spread_ticks==null?'—':num(point.median_spread_ticks,2),depth=point.median_total_depth_value==null?'—':`${num(point.median_total_depth_value,0)} đ × cổ phiếu`;tip.innerHTML=`<strong>${esc(point.symbol)}${point.name?` · ${esc(point.name)}`:''}</strong><div>Sàn: ${esc(point.exchange||'Chưa có thông tin')}</div><div>${priceLabel}: ${price}${priceTime}</div><div>Bước giá: ${tick}</div><div>Spread trung vị: ${num(point.median_spread_bps,2)} bps · ${ticks} bước giá</div><div>Độ sâu top 3: ${depth}</div><div>Số bar: ${num(point.n_bars??point.sample_count,0)}</div>`;tip.classList.add('visible');const pad=14,rect=tip.getBoundingClientRect();tip.style.left=`${Math.max(8,Math.min(event.clientX+pad,innerWidth-rect.width-8))}px`;tip.style.top=`${Math.max(8,Math.min(event.clientY+pad,innerHeight-rect.height-8))}px`;}  function hideLiquidityMapTooltip(){liquidityMapState.tooltip?.classList.remove('visible');}
  function nearestLiquidityPoint(event,root){const svg=root.querySelector('svg');if(!svg)return null;const rect=svg.getBoundingClientRect(),width=Number(svg.getAttribute('width'))||rect.width,height=Number(svg.getAttribute('height'))||rect.height,x=(event.clientX-rect.left)*width/rect.width,y=(event.clientY-rect.top)*height/rect.height;let best=null,distance=Infinity;for(const item of liquidityMapState.points){if(item.root!==root)continue;const d=Math.hypot(item.x-x,item.y-y);if(d<distance){distance=d;best=item;}}return best&&distance<=22?best:null;}
  function openLiquiditySymbol(symbol){const ticker=mapSymbol(symbol),detail=document.querySelector('#detail-symbol');if(!detail||!ticker)return;detail.value=ticker;localStorage.setItem('sli-detail-symbol',ticker);const input=document.querySelector('#detail-symbol-search');if(input)input.value=ticker;const url=new URL(location.href);url.searchParams.set('symbol',ticker);history.replaceState(null,'',url);detail.dispatchEvent(new Event('change',{bubbles:true}));window.setRoute?.('detail');}
  function renderLiquidityHighlights(points){const node=document.querySelector('#liquidity-map-highlights');if(!node)return;const groups=[['good','Nhóm thuận lợi'],['bad','Nhóm cần lưu ý'],['mixed','Nhóm hỗn hợp']];node.innerHTML=groups.map(([key,title])=>{let rows=points.filter(p=>liquidityMapGroup(p)===key);if(key==='good')rows.sort((a,b)=>Number(a.median_spread_bps)-Number(b.median_spread_bps)||Number(b.median_total_depth_value)-Number(a.median_total_depth_value));else if(key==='bad')rows.sort((a,b)=>Number(b.median_spread_bps)-Number(a.median_spread_bps));else rows.sort((a,b)=>Number(b.n_bars??b.sample_count)-Number(a.n_bars??a.sample_count));rows=rows.slice(0,5);return `<section class="liquidity-map-group-list"><h3>${title}</h3>${rows.length?rows.map(p=>`<button type="button" data-map-select="${esc(p.symbol)}" class="liquidity-map-row${liquidityMapState.selected===p.symbol?' selected':''}"><strong>${esc(p.symbol)}</strong><span>${num(p.median_spread_bps,1)} bps · ${num(p.median_total_depth_value/1e6,1)} tr</span></button>`).join(''):'<p>Chưa có mã đủ điều kiện.</p>'}</section>`;}).join('');}
  function mapNiceTicks(min,max){const values=[];const lo=Math.floor(Math.log10(Math.max(min,1e-6)))-1,hi=Math.ceil(Math.log10(Math.max(max,1e-6)))+1;for(let e=lo;e<=hi;e++)for(const m of [1,2,5])values.push(m*10**e);return [...new Set(values)].sort((a,b)=>a-b);}
  function makeLiquidityMapSvg(root,data){const points=(data.points||[]).filter(p=>Number.isFinite(Number(p.median_spread_bps))&&Number(p.median_total_depth_value)>0);const width=Math.max(320,Math.floor(root.clientWidth)),height=Math.max(360,Math.floor(root.clientHeight));if(!points.length){root.innerHTML='<div class="liquidity-map-empty">Chưa có đủ dữ liệu hợp lệ để vẽ bản đồ thanh khoản.</div>';return;}const compact=width<520,L=compact?58:82,R=20,T=58,B=compact?100:88,plotW=width-L-R,plotH=height-T-B,spreads=points.map(p=>Math.max(Number(p.median_spread_bps)||0,.01)),depths=points.map(p=>Number(p.median_total_depth_value)),minS=Math.min(...spreads),maxS=Math.max(...spreads),minD=Math.min(...depths),maxD=Math.max(...depths),xTicksAll=mapNiceTicks(minS,maxS),yTicksAll=mapNiceTicks(minD,maxD),lowerX=xTicksAll.filter(v=>v<minS),upperX=xTicksAll.filter(v=>v>maxS),lowerY=yTicksAll.filter(v=>v<minD),upperY=yTicksAll.filter(v=>v>maxD),xMin=lowerX.slice(-1)[0]||minS/2,xMax=upperX[0]||maxS*2,yMin=lowerY.slice(-1)[0]||minD/2,yMax=upperY[0]||maxD*2;const lx0=Math.log10(Math.max(xMin,.001)),lx1=Math.log10(Math.max(xMax,xMin*1.01)),ly0=Math.log10(Math.max(yMin,.001)),ly1=Math.log10(Math.max(yMax,yMin*1.01)),xAt=v=>L+(Math.log10(Math.max(Number(v)||.01,xMin))-lx0)/(lx1-lx0)*plotW,yAt=v=>T+(ly1-Math.log10(Math.max(Number(v)||.01,yMin)))/(ly1-ly0)*plotH,medianSpread=Number(data.spread_median)||0,medianDepth=Number(data.depth_median)||0,midX=xAt(Math.max(medianSpread,xMin)),midY=yAt(Math.max(medianDepth,yMin));let svg=`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" role="img" aria-label="Biểu đồ spread và độ sâu sổ lệnh cổ phiếu">`;svg+=`<rect x="${L}" y="${T}" width="${Math.max(0,midX-L)}" height="${Math.max(0,midY-T)}" fill="#e5f4e9"/><rect x="${midX}" y="${midY}" width="${Math.max(0,width-R-midX)}" height="${Math.max(0,height-B-midY)}" fill="#fbe9e7"/>`;
    const xVisibleTicks=[];for(const v of xTicksAll.filter(v=>v>=xMin&&v<=xMax)){const px=xAt(v),gap=compact?48:58;if(!xVisibleTicks.length||px-xAt(xVisibleTicks[xVisibleTicks.length-1])>=gap)xVisibleTicks.push(v);}for(const v of xVisibleTicks){const x=xAt(v);svg+=`<line x1="${x}" y1="${T}" x2="${x}" y2="${height-B}" stroke="#dde7df" pointer-events="none"/><text x="${x}" y="${height-B+20}" text-anchor="middle">${num(v,v<10?1:0)}</text>`;}for(const v of yTicksAll.filter(v=>v>=yMin&&v<=yMax)){const y=yAt(v),label=v>=1e9?`${num(v/1e9,1)} tỷ`:v>=1e6?`${num(v/1e6,0)} tr`:num(v,0);svg+=`<line x1="${L}" y1="${y}" x2="${width-R}" y2="${y}" stroke="#dde7df" pointer-events="none"/><text x="${L-9}" y="${y+4}" text-anchor="end">${label}</text>`;}svg+=`<line x1="${midX}" y1="${T}" x2="${midX}" y2="${height-B}" stroke="#627b6d" stroke-dasharray="5 4" pointer-events="none"/><line x1="${L}" y1="${midY}" x2="${width-R}" y2="${midY}" stroke="#627b6d" stroke-dasharray="5 4" pointer-events="none"/><text x="${L+8}" y="${T+16}" class="map-zone-label" pointer-events="none">Spread hẹp · độ sâu cao</text><text x="${L+8}" y="${height-B-8}" class="map-zone-label" pointer-events="none">Spread rộng · độ sâu thấp</text><g class="map-median-badge" pointer-events="none"><rect x="${Math.max(L,width-R-236)}" y="8" width="228" height="21" rx="5"/><line x1="${Math.max(L,width-R-226)}" y1="18" x2="${Math.max(L,width-R-212)}" y2="18"/><text x="${Math.max(L,width-R-206)}" y="22">Trung vị spread: ${num(medianSpread,1)} bps</text><rect x="${Math.max(L,width-R-236)}" y="32" width="228" height="21" rx="5"/><line x1="${Math.max(L,width-R-226)}" y1="42" x2="${Math.max(L,width-R-212)}" y2="42"/><text x="${Math.max(L,width-R-206)}" y="46">Trung vị độ sâu: ${num(medianDepth/1e6,1)} tr</text></g><text x="${L+plotW/2}" y="${height-48}" text-anchor="middle" class="map-axis-title">Spread (bps) · thấp hơn là tốt hơn</text><text x="${L+plotW/2}" y="${height-28}" text-anchor="middle" class="map-axis-subtitle">Độ sâu top 3 (đồng × cổ phiếu) · cao hơn là tốt hơn</text>`;
    const candidates=[],byDepth=[...points].sort((a,b)=>Number(b.median_total_depth_value)-Number(a.median_total_depth_value)).slice(0,5),bySpread=[...points].sort((a,b)=>Number(b.median_spread_bps)-Number(a.median_spread_bps)).slice(0,5);if(liquidityMapState.selected){const selected=points.find(p=>mapSymbol(p.symbol)===liquidityMapState.selected);if(selected)candidates.push(selected);}for(const p of [...byDepth,...bySpread])if(!candidates.some(x=>x.symbol===p.symbol))candidates.push(p);const labelBoxes=[],rendered=[],colors={good:'#078445',bad:'#c9413a',mixed:'#78858b'};
    for(const p of points){const group=liquidityMapGroup(p);if(liquidityMapState.hidden.has(group))continue;const rawX=Number(p.median_spread_bps),x=xAt(rawX),y=yAt(p.median_total_depth_value),oneTick=Math.abs(Number(p.median_spread_ticks)-1)<1e-6,selected=mapSymbol(p.symbol)===liquidityMapState.selected,color=colors[group];rendered.push({x,y,point:p,root});const shape=oneTick?`<circle cx="${x}" cy="${y}" r="${selected?8:6}" fill="white" stroke="${selected?'#f3a712':color}" stroke-width="${selected?3:2}"/>`:`<circle cx="${x}" cy="${y}" r="${selected?8:6}" fill="${color}" stroke="${selected?'#f3a712':'white'}" stroke-width="${selected?3:1.5}"/>`;svg+=`<g data-map-point="${esc(p.symbol)}" tabindex="0" role="button" aria-label="${esc(p.symbol)} · spread ${num(rawX,2)} bps · độ sâu ${num(p.median_total_depth_value,0)}">${shape}</g>`;}
    for(const p of candidates){if(liquidityMapState.hidden.has(liquidityMapGroup(p)))continue;const x=xAt(p.median_spread_bps),y=yAt(p.median_total_depth_value),label=String(p.symbol),w=label.length*8+8,positions=[[x+9,y-8],[x+9,y+18],[x-w-8,y-8],[x-w-8,y+18],[x-w/2,y-13],[x-w/2,y+24],[x+12,y-22],[x-w-12,y-22]];let chosen=null;for(const [lx,ly] of positions){const bx=Math.max(L,Math.min(width-R-w,lx)),by=Math.max(T+12,Math.min(height-B-3,ly)),box={x:bx,y:by-12,w,h:16};if(!labelBoxes.some(q=>box.x<q.x+q.w+3&&box.x+box.w+3>q.x&&box.y<q.y+q.h+2&&box.y+box.h+2>q.y)){chosen={...box,label};labelBoxes.push(box);break;}}if(chosen)svg+=`<text x="${chosen.x}" y="${chosen.y+12}" class="map-symbol-label" pointer-events="none">${esc(chosen.label)}</text>`;}svg+='</svg>';root.innerHTML=svg;liquidityMapState.points.push(...rendered);}
  function renderLiquidityMap(data){liquidityMapState.data=data;liquidityMapState.points=[];const main=document.querySelector('#liquidity-map'),expanded=document.querySelector('#liquidity-map-expanded');if(main)makeLiquidityMapSvg(main,data);if(expanded&&!document.querySelector('#liquidity-map-modal')?.hidden)makeLiquidityMapSvg(expanded,data);renderLiquidityHighlights(data.points||[]);const legend=document.querySelector('#liquidity-map-legend');if(legend&&!legend.dataset.bound){legend.dataset.bound='1';legend.innerHTML=[['good','Tốt · spread hẹp, độ sâu cao'],['bad','Kém · spread rộng, độ sâu thấp'],['mixed','Hỗn hợp']].map(([key,label])=>`<button type="button" class="liquidity-map-legend-item ${key}" data-map-group="${key}" aria-pressed="true"><i></i>${label}</button>`).join('')+'<span class="liquidity-map-one-tick"><i></i>Vòng rỗng: spread trung vị bằng 1 bước giá</span>';}
    const note=document.querySelector('#liquidity-map-note');if(note)note.textContent=`${(data.points||[]).length} mã đủ mẫu · cửa sổ chung ${num(data.window_minutes,0)} phút · rê/chạm để xem chi tiết.`;}
  function bindLiquidityMapRoot(root){if(!root||root.dataset.bound==='1')return;root.dataset.bound='1';root.addEventListener('pointermove',event=>{if(event.pointerType==='touch')return;liquidityMapState.hovering=true;const hit=nearestLiquidityPoint(event,root);if(hit)showLiquidityMapTooltip(hit.point,event);else hideLiquidityMapTooltip();});root.addEventListener('pointerleave',()=>{liquidityMapState.hovering=false;hideLiquidityMapTooltip();});root.addEventListener('pointerdown',event=>{if(event.pointerType!=='touch')return;liquidityMapState.hovering=true;const hit=nearestLiquidityPoint(event,root);if(hit){const now=Date.now(),previous=liquidityMapState.touchTap,symbol=hit.point.symbol;liquidityMapState.touchTap={symbol,at:now,secondTap:!!previous&&previous.symbol===symbol&&now-previous.at<1600};showLiquidityMapTooltip(hit.point,event);}setTimeout(()=>{liquidityMapState.hovering=false;},1200);});root.addEventListener('click',event=>{const point=event.target.closest('[data-map-point]');if(!point)return;if(event.pointerType==='touch'&&liquidityMapState.touchTap?.symbol===point.dataset.mapPoint){if(!liquidityMapState.touchTap.secondTap)return;liquidityMapState.touchTap=null;}openLiquiditySymbol(point.dataset.mapPoint);});root.addEventListener('keydown',event=>{const point=event.target.closest('[data-map-point]');if(point&&(event.key==='Enter'||event.key===' ')){event.preventDefault();openLiquiditySymbol(point.dataset.mapPoint);}});}
  function initLiquidityMap(){const main=document.querySelector('#liquidity-map'),expanded=document.querySelector('#liquidity-map-expanded'),search=document.querySelector('#liquidity-map-search'),modal=document.querySelector('#liquidity-map-modal');bindLiquidityMapRoot(main);bindLiquidityMapRoot(expanded);if(!liquidityMapState.observer&&window.ResizeObserver){liquidityMapState.observer=new ResizeObserver(entries=>{if(!liquidityMapState.data)return;for(const entry of entries){const root=entry.target;if(root.clientWidth&&root.clientHeight){liquidityMapState.points=liquidityMapState.points.filter(p=>p.root!==root);makeLiquidityMapSvg(root,liquidityMapState.data);}}});if(main)liquidityMapState.observer.observe(main);if(expanded)liquidityMapState.observer.observe(expanded);}if(search&&!search.dataset.bound){search.dataset.bound='1';search.addEventListener('input',()=>{const term=mapSymbol(search.value),matches=(liquidityMapState.data?.points||[]).filter(p=>mapSymbol(p.symbol).startsWith(term)||(p.name||'').toLocaleUpperCase('vi').includes(term));liquidityMapState.selected=term&&matches.length===1?mapSymbol(matches[0].symbol):term&&matches.some(p=>mapSymbol(p.symbol)===term)?term:'';renderLiquidityMap(liquidityMapState.data||{points:[]});});}if(!document.body.dataset.liquidityMapBound){document.body.dataset.liquidityMapBound='1';document.addEventListener('click',event=>{const group=event.target.closest('[data-map-group]'),select=event.target.closest('[data-map-select]');if(group){const key=group.dataset.mapGroup;liquidityMapState.hidden.has(key)?liquidityMapState.hidden.delete(key):liquidityMapState.hidden.add(key);group.setAttribute('aria-pressed',String(!liquidityMapState.hidden.has(key)));renderLiquidityMap(liquidityMapState.data||{points:[]});return;}if(select){liquidityMapState.selected=mapSymbol(select.dataset.mapSelect);const input=document.querySelector('#liquidity-map-search');if(input)input.value=liquidityMapState.selected;renderLiquidityMap(liquidityMapState.data||{points:[]});return;}if(event.target.closest('[data-map-reset]')){liquidityMapState.selected='';liquidityMapState.hidden.clear();const input=document.querySelector('#liquidity-map-search');if(input)input.value='';document.querySelectorAll('[data-map-group]').forEach(b=>b.setAttribute('aria-pressed','true'));renderLiquidityMap(liquidityMapState.data||{points:[]});return;}if(event.target.closest('[data-map-zoom]')){if(modal){modal.hidden=false;document.body.classList.add('liquidity-map-modal-open');if(liquidityMapState.data)makeLiquidityMapSvg(expanded,liquidityMapState.data);}return;}if(event.target.closest('[data-map-close]')||event.target===modal){if(modal)modal.hidden=true;document.body.classList.remove('liquidity-map-modal-open');hideLiquidityMapTooltip();}});document.addEventListener('keydown',event=>{if(event.key==='Escape'&&modal&&!modal.hidden){modal.hidden=true;document.body.classList.remove('liquidity-map-modal-open');hideLiquidityMapTooltip();}});}}
  function intradayChart(data,marketStatus=null){const node=document.querySelector('#intraday-profile'),note=document.querySelector('#intraday-profile-note');if(!node)return;const profile=data.profile||[];if(!profile.length){node.innerHTML='<div class="empty-state">Chưa có thanh dữ liệu một phút (bar). Dữ liệu này sẽ xuất hiện sau khi bộ thu thập lưu liên tục trong phiên giao dịch.</div>';if(note)note.textContent=data.note||'';return;}const last=profile.at(-1)?.minute,rawLast=String(last||''),hasFullDate=/^\d{4}-\d{2}-\d{2}/.test(rawLast),persistedAt=marketStatus?.last_persisted_at||marketStatus?.last_data_at||marketStatus?.meta?.data_as_of||'',barDate=data.session_date||(hasFullDate?rawLast.slice(0,10):String(persistedAt).slice(0,10)),barDateLabel=barDate?formatVnDate(barDate):'chưa rõ ngày',lastTime=last?(hasFullDate?formatVnTime(last):rawLast.slice(0,5)):'—';const chartRows=profile.map(p=>({minute:/^\d{4}-\d{2}-\d{2}/.test(String(p.minute||''))?p.minute:(barDate?`${barDate}T${String(p.minute).slice(0,5)}:00+07:00`:p.minute),spread_bps:p.median_spread_bps,sample_count:p.symbols_count}));lineChart(node,[{name:'Trung vị spread theo phút',rows:chartRows,color:'#008A45'}],'spread_bps','Trung vị spread toàn thị trường theo phút');const latestDate=marketStatus?.session_date||marketStatus?.meta?.session_date,availability=marketStatus?.market_state==='CLOSED'&&latestDate&&barDate&&latestDate!==barDate?` Ngoài phiên: bar thanh khoản cuối đã lưu thuộc phiên ${barDateLabel}, cập nhật đến ${lastTime} ngày ${barDateLabel}; chưa có bar đã lưu cho phiên ${formatVnDate(latestDate)}.`:'';if(note)note.textContent=`Nguồn ${sourceName(data.source)} · bar SSI đã lưu đến ${lastTime} ngày ${barDateLabel} · mỗi điểm là trung vị spread bar 1 phút giữa các mã có bar hợp lệ; số mã tham gia hiện khi rê chuột. ${data.note||''}${availability}`;}
  function marketScatter(rows){
    const node=document.querySelector('#liquidity-map'),note=document.querySelector('#liquidity-map-note');if(!node)return;
    const points=(rows||[]).filter(r=>Number.isFinite(Number(r.change_pct))&&Number(r.traded_value)>0).slice(0,25);
    if(!points.length){node.innerHTML='<div class="empty-state">SSI chưa có OHLC đủ hai phiên để so sánh.</div>';return;}
    const W=900,H=330,L=82,R=30,T=24,B=68,px=W-L-R,py=H-T-B;
    const xs=points.map(p=>Number(p.change_pct)),ys=points.map(p=>Number(p.traded_value)/1e9);
    const xt=niceTicks(Math.min(...xs),Math.max(...xs),5),yt=niceTicks(0,Math.max(...ys),4),xmin=xt[0],xmax=xt[xt.length-1],ymin=0,ymax=yt[yt.length-1]||1,dx=xmax-xmin||1,dy=ymax-ymin||1;
    let svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Thay đổi giá và giá trị giao dịch của nhóm 25 mã dẫn đầu thanh khoản theo OHLC ngày SSI">';
    for(const value of yt){const y=T+(ymax-value)/dy*py;svg+=`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#e2ebe4"/><text x="${L-9}" y="${y+4}" text-anchor="end" fill="#40594a" font-size="10">${num(value,value<10?1:0)}</text>`;}
    for(const value of xt){const x=L+(value-xmin)/dx*px;svg+=`<line x1="${x}" y1="${T}" x2="${x}" y2="${H-B}" stroke="#edf1ed"/><text x="${x}" y="${H-B+16}" text-anchor="middle" fill="#40594a" font-size="10">${num(value,value<10?1:0)}%</text>`;}
    for(const p of points){const change=Number(p.change_pct),turnover=Number(p.traded_value)/1e9,x=L+(change-xmin)/dx*px,y=T+(ymax-turnover)/dy*py,color=change<0?'#D84A42':'#008A45';svg+=`<circle cx="${x}" cy="${y}" r="6" fill="${color}" fill-opacity=".86" stroke="white" stroke-width="2"><title>${esc(p.symbol)} · ${num(change,2)}% · ${num(turnover,2)} tỷ đồng · ${esc(p.trading_date)}</title></circle><text x="${x+8}" y="${y+4}" fill="#17382a" font-size="10">${esc(p.symbol)}</text>`;}
    svg+=`<text x="${L+px/2}" y="${H-8}" text-anchor="middle" fill="#40594a" font-size="11">Thay đổi đóng cửa so với phiên trước (%) · trục X</text><text x="14" y="${T-9}" fill="#40594a" font-size="10">Giá trị giao dịch (tỷ đồng) · trục Y</text></svg>`;
    node.innerHTML=svg;if(note)note.textContent='Mỗi điểm là một mã trong top 25 mã theo giá trị giao dịch OHLC ngày SSI, không phải toàn bộ danh sách. X là phần trăm thay đổi đóng cửa; Y là giá trị giao dịch (tỷ đồng). Xanh tăng, đỏ giảm; không biểu thị độ sâu sổ lệnh.';
  }
  let liquidityMapCache=null,liquidityMapFetchedAt=0,liquidityMapRequest=null,liquidityMapCacheKey='';
  document.addEventListener('sli-market-data-changed',()=>{marketHistoryCache=null;marketHistoryFetchedAt=0;marketHistoryCacheKey='';liquidityMapCache=null;liquidityMapFetchedAt=0;overviewDataStore.cache.clear();});
  async function loadResearchPanels(sharedProfile=null,windowMinutes=60,marketStatus=null){
    const mapNode=document.querySelector('#liquidity-map'),mapNote=document.querySelector('#liquidity-map-note');
    let profile=sharedProfile;
    const range=overviewDateParams(),historyKey=`${range.from}|${range.to}`;
    const mapPanel=mapNode?.closest('.panel'),mapTitle=mapPanel?.querySelector('.panel-heading h2'),mapDescription=mapPanel?.querySelector('.panel-heading p');
    if(mapTitle)mapTitle.textContent='Bản đồ thanh khoản';
    if(mapDescription)mapDescription.textContent=`Spread và độ sâu từ bar đã hoàn tất · ${range.from||'đầu dữ liệu'} đến ${range.to||'mới nhất'}`;
    const mapTask=(async()=>{try{
      initLiquidityMap();
      const mapRefreshMs=marketStatus?.market_state==='IN_SESSION'?10000:45000;
      const refreshMap=!liquidityMapState.hovering&&(!liquidityMapCache||liquidityMapCacheKey!==historyKey||Date.now()-liquidityMapFetchedAt>mapRefreshMs);
      if(refreshMap){
        if(!liquidityMapRequest){
          if(mapNode){mapNode.setAttribute('aria-busy','true');mapNode.innerHTML='<div class="liquidity-map-empty">Đang tải dữ liệu bản đồ SSI…</div>';}
          liquidityMapRequest=call(`/api/liquidity-map?window_minutes=${Math.max(60,windowMinutes)}${range.query}`)
            .then(data=>{liquidityMapCache=data;liquidityMapFetchedAt=Date.now();liquidityMapCacheKey=historyKey;return data;})
            .finally(()=>{liquidityMapRequest=null;if(mapNode)mapNode.removeAttribute('aria-busy');});
        }
        await liquidityMapRequest;
      }
      if(liquidityMapCache.points?.length){if(refreshMap||!liquidityMapState.data)renderLiquidityMap(liquidityMapCache);}
      else{if(mapNode)mapNode.innerHTML='<div class="empty-state">Chưa có đủ mẫu để lập bản đồ. Cần ít nhất 30 bar đã hoàn tất và 200 snapshot trong cùng cửa sổ.</div>';if(mapNote)mapNote.textContent=`Đủ điều kiện: ${num(liquidityMapCache.eligible_symbols||0,0)} mã; tối thiểu ${num(liquidityMapCache.minimum_samples||0,0)} bar và ${num(liquidityMapCache.minimum_snapshots||0,0)} snapshot.`;}
    }catch(error){
      if(mapNode)mapNode.innerHTML=`<div class="empty-state research-retry"><span>Bản đồ thanh khoản chưa tải được. Các biểu đồ khác vẫn hoạt động.</span><button type="button" class="button button-secondary" data-retry-liquidity-map>Thử lại</button></div>`;
      if(mapNote)mapNote.textContent='Bản đồ dùng dữ liệu bar một phút đã hoàn tất và bị giới hạn theo cửa sổ thời gian để tránh truy vấn toàn bộ lịch sử.';
      liquidityMapFetchedAt=0;
    }})();
    const profileTask=profile?Promise.resolve(profile):call(`/api/intraday-profile?window_minutes=${windowMinutes}${range.query}`).catch(()=>({profile:[],note:'Chưa tải được các bar thanh khoản đã hoàn tất.'}));
    const now=Date.now();
    const historyTask=(async()=>{if(!marketHistoryCache||marketHistoryCacheKey!==historyKey||now-marketHistoryFetchedAt>60000){try{const breadthData=await call(`/api/market-breadth?limit=10000${range.query}`),asOf=breadthData.latest_trading_date||range.to||'',asOfQuery=asOf?`&as_of=${encodeURIComponent(asOf)}`:'';const leadersData=await call(`/api/market-leaders?limit=25${asOfQuery}`);marketHistoryCache=[leadersData,breadthData];marketHistoryCacheKey=historyKey;marketHistoryFetchedAt=now;}catch{marketHistoryCache=[{rows:[]},{rows:[]}];}}})();
    const [profileResult]=await Promise.all([profileTask,mapTask,historyTask]);profile=profileResult;
    const [leaders,breadth]=marketHistoryCache,rows=leaders.rows||[];
    const turnover=rows.slice(0,10).map(r=>({...r,turnover_bil:Number(r.traded_value||0)/1e9}));
    bars(document.querySelector('#spread-compare'),turnover,'symbol','turnover_bil','#008A45',v=>num(v,2)+' tỷ đồng');
    const turnoverNote=document.querySelector('#turnover-chart-note');if(turnoverNote)turnoverNote.textContent='OHLC ngày SSI · 10 mã dẫn đầu theo giá trị giao dịch; nội dung giá nằm trong mục có thể mở rộng bên dưới.';
    const movers=[...rows].sort((a,b)=>Number(b.change_pct)-Number(a.change_pct)),top=[...movers.slice(0,5),...movers.slice(-5).reverse()];
    bars(document.querySelector('#coverage-chart'),top,'symbol','change_pct','#68A92E',v=>num(v,2)+'%');
    const coverageNote=document.querySelector('#coverage-note');if(coverageNote)coverageNote.textContent='OHLC ngày SSI · 5 mã tăng và 5 mã giảm mạnh trong top 25 theo giá trị giao dịch.';
    const profilePanel=document.querySelector('#intraday-profile')?.closest('.panel'),profileTitle=profilePanel?.querySelector('.panel-heading h2'),profileDescription=profilePanel?.querySelector('.panel-heading p');
    if(profileTitle)profileTitle.textContent='Diễn biến spread trong phiên';
    if(profileDescription)profileDescription.textContent=`Bar 1 phút đã hoàn tất · ${num(profile?.sessions_count||0,0)} phiên · ${range.from||'đầu dữ liệu'} đến ${range.to||'mới nhất'}`;
    intradayChart(profile||{profile:[],note:'Chưa có bar hoàn tất trong cửa sổ này.'},marketStatus);
  }
  async function enhancedSymbols() {
    if(!symbolsCache||Date.now()-symbolsFetchedAt>300000){symbolsCache=await call('/api/symbols');symbolsFetchedAt=Date.now();}
    const d=symbolsCache;state.symbols=d.symbols||[];securitiesDirectory=d.securities||state.symbols.map(symbol=>({symbol,name_vi:'',board:''}));
    const overview=document.querySelector('#overview-symbol');
    if(overview){const old=overview.value,requested=new URLSearchParams(location.search).get('symbol');overview.innerHTML='<option value="__all__">Tất cả mã</option>'+state.symbols.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');const preferred=(requested||old||'').toUpperCase();overview.value=state.symbols.includes(preferred)?preferred:'__all__';}
    const detail=document.querySelector('#detail-symbol');
    if(detail){const old=detail.value,requested=new URLSearchParams(location.search).get('symbol'),remembered=localStorage.getItem('sli-detail-symbol'),preferred=[requested,remembered,d.detail_default,old].find(x=>x&&state.symbols.includes(x.toUpperCase()));detail.innerHTML=state.symbols.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');if(preferred)detail.value=preferred.toUpperCase();else if(state.symbols.length)detail.value=state.symbols[0];}
    const search=document.querySelector('#detail-symbol-search');if(search&&detail){if(!detailSearchDirty&&document.activeElement!==search)search.value=detail.value;updateDetailCompanyName();}
  }
  function updateDetailCompanyName(){
    const detail=document.querySelector('#detail-symbol'),search=document.querySelector('#detail-symbol-search'),label=document.querySelector('#detail-company-name');
    if(!detail||!label)return;
    const selected=detail.value,profile=securitiesDirectory.find(item=>item.symbol===selected);
    label.textContent=profile?.name_vi?`${profile.name_vi}${profile.board?' · '+profile.board:''}`:'Chưa có tên doanh nghiệp trong danh mục SSI.';
  }
  function renderDetailSuggestions(query=''){
    const box=document.querySelector('#detail-symbol-options'),input=document.querySelector('#detail-symbol-search');if(!box||!input)return;
    const term=query.trim().toLocaleLowerCase('vi');
    const matches=securitiesDirectory.filter(item=>!term||item.symbol.toLowerCase().includes(term)||(item.name_vi||'').toLocaleLowerCase('vi').includes(term)).slice(0,10);
    box.innerHTML=matches.length?matches.map(item=>`<button type="button" class="symbol-suggestion" role="option" aria-selected="false" data-detail-symbol="${esc(item.symbol)}"><strong>${esc(item.symbol)}</strong><span>${esc(item.name_vi||'Chưa có tên trong danh mục')}${item.board?' · '+esc(item.board):''}</span></button>`).join(''):'<div class="symbol-suggestion-empty">Không tìm thấy mã hoặc doanh nghiệp phù hợp.</div>';
    box.classList.remove('hidden');input.setAttribute('aria-expanded','true');
  }
  function chooseDetailSymbol(symbol){
    const detail=document.querySelector('#detail-symbol'),input=document.querySelector('#detail-symbol-search'),box=document.querySelector('#detail-symbol-options');
    if(!detail||!state.symbols.includes(symbol))return;
    detail.value=symbol;localStorage.setItem('sli-detail-symbol',symbol);const url=new URL(location.href);url.searchParams.set('symbol',symbol);history.replaceState(null,'',url);if(input)input.value=symbol;detailSearchDirty=false;if(box)box.classList.add('hidden');input?.setAttribute('aria-expanded','false');updateDetailCompanyName();
    detail.dispatchEvent(new Event('change',{bubbles:true}));
  }
  document.addEventListener('focusin',event=>{if(event.target?.id==='detail-symbol-search')renderDetailSuggestions(event.target.value);});
  document.addEventListener('input',event=>{if(event.target?.id==='detail-symbol-search'){detailSearchDirty=true;renderDetailSuggestions(event.target.value);}});
  document.addEventListener('keydown',event=>{
    if(event.target?.id!=='detail-symbol-search')return;
    if(event.key==='Escape'){document.querySelector('#detail-symbol-options')?.classList.add('hidden');event.target.setAttribute('aria-expanded','false');}
    if(event.key==='Enter'){event.preventDefault();const exact=securitiesDirectory.find(item=>item.symbol.toLowerCase()===event.target.value.trim().toLowerCase());const first=document.querySelector('#detail-symbol-options [data-detail-symbol]');if(exact)chooseDetailSymbol(exact.symbol);else if(first)chooseDetailSymbol(first.dataset.detailSymbol);}
    if(event.key==='ArrowDown')document.querySelector('#detail-symbol-options [data-detail-symbol]')?.focus();
  });
  document.addEventListener('click',event=>{
    const option=event.target?.closest?.('[data-detail-symbol]');
    if(option){chooseDetailSymbol(option.dataset.detailSymbol);return;}
    if(!event.target?.closest?.('.detail-symbol-picker')){const box=document.querySelector('#detail-symbol-options'),input=document.querySelector('#detail-symbol-search');box?.classList.add('hidden');input?.setAttribute('aria-expanded','false');}
  });
  document.addEventListener('change',event=>{if(event.target?.id==='detail-symbol')updateDetailCompanyName();});
  let historyPage=1, historySearch="", historyTotal=0, historyTimer=null,historyRange='1m';
  function historyDateString(date){return `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;}
  function latestStoredDate(){return overviewDataStore.bounds?.latest_date||overviewDataStore.realtime.status?.latest_historical_date||historyDateString(new Date());}
  function earliestStoredDate(){return overviewDataStore.bounds?.earliest_date||'';}
  function normalizeOverviewPreset(range){return ({'1y':'1m','3y':'3m','5y':'6m'}[range]||range);}
  function syncOverviewUrl(){const url=new URL(location.href),f=overviewDataStore.filters;for(const key of ['from','to','preset','symbol'])url.searchParams.delete(key);if(f.dateFrom)url.searchParams.set('from',f.dateFrom);if(f.dateTo)url.searchParams.set('to',f.dateTo);if(f.preset)url.searchParams.set('preset',f.preset);if(f.symbol&&f.symbol!=='__all__')url.searchParams.set('symbol',f.symbol);history.replaceState(null,'',url);}
  function applyRangeToInputs(range,fromSelector,toSelector){
    const end=latestStoredDate(),start=new Date(`${end}T12:00:00`);if(range==='1m'||range==='3m'||range==='6m'){const months=Number(range[0]),day=start.getDate();start.setDate(1);start.setMonth(start.getMonth()-months);start.setDate(Math.min(day,new Date(start.getFullYear(),start.getMonth()+1,0).getDate()));}else if(range==='1y')start.setFullYear(start.getFullYear()-1);else if(range==='3y')start.setFullYear(start.getFullYear()-3);else if(range==='5y')start.setFullYear(start.getFullYear()-5);let fromValue=range==='all'?earliestStoredDate():historyDateString(start);if(earliestStoredDate()&&fromValue<earliestStoredDate())fromValue=earliestStoredDate();
    const from=document.querySelector(fromSelector),to=document.querySelector(toSelector);if(from)from.value=fromValue;if(to)to.value=end;
  }
  function overviewDateParams(){const from=document.querySelector('#overview-history-from')?.value,to=document.querySelector('#overview-history-to')?.value;return {from,to,query:`${from?'&from_date='+encodeURIComponent(from):''}${to?'&to_date='+encodeURIComponent(to):''}`};}
  function setOverviewRange(range){range=normalizeOverviewPreset(range);overviewDataStore.filters.preset=range;applyRangeToInputs(range,'#overview-history-from','#overview-history-to');applyRangeToInputs(range,'#history-from-date','#history-to-date');historyRange=range;const f=overviewDataStore.filters;f.dateFrom=document.querySelector('#overview-history-from')?.value||'';f.dateTo=document.querySelector('#overview-history-to')?.value||'';document.querySelectorAll('[data-overview-range]').forEach(button=>button.classList.toggle('active',button.dataset.overviewRange===range));document.querySelectorAll('[data-history-range]').forEach(button=>button.classList.toggle('active',button.dataset.historyRange===range));syncOverviewUrl();}
  async function loadOverviewDateBounds(){const now=Date.now();if(overviewDataStore.bounds&&now-overviewDataStore.boundsFetchedAt<60000)return overviewDataStore.bounds;if(overviewDataStore.boundsPromise)return overviewDataStore.boundsPromise;const key='/api/market-history-range';overviewDataStore.boundsPromise=call(key,{noStore:true}).then(data=>{overviewDataStore.bounds=data;overviewDataStore.boundsFetchedAt=Date.now();return data;}).catch(()=>overviewDataStore.bounds).finally(()=>{overviewDataStore.boundsPromise=null;});return overviewDataStore.boundsPromise;}
  window.invalidateOverviewDateBounds=()=>{overviewDataStore.boundsFetchedAt=0;};
  function setDetailRange(range){applyRangeToInputs(range,'#detail-history-from','#detail-history-to');document.querySelectorAll('[data-detail-range]').forEach(button=>button.classList.toggle('active',button.dataset.detailRange===range));}
  function setHistoryRange(range){
    historyRange=range;applyRangeToInputs(range,'#history-from-date','#history-to-date');
    document.querySelectorAll('[data-history-range]').forEach(button=>button.classList.toggle('active',button.dataset.historyRange===range));
  }
  async function historicalPriceChart(chosen, limit) {
    const node=document.querySelector('#historical-price-chart'), summary=document.querySelector('#historical-price-summary');if(!node)return;
    try {
      if(chosen!=='__all__'){
        const controls=document.querySelector('#history-range-controls');if(controls)controls.hidden=false;
        if(!document.querySelector('#history-to-date')?.value)setHistoryRange(historyRange);
        node.classList.remove('history-all-mode');const chartHeading=document.querySelector('#history-chart-heading');if(chartHeading)chartHeading.textContent=`Giá đóng cửa lịch sử SSI · ${chosen}`;
        const params=new URLSearchParams({limit:String(limit),symbol:chosen}),from=document.querySelector('#history-from-date')?.value,to=document.querySelector('#history-to-date')?.value;if(from)params.set('from_date',from);if(to)params.set('to_date',to);
        const data=await call(`/api/price-history?${params}`),rows=(data.series_by_symbol||{})[chosen]||[];
        lineChart(node,[{name:chosen,rows,color:'#008A45'}],'close_price',`Giá đóng cửa lịch sử SSI · ${chosen}`);
        if(summary)summary.innerHTML=rows.length?`<div class="historical-price-table"><b>Mã</b><b>Ngày</b><b>Giá đóng cửa</b><b>Khối lượng</b>${rows.slice(-1).map(r=>`<span>${esc(chosen)}</span><span>${esc(r.trading_date)}</span><strong>${num(r.close_price,0)} đ</strong><span>${num(r.volume,0)}</span>`).join('')}</div>`:'<div class="empty-state">SSI chưa trả về giá lịch sử cho mã này.</div>';
      }else{
        const controls=document.querySelector('#history-range-controls');if(controls)controls.hidden=true;
        node.classList.add('history-all-mode');const chartHeading=document.querySelector('#history-chart-heading');if(chartHeading)chartHeading.textContent='Độ rộng thị trường SSI · số mã tăng / giảm theo phiên';
        const range=overviewDateParams();const [data,breadth]=await Promise.all([call(`/api/price-history?page=${historyPage}&page_size=50&search=${encodeURIComponent(historySearch)}`),call(`/api/market-breadth?limit=10000${range.query}`)]);historyTotal=data.total||0;
        const marketRows=breadth.rows||[];lineChart(node,[{name:'Mã tăng giá · xanh lá',rows:marketRows.map(r=>({...r,count:r.advancing})),color:'#008A45'},{name:'Mã giảm giá · đỏ',rows:marketRows.map(r=>({...r,count:r.declining})),color:'#E2231A'}],'count','Số mã tăng / giảm theo phiên');
        if(summary){const rows=data.rows||[];summary.innerHTML=rows.length?`<div class="historical-price-table"><b>Mã</b><b>Doanh nghiệp / sàn</b><b>Ngày · giá đóng cửa</b><b>Khối lượng</b>${rows.map(r=>`<span><button type="button" class="history-symbol-link" data-history-symbol="${esc(r.symbol)}">${esc(r.symbol)}</button></span><span>${esc(r.name_vi||'—')} · ${esc(r.board||'—')}</span><strong>${r.close_price==null?'<i class="history-missing">SSI không trả OHLC lịch sử</i>':`${esc(r.trading_date)} · ${num(r.close_price,0)} đ`}</strong><span>${r.volume==null?'—':num(r.volume,0)}</span>`).join('')}</div>`:'<div class="empty-state">Không có mã phù hợp trong catalog SSI.</div>';}
        const pages=Math.max(1,Math.ceil(historyTotal/50));const status=document.querySelector('#history-page-status');if(status)status.textContent=`Trang ${historyPage} / ${pages} · ${num(historyTotal,0)} mã cổ phiếu`;
        const prev=document.querySelector('#history-prev'),next=document.querySelector('#history-next');if(prev)prev.disabled=historyPage<=1;if(next)next.disabled=historyPage>=pages;
        const st=await call('/api/status');const prog=document.querySelector('#history-progress');if(prog)prog.textContent=`SSI catalog: ${num(st.universe_symbols||historyTotal,0)} mã · OHLC đã đồng bộ: ${num(st.historical_symbols_processed||0,0)} / ${num(st.historical_symbols_total||historyTotal,0)} mã`;const context=document.querySelector('#overview-data-context');if(context){if(st.last_message_at){context.innerHTML=`<b>Đã có quote SSI.</b> Đang hiển thị ${num(st.rows_valid||0,0)} snapshot hợp lệ; biểu đồ thanh khoản sẽ có dữ liệu sau khi collector nhận và lưu báo giá.`;}else{const wsState=st.connection_status==='connected'?'đã kết nối':'chưa kết nối';const subState=st.subscription_status==='confirmed'?`Đăng ký quote đã xác nhận cho ${(st.subscribed_boards||[]).join(', ')}.`:`Đăng ký quote: ${st.subscription_status||'đang kiểm tra'}.`;const marketState=st.market_session_expected?'Đang trong giờ giao dịch nhưng chưa nhận quote; nếu kéo dài, hãy kiểm tra quyền streaming.':'Hiện ngoài giờ giao dịch nên chưa có snapshot sổ lệnh mới.';context.innerHTML=`<b>Đã tải ${num(st.universe_symbols||historyTotal,0)} mã và OHLC lịch sử từ SSI.</b> WebSocket ${wsState}. ${subState} ${marketState} Spread/depth cần giá bid/ask và khối lượng quote; không thể tính từ nến OHLC.`;}}
      }
      const note=document.querySelector('#historical-price-note');if(note)note.textContent=`Nguồn SSI Market Data API · OHLC ngày (1d). ${chosen==='__all__'?'Biểu đồ là số mã tăng/giảm so với phiên trước; bảng thể hiện giá lịch sử thực tế theo mã. Đây không phải thanh khoản sổ lệnh.':'Đồ thị giá lịch sử của mã đã chọn; không dùng OHLC để tính spread, depth hay imbalance.'}`;
    }catch(error){node.innerHTML='<div class="empty-state">Không tải được danh mục / giá SSI: '+esc(error.message)+'</div>';}
  }

  function overviewCard(id,title,value,detail){const node=document.querySelector('#'+id);if(!node)return;const card=node.closest('.kpi-card');if(card){const label=card.querySelector('span'),small=card.querySelector('small');if(label)label.textContent=title;if(small)small.textContent=detail;}node.textContent=value;}
  function overviewChartHeading(nodeId,title,description){const node=document.querySelector('#'+nodeId),panel=node?.closest('.panel');if(!panel)return;const h=panel.querySelector('.panel-heading h2'),p=panel.querySelector('.panel-heading p');if(h)h.textContent=title;if(p)p.textContent=description;}
  let rankingPopover=null,rankingPopoverTimer=null,rankingPopoverButton=null,rankingPopoverPinned=false;
  function hideRankingPopover(){clearTimeout(rankingPopoverTimer);if(rankingPopover)rankingPopover.style.display='none';rankingPopoverButton?.setAttribute('aria-expanded','false');rankingPopoverButton=null;rankingPopoverPinned=false;}
  function showRankingPopover(button,pinned=false){
    clearTimeout(rankingPopoverTimer);if(!rankingPopover){rankingPopover=document.createElement('div');rankingPopover.id='overview-ranking-tooltip';rankingPopover.className='ranking-explainer-popover';rankingPopover.setAttribute('role','tooltip');document.body.appendChild(rankingPopover);rankingPopover.addEventListener('pointerenter',()=>clearTimeout(rankingPopoverTimer));rankingPopover.addEventListener('pointerleave',()=>{if(!rankingPopoverPinned)rankingPopoverTimer=setTimeout(hideRankingPopover,140);});}
    if(rankingPopoverButton&&rankingPopoverButton!==button)rankingPopoverButton.setAttribute('aria-expanded','false');
    rankingPopoverButton=button;rankingPopoverPinned=pinned;button.setAttribute('aria-expanded','true');
    const d=button.dataset;rankingPopover.innerHTML=`<div class="rank-popover-heading"><b>${esc(d.symbol)} · ${esc(d.board)}</b><span>Hạng #${esc(d.rank)} / ${esc(d.total)}</span><button type="button" class="rank-popover-close" aria-label="Đóng phần giải thích">×</button></div><div class="rank-popover-stats"><div><small>Spread trung vị</small><strong>${esc(d.ticks)} bước giá · ${esc(d.bps)} bps</strong></div><div><small>Độ sâu top 3 trung vị</small><strong>${esc(d.depth)} đồng</strong></div><div><small>Dữ liệu dùng để tính</small><strong>${esc(d.bars)} bar phút · ${esc(d.snapshots)} snapshot</strong></div></div><p>Spread nhỏ nghĩa là giá mua và giá bán gần nhau hơn. Độ sâu top 3 cho biết giá trị đang chờ ở ba mức giá đầu.</p><p>Thứ tự ưu tiên: ít bước giá hơn → spread bps thấp hơn → độ sâu cao hơn nếu hòa. Đây là so sánh thống kê, không bảo đảm lệnh sẽ khớp.</p><small>Cửa sổ ${esc(d.window)} phút · dữ liệu SSI</small>`;
    rankingPopover.querySelector('.rank-popover-close').addEventListener('click',event=>{event.stopPropagation();hideRankingPopover();});
    rankingPopover.style.display='block';const anchor=button.getBoundingClientRect(),width=rankingPopover.offsetWidth,height=rankingPopover.offsetHeight,gap=10,edge=10;let left=Math.max(edge,Math.min(anchor.left,innerWidth-width-edge)),top=anchor.bottom+gap;if(top+height>innerHeight-edge)top=anchor.top-height-gap;if(top<edge)top=edge;rankingPopover.style.left=`${left}px`;rankingPopover.style.top=`${top}px`;
  }
  function renderOverviewSpreadRanking(ranking,windowMinutes,status){
    const panel=document.querySelector('#overview-ranking'),items=ranking.lowest_spread||[],eligible=Number(ranking.eligible_symbols||items.length),isLive=status?.market_state==='IN_SESSION',refresh=Number(status?.refresh_seconds||5);
    overviewChartHeading('overview-ranking','Top mã có chênh lệch mua – bán thấp',isLive?`Trong phiên · tự xếp lại khi SSI có dữ liệu mới (khoảng ${refresh} giây) · cửa sổ ${windowMinutes} phút`:`Ngoài phiên · giữ dữ liệu SSI gần nhất · cửa sổ ${windowMinutes} phút`);
    if(!panel)return;
    if(!items.length){hideRankingPopover();panel.innerHTML=`<div class="ranking-empty">Chưa có mã đủ điều kiện xếp hạng trong ${windowMinutes} phút. Cần tối thiểu ${num(ranking.minimum_eligible_samples,0)} bar phút hợp lệ cho mỗi mã.</div>`;return;}
    const comparisonDate=ranking.comparison_session_date?formatVnDate(ranking.comparison_session_date):null;
    const tableRows=items.slice(0,5).map((r,index)=>{
      const rank=index+1,previous=r.previous_rank==null?null:Number(r.previous_rank),delta=r.rank_change==null?null:Number(r.rank_change);
      const move=delta==null?`<span class="rank-change new" title="Không có thứ hạng đủ điều kiện để đối chiếu ở phiên trước">Chưa có hạng trước</span>`:delta>0?`<span class="rank-change up" title="Tăng ${delta} bậc so với cùng khung giờ phiên ${esc(comparisonDate||'trước')}">↑ ${delta} hạng</span>`:delta<0?`<span class="rank-change down" title="Giảm ${Math.abs(delta)} bậc so với cùng khung giờ phiên ${esc(comparisonDate||'trước')}">↓ ${Math.abs(delta)} hạng</span>`:'<span class="rank-change same" title="Thứ hạng không đổi so với cùng khung giờ phiên trước">0 hạng</span>';
      const bars=Number(r.sample_count??r.bar_count??0),snapshots=Number(r.snapshot_count??0);
      return `<tr class="ranking-row${delta>0?' moved-up':delta<0?' moved-down':''}"><td><span class="rank-number">${rank}</span>${move}</td><td><button type="button" class="rank-symbol" aria-controls="overview-ranking-tooltip" aria-expanded="false" aria-label="Xem phân tích ${esc(r.symbol)}" data-symbol="${esc(r.symbol)}" data-board="${esc(r.board||'—')}" data-rank="${rank}" data-total="${eligible}" data-ticks="${num(r.median_spread_ticks,2)}" data-bps="${num(r.median_spread_bps,2)}" data-depth="${num(r.median_total_depth_value,0)}" data-bars="${num(bars,0)}" data-snapshots="${num(snapshots,0)}" data-window="${windowMinutes}">${esc(r.symbol)}</button><small class="rank-exchange">${esc(r.board||'—')}</small><button type="button" class="quick-watch-button" data-quick-watch-symbol="${esc(r.symbol)}">＋ Danh mục</button></td></tr>`;
    }).join('');
    hideRankingPopover();
    const basis=comparisonDate?`So với phiên ${comparisonDate}, cùng khung giờ`:(ranking.rank_change_basis||'Chưa có dữ liệu lịch sử để so sánh');
    panel.innerHTML=`<div class="ranking-board"><div class="ranking-board-meta"><span class="ranking-live-dot${isLive?' live':''}"></span><b>${num(eligible,0)} mã đủ điều kiện</b><span>${isLive?'Thứ hạng cập nhật theo SSI':'Thứ hạng từ bar SSI đã lưu'} · ${esc(basis)}</span></div><div class="ranking-table-scroll"><table class="ranking-table ranking-preview-table"><thead><tr><th>HẠNG · THAY ĐỔI</th><th>MÃ / SÀN</th></tr></thead><tbody>${tableRows}</tbody></table></div><div class="ranking-board-note">↑ tăng hạng · ↓ giảm hạng · 0 hạng không đổi. So sánh thứ hạng hiện tại với cùng khung giờ của phiên SSI trước đó; thiếu dữ liệu thì không suy đoán.</div></div>`;
    const tipButtons=panel.querySelectorAll('.rank-symbol');tipButtons.forEach(button=>{button.addEventListener('pointerenter',()=>{if(!(rankingPopoverButton===button&&rankingPopoverPinned))showRankingPopover(button);});button.addEventListener('pointerleave',()=>{if(!rankingPopoverPinned)rankingPopoverTimer=setTimeout(hideRankingPopover,140);});button.addEventListener('focus',()=>{if(!(rankingPopoverButton===button&&rankingPopoverPinned))showRankingPopover(button);});button.addEventListener('blur',()=>{if(!rankingPopoverPinned)rankingPopoverTimer=setTimeout(hideRankingPopover,140);});button.addEventListener('click',event=>{event.stopPropagation();if(rankingPopoverButton===button&&rankingPopoverPinned)hideRankingPopover();else showRankingPopover(button,true);});});
  }
  function restoreLiquidityOverviewHeadings(){
    overviewChartHeading('spread-chart','Diễn biến chênh lệch giá mua – bán (spread)','Chênh lệch theo báo giá trong các ảnh chụp dữ liệu nhận được');
    overviewChartHeading('depth-chart','Độ sâu hai phía sổ lệnh','Mua phía trên trục 0 · bán phía dưới · trung vị nhóm đủ dữ liệu');
    overviewChartHeading('imbalance-chart','Cân bằng khối lượng mua / bán (bid / ask)','Mất cân bằng khối lượng chờ (queue imbalance) từ −1 đến +1');
    overviewChartHeading('overview-ranking','Xếp hạng chênh lệch giá (spread)','Trung vị trong cửa sổ đang chọn');
  }
  function renderMarketTurnoverChart(){
    const host=document.querySelector('#market-turnover-chart'),data=turnoverPayload;if(!host||!data)return;
    const rows=(data.rows||[]).filter(r=>r.value!=null),rect=host.getBoundingClientRect(),w=Math.max(300,Math.floor(rect.width)),h=Math.max(260,Math.floor(rect.height||320));
    if(!rows.length){host.style.height='128px';host.style.minHeight='120px';host.innerHTML='<div class="empty-state">'+esc(data.pending_ohlc_missing?data.note:'SSI chưa có dữ liệu OHLC trong khoảng thời gian đã chọn.')+'</div>';turnoverSignature='';return;}
    host.style.height='330px';host.style.minHeight='300px';
    const signature=JSON.stringify([turnoverMetric,w,h,rows.map(r=>[r.trading_date,r.value,r.ma20,r.is_spike,r.is_incomplete,r.is_partial_coverage,r.coverage_present,r.coverage_expected])]);if(signature===turnoverSignature)return;turnoverSignature=signature;
    const pad={l:58,r:18,t:18,b:49},pw=w-pad.l-pad.r,ph=h-pad.t-pad.b,maximum=Math.max(...rows.map(r=>Number(r.value)||0),...rows.map(r=>Number(r.ma20)||0),0),step=maximum<=0?1:Math.pow(10,Math.floor(Math.log10(maximum))),rough=maximum/5,scale=[1,2,2.5,5,10].find(x=>x*step>=rough)||10,maxY=Math.ceil(maximum/(step*scale))*step*scale||1;
    const y=v=>pad.t+ph-(Number(v||0)/maxY)*ph,slot=pw/rows.length,bw=Math.min(18,Math.max(2,slot*.68)),x=i=>pad.l+(i+.5)*slot;
    const grid=Array.from({length:6},(_,i)=>{const v=maxY*i/5,yy=y(v);return `<line x1="${pad.l}" x2="${w-pad.r}" y1="${yy}" y2="${yy}" class="turnover-grid"/><text x="${pad.l-8}" y="${yy+4}" text-anchor="end">${num(v,1)}</text>`}).join('');
    const bars=rows.map((r,i)=>{const yy=y(r.value),xx=x(i)-bw/2,fill=r.is_spike?'#087d4b':'#559d79',latest=i===rows.length-1,stroke=(latest||r.is_incomplete||r.is_partial_coverage)?'stroke="#153d2a" stroke-width="2"':'',dashed=(r.is_incomplete||r.is_partial_coverage)?'stroke-dasharray="4 3"':'';return `<rect class="turnover-bar" data-index="${i}" x="${xx}" y="${yy}" width="${bw}" height="${Math.max(1,pad.t+ph-yy)}" rx="3" fill="${fill}" ${stroke} ${dashed}><title>${esc(r.trading_date)} · ${num(r.value,3)} ${esc(data.unit)}</title></rect>`}).join('');
    const line=rows.map((r,i)=>r.ma20==null?null:[x(i),y(r.ma20)]).filter(Boolean);const paths=[];let segment=[];for(let i=0;i<rows.length;i++){if(rows[i].ma20==null){if(segment.length)paths.push(segment);segment=[];}else segment.push([x(i),y(rows[i].ma20)]);}if(segment.length)paths.push(segment);
    const ma=paths.map(points=>`<path d="${points.map((p,i)=>`${i?'L':'M'}${p[0]},${p[1]}`).join(' ')}" class="turnover-ma"/>`).join('');
    const dateLabel=value=>formatVnDate(value);
    const labelCount=Math.max(2,Math.min(8,Math.floor(pw/46),rows.length)),labelIndexes=new Set(Array.from({length:labelCount},(_,i)=>Math.round(i*(rows.length-1)/(labelCount-1)))),labels=rows.map((r,i)=>labelIndexes.has(i)?`<text x="${x(i)}" y="${h-20}" text-anchor="middle">${esc(dateLabel(r.trading_date))}</text>`:'').join('');
    host.innerHTML=`<svg class="turnover-svg" width="${w}" height="${h}" role="img" aria-label="${turnoverMetric==='value'?'Giá trị khớp, nghìn tỷ đồng':'Khối lượng khớp, triệu cổ phiếu'} theo phiên"><text x="${pad.l}" y="13" class="turnover-axis-title">${turnoverMetric==='value'?'Giá trị khớp · nghìn tỷ đồng':'Khối lượng khớp · triệu cổ phiếu'}</text>${grid}<line x1="${pad.l}" x2="${w-pad.r}" y1="${pad.t+ph}" y2="${pad.t+ph}" class="turnover-baseline"/>${bars}${ma}${labels}<text x="${pad.l+pw/2}" y="${h-2}" text-anchor="middle">Phiên giao dịch · ngày/tháng</text></svg>`;
    let tip=document.querySelector('#turnover-tooltip');if(!tip){tip=document.createElement('div');tip.id='turnover-tooltip';tip.className='turnover-tooltip';tip.hidden=true;document.body.appendChild(tip);}
    host.querySelectorAll('.turnover-bar').forEach(bar=>{const show=e=>{const r=rows[Number(bar.dataset.index)],tip=document.querySelector('#turnover-tooltip');if(!r||!tip)return;const weekday=new Date(String(r.trading_date).replaceAll('/','-')+'T12:00:00+07:00').toLocaleDateString('vi-VN',{weekday:'long'});tip.innerHTML=`<b>${weekday}, ${esc(r.trading_date)}</b><br>${turnoverMetric==='value'?'Giá trị khớp':'Khối lượng'}: <strong>${num(r.value,3)} ${esc(data.unit)}</strong><br>So với trung bình 20 phiên: ${r.ratio_to_ma20_pct==null?'Chưa đủ 20 phiên':num(r.ratio_to_ma20_pct,1)+'%'}<br>Mã tăng / giảm: ${num(r.advancing,0)} / ${num(r.declining,0)}${r.is_incomplete?'<br>Phiên đang diễn ra':r.is_partial_coverage?`<br>Độ phủ OHLC SSI: ${num(r.coverage_present,0)}/${num(r.coverage_expected,0)} mã; phiên tính từ dữ liệu sẵn có`:''}`;tip.hidden=false;const left=Math.min(e.clientX+14,window.innerWidth-tip.offsetWidth-12),top=Math.max(70,Math.min(e.clientY+14,window.innerHeight-tip.offsetHeight-12));tip.style.left=left+'px';tip.style.top=top+'px';};bar.addEventListener('pointermove',show);bar.addEventListener('pointerdown',show);bar.addEventListener('pointerleave',()=>{const tip=document.querySelector('#turnover-tooltip');if(tip)tip.hidden=true;});});
    if(!turnoverResizeObserver&&window.ResizeObserver){turnoverResizeObserver=new ResizeObserver(()=>{turnoverSignature='';renderMarketTurnoverChart();});turnoverResizeObserver.observe(host);}
  }

  async function loadMarketTurnover(){
    const host=document.querySelector('#market-turnover-chart');if(!host)return;
    const priorTip=document.querySelector('#turnover-tooltip');if(priorTip)priorTip.hidden=true;
    try{const range=overviewDateParams();turnoverPayload=await call(`/api/market-turnover?limit=10000&metric=${turnoverMetric}${range.query}`);const k=turnoverPayload.kpis||{},unit=turnoverPayload.unit;
      document.querySelectorAll('[data-turnover-metric]').forEach(b=>{const selected=b.dataset.turnoverMetric===turnoverMetric;b.classList.toggle('active',selected);b.setAttribute('aria-pressed',String(selected));});
      const seriesDate=turnoverPayload.series_latest_date||k.latest_date||'—';
      const cards=document.querySelector('#market-turnover-kpis');if(cards)cards.innerHTML=`<article><span>Phiên OHLC trong biểu đồ gần nhất · ${esc(seriesDate)}</span><strong>${num(k.latest_value,2)} ${esc(unit)}</strong><small>Tổng hợp dữ liệu ${esc(turnoverPayload.source||'SSI')}</small></article><article><span>Bằng bao nhiêu % trung bình 20 phiên</span><strong>${k.ratio_to_average20_pct==null?'Chưa đủ dữ liệu':num(k.ratio_to_average20_pct,0)+'%'}</strong><small>100% = bằng trung bình · ${k.average20==null?'—':num(k.average20,2)+' '+esc(unit)}</small></article><article><span>Vị trí trong ${num(k.sessions_in_window,0)} phiên</span><strong>${k.higher_than_pct==null?'—':'Cao hơn '+num(k.higher_than_pct,0)+'%'}</strong><small>Số phiên có giá trị thấp hơn</small></article>`;
      const note=document.querySelector('#market-turnover-note');if(note){const asOf=turnoverPayload.as_of?fmtDateTime(turnoverPayload.as_of):'chưa rõ thời điểm';note.textContent=`Nguồn OHLC SSI · dữ liệu mới nhất được lưu ${asOf}. Biểu đồ tổng hợp đến phiên ${seriesDate}; phiên thiếu độ phủ vẫn dùng bản ghi SSI thực có và được đánh dấu riêng. ${turnoverPayload.note||''}`;}
      renderMarketTurnoverChart();
    }catch{if(turnoverPayload?.rows?.length){const note=document.querySelector('#market-turnover-note');if(note)note.textContent='Chưa làm mới được OHLC SSI; biểu đồ vẫn giữ nguyên dữ liệu lịch sử đã tải.';host.dataset.widgetState='stale';host.setAttribute('aria-description','Đang hiển thị OHLC SSI đã tải gần nhất; lượt làm mới mới nhất chưa thành công.');}else{host.style.height='128px';host.style.minHeight='120px';host.innerHTML='<div class="empty-state research-retry"><span>Chưa tải được dữ liệu OHLC SSI trong khoảng đã chọn.</span><button id="retry-market-turnover" type="button" class="button button-secondary">Thử lại</button></div>';const note=document.querySelector('#market-turnover-note');if(note)note.textContent='Khối dữ liệu giá theo ngày tải độc lập; các biểu đồ thanh khoản khác vẫn hoạt động.';}}
  }

  async function showHistoricalInOverview(chosen,status){
    const all=chosen==='__all__';
    if(all){
      const range=overviewDateParams(),d=await call(`/api/market-breadth?limit=10000${range.query}`),rawRows=d.rows||[],rawLatest=d.rows?.[d.rows.length-1];
      const expectedDate=String(status.session_date||'').replaceAll('-','/'),actualDate=String(rawLatest?.trading_date||'').replaceAll('-','/');
      const syncState=status.meta?.session_sync?.status;
      const pendingSync=!!expectedDate&&(expectedDate!==actualDate||['not_started','closed_ohlc_pending','ohlc_incomplete'].includes(syncState));
      const rows=rawRows,latest=rows[rows.length-1],sync=status.meta?.session_sync||{},pendingCoverage=d.pending_session_coverage||{present:sync.ohlc_symbols||0,expected:sync.ohlc_expected||0},coverageText=pendingCoverage.expected?`OHLC ${formatVnDate(expectedDate)} có ${num(pendingCoverage.present,0)}/${num(pendingCoverage.expected,0)} mã; mã không có bản ghi không được tính là đứng giá.`:'';
      const historyDateLabel=pendingSync?`Có dữ liệu · ${formatVnDate(expectedDate)}`:(latest?.trading_date||'—');
      overviewCard('kpi-symbols','Mã có dữ liệu lịch sử',num(status.historical_symbols_with_data||0,0),'Cổ phiếu có OHLC ngày từ SSI');
      overviewCard('kpi-spread','Mã tăng giá',num(latest?.advancing,0),`Phiên ${latest?.trading_date||'—'} · so với phiên trước`);
      overviewCard('kpi-depth','Khối lượng giao dịch thị trường',num(Number(latest?.total_volume||0)/1e6,1),'Triệu cổ phiếu · OHLC SSI');
      overviewCard('kpi-imbalance','Mã giảm giá',num(latest?.declining,0),'Từ giá đóng cửa phiên gần nhất');
      overviewCard('kpi-valid',pendingSync?'OHLC phiên mới nhất':'Phiên lịch sử mới nhất',historyDateLabel,pendingSync?`${coverageText} Mốc dữ liệu ${fmtDateTime(status.meta?.data_as_of||status.data_as_of)}`:`${num(latest?.unchanged,0)} mã đứng giá`);
      overviewChartHeading('spread-chart','Độ rộng thị trường SSI','Số mã tăng / giảm theo phiên, tính từ OHLC ngày');
      lineChart(document.querySelector('#spread-chart'),[{name:'Mã tăng giá · xanh lá',rows:rows.map(r=>({...r,count:r.advancing})),color:'#008A45'},{name:'Mã giảm giá · đỏ',rows:rows.map(r=>({...r,count:r.declining})),color:'#E2231A'}],'count','Số mã tăng / giảm theo phiên');
      setChartNote('spread-chart','Hai đường lần lượt là số mã tăng và số mã giảm toàn thị trường trong từng phiên; so sánh giá đóng cửa OHLC SSI với phiên liền trước. Không phải giá cổ phiếu hay spread.');
      overviewChartHeading('depth-chart','Độ sâu hai phía sổ lệnh','Các phiên có bar SSI trong khoảng lịch sử đang chọn');
      overviewChartHeading('imbalance-chart','Chênh lệch số mã tăng – giảm','Thống kê giá lịch sử; không phải mất cân bằng sổ lệnh');
      advanceDeclineChart(document.querySelector('#imbalance-chart'),rows);
      setChartNote('imbalance-chart','Mỗi cột là số mã tăng trừ số mã giảm trong phiên. Cột xanh phía trên 0: mã tăng nhiều hơn; cột đỏ dưới 0: mã giảm nhiều hơn. Đây là thống kê từ OHLC SSI, không phải mất cân bằng sổ lệnh.');
      document.querySelector('#imbalance-reading').textContent=`Phiên ${latest?.trading_date||'—'}: ${num(latest?.advancing,0)} mã tăng, ${num(latest?.declining,0)} mã giảm, ${num(latest?.unchanged,0)} mã đứng giá.`;
      const intradaySavedDate=depthPayload?.session_date,intradaySavedLast=depthPayload?.rows?.at(-1)?.minute,intradayFreshness=intradaySavedDate?`Dữ liệu sổ lệnh cuối đã lưu: phiên ${formatVnDate(intradaySavedDate)}${intradaySavedLast?`, ${formatVnTime(intradaySavedLast)}`:''}.`:'Chưa có bar thanh khoản SSI đã lưu để đối chiếu.';
      document.querySelector('#overview-analysis').textContent=pendingSync?`Ngoài giờ giao dịch · phiên SSI ${formatVnDate(expectedDate)} được cập nhật từ ${num(pendingCoverage.present,0)}/${num(pendingCoverage.expected,0)} mã có OHLC; lưu gần nhất lúc ${fmtDateTime(status.meta?.data_as_of||status.data_as_of)}. Biểu đồ độ rộng dùng bản ghi có sẵn; mã thiếu bản ghi không được tính là đứng giá. Đây là OHLC lịch sử, không phải realtime hoặc spread/depth. ${intradayFreshness}`:`Ngoài giờ giao dịch · dữ liệu OHLC lịch sử SSI cho ${num(status.historical_symbols_with_data||0,0)} mã. Các thẻ và biểu đồ trên đang tóm tắt giá/khối lượng phiên ${latest?.trading_date||'—'}; đây không phải chỉ số spread/depth của sổ lệnh. ${intradayFreshness}`;
    }else{
      const range=overviewDateParams(),d=await call(`/api/price-history?symbol=${encodeURIComponent(chosen)}&limit=10000${range.query}`),rawRows=(d.series_by_symbol||{})[chosen]||[];
      const expectedDate=String(status.session_date||'').replaceAll('-','/'),rawLatest=rawRows[rawRows.length-1],actualDate=String(rawLatest?.trading_date||'').replaceAll('-','/'),syncState=status.meta?.session_sync?.status;
      const pendingSync=!!expectedDate&&(expectedDate!==actualDate||['not_started','closed_ohlc_pending','ohlc_incomplete'].includes(syncState));
      const rows=rawRows,latest=rows[rows.length-1],prior=rows[rows.length-2];
      const change=latest&&prior&&Number(prior.close_price)?(Number(latest.close_price)-Number(prior.close_price))*100/Number(prior.close_price):null;
      overviewCard('kpi-symbols','Số phiên có lịch sử',num(rows.length,0),`OHLC ngày · ${chosen}`);
      overviewCard('kpi-spread','Giá đóng cửa',latest?num(latest.close_price,0)+' đ':'—',`Phiên ${latest?.trading_date||'—'} · SSI`);
      overviewCard('kpi-depth','Khối lượng giao dịch',num(latest?.volume,0),'Cổ phiếu · phiên mới nhất');
      overviewCard('kpi-imbalance','Biến động giá',change==null?'—':`${change>=0?'+':''}${num(change,2)}%`,'So với giá đóng cửa phiên trước');
      overviewCard('kpi-valid',pendingSync?'OHLC phiên cần đồng bộ':'Phiên lịch sử mới nhất',pendingSync?`Chưa đồng bộ · ${expectedDate}`:(latest?.trading_date||'—'),pendingSync?`SSI chưa đồng bộ OHLC ngày ${expectedDate}`:latest?`${num(latest.high_price,0)} / ${num(latest.low_price,0)} đ cao/thấp`:'Chưa có nến OHLC');
      overviewChartHeading('spread-chart',`Giá đóng cửa lịch sử · ${chosen}`,'OHLC ngày SSI · di chuột để xem ngày và giá');
      lineChart(document.querySelector('#spread-chart'),[{name:chosen,rows,color:'#008A45'}],'close_price',`Giá đóng cửa ${chosen} · đồng/cổ phiếu`);
      setChartNote('spread-chart',`Một đường thể hiện giá đóng cửa SSI của riêng ${chosen}, đơn vị đồng/cổ phiếu; dữ liệu theo ngày, không phải quote realtime.`);
      overviewChartHeading('depth-chart','Độ sâu hai phía sổ lệnh','Các phiên có bar SSI trong khoảng lịch sử đang chọn');
      overviewChartHeading('imbalance-chart',`Biến động giá theo phiên · ${chosen}`,'Phần trăm thay đổi từ giá đóng cửa phiên trước');
      const moves=rows.map((r,i)=>({...r,value:i&&Number(rows[i-1].close_price)?(Number(r.close_price)-Number(rows[i-1].close_price))*100/Number(rows[i-1].close_price):null}));lineChart(document.querySelector('#imbalance-chart'),[{name:'Thay đổi giá',rows:moves,color:'#008A45'}],'value',`Thay đổi giá ${chosen} · %`);
      setChartNote('imbalance-chart',`Một đường là phần trăm thay đổi giá đóng cửa của ${chosen} so với phiên trước; không phải chỉ số mất cân bằng sổ lệnh.`);
      overviewChartHeading('overview-ranking',`Biên độ giá gần nhất · ${chosen}`,'Giá cao nhất trừ giá thấp nhất theo ngày');
      document.querySelector('#overview-ranking').innerHTML=latest?window.table(['Mã','Ngày','Cao nhất','Thấp nhất','Biên độ','Giá trị giao dịch'],[[`<b>${esc(chosen)}</b>`,esc(latest.trading_date),num(latest.high_price,0)+' đ',num(latest.low_price,0)+' đ',num(Number(latest.high_price)-Number(latest.low_price),0)+' đ',num(Number(latest.value||0)/1e9,2)+' tỷ']]):'<div class="empty-state">SSI chưa có OHLC cho mã đã chọn.</div>';
      document.querySelector('#imbalance-reading').textContent=latest?`${chosen} đóng cửa ${num(latest.close_price,0)} đ ngày ${latest.trading_date}; thay đổi phiên ${change==null?'—':num(change,2)+'%'}.`:'Chưa có OHLC lịch sử cho mã này.';
      document.querySelector('#overview-analysis').textContent=pendingSync?`Ngoài giờ giao dịch · Dữ liệu OHLC ngày ${expectedDate} chưa được đồng bộ từ SSI. Đang giữ dữ liệu gần nhất của ${chosen} ở phiên ${latest?.trading_date||'—'} và đánh dấu rõ là dữ liệu cũ.`:`Ngoài giờ giao dịch · OHLC SSI của ${chosen}: đóng cửa ${num(latest?.close_price,0)} đ, khối lượng ${num(latest?.volume,0)} cổ phiếu, phiên ${latest?.trading_date||'—'}. Các số liệu này là lịch sử, không phải quote sổ lệnh.`;
      void historicalPriceChart(chosen,10000);
    }
    const statusText=document.querySelector('#overview-data-context');if(statusText)statusText.textContent=`WebSocket SSI ${status.connection_status==='connected'?'đã kết nối':'chưa kết nối'} · đăng ký quote ${status.subscription_status==='confirmed'?'đã xác nhận cho '+(status.subscribed_boards||[]).join(', '):status.subscription_status||'đang kiểm tra'}. Ngoài phiên: đang hiển thị giá/khối lượng lịch sử ở cùng các thẻ và biểu đồ tổng quan.`;
  }


  let overviewFiltersHydrated=false,overviewRefreshPromise=null,overviewRefreshQueued=false;
  function refreshOverviewForFilter(){if(overviewRefreshPromise)overviewRefreshQueued=true;return enhancedOverview();}
  async function enhancedOverview(){
    if(overviewRefreshPromise)return overviewRefreshPromise;
    overviewRefreshPromise=(async()=>{
    let overviewCoreRendered=false;
    try{
      const previousLatestDate=overviewDataStore.bounds?.latest_date;
      const freshBounds=await loadOverviewDateBounds();
      if(overviewFiltersHydrated&&freshBounds?.latest_date&&previousLatestDate&&freshBounds.latest_date!==previousLatestDate&&overviewDataStore.filters.preset!=='custom')setOverviewRange(overviewDataStore.filters.preset||'1y');
      if(!overviewFiltersHydrated){
        const params=new URLSearchParams(location.search),preset=params.get('preset');
        if(['1m','3m','6m','1y','3y','5y','all'].includes(preset))setOverviewRange(preset);
        else if(params.get('from')||params.get('to')){const from=document.querySelector('#overview-history-from'),to=document.querySelector('#overview-history-to');if(from)from.value=params.get('from')||earliestStoredDate();if(to)to.value=params.get('to')||latestStoredDate();overviewDataStore.filters.preset='custom';overviewDataStore.filters.dateFrom=from?.value||'';overviewDataStore.filters.dateTo=to?.value||'';syncOverviewUrl();}
        else setOverviewRange('1m');
        overviewFiltersHydrated=true;
      }
      await enhancedSymbols();
      void loadMarketTurnover();
      const chosen=document.querySelector('#overview-symbol').value||'__all__',windowMinutes=Number(document.querySelector('#overview-limit').value||60);
      overviewDataStore.filters.symbol=chosen;overviewDataStore.filters.chartWindow=windowMinutes;syncOverviewUrl();
      void loadOverviewDepth(windowMinutes);
      const status=await call('/api/status');
      const marketClosed=status.market_state==='CLOSED';
      if(marketClosed){
        const jobs=[
          showHistoricalInOverview(chosen,status).catch(()=>{
            const analysis=document.querySelector('#overview-analysis');
            if(analysis)analysis.textContent='Chưa tải được OHLC lịch sử SSI; các biểu đồ thanh khoản vẫn được giữ riêng.';
          }),
          loadResearchPanels(null,windowMinutes,status).catch(()=>{
            for(const id of ['intraday-profile','spread-compare','coverage-chart','liquidity-map']){
              const node=document.querySelector('#'+id);
              if(node)node.innerHTML='<div class="empty-state">Chưa tải được dữ liệu cho biểu đồ này. Các mục khác vẫn hoạt động.</div>';
            }
          }),
          call(`/api/trades?limit=25${chosen!=='__all__'?'&symbol='+encodeURIComponent(chosen):''}`)
            .then(renderTradeTape).catch(()=>renderTradeTape({trades:[]})),
          call(`/api/rankings?window_minutes=${windowMinutes}`)
            .then(ranking=>renderOverviewSpreadRanking(ranking,windowMinutes,status))
            .catch(()=>{const node=document.querySelector('#overview-ranking');if(node)node.innerHTML='<div class="ranking-empty">Chưa tải được bảng xếp hạng dữ liệu SSI.</div>';})
        ];
        await Promise.all(jobs);
        return;
      }
      const [d,tradeData,profileData]=await Promise.all([
        call(`/api/overview?window_minutes=${windowMinutes}${chosen!=='__all__'?'&symbol='+encodeURIComponent(chosen):''}`),
        call(`/api/trades?limit=25${chosen!=='__all__'?'&symbol='+encodeURIComponent(chosen):''}`),
        call(`/api/intraday-profile?window_minutes=${windowMinutes}${overviewDateParams().query}${chosen!=='__all__'?'&symbol='+encodeURIComponent(chosen):''}`)
      ]);
      renderTradeTape(tradeData);restoreLiquidityOverviewHeadings();
      const groups=d.series_by_symbol||{},active=chosen==='__all__'?Object.keys(groups):[chosen],rows=active.flatMap(s=>groups[s]||[]),valid=rows.filter(r=>r.validation_status==='valid');
      const med=(arr)=>{arr=arr.filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number).sort((a,b)=>a-b);return arr.length?(arr[(arr.length-1)>>1]+arr[arr.length>>1])/2:null;};
      overviewCard('kpi-symbols','Mã có quote trong cửa sổ',num(active.length,0),`Mỗi mã lấy quote mới nhất trong ${windowMinutes} phút`);
      overviewCard('kpi-spread','Spread trung vị',num(med(valid.map(r=>r.spread_bps))),'Quote mới nhất/mã · điểm cơ bản (bps)');
      overviewCard('kpi-depth','Độ sâu trung vị',num(med(valid.map(r=>r.total_depth_value)),0),'Quote mới nhất/mã · đồng × cổ phiếu');
      overviewCard('kpi-imbalance','Mất cân bằng khối lượng chờ',num(med(valid.map(r=>r.queue_imbalance)),3),'Quote mới nhất/mã · −1 đến +1');
      overviewCard('kpi-valid','Quote hợp lệ',rows.length?(valid.length/rows.length*100).toFixed(1)+'%':'—',`${num(valid.length,0)} / ${num(rows.length,0)} mã`);
      document.querySelector('#overview-symbol-label').textContent=chosen==='__all__'?'Tất cả mã':chosen;
      const chartRows=(profileData.profile||[]).map(p=>({minute:p.minute,spread_bps:p.median_spread_bps,total_depth_value:p.median_total_depth_value,queue_imbalance:p.median_queue_imbalance,sample_count:p.symbols_count,snapshot_count:p.snapshot_count}));
      const allMode=chosen==='__all__',line=[{name:allMode?'Trung vị giữa các mã':'Mã '+chosen,rows:chartRows,color:'#008A45'}];
      overviewChartHeading('spread-chart',allMode?'Spread trung vị toàn bộ mã theo phút':`Spread theo phút · ${chosen}`,`Bar một phút đã hoàn tất · cửa sổ ${windowMinutes} phút`);
      overviewChartHeading('imbalance-chart',allMode?'Mất cân bằng trung vị toàn bộ mã':`Mất cân bằng mua/bán · ${chosen}`,`Chỉ số từ cùng bar một phút đã hoàn tất`);
      lineChart(document.querySelector('#spread-chart'),line,'spread_bps','Spread trung vị (điểm cơ bản, bps)');
      lineChart(document.querySelector('#imbalance-chart'),line,'queue_imbalance','Mất cân bằng bid/ask (tỷ lệ)');
      overviewCoreRendered=true;
      const method='Mỗi phút: lấy trung vị snapshot hợp lệ của từng mã, rồi lấy trung vị giữa các mã. Hai biểu đồ spread dùng cùng tập bar, cùng cách tính và cùng số mã tham gia; phút hiện tại chưa hoàn tất bị loại.';
      setChartNote('spread-chart',`${allMode?'Tất cả mã. ':`Chỉ ${chosen}. `}${method} Số mã tham gia hiển thị khi rê chuột.`);
      setChartNote('imbalance-chart',`${allMode?'Tất cả mã. ':`Chỉ ${chosen}. `}${method} Dương nghiêng về khối lượng bid, âm nghiêng về ask.`);
      const period=`${String(profileData.window_start||'').slice(11,16)}–${String(profileData.completed_through_exclusive||'').slice(11,16)} giờ Việt Nam`;
      const latestMin=profileData.profile?.at(-1)?.minute||'—';
      const freshness=!status.market_session_expected?'Ngoài giờ liên tục; đang xem các bar SSI đã hoàn tất gần nhất.':status.processing_delayed?'Luồng SSI đang nhận dữ liệu nhưng bộ lưu có độ trễ.':'SSI đang trong phiên liên tục.';
      const context=document.querySelector('#overview-data-context');if(context)context.textContent=`${freshness} Cửa sổ biểu đồ: ${period} (${windowMinutes} phút), cập nhật đến bar ${latestMin}; loại phút chưa hoàn tất. KPI lấy quote mới nhất của mỗi mã trong đúng cửa sổ. Biểu đồ spread và diễn biến phiên dùng cùng các bar đã hoàn tất; số mã ở mỗi phút hiện khi rê chuột.`;
      try{
        const ranking=await call(`/api/rankings?window_minutes=${windowMinutes}&limit=5`);
        renderOverviewSpreadRanking(ranking,windowMinutes,status);
      }catch{
        const panel=document.querySelector('#overview-ranking');
        if(panel)panel.innerHTML='<div class="ranking-empty">Chưa tải được bảng xếp hạng; các biểu đồ khác vẫn hoạt động.</div>';
      }
      const reading=document.querySelector('#imbalance-reading');if(reading){const value=med(valid.map(r=>r.queue_imbalance));reading.textContent=value==null?'Chưa đủ quote hợp lệ để diễn giải.':Math.abs(value)<.1?'Khối lượng hai phía tương đối cân bằng ở quote mới nhất mỗi mã.':value>0?`Khối lượng bid trội hơn, trung vị ${num(value,3)}.`:`Khối lượng ask trội hơn, trung vị ${num(value,3)}.`;}
      const analysis=document.querySelector('#overview-analysis');if(analysis)analysis.textContent=`Phạm vi KPI: ${num(active.length,0)} mã có quote trong cửa sổ, ${num(valid.length,0)} quote hợp lệ (mỗi mã tối đa một quote mới nhất). Phạm vi biểu đồ: bar một phút đã hoàn tất trong ${windowMinutes} phút. Nguồn SSI FastConnect; đơn vị và phép tính được ghi dưới từng biểu đồ.`;
      await loadResearchPanels(profileData,windowMinutes,status);
    }catch(e){const node=document.querySelector('#spread-chart');if(overviewCoreRendered){const panel=document.querySelector('#overview-ranking');if(panel&&!panel.querySelector('.ranking-board'))panel.innerHTML='<div class="ranking-empty">Bảng xếp hạng đang tải chưa thành công; dữ liệu biểu đồ vẫn được giữ.</div>';}else{const hasPrior=!!node?.dataset.widgetReady;if(hasPrior){for(const url of ['/api/overview','/api/market-depth','/api/rankings','/api/intraday-profile','/api/trades','/api/market-leaders'])setOverviewWidgetState(url,'stale');}else if(node)node.innerHTML='<div class="empty-state research-retry"><span>Chưa tải được biểu đồ thanh khoản trong cửa sổ đã chọn.</span><button type="button" class="button button-secondary" id="retry-overview">Thử lại</button></div>';}}
    })().finally(()=>{overviewRefreshPromise=null;if(overviewRefreshQueued){overviewRefreshQueued=false;setTimeout(()=>void enhancedOverview(),0);}});
    return overviewRefreshPromise;
  }
  async function enhancedDetail(){
    const analysis=document.querySelector('#detail-analysis');
    const ticker=document.querySelector('#detail-symbol')?.value;
    if(!ticker)return;
    try{
      const detailFrom=document.querySelector('#detail-history-from')?.value,detailTo=document.querySelector('#detail-history-to')?.value;
      const detailRange=`${detailFrom?'&from_date='+encodeURIComponent(detailFrom):''}${detailTo?'&to_date='+encodeURIComponent(detailTo):''}`;
      const d=await call('/api/detail/'+encodeURIComponent(ticker)+'?window_sessions=5'+detailRange);
      const widget=name=>d.widgets?.[name]?.status==='ready'?d.widgets[name].data:null;
      const company=widget('company'),book=widget('orderbook'),k=widget('kpis'),charts=widget('charts'),history=widget('history');
      const profile=document.querySelector('#company-profile');
      if(company){profile.querySelector('h2').textContent=company.name||ticker;profile.querySelector('p').textContent=`Mã ${ticker} · ${company.board||'—'}`;profile.querySelector('.company-sector').textContent=company.board||'Cổ phiếu';}
      const companyName=document.querySelector('#detail-company-name');if(companyName)companyName.textContent=company?.name||ticker;
      const statusLabels={quote:'Có quote trong phiên',stale:'Quote cũ',no_quote:'Không có quote trong phiên',one_sided:'Sổ lệnh một phía',outside_session:'Ngoài phiên'};
      const heading=document.querySelector('#view-detail .page-heading > div > p:last-child');
      if(heading)heading.textContent=`${statusLabels[d.state]||'Chưa có dữ liệu'} · Cập nhật theo mốc ${window.fmtTime(d.as_of)} giờ Việt Nam · ${d.quote_age_seconds==null?'chưa có tuổi dữ liệu':`quote ${num(d.quote_age_seconds,0)} giây trước`}.`;
      const latest=book;
      overviewCard('detail-spread','Spread mua–bán',k?.spread_bps==null?'—':num(k.spread_bps),`${k?.spread_ticks==null?'—':num(k.spread_ticks,1)+' tick'} · ${k?.spread_percentile==null?'chưa đủ baseline cùng pha':`phân vị ${num(k.spread_percentile,0)}% trên ${num(k.spread_baseline_count,0)} bar`}`);
      overviewCard('detail-bid-depth','Độ sâu bên mua',num(k?.bid_depth_value,0),`Top 3 · ${k?.depth_percentile==null?'chưa đủ lịch sử cùng pha':`phân vị ${num(k.depth_percentile,0)}%`}`);
      overviewCard('detail-ask-depth','Độ sâu bên bán',num(k?.ask_depth_value,0),'Top 3 mức giá đầu · đồng × cổ phiếu');
      overviewCard('detail-imbalance','Mất cân bằng khối lượng chờ',num(k?.queue_imbalance,3),'Chỉ báo theo khối lượng (−1 đến +1)');
      overviewCard('detail-value-imbalance','Mất cân bằng giá trị chờ',num(k?.value_imbalance,3),'Khác với mất cân bằng khối lượng (queue imbalance)');
      overviewCard('detail-valid','Cảnh báo đang hoạt động',k?.active_alert_count==null?'—':num(k.active_alert_count,0),d.baseline_eligible?'Đủ điều kiện baseline':'Chưa đủ lịch sử để so sánh');
      document.querySelector('#detail-snapshots').textContent=`${num(d.valid_snapshot_count,0)} / ${num(d.snapshot_count,0)} snapshot hợp lệ · ${d.source==='ssi'?'SSI':'nguồn hiện tại'}`;
      const orderbook=document.querySelector('#orderbook'),bookTime=document.querySelector('#book-time'),bookValidity=document.querySelector('#book-validity');
      if(orderbook)renderOrderbookLadder(latest,d.quote_time,d.state);
      if(bookTime){const age=Math.max(0,Number(d.quote_age_seconds)||0),ageLabel=age<60?'vừa cập nhật':age<3600?`${Math.floor(age/60)} phút trước`:age<86400?`${Math.floor(age/3600)} giờ ${Math.floor(age%3600/60)} phút trước`:`${Math.floor(age/86400)} ngày ${Math.floor(age%86400/3600)} giờ trước`;bookTime.textContent=d.quote_time?`Snapshot ${window.fmtTime(d.quote_time)} · ${formatVnDate(d.quote_time)} · ${ageLabel}`:'Chưa có thời điểm quote';}
      if(bookValidity)bookValidity.textContent=statusLabels[d.state]||'Chưa có quote';
      const depthHeading=document.querySelector('#view-detail .detail-depth-panel .panel-heading p');
      const showCumulative=['HNX','UPCOM'].includes(d.board);
      if(depthHeading)depthHeading.textContent=showCumulative?`Đường tích lũy dùng ${num(charts?.levels_used??d.widgets?.charts?.data?.levels_used??3,0)} mức SSI đã lưu · tối đa 10 nếu nguồn cung cấp`:'Giá trị niêm yết ba mức giá đầu theo từng phút';
      if(charts){
        const spreadRows=charts.spread||[];
        overviewChartHeading('detail-spread-chart',`Spread theo thời gian · ${ticker}`,`${charts.session_dates?.length||0} phiên có bar trong khoảng chọn · mốc ${window.fmtTime(d.as_of)} · ${charts.baseline_eligible?'mốc tham chiếu dùng phiên trước':'chưa đủ dữ liệu tham chiếu'}`);
        const lines=[{name:'Spread mã '+ticker,rows:spreadRows,color:'#008A45'}];
        if(charts.baseline_eligible){lines.push({name:'Ngưỡng thuận lợi P25',rows:spreadRows.map(r=>({...r,spread_bps:r.baseline_p25})),color:'#8aa897'},{name:'Ngưỡng căng thẳng P90',rows:spreadRows.map(r=>({...r,spread_bps:r.baseline_p90})),color:'#b6c8bd'});}
        lineChart(document.querySelector('#detail-spread-chart'),lines,'spread_bps',`Spread ${ticker} · điểm cơ bản (bps)`);
        if(showCumulative)renderCumulativeDepthCurve(document.querySelector('#detail-depth-chart'),charts.depth_curve||d.widgets?.charts?.data?.depth_curve);
        else window.renderDepthChart('#detail-depth-chart',(charts.depth||[]).map(r=>({...r,bid_depth_value:r.bid,ask_depth_value:r.ask})));
      }else{
        ['#detail-spread-chart','#detail-depth-chart'].forEach(sel=>{const node=document.querySelector(sel);if(node)node.innerHTML='<div class="empty-state">Biểu đồ chưa có dữ liệu trong cửa sổ thời gian đã chọn.</div>';});
      }
      if(analysis){
        const stateText={quote:'Quote mới trong phiên; có thể xem spread, độ sâu và mô phỏng trong giới hạn dữ liệu đang niêm yết.',stale:'Quote đã quá ngưỡng cập nhật trong phiên. Sổ lệnh được đánh dấu cũ; không dùng để mô phỏng.',no_quote:'Chưa có quote hợp lệ trong phiên. Chỉ hiển thị OHLC lịch sử nếu có.',one_sided:'Sổ lệnh chỉ có dữ liệu một phía; phía thiếu và mô phỏng tương ứng bị giới hạn.',outside_session:'Ngoài giờ giao dịch; quote gần nhất chỉ mang tính lịch sử.'};
        const h=history,latestPrice=h?.latest,prior=h?.prior;
        analysis.innerHTML=`<span class="eyebrow">DIỄN GIẢI DỮ LIỆU</span><h2>${esc(company?.name||ticker)} · ${esc(ticker)}</h2><p>${esc(stateText[d.state]||'Chưa đủ dữ liệu để xác định trạng thái quote.')}${k?.spread_percentile==null?' Chưa đủ lịch sử cùng pha để tính phân vị spread.':` Spread hiện tại ở phân vị ${num(k.spread_percentile,0)}% trên ${num(k.spread_baseline_count,0)} bar baseline.`}</p><p>${latestPrice?`OHLC gần nhất: ${esc(latestPrice.trading_date)} đóng cửa ${num(latestPrice.close_price,0)} đồng; khối lượng ${num(latestPrice.volume,0)} cổ phiếu. Có giao dịch trong ${num(h.sessions_with_trades_in_last_5,0)} trên 5 phiên gần nhất; lần giao dịch gần nhất ${esc(h.last_trade_date||'—')}.`:'Chưa có dữ liệu OHLC ngày đã lưu.'}</p><p>Biểu đồ chỉ thể hiện ${esc(ticker)}. Baseline dùng các bar phút hợp lệ của chính mã này, cùng pha phiên và các phiên trước; biểu đồ độ sâu dùng giá trị niêm yết top 3 bid và ask. Mọi khối trên trang dùng cùng mốc ${window.fmtTime(d.as_of)} giờ Việt Nam.</p>`;
      }
      const alerts=widget('alerts')||[];const alertBox=document.querySelector('#detail-alerts');if(alertBox)alertBox.innerHTML=alerts.length?window.table(['Loại','Mức','Thời điểm','Diễn giải'],alerts.slice(0,10).map(a=>[esc(a.alert_type),esc(a.severity),esc(a.triggered_at),esc(a.explanation)])):'<div class="empty-state">Không có cảnh báo đang hoạt động cho mã này.</div>';
      const historyRows=history?.rows||[],historyTable=document.querySelector('#detail-history-table'),historySummary=document.querySelector('#detail-history-summary');if(historySummary)historySummary.textContent=historyRows.length?`${historyRows.length} phiên · ${num(history?.sessions_with_trades_in_last_5,0)}/5 phiên có giao dịch · gần nhất ${history?.last_trade_date||'—'}`:'Chưa có OHLC lịch sử';if(historyTable){const recent=historyRows.slice(-30).reverse();historyTable.innerHTML=recent.length?window.table(['Ngày','Mở cửa','Cao nhất','Thấp nhất','Đóng cửa','Khối lượng','Ghi chú'],recent.map((r,i)=>{const prev=recent[i+1],gap=prev&&Math.abs((new Date(r.trading_date)-new Date(prev.trading_date))/86400000)>4;return [esc(r.trading_date),num(r.open_price,0),num(r.high_price,0),num(r.low_price,0),num(r.close_price,0),num(r.volume,0),gap?'Có khoảng trống lịch ngày':''];})):'<div class="empty-state">Chưa có giá lịch sử được lưu cho mã này.</div>';}
      
      window.latestBookwalkSnapshot=latest;
      const canWalk=['quote','outside_session'].includes(d.state)&&latest&&!(['bid','ask'].some(side=>latest[`${side}_price_1`]==null||latest[`${side}_volume_1`]==null));
      ['#bookwalk-run','#bookwalk-side','#bookwalk-quantity','#bookwalk-slippage'].forEach(sel=>{const el=document.querySelector(sel);if(el)el.disabled=!canWalk;});
      const result=document.querySelector('#bookwalk-result');if(result&&!canWalk)result.textContent=d.state==='stale'?'Quote đã cũ trong phiên; mô phỏng đang tắt để tránh dùng sổ lệnh không cập nhật.':'Chỉ mô phỏng khi đã lưu đủ hai phía sổ lệnh.';
      const bookwalkHeading=document.querySelector('#bookwalk-panel .panel-heading p');if(bookwalkHeading)bookwalkHeading.textContent=d.state==='outside_session'?`Ngoài phiên · mô phỏng trên sổ lệnh cuối phiên ${d.quote_time?window.fmtTime(d.quote_time):''}`:'Ước lượng trên sổ lệnh SSI gần nhất; không gửi lệnh';
      const run=document.querySelector('#bookwalk-run');if(run&&canWalk)run.onclick=()=>runBookwalk(ticker);
    }catch(e){if(analysis)analysis.textContent='Không thể tải nội dung trang lúc này. Vui lòng thử lại sau.';}
  }
  async function runBookwalk(ticker){const node=document.querySelector('#bookwalk-result');if(!node)return;const quantity=Number(document.querySelector('#bookwalk-quantity')?.value),side=document.querySelector('#bookwalk-side')?.value||'buy',slippage=document.querySelector('#bookwalk-slippage')?.value;if(!Number.isFinite(quantity)||quantity<100){node.textContent='Khối lượng phải từ 100 cổ phiếu trở lên.';return;}node.textContent='Đang tính trên ảnh chụp dữ liệu (snapshot) gần nhất…';try{const q=new URLSearchParams({side,quantity:String(quantity)});if(slippage!=='')q.set('max_slippage_bps',slippage);const r=await fetch(`/api/bookwalk/${encodeURIComponent(ticker)}?${q}`,{method:'POST'});if(!r.ok)throw Error('HTTP '+r.status);const d=await r.json();if(document.querySelector('#detail-symbol')?.value!==ticker)return;if(d.status==='unavailable'){node.textContent='Chưa đủ quote hợp lệ để mô phỏng sổ lệnh.';return;}const terms=(d.fills||[]).map(f=>`${num(f.level_price)} × ${num(f.quantity,0)}`).join(' + ')||'—',book=window.latestBookwalkSnapshot||{},prefix=side==='buy'?'ask':'bid',visibleQty=[1,2,3].reduce((sum,i)=>sum+Math.floor((Number(book[`${prefix}_volume_${i}`])||0)/100)*100,0),mid=(Number(d.mid_price)||0),vwap=(Number(d.vwap)||0),midCalc=`(${num(book.bid_price_1)} + ${num(book.ask_price_1)}) ÷ 2 = ${num(d.mid_price)}`,slipCalc=side==='buy'?`(${num(vwap)} − ${num(mid)}) ÷ ${num(mid)} × 10.000`:`(${num(mid)} − ${num(vwap)}) ÷ ${num(mid)} × 10.000`;node.innerHTML=`<div class="kpi-grid"><article class="kpi-card"><span>Lượng trong ba mức giá đầu đáp ứng</span><strong>${num(d.filled_quantity,0)}</strong><small>${(Number(d.fill_ratio||0)*100).toFixed(1)}% khối lượng yêu cầu</small></article><article class="kpi-card"><span>Giá bình quân theo khối lượng mô phỏng (VWAP)</span><strong>${num(d.vwap)}</strong><small>${d.levels_reached} mức giá được sử dụng</small></article><article class="kpi-card"><span>Trượt giá so với giá giữa (mid-price)</span><strong>${num(d.slippage_bps)} điểm cơ bản (bps)</strong><small>${esc(statusName(d.status==='full'?'full':'partial'))}</small></article><article class="kpi-card"><span>Thanh khoản ba mức giá đầu</span><strong>${num(visibleQty,0)}</strong><small>Cổ phiếu khả dụng trong ảnh chụp dữ liệu (snapshot)</small></article><article class="kpi-card"><span>Khối lượng tối đa theo ngưỡng</span><strong>${d.q_max_within_slippage==null?'—':num(d.q_max_within_slippage,0)}</strong><small>Với ngưỡng ${num(d.max_slippage_bps)} điểm cơ bản (bps)</small></article></div><div class="bookwalk-calculation"><h3>Thay số từ ảnh chụp dữ liệu</h3><p><b>Giá giữa (mid-price)</b> = ${midCalc}</p><p><b>Giá bình quân theo khối lượng (VWAP)</b> = (${terms}) ÷ ${num(d.filled_quantity,0)} = <b>${num(d.vwap)}</b></p><p><b>Trượt giá ${side==='buy'?'mua':'bán'}</b> = ${slipCalc} = <b>${num(d.slippage_bps)} điểm cơ bản (bps)</b></p><p><b>Tỷ lệ khớp mô phỏng</b> = ${num(d.filled_quantity,0)} ÷ ${num(quantity,0)} = <b>${(Number(d.fill_ratio||0)*100).toFixed(1)}%</b></p></div><p><b>Lưu ý:</b> ${num(d.fill_ratio*100,1)}% nghĩa là khối lượng yêu cầu được lượng đang niêm yết trong ba mức giá đầu đáp ứng trên ảnh chụp dữ liệu này; không phải xác suất lệnh thật sẽ khớp. Sổ lệnh có thể đổi hoặc bị hủy trước khi đặt lệnh.</p><p>${esc(d.disclaimer||'Ước lượng tĩnh; không gửi lệnh.')}</p>`;}catch(e){node.textContent='Không thể mô phỏng lúc này. Hãy thử lại khi có quote mới.';}}
  // This declaration intentionally replaces the legacy renderer above so the
  // server walk is tied to the exact quote currently shown in the ladder.
  async function runBookwalk(ticker){
    const node=document.querySelector('#bookwalk-result');if(!node)return;
    const quantity=Number(document.querySelector('#bookwalk-quantity')?.value),side=document.querySelector('#bookwalk-side')?.value||'buy',slippage=document.querySelector('#bookwalk-slippage')?.value,book=window.latestBookwalkSnapshot||{};
    if(!Number.isFinite(quantity)||quantity<100){node.textContent='Khối lượng phải từ 100 cổ phiếu trở lên.';return;}
    const snapshotAt=book.source_ts_vn||book.received_at_vn||book.received_at_utc;if(!snapshotAt){node.textContent='Chưa xác định được thời điểm của sổ lệnh đang hiển thị.';return;}
    node.textContent='Đang tính trên đúng ảnh chụp sổ lệnh đang hiển thị…';
    try{
      const q=new URLSearchParams({side,quantity:String(quantity),snapshot_at:snapshotAt});if(slippage!=='')q.set('max_slippage_bps',slippage);
      const response=await fetch(`/api/bookwalk/${encodeURIComponent(ticker)}?${q}`,{method:'POST'});if(!response.ok)throw Error('HTTP '+response.status);
      const data=await response.json();if(document.querySelector('#detail-symbol')?.value!==ticker)return;
      if(data.status==='unavailable'){node.textContent='Không thể dùng đúng sổ lệnh đang hiển thị để mô phỏng. Hãy tải lại mã.';return;}
      if(data.snapshot_at!==snapshotAt){node.textContent='Sổ lệnh đã đổi trong lúc tính. Hãy bấm mô phỏng lại để ghép đúng thời điểm.';return;}
      book.__fills=(data.fills||[]).map(fill=>({...fill,side}));renderOrderbookLadder(book,data.snapshot_at,data.market_state==='CLOSED'?'outside_session':'quote');
      const terms=(data.fills||[]).map(fill=>`${num(fill.level_price,0)} × ${num(fill.quantity,0)}`).join(' + ')||'—',midCalc=`(${num(book.bid_price_1,0)} + ${num(book.ask_price_1,0)}) ÷ 2 = ${num(data.mid_price,2)}`,slipCalc=side==='buy'?`(${num(data.vwap,2)} − ${num(data.mid_price,2)}) ÷ ${num(data.mid_price,2)} × 10.000`:`(${num(data.mid_price,2)} − ${num(data.vwap,2)}) ÷ ${num(data.mid_price,2)} × 10.000`;
      node.innerHTML=`<div class="bookwalk-result-grid"><article><small>Lượng mô phỏng đáp ứng</small><strong>${num(data.filled_quantity,0)} cổ phiếu</strong><span>${num(Number(data.fill_ratio||0)*100,1)}% · ${data.status==='full'?'đủ lượng hiển thị':'vượt độ sâu đang hiển thị'}</span></article><article><small>Giá bình quân theo khối lượng (VWAP)</small><strong>${num(data.vwap,2)} đồng</strong><span>${num(data.levels_reached,0)} mức giá được dùng</span></article><article><small>Trượt giá so với giá giữa</small><strong>${num(data.slippage_bps,2)} bps</strong><span>So với snapshot ${esc(data.snapshot_at)}</span></article></div><div class="bookwalk-calculation"><b>Giá giữa</b> ${midCalc}<br><b>VWAP</b> (${terms}) ÷ ${num(data.filled_quantity,0)} = ${num(data.vwap,2)}<br><b>Trượt giá ${side==='buy'?'mua':'bán'}</b> ${slipCalc} = ${num(data.slippage_bps,2)} bps</div><p>${esc(data.disclaimer||'Ước lượng tĩnh trên sổ lệnh hiển thị; không gửi lệnh.')}</p>`;
    }catch(_){node.textContent='Không thể mô phỏng lúc này. Hãy thử lại khi có sổ lệnh hợp lệ.';}
  }
  function bind(){
    document.querySelector('#overview-symbol')?.addEventListener('change',event=>{overviewDataStore.filters.symbol=event.target.value||'__all__';syncOverviewUrl();void refreshOverviewForFilter();});
    document.querySelector('#overview-limit')?.addEventListener('change',event=>{overviewDataStore.filters.chartWindow=Number(event.target.value||60);liquidityMapCache=null;syncOverviewUrl();void refreshOverviewForFilter();});
    document.querySelector('#ranking-limit')?.addEventListener('change',()=>baseRankings?.());
    document.querySelector('#ranking-board')?.addEventListener('change',()=>baseRankings?.());
    document.addEventListener('click',event=>{if(event.target?.id==='retry-market-turnover'){void loadMarketTurnover();return;}const button=event.target?.closest?.('[data-turnover-metric]');if(!button)return;const metric=button.dataset.turnoverMetric;if(!['value','volume'].includes(metric)||metric===turnoverMetric)return;turnoverMetric=metric;turnoverSignature='';loadMarketTurnover();});
    document.addEventListener('click',event=>{
      const overviewPreset=event.target?.closest?.('[data-overview-range]');
      if(overviewPreset){setOverviewRange(overviewPreset.dataset.overviewRange);void refreshOverviewForFilter();return;}
      if(event.target?.id==='overview-history-apply'){
        const from=document.querySelector('#overview-history-from')?.value,to=document.querySelector('#overview-history-to')?.value;
        const error=document.querySelector('#overview-history-error'),today=historyDateString(new Date());
        let message='';if(from&&to&&from>to)message='Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.';else if(to&&to>today)message='Ngày kết thúc không được sau ngày hôm nay.';
        if(message){if(error){error.textContent=message;error.hidden=false;}return;}if(error){error.textContent='';error.hidden=true;}
        const historyFrom=document.querySelector('#history-from-date'),historyTo=document.querySelector('#history-to-date');if(historyFrom)historyFrom.value=from||'';if(historyTo)historyTo.value=to||'';
        overviewDataStore.filters.preset='custom';overviewDataStore.filters.dateFrom=from||'';overviewDataStore.filters.dateTo=to||'';syncOverviewUrl();document.querySelectorAll('[data-overview-range]').forEach(button=>button.classList.remove('active'));void refreshOverviewForFilter();return;
      }
      const detailPreset=event.target?.closest?.('[data-detail-range]');
      if(detailPreset){setDetailRange(detailPreset.dataset.detailRange);void enhancedDetail();return;}
      if(event.target?.id==='detail-history-apply'){
        const from=document.querySelector('#detail-history-from')?.value,to=document.querySelector('#detail-history-to')?.value;
        if(from&&to&&from>to){const node=document.querySelector('#detail-spread-chart');if(node)node.innerHTML='<div class="empty-state">Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.</div>';return;}
        document.querySelectorAll('[data-detail-range]').forEach(button=>button.classList.remove('active'));void enhancedDetail();return;
      }
    });
    ['#overview-history-from','#overview-history-to'].forEach(selector=>document.querySelector(selector)?.addEventListener('change',()=>{overviewDataStore.filters.preset='custom';document.querySelectorAll('[data-overview-range]').forEach(button=>button.classList.remove('active'));}));
    document.addEventListener('click',event=>{if(event.target?.id==='retry-overview')void enhancedOverview();});
    document.addEventListener('click',event=>{if(event.target?.closest?.('[data-retry-overview]'))void refreshOverviewForFilter();});
    ['#detail-history-from','#detail-history-to'].forEach(selector=>document.querySelector(selector)?.addEventListener('change',()=>document.querySelectorAll('[data-detail-range]').forEach(button=>button.classList.remove('active'))));
    document.addEventListener('click',event=>{
      const preset=event.target?.closest?.('[data-history-range]');
      if(preset){setHistoryRange(preset.dataset.historyRange);const chosen=document.querySelector('#overview-symbol')?.value||'__all__';if(chosen!=='__all__')historicalPriceChart(chosen,10000);return;}
      if(event.target?.id==='history-apply-dates'){
        const from=document.querySelector('#history-from-date')?.value,to=document.querySelector('#history-to-date')?.value,note=document.querySelector('#historical-price-note'),chosen=document.querySelector('#overview-symbol')?.value||'__all__';
        if(from&&to&&from>to){if(note)note.textContent='Ngày bắt đầu phải trước hoặc bằng ngày kết thúc.';return;}
        historyRange='custom';document.querySelectorAll('[data-history-range]').forEach(button=>button.classList.remove('active'));if(chosen!=='__all__')historicalPriceChart(chosen,10000);
      }
    });
    ['#history-from-date','#history-to-date'].forEach(selector=>document.querySelector(selector)?.addEventListener('change',()=>{
      historyRange='custom';document.querySelectorAll('[data-history-range]').forEach(button=>button.classList.remove('active'));
    }));
    document.addEventListener('click',e=>{if(e.target?.closest?.('[data-retry-liquidity-map]')){liquidityMapCache=null;loadResearchPanels(null,Number(document.querySelector('#overview-limit')?.value||60));}});
  }
  function init(){
    setupMarketBackground();addBrandAndNavigation();
    try{bind();}catch(error){console.error('Không thể khởi tạo một số điều khiển giao diện.',error);}
    document.addEventListener('click',event=>{if(!event.target?.closest?.('.rank-symbol')&&!event.target?.closest?.('#overview-ranking-tooltip'))hideRankingPopover();});
    document.addEventListener('keydown',event=>{if(event.key==='Escape')hideRankingPopover();});
    window.renderOverviewSpreadRanking=renderOverviewSpreadRanking;
    window.loadOverview=enhancedOverview;window.loadDetail=enhancedDetail;window.loadRankings=enhancedRankings;
    window.loadAlerts=enhancedAlerts;window.loadMethodology=enhancedMethodology;
    let refreshing=false;
    window.enhancedRefresh=async()=>{if(refreshing)return;refreshing=true;try{if(state.route==='overview')await enhancedOverview();if(state.route==='detail')await enhancedDetail();if(state.route==='rankings')await enhancedRankings();if(state.route==='alerts')await enhancedAlerts();if(state.route==='watchlist')await window.loadWatchlist?.(true,true);}finally{refreshing=false;}};
    window.MarketClock?.subscribe(()=>window.enhancedRefresh());
    if(!document.querySelector('#application').classList.contains('hidden'))window.setRoute(state.route||'overview');
    // Bootstrap the map after layout paint in case the restored overview route fires
    // before its dynamic panel gets measurable dimensions.
    window.requestAnimationFrame(()=>window.setTimeout(()=>{
      if(document.querySelector('#liquidity-map')&&!liquidityMapCache){
        loadResearchPanels(null,Number(document.querySelector('#overview-limit')?.value||60));
      }
    },0));
  }
  const bootstrapLiquidityMap=()=>window.setTimeout(()=>{
    if(document.querySelector('#liquidity-map'))loadResearchPanels(null,Number(document.querySelector('#overview-limit')?.value||60));
  },0);
  if(document.readyState==='loading'){
    document.addEventListener('DOMContentLoaded',init,{once:true});
    document.addEventListener('DOMContentLoaded',bootstrapLiquidityMap,{once:true});
  }else{init();bootstrapLiquidityMap();}

  document.addEventListener('input',e=>{if(e.target?.id==='history-symbol-search'){clearTimeout(historyTimer);historyTimer=setTimeout(()=>{historySearch=e.target.value.trim();historyPage=1;const exact=state.symbols.find(symbol=>symbol.toUpperCase()===historySearch.toUpperCase());const selector=document.querySelector('#overview-symbol');if(exact&&selector){selector.value=exact;enhancedOverview();}else{if(selector)selector.value='__all__';historicalPriceChart('__all__',10000);}},250);}});
  document.addEventListener('click',e=>{const symbolButton=e.target?.closest?.('[data-history-symbol]');if(symbolButton){const symbol=symbolButton.dataset.historySymbol,selector=document.querySelector('#overview-symbol'),search=document.querySelector('#history-symbol-search');if(selector)selector.value=symbol;if(search)search.value=symbol;historySearch=symbol;enhancedOverview();return;}if(e.target?.id==='history-prev'){historyPage=Math.max(1,historyPage-1);historicalPriceChart('__all__',10000);}if(e.target?.id==='history-next'){historyPage++;historicalPriceChart('__all__',10000);}});
})();




