self.addEventListener('install', e => {
  e.waitUntil(caches.open('betbot-v2').then(cache => cache.addAll([
    './index.html', './manifest.webmanifest', './icon-192.png', './icon-512.png'
  ])));
  self.skipWaiting();
});
self.addEventListener('activate', e => self.clients.claim());
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  // Always try network first for picks data so the app shows fresh picks.
  if (e.request.url.includes('picks.json')) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).catch(() => caches.match('./index.html'))));
});
