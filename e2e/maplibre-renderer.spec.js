const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative));

test('MapLibre live layer renders trains; aircraft stay on the map markers with their data block', async ({ page }) => {
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/static/live-object-renderer.js') return route.fulfill({contentType:'text/javascript',body:read('app/static/live-object-renderer.js')});
    if (pathname === '/static/motion-engine.js') return route.fulfill({contentType:'text/javascript',body:read('app/static/motion-engine.js')});
    if (pathname === '/static/vendor/maplibre/maplibre-gl.mjs') return route.fulfill({contentType:'text/javascript',body:read('app/static/vendor/maplibre/maplibre-gl.mjs')});
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html><body><div class="map-panel"><div id="live" style="width:640px;height:420px"></div></div><script src="/static/motion-engine.js"></script><script type="module">
      import {LiveObjectRenderer,frameInterval} from '/static/live-object-renderer.js';
      document.body.dataset.frameIntervals=[frameInterval(50),frameInterval(300),frameInterval(900),frameInterval(900,true)].join(',');
      class LeafMap { constructor(){this.events={};} getCenter(){return {lat:52.6,lng:-1.2};} getZoom(){return 9;} on(names,handler){for(const name of names.split(' '))this.events[name]=handler;} off(){} }
      const now=Date.now(),renderer=window.__renderer=new LiveObjectRenderer({container:document.querySelector('#live'),leafMap:new LeafMap(),motionEngine:window.UKActivityMotionEngine});
      renderer.setActivities([
        {id:'train-1',type:'train',bearing:135,lat:52.6,lon:-1.2,reported_lat:52.6,reported_lon:-1.2,next_lat:52.7,next_lon:-1.1,estimate_path:[[52.6,-1.2],[52.65,-1.16],[52.7,-1.1]],estimate_started_at:new Date(now-30000).toISOString(),estimate_due_at:new Date(now+30000).toISOString()},
        {id:'train-2',type:'train',lat:52.6,lon:-1.2,reported_lat:52.6,reported_lon:-1.2,next_lat:52.7,next_lon:-1.1,estimate_path:[[52.6,-1.2],[52.65,-1.16],[52.7,-1.1]],estimate_started_at:new Date(now-30000).toISOString(),estimate_due_at:new Date(now+30000).toISOString()},
        {id:'air-1',type:'aircraft',lat:52.5,lon:-1.3,bearing:90,speed:250,last_updated:new Date(now-3000).toISOString(),aircraft_model:'Airbus A320'}
      ],'train-1');
    </script></body></html>`});
  });
  await page.goto('http://radar.test/maplibre-test');
  await expect(page.locator('#live')).toHaveAttribute('data-renderer','ready',{timeout:10000}).catch(error=>{throw new Error(`${error.message}\nBrowser errors: ${errors.join(' | ')}`);});
  await expect(page.locator('#live')).toHaveAttribute('data-object-count','2');
  await expect(page.locator('#live')).toHaveAttribute('data-coincident-groups','1');
  await expect(page.locator('#live')).toHaveAttribute('data-separated-objects','2');
  await expect.poll(()=>page.evaluate(()=>{
    const renderer=window.__renderer;
    const feature=renderer.lastFeatures.find(row=>row.properties.id==='train-2');
    return renderer.featureAt(renderer.map.project(feature.geometry.coordinates));
  })).toBe('train-2');
  // Train badges sit upright whatever their direction (aircraft are not on this layer).
  expect(await page.evaluate(()=>Object.fromEntries(window.__renderer.lastFeatures.map(row=>[row.properties.id,row.properties.rotation])))).toEqual({'train-1':0,'train-2':0});
  await expect(page.locator('body')).toHaveAttribute('data-frame-intervals','16,33,50,16');
  await expect(page.locator('.map-panel')).toHaveClass(/maplibre-live-ready/);
  // When one train leaves a shared spot, the one left behind slides back to
  // its true place instead of jumping ~22 px in one frame (seen at stations).
  const steps = await page.evaluate(async () => {
    const r = window.__renderer, rows = r.rows.filter(row => row.id !== 'train-2');
    const px = () => { const f = r.lastFeatures.find(row => row.properties.id === 'train-1'); return r.map.project(f.geometry.coordinates); };
    const t0 = performance.now(); r.draw(t0, true); const start = px();
    r.setActivities(rows, 'train-1');
    const moves = []; let last = start;
    for (let i = 1; i <= 50; i++) { r.draw(t0 + i * 16, true); const now = px(); moves.push(Math.hypot(now.x - last.x, now.y - last.y)); last = now; }
    return { biggest: Math.max(...moves), total: Math.hypot(last.x - start.x, last.y - start.y) };
  });
  expect(steps.total).toBeGreaterThan(10);      // it did move back to its own place
  expect(steps.biggest).toBeLessThan(steps.total / 2);   // but over several frames, not in one jump
});

test('the live layer lines up with the base map at every zoom', async ({ page }) => {
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/static/live-object-renderer.js') return route.fulfill({contentType:'text/javascript',body:read('app/static/live-object-renderer.js')});
    if (pathname === '/static/motion-engine.js') return route.fulfill({contentType:'text/javascript',body:read('app/static/motion-engine.js')});
    if (pathname === '/static/vendor/maplibre/maplibre-gl.mjs') return route.fulfill({contentType:'text/javascript',body:read('app/static/vendor/maplibre/maplibre-gl.mjs')});
    if (pathname === '/static/vendor/leaflet/leaflet.js') return route.fulfill({contentType:'text/javascript',body:read('app/static/vendor/leaflet/leaflet.js')});
    if (pathname === '/static/vendor/leaflet/leaflet.css') return route.fulfill({contentType:'text/css',body:read('app/static/vendor/leaflet/leaflet.css')});
    return route.fulfill({contentType:'text/html',body:`<!doctype html><html><head><link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css"></head><body style="margin:0"><div class="map-panel" style="position:relative;width:800px;height:600px"><div id="map" style="position:absolute;inset:0"></div><div id="live" style="position:absolute;inset:0;pointer-events:none"></div></div>
      <script src="/static/vendor/leaflet/leaflet.js"></script><script src="/static/motion-engine.js"></script><script type="module">
      import {LiveObjectRenderer} from '/static/live-object-renderer.js';
      const map=window.__leaflet=L.map('map',{zoomControl:false}).setView([52.48,-1.9],10);
      window.__renderer=new LiveObjectRenderer({container:document.querySelector('#live'),leafMap:map,motionEngine:window.UKActivityMotionEngine});
    </script></body></html>`});
  });
  await page.goto('http://radar.test/align-test');
  await expect(page.locator('#live')).toHaveAttribute('data-renderer','ready',{timeout:10000});
  const offset = () => page.evaluate(() => {
    const point=[52.6,-1.5];
    const a=window.__leaflet.latLngToContainerPoint(point), b=window.__renderer.map.project([point[1],point[0]]);
    return Math.hypot(a.x-b.x,a.y-b.y);
  });
  expect(await offset()).toBeLessThan(1.5);
  await page.evaluate(() => window.__leaflet.setView([52.5,-1.7],13,{animate:false}));
  await expect.poll(offset).toBeLessThan(1.5);
  await page.evaluate(() => window.__leaflet.setZoom(7,{animate:false}));
  await expect.poll(offset).toBeLessThan(1.5);
  // A detail panel opening narrows the map without a window resize (and
  // without Leaflet being told). Planes were then drawn half the change off,
  // 200 px for a 400 px panel, and stayed there.
  const onScreen = () => page.evaluate(() => {
    const point=[52.6,-1.5], L=window.__leaflet, r=window.__renderer;
    const m=L.getContainer().getBoundingClientRect(), c=r.map.getCanvas().getBoundingClientRect();
    const a=L.latLngToContainerPoint(point), b=r.map.project([point[1],point[0]]);
    return Math.hypot(m.left+a.x-(c.left+b.x), m.top+a.y-(c.top+b.y));
  });
  await page.evaluate(() => { document.querySelector('.map-panel').style.width='400px'; });
  await expect.poll(onScreen).toBeLessThan(1.5);
  await page.evaluate(() => window.__leaflet.setZoom(9,{animate:false}));
  await expect.poll(onScreen).toBeLessThan(1.5);
  await page.evaluate(() => { document.querySelector('.map-panel').style.width='800px'; });
  await expect.poll(onScreen).toBeLessThan(1.5);
});
