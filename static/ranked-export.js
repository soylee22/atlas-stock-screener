import { ZONES } from './quadrant-math.js';
import { metricPeriodKey } from './sma-math.js';
export function rankedCSV(points,options) {
  const keys=['rank','symbol','name','market','sector','industry','x_metric','x_value','x_preference','x_period','y_metric','y_value','y_preference','y_period','zone','pareto_frontier','balanced_score','order','reference_close_date'];
  const cell=v=>{if(v==null)return '';let s=String(v);if(typeof v==='string'&&/^[=+\-@]/.test(s))s="'"+s;return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  return [keys.join(','),...points.map((p,i)=>[i+1,p.row.symbol,p.row.name,p.row.region,p.row.sector,p.row.industry,options.x,p.x,options.xPrefer,p.row[metricPeriodKey(options.x)],options.y,p.y,options.yPrefer,p.row[metricPeriodKey(options.y)],ZONES[p.zone].name,p.pareto,p.score,options.order,p.row.technical_asof].map(cell).join(','))].join('\r\n')+'\r\n';
}
