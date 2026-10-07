export function smaSettings(params) {
  const interval=params.get('sma_interval')||'daily',window=Number(params.get('sma_window')||200);
  if(!['daily','weekly'].includes(interval)||!Number.isInteger(window)||window<2||window>(interval==='daily'?500:260))throw new Error('Custom SMA needs 2–500 days or 2–260 weeks.');
  return {interval,window};
}
export function usesCustomSMA(params) {
  return [params.get('sort'),params.get('x'),params.get('y'),...(params.get('columns')||'').split(','),...JSON.parse(params.get('filters')||'[]').map(f=>f.field)].some(k=>k?.startsWith('sma_custom'));
}
export function customSMA(row,history,currency,fx,settings) {
  const series=history?.[settings.interval],values=series?.closes||[],dates=series?.dates||[];
  const n=settings.window,latest=history?.daily?.closes?.at(-1);
  const enough=values.length>=n&&values.slice(-n).every(v=>typeof v==='number'&&Number.isFinite(v)&&v>0);
  const mean=enough?values.slice(-n).reduce((s,v)=>s+v,0)/n:null;
  const rate=fx?.[currency==='GBp'||currency==='GBX'?'GBP':currency]?.rate;
  return {...row,sma_custom:mean&&Number.isFinite(rate)?mean*rate*(currency==='GBp'||currency==='GBX'?.01:1):null,
    sma_custom_distance:mean&&currency&&typeof latest==='number'?(latest/mean-1)*100:null,
    sma_custom_period:mean?`${n} ${settings.interval} closes · ${dates.at(-n)} to ${dates.at(-1)} · ${currency||'currency unknown'} · reference ${history.daily.dates.at(-1)}`:`Needs ${n} completed ${settings.interval} closes. ${values.length} cached.`,
    technical_version:history?1:row.technical_version};
}
export const metricPeriodKey=key=>key.replace(/_distance$/,'')+'_period';
