import { escapeHtml as esc } from './format.js';
const definitions = [
  ['roic_proxy','ROIC proxy'],['roce','ROCE'],
  ['roic_proxy_5y_avg','ROIC proxy 5Y average'],['roce_5y_avg','ROCE 5Y average'],
];
const percent = v => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(2)+'%' : '<span class="missing">—</span>';
export function capitalReturnsHTML(row) {
  const cards=definitions.map(([key,label])=>{
    const average=key.endsWith('_5y_avg'),base=average?key.replace('_5y_avg',''):key;
    const count=average?`<br>${row[base+'_5y_count']??0}/5 valid annual ratios`:'';
    return `<div class="detail-metric"><label>${label}</label><strong>${percent(row[key])}</strong><small>${esc(row[key+'_period']||row[key+'_reason']||'Awaiting annual income and balance sheets')}${count}</small></div>`;
  }).join('');
  const annual=row.capital_returns_history||[];
  const history=annual.length?`<div class="div-table-wrap"><table class="div-table"><thead><tr><th>Fiscal year end</th><th>ROIC proxy</th><th>ROCE</th></tr></thead><tbody>${[...annual].reverse().map(p=>`<tr><td>${esc(p.end_date)}</td><td title="${esc(p.reasons?.roic_proxy||'')}">${percent(p.roic_proxy)}</td><td title="${esc(p.reasons?.roce||'')}">${percent(p.roce)}</td></tr>`).join('')}</tbody></table></div>`:'';
  return `<section aria-label="Capital returns"><h3>Returns on capital</h3><p class="detail-description">ROIC proxy uses after-tax operating income divided by average debt + equity − cash. ROCE uses EBIT divided by average total assets − current liabilities. Both use matched reporting currency and opening/closing annual balances. Financial businesses are excluded.</p><div class="detail-metrics">${cards}</div><p class="detail-period">Five-year averages are arithmetic means of five consecutive annual ratios ending at the latest FY. They stay unavailable with fewer years or invalid inputs. Yahoo usually returns four annual statements, and an opening balance is needed for the earliest ratio. Older records are retained as its window advances. No new data requests are needed for these calculations.</p>${history}<p class="detail-period">Book-capital estimates, without economic adjustments for leases, R&amp;D, goodwill or excess cash. Annual operands and missing reasons are included in the AI pack. Methods: <a href="https://pages.stern.nyu.edu/~adamodar/New_Home_Page/definitions.html" target="_blank" rel="noopener">ROIC</a> · <a href="https://www.accaglobal.com/uk/en/student/exam-support-resources/fundamentals-exams-study-resources/f2/technical-articles/ratio-analysis.html" target="_blank" rel="noopener">ROCE</a>.</p></section>`;
}
