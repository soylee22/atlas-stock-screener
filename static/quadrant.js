import { quadrantModel, ZONES, validViewport, zoomViewport, panViewport, centreViewport, orderPoints } from './quadrant-math.js';
import { escapeHtml as esc, parseNumber } from './format.js';

export const quadrantDefaults = { x:'net_income', y:'div_years', xPrefer:'higher', yPrefer:'higher', xScale:'symlog', yScale:'linear', split:'median', xCut:0, yCut:10, fit:true, zone:'all', pareto:true, frontierOnly:false, order:'balanced', viewport:null };
const lowerDefault = new Set(['pe','forward_pe','price_book','debt','delay','below_52w_high']);
const number = n => n.toLocaleString('en-GB');
export function createQuadrant(root, { fields, settings, loadData, onChange, onDetail, format }) {
  const numeric = Object.values(fields).filter(f=>f.kind !== 'text');
  let options = { ...quadrantDefaults, ...settings }, data, model, seq = 0, hovered = null, screenPoints = [], geometry, drawFrame, dragging=null, suppressClick=false, clickTimer;
  const pointers=new Map();
  const el = id => root.querySelector('#'+id);
  function validate() {
    if (!numeric.some(f=>f.key === options.x)) options.x = quadrantDefaults.x;
    if (!numeric.some(f=>f.key === options.y)) options.y = quadrantDefaults.y;
    for (const a of ['x','y']) {
      if (!['linear','symlog','rank'].includes(options[a+'Scale'])) options[a+'Scale'] = 'linear';
      if (!['higher','lower'].includes(options[a+'Prefer'])) options[a+'Prefer'] = 'higher';
    }
    if (!['balanced','x','y','frontier'].includes(options.order)) options.order='balanced';
    if (!validViewport(options.viewport)) options.viewport=null;
    if (!['median','quartile','custom'].includes(options.split)) options.split = 'median';
    if (!['all',...Object.keys(ZONES)].includes(options.zone)) options.zone = 'all';
  }
  validate();
  const choices = numeric.map(f=>`<option value="${esc(f.key)}">${esc(f.label)}${f.kind==='usd'||f.kind==='price'?' · USD':f.kind==='percent'?' · %':''}</option>`).join('');
  const axisControls = a => `<div class="quad-axis-controls"><label>${a.toUpperCase()} axis<select id="quad-${a}" aria-label="${a.toUpperCase()} axis metric">${choices}</select></label><label>Preference<select id="quad-${a}-prefer" aria-label="${a.toUpperCase()} axis preference"><option value="higher">Higher values</option><option value="lower">Lower values</option></select></label><label>Scale<select id="quad-${a}-scale" aria-label="${a.toUpperCase()} axis scale"><option value="linear">Linear</option><option value="symlog">Signed log</option><option value="rank">Percentile</option></select></label></div>`;
  root.innerHTML = `<header class="quad-heading"><div><span class="eyebrow">EXPLORE THE RELATIONSHIP</span><h2>Find your dream zone<span class="heading-dot">.</span></h2><p>Your current screen, plotted across two metrics.</p></div><span class="quad-scope">All matching listings · same screen filters</span></header><div class="quad-controls">${axisControls('x')}${axisControls('y')}<div class="quad-split-controls"><label>Zone cut-offs<select id="quad-split" aria-label="Quadrant cut-offs"><option value="median">Medians · 50th percentile</option><option value="quartile">Top quartile · preferred 25%</option><option value="custom">Custom targets</option></select></label><label class="quad-fit-switch"><input type="checkbox" id="quad-fit"> Best-fit line</label><button id="quad-swap" class="button secondary">⇄ Swap axes</button></div></div><div id="quad-custom" class="quad-custom" hidden><label>X target<input id="quad-x-cut" placeholder="e.g. 1B" aria-label="X custom cut-off"></label><label>Y target<input id="quad-y-cut" placeholder="e.g. 10" aria-label="Y custom cut-off"></label><button id="quad-apply" class="button secondary">Apply targets</button></div><p id="quad-metric-note" class="quad-method"></p><div class="quad-summary" aria-live="polite"><span id="quad-coverage">Loading your screen…</span><span id="quad-fit-stats"></span></div><div class="quad-zones" id="quad-zones"></div><div id="quad-stage" class="quad-stage"><div class="quad-navigation"><div class="quad-nav-buttons"><button id="quad-zoom-in" class="button secondary" aria-label="Zoom in">＋</button><button id="quad-zoom-out" class="button secondary" aria-label="Zoom out">−</button><button id="quad-fit-all" class="button secondary">Fit all</button><button id="quad-centre" class="button secondary">Centre cut-offs</button></div><label><input id="quad-pareto" type="checkbox"> Pareto frontier <span id="quad-frontier-count"></span></label><label><input id="quad-frontier-only" type="checkbox"> Frontier only</label><span id="quad-view-status"></span><button id="quad-maximise" class="button secondary">⛶ Maximise chart</button></div><p class="quad-nav-note">Drag to move · Scroll or pinch to zoom · Double-click to fit all · Arrow keys to move, + / − to zoom</p><div class="quad-plot" id="quad-plot"><canvas id="quad-canvas" tabindex="0" role="img" aria-label="Stock quadrant scatter plot. Stock values are also available in the list below."></canvas><svg id="quad-axes" aria-hidden="true"></svg><div id="quad-empty" class="quad-empty" hidden></div><div id="quad-tooltip" class="quad-tooltip" hidden></div></div><div class="quad-axis-caption"><span id="quad-x-caption"></span><span id="quad-y-caption"></span></div></div><p id="quad-method" class="quad-method"></p><div class="quad-list-heading"><h3 id="quad-list-title">Stocks in this view</h3><label class="quad-order-label">Order<select id="quad-order" aria-label="Stock list order"><option value="balanced">Balanced score</option><option value="x">Preferred X</option><option value="y">Preferred Y</option><option value="frontier">Frontier first</option></select></label><button id="quad-all" class="text-button">Show all zones</button></div><p class="quad-list-note" id="quad-list-note"></p><div class="quad-stock-list" id="quad-stock-list"></div>`;
  function sync() {
    for (const a of ['x','y']) { el('quad-'+a).value = options[a]; el('quad-'+a+'-prefer').value = options[a+'Prefer']; el('quad-'+a+'-scale').value = options[a+'Scale']; el('quad-'+a+'-cut').value = options[a+'Cut'] ?? ''; }
    el('quad-metric-note').textContent = ([options.x, options.y].some(k => /^(revenue|net_income)_growth_/.test(k)) ? 'Growth uses completed fiscal years in reporting currency. 1Y is annual YoY. 3Y, 5Y and 10Y are CAGR. Yahoo usually supplies four annual records, so longer horizons remain unavailable until enough history is cached. ' : '') + ([options.x, options.y].includes('below_52w_high') ? 'Below 52W high: 0% is at the high. Lower values are closer. Negative values mean above the quoted high.' : '');
    el('quad-metric-note').hidden = !el('quad-metric-note').textContent;
    el('quad-pareto').checked=options.pareto;el('quad-frontier-only').checked=options.frontierOnly;el('quad-order').value=options.order;
    el('quad-split').value = options.split; el('quad-fit').checked = options.fit; el('quad-custom').hidden = options.split !== 'custom';
  }
  function changed() { onChange({ ...options }); }
  function update(next, reload=false) { if(['x','y','xScale','yScale','xPrefer','yPrefer','split','xCut','yCut'].some(k=>k in next && next[k]!==options[k]))options.viewport=null; Object.assign(options,next); validate(); sync(); changed(); if(reload) refresh(); else render(); }
  for (const a of ['x','y']) {
    el('quad-'+a).onchange = e => update({[a]:e.target.value,[a+'Scale']:['usd','price'].includes(fields[e.target.value].kind)?'symlog':'linear',[a+'Prefer']:lowerDefault.has(e.target.value)?'lower':'higher'},true);
    el('quad-'+a+'-scale').onchange = e => update({[a+'Scale']:e.target.value});
    el('quad-'+a+'-prefer').onchange = e => update({[a+'Prefer']:e.target.value});
  }
  el('quad-pareto').onchange=e=>update({pareto:e.target.checked});
  el('quad-frontier-only').onchange=e=>update({frontierOnly:e.target.checked});
  el('quad-order').onchange=e=>update({order:e.target.value});
  const stage=el('quad-stage');
  function syncMaximise(){const active=document.fullscreenElement===stage||stage.classList.contains('quad-expanded');el('quad-maximise').textContent=active?'⛶ Restore chart':'⛶ Maximise chart';el('quad-maximise').setAttribute('aria-pressed',String(active));scheduleDraw();}
  el('quad-maximise').onclick=async()=>{
    if(document.fullscreenElement===stage)await document.exitFullscreen();
    else if(stage.classList.contains('quad-expanded'))stage.classList.remove('quad-expanded');
    else {try{if(!stage.requestFullscreen)throw new Error('Fullscreen unavailable');await stage.requestFullscreen();}catch{stage.classList.add('quad-expanded');}}
    syncMaximise();
  };
  document.addEventListener('fullscreenchange',syncMaximise);
  document.addEventListener('keydown',async e=>{if(e.key==='Escape'){if(document.fullscreenElement===stage)await document.exitFullscreen();stage.classList.remove('quad-expanded');syncMaximise();}});
  async function openStock(symbol){if(document.fullscreenElement===stage)await document.exitFullscreen();stage.classList.remove('quad-expanded');syncMaximise();onDetail(symbol);}
  const fullView=()=>({x:model.xdomain,y:model.ydomain});
  const currentView=()=>options.viewport||fullView();
  function moveView(view) { if(!model?.points.length||!validViewport(view))return;options.viewport=view;hovered=null;el('quad-tooltip').hidden=true;changed();scheduleDraw(); }
  function zoom(factor,anchor) { if(model?.points.length)moveView(zoomViewport(currentView(),factor,anchor,fullView())); }
  function fitAll() { options.viewport=null;changed();scheduleDraw(); }
  el('quad-zoom-in').onclick=()=>zoom(.7);el('quad-zoom-out').onclick=()=>zoom(1/.7);
  el('quad-fit-all').onclick=fitAll;el('quad-centre').onclick=()=>{if(model?.points.length)moveView(centreViewport(currentView(),model.xt,model.yt));};
  el('quad-split').onchange = e => update({split:e.target.value});
  el('quad-fit').onchange = e => update({fit:e.target.checked});
  el('quad-apply').onclick = () => {
    const xCut=parseNumber(el('quad-x-cut').value), yCut=parseNumber(el('quad-y-cut').value);
    if (xCut===null || yCut===null) { el('quad-coverage').textContent='Enter two numeric targets, such as 1B and 10.'; return; }
    update({xCut,yCut});
  };
  el('quad-swap').onclick = () => update({x:options.y,y:options.x,xPrefer:options.yPrefer,yPrefer:options.xPrefer,xScale:options.yScale,yScale:options.xScale,xCut:options.yCut,yCut:options.xCut},true);
  el('quad-zones').onclick = e => { const z=e.target.closest('[data-zone]')?.dataset.zone; if(z) update({zone:options.zone===z?'all':z}); };
  el('quad-all').onclick = () => update({zone:'all'});
  el('quad-stock-list').onclick = e => { const symbol=e.target.closest('[data-detail]')?.dataset.detail; if(symbol) openStock(symbol); };
  async function refresh(next) {
    if(next) { options={...quadrantDefaults,...next};validate();sync(); }
    const request=++seq; el('quad-coverage').textContent='Loading all matching listings…'; root.classList.add('quad-loading');
    try { const result=await loadData(options); if(request!==seq)return; data=result; render(); }
    catch(error) { if(request===seq) { data=null;model=null;screenPoints=[];el('quad-coverage').textContent=error.message;el('quad-empty').hidden=false;el('quad-empty').textContent='Chart data unavailable. Use Refresh data to retry.';el('quad-stock-list').innerHTML='';el('quad-zones').innerHTML='';el('quad-fit-stats').textContent='';scheduleDraw(); } }
    finally { if(request===seq)root.classList.remove('quad-loading'); }
  }
  function render() {
    if(!data) return;
    try { model=quadrantModel(data.rows,options,fields); model.total=data.total; model.missing=data.total-model.points.length; }
    catch(error) { el('quad-coverage').textContent=error.message; return; }
    hovered=null;el('quad-tooltip').hidden=true;
    const n=model.points.length;
    el('quad-frontier-count').textContent=`· ${number(model.frontier?.length||0)} stocks`;
    el('quad-coverage').textContent=`${number(n)} plotted / ${number(model.total)} matching listings · ${number(model.missing)} missing one or both metrics`;
    el('quad-fit-stats').textContent=options.fit ? model.fit ? `Best fit · r ${model.fit.r.toFixed(2)} · R² ${model.fit.r2.toFixed(3)} · n ${number(n)}`:'Best fit unavailable · needs 3 points and variation on both axes' : 'Best-fit line hidden';
    el('quad-zones').innerHTML=Object.entries(ZONES).map(([key,z])=>`<button class="quad-zone ${options.zone===key?'selected':''}" data-zone="${key}" aria-pressed="${options.zone===key}" style="--zone:${z.colour}"><span>${z.name}</span><strong>${number(model.counts[key])}<small>${n ? (model.counts[key]/n*100).toFixed(1):'0'}%</small></strong><em>${z.hint}</em></button>`).join('');
    const scaleName={linear:'linear',symlog:'signed log',rank:'percentile rank'};
    el('quad-x-caption').textContent=`X · ${fields[options.x].label} · ${scaleName[options.xScale]} · ${options.xPrefer} preferred`;
    el('quad-y-caption').textContent=`Y · ${fields[options.y].label} · ${scaleName[options.yScale]} · ${options.yPrefer} preferred`;
    const cuts=n ? `Cut-offs: X ${format(model.xcut,fields[options.x],true)}, Y ${format(model.ycut,fields[options.y],true)}. `:'';
    const text=document.createElement('span');text.innerHTML=cuts;
    el('quad-method').textContent=text.textContent+'Cut-offs, best fit and frontier use all paired listings, including hidden zones and points outside the viewport. Ties at a cut-off go to the preferred side. Signed log retains zero and losses. Percentiles give equal weight to listings and share tied ranks. The line is ordinary least squares in the displayed scales. Zones are relative to this screen. Financial periods and dividend history coverage can differ. Frontier stocks have no other matching stock at least as good on both raw metrics and strictly better on one. The purple line joins observed frontier points, not attainable intermediate combinations.';
    el('quad-empty').hidden=!!n; el('quad-empty').textContent='No paired observations in this screen. Try different metrics or wider filters. Missing values are not treated as zero.';
    const visible=orderPoints(model.points.filter(p=>(options.zone==='all'||p.zone===options.zone)&&(!options.frontierOnly||p.pareto)),options.order);
    const orderName={balanced:'Balanced score, highest first',x:'Preferred X, then balanced score',y:'Preferred Y, then balanced score',frontier:'Frontier members first, then balanced score'};
    el('quad-list-note').textContent=orderName[options.order]+'. Balanced score = 50% preferred X percentile + 50% preferred Y percentile (0 to 100). Symbol breaks exact ties. The list covers the selected zones, including stocks outside the viewport. Select a stock for its deep dive.';
    el('quad-list-title').textContent=`${options.frontierOnly?'Frontier · ':''}${options.zone==='all'?'All zones':ZONES[options.zone].name} · ${number(visible.length)} listings${visible.length>100?' · top 100 shown':''}`;
    el('quad-all').hidden=options.zone==='all';
    el('quad-stock-list').innerHTML=`<div class="quad-stock-header"><span>Company</span><span>${esc(fields[options.x].label)}</span><span>${esc(fields[options.y].label)}</span><span>Zone / frontier</span><span>Balanced score</span></div>`+visible.slice(0,100).map(p=>`<button class="quad-stock" data-detail="${esc(p.row.symbol)}"><span><b>${esc(p.row.symbol)}</b><small>${esc(p.row.name)} · ${esc(p.row.region || '')}</small></span><span>${format(p.x,fields[options.x])}</span><span>${format(p.y,fields[options.y])}</span><span class="quad-zone-tag" style="color:${ZONES[p.zone].colour}">${ZONES[p.zone].name}${p.pareto?'<small class="quad-frontier-tag">◆ Frontier</small>':''}</span><span>${p.score.toFixed(1)}</span></button>`).join('')+(n&&!visible.length?'<p class="helper">No stocks fall in this zone.</p>':'');
    el('quad-canvas').setAttribute('aria-label',`${number(n)} stocks plotted. X: ${fields[options.x].label}. Y: ${fields[options.y].label}. ${number(model.counts.dream)} in the Dream zone. Use the stock list below for keyboard access.`);
    scheduleDraw();
  }
  function scheduleDraw() { cancelAnimationFrame(drawFrame); drawFrame=requestAnimationFrame(draw); }
  function tick(v,field,axis) { if(axis.mode==='rank') return `${(axis.prefer==='lower'?-v:v).toFixed(0)}%`; const raw=axis.inverse(v); return format(Math.abs(raw)<1e-9?0:raw,field,true); }
  function draw() {
    const plot=el('quad-plot'), canvas=el('quad-canvas'), width=plot.clientWidth,height=plot.clientHeight,dpr=window.devicePixelRatio || 1;
    if(!width||!height)return;
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.style.width=width+'px';canvas.style.height=height+'px';
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);screenPoints=[];
    el('quad-axes').setAttribute('viewBox',`0 0 ${width} ${height}`);
    if(!model?.points.length){el('quad-axes').innerHTML='';return;}
    const left=width<600?74:94,right=22,top=36,bottom=58,w=width-left-right,h=height-top-bottom;
    const view=currentView(),[xmin,xmax]=view.x,[ymin,ymax]=view.y;
    el('quad-view-status').textContent=options.viewport?`Zoom ${( (model.xdomain[1]-model.xdomain[0])/(xmax-xmin)).toFixed(1)}× · moved view`:'All data fitted';
    const px=v=>left+(v-xmin)/(xmax-xmin)*w,py=v=>top+h-(v-ymin)/(ymax-ymin)*h;
    const cx=Math.max(left,Math.min(left+w,px(model.xt))),cy=Math.max(top,Math.min(top+h,py(model.yt)));geometry={left,top,w,h,px,py};
    const boxes=[['dream',cx,top,left+w-cx,cy-top],['x',cx,cy,left+w-cx,top+h-cy],['y',left,top,cx-left,cy-top],['lagging',left,cy,cx-left,top+h-cy]];
    for(const [zone,x,y,bw,bh] of boxes){ctx.fillStyle=ZONES[zone].colour+'0d';ctx.fillRect(x,y,bw,bh);}
    let axes='';
    const steps=width<600?2:4;
    for(let i=0;i<=steps;i++) {
      const xv=xmin+(xmax-xmin)*i/steps,yv=ymin+(ymax-ymin)*i/steps,x=px(xv),y=py(yv);
      ctx.strokeStyle='#ffffff0b';ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,top+h);ctx.moveTo(left,y);ctx.lineTo(left+w,y);ctx.stroke();
      axes+=`<text x="${x}" y="${top+h+25}" text-anchor="middle">${tick(xv,fields[options.x],model.xaxis)}</text><text x="${left-12}" y="${y+4}" text-anchor="end">${tick(yv,fields[options.y],model.yaxis)}</text>`;
    }
    ctx.strokeStyle='#a9b6c080';ctx.setLineDash([5,5]);ctx.beginPath();if(model.xt>=xmin&&model.xt<=xmax){ctx.moveTo(cx,top);ctx.lineTo(cx,top+h);}if(model.yt>=ymin&&model.yt<=ymax){ctx.moveTo(left,cy);ctx.lineTo(left+w,cy);}ctx.stroke();ctx.setLineDash([]);
    for(const [zone,x,y,bw,bh] of boxes) { if(bw>120&&bh>50) axes+=`<text class="quad-zone-label" x="${x+12}" y="${y+23}" style="fill:${ZONES[zone].colour}">${ZONES[zone].name.toUpperCase()}</text>`; }
    ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();
    const shown=model.points.filter(p=>(options.zone==='all'||p.zone===options.zone)&&(!options.frontierOnly||p.pareto)), radius=shown.length>5000?2:shown.length>1500?2.8:3.5;
    for(const p of shown) { const sx=px(p.tx),sy=py(p.ty);if(sx>=left&&sx<=left+w&&sy>=top&&sy<=top+h)screenPoints.push({...p,sx,sy});ctx.fillStyle=ZONES[p.zone].colour+'a0';ctx.beginPath();ctx.arc(sx,sy,radius,0,Math.PI*2);ctx.fill(); }
    if(options.pareto) {
      const front=[...(model.frontier||[])].sort((a,b)=>a.tx-b.tx||b.ty-a.ty);
      ctx.strokeStyle='#cfa3ff';ctx.lineWidth=2.2;ctx.beginPath();front.forEach((p,i)=>{if(i)ctx.lineTo(px(p.tx),py(p.ty));else ctx.moveTo(px(p.tx),py(p.ty));});ctx.stroke();
      for(const p of front)if(options.zone==='all'||p.zone===options.zone){ctx.beginPath();ctx.arc(px(p.tx),py(p.ty),5,0,Math.PI*2);ctx.stroke();}
    }
    if(options.fit&&model.fit) { const f=model.fit;ctx.strokeStyle='#e4edf4';ctx.lineWidth=1.8;ctx.setLineDash([8,5]);ctx.beginPath();ctx.moveTo(px(xmin),py(f.intercept+f.slope*xmin));ctx.lineTo(px(xmax),py(f.intercept+f.slope*xmax));ctx.stroke();ctx.setLineDash([]); }
    ctx.restore();
    axes+=`<text class="quad-axis-name" x="${left+w/2}" y="${height-8}" text-anchor="middle">${esc(fields[options.x].label)}${['usd','price'].includes(fields[options.x].kind)?' (USD)':''} → ${options.xPrefer} preferred</text><text class="quad-axis-name" x="${left}" y="18">${esc(fields[options.y].label)} · ${options.yPrefer} preferred ↑</text>`;
    el('quad-axes').innerHTML=axes;
  }
  function hover(event) {
    if(root.classList.contains('quad-loading'))return;
    const rect=el('quad-canvas').getBoundingClientRect(),x=event.clientX-rect.left,y=event.clientY-rect.top;
    let nearest=null,distance=100;
    for(const p of screenPoints) {const d=(p.sx-x)**2+(p.sy-y)**2;if(d<distance){distance=d;nearest=p;}}
    hovered=nearest; const tip=el('quad-tooltip');tip.hidden=!nearest;el('quad-canvas').style.cursor=nearest?'pointer':'grab';
    if(!nearest)return;
    const p=nearest,periods=[p.row.income_period,p.row.cf_period,p.row.fcf_growth_period,p.row[options.x+'_period'],p.row[options.y+'_period']].filter(Boolean);
    tip.innerHTML=`<strong>${esc(p.row.symbol)}</strong><span>${esc(p.row.name)}</span><dl><dt>${esc(fields[options.x].label)}</dt><dd>${format(p.x,fields[options.x])}</dd><dt>${esc(fields[options.y].label)}</dt><dd>${format(p.y,fields[options.y])}</dd></dl><b style="color:${ZONES[p.zone].colour}">${ZONES[p.zone].name}${p.pareto?' · Pareto frontier':''}</b><small>${esc([...new Set(periods)].join(' · '))}</small><small>Click for stock deep dive</small>`;
    tip.style.left=Math.max(8,Math.min(x+16,rect.width-300))+'px';tip.style.top=Math.max(8,Math.min(y+16,rect.height-230))+'px';
  }
  const canvas=el('quad-canvas');
  const local=e=>{const r=canvas.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top};};
  const inside=p=>geometry&&p.x>=geometry.left&&p.x<=geometry.left+geometry.w&&p.y>=geometry.top&&p.y<=geometry.top+geometry.h;
  const gesture=()=>{const ps=[...pointers.values()];return ps.length>1?{x:(ps[0].x+ps[1].x)/2,y:(ps[0].y+ps[1].y)/2,d:Math.hypot(ps[0].x-ps[1].x,ps[0].y-ps[1].y)}:ps[0];};
  canvas.onpointerdown=e=>{
    const p=local(e);if(!inside(p)||!model?.points.length||e.button>0)return;
    if(!pointers.size)suppressClick=false;pointers.set(e.pointerId,p);canvas.setPointerCapture(e.pointerId);
    dragging={start:p,previous:gesture()};canvas.style.cursor='grabbing';
  };
  canvas.onpointermove=e=>{
    if(!pointers.has(e.pointerId)){hover(e);return;}
    pointers.set(e.pointerId,local(e));const next=gesture(),prev=dragging.previous;
    if(pointers.size>1||suppressClick||Math.hypot(next.x-dragging.start.x,next.y-dragging.start.y)>4){
      suppressClick=true;moveView(panViewport(currentView(),(next.x-prev.x)/geometry.w,(next.y-prev.y)/geometry.h));
      if(next.d&&prev.d)zoom(prev.d/next.d,{x:(next.x-geometry.left)/geometry.w,y:1-(next.y-geometry.top)/geometry.h});
      canvas.style.cursor='grabbing';
    }
    dragging.previous=next;
  };
  function endPointer(e){pointers.delete(e.pointerId);if(!pointers.size){dragging=null;canvas.style.cursor='grab';}else dragging={start:gesture(),previous:gesture()};}
  canvas.onpointerup=endPointer;canvas.onpointercancel=endPointer;
  canvas.onpointerleave=()=>{if(!pointers.size){hovered=null;el('quad-tooltip').hidden=true;}};
  canvas.onclick=e=>{clearTimeout(clickTimer);if(suppressClick)return;hover(e);if(hovered){const symbol=hovered.row.symbol;clickTimer=setTimeout(()=>openStock(symbol),220);}};
  canvas.ondblclick=e=>{e.preventDefault();clearTimeout(clickTimer);fitAll();};
  canvas.addEventListener('wheel',e=>{const p=local(e);if(!inside(p)||!model?.points.length)return;e.preventDefault();const delta=e.deltaY*(e.deltaMode===1?16:e.deltaMode===2?geometry.h:1);zoom(Math.exp(Math.max(-1,Math.min(1,delta*.002))),{x:(p.x-geometry.left)/geometry.w,y:1-(p.y-geometry.top)/geometry.h});},{passive:false});
  canvas.onkeydown=e=>{if(!model?.points.length)return;const moves={ArrowLeft:[.1,0],ArrowRight:[-.1,0],ArrowUp:[0,.1],ArrowDown:[0,-.1]};if(moves[e.key]){e.preventDefault();moveView(panViewport(currentView(),...moves[e.key]));}else if(['+','=','-','Home'].includes(e.key)){e.preventDefault();if(e.key==='Home')fitAll();else zoom(e.key==='-'?1/.7:.7);}};
  new ResizeObserver(scheduleDraw).observe(el('quad-plot'));
  sync();
  return { refresh, settings:()=>({...options}) };
}
