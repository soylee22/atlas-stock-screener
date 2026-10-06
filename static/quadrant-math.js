// Quantiles and fit use the complete paired population, never a table page or selected zone.
export const ZONES = {
  dream: { name: 'Dream zone', colour: '#bcf180', hint: 'Preferred on both axes' },
  x: { name: 'X strength', colour: '#71c9e6', hint: 'Preferred X, below target Y' },
  y: { name: 'Y strength', colour: '#efc77b', hint: 'Preferred Y, below target X' },
  lagging: { name: 'Below both', colour: '#ef8d9c', hint: 'Below both chosen targets' },
};
export function quantile(sorted, p) {
  if (!sorted.length) return null;
  const position = (sorted.length - 1) * Math.max(0, Math.min(1, p));
  const lo = Math.floor(position), fraction = position - lo;
  return sorted[lo] * (1 - fraction) + sorted[Math.ceil(position)] * fraction;
}
function ranks(sorted) {
  const values = [], positions = [];
  for (let i = 0; i < sorted.length;) {
    let j = i + 1; while (j < sorted.length && sorted[j] === sorted[i]) j++;
    values.push(sorted[i]); positions.push(sorted.length === 1 ? 50 : (i + j - 1) / 2 / (sorted.length - 1) * 100); i = j;
  }
  function interpolate(v, from, to) {
    if (v <= from[0]) return to[0]; if (v >= from.at(-1)) return to.at(-1);
    let lo = 0, hi = from.length - 1;
    while (hi - lo > 1) { const mid = (hi + lo) >> 1; if (from[mid] <= v) lo = mid; else hi = mid; }
    const f = (v - from[lo]) / (from[hi] - from[lo]); return to[lo] * (1 - f) + to[hi] * f;
  }
  return { forward: v => interpolate(v, values, positions), inverse: p => interpolate(p, positions, values) };
}
export function axisScale(sorted, mode = 'linear', prefer = 'higher', kind = 'number') {
  const rank = ranks(sorted), sign = prefer === 'lower' ? -1 : 1, unit = kind === 'usd' ? 1e6 : 1;
  const forward = mode === 'rank' ? rank.forward : mode === 'symlog' ? v => Math.sign(v) * Math.log10(1 + Math.abs(v) / unit) : v => v;
  const inverse = mode === 'rank' ? rank.inverse : mode === 'symlog' ? v => Math.sign(v) * Math.expm1(Math.abs(v) * Math.LN10) * unit : v => v;
  return { mode, prefer, forward: v => sign * forward(v), inverse: v => inverse(sign * v), percentile: v => prefer === 'lower' ? 100 - rank.forward(v) : rank.forward(v) };
}
export function regression(points) {
  if (points.length < 3) return null;
  let mx = 0, my = 0;
  points.forEach((p, i) => { mx += (p.tx - mx) / (i + 1); my += (p.ty - my) / (i + 1); });
  let xx = 0, yy = 0, xy = 0;
  for (const p of points) { const dx = p.tx - mx, dy = p.ty - my; xx += dx * dx; yy += dy * dy; xy += dx * dy; }
  if (!(xx > 0) || !(yy > 0)) return null;
  const slope = xy / xx, intercept = my - slope * mx, r = Math.max(-1, Math.min(1, xy / Math.sqrt(xx) / Math.sqrt(yy)));
  if (![slope, intercept, r].every(Number.isFinite)) return null;
  return { slope, intercept, r, r2: r * r, n: points.length };
}
export function quadrantModel(rows, settings, fields) {
  const paired = rows.filter(row => typeof row[settings.x] === 'number' && Number.isFinite(row[settings.x]) && typeof row[settings.y] === 'number' && Number.isFinite(row[settings.y]));
  const counts = Object.fromEntries(Object.keys(ZONES).map(k => [k, 0]));
  if (!paired.length) return { points: [], counts, total: rows.length, missing: rows.length, fit: null };
  const xs = paired.map(r => r[settings.x]).sort((a,b) => a-b), ys = paired.map(r => r[settings.y]).sort((a,b) => a-b);
  const cut = (values, preference, custom) => settings.split === 'custom' ? custom : quantile(values, settings.split === 'quartile' ? preference === 'lower' ? .25 : .75 : .5);
  const xcut = cut(xs, settings.xPrefer, settings.xCut), ycut = cut(ys, settings.yPrefer, settings.yCut);
  if (![xcut,ycut].every(v => typeof v === 'number' && Number.isFinite(v))) throw new Error('Enter two numeric cut-offs, such as 1B and 10.');
  const xaxis = axisScale(xs, settings.xScale, settings.xPrefer, fields[settings.x].kind), yaxis = axisScale(ys, settings.yScale, settings.yPrefer, fields[settings.y].kind);
  const preferred = (v, cut, preference) => preference === 'lower' ? v <= cut : v >= cut;
  const points = paired.map(row => {
    const x = row[settings.x], y = row[settings.y], px = preferred(x, xcut, settings.xPrefer), py = preferred(y, ycut, settings.yPrefer);
    const zone = px ? py ? 'dream' : 'x' : py ? 'y' : 'lagging'; counts[zone]++;
    const xRank=xaxis.percentile(x), yRank=yaxis.percentile(y);
    return { row, x, y, tx: xaxis.forward(x), ty: yaxis.forward(y), zone, xRank, yRank, score: (xRank+yRank)/2 };
  });
  const frontier=paretoFrontier(points,settings.xPrefer,settings.yPrefer);
  const members=new Set(frontier); for(const p of points)p.pareto=members.has(p);
  const domain = (values, cut, mode) => {
    let lo = Math.min(...values, cut), hi = Math.max(...values, cut);
    const pad = (hi - lo || Math.max(Math.abs(lo) * .1, 1)) * .07;
    if (mode === 'rank' && lo >= 0) return [-2,102];
    if (mode === 'rank' && hi <= 0) return [-102,2];
    return [lo - pad, hi + pad];
  };
  return { points, frontier, counts, total: rows.length, missing: rows.length - points.length, xcut, ycut, xaxis, yaxis,
    xt: xaxis.forward(xcut), yt: yaxis.forward(ycut), xdomain: domain(points.map(p=>p.tx), xaxis.forward(xcut), settings.xScale),
    ydomain: domain(points.map(p=>p.ty), yaxis.forward(ycut), settings.yScale), fit: regression(points) };
}

