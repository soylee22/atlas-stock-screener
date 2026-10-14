import assert from 'node:assert/strict';
import { qualityPreset, withValuation, usesValuation, qualitySourceKeys } from '../static/dividend-quality.js';
import { selectSnapshot, snapshotCSV, chartCoverage } from '../static/snapshot-engine.js';

const keys=['market_cap','div_fiscal_growth_5y','fcf_growth_5y','fcf_payout','roic_proxy','current_ratio','fcf_div_growth_gap_5y','div_yield','fcf_yield','margin_of_safety','fair_value'];
const schema={regions:{us:'United States'},columns:[...keys.map(key=>({key,kind:'number',group:'Dividend quality'})),{key:'sector',kind:'text'}]};
const row={symbol:'GOOD',name:'Example',sector:'Technology',region_code:'us',instrument:'stock',active:true,main_listing:true,
 market_cap:30e9,div_fiscal_growth_5y:8,fcf_growth_5y:10,fcf_payout:50,roic_proxy:15,current_ratio:1.2,fcf_div_growth_gap_5y:2,
 div_yield:.5,fcf_yield:5,price:90,quote_time:'2026-10-14T12:00:00Z',statement_version:1};
const rules=qualityPreset();
const params=new URLSearchParams({filters:JSON.stringify(rules),sort:'market_cap'});
assert.deepEqual(selectSnapshot([row,{...row,symbol:'BANK',sector:'Financial Services'},{...row,symbol:'MISSING',fcf_growth_5y:null},{...row,symbol:'OVER',fcf_payout:60},{...row,symbol:'GAP',fcf_div_growth_gap_5y:-.1}],schema,params).map(r=>r.symbol),['GOOD']);
assert.equal(rules.some(r=>r.field==='div_yield'),false);
assert.equal(rules.some(r=>r.field==='fcf_yield'),false);
assert.equal(rules.some(r=>r.field==='margin_of_safety'),false);
assert.ok(qualityPreset({horizon:3}).some(r=>r.field==='fcf_growth_3y'));
assert.throws(()=>qualityPreset({horizon:2}),/horizon/);
assert.throws(()=>qualityPreset({yieldMin:4,yieldMax:2}),/range/);
const entered={GOOD:{value:100,date:'2026-10-13',source:'My DCF'}};
const valued=withValuation(row,entered);
assert.ok(Math.abs(valued.margin_of_safety-10)<1e-10);
assert.equal(valued.fair_value_period,'2026-10-13');
assert.equal(withValuation(row,{}).margin_of_safety,null);
assert.equal(withValuation(row,{GOOD:{value:0,date:'2026-10-13'}}).margin_of_safety,null);
assert.equal(withValuation({...row,price:null},entered).margin_of_safety,null);
assert.equal(withValuation({...row,price:120},entered).margin_of_safety,-20);
assert.equal(usesValuation(new URLSearchParams({filters:JSON.stringify(qualityPreset({marginGate:true}))})),true);
assert.equal(usesValuation(params),false);
assert.ok(qualitySourceKeys('fcf_payout').includes('fcf_payout_basis'));
const csv=snapshotCSV([{...valued,fcf_payout_period:'TTM 2026-06-30',dividend_quality_fetched:'2026-10-13',fcf_payout_basis:'Common dividends'}],schema,'fcf_payout,margin_of_safety');
assert.ok(csv.includes('fcf_payout_period'));
assert.ok(csv.includes('fair_value_source'));
assert.ok(csv.includes('My DCF'));
assert.ok(csv.includes('TTM 2026-06-30'));
const fields=Object.fromEntries(schema.columns.map(f=>[f.key,f]));
assert.deepEqual(chartCoverage([{...row,fcf_growth_5y:null},{...row,statement_version:0,fcf_growth_5y:null}],'market_cap','fcf_growth_5y',fields),{awaiting:1,unavailable:1});
assert.deepEqual(chartCoverage([{...row,sector:'Financial Services',statement_version:0,fcf_growth_5y:null}],'market_cap','fcf_growth_5y',fields),{awaiting:0,unavailable:1});
console.log('Dividend quality preset, optional gates, missing history, valuation and dated export checks passed');
