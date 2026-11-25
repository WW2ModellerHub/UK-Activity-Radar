const CACHE = 'uk-activity-radar-shell-v1.5.0-r180';
const SHELL = ['/', '/about', '/terms', '/privacy', '/data-licences', '/static/map-markers.css?v=16393', '/static/app.css?v=16401', '/static/motion-engine.js?v=16398', '/static/renderer-loader.js?v=16382', '/static/live-object-renderer.js?v=16382', '/static/vendor/maplibre/maplibre-gl.css?v=6.17.0', '/static/vendor/maplibre/maplibre-gl.mjs', '/static/app.js?v=16403', '/static/share.html', '/static/share.css', '/static/share.js', '/static/legal.css?v=16375', '/static/manifest.webmanifest', '/static/icon-192.png', '/static/icon-512.png', '/static/brand/logo.svg'];
const NETWORK_FIRST_ASSETS = new Set(['/static/app.css', '/static/map-markers.css', '/static/app.js', '/static/movement-history.js', '/static/motion-engine.js', '/static/live-object-renderer.js']);
self.addEventListener('install', event => {
  // One file failing (the server mid-restart) must not leave nothing saved.
  event.waitUntil(caches.open(CACHE).then(cache => Promise.allSettled(SHELL.map(item => cache.add(item)))).catch(() => null));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  // Keep an older copy of any file this version could not fetch, then drop
  // the old caches.
  event.waitUntil(caches.open(CACHE).then(async current => {
    for (const key of (await caches.keys()).filter(k => k !== CACHE)) {
      const old = await caches.open(key);
      for (const request of await old.keys()) {
        if (!(await current.match(request, { ignoreSearch: true }))) {
          const response = await old.match(request);
          if (response) await current.put(request, response);
        }
      }
      await caches.delete(key);
    }
  }).catch(() => null));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname === '/ws') return;
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/static/')) {
    if (NETWORK_FIRST_ASSETS.has(url.pathname)) {
      // While the server restarts the new version may 502: use the copy
      // saved for this version, else the last version saved, rather than
      // drawing the map with no styles.
      const saved = () => caches.match(event.request).then(hit => hit || caches.match(event.request, { ignoreSearch: true }));
      event.respondWith(fetch(event.request).then(response => {
        // Copy before handing it over: once the page reads the body, it cannot be copied.
        if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response; }
        return saved().then(hit => hit || response);
      }).catch(() => saved()));
      return;
    }
    event.respondWith(caches.match(event.request).then(hit => {
      const refresh = fetch(event.request).then(response => {
        if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); }
        return response;
      });
      return hit || refresh;
    }));
    return;
  }
  // Pages: never save an error page (a 502 while the server restarts) as
  // the copy to show offline.
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response; }
    return caches.match(event.request).then(hit => hit || response);
  }).catch(() => caches.match(event.request).then(hit => hit || caches.match('/'))));
});
self.addEventListener('push', event => {
  let payload={title:'UK Activity Radar',body:'There is a new travel update.',url:'/',tag:'radar-travel'};
  try { payload={...payload,...event.data.json()}; } catch {}
  event.waitUntil(self.registration.showNotification(payload.title, {
    body:payload.body, icon:'/static/icon-192.png', badge:'/static/icon-192.png',
    tag:payload.tag, renotify:false, data:{url:payload.url || '/'},
  }));
});
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target=event.notification.data?.url || '/';
  event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{
    const existing=windows.find(client=>new URL(client.url).origin===self.location.origin);
    if(existing){existing.navigate(target);return existing.focus();}
    return clients.openWindow(target);
  }));
});
