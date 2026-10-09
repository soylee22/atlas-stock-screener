export function decodeSnapshot(data) {
  if (data.version !== 1 || !Array.isArray(data.fields) || !Array.isArray(data.rows)) throw new Error('Unsupported data snapshot');
  return data.rows.map(values => Object.fromEntries(data.fields.map((field, i) => [field, values[i]])));
}

export function selectSnapshot(rows, schema, params) {
  const fields = Object.fromEntries(schema.columns.map(f => [f.key, f]));
  Object.assign(fields, { symbol: { kind: 'text' }, name: { kind: 'text' }, instrument: { kind: 'text' } });
  const sort = params.get('sort') || 'market_cap', direction = params.get('direction') || 'desc';
  if (!fields[sort] || !['asc', 'desc'].includes(direction)) throw new Error('Invalid sort settings');
  const mainOnly = params.get('main_only') !== 'false', other = params.get('include_other') === 'true';
  const regions = (params.get('regions') || '').split(',').filter(Boolean);
  if (regions.some(r => !schema.regions[r])) throw new Error('Unknown market');
  const symbols = (params.get('only_symbols') || '').split(',').filter(Boolean).slice(0, 1000);
  const search = (params.get('search') || '').toLowerCase();
  const filters = JSON.parse(params.get('filters') || '[]');
  if (!Array.isArray(filters) || filters.length > 40) throw new Error('Use at most 40 filters');
  const checks = filters.map(rule => {
    if (!rule || !fields[rule.field]) throw new Error('Invalid filter column');
    const text = fields[rule.field].kind === 'text';
    const ops = text ? ['eq', 'ne', 'contains', 'not_contains'] : ['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'between'];
    if (!['missing', 'present', ...ops].includes(rule.op)) throw new Error('Invalid filter operation');
    const n = Number(rule.value), n2 = Number(rule.value2);
    if (!text && !['missing', 'present'].includes(rule.op) && (rule.value == null || String(rule.value).trim() === '' || !Number.isFinite(n))) throw new Error('Invalid filter value');
    if (rule.op === 'between' && (rule.value2 == null || String(rule.value2).trim() === '' || !Number.isFinite(n2) || n2 < n)) throw new Error('Invalid filter range');
    return row => {
      const value = row[rule.field];
      if (rule.op === 'missing') return value == null;
      if (rule.op === 'present') return value != null;
      if (value == null) return false;
      if (text) {
        const a = String(value).toLowerCase(), b = String(rule.value ?? '').toLowerCase();
        return ({ eq: () => a === b, ne: () => a !== b, contains: () => a.includes(b), not_contains: () => !a.includes(b) })[rule.op]();
      }
      return ({ eq: () => value === n, ne: () => value !== n, gt: () => value > n, gte: () => value >= n, lt: () => value < n, lte: () => value <= n, between: () => value >= n && value <= n2 })[rule.op]();
    };
  });
  const selected = rows.filter(row => row.active && (!mainOnly || row.main_listing) && (other || row.instrument === 'stock') && (!regions.length || regions.includes(row.region_code)) && (!symbols.length || symbols.includes(row.symbol)) && (!search || (row.symbol + '\n' + row.name).toLowerCase().includes(search)) && checks.every(test => test(row)));
  const textSort = fields[sort].kind === 'text', sign = direction === 'desc' ? -1 : 1;
  return selected.sort((a, b) => {
    const x = a[sort], y = b[sort];
    if (x == null && y != null) return 1;
    if (y == null && x != null) return -1;
    const comparison = x == null ? 0 : textSort ? String(x).localeCompare(String(y), 'en', { sensitivity: 'base' }) : x - y;
    return comparison * sign || a.symbol.localeCompare(b.symbol);
  });
}

export function chartCoverage(rows, x, y, fields) {
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const pending = (row, key) => {
    if (finite(row[key])) return false;
    if(key === 'eps_diluted') return !row.eps_version;
    if(key.startsWith('sma_')) return !row.technical_version;
    if (/^(revenue|net_income)_growth_(1|3|5|10)y$/.test(key)) return !row.annual_growth_version;
    if (/^(roic_proxy|roce)(_(5y_avg|5y_count))?$/.test(key)) return !row.statement_version;
    if (['net_income', 'revenue', 'net_margin'].includes(key)) return !row.financial_fetched && !row.income_fetched;
    if (fields[key]?.group === 'Dividends' && key !== 'div_yield') return !row.dividend_fetched && !row.financial_fetched;
    if ((['Financials', 'Cash flow', 'Dividends'].includes(fields[key]?.group) && key !== 'div_yield') || key === 'beta') return !row.financial_fetched;
    return false;
  };
  const coverage = { awaiting: 0, unavailable: 0 };
  for (const row of rows) {
    if (finite(row[x]) && finite(row[y])) continue;
    coverage[pending(row, x) || pending(row, y) ? 'awaiting' : 'unavailable']++;
  }
  return coverage;
}

export function snapshotCSV(rows, schema, requested = '') {
  const allowed = new Set(['symbol', 'name', 'instrument', ...schema.columns.map(f => f.key)]);
  const columns = requested ? requested.split(',') : schema.columns.filter(f => f.default).map(f => f.key);
  if (columns.some(k => !allowed.has(k))) throw new Error('Invalid export column');
  const keys = [...new Set(['symbol', 'name', ...columns, 'income_period', 'cf_period', 'fcf_growth_period', 'quote_time', 'financial_fetched', ...(columns.includes('eps_diluted')?['eps_diluted_period','eps_currency','eps_fetched']:[]), ...columns.filter(k=>/^(roic_proxy|roce)(_5y_avg)?$/.test(k)).map(k=>k+'_period'), ...(columns.some(k=>/^(roic_proxy|roce)(_5y_avg)?$/.test(k))?['statement_fetched']:[]), ...columns.filter(k=>k.startsWith('sma_')).map(k=>k.replace(/_distance$/,'')+'_period'), ...(columns.some(k=>k.startsWith('sma_'))?['technical_asof']:[]), ...columns.filter(k => /^(revenue|net_income)_growth_(1|3|5|10)y$/.test(k)).map(k => k + '_period'), ...(columns.some(k => /^(revenue|net_income)_growth_(1|3|5|10)y$/.test(k)) ? ['annual_growth_fetched'] : [])])];
  const cell = value => {
    if (value == null) return '';
    let text = String(value);
    if (typeof value === 'string' && /^[=+\-@]/.test(text)) text = "'" + text;
    return /[",\r\n]/.test(text) ? '"' + text.replace(/"/g, '""') + '"' : text;
  };
  return [keys.join(','), ...rows.map(row => keys.map(k => cell(row[k])).join(','))].join('\r\n') + '\r\n';
}
