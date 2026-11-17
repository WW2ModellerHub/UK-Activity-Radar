const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');

const worker = fs.readFileSync(path.resolve(__dirname, '..', 'app/static/service-worker.js'));

// A worker only covers pages at or below its own path. This uses a real
// worker on a real local server (the other tests fake one) to prove the app
// page is covered, which phone alerts depend on.
test('the worker registered from the top of the site covers the app page', async ({ page }) => {
  const server = http.createServer((request, response) => {
    const pathname = new URL(request.url, 'http://localhost').pathname;
    if (pathname === '/service-worker.js' || pathname === '/static/service-worker.js') { response.writeHead(200, { 'Content-Type':'text/javascript' }); return response.end(worker); }
    if (pathname === '/') { response.writeHead(200, { 'Content-Type':'text/html' }); return response.end('<!doctype html><title>app</title>'); }
    response.writeHead(404); response.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await page.goto(`http://localhost:${server.address().port}/`);
    const ready = (script, options) => page.evaluate(async ([s, o]) => {
      for (const old of await navigator.serviceWorker.getRegistrations()) await old.unregister();
      await navigator.serviceWorker.register(s, o);
      return Promise.race([
        navigator.serviceWorker.ready.then(registration => new URL(registration.scope).pathname),
        new Promise(resolve => setTimeout(() => resolve('never ready'), 2500)),
      ]);
    }, [script, options]);
    // How it used to be registered: the page waits for ever.
    expect(await ready('/static/service-worker.js', undefined)).toBe('never ready');
    // How it is registered now.
    expect(await ready('/service-worker.js', { scope:'/' })).toBe('/');
  } finally { server.close(); }
});

// While Render restarts the server, the new stylesheet returns 502. The map
// must keep its styles from the last saved version, not lose them all.
test('a stylesheet that fails during a restart falls back to the last saved version', async ({ page }) => {
  let restarting = false;
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    if (url.pathname === '/service-worker.js') { response.writeHead(200, { 'Content-Type':'text/javascript' }); return response.end(worker); }
    if (url.pathname === '/') { response.writeHead(restarting ? 502 : 200, { 'Content-Type':'text/html' }); return response.end(restarting ? 'Bad gateway' : '<!doctype html><title>app</title>'); }
    if (url.pathname === '/static/map-markers.css') {
      if (url.searchParams.get('v') === 'old') { response.writeHead(200, { 'Content-Type':'text/css' }); return response.end('/* old */'); }
      response.writeHead(502); return response.end('Bad gateway');
    }
    response.writeHead(404); response.end();
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const base = `http://localhost:${server.address().port}`;
    await page.goto(`${base}/`);
    await page.evaluate(async () => { await navigator.serviceWorker.register('/service-worker.js', { scope:'/' }); await navigator.serviceWorker.ready; });
    await page.reload();
    await expect.poll(() => page.evaluate(() => Boolean(navigator.serviceWorker.controller))).toBe(true);
    // Earlier visit: the old version was fetched and saved.
    expect(await page.evaluate(() => fetch('/static/map-markers.css?v=old').then(r => r.text()))).toBe('/* old */');
    await expect.poll(() => page.evaluate(async () => Boolean(await caches.match('/static/map-markers.css?v=old')))).toBe(true);
    // Now the new version 502s: the saved old one is used instead.
    const text = await page.evaluate(() => fetch('/static/map-markers.css?v=new').then(r => r.text()));
    expect(text).toBe('/* old */');
    // And a 502 home page is never saved as the offline copy.
    restarting = true;
    await page.evaluate(() => fetch('/'));
    restarting = false;
    const saved = await page.evaluate(async () => { const hit = await caches.match('/'); return hit ? hit.text() : null; });
    expect(saved).not.toBe('Bad gateway');
  } finally { server.close(); }
});
