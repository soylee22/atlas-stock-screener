import { rankedCSV } from './ranked-export.js';
import { openAnalysisPack } from './analysis-ui.js';
import { api, isPublished, reloadSnapshot, exportSnapshot, chartRows, prepareTextDownload, customDetail } from './data-source.js';
import { createQuadrant, quadrantDefaults } from './quadrant.js';
import { parseNumber, compact, escapeHtml as esc, annualDividends } from './format.js';

const $ = id => document.getElementById(id);
const FLAGS = { us: '🇺🇸', gb: '🇬🇧', ca: '🇨🇦', jp: '🇯🇵', kr: '🇰🇷', tw: '🇹🇼' };
const SHORT = { us: 'US', gb: 'UK', ca: 'Canada', jp: 'Japan', kr: 'South Korea', tw: 'Taiwan' };
function readStorage(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; } }
const state = { regions: [], search: '', filters: [], sort: 'market_cap', direction: 'desc', columns: [], page: 0, pageSize: 100, includeOther: false, mainOnly: true, preset: 'all', watchOnly: false, view: '', section:'table', sma:{window:200,interval:'daily'}, quadrant:{...quadrantDefaults} };
let quadrant, chartRefreshed = 0;
let schema, fields, defaults, rows = [], total = 0, status = {}, saved = readStorage('atlas.views.v1', {}), watched = readStorage('atlas.watch.v1', []);
let requestSeq = 0, selectedSymbol = null, dividendSymbol = null, filterIndex = -1, toastTimeout, refreshTimer, isLoading = false;

function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimeout); toastTimeout = setTimeout(() => $('toast').hidden = true, 4200); }
function persist(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch { toast('Browser storage is unavailable. Changes last for this session.'); } }
function params() {
  return new URLSearchParams({ search: state.search, regions: state.regions.join(','), filters: JSON.stringify(state.filters), sort: state.sort, direction: state.direction, include_other: state.includeOther, main_only: state.mainOnly, sma_window:state.sma.window, sma_interval:state.sma.interval, columns:state.columns.join(','), only_symbols: state.watchOnly ? watched.join(',') || '__empty_watchlist__' : '' });
}
function fmt(value, field, plain = false) {
  if (value === null || value === undefined || value === '') return plain ? '' : '<span class="missing" title="Unavailable or awaiting retrieval">—</span>';
  if (field.kind === 'text') return esc(value);
  if (!Number.isFinite(Number(value))) return '<span class="missing">—</span>';
  if (field.kind === 'percent') {
    const sign = (['change', 'change_52w', 'fcf_change', 'div_growth'].includes(field.key) || (/_growth_(1|3|5|10)y$/.test(field.key)||/^sma_.*_distance$/.test(field.key))) && value > 0 ? '+' : '';
    return `${sign}${Number(value).toFixed(2)}%`;
  }
  if (field.kind === 'integer') return Math.round(value).toLocaleString('en-GB');
  if (field.kind === 'price') return `${plain ? '$' : '<span class="usd-sign">$</span>'}${Number(value).toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: Math.abs(value) < 1 ? 4 : 2 })}`;
  if (field.kind === 'usd' || field.key.includes('volume')) {
    const n = compact(value, field.key.includes('volume') ? 1 : 2);
    return `${field.kind === 'usd' ? (plain ? '$' : '<span class="usd-sign">$</span>') : ''}${n.value}${plain ? n.unit : '<span class="number-unit">' + n.unit + '</span>'}`;
  }
  return Number(value).toLocaleString('en-GB', { maximumFractionDigits: 2 });
}
function valueClass(value, key) {
  if (value == null) return '';
  if ((['change', 'change_52w', 'fcf_change', 'div_growth', 'net_margin', 'fcf_yield'].includes(key) || /_growth_(1|3|5|10)y$/.test(key)||/^sma_.*_distance$/.test(key))) return value > 0 ? 'positive' : value < 0 ? 'negative' : '';
  return value < 0 ? 'negative' : '';
}
function companyIcon(row, color = '#385943') {
  const initials = esc(row.name.replace(/[^a-z0-9]/gi, '').slice(0, 2).toUpperCase());
  return `<span class="company-monogram" style="background:${color}45;color:#c1cfb4">${initials}${row.logo_url ? `<img class="company-logo" src="${esc(row.logo_url)}" alt="" loading="lazy" decoding="async">` : ''}</span>`;
}
function markEdited() { $('unsaved').hidden = false; }
function showSection(section, refresh = true) {
  state.section = section === 'quadrant' ? 'quadrant' : 'table';
  const chart = state.section === 'quadrant';
  $('table-view').hidden = chart; $('quadrant-view').hidden = !chart; $('columns').hidden = chart;
  $('export').querySelector('span').textContent = chart ? 'Export screen' : 'Export';
  for (const key of ['table','quadrant']) { const b = $(key+'-section'); b.classList.toggle('active',state.section===key); b.setAttribute('aria-pressed',state.section===key); }
  if (chart && refresh) { chartRefreshed = Date.now(); quadrant.refresh(state.quadrant); }
}
function update(changes = {}, edited = true) {
  Object.assign(state, changes, { page: 0 });
  if (edited) markEdited();
  persist('atlas.last.v1', { ...state, search: state.search });
  renderControls(); loadRows();
}

