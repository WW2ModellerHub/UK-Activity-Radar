const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = relative => fs.readFileSync(path.join(root, relative));
const snapshot = JSON.parse(read('e2e/fixtures/snapshot.json'));
const places = JSON.parse(read('e2e/fixtures/places.json'));

async function installAppFixture(page) {
  await page.addInitScript(payload => {
    Date.now = () => Date.parse('2026-10-05T10:01:00Z');
    HTMLMediaElement.prototype.play = async function () { this.dataset.played = 'true'; };
    class FixtureWebSocket {
      static OPEN = 1;
      constructor() {
        this.readyState = FixtureWebSocket.OPEN;
        setTimeout(() => { this.onopen?.(); this.onmessage?.({ data: JSON.stringify(payload) }); }, 20);
      }
      send() {}
      close() { this.readyState = 3; }
    }
    window.WebSocket = FixtureWebSocket;
  }, snapshot);

  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    const decodedPathname = decodeURIComponent(pathname);
    if (url.hostname === 'radar.test') {
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: read('app/static/index.html') });
      if (pathname === '/static/app.js') return route.fulfill({ contentType: 'text/javascript', body: read('app/static/app.js') });
      if (pathname === '/static/styles.css') return route.fulfill({ contentType: 'text/css', body: read('app/static/styles.css') });
      if (pathname === '/static/service-worker.js') return route.fulfill({ contentType: 'text/javascript', body: 'self.addEventListener("fetch",()=>{});' });
      if (pathname === '/static/manifest.webmanifest') return route.fulfill({ contentType: 'application/manifest+json', body: '{}' });
      if (pathname.startsWith('/static/icon-')) return route.fulfill({ contentType: 'image/png', body: Buffer.alloc(0) });
      if (pathname === '/api/places') return route.fulfill({ json: places });
      if (pathname === '/api/places/nearest-rail') return route.fulfill({ json: { place: places.places[0] } });
      if (decodedPathname.includes('/api/places/rail:MKC/board')) return route.fulfill({ json: { board: { mode: 'live', source: 'Recorded rail fixture', next_live_id: 'train-live-1', departures: [{ expected:'10:14', destination:'Manchester Piccadilly', platform:'4', status:'On time', headcode:'1A23', live_train_id:'train-live-1' }], arrivals: [{ expected:'10:08', origin:'Birmingham New Street', platform:'2', status:'On time' }] } } });
      if (decodedPathname.includes('/api/places/bus:490000077A/board')) return route.fulfill({ json: { board: { mode: 'live_predictions', source: 'Recorded BODS fixture', next_live_id:'bus-live-7', rows: [{ expected:'10:06', route:'7', destination:'City Centre', operator:'Fixture Buses', status:'Live', minutes:5, live_vehicle_id:'bus-live-7' }] } } });
      if (pathname.startsWith('/api/radio/nearby')) return route.fulfill({ json: { stations: [{ name:'Fixture Coast Radio', specialism:'marine', stream_url:'https://audio.invalid/live.mp3', homepage:'https://audio.invalid', lat:50.8, lon:-1.1, distance_km:3, codec:'MP3' }] } });
      if (pathname === '/api/weather/radar') return route.fulfill({ json: { tile_template:'https://radar.invalid/{z}/{x}/{y}.png', frame_time:1791194400, max_native_zoom:7, attribution:'Fixture radar' } });
      if (pathname === '/api/traffic/status') return route.fulfill({ json: { configured:true } });
      if (pathname === '/api/product/plans') return route.fulfill({ json: { payments_enabled:false, plans:[] } });
      if (pathname === '/api/product/summary') return route.fulfill({ json: { anonymous_sessions:0, events:{}, pricing_interest:{} } });
      if (pathname === '/api/product/event') return route.fulfill({ json: { ok:true } });
      if (pathname.startsWith('/api/activity/')) return route.fulfill({ status:404, json:{ ok:false } });
      if (pathname.startsWith('/api/events/')) return route.fulfill({ json:{ articles:[], official_links:[] } });
      return route.fulfill({ status:404, body:'' });
    }
    if (url.hostname === 'unpkg.com' && pathname.endsWith('.js')) return route.fulfill({ contentType:'text/javascript', body:read('e2e/support/leaflet-stub.js') });
    if (url.hostname === 'unpkg.com') return route.fulfill({ contentType:'text/css', body:'' });
    if (url.hostname === 'tile.openstreetmap.org' || url.hostname.endsWith('basemaps.cartocdn.com') || url.hostname === 'radar.invalid') return route.fulfill({ contentType:'image/png', body:Buffer.alloc(0) });
    return route.abort();
  });
}

module.exports = { installAppFixture };
