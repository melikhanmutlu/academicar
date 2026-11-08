// Presenter-mode service worker (served at /present-sw.js, registered with the
// presenter page as its scope by static/js/presenter.js). It only controls
// presenter pages, never the rest of the site.
//
// Pages: network first (4 s), then the saved copy, so a slow congress Wi-Fi
// cannot hold up the talk. Everything else the page loads (PDF, pdf.js,
// model-viewer, the Draco decoder, GLBs): the saved copy first, refreshed in
// the background when online.
const CACHE = 'academicar-present-v1';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()));

function storable(request, response) {
  return request.method === 'GET' && response && response.status === 200 &&
    (response.type === 'basic' || response.type === 'cors');
}

async function fromNetwork(request, cache) {
  const response = await fetch(request);
  if (storable(request, response)) await cache.put(request, response.clone());
  return response;
}

async function pageFirst(request) {
  const cache = await caches.open(CACHE);
  const network = fromNetwork(request, cache);
  const timeout = new Promise((resolve) => setTimeout(resolve, 4000));
  try {
    const response = await Promise.race([network, timeout]);
    if (response) return response;
  } catch (error) { /* offline: fall back to the saved copy */ }
  const saved = await cache.match(request, { ignoreVary: true });
  return saved || network;
}

async function savedFirst(event) {
  const cache = await caches.open(CACHE);
  const saved = await cache.match(event.request, { ignoreVary: true });
  if (saved) {
    event.waitUntil(fromNetwork(event.request, cache).catch(() => {}));
    return saved;
  }
  return fromNetwork(event.request, cache);
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET' || request.headers.has('range')) return;
  if (request.mode === 'navigate') event.respondWith(pageFirst(request));
  else event.respondWith(savedFirst(event));
});