function renderMarkets() {
  $('markets').innerHTML = `<button class="all-markets ${!state.regions.length ? 'active' : ''}" data-region="all">◎ All markets</button>` + Object.keys(FLAGS).map(code => {
    const counts = status.counts?.find(c => c.region === code);
    return `<button class="market-card ${state.regions.includes(code) ? 'active' : ''}" data-region="${code}" aria-pressed="${state.regions.includes(code)}"><span class="market-flag">${FLAGS[code]}</span><span class="market-info"><span class="market-name">${SHORT[code]}</span><span class="market-count">${counts ? (state.mainOnly ? counts.main_stocks : counts.stocks).toLocaleString('en-GB') + ' listings' : 'Loading listings'}</span></span></button>`;
  }).join('') + '<span class="market-caption">Click markets to combine them</span>';
}
function renderControls() {
  $('sma-setting-note').textContent=`Custom SMA: ${state.sma.window} ${state.sma.interval==='daily'?'days':'weeks'}`;
  renderMarkets();
  $('search').value = state.search;
  $('column-count').textContent = state.columns.length;
  $('watch-count').textContent = watched.length ? `(${watched.length})` : '';
  $('include-other').checked = state.includeOther;
  $('main-only').checked = state.mainOnly;
  $('include-other').disabled = state.mainOnly;
  $('reset').hidden = !state.filters.length && !state.search && !state.regions.length && !state.watchOnly;
  $('active-filters').hidden = !state.filters.length;
  const symbols = { gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', ne: '≠', contains: 'contains', not_contains: 'excludes', missing: 'is missing', present: 'is available', between: 'between' };
  $('active-filters').innerHTML = state.filters.map((f, i) => {
    const field = fields[f.field], value = field.kind === 'text' ? esc(f.value) : fmt(f.value, field, true);
    return `<span class="filter-chip"><button class="edit-filter" data-index="${i}">${esc(field.label)} ${symbols[f.op]} ${['missing', 'present'].includes(f.op) ? '' : value}${f.op === 'between' ? ' and ' + fmt(f.value2, field, true) : ''}</button><button data-remove="${i}" aria-label="Remove ${esc(field.label)} filter">×</button></span>`;
  }).join('');
  for (const b of $('presets').querySelectorAll('button')) b.classList.toggle('active', b.dataset.preset === state.preset);
}
function colWidth(field) { if (field.kind === 'text') return field.key === 'industry' ? 240 : field.key.includes('period') || field.key.includes('time') || field.key.includes('fetched') ? 190 : 160; return field.key === 'div_years' ? 155 : 145; }
function renderTable() {
  $('thead').innerHTML = `<tr><th class="symbol-head ${state.sort === 'symbol' ? 'sorted' : ''}"><button data-sort="symbol">Company <span class="sort-arrow">${state.sort === 'symbol' ? (state.direction === 'desc' ? '↓' : '↑') : ''}</span></button><span class="col-unit">SYMBOL / NAME</span></th>` + state.columns.map(key => {
    const f = fields[key], unit = ['usd', 'price'].includes(f.kind) ? 'USD' : f.kind === 'percent' ? '%' : key === 'fcf_change' ? 'FY YoY' : key === 'volume' ? 'SHARES' : key === 'div_years' ? 'OBSERVED STREAK' : '';
    return `<th class="${f.kind === 'text' ? 'text' : ''} ${state.sort === key ? 'sorted' : ''}" style="width:${colWidth(f)}px" title="${esc(f.description || f.label)}"><button data-sort="${key}">${esc(f.label)}<span class="sort-arrow">${state.sort === key ? (state.direction === 'desc' ? '↓' : '↑') : ''}</span></button><span class="col-unit">${key === 'fcf_change' ? 'FY YoY %' : unit}</span></th>`;
  }).join('') + '</tr>';
  const colors = ['#385943', '#4e4768', '#314c63', '#6c4c36', '#53653f', '#553f4f'];
  $('tbody').innerHTML = rows.map(row => {
    const color = colors[[...row.symbol].reduce((a, c) => a + c.charCodeAt(0), 0) % colors.length];
    return `<tr><td class="company"><div class="company-cell"><button class="watch-star ${watched.includes(row.symbol) ? 'watched' : ''}" data-watch="${esc(row.symbol)}" aria-label="${watched.includes(row.symbol) ? 'Remove' : 'Add'} ${esc(row.symbol)} ${watched.includes(row.symbol) ? 'from' : 'to'} watchlist">${watched.includes(row.symbol) ? '★' : '☆'}</button>${companyIcon(row, color)}<button class="company-meta company-link" data-detail="${esc(row.symbol)}" title="Open ${esc(row.name)} deep dive"><span class="symbol-line"><span class="ticker">${esc(row.symbol)}</span><span class="ticker-tag">${SHORT[row.region_code]}</span></span><span class="company-name">${esc(row.name)}</span></button></div></td>` + state.columns.map(key => {
      const f = fields[key], val = row[key], period = key === 'net_income' || key === 'net_margin' || key === 'revenue' ? row.income_period : ['fcf', 'capex', 'operating_cf', 'fcf_yield'].includes(key) ? row.cf_period : key === 'fcf_change' ? row.fcf_growth_period : row[key + '_period'] || '';
      return `<td class="${f.kind === 'text' ? 'text' : ''} ${valueClass(val, key)}" title="${esc(period ? period + ' · ' : '')}${esc(val ?? row.annual_growth_missing?.[key] ?? 'Unavailable or awaiting retrieval')}">${fmt(val, f)}</td>`;
    }).join('') + '</tr>';
  }).join('');
  $('stock-table').style.minWidth = `${360 + state.columns.reduce((a, k) => a + colWidth(fields[k]), 0)}px`;
  $('empty').hidden = !!rows.length || isLoading;
  $('result-count').textContent = total.toLocaleString('en-GB');
  $('result-label').textContent = ' listings';
  $('sort-note').textContent = `Sorted by ${fields[state.sort]?.label || 'symbol'} ${state.direction === 'desc' ? '↓' : '↑'}`;
  $('page-range').textContent = `${total ? state.page * state.pageSize + 1 : 0}–${Math.min((state.page + 1) * state.pageSize, total)} of ${total.toLocaleString('en-GB')}`;
  $('page-number').textContent = state.page + 1;
  $('first-page').disabled = $('prev-page').disabled = state.page === 0;
  $('next-page').disabled = $('last-page').disabled = (state.page + 1) * state.pageSize >= total;
}
async function loadRows(quiet = false) {
  if(quiet && isLoading)return;
  const seq = ++requestSeq; isLoading = true;
  if (!quiet) $('loading-dot').classList.add('pulse');
  const query = params(); query.set('limit', state.pageSize); query.set('offset', state.page * state.pageSize);
  try {
    const response = await api('/api/stocks?' + query);
    if (!response.ok) throw new Error((await response.json()).detail || 'Data request failed');
    const data = await response.json();
    if (seq !== requestSeq) return;
    rows = data.rows; total = data.total; isLoading = false; renderTable();
    if (state.section === 'quadrant' && (!quiet || Date.now()-chartRefreshed >= 60000)) { chartRefreshed=Date.now(); quadrant.refresh(state.quadrant); }
  } catch (error) { if (seq === requestSeq) { isLoading = false; toast(error.message); } }
  if (seq === requestSeq) $('loading-dot').classList.remove('pulse');
}
async function loadStatus() {
  try {
    const r = await api('/api/status'); if (!r.ok) return; status = await r.json(); renderMarkets();
    const counts = status.counts || [], enriched = counts.reduce((a, c) => a + (state.mainOnly ? c.main_enriched : c.enriched), 0), stocks = counts.reduce((a, c) => a + (state.mainOnly ? c.main_stocks : c.stocks), 0);
    $('data-note').textContent = `${enriched.toLocaleString('en-GB')} / ${stocks.toLocaleString('en-GB')} company profiles loaded`;
    $('refresh-status').textContent = isPublished ? `Snapshot ${new Date(status.snapshot.built).toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' })}${status.error ? ' · Cached quotes, refresh failed' : ''}` : status.refreshing ? 'Refreshing market universe…' : status.error ? 'Refresh failed. Cached data shown.' : status.completed ? `Quotes fetched ${new Date(status.completed).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : 'First market scan in progress';
    $('refresh').disabled = !!status.refreshing;
    if ($('data-dialog').open) renderCoverage();
  } catch { $('refresh-status').textContent = isPublished ? 'Published data unavailable' : 'Local service unavailable'; }
}
function reset() { update({ filters: [], regions: [], search: '', preset: 'all', watchOnly: false, sort: 'market_cap', direction: 'desc' }); }
function renderSaved() {
  $('saved-views').innerHTML = '<option value="">All stocks</option>' + Object.keys(saved).sort().map(name => `<option value="${esc(name)}">${esc(name)}</option>`).join('');
  $('saved-views').value = state.view;
}
function filterOptions(key) {
  const text = fields[key].kind === 'text';
  const options = text ? [['eq', 'is'], ['ne', 'is not'], ['contains', 'contains'], ['not_contains', 'does not contain']] : [['gte', 'at least ≥'], ['gt', 'greater than >'], ['lte', 'at most ≤'], ['lt', 'less than <'], ['between', 'between'], ['eq', 'equals ='], ['ne', 'does not equal ≠']];
  options.push(['present', 'is available'], ['missing', 'is missing']);
  $('filter-op').innerHTML = options.map(([value, label]) => `<option value="${value}">${label}</option>`).join('');
  $('filter-description').textContent = fields[key].description || '';
  $('filter-value').placeholder = text ? 'e.g. Technology' : fields[key].kind === 'percent' ? 'e.g. 3.5' : 'e.g. 10B or 100M';
  filterInputs();
}
function filterInputs() {
  const op = $('filter-op').value;
  $('filter-value-label').hidden = ['present', 'missing'].includes(op);
  $('filter-value2-label').hidden = op !== 'between';
}
function openFilter(key = 'market_cap', index = -1) {
  filterIndex = index; $('filter-error').textContent = '';
  $('filter-field').innerHTML = schema.columns.map(f => `<option value="${f.key}">${esc(f.label)}</option>`).join('');
  $('filter-field').value = key; filterOptions(key);
  const f = index >= 0 ? state.filters[index] : null;
  if (f) { $('filter-op').value = f.op; filterInputs(); }
  $('filter-value').value = f?.value ?? ''; $('filter-value2').value = f?.value2 ?? '';
  $('filter-dialog').showModal();
}
function renderColumns() {
  const search = $('column-search').value.toLowerCase();
  const groups = [...new Set(schema.columns.map(f => f.group))];
  $('column-options').innerHTML = groups.map(g => {
    const matches = schema.columns.filter(f => f.group === g && f.label.toLowerCase().includes(search));
    if (!matches.length) return '';
    return `<h4>${esc(g)}</h4>` + matches.map(f => `<label class="column-option" title="${esc(f.description)}"><input type="checkbox" data-column="${f.key}" ${state.columns.includes(f.key) ? 'checked' : ''}>${esc(f.label)}</label>`).join('');
  }).join('');
  $('visible-count').textContent = state.columns.length;
  $('column-order').innerHTML = '<div class="column-order-row"><span class="order-no">⌖</span><span class="order-label">Company (pinned)</span></div>' + state.columns.map((k, i) => `<div class="column-order-row"><span class="order-no">${i + 1}</span><span class="order-label">${esc(fields[k].label)}</span><button data-move="${i}" data-step="-1" ${i === 0 ? 'disabled' : ''} aria-label="Move ${esc(fields[k].label)} left">↑</button><button data-move="${i}" data-step="1" ${i === state.columns.length - 1 ? 'disabled' : ''} aria-label="Move ${esc(fields[k].label)} right">↓</button><button data-hide="${k}" aria-label="Hide ${esc(fields[k].label)}">×</button></div>`).join('');
}
function renderCoverage() {
  $('coverage').innerHTML = Object.keys(FLAGS).map(code => {
    const count = status.counts?.find(c => c.region === code), progress = status.coverage?.[code];
    return `<div class="coverage-card"><h3>${FLAGS[code]} ${SHORT[code]}</h3><strong>${(count?.main_stocks || 0).toLocaleString('en-GB')}</strong><p>main listings · ${(count?.stocks || 0).toLocaleString('en-GB')} total stocks<br>${count?.enriched || 0} profiles · ${count?.fcf || 0} with free cash flow<br>${progress?.error ? esc(progress.error) : progress?.finished ? `${progress.stored.toLocaleString('en-GB')} / ${progress.expected.toLocaleString('en-GB')} Yahoo records scanned${progress.difference ? ' · reconciliation gap ' + progress.difference : ''}` : 'Universe scan in progress'}</p></div>`;
  }).join('');
  $('logo-status').textContent = `${(status.logos?.cached || 0).toLocaleString('en-GB')} website icons ${isPublished ? 'in this published snapshot' : 'cached on this Mac'}. Icons load as company website data becomes available. Missing icons keep a letter badge.`;
  const refreshInfo = isPublished ? `<p>Quotes last fetched ${esc(status.completed ? new Date(status.completed).toLocaleString('en-GB') : 'unknown')}. Snapshots update every four hours. ${status.error ? esc(status.error) : ''}</p>${status.refresh_health ? `<p>Latest cloud batch: ${status.refresh_health.financials?.succeeded || 0} company profiles added or refreshed, ${status.refresh_health.financials?.failed || 0} unsuccessful attempts.</p>` : ''}` : '';
  $('fx-rates').innerHTML = refreshInfo + '<h3>Cached currency rates</h3><table class="fx-table"><tbody>' + Object.entries(status.fx || {}).sort().map(([currency, fx]) => `<tr><td>${esc(currency)}</td><td>1 ${esc(currency)} = $${Number(fx.rate).toFixed(6)}</td><td>${esc(fx.date)}</td></tr>`).join('') + '</tbody></table>' + (status.missing_fx ? `<p>${status.missing_fx} market caps have no cached conversion rate.</p>` : '');
}
async function exportRanked(points,options) {
  const csv=rankedCSV(points,options);
  $('export-dialog').showModal();$('export-title').textContent='Export ranked stock list';
  $('export-description').textContent='Ranks match the selected zones, frontier filter and list order. Includes raw axis values, score, frontier membership and reporting dates. Balanced score is a relative rank, not an investment recommendation.';
  $('export-summary').textContent=`${points.length.toLocaleString('en-GB')} ranked listings ready to export.`;
  $('download-csv').hidden=true;$('copy-csv').disabled=false;
  $('copy-csv').onclick=async()=>{try{await navigator.clipboard.writeText(csv);toast('Ranked CSV copied');}catch{toast('Clipboard unavailable. Use Download CSV.');}};
  const prepared=await prepareTextDownload(csv,'atlas-ranked-stocks.csv','text/csv');
  $('download-csv').href=prepared.url;$('download-csv').download='atlas-ranked-stocks.csv';$('download-csv').hidden=false;$('export-dialog').onclose=prepared.dispose;
}
async function openDetail(symbol) {
  selectedSymbol = symbol; $('detail').hidden = false;
  let row = await api('/api/stock/' + encodeURIComponent(symbol)).then(r => r.json());
  row=await customDetail(row,params());
  if (!row.symbol) { toast(row.detail || 'Company data unavailable'); return; }
  if (symbol !== selectedSymbol) return;
  const metrics = ['sma_200d_distance','sma_200w_distance','sma_custom_distance','market_cap', 'div_yield', 'net_income', 'net_margin', 'fcf', 'capex', 'fcf_change', 'div_years', 'div_growth', 'pe', 'below_52w_high'];
  const growthKeys = schema.columns.filter(f => /^(revenue|net_income)_growth_(1|3|5|10)y$/.test(f.key)).map(f => f.key);
  const growthDetails = `<h3>Annual revenue &amp; income growth</h3><p class="detail-description">Completed fiscal years in reporting currency. 1Y compares consecutive years. Longer horizons are annualised CAGR. Yahoo usually supplies four annual records. Older records are retained as its window advances. Loss or zero starting income has no meaningful percentage growth.</p><div class="detail-metrics">${growthKeys.map(k => `<div class="detail-metric"><label>${esc(fields[k].label)}</label><strong class="${valueClass(row[k], k)}">${fmt(row[k], fields[k])}</strong><small>${esc(row[k + '_period'] || '')}${row[k] == null ? '<br>' + esc(row.annual_growth_missing?.[k] || 'Awaiting annual statements') : ''}</small></div>`).join('')}</div><p class="detail-period">Annual statements fetched: ${esc(row.annual_growth_fetched ? new Date(row.annual_growth_fetched).toLocaleString('en-GB') : 'Awaiting retrieval')}</p>`;
  $('detail-content').innerHTML = `<div class="detail-top"><span>${FLAGS[row.region_code]} ${esc(row.exchange)} · ${esc(row.quote_currency)}</span><button class="close" id="close-detail" aria-label="Close company details">×</button></div><div class="detail-identity">${companyIcon(row)}<h2>${esc(symbol)}</h2></div><p class="detail-name">${esc(row.name)}</p><div class="detail-tags"><span>${esc(row.sector || 'Sector awaiting data')}</span><span>${esc(row.industry || 'Industry awaiting data')}</span></div><div class="detail-price">${fmt(row.price, fields.price)}<span class="${valueClass(row.change, 'change')}">${fmt(row.change, fields.change)}</span></div><div class="detail-metrics">${metrics.map(k => `<div class="detail-metric"><label>${esc(fields[k].label)}</label><strong class="${valueClass(row[k], k)}">${fmt(row[k], fields[k])}</strong></div>`).join('')}</div><p class="detail-period">Income: ${esc(row.income_period || 'Awaiting data')}<br>Cash flow: ${esc(row.cf_period || 'Awaiting data')}<br>FCF growth: ${esc(row.fcf_growth_period || 'Awaiting data')}<br>Quote: ${esc(row.quote_time ? new Date(row.quote_time).toLocaleString('en-GB') : 'Unavailable')} · ${row.delay ?? 'Unknown'} min provider delay</p>${row.financial_quality_note?'<p class="detail-period negative">'+esc(row.financial_quality_note)+'</p>':''}<h3>Moving averages</h3><p class="detail-description">${esc(row.technical_basis||'Cached Yahoo close history is awaiting retrieval.')} Reference close: ${esc(row.technical_asof||'Pending')}. Positive distance means above the average.</p><p class="detail-period">200-day: ${esc(row.sma_200d_period||'Pending')}<br>200-week: ${esc(row.sma_200w_period||'Pending')}<br>Custom: ${esc(row.sma_custom_period||'Pending')}</p>${growthDetails}<div class="analysis-card"><span class="eyebrow">TAKE IT TO YOUR AI</span><h3>Analyse the whole business</h3><p>Financial statements, earnings trends, dividends and peer comparisons, with a detailed analysis prompt.</p><button id="open-ai-analysis" class="button primary">Create AI analysis pack ↗</button></div><h3>Dividend history</h3><p class="detail-description">Annual dividends per share, individual payments and growth from Yahoo’s earliest available record.</p><button id="open-dividends" class="button primary">Explore dividend history ↗</button><button id="refresh-profile" class="button secondary">↻ Refresh company data</button>${row.financial_error ? `<p class="detail-period negative">${esc(row.financial_error)}</p>` : !row.financial_fetched ? `<p class="detail-period">${isPublished ? 'Financial profile is not in this snapshot yet. Scheduled refreshes add more company data.' : 'Financial profile queued. It will update as data arrives.'}</p>` : `<p class="detail-period">Financials fetched ${esc(new Date(row.financial_fetched).toLocaleString('en-GB'))}</p>`}${row.description ? '<h3>About the business</h3><p class="detail-description">' + esc(row.description) + '</p>' : ''}<p class="detail-period"><a href="https://finance.yahoo.com/quote/${encodeURIComponent(symbol)}/" target="_blank" rel="noopener">View source on Yahoo Finance ↗</a></p>`;
  $('close-detail').onclick = () => { $('detail').hidden = true; selectedSymbol = null; };
  $('open-ai-analysis').onclick = () => openAnalysisPack(row, {columns:Object.values(fields)}, status, toast);
  $('open-dividends').onclick = () => openDividends(symbol);
  if (isPublished) $('refresh-profile').textContent = '↻ Reload published company data';
  $('refresh-profile').onclick = async () => { if (isPublished) { await reloadSnapshot(); await openDetail(symbol); toast('Latest published company snapshot loaded'); return; } api('/api/enrich', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbols: [symbol] }) }); toast('Company data queued for refresh'); };
}

let dividendRow, dividendCurrency = 'local', dividendTab = 'annual';
function perShare(amount, currency) {
  const value = dividendCurrency === 'usd' ? amount * (status.fx?.[currency]?.rate ?? NaN) : amount;
  return Number.isFinite(value) ? `${dividendCurrency === 'usd' ? '$' : ''}${value.toLocaleString('en-GB', { minimumFractionDigits: 2, maximumFractionDigits: 6 })}${dividendCurrency === 'local' ? ' ' + esc(currency) : ''}` : 'FX unavailable';
}
async function openDividends(symbol) {
  dividendSymbol = symbol;
  if (!$('dividend-dialog').open) $('dividend-dialog').showModal();
  $('dividend-content').innerHTML = '<div class="dialog-heading"><h2>Loading dividend history…</h2><button class="close" data-close="dividend-dialog">×</button></div>';
  const row = await api('/api/stock/' + encodeURIComponent(symbol)).then(r => r.json());
  if (!row.symbol) { toast(row.detail || 'Company data unavailable'); return; }
  if (symbol !== dividendSymbol) return;
  dividendRow = row; dividendCurrency = 'local'; dividendTab = 'annual'; renderDividends();
}
function renderDividends() {
  const row = dividendRow;
  const rawCurrency = row.dividend_currency || row.quote_currency;
  const pence = ['GBp', 'GBX'].includes(rawCurrency), currency = pence ? 'GBP' : rawCurrency;
  const events = (row.dividend_events || []).map(e => ({ ...e, amount: e.amount * (pence ? .01 : 1) }));
  const years = annualDividends(events);
  const completed = years.filter(y => !y.incomplete), latest = completed.at(-1), current = years.at(-1);
  const max = Math.max(...years.map(y => y.total), .01), width = 1040, height = 170, barWidth = (width - 30) / Math.max(years.length, 1);
  const chart = years.length ? `<svg viewBox="0 0 ${width} ${height + 27}" role="img" aria-label="Annual dividends per share from ${years[0].year} to ${years.at(-1).year}"><line x1="0" y1="${height}" x2="${width}" y2="${height}" stroke="#344235"/>${years.map((y, i) => { const h = y.total / max * (height - 15), x = i * barWidth + 12; return `<rect x="${x}" y="${height - h}" width="${Math.max(barWidth - 4, 2)}" height="${Math.max(h, 1)}" rx="2" fill="${y.incomplete ? '#596c45' : '#a6d772'}"><title>${y.year}${y.incomplete ? ' YTD' : ''}: ${perShare(y.total, currency)} · ${y.count} recorded payments</title></rect>${i % Math.max(Math.ceil(years.length / 13), 1) === 0 || i === years.length - 1 ? `<text x="${x + barWidth / 2}" y="${height + 19}" text-anchor="middle" fill="#7e90a1" font-size="10">${y.year}</text>` : ''}`; }).join('')}</svg>` : '';
  $('dividend-content').innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">STOCK DEEP DIVE / DIVIDENDS</span><h2>${esc(row.name)}</h2><p class="div-subtitle">${esc(row.symbol)} · ${esc(row.exchange)} · ${events.length.toLocaleString('en-GB')} recorded payments${years.length ? ` · ${years[0].year} to ${years.at(-1).year}` : ''}</p></div><button class="close" data-close="dividend-dialog" aria-label="Close dividend history">×</button></div><div class="div-metrics"><div><label>Latest completed year${latest ? ' · ' + latest.year : ''}</label><strong>${latest ? perShare(latest.total, currency) : '—'}</strong></div><div><label>${current?.year || new Date().getFullYear()} · year to date</label><strong>${current ? perShare(current.total, currency) : '—'}</strong></div><div><label>Observed growth streak</label><strong>${row.div_years != null ? row.div_years + ' years' : '—'}</strong></div><div><label>5Y dividend CAGR</label><strong class="${valueClass(row.div_growth, 'div_growth')}">${fmt(row.div_growth, fields.div_growth)}</strong></div></div><div class="div-toolbar"><div class="segmented"><button data-div-tab="annual" class="${dividendTab === 'annual' ? 'active' : ''}">Annual totals</button><button data-div-tab="events" class="${dividendTab === 'events' ? 'active' : ''}">Individual payments</button></div><div class="segmented" ${currency === 'USD' ? 'hidden' : ''}><button data-div-currency="local" class="${dividendCurrency === 'local' ? 'active' : ''}">${esc(currency || 'Local')}</button><button data-div-currency="usd" class="${dividendCurrency === 'usd' ? 'active' : ''}">USD</button></div><button class="button secondary" id="download-dividends">↓ Export history</button></div><div class="div-chart-large">${chart || `<div class="empty"><h2>${row.dividend_error ? 'Dividend history unavailable' : row.financial_fetched ? 'No dividend records returned' : isPublished ? 'Dividend history is not in this snapshot yet' : 'Dividend history is loading'}</h2><p>${row.dividend_error ? esc(row.dividend_error) : row.financial_fetched ? 'Yahoo did not return dividend events for this listing.' : isPublished ? 'Scheduled refreshes add company profiles progressively. Reload the published data after a new snapshot.' : 'The company profile is queued. This view will update when it arrives.'}</p></div>`}</div><p class="div-explanation">${dividendCurrency === 'usd' ? `USD values use the cached ${esc(currency)} rate${status.fx?.[currency]?.date ? ' from ' + esc(status.fx[currency].date) : ''}. These are current-FX comparisons, rather than historical dollar receipts.` : `Amounts are per share in ${esc(currency || 'the quote currency')}. Yahoo historical dividends reflect its treatment of share splits.`} Hover over a bar for its annual total.</p><div class="div-table-wrap"><table class="div-table"><thead><tr>${dividendTab === 'annual' ? '<th>Year</th><th>Dividend / share</th><th>Annual growth</th><th>Payments</th><th>Period</th>' : '<th>Ex-dividend date</th><th>Dividend / share</th><th>Year</th>'}</tr></thead><tbody>${dividendTab === 'annual' ? [...years].reverse().map(y => `<tr><td>${y.year}</td><td>${perShare(y.total, currency)}</td><td class="${valueClass(y.growth, 'div_growth')}">${y.growth === null ? '—' : (y.growth > 0 ? '+' : '') + y.growth.toFixed(2) + '%'}</td><td>${y.count}</td><td>${y.incomplete ? '<span class="ytd-badge">YTD · incomplete</span>' : y.count === 0 ? '<span class="missing">No recorded dividends</span>' : y.year === years[0].year ? 'First observed year' : 'Calendar year'}</td></tr>`).join('') : [...events].reverse().map(e => `<tr><td>${esc(e.date)}</td><td>${perShare(e.amount, currency)}</td><td>${esc(e.date.slice(0, 4))}</td></tr>`).join('')}</tbody></table></div><div class="div-footnotes"><p>Source: Yahoo Finance. History starts at the earliest returned dividend event${row.dividend_history_start ? ', ' + esc(row.dividend_history_start) : ''}. Older records may be absent. Special dividends may be included. A zero annual total means no event was recorded in that year.</p><p>Dates in the payment table are ex-dividend dates, rather than cash payment dates. Annual growth is withheld for the current year. The first observed year may have incomplete coverage.</p><p>Fetched ${row.financial_fetched ? esc(new Date(row.financial_fetched).toLocaleString('en-GB')) : 'pending'} · <a href="https://finance.yahoo.com/quote/${encodeURIComponent(row.symbol)}/history/?filter=div" target="_blank" rel="noopener">Check Yahoo dividend history ↗</a></p></div>`;
  $('download-dividends').disabled = isPublished && !row.dividend_exports?.[dividendTab];
  $('download-dividends').onclick = () => {
    const a = document.createElement('a');
    a.href = isPublished ? row.dividend_exports[dividendTab] : '/api/dividends/' + encodeURIComponent(row.symbol) + '/export?mode=' + dividendTab;
    a.download = `${row.symbol}-dividends-${dividendTab}.csv`; a.click();
  };
}

function bindEvents() {
  $('table-section').onclick = () => { showSection('table'); markEdited(); persist('atlas.last.v1',state); };
  $('quadrant-section').onclick = () => { showSection('quadrant'); markEdited(); persist('atlas.last.v1',state); };
  $('markets').onclick = e => { const code = e.target.closest('[data-region]')?.dataset.region; if (!code) return; update({ regions: code === 'all' ? [] : state.regions.includes(code) ? state.regions.filter(x => x !== code) : [...state.regions, code] }); };
  let searchTimer; $('search').oninput = e => { clearTimeout(searchTimer); searchTimer = setTimeout(() => update({ search: e.target.value }), 250); };
  $('thead').onclick = e => { const key = e.target.closest('[data-sort]')?.dataset.sort; if (key) update({ sort: key, direction: state.sort === key && state.direction === 'desc' ? 'asc' : 'desc' }); };
  $('tbody').onclick = e => {
    const watch = e.target.closest('[data-watch]')?.dataset.watch;
    if (watch) { watched = watched.includes(watch) ? watched.filter(s => s !== watch) : [...watched, watch]; persist('atlas.watch.v1', watched); renderControls(); if (state.watchOnly) loadRows(); else renderTable(); return; }
    const detail = e.target.closest('[data-detail]')?.dataset.detail; if (detail) openDetail(detail);
  };
  $('add-filter').onclick = () => openFilter();
  $('quick-filters').onclick = e => { const key = e.target.closest('[data-field]')?.dataset.field; if (key) openFilter(key); };
  $('filter-field').onchange = e => filterOptions(e.target.value); $('filter-op').onchange = filterInputs;
  $('filter-form').onsubmit = e => {
    e.preventDefault(); const key = $('filter-field').value, op = $('filter-op').value, text = fields[key].kind === 'text';
    const value = text ? $('filter-value').value.trim() : parseNumber($('filter-value').value), value2 = parseNumber($('filter-value2').value);
    if (!['missing', 'present'].includes(op) && (value === null || value === '')) { $('filter-error').textContent = text ? 'Enter a value.' : 'Enter a number, such as 10B, 100M or 3.5.'; return; }
    if (op === 'between' && (value2 === null || value2 < value)) { $('filter-error').textContent = 'The upper value must be at least the lower value.'; return; }
    const rule = { field: key, op, value, ...(op === 'between' ? { value2 } : {}) }, filters = [...state.filters];
    if (filterIndex >= 0) filters[filterIndex] = rule; else filters.push(rule);
    $('filter-dialog').close(); update({ filters, preset: '' });
  };
  $('active-filters').onclick = e => { const remove = e.target.closest('[data-remove]')?.dataset.remove; if (remove !== undefined) { update({ filters: state.filters.filter((_, i) => i !== Number(remove)), preset: '' }); return; } const index = e.target.closest('[data-index]')?.dataset.index; if (index !== undefined) openFilter(state.filters[Number(index)].field, Number(index)); };
  $('reset').onclick = $('empty-reset').onclick = reset;
  $('presets').onclick = e => {
    const preset = e.target.closest('[data-preset]')?.dataset.preset; if (!preset) return;
    const sets = { all: [], dividend: [{ field: 'div_yield', op: 'gte', value: 1 }, { field: 'div_years', op: 'gte', value: 5 }], cash: [{ field: 'fcf', op: 'gt', value: 0 }, { field: 'fcf_change', op: 'gt', value: 0 }], quality: [{ field: 'net_margin', op: 'gte', value: 15 }, { field: 'pe', op: 'between', value: 0, value2: 25 }], watch: [] };
    update({ filters: sets[preset], preset, watchOnly: preset === 'watch' });
  };
  $('columns').onclick = () => { renderColumns(); $('columns-dialog').showModal(); };
  $('column-search').oninput = renderColumns;
  $('column-options').onchange = e => { const key = e.target.dataset.column; if (!key) return; state.columns = e.target.checked ? [...state.columns, key] : state.columns.filter(k => k !== key); markEdited(); renderColumns(); renderControls(); renderTable(); persist('atlas.last.v1', state); };
  $('column-order').onclick = e => {
    const button = e.target.closest('button'); if (!button) return;
    if (button.dataset.hide) state.columns = state.columns.filter(k => k !== button.dataset.hide);
    else { const index = Number(button.dataset.move), to = index + Number(button.dataset.step); if (to < 0 || to >= state.columns.length) return; [state.columns[index], state.columns[to]] = [state.columns[to], state.columns[index]]; }
    markEdited(); renderColumns(); renderControls(); renderTable(); persist('atlas.last.v1', state);
  };
  $('default-columns').onclick = () => { state.columns = [...defaults]; markEdited(); renderColumns(); renderControls(); renderTable(); persist('atlas.last.v1', state); };
  $('save-view').onclick = () => { $('view-name').value = state.view; $('delete-view').hidden = !state.view; $('save-dialog').showModal(); };
  $('save-form').onsubmit = e => { e.preventDefault(); const name = $('view-name').value.trim(); if (!name) return; state.view = name; saved[name] = JSON.parse(JSON.stringify(state)); persist('atlas.views.v1', saved); persist('atlas.last.v1', state); renderSaved(); $('unsaved').hidden = true; $('save-dialog').close(); toast('Screen saved in this browser'); };
  $('delete-view').onclick = () => { delete saved[state.view]; state.view = ''; persist('atlas.views.v1', saved); renderSaved(); $('save-dialog').close(); toast('Saved screen removed'); };
  $('saved-views').onchange = e => { const name = e.target.value; if (name && saved[name]) { Object.assign(state, { mainOnly: true, section:'table', quadrant:{...quadrantDefaults} }, saved[name], { page: 0, view: name }); $('page-size').value = state.pageSize; } else Object.assign(state, { view: '', columns: [...defaults], filters: [], search: '', regions: [], preset: 'all', watchOnly: false, mainOnly: true, sort: 'market_cap', direction: 'desc', page: 0, section:'table', quadrant:{...quadrantDefaults} }); showSection(state.section,false); $('unsaved').hidden = true; renderControls(); loadRows(); persist('atlas.last.v1', state); };
  $('export').onclick = async () => { $('export-title').textContent='Export matching companies';$('export-description').textContent='All matching rows, your selected columns and reporting periods. Numbers use raw USD values.';$('download-csv').download='atlas-stocks-usd.csv';const p = params(); p.set('columns', [...new Set([...state.columns, ...(state.section === 'quadrant' ? [state.quadrant.x,state.quadrant.y] : [])])].join(',')); if (isPublished || [...state.columns,state.sort,...state.filters.map(f=>f.field),...(state.section==='quadrant'?[state.quadrant.x,state.quadrant.y]:[])].some(k=>k.startsWith('sma_custom'))) {
      $('export-dialog').showModal(); $('export-summary').textContent = 'Preparing your CSV…';
      $('download-csv').hidden = true; $('copy-csv').disabled = true;
      try {
        const prepared = await exportSnapshot(p);
        $('export-summary').textContent = `${prepared.count.toLocaleString('en-GB')} matching ${prepared.count === 1 ? 'listing' : 'listings'} ready to export.`;
        $('download-csv').href = prepared.url; $('download-csv').hidden = false; $('copy-csv').disabled = false;
        $('copy-csv').onclick = async () => { try { await navigator.clipboard.writeText(prepared.csv); toast('CSV copied to clipboard'); } catch { toast('Clipboard access unavailable. Use Download CSV.'); } };
        $('export-dialog').onclose = prepared.dispose;
      } catch (error) { $('export-summary').textContent = error.message; }
      return;
    } const a = document.createElement('a'); a.href = '/api/export?' + p; a.download = 'atlas-stocks-usd.csv'; a.click(); toast('Exporting all matching rows with raw numeric USD values'); };
  $('refresh').onclick = async () => { if (isPublished) { await reloadSnapshot(); await Promise.all([loadRows(), loadStatus()]); toast('Latest published snapshot loaded'); return; } await api('/api/refresh', { method: 'POST' }); toast('Quote and FX refresh queued'); setTimeout(loadStatus, 800); };
  $('data-button').onclick = $('method-button').onclick = () => { renderCoverage(); $('data-dialog').showModal(); };
  $('include-other').onchange = e => update({ includeOther: e.target.checked });
  $('moving-averages').onclick=()=>{$('sma-window').value=state.sma.window;$('sma-interval').value=state.sma.interval;$('sma-window').max=state.sma.interval==='daily'?500:260;$('sma-dialog').showModal();};
  $('sma-interval').onchange=()=>{$('sma-window').max=$('sma-interval').value==='daily'?500:260;};
  $('sma-form').onsubmit=e=>{e.preventDefault();state.sma={window:Number($('sma-window').value),interval:$('sma-interval').value};$('sma-setting-note').textContent=`Custom SMA: ${state.sma.window} ${state.sma.interval==='daily'?'days':'weeks'}`;$('sma-dialog').close();update({sma:state.sma});if(selectedSymbol)openDetail(selectedSymbol);};
  $('main-only').onchange = e => { update({ mainOnly: e.target.checked }); loadStatus(); };
  $('page-size').onchange = e => update({ pageSize: Number(e.target.value) });
  const goPage = p => { state.page = p; loadRows(); $('table-scroll').scrollTop = 0; };
  $('first-page').onclick = () => goPage(0); $('prev-page').onclick = () => goPage(Math.max(0, state.page - 1)); $('next-page').onclick = () => goPage(state.page + 1); $('last-page').onclick = () => goPage(Math.max(0, Math.ceil(total / state.pageSize) - 1));
  document.addEventListener('click', e => { const close = e.target.closest('[data-close]')?.dataset.close; if (close) $(close).close(); const tab = e.target.closest('[data-div-tab]')?.dataset.divTab; if (tab) { dividendTab = tab; renderDividends(); } const currency = e.target.closest('[data-div-currency]')?.dataset.divCurrency; if (currency) { dividendCurrency = currency; renderDividends(); } });
  document.addEventListener('error', e => { if (e.target.classList?.contains('company-logo')) e.target.hidden = true; }, true);
  document.addEventListener('keydown', e => { if (e.key === '/' && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName) && !document.querySelector('dialog[open]')) { e.preventDefault(); $('search').focus(); } if (e.key === 'Escape' && !document.querySelector('dialog[open]')) { $('detail').hidden = true; selectedSymbol = null; } });
}
async function boot() {
  schema = await api('/api/schema').then(r => r.json()); fields = Object.fromEntries(schema.columns.map(f => [f.key, f])); fields.symbol = { key: 'symbol', kind: 'text', label: 'Company' }; defaults = schema.columns.filter(f => f.default).map(f => f.key);
  const last = readStorage('atlas.last.v1', null); if (last) Object.assign(state, last, { page: 0 });
  state.sma={window:200,interval:'daily',...state.sma};
  if(!['daily','weekly'].includes(state.sma.interval)||!Number.isInteger(state.sma.window)||state.sma.window<2||state.sma.window>(state.sma.interval==='daily'?500:260))state.sma={window:200,interval:'daily'};
  if(!readStorage('atlas.listing-policy.v4',false)){state.mainOnly=true;persist('atlas.listing-policy.v4',true);}
  $('sma-setting-note').textContent=`Custom SMA: ${state.sma.window} ${state.sma.interval==='daily'?'days':'weeks'}`;
  state.columns = state.columns.length ? state.columns.filter(k => fields[k]) : [...defaults];
  state.filters = state.filters.filter(f => fields[f.field]);
  state.regions = state.regions.filter(r => FLAGS[r]); if (!fields[state.sort]) state.sort = 'market_cap';
  quadrant = createQuadrant($('quadrant-view'), { fields, settings:state.quadrant, loadData: settings => chartRows(params(),settings.x,settings.y), format:fmt, onDetail:openDetail, onExport:exportRanked, onChange:settings=>{state.quadrant=settings;markEdited();persist('atlas.last.v1',state);} });
  state.quadrant = quadrant.settings(); showSection(state.section,false);
  $('page-size').value = state.pageSize; renderSaved(); bindEvents(); renderControls(); await Promise.all([loadRows(), loadStatus()]);
  refreshTimer = setInterval(async () => { if (document.hidden) return; await loadStatus(); await loadRows(true); if (selectedSymbol && !$('dividend-dialog').open) openDetail(selectedSymbol); if ($('dividend-dialog').open && dividendSymbol) { const row = await api('/api/stock/' + encodeURIComponent(dividendSymbol)).then(r => r.json()); if (row.financial_fetched !== dividendRow?.financial_fetched) { dividendRow = row; renderDividends(); } } }, isPublished ? 60000 : 12000);
  const context = document.modelContext;
  if (context?.registerTool) {
    const lifecycle = new AbortController();
    Promise.resolve(context.registerTool({ name: 'set_stock_screen', title: 'Set stock screen', description: 'Update the visible stock screener search, market selection and numeric sorting.', inputSchema: { type: 'object', properties: { search: { type: 'string' }, regions: { type: 'array', items: { enum: Object.keys(FLAGS) } }, sort: { enum: schema.columns.map(c => c.key) }, direction: { enum: ['asc', 'desc'] } }, additionalProperties: false }, annotations: { readOnlyHint: false, untrustedContentHint: false }, async execute(input) { if (!input || typeof input !== 'object' || Object.keys(input).some(k => !['search','regions','sort','direction'].includes(k)) || (input.regions && (!Array.isArray(input.regions) || input.regions.some(r => !FLAGS[r]))) || (input.sort && !fields[input.sort]) || (input.direction && !['asc','desc'].includes(input.direction)) || (input.search !== undefined && typeof input.search !== 'string')) throw new Error('Invalid screen settings'); Object.assign(state, input, { page: 0 }); markEdited(); renderControls(); await loadRows(); return { matches: total, symbols: rows.slice(0, 10).map(r => r.symbol) }; } }, { signal: lifecycle.signal })).catch(() => {});
    window.addEventListener('pagehide', () => lifecycle.abort(), { once: true });
  }
}
boot().catch(error => { $('refresh-status').textContent = isPublished ? 'Could not load published data' : 'Could not connect to the local service'; toast(error.message); });
