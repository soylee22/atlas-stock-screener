import { quadrantModel, ZONES } from './quadrant-math.js';
import { escapeHtml as esc, parseNumber } from './format.js';

export const quadrantDefaults = { x:'net_income', y:'div_years', xPrefer:'higher', yPrefer:'higher', xScale:'symlog', yScale:'linear', split:'median', xCut:0, yCut:10, fit:true, zone:'all' };
const lowerDefault = new Set(['pe','forward_pe','price_book','debt','delay']);
const number = n => n.toLocaleString('en-GB');
export function createQuadrant(root, { fields, settings, loadData, onChange, onDetail, format }) {
  const numeric = Object.values(fields).filter(f=>f.kind !== 'text');
  let options = { ...quadrantDefaults, ...settings }, data, model, seq = 0, hovered = null, screenPoints = [], geometry, drawFrame;
  const el = id => root.querySelector('#'+id);
  function validate() {
    if (!numeric.some(f=>f.key === options.x)) options.x = quadrantDefaults.x;
    if (!numeric.some(f=>f.key === options.y)) options.y = quadrantDefaults.y;
    for (const a of ['x','y']) {
      if (!['linear','symlog','rank'].includes(options[a+'Scale'])) options[a+'Scale'] = 'linear';
      if (!['higher','lower'].includes(options[a+'Prefer'])) options[a+'Prefer'] = 'higher';
    }
    if (!['median','quartile','custom'].includes(options.split)) options.split = 'median';
    if (!['all',...Object.keys(ZONES)].includes(options.zone)) options.zone = 'all';
  }
  validate();
  const choices = numeric.map(f=>`<option value="${esc(f.key)}">${esc(f.label)}${f.kind==='usd'||f.kind==='price'?' · USD':f.kind==='percent'?' · %':''}</option>`).join('');
  const axisControls = a => `<div class="quad-axis-controls"><label>${a.toUpperCase()} axis<select id="quad-${a}" aria-label="${a.toUpperCase()} axis metric">${choices}</select></label><label>Preference<select id="quad-${a}-prefer" aria-label="${a.toUpperCase()} axis preference"><option value="higher">Higher values</option><option value="lower">Lower values</option></select></label><label>Scale<select id="quad-${a}-scale" aria-label="${a.toUpperCase()} axis scale"><option value="linear">Linear</option><option value="symlog">Signed log</option><option value="rank">Percentile</option></select></label></div>`;
  root.innerHTML = `<header class="quad-heading"><div><span class="eyebrow">EXPLORE THE RELATIONSHIP</span><h2>Find your dream zone<span class="heading-dot">.</span></h2><p>Your current screen, plotted across two metrics.</p></div><span class="quad-scope">All matching listings · same screen filters</span></header><div class="quad-controls">${axisControls('x')}${axisControls('y')}<div class="quad-split-controls"><label>Zone cut-offs<select id="quad-split" aria-label="Quadrant cut-offs"><option value="median">Medians · 50th percentile</option><option value="quartile">Top quartile · preferred 25%</option><option value="custom">Custom targets</option></select></label><label class="quad-fit-switch"><input type="checkbox" id="quad-fit"> Best-fit line</label><button id="quad-swap" class="button secondary">⇄ Swap axes</button></div></div><div id="quad-custom" class="quad-custom" hidden><label>X target<input id="quad-x-cut" placeholder="e.g. 1B" aria-label="X custom cut-off"></label><label>Y target<input id="quad-y-cut" placeholder="e.g. 10" aria-label="Y custom cut-off"></label><button id="quad-apply" class="button secondary">Apply targets</button></div><div class="quad-summary" aria-live="polite"><span id="quad-coverage">Loading your screen…</span><span id="quad-fit-stats"></span></div><div class="quad-zones" id="quad-zones"></div><div class="quad-plot" id="quad-plot"><canvas id="quad-canvas" role="img" aria-label="Stock quadrant scatter plot. Stock values are also available in the list below."></canvas><svg id="quad-axes" aria-hidden="true"></svg><div id="quad-empty" class="quad-empty" hidden></div><div id="quad-tooltip" class="quad-tooltip" hidden></div></div><div class="quad-axis-caption"><span id="quad-x-caption"></span><span id="quad-y-caption"></span></div><p id="quad-method" class="quad-method"></p><div class="quad-list-heading"><h3 id="quad-list-title">Stocks in this view</h3><button id="quad-all" class="text-button">Show all zones</button></div><p class="quad-list-note">Sorted by average preferred percentile across both axes. Select a stock to open its deep dive.</p><div class="quad-stock-list" id="quad-stock-list"></div>`;
  function sync() {
    for (const a of ['x','y']) { el('quad-'+a).value = options[a]; el('quad-'+a+'-prefer').value = options[a+'Prefer']; el('quad-'+a+'-scale').value = options[a+'Scale']; el('quad-'+a+'-cut').value = options[a+'Cut'] ?? ''; }
    el('quad-split').value = options.split; el('quad-fit').checked = options.fit; el('quad-custom').hidden = options.split !== 'custom';
  }
  function changed() { onChange({ ...options }); }
  function update(next, reload=false) { Object.assign(options,next); validate(); sync(); changed(); if(reload) refresh(); else render(); }
  for (const a of ['x','y']) {
    el('quad-'+a).onchange = e => update({[a]:e.target.value,[a+'Scale']:['usd','price'].includes(fields[e.target.value].kind)?'symlog':'linear',[a+'Prefer']:lowerDefault.has(e.target.value)?'lower':'higher'},true);
    el('quad-'+a+'-scale').onchange = e => update({[a+'Scale']:e.target.value});
    el('quad-'+a+'-prefer').onchange = e => update({[a+'Prefer']:e.target.value});
  }
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
  el('quad-stock-list').onclick = e => { const symbol=e.target.closest('[data-detail]')?.dataset.detail; if(symbol) onDetail(symbol); };
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
    el('quad-coverage').textContent=`${number(n)} plotted / ${number(model.total)} matching listings · ${number(model.missing)} missing one or both metrics`;
    el('quad-fit-stats').textContent=options.fit ? model.fit ? `Best fit · r ${model.fit.r.toFixed(2)} · R² ${model.fit.r2.toFixed(3)} · n ${number(n)}`:'Best fit unavailable · needs 3 points and variation on both axes' : 'Best-fit line hidden';
    el('quad-zones').innerHTML=Object.entries(ZONES).map(([key,z])=>`<button class="quad-zone ${options.zone===key?'selected':''}" data-zone="${key}" aria-pressed="${options.zone===key}" style="--zone:${z.colour}"><span>${z.name}</span><strong>${number(model.counts[key])}<small>${n ? (model.counts[key]/n*100).toFixed(1):'0'}%</small></strong><em>${z.hint}</em></button>`).join('');
    const scaleName={linear:'linear',symlog:'signed log',rank:'percentile rank'};
    el('quad-x-caption').textContent=`X · ${fields[options.x].label} · ${scaleName[options.xScale]} · ${options.xPrefer} preferred`;
    el('quad-y-caption').textContent=`Y · ${fields[options.y].label} · ${scaleName[options.yScale]} · ${options.yPrefer} preferred`;
    const cuts=n ? `Cut-offs: X ${format(model.xcut,fields[options.x],true)}, Y ${format(model.ycut,fields[options.y],true)}. `:'';
    const text=document.createElement('span');text.innerHTML=cuts;
    el('quad-method').textContent=text.textContent+'Cut-offs and best fit use all plotted listings, including hidden zones. Ties at a cut-off go to the preferred side. Signed log retains zero and losses. Percentiles give equal weight to listings and share tied ranks. The line is ordinary least squares in the displayed scales. Zones are relative to this screen. Financial periods and dividend history coverage can differ.';
    el('quad-empty').hidden=!!n; el('quad-empty').textContent='No paired observations in this screen. Try different metrics or wider filters. Missing values are not treated as zero.';
    const visible=model.points.filter(p=>options.zone==='all'||p.zone===options.zone).sort((a,b)=>b.score-a.score||a.row.symbol.localeCompare(b.row.symbol));
    el('quad-list-title').textContent=`${options.zone==='all'?'All zones':ZONES[options.zone].name} · ${number(visible.length)} listings${visible.length>100?' · top 100 shown':''}`;
    el('quad-all').hidden=options.zone==='all';
    el('quad-stock-list').innerHTML=`<div class="quad-stock-header"><span>Company</span><span>${esc(fields[options.x].label)}</span><span>${esc(fields[options.y].label)}</span><span>Zone</span></div>`+visible.slice(0,100).map(p=>`<button class="quad-stock" data-detail="${esc(p.row.symbol)}"><span><b>${esc(p.row.symbol)}</b><small>${esc(p.row.name)} · ${esc(p.row.region || '')}</small></span><span>${format(p.x,fields[options.x])}</span><span>${format(p.y,fields[options.y])}</span><span class="quad-zone-tag" style="color:${ZONES[p.zone].colour}">${ZONES[p.zone].name}</span></button>`).join('')+(n&&!visible.length?'<p class="helper">No stocks fall in this zone.</p>':'');
    el('quad-canvas').setAttribute('aria-label',`${number(n)} stocks plotted. X: ${fields[options.x].label}. Y: ${fields[options.y].label}. ${number(model.counts.dream)} in the Dream zone. Use the stock list below for keyboard access.`);
    scheduleDraw();
  }
  function scheduleDraw() { cancelAnimationFrame(drawFrame); drawFrame=requestAnimationFrame(draw); }
  function tick(v,field,axis) { if(axis.mode==='rank') return `${Math.abs(v).toFixed(0)}%`; const raw=axis.inverse(v); return format(Math.abs(raw)<1e-9?0:raw,field,true); }
  function draw() {
    const plot=el('quad-plot'), canvas=el('quad-canvas'), width=plot.clientWidth,height=plot.clientHeight,dpr=window.devicePixelRatio || 1;
    if(!width||!height)return;
    canvas.width=Math.round(width*dpr);canvas.height=Math.round(height*dpr);canvas.style.width=width+'px';canvas.style.height=height+'px';
    const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);screenPoints=[];
    el('quad-axes').setAttribute('viewBox',`0 0 ${width} ${height}`);
    if(!model?.points.length){el('quad-axes').innerHTML='';return;}
    const left=width<600?74:94,right=22,top=36,bottom=58,w=width-left-right,h=height-top-bottom;
    const [xmin,xmax]=model.xdomain,[ymin,ymax]=model.ydomain;
    const px=v=>left+(v-xmin)/(xmax-xmin)*w,py=v=>top+h-(v-ymin)/(ymax-ymin)*h;
    const cx=px(model.xt),cy=py(model.yt);geometry={left,top,w,h,px,py};
    const boxes=[['dream',cx,top,left+w-cx,cy-top],['x',cx,cy,left+w-cx,top+h-cy],['y',left,top,cx-left,cy-top],['lagging',left,cy,cx-left,top+h-cy]];
    for(const [zone,x,y,bw,bh] of boxes){ctx.fillStyle=ZONES[zone].colour+'0d';ctx.fillRect(x,y,bw,bh);}
    let axes='';
    const steps=width<600?2:4;
    for(let i=0;i<=steps;i++) {
      const pct=100*i/steps, xv=options.xScale==='rank'?(options.xPrefer==='lower'?-100+pct:pct):xmin+(xmax-xmin)*i/steps,yv=options.yScale==='rank'?(options.yPrefer==='lower'?-100+pct:pct):ymin+(ymax-ymin)*i/steps,x=px(xv),y=py(yv);
      ctx.strokeStyle='#ffffff0b';ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,top+h);ctx.moveTo(left,y);ctx.lineTo(left+w,y);ctx.stroke();
      axes+=`<text x="${x}" y="${top+h+25}" text-anchor="middle">${tick(xv,fields[options.x],model.xaxis)}</text><text x="${left-12}" y="${y+4}" text-anchor="end">${tick(yv,fields[options.y],model.yaxis)}</text>`;
    }
    ctx.strokeStyle='#a9b6c080';ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(cx,top);ctx.lineTo(cx,top+h);ctx.moveTo(left,cy);ctx.lineTo(left+w,cy);ctx.stroke();ctx.setLineDash([]);
    for(const [zone,x,y,bw,bh] of boxes) { if(bw>120&&bh>50) axes+=`<text class="quad-zone-label" x="${x+12}" y="${y+23}" style="fill:${ZONES[zone].colour}">${ZONES[zone].name.toUpperCase()}</text>`; }
    ctx.save();ctx.beginPath();ctx.rect(left,top,w,h);ctx.clip();
    const shown=model.points.filter(p=>options.zone==='all'||p.zone===options.zone), radius=shown.length>5000?2:shown.length>1500?2.8:3.5;
    for(const p of shown) { const sx=px(p.tx),sy=py(p.ty);screenPoints.push({...p,sx,sy});ctx.fillStyle=ZONES[p.zone].colour+'a0';ctx.beginPath();ctx.arc(sx,sy,radius,0,Math.PI*2);ctx.fill(); }
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
    hovered=nearest; const tip=el('quad-tooltip');tip.hidden=!nearest;el('quad-canvas').style.cursor=nearest?'pointer':'crosshair';
    if(!nearest)return;
    const p=nearest,periods=[p.row.income_period,p.row.cf_period,p.row.fcf_growth_period].filter(Boolean);
    tip.innerHTML=`<strong>${esc(p.row.symbol)}</strong><span>${esc(p.row.name)}</span><dl><dt>${esc(fields[options.x].label)}</dt><dd>${format(p.x,fields[options.x])}</dd><dt>${esc(fields[options.y].label)}</dt><dd>${format(p.y,fields[options.y])}</dd></dl><b style="color:${ZONES[p.zone].colour}">${ZONES[p.zone].name}</b><small>${esc([...new Set(periods)].join(' · '))}</small><small>Click for stock deep dive</small>`;
    tip.style.left=Math.max(8,Math.min(x+16,rect.width-300))+'px';tip.style.top=Math.max(8,Math.min(y+16,rect.height-230))+'px';
  }
  el('quad-canvas').onpointermove=hover;
  el('quad-canvas').onpointerleave=()=>{hovered=null;el('quad-tooltip').hidden=true;};
  el('quad-canvas').onclick=e=>{hover(e);if(hovered)onDetail(hovered.row.symbol);};
  new ResizeObserver(scheduleDraw).observe(el('quad-plot'));
  sync();
  return { refresh, settings:()=>({...options}) };
}
