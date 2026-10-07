import { decodeSnapshot, selectSnapshot, snapshotCSV, chartCoverage } from './snapshot-engine.js';

export const isPublished = document.querySelector('meta[name="atlas-data-mode"]')?.content === 'snapshot';
let loaded, checkedAt = 0;
const details = new Map();
async function snapshot() {
  if (!loaded) loaded = Promise.all(['schema', 'status', 'stocks'].map(async name => {
    const response = await fetch(new URL(`data/${name}.json`, document.baseURI), { cache: 'no-cache' });
    if (!response.ok) throw new Error('Published market data could not be loaded');
    return response.json();
  })).then(([schema, status, data]) => {
    if (data.built !== status.snapshot.built) throw new Error('Snapshot is being updated. Please retry shortly.');
    checkedAt = Date.now(); return { schema, status, rows: decodeSnapshot(data) };
  }).catch(error => { loaded = undefined; throw error; });
  return loaded;
}
export async function reloadSnapshot() { loaded = undefined; details.clear(); return snapshot(); }
export async function chartRows(params, x, y) {
  const query = new URLSearchParams(params); query.set('x', x); query.set('y', y);
  if (isPublished) {
    const data = await snapshot();
    const numeric = new Set(data.schema.columns.filter(f => f.kind !== 'text').map(f => f.key));
    if (!numeric.has(x) || !numeric.has(y)) throw new Error('Choose numeric chart metrics');
    const selected = selectSnapshot(data.rows, data.schema, query);
    const rows = selected.filter(r => typeof r[x] === 'number' && Number.isFinite(r[x]) && typeof r[y] === 'number' && Number.isFinite(r[y]));
    return { rows, total: selected.length, coverage: chartCoverage(selected, x, y, Object.fromEntries(data.schema.columns.map(f => [f.key, f]))) };
  }
  const response = await fetch('/api/chart?' + query);
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Chart data unavailable');
  return data;
}
export async function analysisUniverse(symbol) {
  if (isPublished) {
    const data = await snapshot();
    return { rows:data.rows, schema:data.schema, status:data.status };
  }
  const response = await fetch('/api/analysis-peers/'+encodeURIComponent(symbol));
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail||'Peer data unavailable');
  return data;
}

export async function prepareTextDownload(content,filename,mime) {
  if ('serviceWorker' in navigator) try {
    const registration=await navigator.serviceWorker.register(new URL('export-worker.js',document.baseURI));
    const incoming=registration.installing||registration.waiting;
    if(incoming&&incoming.state!=='activated')await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Download service update timed out')),10000);
      incoming.addEventListener('statechange',()=>{
        if(incoming.state==='activated'){clearTimeout(timer);resolve();}
        if(incoming.state==='redundant'){clearTimeout(timer);reject(new Error('Download service update failed'));}
      });
    });
    await navigator.serviceWorker.ready;
    if(!navigator.serviceWorker.controller)await new Promise((resolve,reject)=>{
      const timeout=setTimeout(()=>reject(new Error('Download service did not start')),10000);
      navigator.serviceWorker.addEventListener('controllerchange',()=>{clearTimeout(timeout);resolve();},{once:true});
    });
    const token=crypto.randomUUID(),channel=new MessageChannel();
    await new Promise((resolve,reject)=>{const timeout=setTimeout(()=>reject(new Error('Download preparation failed')),10000);
      channel.port1.onmessage=()=>{clearTimeout(timeout);channel.port1.close();resolve();};
      registration.active.postMessage({token,content,filename,mime},[channel.port2]);
    });
    return {url:new URL(`atlas-download?token=${token}`,document.baseURI).href,dispose(){}};
  } catch { /* The clipboard, preview and ordinary file download remain available. */ }
  const url=URL.createObjectURL(new Blob([content],{type:mime+';charset=utf-8'}));
  return {url,dispose(){URL.revokeObjectURL(url);}};
}
export async function api(input, options) {
  if (!isPublished) return fetch(input, options);
  try {
    const data = await snapshot(), url = new URL(input, 'https://atlas.invalid');
    let result;
    if (url.pathname === '/api/schema') result = data.schema;
    else if (url.pathname === '/api/status') {
      if (Date.now() - checkedAt >= 60000) {
        checkedAt = Date.now();
        const response = await fetch(new URL('data/status.json', document.baseURI), { cache: 'no-cache' });
        if (!response.ok) throw new Error('Published data unavailable');
        const latest = await response.json();
        if (latest.snapshot.built !== data.status.snapshot.built) result = (await reloadSnapshot()).status;
      }
      result ||= data.status;
    }
    else if (url.pathname === '/api/stocks') {
      const rows = selectSnapshot(data.rows, data.schema, url.searchParams);
      const offset = Math.max(0, Number(url.searchParams.get('offset')) || 0), limit = Math.min(250, Number(url.searchParams.get('limit')) || 100);
      result = { rows: rows.slice(offset, offset + limit), total: rows.length, offset };
    } else if (url.pathname.startsWith('/api/stock/')) {
      const symbol = decodeURIComponent(url.pathname.slice('/api/stock/'.length));
      const row = data.rows.find(r => r.symbol === symbol);
      if (!row) return Response.json({ detail: 'Unknown symbol' }, { status: 404 });
      if (!row.detail_key) result = row;
      else {
        if (!details.has(symbol)) details.set(symbol, fetch(new URL(`data/details/${row.detail_key}.json`, document.baseURI)).then(async r => {
          if (!r.ok) throw new Error('Company snapshot unavailable');
          return r.json();
        }).catch(error => { details.delete(symbol); throw error; }));
        result = await details.get(symbol);
      }
    } else if (url.pathname === '/api/enrich' || url.pathname === '/api/refresh') {
      await reloadSnapshot(); result = { snapshot: true };
    } else return Response.json({ detail: 'Unknown data request' }, { status: 404 });
    return Response.json(result);
  } catch (error) { return Response.json({ detail: error.message }, { status: 400 }); }
}

export async function exportSnapshot(params) {
  const data = await snapshot();
  const selected = selectSnapshot(data.rows, data.schema, params);
  const csv = snapshotCSV(selected, data.schema, params.get('columns') || '');
  if ('serviceWorker' in navigator) try {
    const registration = await navigator.serviceWorker.register(new URL('export-worker.js', document.baseURI));
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Download service did not start. Please retry.')), 10000);
      navigator.serviceWorker.addEventListener('controllerchange', () => { clearTimeout(timeout); resolve(); }, { once: true });
    });
    const token = crypto.randomUUID(), channel = new MessageChannel();
    await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error('Download could not be prepared. Please retry.')), 10000);
      channel.port1.onmessage = () => { clearTimeout(timeout); channel.port1.close(); resolve(); };
      registration.active.postMessage({ token, csv }, [channel.port2]);
    });
    return { csv, count: selected.length, url: new URL(`atlas-export.csv?token=${token}`, document.baseURI).href, dispose() {} };
  }
  catch { /* Copy CSV and the ordinary download remain available. */ }
  const url = URL.createObjectURL(new Blob(['\ufeff', csv], { type: 'text/csv;charset=utf-8' }));
  return { csv, count: selected.length, url, dispose() { URL.revokeObjectURL(url); } };
}