// Strict Pareto dominance in raw values. Equal pairs never dominate each other.
export function paretoFrontier(points, xPrefer='higher', yPrefer='higher') {
  const sx=xPrefer==='lower'?-1:1, sy=yPrefer==='lower'?-1:1;
  const ordered=points.map(p=>({p,x:sx*p.x,y:sy*p.y})).sort((a,b)=>b.x-a.x||b.y-a.y);
  const frontier=[]; let bestY=-Infinity;
  for(let i=0;i<ordered.length;) {
    let end=i+1; while(end<ordered.length&&ordered[end].x===ordered[i].x)end++;
    const topY=ordered[i].y;
    if(topY>bestY) for(let j=i;j<end&&ordered[j].y===topY;j++)frontier.push(ordered[j].p);
    bestY=Math.max(bestY,topY); i=end;
  }
  return frontier;
}
export function validViewport(view) {
  return !!view && ['x','y'].every(a=>Array.isArray(view[a])&&view[a].length===2&&view[a].every(Number.isFinite)&&view[a][1]>view[a][0]);
}
export function zoomViewport(view, factor, anchor={x:.5,y:.5}, bounds=view) {
  const result={};
  for(const a of ['x','y']) {
    const [lo,hi]=view[a], base=bounds[a][1]-bounds[a][0], at=Math.max(0,Math.min(1,anchor[a]));
    const span=Math.max(base/10000,Math.min(base*100,(hi-lo)*factor)), fixed=lo+(hi-lo)*at;
    result[a]=[fixed-span*at,fixed+span*(1-at)];
  }
  return result;
}
export function panViewport(view, dx, dy) {
  return {x:view.x.map(v=>v-dx*(view.x[1]-view.x[0])),y:view.y.map(v=>v+dy*(view.y[1]-view.y[0]))};
}
export function centreViewport(view, x, y) {
  const hx=(view.x[1]-view.x[0])/2,hy=(view.y[1]-view.y[0])/2;
  return {x:[x-hx,x+hx],y:[y-hy,y+hy]};
}
export function orderPoints(points, order='balanced') {
  return [...points].sort((a,b)=>(order==='x'?b.xRank-a.xRank:order==='y'?b.yRank-a.yRank:order==='frontier'?Number(b.pareto)-Number(a.pareto):0)||b.score-a.score||a.row.symbol.localeCompare(b.row.symbol));
}
