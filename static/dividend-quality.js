import { escapeHtml as esc } from './format.js';

export const qualityDefaults = {horizon:5,divGrowth:7,fcfGrowth:8,payout:60,roic:10,current:1,
  sustainability:true,yieldGate:false,yieldMin:2,yieldMax:3,fcfYieldGate:false,fcfYield:8,marginGate:false,margin:10};
export function qualityPreset(options={}) {
  const s={...qualityDefaults,...options};
  const numbers=['divGrowth','fcfGrowth','payout','roic','current','yieldMin','yieldMax','fcfYield','margin'];
  if(![1,3,5,10].includes(Number(s.horizon))||numbers.some(k=>!Number.isFinite(Number(s[k])))||s.payout<=0||s.current<0||s.yieldMax<s.yieldMin)throw new Error('Check the horizon, numeric cut-offs and yield range.');
  const rules=[{field:'sector',op:'ne',value:'Financial Services'},
    {field:`div_fiscal_growth_${s.horizon}y`,op:'gte',value:Number(s.divGrowth)},
    {field:`fcf_growth_${s.horizon}y`,op:'gte',value:Number(s.fcfGrowth)},
    {field:'fcf_payout',op:'lt',value:Number(s.payout)},
    {field:'roic_proxy',op:'gte',value:Number(s.roic)},
    {field:'current_ratio',op:'gt',value:Number(s.current)}];
  if(s.sustainability)rules.push({field:`fcf_div_growth_gap_${s.horizon}y`,op:'gte',value:0});
  if(s.yieldGate)rules.push({field:'div_yield',op:'between',value:Number(s.yieldMin),value2:Number(s.yieldMax)});
  if(s.fcfYieldGate)rules.push({field:'fcf_yield',op:'gte',value:Number(s.fcfYield)});
  if(s.marginGate)rules.push({field:'margin_of_safety',op:'gte',value:Number(s.margin)});
  return rules;
}

export const valuationKey='atlas.fair-values.v1';
export function readValuations() {try{return JSON.parse(localStorage.getItem(valuationKey))||{};}catch{return {};}}
export function withValuation(row,values=readValuations()) {
  const estimate=values[row.symbol];
  const valid=estimate&&typeof estimate.value==='number'&&Number.isFinite(estimate.value)&&estimate.value>0&&/^\d{4}-\d{2}-\d{2}$/.test(estimate.date||'');
  const price=typeof row.price==='number'&&Number.isFinite(row.price)&&row.price>=0?row.price:null;
  return {...row,fair_value:valid?estimate.value:null,fair_value_period:valid?estimate.date:null,
    fair_value_source:valid?estimate.source:null,
    margin_of_safety:valid&&price!==null?(estimate.value-price)/estimate.value*100:null,
    margin_of_safety_period:valid?`Entered valuation ${estimate.date} · USD · quote ${row.quote_time||'date unavailable'}`:null};
}
export function usesValuation(params) {
  const keys=[params.get('x'),params.get('y'),params.get('sort'),...(params.get('columns')||'').split(','),...JSON.parse(params.get('filters')||'[]').map(f=>f.field)];
  return keys.some(k=>k?.startsWith('fair_value')||k?.startsWith('margin_of_safety'));
}
export function qualitySourceKeys(key) {
  if(key?.startsWith('fair_value')||key==='margin_of_safety')return ['fair_value','fair_value_period','fair_value_source','margin_of_safety_period'];
  if(/^(fcf_growth_|div_fiscal_growth_|fcf_div_growth_gap_|fcf_payout$|current_ratio$)/.test(key||''))return [key+'_period','dividend_quality_fetched',...(key==='fcf_payout'?['fcf_payout_basis']:[])];
  return [];
}
export function qualityDetailHTML(row,fields,format) {
  const keys=['fcf_payout','current_ratio','fcf_growth_1y','fcf_growth_3y','fcf_growth_5y','fcf_growth_10y',
    'div_fiscal_growth_3y','div_fiscal_growth_5y','fcf_div_growth_gap_3y','fcf_div_growth_gap_5y','fcf_yield','fair_value','margin_of_safety'];
  return `<section aria-label="Dividend quality"><h3>Dividend quality</h3><p class="detail-description">Cash payout uses dividend cash outflow and positive FCF from matched quarters or FY. Growth gaps compare total FCF with dividends per share over the same fiscal windows. Share-count changes, special dividends and source gaps still need review. These operating-company measures exclude financial businesses.</p><div class="detail-metrics">${keys.map(k=>`<div class="detail-metric"><label>${esc(fields[k].label)}</label><strong>${format(row[k],fields[k])}</strong><small>${esc(row[k+'_period']||'')}${row[k]==null?'<br>'+esc(row.dividend_quality_reasons?.[k]||(['fair_value','margin_of_safety'].includes(k)?'Enter your own fair value below':'Source unavailable')):''}</small></div>`).join('')}</div><p class="detail-period">${esc(row.fcf_payout_basis||'Dividend cash basis unavailable')}. Growth gap is in percentage points. Longer CAGR requires six or eleven consecutive annual records. No shorter horizon is substituted.</p><button class="button secondary" id="edit-fair-value">Enter fair value</button><p class="detail-period">Fair value is your dated USD per-share estimate, stored in this browser. It is not a Yahoo analyst target. ${esc(row.fair_value_source||'No estimate entered.')}</p></section>`;
}

