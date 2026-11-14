// Сервис-воркер: кэширует оболочку приложения, чтобы PWA открывалась мгновенно и без сети.
// Запросы к API всегда идут на сервер (данные и ИИ — только актуальные).
const CACHE = 'ar-inspector-v30-identify-candidate';
const SHELL = ['/', '/index.html', '/styles.css', '/app.js', '/view-geometry.mjs',
  '/js/util.js', '/js/state.js', '/js/net.js', '/js/ui.js', '/js/camera.js', '/js/screen.js', '/js/live.js', '/js/equipment.js', '/js/component.js', '/js/analysis.js', '/js/system.js', '/manifest.webmanifest',
  '/icons/icon.svg', '/icons/icon-192.png', '/icons/icon-512.png'];

SHELL.push('/js/inspection.js', '/js/plan.js');
self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // Сначала сеть (свежая версия), при недоступности сервера — кэш.
  e.respondWith(fetch(e.request, { cache: 'no-cache' }).then((r) => {
    if (r.ok && SHELL.includes(url.pathname)) {
      const copy = r.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
    }
    return r;
  }).catch(() => caches.match(e.request).then((m) => m || caches.match('/'))));
});
