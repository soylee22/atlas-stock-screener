export const williamsMetrics = ['williams_r', 'williams_monthly_r'];
export const isWilliamsMetric = key => williamsMetrics.includes(key);
export function williamsSourceKeys(key) {
  if (!isWilliamsMetric(key)) return [];
  const prefix = key === 'williams_r' ? 'williams' : 'williams_monthly';
  return ['r_period','asof','zone','provisional','source_note','reason','version'].map(suffix => prefix + '_' + suffix).concat('technical_calendar');
}
