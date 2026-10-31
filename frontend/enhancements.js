(() => {
  const profiles = {
    FPT: { name: 'Công ty Cổ phần FPT', sector: 'Công nghệ thông tin' },
    HPG: { name: 'Công ty Cổ phần Tập đoàn Hòa Phát', sector: 'Thép và công nghiệp' },
    VPB: { name: 'Ngân hàng TMCP Việt Nam Thịnh Vượng', sector: 'Ngân hàng' }
  };
  const colors = ['#008A45', '#E2231A', '#68A92E', '#A7BF36', '#00592E'];
  const routeOrder = ['overview','detail','rankings','alerts','methodology','guide','intro','project'];
  const prevRanks = { spread: new Map(), depth: new Map() };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (v, n=2) => v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('vi-VN',{maximumFractionDigits:n});
  const call = async (url) => { const r=await fetch(url); if(!r.ok) throw Error('HTTP '+r.status); return r.json(); };

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
    if(header && !document.querySelector('.project-masthead')) header.insertAdjacentHTML('afterbegin','<div class="project-masthead"><span class="masthead-kicker">MARKET MICROSTRUCTURE · RESEARCH DASHBOARD</span><h1>Stock Liquidity Intelligence</h1><p>Đo lường và diễn giải thanh khoản niêm yết theo thời gian</p></div>');
    const nav=document.querySelector('.main-nav');
    if(nav && !document.querySelector('.swipe-hint')) nav.insertAdjacentHTML('afterend','<div class="swipe-hint">Vuốt ngang hoặc dùng phím Alt + ← / → để chuyển tab</div>');
    const overview=document.querySelector('#view-overview .content-grid');
    if(overview && !document.querySelector('#imbalance-chart')) overview.insertAdjacentHTML('beforeend',
      '<article class="panel"><div class="panel-heading"><div><h2>Cân bằng khối lượng bid / ask</h2><p>Queue imbalance từ −1 đến +1</p></div></div><div id="imbalance-chart" class="chart-box chart-short"></div><p id="imbalance-reading" class="analysis-note"></p></article>'+
      '<article class="panel"><div class="panel-heading"><div><h2>So sánh spread giữa các mã</h2><p>Median quoted spread trong cửa sổ đang chọn</p></div></div><div id="spread-compare" class="chart-box chart-short"></div><p class="analysis-note">Spread thấp hơn thể hiện khoảng cách báo giá hẹp hơn tại snapshot; không tính phí và khả năng khớp.</p></article>'+
      '<article class="panel panel-wide"><div class="panel-heading"><div><h2>Độ bao phủ dữ liệu</h2><p>Số snapshot hợp lệ theo từng mã trong khoảng chọn</p></div></div><div id="coverage-chart" class="chart-box chart-short"></div><p id="overview-analysis" class="analysis-note"></p></article>');
    const detail=document.querySelector('#view-detail .kpi-grid');
    if(detail && !document.querySelector('#company-profile')) detail.insertAdjacentHTML('beforebegin','<article id="company-profile" class="company-profile"><div><span class="eyebrow">DOANH NGHIỆP ĐANG CHỌN</span><h2>—</h2><p></p></div><span class="company-sector"></span></article><article id="detail-analysis" class="analysis-panel"></article>');
    const rankings=document.querySelector('#view-rankings .content-grid');
    if(rankings && !document.querySelector('#ranking-spread-change')) rankings.insertAdjacentHTML('beforeend','<article class="panel"><div class="panel-heading"><div><h2>Spread nới rộng nhất</h2><p>Snapshot hiện tại, cập nhật trực tiếp</p></div></div><div id="ranking-spread-change"></div></article><article class="panel"><div class="panel-heading"><div><h2>Depth suy giảm mạnh nhất</h2><p>Snapshot hiện tại so với median lịch sử</p></div></div><div id="ranking-depth-change"></div></article><p id="ranking-live-note" class="analysis-note panel-wide"></p>');
    const alerts=document.querySelector('#view-alerts .page-heading');
    if(alerts && !document.querySelector('#alert-explainer')) alerts.insertAdjacentHTML('afterend','<div id="alert-explainer" class="alert-explainer"><h2>Diễn giải giám sát</h2><p>Cảnh báo được cập nhật cùng snapshot mới. Mỗi cảnh báo cần đọc kèm mã, chỉ tiêu, ngưỡng tham chiếu, số mẫu baseline và thời điểm. Đây là tín hiệu bất thường thống kê, không phải khuyến nghị giao dịch.</p><div id="alert-live-counts"></div></div>');
  }

  function lineChart(node, series, key, label) {
    if(!node)return;
    const entries=series.map(s=>({name:s.name,values:(s.rows||[]).map(r=>r[key]).filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number),color:s.color})).filter(s=>s.values.length>1);
    if(!entries.length){node.innerHTML='<div class="empty-state">Chưa đủ snapshot hợp lệ để vẽ biểu đồ.</div>';return;}
    const W=900,H=230,L=52,R=15,T=15,B=26, vals=entries.flatMap(e=>e.values), lo=Math.min(...vals),hi=Math.max(...vals),span=hi-lo||Math.max(Math.abs(hi)*.08,1);
    let svg=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}">`;
    for(let i=0;i<4;i++){let y=T+i*(H-T-B)/3;svg+=`<line x1="${L}" y1="${y}" x2="${W-R}" y2="${y}" stroke="#e8eeec"/>`;}
    entries.forEach(e=>{const p=e.values.map((v,i)=>`${i?'L':'M'}${L+i/(e.values.length-1)*(W-L-R)},${T+(hi-v)/span*(H-T-B)}`).join(' ');svg+=`<path d="${p}" fill="none" stroke="${e.color}" stroke-width="2.7" stroke-linecap="round"/>`;});
    svg+='</svg><div class="chart-legend">'+entries.map(e=>`<span><i style="background:${e.color}"></i>${esc(e.name)}</span>`).join('')+'</div>';node.innerHTML=svg;
  }
  function bars(node, items, labelKey, valueKey, color='#008A45', format=(v)=>num(v)) {
    if(!node)return;
    if(!items.length){node.innerHTML='<div class="empty-state">Chưa có dữ liệu.</div>';return;}
    const max=Math.max(...items.map(x=>Math.abs(Number(x[valueKey])||0)),1);
    node.innerHTML='<div class="bar-chart">'+items.map(item=>`<div class="bar-row"><span>${esc(item[labelKey])}</span><div class="bar-track"><i style="width:${Math.max(3,Math.abs(Number(item[valueKey])||0)/max*100)}%;background:${color}"></i></div><b>${format(item[valueKey])}</b></div>`).join('')+'</div>';
  }
  async function enhancedSymbols() {
    const d=await call('/api/symbols');state.symbols=d.symbols||[];
    const overview=document.querySelector('#overview-symbol');
    if(overview){const old=overview.value;overview.innerHTML='<option value="__all__">Tất cả mã</option>'+state.symbols.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');overview.value='__all__';}
    const detail=document.querySelector('#detail-symbol');
    if(detail){const old=detail.value;detail.innerHTML=state.symbols.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');if(state.symbols.includes(old))detail.value=old;}
  }
  async function enhancedOverview() {
    try {
      await enhancedSymbols();const chosen=document.querySelector('#overview-symbol').value||'__all__',limit=Number(document.querySelector('#overview-limit').value||120);
      const d=await call(`/api/overview?limit=${limit}${chosen!=='__all__'?'&symbol='+encodeURIComponent(chosen):''}`),groups=d.series_by_symbol||{};
      const active=chosen==='__all__'?Object.keys(groups):[chosen],rows=active.flatMap(s=>groups[s]||[]),valid=rows.filter(r=>r.validation_status==='valid');
      const med=(arr)=>{arr=arr.filter(v=>v!=null&&Number.isFinite(Number(v))).map(Number).sort((a,b)=>a-b);return arr.length?(arr[(arr.length-1)>>1]+arr[arr.length>>1])/2:null;};
      document.querySelector('#kpi-symbols').textContent=num(active.length,0);document.querySelector('#kpi-spread').textContent=num(med(valid.map(r=>r.spread_bps)));document.querySelector('#kpi-depth').textContent=num(med(valid.map(r=>r.total_depth_value)),0);document.querySelector('#kpi-imbalance').textContent=num(med(valid.map(r=>r.queue_imbalance)),3);document.querySelector('#kpi-valid').textContent=rows.length?(valid.length/rows.length*100).toFixed(1)+'%':'—';document.querySelector('#kpi-valid-count').textContent=`${num(valid.length,0)} / ${num(rows.length,0)} snapshot`;
      document.querySelector('#overview-symbol-label').textContent=chosen==='__all__'?'Tất cả mã':chosen;
      const series=active.map((s,i)=>({name:s,rows:groups[s]||[],color:colors[i%colors.length]}));lineChart(document.querySelector('#spread-chart'),series,'spread_bps','Spread theo thời gian');
      lineChart(document.querySelector('#imbalance-chart'),series,'queue_imbalance','Queue imbalance theo thời gian');
      lineChart(document.querySelector('#depth-chart'),series,'total_depth_value','Tổng top-3 depth theo thời gian');
      const ranking=await call('/api/rankings?limit='+limit), items=ranking.lowest_spread||[];
      bars(document.querySelector('#spread-compare'),items,'symbol','median_spread_bps','#008A45',v=>num(v)+' bps');
      bars(document.querySelector('#coverage-chart'),active.map(s=>({symbol:s,count:(groups[s]||[]).filter(r=>r.validation_status==='valid').length})).map(x=>({symbol:x.symbol,count:x.count})), 'symbol','count','#68A92E',v=>num(v,0));
      const q=med(valid.map(r=>r.queue_imbalance));document.querySelector('#imbalance-reading').textContent=q==null?'Chưa đủ dữ liệu hợp lệ để diễn giải.':Math.abs(q)<.1?'Khối lượng niêm yết hai phía tương đối cân bằng trong snapshot/cửa sổ này.':q>0?`Bid đang trội hơn ask (median ${num(q,3)}); đây chỉ là lượng chờ hiển thị, không phải tín hiệu dự báo.`:`Ask đang trội hơn bid (median ${num(q,3)}); đây chỉ là lượng chờ hiển thị, không phải tín hiệu dự báo.`;
      document.querySelector('#overview-analysis').textContent=`Tổng hợp ${active.length} mã và ${valid.length} snapshot hợp lệ. Median spread ${num(med(valid.map(r=>r.spread_bps)))} bps phản ánh khoảng cách báo giá tốt nhất; median top-3 depth ${num(med(valid.map(r=>r.total_depth_value)),0)} là giá trị lượng niêm yết đang quan sát. Hãy đọc cùng độ bao phủ và trạng thái nguồn ${String(d.source).toUpperCase()}; dữ liệu DEMO là tổng hợp.`;
      const preview=items.slice(0,5).map((r,i)=>[`<b>${i+1}</b>`,`<b>${esc(r.symbol)}</b>`,num(r.median_spread_bps)+' bps',num(r.snapshot_count,0)]);document.querySelector('#overview-ranking').innerHTML=window.table(['#','Mã','Median spread','Snapshots'],preview);
    }catch(e){document.querySelector('#spread-chart').innerHTML='<div class="empty-state">'+esc(e.message)+'</div>';}
  }
  async function enhancedDetail(){
    try{await enhancedSymbols();const ticker=document.querySelector('#detail-symbol').value;if(!ticker)return;
      const [d,c]=await Promise.all([call('/api/symbol/'+encodeURIComponent(ticker)+'?limit=300'),call('/api/company/'+encodeURIComponent(ticker))]);const latest=d.latest,profile=document.querySelector('#company-profile');
      profile.querySelector('h2').textContent=c.name;profile.querySelector('p').textContent=c.description||'';profile.querySelector('.company-sector').textContent=c.sector||'';
      const rows=(d.series||[]).filter(r=>r.validation_status==='valid'),spread=latest?.spread_bps,med=rows.map(r=>r.spread_bps).filter(v=>v!=null).sort((a,b)=>a-b),baseline=med.length?med[(med.length-1)>>1]:null,imb=latest?.queue_imbalance;
      document.querySelector('#detail-analysis').innerHTML=`<span class="eyebrow">DIỄN GIẢI SNAPSHOT</span><h2>${latest?'Đọc điều kiện thanh khoản của '+esc(ticker):'Chưa có snapshot'}</h2><p>${latest?`Spread ${num(spread)} bps, so với median ${num(baseline)} bps trong ${rows.length} snapshot hợp lệ. ${spread!=null&&baseline!=null?(spread>baseline?'Spread hiện cao hơn baseline của chính mã, cho thấy khoảng cách báo giá đang rộng hơn thông thường trong mẫu.':'Spread hiện không vượt median baseline trong mẫu.'):'Chưa đủ trường để đối chiếu spread.'}`:'Backend chưa nhận dữ liệu cho mã này.'}</p><p>${latest&&latest.total_depth_value!=null?`Top-3 depth hai phía tổng cộng ${num(latest.total_depth_value,0)} theo đơn vị giá × lượng.`:'Depth chưa đầy đủ.'} ${imb==null?'Queue imbalance chưa khả dụng.':`Queue imbalance ${num(imb,3)}: ${imb>0?'lượng bid hiển thị lớn hơn ask':imb<0?'lượng ask hiển thị lớn hơn bid':'hai phía cân bằng'}.`}</p><small>Diễn giải mô tả dữ liệu niêm yết tại snapshot; không dự báo giá hay khuyến nghị đầu tư.</small>`;
      document.querySelector('#detail-spread').textContent=latest?num(latest.spread_bps):'—';document.querySelector('#detail-bid-depth').textContent=latest?num(latest.bid_depth_value,0):'—';document.querySelector('#detail-ask-depth').textContent=latest?num(latest.ask_depth_value,0):'—';document.querySelector('#detail-imbalance').textContent=latest?num(latest.queue_imbalance,3):'—';document.querySelector('#detail-valid').textContent=d.valid_snapshot_rate==null?'—':(d.valid_snapshot_rate*100).toFixed(1)+'%';document.querySelector('#detail-snapshots').textContent=num(d.snapshot_count,0)+' snapshot';
      window.renderLineChart('#detail-spread-chart',d.series,'spread_bps','#008A45','Spread '+ticker);window.renderDepthChart('#detail-depth-chart',d.series);document.querySelector('#book-time').textContent=latest?window.fmtTime(latest.received_at_utc):'Chưa có dữ liệu';document.querySelector('#book-validity').textContent=latest?.validation_status||'—';
      document.querySelector('#orderbook').innerHTML=latest?window.table(['','Giá bid','KL bid','Giá ask','KL ask',''],[1,2,3].map(i=>[`<span class="side-bid">Bid ${i}</span>`,num(latest['bid_price_'+i]),num(latest['bid_volume_'+i],0),num(latest['ask_price_'+i]),num(latest['ask_volume_'+i],0),`<span class="side-ask">Ask ${i}</span>`])):'<div class="empty-state">Chưa có snapshot cho mã này.</div>';
    }catch(e){document.querySelector('#detail-analysis').textContent='Không tải được dữ liệu: '+e.message;}
  }
  function rankTable(rows,key,type){const current=new Map(rows.map((r,i)=>[r.symbol,i+1])),previous=prevRanks[type], output=rows.map((r,i)=>{const old=previous.get(r.symbol),movement=old==null?'MỚI':old===i+1?'—':old>i+1?`▲ ${old-i-1}`:`▼ ${i+1-old}`;return [`<b class="rank-number">${i+1}</b>`,`<b>${esc(r.symbol)}</b>`,`<span class="rank-move ${movement.startsWith('▲')?'up':movement.startsWith('▼')?'down':''}">${movement}</span>`,num(r[key],key.includes('depth')?0:2),num(r.snapshot_count,0)];});prevRanks[type]=current;return window.table(['Hạng','Mã','Biến động','Median','Mẫu'],output);}
  async function enhancedRankings(){try{const n=Number(document.querySelector('#ranking-limit').value||120),d=await call('/api/rankings?limit='+n);document.querySelector('#ranking-spread').innerHTML=rankTable(d.lowest_spread||[],'median_spread_bps','spread');document.querySelector('#ranking-depth').innerHTML=rankTable(d.highest_depth||[],'median_total_depth_value','depth');
    const rows=d.symbols||[],widen=[...rows].sort((a,b)=>(b.latest_spread_bps||0)-(a.median_spread_bps||0)-(a.latest_spread_bps||0)+(a.median_spread_bps||0)).slice(0,5),drop=[...rows].sort((a,b)=>(a.latest_total_depth_value||0)/(a.median_total_depth_value||1)-(b.latest_total_depth_value||0)/(b.median_total_depth_value||1)).slice(0,5);
    document.querySelector('#ranking-spread-change').innerHTML=window.table(['Mã','Spread hiện tại','Median'],widen.map(r=>[`<b>${esc(r.symbol)}</b>`,num(r.latest_spread_bps)+' bps',num(r.median_spread_bps)+' bps']));document.querySelector('#ranking-depth-change').innerHTML=window.table(['Mã','Depth hiện tại','Median'],drop.map(r=>[`<b>${esc(r.symbol)}</b>`,num(r.latest_total_depth_value,0),num(r.median_total_depth_value,0)]));document.querySelector('#ranking-live-note').textContent=`Cập nhật theo snapshot ${String(d.source).toUpperCase()} mỗi 2 giây. Mũi tên so sánh vị trí với lần tải trước; dữ liệu DEMO thay đổi tổng hợp để thử nghiệm. Ranking mỗi bảng dùng một metric riêng.`;
    }catch(e){document.querySelector('#ranking-live-note').textContent=e.message;}}
  async function enhancedAlerts(){try{const d=await call('/api/alerts?limit=100'),a=d.alerts||[];document.querySelector('#alert-summary').innerHTML=`<span class="notice-icon">!</span><span>${a.length?`${a.length} cảnh báo gần nhất · nguồn ${String(d.source).toUpperCase()} · cập nhật định kỳ 2 giây.`:`Chưa có cảnh báo. Baseline percentile cần tối thiểu ${d.baseline_min_observations} quan sát hợp lệ.`}</span>`;document.querySelector('#alerts-table').innerHTML=window.table(['Mã','Loại','Mức','Metric','Ngưỡng','Số mẫu','Thời điểm','Giải thích'],a.map(r=>[`<b>${esc(r.symbol)}</b>`,esc(r.alert_type),esc(r.severity),num(r.metric_value),num(r.threshold_value),num(r.baseline_window,0),window.fmtTime(r.triggered_at),esc(r.explanation)]),'Chưa có cảnh báo cho nguồn này.');const counts={};a.forEach(x=>counts[x.alert_type]=(counts[x.alert_type]||0)+1);document.querySelector('#alert-live-counts').innerHTML=Object.entries(counts).map(([k,v])=>`<span class="alert-count"><b>${v}</b>${esc(k)}</span>`).join('')||'<span class="muted">Chưa ghi nhận loại cảnh báo nào.</span>';}catch(e){document.querySelector('#alerts-table').textContent=e.message;}}
  async function enhancedMethodology(){try{const d=await call('/api/methodology');const metrics=d.metrics.map(m=>{const x=(d.interpretations||[]).find(y=>y.metric.toLowerCase().includes(m.name.toLowerCase().split(' ')[0]));return `<article class="method-card"><h3>${esc(m.name)}</h3><div class="formula">${esc(m.formula)}</div><p>${esc(x?.meaning||'Chỉ tính khi các trường đầu vào cần thiết hợp lệ.')}</p>${x?`<p><b>Giới hạn:</b> ${esc(x.caveat)}</p>`:''}</article>`;}).join('');const refs=(d.references||[]).map(r=>`<article class="reference-card"><h3>${esc(r.citation)}</h3><p>${esc(r.relevance)}</p><a href="https://doi.org/${esc(r.doi)}" target="_blank" rel="noreferrer">DOI: ${esc(r.doi)} ↗</a></article>`).join('');const theory=(d.theoretical_framework||[]).map(x=>`<article class="panel theory-card"><h3>${esc(x.title)}</h3><p>${esc(x.body)}</p></article>`).join('');document.querySelector('#method-content').innerHTML=`<h2 class="section-title">Cách đo và ý nghĩa</h2><div class="method-grid">${metrics}</div><h2 class="section-title">Cơ sở lý thuyết và diễn giải</h2><div class="theory-grid">${theory}</div><h2 class="section-title">Tài liệu tham khảo học thuật</h2><p class="analysis-note">Các công trình dưới đây làm nền tảng khái niệm. SLI chỉ triển khai các chỉ tiêu có thể tái lập từ snapshot NEO; không tuyên bố đo được mọi khái niệm trong các bài nghiên cứu.</p><div class="reference-grid">${refs}</div><h2 class="section-title">Nguồn, kiểm soát và giới hạn dữ liệu</h2><div class="content-grid"><article class="panel"><h3>Nguồn</h3><p>Luồng thị trường NEO API, channel stockInfo. Payload thật cần được kiểm tra để xác nhận key, đơn vị giá/lượng, timestamp và quy tắc cập nhật. DEMO chỉ dùng dữ liệu tổng hợp.</p></article><article class="panel"><h3>Quy tắc diễn giải</h3><ul>${d.limitations.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></article></div>`;if(state.status)window.renderCollectorStatus(state.status);}catch(e){document.querySelector('#method-content').textContent=e.message;}}
  function expandGuide(){const view=document.querySelector('#view-guide');if(!view||view.dataset.expanded)return;view.dataset.expanded='1';const steps=view.querySelector('.guide-steps');if(steps)steps.innerHTML=`<article><span>01</span><h2>Chọn phạm vi</h2><p>Mở Tổng quan để xem mặc định toàn bộ mã; chọn một mã khi cần so sánh theo cửa sổ riêng. Chọn 60, 120 hoặc 240 snapshot để điều chỉnh bối cảnh.</p></article><article><span>02</span><h2>Đọc nguồn và độ mới</h2><p>DEMO là dữ liệu tổng hợp. LIVE mới là luồng NEO đã kết nối. Kiểm tra trạng thái, thời điểm nhận cuối và cảnh báo stale trước khi diễn giải.</p></article><article><span>03</span><h2>Đọc spread</h2><p>Mid = (ask1 + bid1)/2; spread bps = (ask1 − bid1)/mid × 10.000. Spread tăng so baseline nghĩa là khoảng cách báo giá rộng hơn trong mẫu của chính mã.</p></article><article><span>04</span><h2>Đọc depth và imbalance</h2><p>Top-3 depth cộng giá × lượng ở ba mức hợp lệ. Queue imbalance = (bid volume − ask volume)/(tổng volume), thuộc [−1,1]. Dương nghĩa lượng bid hiển thị trội hơn.</p></article>`;view.insertAdjacentHTML('beforeend',`<div class="content-grid guide-extra"><article class="panel"><h2>Diễn giải có luận cứ</h2><p>Không đọc một chỉ tiêu cô lập. Spread mô tả chi phí báo giá tức thời; depth mô tả lượng hiển thị gần giá; imbalance mô tả độ lệch lượng hai phía. Khi spread rộng đồng thời depth thấp, điều kiện niêm yết có thể kém thuận lợi hơn trong snapshot đó. Luôn xem số mẫu, thời điểm, nhiều phiên và trạng thái LIVE/DEMO.</p></article><article class="panel"><h2>Đọc ranking và cảnh báo</h2><p>Ranking spread xếp median spread thấp lên trước; ranking depth xếp median depth cao lên trước. Mũi tên realtime cho biết hạng đổi so lần tải trước. Cảnh báo percentile cần baseline tối thiểu; đọc metric hiện tại cùng ngưỡng và cửa sổ mẫu.</p></article><article class="panel"><h2>Khi dữ liệu thiếu</h2><p>Dấu gạch ngang là không khả dụng, không phải số 0. Snapshot thiếu bid/ask hoặc lượng cần thiết có thể không hợp lệ. Nếu timestamp server vắng mặt, hệ thống chỉ có giờ nhận cục bộ; không suy diễn thời gian nguồn.</p></article><article class="panel"><h2>Giới hạn sử dụng</h2><p>Lệnh có thể bị sửa/hủy trước khi khớp; top 3 không phải toàn bộ sổ lệnh. Các chỉ tiêu mô tả dữ liệu, không dự báo giá, không đưa khuyến nghị đầu tư và không thực hiện giao dịch.</p></article></div>`);}
  function setupSwipe(){let start=null;const shell=document.querySelector('.page-shell');if(!shell)return;shell.addEventListener('touchstart',e=>{const t=e.changedTouches[0];start={x:t.clientX,y:t.clientY};},{passive:true});shell.addEventListener('touchend',e=>{if(!start)return;const t=e.changedTouches[0],dx=t.clientX-start.x,dy=t.clientY-start.y;start=null;if(Math.abs(dx)>75&&Math.abs(dx)>Math.abs(dy)*1.35)moveRoute(dx<0?1:-1);},{passive:true});shell.addEventListener('wheel',e=>{if(Math.abs(e.deltaX)>60&&Math.abs(e.deltaX)>Math.abs(e.deltaY)*1.4)moveRoute(e.deltaX>0?1:-1);},{passive:true});document.addEventListener('keydown',e=>{if(e.altKey&&['ArrowRight','ArrowLeft'].includes(e.key)){e.preventDefault();moveRoute(e.key==='ArrowRight'?1:-1);}});}
  function moveRoute(delta){if(document.querySelector('#application').classList.contains('hidden'))return;let i=routeOrder.indexOf(state.route);i=(i+delta+routeOrder.length)%routeOrder.length;window.setRoute(routeOrder[i]);}
  function bind(){document.querySelector('#overview-symbol')?.addEventListener('change',enhancedOverview);document.querySelector('#overview-limit')?.addEventListener('change',enhancedOverview);document.querySelector('#detail-symbol')?.addEventListener('change',enhancedDetail);document.querySelector('#ranking-limit')?.addEventListener('change',enhancedRankings);}
  function init(){setupMarketBackground();addBrandAndNavigation();expandGuide();bind();setupSwipe();window.loadOverview=enhancedOverview;window.loadDetail=enhancedDetail;window.loadRankings=enhancedRankings;window.loadAlerts=enhancedAlerts;window.loadMethodology=enhancedMethodology;window.enhancedRefresh=()=>{if(state.route==='overview')enhancedOverview();if(state.route==='detail')enhancedDetail();if(state.route==='rankings')enhancedRankings();if(state.route==='alerts')enhancedAlerts();};if(!document.querySelector('#application').classList.contains('hidden'))window.setRoute(state.route||'overview');window.setInterval(window.enhancedRefresh,2000);}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
