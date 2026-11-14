const fs = require('node:fs');
const path = require('node:path');

const root = path.resolve(__dirname, '../..');
const read = relative => fs.readFileSync(path.join(root, relative));
const snapshot = JSON.parse(read('e2e/fixtures/snapshot.json'));
const places = JSON.parse(read('e2e/fixtures/places.json'));
const globalCoolFixture = {
  activities: [
    ...snapshot.activities.filter(row => Number(row.cool_score || 0) >= 45),
    {
      ...snapshot.activities.find(row => row.id === 'aircraft-live-1'),
      id: 'aircraft-global-only',
      source_id: 'global01',
      name: 'RAF AWACS SENTINEL01',
      lat: 57.12,
      lon: -3.15,
      cool_score: 99,
      cool_badge: 'AWACS',
      cool_explanation: 'Airborne surveillance aircraft elsewhere in the live UK feed',
    },
    {
      ...snapshot.activities.find(row => row.id === 'vessel-live-1'),
      id: 'rescue-context-only',
      type: 'rescue',
      kind: 'event',
      name: 'RNLI RESCUE CONTEXT',
      lat: 50.71,
      lon: -1.31,
      cool_category: 'rescue',
      cool_subtype: 'lifeboat-response',
      cool_score: 100,
      cool_badge: 'RESCUE',
      cool_candidate: false,
      cool_explanation: 'Active rescue context shown for awareness only',
    },
  ],
  count: 6,
  available_count: 6,
  available_counts: { air:3, rail:2, rescue:1 },
  full_access: true,
  updated_at: '2026-10-05T10:01:00Z',
};
const discoveryCatalogueFixture = {
  version: 1,
  count: 4,
  collectibles: [
    { id:'air:combat-aircraft', key:'air:combat-aircraft', category:'air', subtype:'combat-aircraft', title:'Fast Jet', explanation:'An identified high-performance combat aircraft.', icon:'▲', badge:'FAST JET', safety_note:'Discover from the live map only; never approach moving vehicles or restricted sites.' },
    { id:'rail:steam', key:'rail:steam', category:'rail', subtype:'steam', title:'Steam Train', explanation:'A steam-hauled railway movement.', icon:'♨', badge:'STEAM', safety_note:'Discover from the live map or a lawful public location; never enter railway property.' },
    { id:'rail:railtour-special', key:'rail:railtour-special', category:'rail', subtype:'railtour-special', title:'Railtour', explanation:'A charter or special passenger movement.', icon:'◆', badge:'RAILTOUR', safety_note:'Discover from the live map or a lawful public location; never enter railway property.' },
    { id:'sea:naval', key:'sea:naval', category:'sea', subtype:'naval', title:'Naval Vessel', explanation:'An identified military or naval vessel.', icon:'⚓', badge:'NAVAL', safety_note:'Discover from the live map; never enter restricted ports or interfere with vessel operations.' },
  ],
};
const discoveryMissionsFixture = {
  week: '2026-W41',
  starts_at: '2026-10-05T00:00:00Z',
  ends_at: '2026-10-12T00:00:00Z',
  missions: [
    { id:'britain-in-motion', title:'Britain in Motion', description:'Discover one air, one rail and one sea collectible on the live map.', icon:'✦', targets:[], categories:['air','rail','sea'], total:3, distinct:'category', safety_note:'Map discovery only. Never travel towards a moving target or restricted site.', ends_at:'2026-10-12T00:00:00Z' },
    { id:'heritage-and-specials', title:'Heritage & Specials', description:'Discover two different heritage or special railway movements.', icon:'♨', targets:['rail:steam','rail:railtour-special'], categories:['rail'], total:2, distinct:'collectible', safety_note:'Map discovery only. Observe railway activity from lawful public places.', ends_at:'2026-10-12T00:00:00Z' },
  ],
};