export function installQualityUI({state,apply,refresh,findStock,toast,getCoverage}) {
  const $=id=>document.getElementById(id);
  const dialog=document.createElement('dialog');dialog.id='quality-dialog';dialog.className='wide-dialog';
  const input=(key,label,step='0.1')=>`<label class="field-label">${label}<input name="${key}" type="number" step="${step}" required></label>`;
  dialog.innerHTML=`<form id="quality-form"><div class="dialog-heading"><div><span class="eyebrow">EDITABLE SCREEN</span><h2>Dividend quality preset</h2></div><button type="button" class="close" data-close="quality-dialog">×</button></div><p class="helper">Starting preferences for operating companies, not universal minimums. Applies alongside the $20bn floor and your listing preference.</p><label class="field-label">Matched fiscal-year growth horizon<select name="horizon"><option value="1">1Y growth</option><option value="3">3Y CAGR</option><option value="5">5Y CAGR</option><option value="10">10Y CAGR</option></select></label><div class="quality-grid">${input('divGrowth','Minimum dividend growth %')}${input('fcfGrowth','Minimum FCF growth %')}${input('payout','FCF payout below %')}${input('roic','Minimum ROIC proxy %')}${input('current','Current ratio above')}</div><label class="check-label"><input name="sustainability" type="checkbox"> Require FCF growth to keep pace with dividend growth</label><label class="check-label"><input name="yieldGate" type="checkbox"> Optional starting dividend yield range</label><div class="quality-grid">${input('yieldMin','Minimum dividend yield %')}${input('yieldMax','Maximum dividend yield %')}</div><label class="check-label"><input name="fcfYieldGate" type="checkbox"> Optional minimum FCF yield</label>${input('fcfYield','Minimum FCF yield %')}<label class="check-label"><input name="marginGate" type="checkbox"> Optional discount to your entered fair value</label>${input('margin','Minimum margin of safety %')}<p class="helper">Yahoo often supplies only four annual records. 5Y CAGR needs six and may return no matches. Missing values fail a required rule. Change the horizon explicitly or remove that rule. The sustainability rule compares total FCF with per-share dividends, not future coverage.</p><p class="helper">ROIC is Atlas's book-capital proxy, not issuer-reported ROIC. Financial Services companies are excluded. Current ratio and cash payout remain sector-sensitive.</p><details><summary>Your supplied interpretation bands</summary><table class="div-table"><tbody><tr><th>Dividend growth</th><td>7–10% good. Double digits attractive.</td></tr><tr><th>FCF growth</th><td>8–10% solid. 13–14% strong. 6–7% may support slower dividend growth.</td></tr><tr><th>FCF payout</th><td>Below 50% good. 50–60% can be comfortable. 80–85% needs explanation. 100%+ concerning.</td></tr><tr><th>ROIC</th><td>10% baseline, 15% impressive, 20%+ high quality. Atlas provides a proxy.</td></tr><tr><th>Current ratio</th><td>Above 1.0, preferably above 1.2, depending on sector.</td></tr><tr><th>FCF yield</th><td>8% attractive. Lower may be acceptable with faster growth.</td></tr><tr><th>Starting yield</th><td>Optional 2–3% with growth. Above 5% targets current income.</td></tr><tr><th>Margin of safety</th><td>10% below entered fair value, or 20% for a wider discount.</td></tr></tbody></table></details><p id="quality-error" class="form-error" role="alert"></p><div class="dialog-footer"><button class="button primary" type="submit">Apply preset</button></div></form>`;
  document.body.append(dialog);
  const form=$('quality-form');
  const showCoverage=()=>{let note=$('quality-coverage');if(!note){note=document.createElement('p');note.id='quality-coverage';note.className='helper';form.querySelector('.dialog-footer').before(note);}const data=getCoverage?.();const horizon=form.elements.horizon.value;note.textContent=data?`${data.metrics?.['fcf_growth_'+horizon+'y']?.available||0} of ${data.main_listings} main listings have ${horizon}Y FCF growth. ${data.metrics?.['fcf_div_growth_gap_'+horizon+'y']?.available||0} have a matched dividend-growth comparison. Coverage is for the snapshot universe before your other filters.`:'History coverage appears after loading a published snapshot. Required missing metrics are excluded.';};
  form.elements.horizon.onchange=showCoverage;
  const openPreset=()=>{const options={...qualityDefaults,...state.dividendQualityPreset};for(const [key,v] of Object.entries(options)){const el=form.elements.namedItem(key);if(el.type==='checkbox')el.checked=v;else el.value=v;}$('quality-error').textContent='';showCoverage();dialog.showModal();};
  form.onsubmit=e=>{e.preventDefault();try{const options=Object.fromEntries(Object.keys(qualityDefaults).map(k=>{const el=form.elements.namedItem(k);return [k,el.type==='checkbox'?el.checked:Number(el.value)];}));const filters=qualityPreset(options);state.dividendQualityPreset=options;dialog.close();apply(filters,options);toast('Dividend quality rules applied. Required missing values are excluded.');}catch(error){$('quality-error').textContent=error.message;}};
  const valuation=document.createElement('dialog');valuation.id='valuation-dialog';
  valuation.innerHTML=`<form id="valuation-form"><div class="dialog-heading"><h2>Your fair-value estimate</h2><button type="button" class="close" data-close="valuation-dialog">×</button></div><p class="helper">USD per share. Check the share class and denomination. Estimates are saved in this browser and stay separate from Yahoo data.</p><label class="field-label">Atlas ticker<input name="symbol" required></label>${input('value','Fair value in USD per share','any')}<label class="field-label">Valuation date<input name="date" type="date" required></label><label class="field-label">Source or model<input name="source" maxlength="200" required placeholder="e.g. my DCF, annual report and assumptions"></label><p id="valuation-error" class="form-error" role="alert"></p><div class="dialog-footer"><button id="remove-valuation" type="button" class="text-button danger">Remove estimate</button><button class="button primary" type="submit">Save estimate</button></div></form>`;
  document.body.append(valuation);const vf=$('valuation-form');
  const fill=symbol=>{const old=readValuations()[symbol];vf.elements.value.value=old?.value??'';vf.elements.date.value=old?.date||new Date().toLocaleDateString('en-CA');vf.elements.source.value=old?.source||'';};
  const openValuation=(symbol='')=>{vf.elements.symbol.value=symbol;fill(symbol);$('valuation-error').textContent='';valuation.showModal();};
  vf.elements.symbol.onchange=()=>fill(vf.elements.symbol.value.trim().toUpperCase());
  vf.onsubmit=async e=>{e.preventDefault();try{const symbol=vf.elements.symbol.value.trim().toUpperCase(),value=Number(vf.elements.value.value);if(!Number.isFinite(value)||value<=0)throw new Error('Enter a positive USD per-share estimate.');if(!vf.elements.source.value.trim())throw new Error('Enter the valuation source or model.');const row=await findStock(symbol);if(!row?.symbol)throw new Error('Use a ticker present in Atlas.');const values=readValuations();values[symbol]={value,date:vf.elements.date.value,source:vf.elements.source.value.trim()};localStorage.setItem(valuationKey,JSON.stringify(values));valuation.close();refresh(symbol);toast('Dated fair value saved in this browser.');}catch(error){$('valuation-error').textContent=error.message;}};
  $('remove-valuation').onclick=()=>{try{const symbol=vf.elements.symbol.value.trim().toUpperCase(),values=readValuations();delete values[symbol];localStorage.setItem(valuationKey,JSON.stringify(values));valuation.close();refresh(symbol);}catch(error){$('valuation-error').textContent=error.message;}};
  return {openPreset,openValuation};
}
