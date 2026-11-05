const CACHE = 'uk-activity-radar-shell-v1.5.0-r40';
const SHELL = ['/', '/about', '/terms', '/privacy', '/data-licences', '/static/styles.css?v=15139', '/static/refinements.css?v=15139', '/static/app.js?v=15139', '/static/assets/aircraft/chinook-raf.jpg', '/static/assets/aircraft/apache-army.jpg', '/static/assets/aircraft/kc135-usaf.jpg', '/static/assets/aircraft/typhoon-raf.jpg', '/static/assets/aircraft/a400m-raf.jpg', '/static/assets/aircraft/c17-raf.jpg', '/static/assets/aircraft/s92-coastguard.jpg', '/static/share.html', '/static/share.css', '/static/share.js', '/static/legal.css', '/static/manifest.webmanifest', '/static/icon-192.png', '/static/icon-512.png'];
const NETWORK_FIRST_ASSETS = new Set(['/static/styles.css', '/static/refinements.css', '/static/movement-history.css', '/static/app.js', '/static/movement-history.js']);
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).catch(() => null));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.pathname.startsWith('/api/') || url.pathname === '/ws') return;
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/static/')) {
    if (NETWORK_FIRST_ASSETS.has(url.pathname)) {
      event.respondWith(fetch(event.request).then(response => {
        if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
        return response;
      }).catch(() => caches.match(event.request)));
      return;
    }
    event.respondWith(caches.match(event.request).then(hit => {
      const refresh = fetch(event.request).then(response => {
        if (response.ok) caches.open(CACHE).then(cache => cache.put(event.request, response.clone()));
        return response;
      });
      return hit || refresh;
    }));
    return;
  }
  event.respondWith(fetch(event.request).then(response => {
    const copy = response.clone(); caches.open(CACHE).then(cache => cache.put(event.request, copy)); return response;
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
