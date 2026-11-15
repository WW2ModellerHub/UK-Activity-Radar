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
