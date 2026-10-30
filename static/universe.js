export const universeMode = document.querySelector('meta[name="atlas-universe"]')?.content || 'stock';
export const isETF = universeMode === 'etf';
export const isTransatlantic = universeMode === 'transatlantic';
export const dataBase = document.querySelector('meta[name="atlas-data-path"]')?.content || 'data/';
export const storageKey = key => isETF ? key.replace('atlas.', 'atlas.etfs.') : (isTransatlantic ? key.replace('atlas.', 'atlas.transatlantic.') : key);
export const defaultFilters = () => [];
export const defaultSort = isETF ? 'aum' : (isTransatlantic ? 'rank' : 'market_cap');
export const fundQuadrantDefaults = { x:'aum', y:'williams_r', xPrefer:'higher', yPrefer:'lower', xScale:'symlog', yScale:'linear' };
export const transatlanticQuadrantDefaults = { x:'net_income', y:'williams_r', xPrefer:'higher', yPrefer:'lower', xScale:'symlog', yScale:'linear', split:'median', xCut:1000000000, yCut:-80, fit:true, zone:'all', pareto:true, frontierOnly:false, order:'balanced', viewport:null };
