import assert from 'node:assert/strict';
import { gzipSync } from 'node:zlib';
import { snapshotJSON } from '../static/snapshot-json.js';

const history={symbol:'TEST',statement_history:{income_annual:{currency:'KRW',periods:[{end_date:'2025-12-31',values:{'Net Income':499e9}}]}},dividend_events:[{date:'1980-01-02',amount:0.123}]};
const plain=JSON.stringify(history);
assert.deepEqual(await snapshotJSON(new Response(plain)),history);
assert.deepEqual(await snapshotJSON(new Response(gzipSync(plain)),true),history);
assert.deepEqual(await snapshotJSON(new Response(plain),true),history);
await assert.rejects(snapshotJSON(new Response('Missing',{status:404}),true),/unavailable/);
console.log('Compressed histories, CDN decoding and missing-file checks passed');
