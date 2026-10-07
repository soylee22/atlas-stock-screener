// Serve an in-memory CSV as a normal same-origin file download.
// No screens, watchlists or exports are saved in persistent storage.
const exports = new Map();
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  const { token, csv, content, filename, mime } = event.data || {};
  const body=typeof content==='string'?content:csv;
  if (typeof token !== 'string' || typeof body !== 'string') return;
  const type=['application/json','text/csv','text/plain'].includes(mime)?mime:'text/csv';
  const name=typeof filename==='string'&&/^[a-zA-Z0-9_.-]{1,120}$/.test(filename)?filename:'atlas-stocks-usd.csv';
  exports.set(token, {body,filename:name,mime:type});
  setTimeout(() => exports.delete(token), 300000);
  event.ports[0]?.postMessage({ ready: true });
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || !['atlas-export.csv','atlas-download'].some(path=>url.pathname===new URL(path,self.registration.scope).pathname)) return;
  const token = url.searchParams.get('token');
  const file = exports.get(token);
  event.respondWith(Promise.resolve(file == null
    ? new Response('Export expired. Please use Export again.', { status: 410 })
    : new Response((file.mime==='text/csv'?'\ufeff':'') + file.body, { headers: {
      'Content-Type': file.mime+'; charset=utf-8',
      'Content-Disposition': 'attachment; filename="'+file.filename+'"',
      'Cache-Control': 'no-store',
    } })));
});