async function installAppFixture(page) {
  await page.addInitScript(payload => {
    Date.now = () => Date.parse('2026-10-05T10:01:00Z');
    HTMLMediaElement.prototype.play = async function () { this.dataset.played = 'true'; };
    const geolocationCounterKey = '__radarTestGeolocationRequests';
    const recordGeolocationRequest = () => {
      const next = Number(sessionStorage.getItem(geolocationCounterKey) || 0) + 1;
      sessionStorage.setItem(geolocationCounterKey, String(next));
      window.__geolocationRequests = next;
    };
    window.__geolocationRequests = Number(sessionStorage.getItem(geolocationCounterKey) || 0);
    const pushSubscription={
      endpoint:'https://fcm.googleapis.com/fcm/send/fixture',
      keys:{p256dh:'p'.repeat(65),auth:'a'.repeat(16)},
      toJSON(){return {endpoint:this.endpoint,keys:this.keys};},
      async unsubscribe(){return true;},
    };
    const pushRegistration={pushManager:{async getSubscription(){return null;},async subscribe(){return pushSubscription;}}};
    try {Object.defineProperty(navigator,'serviceWorker',{configurable:true,value:{ready:Promise.resolve(pushRegistration),async register(){return pushRegistration;}}});} catch {}
    try {Object.defineProperty(navigator,'share',{configurable:true,value:async value=>{window.__sharedPayload=value;}});} catch {}
    window.PushManager=window.PushManager||function PushManager(){};
    window.Notification={permission:'default',async requestPermission(){this.permission='granted';return 'granted';}};
    try {
      Object.defineProperty(navigator, 'geolocation', {
        configurable: true,
        value: {
          getCurrentPosition: recordGeolocationRequest,
          watchPosition: () => { recordGeolocationRequest(); return 1; },
          clearWatch: () => {},
        },
      });
    } catch {}
    class FixtureWebSocket {
      static OPEN = 1;
      constructor() {
        this.readyState = FixtureWebSocket.OPEN;
        window.__fixtureSocketMessages = [];
        window.__fixtureSocket = this;
        window.__fixtureSnapshot = payload;
        setTimeout(() => this.onopen?.(), 20);
      }
      send(raw) {
        try {
          const message = JSON.parse(raw);
          window.__fixtureSocketMessages.push(message);
          if (message.action !== 'viewport') return;
          const requested = message.types;
          const activities = requested === null
            ? payload.activities
            : payload.activities.filter(row => Array.isArray(requested) && requested.includes(row.type));
          const response = {
            ...payload,
            activities,
            stream: {...payload.stream, selected_types: requested},
          };
          setTimeout(() => this.onmessage?.({data:JSON.stringify(response)}), 0);
        } catch {}
      }
      close() { this.readyState = 3; }
    }
    window.WebSocket = FixtureWebSocket;
  }, snapshot);

  let nearbyAircraftRequests=0;
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    const decodedPathname = decodeURIComponent(pathname);
    if (url.hostname === 'radar.test') {
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: read('app/static/index.html') });
      if (pathname.startsWith('/live/')) return route.fulfill({ contentType: 'text/html', body: read('app/static/share.html') });
      if (pathname === '/static/app.js') return route.fulfill({ contentType: 'text/javascript', body: read('app/static/app.js') });
      if (pathname === '/static/vendor/leaflet/leaflet.js') return route.fulfill({ contentType: 'text/javascript', body: read('e2e/support/leaflet-stub.js') });
      if (pathname.startsWith('/static/vendor/') && pathname.endsWith('.js')) return route.fulfill({ contentType: 'text/javascript', body: '' });
      if (pathname.startsWith('/static/vendor/') && pathname.endsWith('.css')) return route.fulfill({ contentType: 'text/css', body: '' });
      if (pathname === '/static/movement-history.js') return route.fulfill({ contentType: 'text/javascript', body: read('app/static/movement-history.js') });
      if (pathname === '/static/motion-engine.js') return route.fulfill({ contentType: 'text/javascript', body: read('app/static/motion-engine.js') });
      if (pathname === '/static/live-object-renderer.js') return route.fulfill({ contentType: 'text/javascript', body: 'export class LiveObjectRenderer {}' });
      if (pathname === '/static/share.js') return route.fulfill({ contentType: 'text/javascript', body: read('app/static/share.js') });
      if (pathname === '/static/share.css') return route.fulfill({ contentType: 'text/css', body: read('app/static/share.css') });
      if (pathname === '/static/app.css') return route.fulfill({ contentType: 'text/css', body: read('app/static/app.css') });
      if (pathname === '/static/map-markers.css') return route.fulfill({ contentType: 'text/css', body: read('app/static/map-markers.css') });
      if (pathname === '/static/service-worker.js') return route.fulfill({ contentType: 'text/javascript', body: 'self.addEventListener("fetch",()=>{});' });
      if (pathname === '/static/manifest.webmanifest') return route.fulfill({ contentType: 'application/manifest+json', body: '{}' });
      if (pathname.startsWith('/static/icon-')) return route.fulfill({ contentType: 'image/png', body: Buffer.alloc(0) });
      if (pathname === '/api/map/config') return route.fulfill({ json:{provider:'Mapbox',configured:false,tile_url:null,tile_size:512,zoom_offset:-1,max_zoom:20,attribution:'Fixture map',live_object_renderer:false} });
      if (pathname === '/api/places') return route.fulfill({ json: places });
      if (pathname === '/api/places/nearest-rail') return route.fulfill({ json: { place: places.places[0] } });
      if (pathname === '/api/places/nearest-bus') return route.fulfill({ json: { place: { ...places.places[1], distance_km:0.24 } } });
      if (pathname === '/api/aircraft/nearby') {
        nearbyAircraftRequests+=1;
        const aircraft=snapshot.activities.find(row=>row.id==='aircraft-live-1');
        return route.fulfill({ json: { checked:2, radius_miles:50, activities:[{
          ...aircraft,
          lat:Number(aircraft.lat)+nearbyAircraftRequests*0.002,
          last_updated:'2026-10-05T10:00:58Z',
        }] } });
      }
      if (decodedPathname.includes('/api/places/rail:MKC/board')) return route.fulfill({ json: { board: { mode: 'live', source: 'Recorded rail fixture', next_live_id: 'train-live-1', departures: [{ expected:'10:14', destination:'Manchester Piccadilly', platform:'4', status:'On time', headcode:'1A23', train_uid:'C12345', service_date:'2026-10-05', live_train_id:'train-live-1' },{ expected:'10:32', origin:'Milton Keynes Central', destination:'Birmingham New Street', platform:'6', status:'On time', headcode:'1Z80', train_uid:'C99999', service_date:'2026-10-05', operator:'Fixture Rail' }], arrivals: [{ expected:'10:08', origin:'Birmingham New Street', platform:'2', status:'On time' }] } } });
      if (decodedPathname.startsWith('/api/rail/lines/')) return route.fulfill({ json:{ level:0, lines:[
        { id:1, points:[[52.04,-0.76],[52.055,-0.742],[52.08,-0.70]] },
        { id:2, points:[[52.40,-1.50],[52.48,-1.90]] },
      ] } });
      if (pathname === '/api/rail/line') return route.fulfill({ json:{ found:false } });
      if (pathname === '/api/rail/delays') return route.fulfill({ json:{ hotspots:[
        { from:'Milton Keynes Central', to:'Northampton', late:3, cancelled:1, average_delay:18, worst_delay:31, train_ids:['train-live-1'], center:[52.1,-0.85], points:[[52.03,-0.77],[52.23,-0.9]] },
        { from:'Bletchley', to:'Leighton Buzzard', late:1, cancelled:0, average_delay:7, worst_delay:7, train_ids:[], center:[51.95,-0.7], points:[[51.99,-0.73],[51.92,-0.66]] },
      ] } });
      if (pathname === '/api/trains/near') return route.fulfill({ json:{ radius_km:5, trains:[ { id:'train-live-1', name:'1A23', destination:'Manchester Piccadilly', operator:'Fixture Rail', delay_minutes:6, distance_km:0.4 } ] } });
      if (pathname === '/api/trains/status') return route.fulfill({ json:{ trains:[] } });
      if (decodedPathname.startsWith('/api/rail/locate')) return route.fulfill({ json:{live:false,activity:null,message:'No matching live position is available right now.'} });
      if (decodedPathname.startsWith('/api/rail/schedule/C99999')) return route.fulfill({ json:{train_uid:'C99999',detail:{available:true,mode:'published_schedule',source:'Published railway timetable',live_marker:false,note:'Published route only. A matching live position is not available right now.',runs:[{headcode:'1Z80',origin:'Milton Keynes Central',destination:'Birmingham New Street',status:'Scheduled',calling_points:[{name:'Milton Keynes Central',scheduled:'10:32',event:'Calls'},{name:'Coventry',scheduled:'11:08',event:'Calls'},{name:'Birmingham New Street',scheduled:'11:31',event:'Calls'}]}]}} });
      if (decodedPathname.includes('/api/places/bus:490000077A/board')) return route.fulfill({ json: { board: { mode: 'live_predictions', source: 'Recorded BODS fixture', next_live_id:'bus-live-7', rows: [{ expected:'10:06', route:'7', destination:'City Centre', operator:'Arriva Midlands', status:'Live', minutes:5, live_vehicle_id:'bus-live-7' }] } } });
      if (pathname.startsWith('/api/radio/nearby')) return route.fulfill({ json: { ok:true, stations: [
        { name:'Fixture Coast Radio', specialism:'marine', stream_url:'https://audio.invalid/live.mp3', homepage:'https://audio.invalid', lat:50.8, lon:-1.1, distance_km:3, codec:'MP3' },
        { name:'Fixture Local One', specialism:'general', stream_url:'https://audio.invalid/local-one.mp3', homepage:'https://audio.invalid/one', lat:52.1, lon:-0.8, distance_km:4, codec:'MP3' },
        { name:'Fixture Local Two', specialism:'general', stream_url:'https://audio.invalid/local-two.mp3', homepage:'https://audio.invalid/two', lat:52.2, lon:-0.7, distance_km:7, codec:'AAC' },
      ], specialist_count:1 } });
      if (pathname === '/api/weather/radar') return route.fulfill({ json: { tile_template:'https://radar.invalid/{z}/{x}/{y}.png', frame_time:1791194400, max_native_zoom:7, attribution:'Fixture radar' } });
      if (pathname === '/api/traffic/status') return route.fulfill({ json: { configured:true } });
      if (pathname === '/api/billing/status') return route.fulfill({ json: { enabled:true, plan:'plus', plus:true, subscription_status:'active', price_display:'£3.99/month', features:['cool_full','advanced_identity','advanced_transport','journey_planner','live_cameras','traffic_pro','fast_updates','live_tracking'] } });
      if (pathname === '/api/alerts/status') return route.fulfill({ json:{configured:true,public_key:'B'.repeat(87),subscribed:false,preferences:null,requires_https:true} });
      if (pathname === '/api/alerts/subscribe' && route.request().method() === 'POST') return route.fulfill({ json:{ok:true,message:'Commute alerts are active on this device'} });
      if (pathname === '/api/alerts/unsubscribe' && route.request().method() === 'POST') return route.fulfill({ json:{ok:true} });
      if (pathname === '/api/search') {
        const q=(url.searchParams.get('q') || '').toLowerCase();
        if(q.includes('ryanair') || q.includes('ryr1yy')) return route.fulfill({json:{matches:[snapshot.activities.find(row=>row.id==='aircraft-live-3')],location:null}});
        if(q.includes('chinook') || q.includes('shf475')) return route.fulfill({json:{matches:[snapshot.activities.find(row=>row.id==='aircraft-live-2')],location:null}});
        const match=q.includes('london')
          ? { id:'rail-station:EUS', name:'London Euston', subtitle:'Rail station • EUS', lat:51.528, lon:-0.134 }
          : { id:'rail-station:MKC', name:'Milton Keynes Central', subtitle:'Rail station • MKC', lat:52.034, lon:-0.774 };
        const location=q.includes('leicester') ? {kind:'location',name:'Leicester city centre',lat:52.6369,lon:-1.1398,source:'Fixture geocoder'} : null;
        return route.fulfill({ json:{ matches:[match], location } });
      }
      if (pathname === '/api/journeys/plan' && route.request().method() === 'POST') return route.fulfill({ json:{
        provider:'OpenTripPlanner', status:'ok', available:true, message:'Found 1 journey option(s).',
        itineraries:[{ depart_at:'2026-10-05T10:15:00Z', arrive_at:'2026-10-05T10:51:00Z', duration_seconds:2160, transfers:0, walk_distance_m:420, traffic_delay_seconds:180, geometry:[{lat:52.034,lon:-0.774},{lat:51.8,lon:-0.5},{lat:51.528,lon:-0.134}], route_issues:[{id:'incident-live-1',type:'incident',name:'M1 lane closure',road:'M1',status:'Active',distance_km:0.8,lat:52.01,lon:-0.72}], legs:[
          { mode:'WALK', duration_seconds:300, distance_m:350, from:{name:'Milton Keynes Central'}, to:{name:'Platform 4'}, depart_at:'2026-10-05T10:15:00Z', arrive_at:'2026-10-05T10:20:00Z' },
          { mode:'RAIL', duration_seconds:1860, from:{name:'Milton Keynes Central'}, to:{name:'London Euston'}, headsign:'London Euston', route:{short_name:'West Coast'}, agency:'Fixture Rail', depart_at:'2026-10-05T10:20:00Z', arrive_at:'2026-10-05T10:51:00Z' },
        ]},],
      } });
      if (pathname === '/api/cameras') return route.fulfill({ json: { ok:true, source:'Recorded camera fixture', stale:false, cameras:[{ id:'camera-live-1', type:'camera', name:'M1 Junction 14 Camera', lat:52.045, lon:-0.755, view:'Northbound', available:true, image_url:'https://jamcams.tfl.gov.uk/fixture.jpg', video_url:null, source:'Recorded official camera', source_url:'https://tfl.gov.uk/traffic/status/', refresh_seconds:180 }] } });
      if (pathname === '/api/cameras/nearest') return route.fulfill({ json: { ok:true, source:'Recorded camera fixture', stale:false, camera:{ id:'camera-live-1', type:'camera', name:'M1 Junction 14 Camera', lat:52.045, lon:-0.755, view:'Northbound', available:true, image_url:'https://jamcams.tfl.gov.uk/fixture.jpg', video_url:null, source:'Recorded official camera', source_url:'https://tfl.gov.uk/traffic/status/', refresh_seconds:180, distance_km:1.2 } } });
      if (pathname === '/api/cool') return route.fulfill({ json: globalCoolFixture });
      if (pathname === '/api/today') return route.fulfill({ json: {
        generated_at:'2026-10-05T10:01:00Z',
        best:{activity_id:'today-remote-1',type:'train',name:'Steam Railtour 1Z80',reason:'Steam-hauled charter',status:'Running',freshness:'2026-10-05T10:00:00Z'},
        new_since:[{activity_id:'aircraft-live-1',type:'aircraft',name:'Typhoon TEST01',reason:'Military fast jet',status:'Airborne',freshness:'2026-10-05T10:00:00Z'}],
        disruptions:[{activity_id:'incident-live-1',type:'incident',name:'M1 lane closure',reason:'Live road incident',status:'Active',freshness:'2026-10-05T10:00:00Z'}],
        counts:{live:snapshot.activities.length,interesting:3,new_since:1,disruptions:1},
      } });
      if (decodedPathname.startsWith('/api/share/')) {
        const activityId=decodedPathname.replace('/api/share/','');
        const item=snapshot.activities.find(row=>row.id===activityId) || snapshot.activities[0];
        return route.fulfill({ json:{activity_id:item.id,type:item.type,name:item.name,subtitle:item.subtitle,status:item.status,reason:item.cool_explanation||item.subtitle,location:'Central Britain',position_confidence:item.position_method||'network_report',source:item.source,freshness:item.last_updated,sensitive:false,safety_note:'No precise coordinates are included on this public page.',share_url:`http://radar.test/live/${encodeURIComponent(item.id)}`} });
      }
      if (pathname === '/api/discoveries/catalogue') return route.fulfill({ json: discoveryCatalogueFixture });
      if (pathname === '/api/discoveries/missions') return route.fulfill({ json: discoveryMissionsFixture });
      if (pathname === '/api/discoveries/claim' && route.request().method() === 'POST') {
        let requestPayload = {};
        try { requestPayload = route.request().postDataJSON(); } catch {}
        if (requestPayload.activity_id !== 'steam-live-1') {
          return route.fulfill({ status:404, json:{ detail:{ code:'activity_not_found', message:'That live activity is no longer available.' } } });
        }
        return route.fulfill({ json:{ ok:true, discovery:{
          id:'rail:steam', key:'rail:steam', activity_id:'steam-live-1', category:'rail', subtype:'steam', title:'Steam Train',
          explanation:'Steam locomotive.', icon:'♨', confidence:'high', freshness:'live', age_seconds:60, fresh_for_seconds:2700,
          observed_at:'2026-10-05T10:00:00Z', discovered_at:'2026-10-05T10:01:00Z', broad_region:'Northern England and Wales',
          source:'Network Rail TRUST', identity_level:'type', identity_note:'The movement type is classified, but its exact locomotive or formation is not confirmed.',
          weeks:['2026-W41'],
        } } });
      }
      if (pathname === '/api/product/plans') return route.fulfill({ json: { payments_enabled:false, plans:[] } });
      if (pathname === '/api/product/summary') return route.fulfill({ json: { anonymous_sessions:0, events:{}, pricing_interest:{} } });
      if (pathname === '/api/product/event') return route.fulfill({ json: { ok:true } });
      if (decodedPathname.includes('/api/activity/aircraft-live-3/flight-route')) return route.fulfill({ json:{ found:true, route:{ origin:{name:'London Stansted',iata:'STN',icao:'EGSS'}, destination:{name:'Dublin',iata:'DUB',icao:'EIDW'}, via:[] } } });
      if (decodedPathname.endsWith('/flight-route')) return route.fulfill({ json:{ found:false, route:null } });
      if (decodedPathname.includes('/api/activity/aircraft-live-3/aircraft')) return route.fulfill({ json:{ ok:true, registration:'EI-IGJ', model:'Boeing 737-8200', operator:'Ryanair', country:'Ireland' } });
      if (decodedPathname.includes('/api/activity/aircraft-live-3/media')) return route.fulfill({ json:{ ok:true, title:'EI-IGJ Ryanair Boeing 737-8200', thumbnail_url:'https://upload.wikimedia.org/fixture-ryanair.jpg', page_url:'https://commons.wikimedia.org/wiki/File:Fixture_Ryanair', artist:'Fixture Aviation Photographer', license:'CC BY-SA 4.0', license_url:'https://creativecommons.org/licenses/by-sa/4.0/', source:'Wikimedia Commons', match_kind:'exact_airframe' } });
      if (pathname.includes('/api/activity/aircraft-live-') && pathname.endsWith('/aircraft')) return route.fulfill({ json:{ ok:true, registration:'ZK001', model:'Eurofighter Typhoon', country:'United Kingdom' } });
      if (pathname.includes('/api/activity/aircraft-live-') && pathname.endsWith('/media')) return route.fulfill({ json:{ ok:true, title:'Eurofighter Typhoon reference', thumbnail_url:'https://upload.wikimedia.org/fixture-typhoon.jpg', page_url:'https://commons.wikimedia.org/wiki/File:Fixture', artist:'Fixture Photographer', license:'CC BY-SA 4.0', license_url:'https://creativecommons.org/licenses/by-sa/4.0/', source:'Wikimedia Commons', match_kind:'type_reference' } });
      if (decodedPathname.includes('/api/activity/steam-live-1/media')) return route.fulfill({ json:{ ok:true, title:'Flying Scotsman identity-matched archive image', thumbnail_url:'https://upload.wikimedia.org/fixture-steam.jpg', page_url:'https://commons.wikimedia.org/wiki/File:Fixture_steam', artist:'Fixture Photographer', license:'CC BY-SA 4.0', source:'Wikimedia Commons', match_kind:'exact_train' } });
      if (decodedPathname.includes('/api/activity/train-live-1/rail')) return route.fulfill({ json:{ activity:'train-live-1', detail:{ available:true, source:'Live railway and timetable data', note:'The marker remains at the last confirmed railway report.', rolling_stock:{matched:true,number:'43002',train_class:'43',operator:'Fixture Rail',formation:'2+8',livery:'InterCity',source:'UK Train Dataset',source_url:'https://github.com/jamie-lord/uk-train-dataset',caveat:'Reference data may be incomplete or out of date; this is not a live allocation source.'}, runs:[{ headcode:'1A23', origin:'Milton Keynes Central', destination:'London Euston', operator:'East Midlands Railway', status:'On time', current_location:'Bletchley', next_location:'Watford Junction', route_path:[[52.04,-0.76],[52.055,-0.742],[52.08,-0.70],[51.88,-0.42],[51.53,-0.13]], route_resolution:{kind:'published_timing_path',timing_points:5,mapped_timing_points:5,coverage_percent:100,schedule_recovered:false}, calling_points:[{station:'Ledburn Junction',expected:'2026-10-05T10:28:00Z',event:'Passes'},{station:'Watford Junction',expected:'2026-10-05T10:34:00Z',status:'Expected'},{station:'London Euston',expected:'2026-10-05T10:51:00Z',status:'Expected'}] }] } } });
      if (decodedPathname.includes('/api/activity/bus-live-7/bus')) return route.fulfill({ json:{ activity:'bus-live-7', detail:{ mode:'matched_timetable', source:'DfT BODS GTFS timetable + live BODS GPS', route:'7', destination:'City Centre', operator:'Arriva Midlands', note:'Only the next stop is an operator-published live prediction.', live_prediction:{expected:'2026-10-05T10:06:00Z',delay_minutes:2,status:'Live • 2 min late'}, stops:[{name:'Central Station Stop A',clock:'10:06',state:'next'},{name:'Market Square',clock:'10:13',state:'upcoming'}] } } });
      if (decodedPathname.includes('/api/activity/bus-live-7/media')) return route.fulfill({json:{ok:true,title:'Arriva bus 4201',thumbnail_url:'https://upload.wikimedia.org/fixture-arriva-bus.jpg',page_url:'https://commons.wikimedia.org/wiki/File:Fixture_Arriva',artist:'Fixture Transport Photographer',license:'CC BY-SA 4.0',license_url:'https://creativecommons.org/licenses/by-sa/4.0/',source:'Wikimedia Commons',match_kind:'exact_vehicle'}});
      if (decodedPathname === '/api/activity/train-live-1') return route.fulfill({ json:snapshot.activities.find(row=>row.id==='train-live-1') });
      if (decodedPathname === '/api/activity/bus-live-7') return route.fulfill({ json:snapshot.activities.find(row=>row.id==='bus-live-7') });
      if (decodedPathname === '/api/activity/aircraft-live-3') return route.fulfill({ json:snapshot.activities.find(row=>row.id==='aircraft-live-3') });
      if (decodedPathname === '/api/activity/today-remote-1') return route.fulfill({ json:{...snapshot.activities.find(row=>row.id==='steam-live-1'),id:'today-remote-1',name:'Steam Railtour 1Z80',lat:57.12,lon:-3.15} });
      if (pathname.startsWith('/api/activity/')) return route.fulfill({ status:404, json:{ ok:false } });
      if (pathname.startsWith('/api/events/')) return route.fulfill({ json:{ articles:[], official_links:[] } });
      return route.fulfill({ status:404, body:'' });
    }
    if (url.hostname === 'unpkg.com' && pathname.endsWith('.js')) return route.fulfill({ contentType:'text/javascript', body:read('e2e/support/leaflet-stub.js') });
    if (url.hostname === 'unpkg.com') return route.fulfill({ contentType:'text/css', body:'' });
    if (url.hostname === 'jamcams.tfl.gov.uk' || url.hostname === 'upload.wikimedia.org') return route.fulfill({
      contentType:'image/png',
      body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=','base64')
    });
    return route.abort();
  });
}

module.exports = { installAppFixture };
