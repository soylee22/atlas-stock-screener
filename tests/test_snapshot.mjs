import assert from 'node:assert/strict';
import { decodeSnapshot, selectSnapshot, snapshotCSV } from '../static/snapshot-engine.js';
const schema = { regions: { us: 'US', ca: 'Canada' }, columns: [
  { key: 'market_cap', kind: 'usd', default: true }, { key: 'sector', kind: 'text' },
] };
const rows = [
  { symbol: 'BIG', name: 'Big, "Company"', active: true, instrument: 'stock', main_listing: true, region_code: 'us', market_cap: 10e9, sector: 'Technology' },
  { symbol: 'SMALL', name: 'Small', active: true, instrument: 'stock', main_listing: true, region_code: 'ca', market_cap: 100e6, sector: null },
  { symbol: 'UNKNOWN', name: '=Formula', active: true, instrument: 'stock', main_listing: true, region_code: 'us', market_cap: null },
  { symbol: 'BIG.NE', name: 'Big CDR', active: true, instrument: 'stock', main_listing: false, region_code: 'ca', market_cap: 99e9 },
  { symbol: 'FUND', active: true, instrument: 'other', main_listing: false, market_cap: 99e9 },
  { symbol: 'OLD', active: false, instrument: 'stock', main_listing: true, market_cap: 100e9 },
];
const select = options => selectSnapshot(rows, schema, new URLSearchParams(options));
assert.deepEqual(select({}).map(r => r.symbol), ['BIG', 'SMALL', 'UNKNOWN']);
assert.deepEqual(select({ direction: 'asc' }).map(r => r.symbol), ['SMALL', 'BIG', 'UNKNOWN']);
assert.deepEqual(select({ regions: 'ca' }).map(r => r.symbol), ['SMALL']);
assert.equal(select({ main_only: 'false', search: 'cdr' })[0].symbol, 'BIG.NE');
assert.equal(select({ search: 'BIG.NE' }).length, 0);
assert.equal(select({ main_only: 'false', include_other: 'true' }).length, 5);
assert.deepEqual(select({ only_symbols: 'BIG,UNKNOWN' }).map(r => r.symbol), ['BIG', 'UNKNOWN']);
assert.equal(select({ filters: JSON.stringify([{ field: 'market_cap', op: 'between', value: 1e9, value2: 20e9 }]) })[0].symbol, 'BIG');
assert.equal(select({ filters: JSON.stringify([{ field: 'sector', op: 'contains', value: 'TECH' }]) }).length, 1);
assert.equal(select({ filters: JSON.stringify([{ field: 'market_cap', op: 'missing' }]) })[0].symbol, 'UNKNOWN');
for (const rule of [
  { field: 'market_cap', op: 'gte', value: '' },
  { field: 'market_cap', op: 'between', value: -100, value2: null },
  { field: 'market_cap', op: 'between', value: 100, value2: 0 },
  { field: 'unknown', op: 'eq', value: 1 },
]) assert.throws(() => select({ filters: JSON.stringify([rule]) }));
assert.throws(() => select({ sort: 'unknown' }));
assert.throws(() => select({ regions: 'zz' }));
assert.throws(() => select({ direction: 'up' }));
const csv = snapshotCSV(select({}), schema);
assert.ok(csv.includes('10000000000'));
assert.ok(csv.includes('"Big, ""Company"""'));
assert.ok(csv.includes("'=Formula"));
assert.throws(() => snapshotCSV(rows, schema, 'private_note'));
assert.deepEqual(decodeSnapshot({ version: 1, fields: ['symbol'], rows: [['A']] }), [{ symbol: 'A' }]);
assert.throws(() => decodeSnapshot({ version: 9 }));
const growthSchema = { ...schema, columns: [...schema.columns, {key:'revenue_growth_3y',kind:'percent'}, {key:'below_52w_high',kind:'percent'}] };
const growthRows = rows.map((r,i)=>({...r,revenue_growth_3y:i===0?20:i===1?-5:null,below_52w_high:10,revenue_growth_3y_period:'FY 2026 / 2023 · USD · Yahoo'}));
assert.deepEqual(selectSnapshot(growthRows,growthSchema,new URLSearchParams({sort:'revenue_growth_3y',direction:'desc'})).map(r=>r.symbol),['BIG','SMALL','UNKNOWN']);
assert.equal(selectSnapshot(growthRows,growthSchema,new URLSearchParams({filters:JSON.stringify([{field:'revenue_growth_3y',op:'gte',value:0}])}))[0].symbol,'BIG');
const growthCSV=snapshotCSV(growthRows,growthSchema,'revenue_growth_3y,below_52w_high');
assert.ok(growthCSV.includes('revenue_growth_3y_period'));
assert.ok(growthCSV.includes('FY 2026 / 2023 · USD · Yahoo'));
console.log('27 published filtering, numeric sorting and CSV assertions passed');
