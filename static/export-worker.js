// Serve an in-memory CSV as a normal same-origin file download.
// No screens, watchlists or exports are saved in persistent storage.
const exports = new Map();
self.addEventListener('install', event => event.waitUntil(self.skipWaiting()));
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
self.addEventListener('message', event => {
  const { token, csv } = event.data || {};
  if (typeof token !== 'string' || typeof csv !== 'string') return;
  exports.set(token, csv);
  setTimeout(() => exports.delete(token), 300000);
  event.ports[0]?.postMessage({ ready: true });
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname !== new URL('atlas-export.csv', self.registration.scope).pathname) return;
  const token = url.searchParams.get('token');
  const csv = exports.get(token);
  event.respondWith(Promise.resolve(csv == null
    ? new Response('Export expired. Please use Export again.', { status: 410 })
    : new Response('\ufeff' + csv, { headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': 'attachment; filename="atlas-stocks-usd.csv"',
      'Cache-Control': 'no-store',
    } })));
});
