const CACHE_NAME = 'gayathri-cache-v1';
const ASSETS = [
  '/',
  '/manifest.json',
  '/static/css/school_style.css',
  // add other important assets and templates you want cached
];

self.addEventListener('install', (evt) => {
  evt.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (evt) => {
  evt.waitUntil(clients.claim());
});

self.addEventListener('fetch', (evt) => {
  evt.respondWith(
    caches.match(evt.request).then(resp => resp || fetch(evt.request))
  );
});
