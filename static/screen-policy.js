export const largeCapFilter = () => ({field:'market_cap',op:'gte',value:20000000000});
export function migrateLargeCapScreen(state) {
  if (state.largeCapPolicy === 1) return state;
  state.filters ||= [];
  if (!state.filters.some(f=>f.field==='market_cap'&&['gte','gt','between'].includes(f.op)&&Number(f.value)>=20000000000)) state.filters.push(largeCapFilter());
  if (state.columns?.length && !state.columns.includes('williams_r')) state.columns.push('williams_r');
  state.largeCapPolicy = 1;
  return state;
}
