import { ZONES } from './quadrant-math.js';
import { metricPeriodKey } from './sma-math.js';
export function rankedCSV(points,options) {
  const keys=['rank','symbol','name','market','sector','industry','x_metric','x_value','x_preference','x_period','y_metric','y_value','y_preference','y_period','zone','pareto_frontier','balanced_score','order','reference_close_date','asset_class','focus','product_type','expense_ratio_percent','aum_usd','nav_return_3y_percent','nav_return_currency','catalogue_date','williams_asof','williams_provisional','williams_source_note','technical_calendar'];
  const cell=v=>{if(v==null)return '';let s=String(v);if(typeof v==='string'&&/^[=+\-@]/.test(s))s="'"+s;return /[",\r\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};
  return [keys.join(','),...points.map((p,i)=>[i+1,p.row.symbol,p.row.name,p.row.region,p.row.sector,p.row.industry,options.x,p.x,options.xPrefer,p.row[metricPeriodKey(options.x)],options.y,p.y,options.yPrefer,p.row[metricPeriodKey(options.y)],ZONES[p.zone].name,p.pareto,p.score,options.order,p.row.technical_asof,p.row.asset_class,p.row.focus,p.row.product_type,p.row.expense_ratio,p.row.aum,p.row.nav_return_3y,p.row.nav_return_currency,p.row.catalogue_date,p.row.williams_asof,p.row.williams_provisional,p.row.williams_source_note,p.row.technical_calendar].map(cell).join(','))].join('\r\n')+'\r\n';
}
