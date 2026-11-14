const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const read = relative => fs.readFileSync(path.join(root, relative));

test('MapLibre live layer renders trains and aircraft from one motion clock', async ({ page }) => {
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
        {id:'train-1',type:'train',lat:52.6,lon:-1.2,reported_lat:52.6,reported_lon:-1.2,next_lat:52.7,next_lon:-1.1,estimate_path:[[52.6,-1.2],[52.65,-1.16],[52.7,-1.1]],estimate_started_at:new Date(now-30000).toISOString(),estimate_due_at:new Date(now+30000).toISOString()},
        {id:'train-2',type:'train',lat:52.6,lon:-1.2,reported_lat:52.6,reported_lon:-1.2,next_lat:52.7,next_lon:-1.1,estimate_path:[[52.6,-1.2],[52.65,-1.16],[52.7,-1.1]],estimate_started_at:new Date(now-30000).toISOString(),estimate_due_at:new Date(now+30000).toISOString()},
        {id:'air-1',type:'aircraft',lat:52.5,lon:-1.3,bearing:90,speed:250,last_updated:new Date(now-3000).toISOString(),aircraft_model:'Airbus A320'}
      ],'train-1');
    </script></body></html>`});
  });
  await page.goto('http://radar.test/maplibre-test');
  await expect(page.locator('#live')).toHaveAttribute('data-renderer','ready',{timeout:10000}).catch(error=>{throw new Error(`${error.message}\nBrowser errors: ${errors.join(' | ')}`);});
  await expect(page.locator('#live')).toHaveAttribute('data-object-count','3');
  await expect(page.locator('#live')).toHaveAttribute('data-coincident-groups','1');
  await expect(page.locator('#live')).toHaveAttribute('data-separated-objects','2');
  await expect.poll(()=>page.evaluate(()=>{
    const renderer=window.__renderer;
    const feature=renderer.lastFeatures.find(row=>row.properties.id==='train-2');
    return renderer.featureAt(renderer.map.project(feature.geometry.coordinates));
  })).toBe('train-2');
  await expect(page.locator('body')).toHaveAttribute('data-frame-intervals','16,33,50,16');
  await expect(page.locator('.map-panel')).toHaveClass(/maplibre-live-ready/);
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
});
