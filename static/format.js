export function parseNumber(input) {
  const text = String(input).trim().replace(/[$,%\s]/g, '');
  const match = text.match(/^([+-]?(?:\d+(?:\.\d*)?|\.\d+))([kmbt])?$/i);
  if (!match) return null;
  const scale = { k: 1e3, m: 1e6, b: 1e9, t: 1e12 }[match[2]?.toLowerCase()] || 1;
  const n = Number(match[1]) * scale;
  return Number.isFinite(n) ? n : null;
}
export function compact(value, decimals = 2) {
  const n = Number(value), a = Math.abs(n);
  const unit = a >= 1e12 ? [1e12, 'T'] : a >= 1e9 ? [1e9, 'B'] : a >= 1e6 ? [1e6, 'M'] : a >= 1e3 ? [1e3, 'K'] : [1, ''];
  return { value: (n / unit[0]).toLocaleString('en-GB', { maximumFractionDigits: decimals, minimumFractionDigits: unit[1] ? decimals : 0 }), unit: unit[1] };
}
export function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
export function annualDividends(events, currentYear = new Date().getFullYear()) {
  if (!events?.length) return [];
  const start = Math.min(...events.map(e => Number(e.date.slice(0, 4))));
  const totals = new Map();
  for (const e of events) {
    const y = Number(e.date.slice(0, 4));
    const entry = totals.get(y) || { total: 0, count: 0 };
    entry.total += e.amount; entry.count++; totals.set(y, entry);
  }
  return Array.from({ length: currentYear - start + 1 }, (_, i) => {
    const year = start + i, entry = totals.get(year) || { total: 0, count: 0 }, prev = totals.get(year - 1);
    return { year, ...entry, growth: year < currentYear && prev?.total > 0 ? (entry.total / prev.total - 1) * 100 : null, incomplete: year === currentYear };
  });
}
