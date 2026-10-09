export const isETF = document.querySelector('meta[name="atlas-universe"]')?.content === 'etf';
export const dataBase = document.querySelector('meta[name="atlas-data-path"]')?.content || 'data/';
export const storageKey = key => isETF ? key.replace('atlas.', 'atlas.etfs.') : key;
export const defaultFilters = () => isETF ? [] : [{field:'market_cap',op:'gte',value:20e9}];
export const defaultSort = isETF ? 'aum' : 'market_cap';
export const fundQuadrantDefaults = { x:'aum', y:'williams_r', xPrefer:'higher', yPrefer:'lower', xScale:'symlog', yScale:'linear' };
