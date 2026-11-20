const CACHE = "medqa-a-v1";
const ASSETS = ["/", "/manifest.json", "/icon.png"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)).catch(() => null));
  self.skipWaiting();
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener("fetch", e => {
  if (e.request.method !== "GET" || e.request.url.includes("/ask")) return;
  e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
});
