const { test, expect } = require('@playwright/test');
const { installAppFixture } = require('./support/app-fixture');

// Map moves and layer changes send a debounced (120 ms) viewport request, and
// the fixture socket answers each one with its standard snapshot. A test that
// injects its own snapshot must wait for those answers first, or a late one
// overwrites the injected data on slower machines.
async function waitForViewportRequestsToSettle(page) {
  await expect.poll(async () => {
    const before = await page.evaluate(() => window.__fixtureSocketMessages.length);
    await page.waitForTimeout(300);
    return before === await page.evaluate(() => window.__fixtureSocketMessages.length);
  }).toBe(true);
}

test.beforeEach(async ({ page }) => {
  await installAppFixture(page);
  await page.goto('/');
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout: 5000 });
});

test('app loads with its map and deterministic live snapshot', async ({ page }) => {
  await expect(page.locator('#map')).toBeVisible();
  await expect(page.locator('.leaflet-container')).toBeVisible();
  await expect(page.locator('.marker-bubble')).toHaveCount(0);
  await expect(page.locator('[data-layer-toggle][aria-pressed="true"]')).toHaveCount(0);
  await expect(page.locator('.ticker-items')).toContainText('Map clear');
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('[data-layer-toggle="bus"]').click();
  await expect(page.locator('.ticker-item')).toContainText(['London Express', 'Bus 7']);
});

test('first load centres the map on the visitor when location is available', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'geolocation', {
    configurable:true,
    value:{getCurrentPosition(success){success({coords:{latitude:52.4862,longitude:-1.8904}});},watchPosition(){return 1;},clearWatch(){}},
  }));
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','52.4862,-1.8904');
  await expect(page.locator('#map')).toHaveAttribute('data-map-zoom','12');
  await expect(page.locator('#recenter')).toHaveText('My location');
});

test('Buses near me enables buses and opens the nearest live stop board', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('[data-layer-toggle="bus"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','52.036,-0.772');
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
  await expect(page.locator('#detail-placeboard')).toContainText('Arriva Midlands');
  await expect(page.locator('.place-marker')).toHaveCount(1);
  await expect(page.locator('.place-marker.place-bus_stop .bus-stop-dot')).toHaveCount(1);
  await expect(page.locator('#nearby-note')).toHaveClass(/hidden/);
  await page.locator('#detail-close').click();
  await expect(page.locator('.place-marker')).toHaveCount(0);
});

test('Today on the Radar shows verified live highlights', async ({ page }) => {
  await expect(page.locator('#today-radar')).toContainText('Steam Railtour 1Z80');
  await expect(page.locator('#today-radar')).not.toContainText('BEST NOW');
  await page.locator('#today-radar [data-today-activity="today-remote-1"]').click();
  await expect(page.locator('#detail-title')).toContainText('Steam Railtour 1Z80');
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','57.12,-3.15');
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed','true');
});

test('Saved keeps a real station on this device', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('#saved-places-list')).toContainText('Milton Keynes Central');
  // With a place saved, the Saved tab shows its next departures and nearby activity.
  await expect(page.locator('.passing-next-card')).toBeVisible();
  await expect(page.locator('#my-radar-area')).toBeVisible();
  await expect(page.locator('#saved-places-count')).toHaveText('1');
});

test('a train opened from a station board can return to that board', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#detail-placeboard .board-clickable').first().click();
  await expect(page.locator('#detail-title')).toContainText('London Express');
  await expect(page.locator('#detail-back')).toBeVisible();
  await page.locator('#detail-back').click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
  await expect(page.locator('#detail-placeboard')).toContainText('Manchester Piccadilly');
});

test('Passing Next uses the saved station live board', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('#passing-next')).toContainText('Manchester Piccadilly');
  await expect(page.locator('#passing-next')).toContainText('10:14');
});

test('a saved place can enable proactive train alerts after an explicit click', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await page.locator('#commute-alert-mode').selectOption('train');
  await page.locator('#commute-alert-enable').click();
  await expect(page.locator('#commute-alert-state')).toHaveText('ON');
  await expect(page.locator('#commute-alert-summary')).toContainText('Alerts are on for this phone.');
  await expect(page.locator('#commute-alert-summary')).toContainText('Train delays near Milton Keynes Central, 05:00 to 11:00');
});

test('shared live pages show broad facts without coordinates', async ({ page }) => {
  await page.goto('/live/train-live-1');
  await expect(page.locator('#share-card h1')).toContainText('London Express');
  await expect(page.locator('#share-card')).toContainText('Central Britain');
  await expect(page.locator('#share-card')).not.toContainText('52.0');
  await expect(page.locator('#share-card')).toContainText('No precise coordinates');
  await expect(page.locator('#share-card .open-radar')).toHaveAttribute('href','/?open=train-live-1');
});

test('a shared loco move explains itself in plain words', async ({ page }) => {
  const pageNow = await page.evaluate(() => Date.now());  // the test pages run on a fixed clock
  await page.route('**/api/share/td*', route => route.fulfill({ json:{
    activity_id:'td:0Z37', type:'train', name:'0Z37', badge:'LOCO MOVE', subtitle:'Train describer • BRINKLOW', status:'TD live • next berth unresolved',
    operator:'DB Cargo UK', next_stop:'Rugby', near:'Near Brinklow', location:'Southern Britain', delay_minutes:0,
    what_it_is:'A locomotive running on its own, with no passengers or wagons, usually to or from a depot or its next job.',
    code_note:'0Z37 is its signalling code: 0 means a light locomotive, Z a one-off move not in the regular timetable.',
    how_seen:'Railway signalling, position estimated between signals', source:'Network Rail TD',
    freshness:new Date(pageNow-3*60000).toISOString(), sensitive:false, safety_note:'No precise coordinates are included on this public page.', open_url:'/?open=td%3A0Z37' } }));
  await page.goto('/live/td%3A0Z37');
  const card = page.locator('#share-card');
  await expect(card.locator('.subtitle')).toHaveText('Near Brinklow');
  await expect(card.locator('.seen')).toHaveText('Last seen 3 min ago');
  await expect(card).toContainText('On time');
  await expect(card).toContainText('Rugby');
  await expect(card).toContainText('DB Cargo UK');
  await expect(card).toContainText('A locomotive running on its own');
  await expect(card).toContainText('0 means a light locomotive');
  await expect(card).not.toContainText('berth');
  await expect(card).not.toContainText('Train describer');
  await expect(card.locator('.open-radar')).toHaveText('Show it on the live map');
  await expect(card.locator('.open-radar')).toHaveAttribute('href','/?open=td%3A0Z37');
});

test('a link to the app with ?open= shows that train', async ({ page }) => {
  await page.goto('/?open=train-live-1');
  await expect(page.locator('#detail-title')).toContainText('London Express', { timeout:8000 });
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed','true');
  expect(new URL(page.url()).search).toBe('');
});

test('identical live snapshots do not churn existing map markers', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('[data-layer-toggle="bus"]').click();
  await page.locator('.useful-details summary').click();
  await page.locator('#place-mode').selectOption('all');
  await expect(page.locator('.place-marker')).toHaveCount(2);
  const before = await page.evaluate(() => ({
    applied: window.__radarDiagnostics.appliedSnapshots,
    moves: window.__leafletStats.markerMoves,
    icons: window.__leafletStats.markerIconUpdates,
    tooltips: window.__leafletStats.markerTooltipUpdates,
    clusters: window.__leafletStats.clusterRefreshes,
  }));
  await page.evaluate(() => window.__fixtureSocket.onmessage({ data: JSON.stringify(window.__fixtureSnapshot) }));
  await expect.poll(() => page.evaluate(() => window.__radarDiagnostics.appliedSnapshots)).toBe(before.applied + 1);
  const after = await page.evaluate(() => ({
    moves: window.__leafletStats.markerMoves,
    icons: window.__leafletStats.markerIconUpdates,
    tooltips: window.__leafletStats.markerTooltipUpdates,
    clusters: window.__leafletStats.clusterRefreshes,
  }));
  expect(after).toEqual({ moves:before.moves, icons:before.icons, tooltips:before.tooltips, clusters:before.clusters });
});

test('source-health-only deltas skip the map render path', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(page.locator('.marker-bubble')).toHaveCount(2);
  // Marker clustering refreshes on its own animation frame after the markers
  // appear. Let that initial render settle before measuring the no-op delta.
  await page.waitForTimeout(250);
  const before = await page.evaluate(() => ({
    applied:window.__radarDiagnostics.appliedSnapshots,
    unchanged:window.__radarDiagnostics.unchangedSnapshots,
    activityRevision:document.querySelector('#map').dataset.activityCount,
    moves:window.__leafletStats.markerMoves,
    icons:window.__leafletStats.markerIconUpdates,
    tooltips:window.__leafletStats.markerTooltipUpdates,
    clusters:window.__leafletStats.clusterRefreshes,
  }));
  await page.evaluate(() => window.__fixtureSocket.onmessage({data:JSON.stringify({
    activity_delta:{upsert:[],remove:[]},
    stream:{transport:'delta',upserts:0,removals:0,selected_types:['train']},
    sources:window.__fixtureSnapshot.sources,
    summary:window.__fixtureSnapshot.summary,
  })}));
  await expect.poll(() => page.evaluate(() => window.__radarDiagnostics.appliedSnapshots)).toBe(before.applied+1);
  const after = await page.evaluate(() => ({
    unchanged:window.__radarDiagnostics.unchangedSnapshots,
    activityRevision:document.querySelector('#map').dataset.activityCount,
    moves:window.__leafletStats.markerMoves,
    icons:window.__leafletStats.markerIconUpdates,
    tooltips:window.__leafletStats.markerTooltipUpdates,
    clusters:window.__leafletStats.clusterRefreshes,
  }));
  expect(after).toEqual({
    unchanged:before.unchanged+1,
    activityRevision:before.activityRevision,
    moves:before.moves,
    icons:before.icons,
    tooltips:before.tooltips,
    clusters:before.clusters,
  });
});

test('live stream deltas update vehicles and hold transient train omissions without flicker', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('[data-layer-toggle="bus"]').click();
  await expect(page.locator('.marker-bubble')).toHaveCount(3);
  await waitForViewportRequestsToSettle(page);
  const movesBefore = await page.evaluate(() => window.__leafletStats.markerMoves);
  await page.evaluate(() => {
    const bus = window.__fixtureSnapshot.activities.find(row => row.id === 'bus-live-7');
    window.__fixtureSocket.onmessage({data:JSON.stringify({
      activity_delta:{
        upsert:[{...bus,name:'Bus 7 refreshed',lat:Number(bus.lat)+0.01,last_updated:'2026-10-05T10:02:00Z'}],
        remove:['train-live-1'],
      },
      stream:{transport:'delta',upserts:1,removals:1,selected_types:['train','bus']},
      sources:window.__fixtureSnapshot.sources,
      summary:window.__fixtureSnapshot.summary,
    })});
  });
  // A single missing rail delta is not proof that the train ended. Keep the
  // existing marker during the grace period so a feed hand-off cannot blink.
  await expect(page.locator('.marker-bubble')).toHaveCount(3);
  const retainedTrain = await page.evaluate(() => window.__fixtureSnapshot.activities.find(row => row.id === 'train-live-1'));
  await page.evaluate(train => {
    window.__fixtureSocket.onmessage({data:JSON.stringify({
      activity_delta:{upsert:[train],remove:[]},
      stream:{transport:'delta',upserts:1,removals:0,selected_types:['train','bus']},
      sources:window.__fixtureSnapshot.sources,
      summary:window.__fixtureSnapshot.summary,
    })});
  }, retainedTrain);
  await expect(page.locator('.marker-bubble')).toHaveCount(3);
  await expect(page.locator('#map')).toHaveAttribute('data-activity-count','3');
  await expect.poll(() => page.evaluate(() => window.__leafletStats.markerMoves)).toBeGreaterThan(movesBefore);
});

test('trains sharing one station coordinate remain separately rendered', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-zoom','10');
  const trainToggle=page.locator('[data-layer-toggle="train"]');
  if(await trainToggle.getAttribute('aria-pressed')!=='true')await trainToggle.click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const first=window.__fixtureSnapshot.activities.find(row=>row.id==='train-live-1');
    const second={...first,id:'train-live-same-station',name:'1C17 St Pancras',headcode:'1C17',train_uid:'C1C17'};
    window.__fixtureSocket.onmessage({data:JSON.stringify({
      ...window.__fixtureSnapshot,
      activities:[first,second],
      stream:{...window.__fixtureSnapshot.stream,selected_types:['train']},
    })});
  });
  await expect(page.locator('.marker-wrap-train.train-coincident-wrap')).toHaveCount(2);
  await expect(page.locator('.train-coincident-index')).toHaveText(['1','2']);
  await expect(page.locator('#map')).toHaveAttribute('data-coincident-train-groups','1');
  await expect(page.locator('#map')).toHaveAttribute('data-separated-train-markers','2');
});

test('trains move smoothly along mapped railway timing points between confirmed reports', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#detail-placeboard .board-clickable').first().click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-zoom','12');
  await expect(page.locator('#map')).toHaveAttribute('data-map-context','rail');
  await expect(page.locator('.train-published-route-guide')).toHaveCount(1);
  await expect(page.locator('#map')).toHaveAttribute('data-selected-rail-route-points','5');
  await expect(page.locator('#map')).toHaveAttribute('data-selected-rail-route-coverage','100');
  await expect(page.locator('#map')).toHaveAttribute('data-selected-rail-leg-points','0');
  // The route is drawn on the map; no explanatory text box accompanies it.
  await expect(page.locator('#detail-trail')).toHaveClass(/hidden/);
  await page.evaluate(() => {
    let now=Date.parse('2026-10-05T10:01:20Z');
    Date.now=()=>now+=1000;
    const train=window.__fixtureSnapshot.activities.find(row=>row.id==='train-live-1');
    window.__fixtureSocket.onmessage({data:JSON.stringify({
      ...window.__fixtureSnapshot,
      activities:window.__fixtureSnapshot.activities.map(row=>row.id==='train-live-1'?{
        ...train,
        reported_lat:52.04,
        reported_lon:-0.76,
        next_lat:52.08,
        next_lon:-0.70,
        estimate_path:[[52.04,-0.76],[52.055,-0.742],[52.08,-0.70]],
        next_location:'Bletchley',
        estimate_started_at:'2026-10-05T10:01:00Z',
        estimate_due_at:'2026-10-05T10:03:00Z',
      }:row),
    })});
  });
  await expect(page.locator('.marker-wrap-train .marker-estimated')).toHaveCount(1);
  await expect(page.locator('#map')).toHaveAttribute('data-selected-rail-route-points','5');
  await expect(page.locator('#map')).toHaveAttribute('data-selected-rail-leg-points','3');
  await expect(page.locator('#map')).toHaveAttribute('data-selected-rail-route-arrows','2');
  await expect(page.locator('.train-published-route-guide')).toHaveCount(1);
  await expect(page.locator('.train-live-leg-guide')).toHaveCount(1);
  await expect(page.locator('.train-live-leg-progress')).toHaveCount(1);
  await expect(page.locator('.train-route-arrow')).toHaveCount(2);
  await expect(page.locator('#detail-trail')).toHaveClass(/hidden/);
  const progressBefore=Number(await page.locator('#map').getAttribute('data-selected-rail-route-progress'));
  const movesBefore=await page.evaluate(()=>window.__leafletStats.markerMoves);
  await page.waitForTimeout(3500);
  await expect.poll(()=>page.evaluate(()=>window.__leafletStats.markerMoves)).toBeGreaterThan(movesBefore);
  await expect.poll(async()=>Number(await page.locator('#map').getAttribute('data-selected-rail-route-progress'))).toBeGreaterThan(progressBefore);
  await page.locator('.marker-wrap-train').first().evaluate(el=>el.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true})));
  await expect(page.locator('.activity-tooltip')).toContainText('Estimated between reports');
  await page.locator('#detail-close').click();
  await expect(page.locator('.train-published-route-guide')).toHaveCount(0);
  await expect(page.locator('.train-live-leg-guide')).toHaveCount(0);
  await expect(page.locator('.train-route-arrow')).toHaveCount(0);
});

test('a 2,500-object streamed view refreshes incrementally', async ({ page }) => {
  await page.locator('[data-layer-toggle="bus"]').click();
  // Let the fixture's viewport response settle before injecting the synthetic
  // performance snapshot, otherwise its zero-delay response can legitimately
  // arrive afterwards and overwrite the test payload.
  await expect(page.locator('#map')).toHaveAttribute('data-activity-count','1');
  await page.evaluate(() => {
    const template = window.__fixtureSnapshot.activities.find(row => row.id === 'bus-live-7');
    const next = structuredClone(window.__fixtureSnapshot);
    next.activities = Array.from({length:2500}, (_,index) => ({
      ...template,
      id:`perf-bus-${index}`,
      name:`Performance bus ${index}`,
      lat:51.5+(index%50)*0.006,
      lon:-1.5+Math.floor(index/50)*0.006,
    }));
    window.__largeFixtureSnapshot = next;
    window.__fixtureSocket.onmessage({data:JSON.stringify(next)});
  });
  await expect(page.locator('#map')).toHaveAttribute('data-activity-count','2500',{timeout:10000});
  const before = await page.evaluate(() => ({
    applied:window.__radarDiagnostics.appliedSnapshots,
    moves:window.__leafletStats.markerMoves,
    icons:window.__leafletStats.markerIconUpdates,
    tooltips:window.__leafletStats.markerTooltipUpdates,
    clusters:window.__leafletStats.clusterRefreshes,
  }));
  await page.evaluate(() => window.__fixtureSocket.onmessage({data:JSON.stringify(window.__largeFixtureSnapshot)}));
  await expect.poll(() => page.evaluate(() => window.__radarDiagnostics.appliedSnapshots)).toBe(before.applied+1);
  const after = await page.evaluate(() => ({
    moves:window.__leafletStats.markerMoves,
    icons:window.__leafletStats.markerIconUpdates,
    tooltips:window.__leafletStats.markerTooltipUpdates,
    clusters:window.__leafletStats.clusterRefreshes,
    renderMs:window.__radarDiagnostics.lastRenderMs,
  }));
  expect(after).toMatchObject({moves:before.moves,icons:before.icons,tooltips:before.tooltips,clusters:before.clusters});
  expect(after.renderMs).toBeLessThan(500);
});

test('Useful map layers can independently turn trains and buses off and on', async ({ page }) => {
  await expect(page.locator('.marker-bubble')).toHaveCount(0);
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed', 'false');
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#map')).toHaveAttribute('data-map-context','rail');
  await expect(page.locator('.marker-bubble.marker-train')).toHaveCount(2);
  await expect(page.locator('.marker-bubble.marker-bus')).toHaveCount(0);
  await page.locator('[data-layer-toggle="bus"]').click();
  await expect(page.locator('.marker-bubble.marker-bus')).toHaveCount(1);
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(page.locator('.marker-bubble.marker-train')).toHaveCount(0);
  await expect(page.locator('#map')).toHaveAttribute('data-map-context','standard');
  const viewport = await page.evaluate(() => window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1));
  expect(viewport.types).toEqual(['bus']);
});

test('the operator filter (raw feed codes) is not offered and Clear all resets layers', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  await expect(page.locator('.aerial-aircraft')).toHaveCount(3);
  await page.locator('.useful-details summary').click();
  await expect(page.locator('#operator-filter')).toBeHidden();
  await page.locator('#clear-map').click();
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('.aerial-aircraft')).toHaveCount(0);
});

test('duplicate controls are removed and Clear map turns every overlay off', async ({ page }) => {
  await expect(page.locator('#bus-heat')).toHaveCount(0);
  await expect(page.locator('#weather-radar')).toHaveCount(0);
  await expect(page.locator('#traffic-mode')).toHaveCount(0);
  await expect(page.locator('#open-plus-lab')).toHaveCount(0);
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.useful-details summary').click();
  await page.locator('#place-mode').selectOption('all');
  await page.locator('#traffic-quick').click();
  await expect(page.locator('.marker-bubble')).toHaveCount(2);
  await expect(page.locator('.place-marker')).toHaveCount(2);
  await page.locator('#clear-map').click();
  await expect(page.locator('.marker-bubble')).toHaveCount(0);
  await expect(page.locator('.place-marker')).toHaveCount(0);
  await expect(page.locator('#traffic-quick')).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('#place-mode')).toHaveValue('off');
});

test('weak extras are gone from the panels', async ({ page }) => {
  await page.locator('.workspace-tab[data-workspace="settings"]').click();
  await expect(page.locator('#heat-mode')).toBeHidden();
  await expect(page.locator('[data-layer-toggle="roadfeature"]')).toBeHidden();
  await expect(page.locator('[data-workspace-panel="settings"]')).not.toContainText('Advanced access');
  await page.locator('.workspace-tab[data-workspace="live"]').click();
  await expect(page.locator('#add-watch-zone')).toBeHidden();
  await expect(page.locator('.passing-next-card')).toBeHidden();
  await expect(page.locator('.commute-alert-card')).toBeHidden();
  await expect(page.locator('#saved-places-count')).toBeHidden();
  await page.locator('.workspace-tab[data-workspace="useful"]').click();
  await expect(page.locator('#near-centre')).toBeHidden();
  await expect(page.locator('#nearby-summary')).toBeHidden();
  await expect(page.locator('.layer-switch-grid')).not.toContainText(/[\u{1F300}-\u{1FAFF}\u2600-\u27BF]/u);
});

test('removed CARTO dark mode falls back to the standard map', async ({ page }) => {
  const cartoRequests = [];
  page.on('request', request => {
    if (request.url().includes('cartocdn.com')) cartoRequests.push(request.url());
  });
  await page.evaluate(() => localStorage.setItem('ukRadarMapMode', 'dark'));
  await page.reload();
  await expect(page.locator('#map')).toHaveAttribute('data-map-mode', 'standard');
  await expect(page.locator('.map-mode[data-map-mode="dark"]')).toHaveCount(0);
  expect(cartoRequests).toEqual([]);
});

test('Useful and Cool workspaces alter visible layers and render Cool badges', async ({ page }) => {
  await expect(page.locator('.marker-bubble')).toHaveCount(0);
  await page.getByRole('button', { name: /Discover/ }).click();
  await expect(page.locator('body')).toHaveAttribute('data-experience', 'cool');
  await expect(page.locator('.cool-map-marker')).toHaveCount(2);
  await expect(page.locator('.cool-marker-wrap').first()).toHaveAttribute('data-clustered','false');
  await expect(page.locator('#cool-summary .discover-row').filter({ hasText: 'Steam locomotive' }).first()).toBeVisible();
  // Cool markers come from the national Cool feed, not only the current
  // viewport snapshot, so zooming out cannot silently discard a category.
  await expect(page.locator('.aerial-map-marker')).toHaveCount(3);
  await expect(page.locator('.aerial-marker-wrap').first()).toHaveAttribute('data-clustered', 'false');
  await expect(page.locator('#ranking-title')).toHaveText(/Cool stuff now • UK/);
  await expect(page.locator('#ranking')).toContainText('RAF AWACS SENTINEL01');
  await expect(page.locator('#ranking')).toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('.ticker-items')).toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('#cool-summary')).not.toContainText('matched live');
  await expect(page.locator('#cool-summary .cool-find-row')).toHaveCount(4);
  await expect(page.locator('#cool-summary .discover-row')).toHaveCount(5);
  await page.locator('.cool-filter[data-type="cool-rescue"]').click();
  await expect(page.locator('#ranking')).toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('#cool-summary')).not.toContainText('LIVE INCIDENT');
});

test('clicking an active Cool category turns Cool map markers off', async ({ page }) => {
  await page.getByRole('button', { name: /Discover/ }).click();
  await page.locator('.cool-filter[data-type="cool"]').click();
  await expect(page.locator('#cool-map-switch')).toContainText('Map off');
  await expect(page.locator('.cool-map-marker')).toHaveCount(0);
});

test('Cool rail offers only source-supported subfilters and Best Spot focuses the selected live movement', async ({ page }) => {
  await page.getByRole('button', { name: /Discover/ }).click();
  await page.locator('.cool-filter[data-type="cool-rail"]').click();
  await expect(page.locator('#cool-rail-subfilters')).toBeVisible();
  await expect(page.locator('[data-cool-rail-subtype="steam"]')).toHaveCount(0);
  await expect(page.locator('[data-cool-rail-subtype="special"]')).toHaveText('Special passenger');
  await expect(page.locator('.cool-map-marker')).toHaveCount(1);
  await expect(page.locator('.cool-map-marker')).toContainText('STEAM');
  await page.locator('.cool-filter[data-type="cool"]').click();
  await page.locator('[data-cool-top]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-zoom', '11');
  await expect(page.locator('#detail-title')).toContainText('RAF TYPHOON');
});

test('Cool map can be switched completely off', async ({ page }) => {
  await page.getByRole('button', { name: /Discover/ }).click();
  await expect(page.locator('.cool-map-marker')).toHaveCount(2);
  await expect(page.locator('.aerial-map-marker')).toHaveCount(3);
  await page.locator('.cool-map-switch[data-type="cool-off"]').click();
  await expect(page.locator('.cool-map-marker')).toHaveCount(0);
  await expect(page.locator('.aerial-map-marker')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => window.__fixtureSocketMessages.at(-1)?.types)).toEqual([]);
});

test('Cool rail detail shows an attributed and honestly labelled reference image', async ({ page }) => {
  let mediaCalls = 0;
  page.on('request', request => {
    if (decodeURIComponent(new URL(request.url()).pathname).includes('/api/activity/steam-live-1/media')) mediaCalls += 1;
  });
  await page.getByRole('button', { name: /Discover/ }).click();
  await page.locator('.cool-filter[data-type="cool-rail"]').click();
  await page.locator('[data-cool-rail-subtype="special"]').click();
  await page.locator('.cool-map-marker').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('Flying Scotsman Railtour');
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src', 'https://upload.wikimedia.org/fixture-steam.jpg');
  await expect(page.locator('#detail-photo .image-truth')).toContainText('Photo of this vehicle or class — not live');
  await expect(page.locator('#detail-photo')).toContainText('Fixture Photographer');
  await expect(page.locator('#detail-photo')).toContainText('CC BY-SA 4.0');
  await page.evaluate(() => window.__fixtureSocket.onmessage({ data: JSON.stringify(window.__fixtureSnapshot) }));
  await page.waitForTimeout(50);
  expect(mediaCalls).toBe(1);
});

test('Discover renders its live view and private Field Guide without an extra location request', async ({ page }) => {
  await page.locator('[data-workspace="cool"]').click();
  await expect(page.locator('[data-cool-view-panel="now"]')).toHaveClass(/active/);
  await expect(page.locator('#discovery-now-status')).toContainText('0/3');

  await expect(page.locator('[data-cool-view="missions"]')).toHaveCount(0);

  await page.locator('[data-cool-view="guide"]').click();
  await expect(page.locator('[data-cool-view-panel="guide"]')).toHaveClass(/active/);
  await expect(page.locator('#field-guide-progress')).toContainText('0 of 3 discovered');
  await expect(page.locator('#field-guide-grid .field-guide-card')).toHaveCount(3);
  await expect(page.locator('#field-guide-grid')).toContainText('Steam Train');
  await expect(page.locator('#field-guide-grid .field-guide-card.locked')).toHaveCount(3);
  expect(await page.evaluate(() => window.__geolocationRequests)).toBe(1);
});

test('a live steam discovery enters the private Field Guide and survives reload', async ({ page }) => {
  let claimCalls = 0;
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/discoveries/claim') claimCalls += 1;
  });

  await page.locator('[data-workspace="cool"]').click();
  await page.locator('.cool-filter[data-type="cool-rail"]').click();
  await page.locator('[data-cool-rail-subtype="special"]').click();
  await page.locator('.cool-map-marker').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('Flying Scotsman Railtour');
  await expect(page.locator('#detail-discovery')).toHaveClass(/hidden/);
  await page.locator('#save-discovery').click();
  await expect(page.locator('#save-discovery')).toHaveText('Field Guide ✓');
  expect(claimCalls).toBe(1);

  await page.locator('[data-cool-view="guide"]').click();
  const steamCard = page.locator('#field-guide-grid .field-guide-card').filter({ hasText:'Steam Train' });
  await expect(steamCard).toHaveClass(/discovered/);
  await expect(steamCard).toContainText('FOUND');
  await expect(steamCard).toContainText('Northern England and Wales');
  await expect(page.locator('#field-guide-progress')).toContainText('1 of 3 discovered');
  await expect(page.locator('#field-guide-nav-count')).toHaveText('1 found');

  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('ukRadarDiscoveryCollectionV1')));
  expect(stored).toHaveLength(1);
  expect(stored[0]).toMatchObject({ id:'rail:steam', activity_id:'steam-live-1', broad_region:'Northern England and Wales' });
  expect(stored[0]).not.toHaveProperty('lat');
  expect(stored[0]).not.toHaveProperty('lon');
  expect(await page.evaluate(() => window.__geolocationRequests)).toBe(1);

  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.locator('[data-workspace="cool"]').click();
  await page.locator('[data-cool-view="guide"]').click();
  await expect(page.locator('#field-guide-grid .field-guide-card').filter({ hasText:'Steam Train' })).toHaveClass(/discovered/);
  await expect(page.locator('#field-guide-progress')).toContainText('1 of 3 discovered');
  expect(await page.evaluate(() => Number(sessionStorage.getItem('__radarTestGeolocationRequests') || 0))).toBe(2);
});

test('a rejected discovery claim cannot unlock the Field Guide', async ({ page }) => {
  let rejectedClaims = 0;
  await page.route('**/api/discoveries/claim', route => {
    rejectedClaims += 1;
    return route.fulfill({
      status: 422,
      json: { detail:{ code:'activity_not_live', message:'This live activity could not be verified, so nothing was saved.' } },
    });
  });

  await page.locator('[data-workspace="cool"]').click();
  await page.locator('.cool-filter[data-type="cool-rail"]').click();
  await page.locator('[data-cool-rail-subtype="special"]').click();
  await page.locator('.cool-map-marker').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await page.locator('#save-discovery').click();

  await expect(page.locator('#watch-toast')).toContainText('nothing was saved');
  await expect(page.locator('#save-discovery')).toBeEnabled();
  await expect(page.locator('#save-discovery')).toHaveText('Save to Field Guide');
  expect(rejectedClaims).toBe(1);
  expect(await page.evaluate(() => localStorage.getItem('ukRadarDiscoveryCollectionV1'))).toBeNull();

  await page.locator('[data-cool-view="guide"]').click();
  await expect(page.locator('#field-guide-progress')).toContainText('0 of 3 discovered');
  await expect(page.locator('#field-guide-grid .field-guide-card.discovered')).toHaveCount(0);
});

test('an earlier Field Guide find stays unlocked without weekly mission revalidation', async ({ page }) => {
  await page.evaluate(record => {
    localStorage.setItem('ukRadarDiscoveryCollectionV1', JSON.stringify([record]));
  }, {
    id:'rail:steam', key:'rail:steam', activity_id:'old-steam-live', category:'rail', subtype:'steam', title:'Steam Train',
    explanation:'Steam locomotive.', icon:'♨', confidence:'high', freshness:'live', observed_at:'2026-09-28T10:00:00Z',
    discovered_at:'2026-09-28T10:01:00Z', first_discovered_at:'2026-09-28T10:01:00Z', broad_region:'Northern England and Wales',
    source:'Network Rail TRUST', identity_level:'type', identity_note:'The movement type was classified.', weeks:['2026-W40'],
  });
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });

  let claimCalls = 0;
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/discoveries/claim') claimCalls += 1;
  });
  await page.locator('[data-workspace="cool"]').click();
  await page.locator('[data-cool-view="guide"]').click();
  const steamCard = page.locator('#field-guide-grid .field-guide-card').filter({ hasText:'Steam Train' });
  await expect(steamCard).toHaveClass(/discovered/);
  await expect(steamCard).toContainText('FOUND');
  await expect(page.locator('#field-guide-progress')).toContainText('1 of 3 discovered');

  await page.locator('[data-cool-view="now"]').click();
  await page.locator('.cool-filter[data-type="cool-rail"]').click();
  await page.locator('[data-cool-rail-subtype="special"]').click();
  await page.locator('.cool-map-marker').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#save-discovery')).toHaveText('Field Guide ✓');
  await page.locator('#save-discovery').click();
  expect(claimCalls).toBe(0);

  await page.locator('[data-cool-view="guide"]').click();
  await expect(page.locator('#field-guide-grid .field-guide-card.discovered')).toHaveCount(1);
  await expect(page.locator('#field-guide-progress')).toContainText('1 of 3 discovered');
  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem('ukRadarDiscoveryCollectionV1')));
  expect(stored).toHaveLength(1);
  expect(stored[0].weeks).toEqual(['2026-W40']);
  expect(stored[0].first_discovered_at).toBe('2026-09-28T10:01:00Z');
});

test('Live cameras appear on the map and open their current provider media', async ({ page }) => {
  // Next to Live traffic in the Map tools, not tucked away in More.
  await expect(page.locator('[data-workspace-panel="settings"] #live-webcams')).toHaveCount(0);
  await page.locator('.workspace-tab[data-workspace="useful"]').click();
  await page.locator('#live-webcams').click();
  await expect(page.locator('#live-webcams')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.camera-map-marker')).toHaveCount(1);
  await page.locator('.camera-map-marker').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('M1 Junction 14 Camera');
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src', 'https://jamcams.tfl.gov.uk/fixture.jpg');
});

test('road incidents show the closest verified camera', async ({ page }) => {
  await page.locator('[data-layer-toggle="incident"]').click();
  await page.locator('.marker-bubble.marker-incident').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('M1 collision');
  await expect(page.locator('#detail-photo')).toContainText('Nearest live camera');
  await expect(page.locator('#detail-photo')).toContainText('1.2 km');
});

test('live detail can track a moving object across snapshots', async ({ page }) => {
  await page.locator('[data-layer-toggle="bus"]').click();
  await page.locator('.marker-bubble.marker-bus').evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await page.locator('#track-activity').click();
  await expect(page.locator('#track-activity')).toHaveAttribute('aria-pressed', 'true');
  const before = await page.evaluate(() => window.__radarDiagnostics.appliedSnapshots);
  await page.evaluate(() => {
    for (const [lat,lon] of [[52.21,-0.49],[52.23,-0.47],[52.25,-0.45]]) {
      const next = structuredClone(window.__fixtureSnapshot);
      const bus = next.activities.find(row => row.id === 'bus-live-7');
      bus.lat = lat; bus.lon = lon; bus.last_updated = '2026-10-05T10:02:00Z';
      window.__fixtureSocket.onmessage({ data: JSON.stringify(next) });
    }
  });
  await expect(page.locator('#map')).toHaveAttribute('data-map-center', '52.25,-0.45');
  await expect.poll(() => page.evaluate(() => window.__radarDiagnostics.appliedSnapshots)).toBe(before + 1);
  await expect(page.locator('#track-activity')).toContainText('Stop following');
});

test('aircraft media is not restarted on every live snapshot', async ({ page }) => {
  let mediaCalls = 0;
  page.on('request', request => { if (request.url().includes('/api/activity/aircraft-live-1/media')) mediaCalls += 1; });
  await page.getByRole('button', { name: /Discover/ }).click();
  await page.locator('.aerial-map-marker.aerial-aircraft').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-panel')).toContainText('Eurofighter Typhoon');
  // No generic type or Wikimedia fallback appears when the exact airframe has no photo.
  await expect(page.locator('#detail-photo img')).toHaveCount(0);
  await page.evaluate(() => window.__fixtureSocket.onmessage({ data: JSON.stringify(window.__fixtureSnapshot) }));
  await page.waitForTimeout(50);
  expect(mediaCalls).toBe(1);
});

test('known military helicopters do not show a generic photo when exact lookup is unavailable', async ({ page }) => {
  await page.route('**/api/activity/aircraft-live-2/media', route => route.fulfill({ json:{ ok:false, reason:'no_match' } }));
  await page.locator('[data-layer-toggle="aircraft"]').click();
  const marker=page.locator('.aircraft-shape-chinook').first();
  await expect(marker).toBeVisible();
  await expect(marker.locator('.rotorcraft-silhouette .map-chinook-svg')).toHaveCount(1);
  await expect(marker.locator('.map-chinook-svg .ac-rotor')).toHaveCount(1);
  await expect(marker.locator('.map-chinook-svg .ac-body')).toHaveCount(1);
  await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-photo img')).toHaveCount(0);
  await expect(page.locator('#detail-insight')).toBeVisible();
  await expect(page.locator('#detail-insight')).toContainText('military heavy-lift helicopter');
});

test('aircraft markers distinguish airliners and propeller aircraft with the type below', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  await page.evaluate(() => {
    const next=structuredClone(window.__fixtureSnapshot);
    const aircraft=next.activities.find(row=>row.id==='aircraft-live-1');
    Object.assign(aircraft,{name:'RYR1YY',vehicle_ref:'EI-TEST',aircraft_model:'Boeing 737-8200',aircraft_type_code:'B38M'});
    window.__fixtureSocket.onmessage({data:JSON.stringify(next)});
  });
  const airliner=page.locator('.aircraft-shape-airliner').filter({ hasText:'737-8200' }).first();
  await expect(airliner).toBeVisible();
  await expect(airliner.locator('.map-airliner-svg')).toHaveCount(1);
  await page.evaluate(() => {
    const next=structuredClone(window.__fixtureSnapshot);
    const aircraft=next.activities.find(row=>row.id==='aircraft-live-1');
    Object.assign(aircraft,{name:'G-TALR',vehicle_ref:'G-TALR',aircraft_model:'Cessna 152',aircraft_type_code:'C152'});
    window.__fixtureSocket.onmessage({data:JSON.stringify(next)});
  });
  const propeller=page.locator('.aircraft-shape-propeller').filter({hasText:'CESSNA 152'}).first();
  await expect(propeller).toBeVisible();
  await expect(propeller.locator('.map-propeller-svg')).toHaveCount(1);
  await expect(propeller.locator('.ac-rotor')).toHaveCount(1);
  await expect(propeller.locator('.motion-speed')).toHaveCount(0);
});

test('Westland Lynx aircraft use a helicopter silhouette and identity', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  // Wait for the initial snapshot render before injecting the replacement.
  // On a busy full-suite worker the startup snapshot can otherwise arrive
  // after this test's synthetic WebSocket message and overwrite it.
  await expect(page.locator('.aircraft-shape-fastjet').first()).toBeVisible();
  await page.evaluate(() => {
    const next=structuredClone(window.__fixtureSnapshot);
    const aircraft=next.activities.find(row=>row.id==='aircraft-live-1');
    Object.assign(aircraft,{name:'ARMYLYNX',vehicle_ref:'XZ179',aircraft_model:'Westland Lynx AH.7',aircraft_type_code:'LYNX'});
    window.__fixtureSocket.onmessage({data:JSON.stringify(next)});
  });
  const lynx=page.locator('.aircraft-shape-helicopter').filter({hasText:'LYNX'}).first();
  await expect(lynx).toBeVisible();
  await expect(lynx.locator('.map-helicopter-svg')).toHaveCount(1);
  await expect(lynx.locator('.ac-rotor')).toHaveCount(1);
});

test('selected aircraft draws its trail on the map and shows compact facts', async ({ page }) => {
  await page.goto('/');
  await page.locator('[data-workspace="cool"]').click();
  await page.locator('.aerial-map-marker.aerial-aircraft').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-trail')).toHaveClass(/hidden/);
  await page.evaluate(() => {
    const moved={
      ...window.__fixtureSnapshot,
      activities:window.__fixtureSnapshot.activities.map(row=>row.id==='aircraft-live-1'?{...row,lat:Number(row.lat)+0.18,lon:Number(row.lon)+0.12,last_updated:'2026-10-05T10:01:00Z'}:row),
    };
    window.__fixtureSocket.onmessage({data:JSON.stringify(moved)});
  });
  // The trail is drawn on the map; there is no explanatory text box.
  await expect(page.locator('#detail-trail')).toHaveClass(/hidden/);
  await expect(page.locator('#detail-fields [data-field="Altitude"]')).toBeVisible();
  await expect(page.locator('#detail-fields [data-field="ICAO type code"]')).toBeHidden();
  // At most two short rows of chips, whatever the font (the old boxes were ~200 px).
  expect((await page.locator('#detail-fields').boundingBox()).height).toBeLessThan(90);
  await expect(page.locator('#map')).toHaveAttribute('data-selected-trail-points','2');
});

test('What’s above me loads nearby aircraft and can be turned off', async ({ page }) => {
  await page.goto('/');
  let nearbyRequests=0;
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/aircraft/nearby')nearbyRequests+=1;});
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('[data-job="planes"]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','54.11,-2.45');
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','true');
  // One tap answers the question: a list of the planes above you, nearest first.
  await expect(page.locator('#detail-title')).toHaveText(/Planes above you|Nearest planes/);
  await expect(page.locator('[data-job-plane]').first()).toBeVisible();
  await expect(page.locator('.aerial-map-marker').first()).toBeVisible();
  await expect.poll(()=>nearbyRequests,{timeout:15000}).toBeGreaterThan(1);
  await page.locator('#nearby-note .nearby-note-close').click();
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','false');
});

test('What’s above me keeps retained aircraft visible when its point lookup fails', async ({ page }) => {
  await page.setViewportSize({width:375,height:812});
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.route('**/api/aircraft/nearby**', route => route.fulfill({
    contentType:'application/json',
    body:JSON.stringify({
      checked:1,
      radius_miles:50,
      degraded:true,
      source:'Retained UK aircraft feed',
      warning:'The live point lookup is retrying; showing the latest retained aircraft positions.',
      activities:[{
        kind:'vehicle',id:'aircraft:retained',type:'aircraft',name:'TEST01',
        lat:54.11,lon:-2.45,altitude_ft:12000,aircraft_model:'Airbus A320',
        source:'Retained UK aircraft feed',last_updated:'2026-10-05T10:00:58Z',
      }],
    }),
  }));
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('[data-job="planes"]').click();
  await expect(page.locator('#overhead-now small')).toContainText('retained aircraft');
  await expect(page.locator('#overhead-now small')).toContainText('retrying');
  await expect(page.locator('.aerial-map-marker').first()).toBeVisible();
  await expect(page.locator('#detail-title')).toHaveText(/Planes above you|Nearest planes/);
  await expect(page.locator('[data-job="planes"]')).not.toHaveAttribute('aria-busy','true');
  await page.setViewportSize({width:812,height:375});
  await expect(page.locator('.aerial-map-marker').first()).toBeVisible();
});

test('aircraft search landing links open their promised live map views', async ({ page }) => {
  await page.goto('/?view=helicopters');
  await expect(page).toHaveURL('http://radar.test/');
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.aircraft-shape-chinook').first()).toBeVisible();
  await expect(page.locator('.aircraft-shape-fastjet')).toHaveCount(0);

  await page.goto('/?view=military-aircraft');
  await expect(page).toHaveURL('http://radar.test/');
  await expect(page.locator('body')).toHaveAttribute('data-experience','cool');
  await expect(page.locator('.filter[data-type="cool-air"]')).toHaveClass(/active/);
  await expect(page.locator('.aerial-map-marker').first()).toBeVisible();
});

test('what-plane-is-above-me landing link starts the nearby-aircraft lookup', async ({ page }) => {
  await page.addInitScript(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
    getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});},
    watchPosition(){return 1;},clearWatch(){},
  }}));
  await page.goto('/?view=aircraft-near-me');
  await expect(page).toHaveURL('http://radar.test/');
  await expect(page.locator('#overhead-now')).toHaveClass(/active/);
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','54.11,-2.45');
  await expect(page.locator('[data-job-plane]').first()).toBeVisible();
});

test('Discover clears above-me radius and returns to the full UK feed', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('[data-job="planes"]').click();
  await expect(page.locator('#nearby-note')).not.toHaveClass(/hidden/);
  await page.getByRole('button',{name:/Discover/}).click();
  await expect(page.locator('#nearby-note')).toHaveClass(/hidden/);
  await expect(page.locator('#overhead-now')).not.toHaveClass(/active/);
  await expect(page.locator('#cool-summary')).toContainText('RAF AWACS SENTINEL01');
  await expect(page.locator('#cool-summary')).toContainText('RNLI RESCUE CONTEXT');
});

test('left workspace drawer collapses and reopens', async ({ page }) => {
  await page.locator('#workspace-collapse').click();
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await page.getByRole('button', { name: /Discover/ }).click();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
  await expect(page.locator('[data-workspace-panel="cool"]')).toBeVisible();
});

test('Focus map hides both panels and Escape restores them', async ({ page }) => {
  await page.locator('#focus-map').click();
  await expect(page.locator('.v12-layout')).toHaveClass(/focus-mode/);
  await expect(page.locator('#focus-map')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#focus-map')).toContainText('Show panels');
  await page.keyboard.press('Escape');
  await expect(page.locator('.v12-layout')).not.toHaveClass(/focus-mode/);
  await expect(page.locator('#focus-map')).toHaveAttribute('aria-pressed','false');
});

test('searching a location and clicking it moves the map there', async ({ page }) => {
  await page.locator('#search').fill('Leicester');
  await page.locator('#search-go').click();
  // The place itself is the first result.
  await expect(page.locator('#search-results .search-result').first()).toContainText('Leicester city centre');
  await page.getByRole('button',{name:/Leicester city centre/}).click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','52.6369,-1.1398');
});

test('commercial aircraft use operator colours and an exact licensed photo', async ({ page }) => {
  await page.locator('#search').fill('Ryanair');
  await page.locator('#search-go').click();
  await page.getByRole('button',{name:/RYR1YY/}).click();
  await expect(page.locator('#detail-operator')).toContainText('AIRLINE');
  await expect(page.locator('#detail-operator')).toContainText('Ryanair');
  await expect(page.locator('#detail-operator')).toContainText('Boeing 737-8200');
  await expect(page.locator('#detail-subtitle')).toHaveText('London Stansted (STN) → Dublin (DUB)');
  await expect(page.locator('#detail-fields [data-field="Route"]')).toHaveCount(0);
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src','https://t.plnspttrs.net/fixture-ryanair.jpg');
  await expect(page.locator('#detail-photo .image-truth')).toContainText('this aircraft registration');
  await expect(page.locator('#detail-photo')).toContainText('linked thumbnail');
  await expect(page.locator('#detail-photo')).toContainText('Fixture Aviation Photographer');
  await expect(page.locator('#detail-insight')).toBeHidden();
});

test('activity marker hover shows facts and click opens and closes detail', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  const marker = page.locator('.marker-bubble.marker-train').first();
  await marker.evaluate(el => el.closest('.leaflet-marker-icon').dispatchEvent(new MouseEvent('mouseenter')));
  await expect(page.locator('.activity-tooltip')).toContainText('London Express');
  await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-sidebar')).not.toHaveClass(/is-empty/);
  await expect(page.locator('#detail-title')).toContainText('London Express');
  await page.locator('#detail-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
});

test('a selected train that the server no longer holds stops polling and says so', async ({ page }) => {
  const railRequests = [];
  await page.route('**/api/activity/train-live-1/rail', route => {
    railRequests.push(Date.now());
    return route.fulfill({ status: 404, json: { detail: 'Train activity not found' } });
  });
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-intelligence')).toContainText('No longer reporting');
  const settled = railRequests.length;
  // The old code re-requested every 10 seconds, indefinitely.
  await page.waitForTimeout(11500);
  expect(railRequests.length).toBe(settled);
});

test('a selected train merged into another live identity is re-linked to it', async ({ page }) => {
  await page.route('**/api/activity/train-live-1/rail', route => route.fulfill({ status: 404, json: { detail: 'Train activity not found' } }));
  await page.route('**/api/rail/locate?**', route => {
    const url = new URL(route.request().url());
    expect(url.searchParams.get('train_uid')).toBe('C12345');
    return page.evaluate(() => window.__fixtureSnapshot.activities.find(row => row.id === 'train-live-1'))
      .then(train => route.fulfill({ json: { live: true, activity: { ...train, id: 'td:XX:1A23', name: '1A23 relinked' } } }));
  });
  await page.route('**/api/activity/td%3AXX%3A1A23/rail', route => route.fulfill({ json: { activity: 'td:XX:1A23', detail: { available: true, source: 'Live railway and timetable data', runs: [{ headcode: '1A23', origin: 'Milton Keynes Central', destination: 'London Euston', status: 'On time', next_location: 'Watford Junction', calling_points: [{ station: 'Watford Junction', expected: '2026-10-05T10:34:00Z', status: 'Expected' }] }] } } }));
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('1A23 relinked');
  await expect(page.locator('#detail-intelligence')).toContainText('Watford Junction');
});

test('clicked trains and buses load source-backed route intelligence', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-intelligence .train-full-route')).toBeVisible();
  await expect(page.locator('#detail-intelligence')).toContainText('Watford Junction');
  await expect(page.locator('#detail-intelligence')).not.toContainText('Ledburn Junction');

  await page.locator('#detail-close').click();
  await page.locator('[data-layer-toggle="bus"]').click();
  await page.locator('.marker-bubble.marker-bus').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-intelligence .transport-route-head')).toContainText('Route 7 to City Centre');
  await expect(page.locator('#detail-intelligence .bus-lateness')).toHaveText('2 min late');
  await expect(page.locator('#detail-intelligence .bus-stops')).toBeVisible();
  await expect(page.locator('#detail-intelligence')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-intelligence')).toContainText('2 min');
  await expect(page.locator('#detail-operator')).toContainText('Arriva');
  await expect(page.locator('#detail-operator')).toContainText('Route 7');
  // Bus photos are off: a wrong bus misleads more than no photo.
  await expect(page.locator('#detail-photo img')).toHaveCount(0);
});

test('driving planner resolves places and renders a traffic-aware car route', async ({ page }) => {
  await page.locator('#journey-planner').click();
  await expect(page.locator('#journey-dialog')).toBeVisible();
  await expect(page.locator('#journey-dialog')).toContainText('DRIVING ROUTES');
  await expect(page.locator('#journey-mode')).toHaveValue('car');
  await page.locator('#journey-origin').fill('Milton Keynes');
  await page.locator('#journey-destination').fill('London');
  await page.locator('#journey-submit').click();
  await expect(page.locator('#journey-results .journey-issue-summary')).toContainText('1 incident on this route');
  await expect(page.locator('#journey-results')).toContainText('Drive');
  await expect(page.locator('#journey-results')).toContainText('Milton Keynes → London');
  await expect(page.locator('#journey-results')).toContainText('1 hr 6 min');
  await expect(page.locator('#journey-results')).toContainText('M1 lane closure');
  await expect(page.locator('#journey-results')).toContainText('Traffic adds 3 min');
  await page.locator('.journey-show-map').click();
  await expect(page.locator('#journey-dialog')).not.toBeVisible();
  await expect(page.locator('#map')).toHaveAttribute('data-journey-route-points','3');
  await expect(page.locator('#journey-route-clear')).toBeVisible();
  await page.locator('#journey-route-clear').click();
  await expect(page.locator('#map')).not.toHaveAttribute('data-journey-route-points');
});

test('Station Board opens a real fixture station and departure', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
  await expect(page.locator('#detail-placeboard [data-board-tab="departures"]')).toHaveAttribute('aria-selected','true');
  await expect(page.locator('#detail-placeboard')).toContainText('Manchester Piccadilly');
  await expect(page.locator('#detail-placeboard [data-live-id="train-live-1"]')).toHaveClass(/is-live/);
  // Arrivals are one tap away rather than squeezed into a second column.
  await expect(page.locator('#detail-placeboard [data-board-list="arrivals"]')).toBeHidden();
  await page.locator('#detail-placeboard [data-board-tab="arrivals"]').click();
  await expect(page.locator('#detail-placeboard [data-board-list="arrivals"]')).toContainText('Birmingham New Street');
  await page.locator('#detail-placeboard [data-board-tab="departures"]').click();
  await page.locator('#detail-placeboard [data-live-id="train-live-1"]').click();
  await expect(page.locator('#detail-title')).toContainText('London Express');
  await expect(page.locator('#detail-intelligence')).toContainText('Rolling stock');
  await expect(page.locator('#detail-intelligence')).toContainText('Class 43');
  await expect(page.locator('.rolling-stock-card')).toHaveAttribute('data-livery-theme', 'intercity');
  await expect(page.locator('.rolling-stock-card')).toContainText('InterCity');
  await expect(page.locator('.transport-run-card')).toHaveAttribute('data-livery-theme', 'east-midlands');
  await expect(page.locator('#detail-panel')).toHaveClass(/operator-theme-train/);
  await expect(page.locator('#detail-operator')).toContainText('Fixture Rail');
});

test('scheduled station-board train opens its published route without a live marker', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#detail-placeboard [data-train-uid="C99999"]')).toHaveClass(/board-clickable/);
  await page.locator('#detail-placeboard [data-train-uid="C99999"]').click();
  await expect(page.locator('#detail-title')).toContainText('1Z80');
  await expect(page.locator('#detail-intelligence')).toContainText('Coventry');
  await expect(page.locator('#detail-intelligence')).toContainText('Timetable only · not live on the map');
  // Passenger calling points alone are not railway geometry. An unresolved
  // route must never be rendered as straight station-to-station chords.
  await expect(page.locator('.train-published-route-guide')).toHaveCount(0);
  await expect(page.locator('#map')).not.toHaveAttribute('data-selected-rail-route-points');
});

test('Share live page works directly from a selected activity', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  await page.locator('#share-activity').click();
  await expect.poll(()=>page.evaluate(()=>window.__sharedPayload?.url || '')).toContain('/live/train-live-1');
  await expect.poll(()=>page.evaluate(()=>window.__sharedPayload?.text || '')).toContain('London Express');
});

test('clicking a bus stop opens live timetable information', async ({ page }) => {
  await page.locator('.useful-details summary').click();
  await page.locator('#place-mode').selectOption('all');
  const stop = page.locator('.place-marker.place-bus_stop');
  await expect(stop).toBeVisible();
  await stop.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('Live predicted buses');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
});

test('TomTom flow toggle exposes active map state', async ({ page }) => {
  await page.locator('#traffic-quick').click();
  await expect(page.locator('#traffic-quick')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#traffic-style')).toHaveValue('combined');
  await expect(page.locator('#map')).toHaveAttribute('data-traffic', 'combined');
  await expect(page.locator('#traffic-legend')).not.toHaveClass(/hidden/);
});

test('Incidents asks the server for live incidents in view, not a picture overlay', async ({ page }) => {
  await page.locator('[data-layer-toggle="incident"]').click();
  await expect(page.locator('[data-layer-toggle="incident"]')).toHaveAttribute('aria-pressed','true');
  await expect.poll(() => page.evaluate(() => window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types)).toContain('incident');
  await expect(page.locator('#map')).not.toHaveAttribute('data-traffic','incidents');
  await expect(page.locator('#watch-toast')).toContainText('Zoom in to see live incidents');
});

test('mobile viewport keeps core navigation, map, and details usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#map')).toBeVisible();
  await expect(page.locator('#workspace-shell')).toBeVisible();
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-sidebar')).toBeVisible();
  const box = await page.locator('#detail-sidebar').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.height).toBeLessThan(500);
  expect(await page.locator('.transport-run-card').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  // Flat mobile layout: details are a full-width bottom sheet that leaves
  // the upper map visible, and navigation is a bottom tab bar.
  expect(box.width).toBe(390);
  expect(box.y).toBeGreaterThanOrEqual(300);
  const tabBar = await page.locator('.workspace-rail').boundingBox();
  expect(tabBar.height).toBeLessThanOrEqual(72);
  expect(tabBar.y + tabBar.height).toBeGreaterThanOrEqual(840);
  const mapBox = await page.locator('#map').boundingBox();
  expect(mapBox.y + mapBox.height).toBeGreaterThan(820);
  await page.locator('#detail-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
  await page.locator('[data-workspace="cool"]').click();
  expect(await page.locator('.cool-filter').evaluateAll(cards => cards.every(card => card.scrollWidth <= card.clientWidth))).toBe(true);
});

test('tapping bare map on mobile minimizes train details but keeps its route visible', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  await expect(page.locator('#detail-sidebar')).not.toHaveClass(/is-empty/);
  await expect(page.locator('#detail-route-view')).toBeVisible();
  await expect(page.locator('.train-published-route-guide')).toHaveCount(1);
  await page.locator('#detail-sidebar').evaluate(element=>{element.scrollTop=element.scrollHeight;});
  await page.locator('#map').evaluate(element=>element.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  await expect(page.locator('#detail-sidebar')).toHaveClass(/route-view/);
  await expect(page.locator('#detail-sidebar')).toHaveAttribute('data-detail-view','route');
  await expect(page.locator('#detail-route-peek')).toBeVisible();
  await expect(page.locator('.train-published-route-guide')).toHaveCount(1);
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  const peek=await page.locator('#detail-sidebar').boundingBox();
  expect(peek.height).toBeLessThanOrEqual(66);
  await page.locator('#detail-route-restore').click();
  await expect(page.locator('#detail-sidebar')).not.toHaveClass(/route-view/);
  await expect(page.locator('#detail-panel')).toBeVisible();
  await expect(page.locator('.train-published-route-guide')).toHaveCount(1);
  await page.locator('#detail-route-view').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/route-view/);
  await expect.poll(async()=>Number(await page.locator('#map').getAttribute('data-map-zoom'))).toBeGreaterThanOrEqual(8);
  await expect(page.locator('.marker-wrap-train')).toHaveCount(1);
  await page.evaluate(()=>window.__fixtureSocket.onmessage?.({data:JSON.stringify({
    ...window.__fixtureSnapshot,
    activities:window.__fixtureSnapshot.activities.filter(row=>row.type!=='train'),
  })}));
  await expect(page.locator('.train-published-route-guide')).toHaveCount(1);
  await expect(page.locator('.marker-wrap-train')).toHaveCount(1);
  await page.locator('#detail-route-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
  await expect(page.locator('.train-published-route-guide')).toHaveCount(0);
});

test('the railway network is drawn with the train layer and cleared without it', async ({ page }) => {
  const requests = [];
  page.on('request', request => { const url = new URL(request.url()); if (url.pathname.startsWith('/api/rail/lines/')) requests.push(url.pathname); });
  await expect(page.locator('#map')).not.toHaveAttribute('data-rail-lines', /[1-9]/);
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-rail-lines', '2');
  expect(requests).toContain('/api/rail/lines/national');
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-rail-lines', '0');
});

test('train markers show punctuality only when lateness is known', async ({ page }) => {
  await page.locator('[data-layer-chip="train"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const first = window.__fixtureSnapshot.activities.find(row => row.id === 'train-live-1');
    const late = { ...first, id:'train-late', name:'1K31', delay_minutes:8, lat:52.3, lon:-1.2 };
    const onTime = { ...first, id:'train-on-time', name:'1P32', delay_minutes:0, lat:52.5, lon:-1.4 };
    const unknown = { ...first, id:'train-unknown', name:'1P34', delay_minutes:null, lat:52.7, lon:-1.6 };
    window.__fixtureSocket.onmessage({ data: JSON.stringify({ ...window.__fixtureSnapshot, activities:[late,onTime,unknown], stream:{ ...window.__fixtureSnapshot.stream, selected_types:['train'] } }) });
  });
  await expect(page.locator('.marker-wrap-train')).toHaveCount(3);
  await expect(page.locator('.train-punctuality.is-very-late')).toHaveCount(1);
  await expect(page.locator('.train-punctuality.is-on-time')).toHaveCount(1);
  await expect(page.locator('.train-punctuality')).toHaveCount(2);
});

test('rail stations appear with the train layer and go when it is turned off', async ({ page }) => {
  const placeModes = [];
  page.on('request', request => {
    const url = new URL(request.url());
    if (url.pathname === '/api/places') placeModes.push(url.searchParams.get('mode'));
  });
  await expect(page.locator('.station-marker')).toHaveCount(0);
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('.station-marker').first()).toBeAttached();
  expect(placeModes).toContain('rail');
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('.station-marker')).toHaveCount(0);
});

test('the train journey shows where the train is, platforms and expected times', async ({ page }) => {
  await page.route('**/api/activity/train-live-1/rail', route => route.fulfill({ json: { activity:'train-live-1', detail:{
    available:true, source:'Live railway and timetable data',
    runs:[{ headcode:'1A23', origin:'Leicester', destination:'Birmingham New Street', delay_minutes:4, delay_source:'signalling',
      calling_points:[
        { name:'Leicester', scheduled:'19:50', platform:'4A', event:'Stops', state:'passed' },
        { name:'Nuneaton', scheduled:'20:09', expected:'20:13', platform:'6', event:'Stops', state:'next' },
        { name:'Coleshill Parkway', scheduled:'20:25', expected:'20:29', platform:'1', event:'Stops', state:'upcoming' },
        { name:'Birmingham New Street', scheduled:'20:38', expected:'20:42', platform:'10A', event:'Stops', state:'upcoming' },
      ] }],
  } } }));
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  const stops = page.locator('.train-full-route.has-progress > li');
  await expect(stops).toHaveCount(5);
  await expect(stops.nth(1)).toHaveClass(/train-here/);
  await expect(stops.nth(2)).toContainText('Nuneaton');
  await expect(stops.nth(2)).toContainText('Platform 6');
  await expect(stops.nth(2).locator('.stop-late')).toHaveText('20:13');
  await expect(stops.nth(0).locator('.stop-late')).toHaveCount(0);
  await expect(page.locator('#detail-fields [data-field="Status"] strong')).toHaveText('4 min late');
});

test('map chips switch layers in step with the panel switches', async ({ page }) => {
  const chip = page.locator('[data-layer-chip="train"]');
  await expect(chip).toHaveAttribute('aria-pressed', 'false');
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.marker-wrap-train').first()).toBeAttached();
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(chip).toHaveAttribute('aria-pressed', 'false');
});

test('the map row is layer switches only; the everyday answers are at the top of the Map panel', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await expect(page.locator('.layer-chips [data-job]')).toHaveCount(0);
  await expect(page.locator('.layer-chips .layer-chip').first()).toHaveText('Trains');
  await expect(page.locator('#map-layers-open')).toBeHidden();
  // On the map, Map closes the panel; tapped again it opens it with "Near you" first.
  await page.locator('[data-workspace="useful"]').click();
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await page.locator('[data-workspace="useful"]').click();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
  await expect(page.locator('.near-you')).toBeVisible();
  await expect(page.locator('.near-you .job-row strong')).toHaveText(['What’s that?','Planes above you','Trains near you','Your next bus','My journey']);
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
});

test('mobile tabs put an open detail sheet away', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('#detail-sidebar')).not.toHaveClass(/is-empty/);
  await page.locator('[data-workspace="settings"]').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
  await expect(page.locator('[data-workspace-panel="settings"]')).toBeVisible();
});

test('double tapping the mobile map zooms towards the tapped point', async ({ page }) => {
  await page.setViewportSize({width:390,height:844});
  await expect(page.locator('#map')).toHaveAttribute('data-map-zoom','6');
  await page.locator('#map').evaluate(element=>element.dispatchEvent(new MouseEvent('dblclick',{bubbles:true,cancelable:true})));
  await expect(page.locator('#map')).toHaveAttribute('data-map-zoom','7');
});

test('mobile first visit can find a bus, save the stop, and reach simple alerts', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await expect(page.locator('[data-cool-view="missions"]')).toHaveCount(0);
  for (const selector of ['[data-job="bus"]','[data-job="planes"]']) {
    const box=await page.locator(selector).boundingBox();
    // Rows in the Map panel's "Near you" list: comfortably tappable.
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeLessThanOrEqual(80);
    expect(box.x+box.width).toBeLessThanOrEqual(390);
  }
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('[data-workspace-panel="live"] .experience-hero')).toContainText('Saved');
  await expect(page.locator('#saved-places-list')).toContainText('Central Station Stop A');
  await expect(page.locator('#passing-next')).toContainText('City Centre');
  await expect(page.locator('.commute-alert-card')).toContainText('Travel alerts');
  await expect(page.locator('.alert-options')).not.toHaveAttribute('open','');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBe(true);
});

test('on a phone, planes above you opens a list and keeps the map in view', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('[data-job="planes"]').click();
  await expect(page.locator('#detail-title')).toHaveText(/Planes above you|Nearest planes/);
  await expect(page.locator('#detail-subtitle')).toContainText('25 miles');
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await expect(page.locator('#nearby-note')).toBeVisible();
  await page.locator('[data-job-plane]').first().click();
  await expect(page.locator('#detail-title')).not.toHaveText(/Planes above you|Nearest planes/);
});

test.describe('on a touch screen', () => {
  test.use({ hasTouch:true, isMobile:true, viewport:{ width:390, height:844 } });

  test('tapping a train opens one panel and no floating map card', async ({ page }) => {
    await page.locator('[data-layer-chip="train"]').click();
    const marker = page.locator('.marker-bubble.marker-train').first();
    await marker.evaluate(el => el.closest('.leaflet-marker-icon').dispatchEvent(new MouseEvent('mouseenter')));
    await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
    await expect(page.locator('#detail-title')).toContainText('London Express');
    await expect(page.locator('.activity-tooltip')).toHaveCount(0);
  });
});

test('tapping a railway line lists the trains on that stretch in order', async ({ page }) => {
  await page.route(url => new URL(url).pathname === '/api/rail/line', route => route.fulfill({ json:{
    found:true, from:'MILTON KEYNES CENTRAL', to:'Northampton', length_km:24.6,
    points:[[52.03,-0.77],[52.23,-0.9]],
    trains:[
      { id:'train-live-1', name:'1A23', destination:'Manchester Piccadilly', operator:'Fixture Rail', delay_minutes:12, cancelled:false, along_km:3.1 },
      { id:'train-other', name:'2N40', destination:'Northampton', operator:'Fixture Rail', delay_minutes:0, cancelled:false, along_km:9.4 },
    ],
  } }));
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-rail-lines', /[1-9]/);
  await page.locator('#map').evaluate(element=>element.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  await expect(page.locator('#detail-title')).toHaveText('Milton Keynes Central – Northampton');
  await expect(page.locator('#detail-subtitle')).toContainText('2 trains on this line');
  await expect(page.locator('.line-train strong')).toHaveText(['1A23','2N40']);
  await expect(page.locator('.line-train').first()).toContainText('12 min late');
  await expect(page.locator('.line-train').nth(1)).toContainText('On time');
  await expect(page.locator('#map')).toHaveAttribute('data-rail-line-view','2');
  await page.locator('[data-line-train="train-live-1"]').click();
  await expect(page.locator('#detail-title')).not.toHaveText('Milton Keynes Central – Northampton');
  await expect(page.locator('#map')).not.toHaveAttribute('data-rail-line-view', /./);
});

test('a second row under the chips offers options for the layer switched on last', async ({ page }) => {
  const row = page.locator('#sub-chips');
  await expect(row).toBeHidden();
  await page.locator('[data-layer-chip="train"]').click();
  await expect(row).toBeVisible();
  await expect(row.locator('.sub-chip:visible')).toHaveText(['Delays','Line status','Late only','Near me','Nearest station','Camera view']);
  // Late only hides trains that are on time (the fixture trains have no delay).
  await expect(page.locator('.marker-wrap-train').first()).toBeAttached();
  await row.locator('[data-late-only]').click();
  await expect(row.locator('[data-late-only]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('.marker-wrap-train')).toHaveCount(0);
  await row.locator('[data-late-only]').click();
  await expect(page.locator('.marker-wrap-train').first()).toBeAttached();
  // Switching buses on shows the bus options; aircraft and roads have theirs.
  await page.locator('[data-layer-chip="bus"]').click();
  await expect(row.locator('.sub-chip:visible')).toHaveText(['Next bus','Bus stops','Disruptions','Camera view']);
  await row.locator('[data-sub-action="bus-disruptions"]').click();
  await expect(page.locator('#detail-title')).toHaveText('Bus disruptions');
  await page.locator('[data-layer-chip="aircraft"]').click();
  await expect(row.locator('.sub-chip:visible')).toHaveText(['Above me','Point at sky','Camera view']);
  await page.locator('[data-layer-chip="incident"]').click();
  await expect(row.locator('.sub-chip:visible')).toHaveText(['My journey','Live traffic','Traffic cameras']);
  // Turning the latest layer off falls back to another one that is still on.
  await page.locator('[data-layer-chip="incident"]').click();
  await expect(row.locator('.sub-chip:visible').first()).toHaveText('Delays');
  for (const type of ['train','bus','aircraft']) await page.locator(`[data-layer-chip="${type}"]`).click();
  await expect(row).toBeHidden();
});

test('on a phone the option row sits just under the layer chips', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.locator('[data-layer-chip="train"]').click();
  const chips = await page.locator('.layer-chips').boundingBox();
  const sub = await page.locator('#sub-chips').boundingBox();
  expect(sub.y).toBeGreaterThanOrEqual(chips.y + chips.height);
  expect(sub.y - (chips.y + chips.height)).toBeLessThan(16);
});

test('Delays shows the stretches where trains are running late', async ({ page }) => {
  await expect(page.locator('[data-rail-delays]')).toBeHidden();
  await page.locator('[data-layer-chip="train"]').click();
  await page.locator('[data-rail-delays]').click();
  await expect(page.locator('[data-rail-delays]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#detail-title')).toHaveText('Delays now');
  await expect(page.locator('.delay-spot')).toHaveCount(2);
  await expect(page.locator('.delay-spot').first()).toContainText('Milton Keynes Central – Northampton');
  await expect(page.locator('.delay-spot').first()).toContainText('3 late, about 18 min · 1 cancelled');
  await expect(page.locator('#map')).toHaveAttribute('data-delay-hotspots','2');
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('[data-rail-delays]')).toBeHidden();
  await expect(page.locator('#map')).not.toHaveAttribute('data-delay-hotspots', /./);
});

test('station boards strike out cancelled trains and flag platform changes', async ({ page }) => {
  await page.route('**/api/places/rail*MKC/board*', route => route.fulfill({ json:{ board:{ mode:'network_rail', departures:[
    { scheduled:'10:14', expected:'10:14', destination:'Manchester Piccadilly', platform:'4', status:'Cancelled', cancelled:true, headcode:'1A23', train_uid:'C12345', service_date:'2026-10-05' },
    { scheduled:'10:32', expected:'10:32', destination:'Birmingham New Street', platform:'5', booked_platform:'2', platform_changed:true, status:'Scheduled', headcode:'1Z80', train_uid:'C99999', service_date:'2026-10-05' },
  ], arrivals:[] } } }));
  await page.locator('#station-boards').click();
  const cancelled = page.locator('#detail-placeboard .board-row').first();
  await expect(cancelled).toHaveClass(/is-cancelled/);
  await expect(cancelled.locator('.board-status')).toHaveText('Cancelled');
  await expect(page.locator('#detail-placeboard .platform-changed')).toHaveText('Platform 5 (was 2)');
});

test('a cancelled train is drawn struck through and says so', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const first = window.__fixtureSnapshot.activities.find(row => row.id === 'train-live-1');
    const cancelled = { ...first, id:'train-cancelled', name:'1K40', delay_minutes:9, cancelled_at:new Date().toISOString(), lat:52.3, lon:-1.2 };
    window.__fixtureSocket.onmessage({ data: JSON.stringify({ ...window.__fixtureSnapshot, activities:[cancelled], stream:{ ...window.__fixtureSnapshot.stream, selected_types:['train'] } }) });
  });
  await expect(page.locator('.marker-bubble.marker-train.is-cancelled')).toHaveCount(1);
  await expect(page.locator('.marker-bubble.marker-train.is-cancelled .train-punctuality')).toHaveCount(0);
});

test('a moving aircraft glides to its new position instead of being redrawn', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  await waitForViewportRequestsToSettle(page);
  const move = step => page.evaluate(step => {
    const next = structuredClone(window.__fixtureSnapshot);
    for (const row of next.activities.filter(item => ['aircraft','sar'].includes(item.type))) {
      row.lat += 0.05 * step; row.lon += 0.05 * step; row.last_updated = new Date().toISOString();
    }
    window.__fixtureSocket.onmessage({ data: JSON.stringify(next) });
  }, step);
  await move(1);
  await expect.poll(() => page.evaluate(() => window.__leafletStats.markerMoves)).toBeGreaterThan(0);
  await page.waitForTimeout(1200);
  const before = await page.evaluate(() => ({ moves: window.__leafletStats.markerMoves, icons: window.__leafletStats.markerIconUpdates }));
  await move(2);
  await expect.poll(() => page.evaluate(() => window.__leafletStats.markerMoves)).toBeGreaterThan(before.moves);
  await page.waitForTimeout(1200);
  expect(await page.evaluate(() => window.__leafletStats.markerIconUpdates)).toBe(before.icons);
});

test('the nearby limit is a small pill that can be closed from the map', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('[data-job="planes"]').click();
  const pill = page.locator('#nearby-note');
  await expect(pill).toBeVisible();
  await expect(pill).toHaveText(/Aircraft within 25 miles of you/);
  await expect(pill).not.toContainText('live matches');
  const box = await pill.boundingBox();
  expect(box.height).toBeLessThan(48);
  await pill.locator('.nearby-note-close').click();
  await expect(pill).toBeHidden();
  await expect(page.locator('#overhead-now')).not.toHaveClass(/active/);
});

test('the stop list opens at the stop before the train and can show earlier stops', async ({ page }) => {
  await page.route('**/api/activity/train-live-1/rail', route => route.fulfill({ json: { activity:'train-live-1', detail:{
    available:true, runs:[{ headcode:'1A23', origin:'London Euston', destination:'Manchester Piccadilly', calling_points:[
      { name:'London Euston', scheduled:'10:00', state:'passed' },
      { name:'Watford Junction', scheduled:'10:15', state:'passed' },
      { name:'Milton Keynes Central', scheduled:'10:35', state:'passed' },
      { name:'Rugby', scheduled:'10:55', state:'passed' },
      { name:'Stoke-on-Trent', scheduled:'11:40', expected:'11:44', state:'next' },
      { name:'Manchester Piccadilly', scheduled:'12:10', expected:'12:14', state:'upcoming' },
    ] }] } } }));
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  const list = page.locator('#detail-intelligence .train-full-route');
  await expect(list.locator('[data-show-earlier]')).toHaveText('Show 3 earlier stops');
  await expect(list.locator('li:visible strong')).toHaveText(['Rugby','Stoke-on-Trent','Manchester Piccadilly']);
  await expect(list.locator('.train-here')).toBeVisible();
  await list.locator('[data-show-earlier]').click();
  await expect(list.locator('li:visible strong')).toHaveText(['London Euston','Watford Junction','Milton Keynes Central','Rugby','Stoke-on-Trent','Manchester Piccadilly']);
  await expect(list.locator('[data-show-earlier]')).toHaveText('Hide earlier stops');
});

test('a train status sits in one compact row of chips', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const first = window.__fixtureSnapshot.activities.find(row => row.id === 'train-live-1');
    window.__fixtureSocket.onmessage({ data: JSON.stringify({ ...window.__fixtureSnapshot, activities:[{ ...first, delay_minutes:12, next_location:'Rugby' }] }) });
  });
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  const status = page.locator('#detail-fields [data-field="Status"]');
  await expect(status).toHaveText(/12 min late/);
  await expect(status).toHaveAttribute('data-tone','bad');
  const box = await page.locator('#detail-fields').boundingBox();
  expect(box.height).toBeLessThan(44);
});

test('an open page reloads itself when a newer version is published', async ({ page }) => {
  let loads = 0;
  page.on('load', () => { loads += 1; });
  const fs = require('fs');
  const html = fs.readFileSync(require('path').join(__dirname, '..', 'app/static/index.html'), 'utf8');
  await page.route(url => new URL(url).pathname === '/', route => route.fulfill({ contentType:'text/html', body: html.replace(/app\.js\?v=\d+/, 'app.js?v=99999') }));
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => loads).toBeGreaterThan(0);
});

test('trains near you is one tap and lists what is passing', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('[data-job="trains"]').click();
  await expect(page.locator('#detail-title')).toHaveText('Trains near you');
  await expect(page.locator('[data-line-train="train-live-1"]')).toContainText('400 m away');
  await expect(page.locator('[data-line-train="train-live-1"]')).toContainText('6 min late');
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed','true');
  await page.locator('[data-line-train="train-live-1"]').click();
  await expect(page.locator('#detail-title')).not.toHaveText('Trains near you');
});

test('my journey is remembered and runs again in one tap', async ({ page }) => {
  await page.locator('[data-job="journey"]').click();
  await page.locator('#journey-origin').fill('Milton Keynes');
  await page.locator('#journey-destination').fill('London');
  await page.locator('#journey-submit').click();
  await expect(page.locator('#journey-results .journey-issue-summary')).toContainText('1 incident on this route');
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.locator('[data-job="journey"]').click();
  await expect(page.locator('#journey-destination')).toHaveValue('London');
  await expect(page.locator('#journey-results .journey-issue-summary')).toContainText('1 incident on this route');
});

test('a late train says where the delay built up', async ({ page }) => {
  await page.route('**/api/activity/train-live-1/rail', route => route.fulfill({ json: { detail:{ available:true, runs:[{ headcode:'1A23',
    delay_explanation:{ late:12, grew_by:8, between:['RUGBY','Nuneaton'], others_late:3 },
    calling_points:[{ name:'Rugby', scheduled:'10:00', state:'passed' },{ name:'Nuneaton', scheduled:'10:15', expected:'10:27', state:'next' }] }] } } }));
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  const why = page.locator('.delay-why');
  await expect(why).toContainText('12 min late.');
  await expect(why).toContainText('Most of it (8 min) was lost between Rugby and Nuneaton.');
  await expect(why).toContainText('3 other trains nearby are late too');
});

test('light aircraft and business jets are not mistaken for military types', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const first = window.__fixtureSnapshot.activities.find(row => row.id === 'aircraft-live-1');
    const plane = (id, name, model, code, lat) => ({ ...first, id, name, vehicle_ref:name, aircraft_model:model, aircraft_type_code:code, subtitle:`${name} • ${code}`, lat, cool_category:null, cool_score:0, cool_label:null });
    window.__fixtureSocket.onmessage({ data: JSON.stringify({ ...window.__fixtureSnapshot, activities:[
      plane('ac-c172','GBJXZ','','C172',52.1), plane('ac-f150','GAVGE','','F150',52.3), plane('ac-hawker','GOMHC','Hawker 800XP','H25B',52.5),
    ] }) });
  });
  await expect(page.locator('.aerial-map-marker')).toHaveCount(3);
  await expect(page.locator('.aerial-map-marker').filter({ hasText:'C-17' })).toHaveCount(0);
  await expect(page.locator('.aerial-map-marker').filter({ hasText:'CESSNA 172' })).toHaveClass(/aircraft-shape-propeller/);
  await expect(page.locator('.aircraft-shape-fastjet')).toHaveCount(0);
  await expect(page.locator('.aircraft-shape-heavy')).toHaveCount(0);
});

test('an incident says what it means for a driver, not the feed wording', async ({ page }) => {
  await page.locator('[data-layer-toggle="incident"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const crash = { kind:'event', id:'nh:crash1', type:'incident', name:'M1 — accident', subtitle:'raw', lat:52.05, lon:-0.75,
      category:'Unplanned road incident / disruption', status:'Active', road:'M1', source:'National Highways', source_id:'crash1',
      last_updated:new Date().toISOString(), position_method:'static', position_label:'x',
      impact:{ title:'Crash · M1', what:'Crash', where:'M1 northbound between J14 and J15', impact:'2 lanes closed · Delays of about 20 min',
        when:'Started 25 min ago', why:null, note:'Two vehicles involved. Recovery on scene.', active:true, rank:4 } };
    window.__fixtureSocket.onmessage({ data: JSON.stringify({ ...window.__fixtureSnapshot, activities:[crash], stream:{ ...window.__fixtureSnapshot.stream, selected_types:['incident'] } }) });
  });
  await page.locator('.marker-bubble.marker-incident').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toHaveText('Crash · M1');
  await expect(page.locator('#detail-subtitle')).toHaveText('M1 northbound between J14 and J15');
  await expect(page.locator('#detail-fields [data-field="Impact"]')).toContainText('Delays of about 20 min');
  await expect(page.locator('#detail-fields [data-field="Impact"]')).toHaveAttribute('data-tone','bad');
  await expect(page.locator('#detail-fields [data-field="When"]')).toContainText('Started 25 min ago');
  await expect(page.locator('.traffic-note')).toContainText('Recovery on scene');
  await expect(page.locator('#detail-panel')).not.toContainText('Unplanned road incident / disruption');
});

test('a selected bus shows its route on the map and no stop codes', async ({ page }) => {
  await page.locator('[data-layer-toggle="bus"]').click();
  await page.locator('.marker-bubble.marker-bus').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-intelligence .bus-stops')).toBeVisible();
  await expect(page.locator('#map')).toHaveAttribute('data-bus-route-stops','3');
  await expect(page.locator('#detail-intelligence')).not.toContainText('leidtgwt');
  await expect(page.locator('#detail-intelligence')).toContainText('10:06');
  await page.locator('#detail-close').click();
  await expect(page.locator('#map')).not.toHaveAttribute('data-bus-route-stops', /./);
});

test('quick-answer lists never show a previous station board underneath', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
  await page.locator('[data-job="trains"]').click();
  await expect(page.locator('#detail-title')).toHaveText('Trains near you');
  await expect(page.locator('#detail-placeboard')).toBeHidden();
});

test('common civil helicopters such as the Robinson R44 get the helicopter icon', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const first = window.__fixtureSnapshot.activities.find(row => row.id === 'aircraft-live-1');
    const heli = (id, name, code, lat) => ({ ...first, id, name, vehicle_ref:name, aircraft_model:'', aircraft_type_code:code, subtitle:`${name} • ${code}`, lat, cool_category:null, cool_score:0, cool_label:null });
    window.__fixtureSocket.onmessage({ data: JSON.stringify({ ...window.__fixtureSnapshot, activities:[heli('h1','G-INDX','R44',52.1), heli('h2','G-HEMS','EC35',52.3), heli('h3','G-CABI','G2CA',52.5)] }) });
  });
  await expect(page.locator('.aerial-map-marker')).toHaveCount(3);
  await expect(page.locator('.aircraft-shape-helicopter')).toHaveCount(3);
});

test('an everyday train shows an example photo in its operator colours, clearly labelled', async ({ page }) => {
  await page.route('**/api/activity/train-live-1/media', route => route.fulfill({ json:{ ok:true, title:'GWR 800 004 at Reading.jpg', thumbnail_url:'https://upload.wikimedia.org/fixture-gwr.jpg', page_url:'https://commons.wikimedia.org/?curid=2', artist:'A Photographer', license:'CC BY-SA 4.0', source:'Wikimedia Commons', match_kind:'type_reference', reference_label:'Great Western Railway — example of their trains, not this exact one' } }));
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-wrap-train .marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  const photo = page.locator('#detail-photo');
  await expect(photo.locator('img')).toBeVisible();
  await expect(photo.locator('.image-truth')).toHaveText('Great Western Railway — example of their trains, not this exact one');
});

test('an aircraft gets one stable exact Planespotters photo in one request', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  const asked = [];
  await page.route('**/api/activity/**/media**', route => { asked.push(route.request().url()); return route.fulfill({ json:{ ok:true, title:'Aircraft photo', thumbnail_url:'https://t.plnspttrs.net/fixture-exact.jpg', thumbnail_width:420, thumbnail_height:280, page_url:'https://www.planespotters.net/photo/123/example?utm_source=api', artist:'© A Spotter', license:'linked thumbnail', source:'Planespotters.net', match_kind:'exact_airframe' } }); });
  await page.locator('[data-job="planes"]').click();
  await page.locator('[data-job-plane]').first().click();
  const photo=page.locator('#detail-photo img');
  await expect(photo).toBeVisible();
  await expect(photo).toHaveAttribute('src','https://t.plnspttrs.net/fixture-exact.jpg');
  await page.waitForTimeout(1200);
  await expect(photo).toHaveAttribute('src','https://t.plnspttrs.net/fixture-exact.jpg');
  await expect(page.locator('#detail-photo')).toContainText('© A Spotter');
  expect(asked.length).toBe(1);
  expect(asked[0]).toContain('kind=aircraft');
});

test('saving a train turns phone alerts on for it, with a test alert to prove it', async ({ page }) => {
  const posted = [];
  await page.route('**/api/alerts/subscribe', route => { posted.push(route.request().postDataJSON()); return route.fulfill({ json:{ ok:true } }); });
  let tests = 0;
  await page.route('**/api/alerts/test', route => { tests += 1; return route.fulfill({ json:{ ok:true } }); });
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-wrap-train .marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await page.locator('#follow').click();
  await expect.poll(() => posted.length).toBe(1);
  expect(posted[0].preferences.mode).toBe('trains-only');
  await expect(page.locator('#watch-toast')).toContainText('Your phone will tell you');
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('#commute-alert-state')).toHaveText('ON');
  await expect(page.locator('#commute-alert-summary')).toContainText('Your saved trains: late, cancelled or a platform change');
  await page.locator('#commute-alert-test').click();
  await expect.poll(() => tests).toBe(1);
});

test('a Special train says why it is special, and there is one Share button', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await waitForViewportRequestsToSettle(page);
  await page.evaluate(() => {
    const snapshot = JSON.parse(JSON.stringify(window.__fixtureSnapshot));
    const train = snapshot.activities.find(row => row.id === 'train-live-1');
    Object.assign(train, { headcode:'1Z64', name:'1Z64', cool_score:72, cool_candidate:true, cool_badge:'SPECIAL', cool_subtype:'special-passenger', cool_category:'rail', cool_explanation:'Special passenger train.' });
    window.__fixtureSocket.onmessage({ data: JSON.stringify(snapshot) });
  });
  await page.locator('.marker-wrap-train .marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('1Z64');
  await expect(page.locator('.special-why')).toContainText('a Z in the headcode marks a one-off working');
  await expect(page.locator('.detail-actions button:visible', { hasText: /^Share$/ })).toHaveCount(1);
});

test('saved stations and trains are answered at the top of the Map panel', async ({ page }) => {
  await expect(page.locator('#yours')).toBeHidden();
  await page.route('**/api/trains/status**', route => route.fulfill({ json:{ trains:[
    { found:true, id:'nr:1L56', name:'1L56', destination:'CAMBRIDGE', cancelled:false, arrived:false, delay_minutes:16, platform:'4', platform_changed:true, booked_platform:'2', alerts:[] },
    { found:false },
  ] } }));
  await page.evaluate(() => localStorage.setItem('ukRadarFollowed', JSON.stringify(['nr:1L56','nr:9Z99'])));
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout: 5000 });
  const train = page.locator('[data-yours-train="nr:1L56"]');
  await expect(train.locator('strong')).toHaveText('1L56 to Cambridge');
  await expect(train.locator('small')).toHaveText('16 min late · now platform 4');
  await expect(train.locator('small')).toHaveClass(/is-late/);
  await expect(page.locator('[data-yours-train]')).toHaveCount(1);
  // Saving a station adds its next departure.
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  const station = page.locator('[data-yours-place]').first();
  await expect(station.locator('strong')).toHaveText('Milton Keynes Central');
  await expect(station.locator('small')).toContainText('10:14 Manchester Piccadilly · Plat 4 · On time · then 10:32');
  await page.locator('#detail-close').click();
  await station.click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
});

test('on a phone the app opens with your saved things showing, not a bare map', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.reload();
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await page.route('**/api/trains/status**', route => route.fulfill({ json:{ trains:[{ found:true, id:'nr:1L56', name:'1L56', destination:'CAMBRIDGE', cancelled:true, arrived:false, delay_minutes:null, alerts:[] }] } }));
  await page.evaluate(() => localStorage.setItem('ukRadarFollowed', JSON.stringify(['nr:1L56'])));
  await page.reload();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
  await expect(page.locator('[data-yours-train] small')).toHaveText('Cancelled');
  await expect(page.locator('#yours')).toBeInViewport();
});

test('Line status grades every operator and opens its late trains', async ({ page }) => {
  await page.route('**/api/rail/status', route => route.fulfill({ json:{ operators:[
    { operator:'Northern', status:'severe', label:'Severe delays', running:10, late:5, cancelled:1, average_delay:25, worst:[{ id:'train-live-1', name:'1A23', destination:'LEEDS', delay_minutes:31 }] },
    { operator:'Southern', status:'good', label:'Good service', running:10, late:0, cancelled:0, average_delay:0, worst:[] },
  ] } }));
  await page.locator('[data-layer-chip="train"]').click();
  await page.locator('[data-sub-action="line-status"]').click();
  await expect(page.locator('#detail-title')).toHaveText('Line status');
  await expect(page.locator('#detail-subtitle')).toHaveText('1 operator with delays');
  const northern = page.locator('.line-status-row').first();
  await expect(northern).toContainText('Northern');
  await expect(northern).toContainText('5 of 10 late · about 25 min · 1 cancelled');
  await expect(northern).toContainText('Severe delays');
  await expect(northern.locator('.line-status-worst')).toBeHidden();
  await northern.locator('[data-line-operator]').click();
  await expect(northern.locator('.line-status-train')).toContainText('31 min late');
});

test('spotter alerts switch on from Discover with the place and distance', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.04,longitude:-0.76}});}}}));
  const posted = [];
  await page.route('**/api/alerts/subscribe', route => { posted.push(route.request().postDataJSON()); return route.fulfill({ json:{ ok:true } }); });
  await page.getByRole('button', { name: /Discover/ }).click();
  await expect(page.locator('#spotter-state')).toHaveText('OFF');
  await page.locator('#spotter-radius').selectOption('25');
  await page.locator('#spotter-toggle').click();
  await expect(page.locator('#spotter-state')).toHaveText('ON');
  expect(posted[0].preferences.spotter).toEqual({ lat:52.04, lon:-0.76, radius_miles:25, kinds:['steam','test','loco','military'] });
  // Unticking one updates what is watched straight away.
  await page.locator('[data-spotter-kind="military"]').uncheck();
  await expect.poll(() => posted.length).toBe(2);
  expect(posted[1].preferences.spotter.kinds).toEqual(['steam','test','loco']);
  await expect(page.locator('#spotter-note')).toContainText('On within 25 miles');
  await page.locator('#spotter-toggle').click();
  await expect(page.locator('#spotter-state')).toHaveText('OFF');
  expect(posted[2].preferences.spotter).toBeNull();
});

test('a drive can be watched, so the phone warns before leaving', async ({ page }) => {
  const posted = [];
  await page.route('**/api/alerts/subscribe', route => { posted.push(route.request().postDataJSON()); return route.fulfill({ json:{ ok:true } }); });
  await page.locator('[data-job="journey"]').click();
  await page.locator('#journey-origin').fill('Milton Keynes');
  await page.locator('#journey-destination').fill('London');
  await page.locator('#journey-submit').click();
  await expect(page.locator('.journey-watch')).toContainText('Warn me before I leave');
  await page.locator('#journey-watch-time').fill('08:15');
  await page.locator('[data-journey-watch="on"]').click();
  await expect(page.locator('.journey-watch')).toContainText('Watching your drive to London');
  await expect(page.locator('.journey-watch')).toContainText('before 08:15');
  const journey = posted[0].preferences.journey;
  expect(journey.leave_minutes).toBe(495);
  expect(journey.points).toEqual([[52.034,-0.774],[51.8,-0.5],[51.528,-0.134]]);
  await page.locator('[data-journey-watch="off"]').click();
  await expect(page.locator('.journey-watch')).toContainText('Warn me before I leave');
  expect(posted[1].preferences.journey).toBeNull();
});

test('a train panel can tell you when to leave for it', async ({ page }) => {
  const posted = [];
  await page.route('**/api/alerts/subscribe', route => { posted.push(route.request().postDataJSON()); return route.fulfill({ json:{ ok:true } }); });
  const soon = minutes => new Date(Date.now() + minutes * 60000).toISOString();
  await page.route('**/api/activity/train-live-1/rail', route => route.fulfill({ json:{ detail:{ available:true, runs:[{ headcode:'1A23',
    calling_points:[{ name:'Rugby', scheduled:soon(-10), state:'passed' },{ name:'Nuneaton', scheduled:soon(40), state:'next' },{ name:'Stafford', scheduled:soon(70) },{ name:'Crewe', scheduled:soon(95) }] }] } } }));
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-wrap-train .marker-bubble.marker-train').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  const plan = page.locator('details.leave-plan');
  await plan.locator('summary').click();
  // Stops already passed, and the final stop, are not offered.
  await expect(plan.locator('[data-leave-stop] option')).toHaveCount(2);
  await plan.locator('[data-leave-stop]').selectOption('1');
  await plan.locator('[data-leave-minutes]').fill('20');
  await plan.locator('[data-leave-set]').click();
  await expect(page.locator('p.leave-plan.is-set')).toContainText('from Stafford, allowing 20 min');
  const saved = posted.at(-1).preferences.leave_plans;
  expect(saved).toHaveLength(1);
  expect(saved[0]).toMatchObject({ train_id:'train-live-1', station:'Stafford', minutes:20 });
  await expect(page.locator('#follow')).toHaveText('Saved ✓');
  await page.locator('[data-leave-cancel]').click();
  await expect(page.locator('details.leave-plan')).toBeVisible();
  expect(posted.at(-1).preferences.leave_plans).toEqual([]);
});

test('pointing the phone at the sky names the aircraft in that direction', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  // One aircraft 10 km due east at 10,000 ft: about 17 degrees above the horizon.
  await page.route('**/api/aircraft/nearby**', route => route.fulfill({ json:{ checked:1, radius_miles:50, activities:[
    { kind:'vehicle', id:'aircraft:east', type:'aircraft', name:'EZY12AB', lat:54.11, lon:-2.297, altitude_ft:10000, aircraft_model:'Airbus A320', bearing:270, source:'ADSB.lol', last_updated:new Date().toISOString() },
  ] } }));
  const point = (heading, beta) => page.evaluate(([h, b]) => { const e = new Event('deviceorientation'); e.webkitCompassHeading = h; e.beta = b; window.dispatchEvent(e); }, [heading, beta]);
  await page.locator('[data-layer-chip="aircraft"]').click();
  // Without a compass the feature says so instead of opening.
  await page.locator('[data-sub-action="sky"]').click();
  await expect(page.locator('#watch-toast')).toContainText('needs a phone with a compass');
  await page.evaluate(() => { window.DeviceOrientationEvent = class extends Event {}; });
  await page.locator('[data-sub-action="sky"]').click();
  await expect(page.locator('#detail-title')).toHaveText('Point at the sky');
  await expect(page.locator('.sky-match')).toContainText('EZY12AB');
  await point(88, 15);
  await expect(page.locator('[data-sky-direction]')).toHaveText('Pointing E, 15° up');
  await expect(page.locator('.sky-found')).toHaveText('That’s this one:');
  await expect(page.locator('.sky-match')).toContainText('Airbus A320 · 10,000 ft');
  await page.waitForTimeout(450);
  await point(270, 40);
  await expect(page.locator('[data-sky-direction]')).toHaveText('Pointing W, 40° up');
  await expect(page.locator('.sky-pointer')).toContainText('Nothing in that direction. The closest to where you’re pointing is to the E, 17° up');
  await expect(page.locator('.sky-found')).toHaveCount(0);
});

test('a board says which platform a train usually uses before it is announced', async ({ page }) => {
  await page.route('**/api/places/rail*MKC/board*', route => route.fulfill({ json:{ board:{ mode:'network_rail', departures:[
    { scheduled:'10:14', expected:'10:14', destination:'Manchester Piccadilly', status:'Scheduled', headcode:'1A23', train_uid:'C12345', service_date:'2026-10-05', usual_platform:{ platform:'6', seen:8, of:10 } },
    { scheduled:'10:32', expected:'10:32', destination:'Birmingham New Street', platform:'5', status:'Scheduled', headcode:'1B01', train_uid:'C22222', service_date:'2026-10-05', usual_platform:{ platform:'7', seen:4, of:5 } },
  ], arrivals:[] } } }));
  await page.locator('#station-boards').click();
  const rows = page.locator('#detail-placeboard .board-row');
  await expect(rows.nth(0).locator('.usual-platform')).toHaveText('Usually platform 6 (8 of last 10)');
  await expect(rows.nth(1)).toContainText('Platform 5');
  await expect(rows.nth(1).locator('.usual-platform')).toHaveText('usually 7 (4 of last 5)');
});

test('the camera view labels the aircraft, train and bus in the direction the phone faces', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  // An aircraft 10 km due east at 10,000 ft (about 17 degrees up), a train 1 km north, a bus 300 m south.
  await page.route('**/api/aircraft/nearby**', route => route.fulfill({ json:{ checked:1, radius_miles:50, activities:[
    { kind:'vehicle', id:'aircraft:east', type:'aircraft', name:'EZY12AB', lat:54.11, lon:-2.297, altitude_ft:10000, aircraft_model:'Airbus A320', bearing:270, source:'ADSB.lol', last_updated:new Date().toISOString() },
  ] } }));
  await page.route('**/api/around**', route => route.fulfill({ json:{ vehicles:[
    { id:'nr:north', type:'train', name:'1A23', destination:'LEEDS', operator:'Northern', delay_minutes:6, lat:54.119, lon:-2.45 },
    { id:'bus:south', type:'bus', name:'Bus 7', destination:'City Centre', operator:'Stagecoach', lat:54.1073, lon:-2.45 },
  ] } }));
  await page.locator('[data-layer-chip="aircraft"]').click();
  await page.locator('[data-sub-action="camera"]:visible').click();
  await expect(page.locator('#watch-toast')).toContainText('needs a phone with a camera and a compass');
  await page.evaluate(() => {
    window.DeviceOrientationEvent = class extends Event {};
    const stream = document.createElement('canvas').captureStream();
    Object.defineProperty(navigator, 'mediaDevices', { configurable:true, value:{ getUserMedia: async () => stream } });
  });
  const face = (heading, beta) => page.evaluate(([h, b]) => { for (let i = 0; i < 25; i += 1) { const e = new Event('deviceorientation'); e.webkitCompassHeading = h; e.beta = b; window.dispatchEvent(e); } }, [heading, beta]);
  await page.locator('[data-sub-action="camera"]:visible').click();
  await expect(page.locator('#ar-view')).toBeVisible();
  // Facing east and tilted up at the aircraft: it is labelled near the middle of the screen.
  await face(90, 107);
  await expect(page.locator('#ar-direction')).toHaveText('Looking E, 17° up');
  const plane = page.locator('.ar-label.ar-aircraft');
  await expect(plane).toContainText('EZY12AB');
  await expect(plane).toContainText('Airbus A320 · 10,000 ft');
  const size = page.viewportSize(), box = await plane.boundingBox();
  expect(Math.abs(box.x + box.width / 2 - size.width / 2)).toBeLessThan(40);
  expect(Math.abs(box.y + box.height / 2 - size.height / 2)).toBeLessThan(40);
  await expect(page.locator('.ar-label.ar-train')).toHaveCount(0);
  // Opened from Aircraft, so trains and buses are switched on in the view.
  await page.locator('[data-ar-kind="train"]').click();
  await page.locator('[data-ar-kind="bus"]').click();
  // Facing north at the horizon: the train, not the plane or the bus behind you.
  await face(0, 90);
  await expect(page.locator('.ar-label.ar-train')).toContainText('1A23 to Leeds');
  await expect(page.locator('.ar-label.ar-train')).toContainText('6 min late');
  await expect(page.locator('.ar-label')).toHaveCount(1);
  await face(180, 90);
  await expect(page.locator('.ar-label.ar-bus')).toContainText('Bus 7 to City Centre');
  await page.locator('#ar-close').click();
  await expect(page.locator('#ar-view')).toBeHidden();
});

test('the camera view shows only what it was opened from, until more is switched on', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  let aircraftAsked = 0;
  // A plane and a train both due north, so both would be in view.
  await page.route('**/api/aircraft/nearby**', route => { aircraftAsked += 1; return route.fulfill({ json:{ checked:1, radius_miles:50, activities:[
    { kind:'vehicle', id:'aircraft:north', type:'aircraft', name:'EZY12AB', lat:54.2, lon:-2.45, altitude_ft:3000, aircraft_model:'Airbus A320', source:'ADSB.lol', last_updated:new Date().toISOString() } ] } }); });
  await page.route('**/api/around**', route => route.fulfill({ json:{ vehicles:[
    { id:'nr:north', type:'train', name:'1A23', destination:'LEEDS', operator:'Northern', lat:54.119, lon:-2.45 },
    { id:'bus:north', type:'bus', name:'Bus 7', destination:'City Centre', operator:'Stagecoach', lat:54.113, lon:-2.4495 } ] } }));
  await page.evaluate(() => {
    window.DeviceOrientationEvent = class extends Event {};
    const stream = document.createElement('canvas').captureStream();
    Object.defineProperty(navigator, 'mediaDevices', { configurable:true, value:{ getUserMedia: async () => stream } });
  });
  const face = (heading, beta) => page.evaluate(([h, b]) => { for (let i = 0; i < 25; i += 1) { const e = new Event('deviceorientation'); e.webkitCompassHeading = h; e.beta = b; window.dispatchEvent(e); } }, [heading, beta]);
  await page.locator('[data-layer-chip="train"]').click();
  await page.locator('[data-sub-action="camera"]:visible').click();
  await face(0, 90);
  await expect(page.locator('.ar-label.ar-train')).toHaveCount(1);
  await expect(page.locator('.ar-label')).toHaveCount(1);
  await expect(page.locator('[data-ar-kind="train"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('[data-ar-kind="aircraft"]')).toHaveAttribute('aria-pressed','false');
  expect(aircraftAsked).toBe(0);
  await page.locator('[data-ar-kind="bus"]').click();
  await expect(page.locator('.ar-label.ar-bus')).toHaveCount(1);
  await page.locator('[data-ar-kind="aircraft"]').click();
  await expect(page.locator('.ar-label.ar-aircraft')).toHaveCount(1);
  await page.locator('[data-ar-kind="train"]').click();
  await expect(page.locator('.ar-label.ar-train')).toHaveCount(0);
});

test('the Saved tab says plainly whether alerts are on, and why they failed if they did', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  const summary = page.locator('#commute-alert-summary');
  await expect(summary).toContainText('Alerts are off.');
  await expect(summary).toContainText('Saving a station does not turn them on');
  await expect(page.locator('#commute-alert-enable')).toHaveText('Turn alerts on for Milton Keynes Central');
  // The server refuses: the reason is shown and stays on the page.
  await page.route('**/api/alerts/subscribe', route => route.fulfill({ status:400, json:{ detail:'Unsupported browser push endpoint' } }));
  await page.locator('#commute-alert-enable').click();
  await expect(page.locator('#commute-alert-note')).toHaveClass(/is-problem/);
  await expect(page.locator('#commute-alert-note')).toContainText('Unsupported browser push endpoint');
  await expect(page.locator('#commute-alert-state')).toHaveText('OFF');
  // It works: on, with what will be sent, a test button and a way to turn it off.
  await page.unroute('**/api/alerts/subscribe');
  await page.locator('#commute-alert-enable').click();
  await expect(summary).toContainText('Alerts are on for this phone.');
  await expect(page.locator('#commute-alert-note')).not.toHaveClass(/is-problem/);
  await expect(page.locator('#commute-alert-test')).toBeVisible();
  await page.locator('#commute-alert-disable').click();
  await expect(summary).toContainText('Alerts are off.');
});

test('tapping a saved place chooses it for the departures and alerts below; Map opens it', async ({ page }) => {
  await page.evaluate(() => localStorage.setItem('ukRadarMyRadarV1', localStorage.getItem('ukRadarMyRadarV1') || ''));
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await page.locator('#save-place').click();
  await page.locator('#detail-close').click();
  await page.locator('[data-workspace="live"]').click();
  const rows = page.locator('#saved-places-list .saved-place-row');
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(0)).toHaveClass(/is-chosen/);
  await rows.nth(1).locator('[data-saved-place]').click();
  await expect(rows.nth(1)).toHaveClass(/is-chosen/);
  await expect(page.locator('#passing-next-place')).toHaveValue(await page.locator('#commute-alert-place').inputValue());
  await expect(page.locator('#commute-alert-enable')).toContainText('Central Station Stop A');
  await expect(page.locator('#passing-next .passing-next-label')).toContainText('Next buses from Central Station Stop A');
  // The detail panel did not open a separate timetable.
  await expect(page.locator('#detail-panel')).not.toHaveClass(/has-selection/);
  await rows.nth(0).locator('[data-saved-place-map]').click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
});

test('a saved plane that is out of range is still listed, and says so', async ({ page }) => {
  await page.evaluate(() => {
    localStorage.setItem('ukRadarFollowed', JSON.stringify(['aircraft:400abc']));
    localStorage.setItem('ukRadarFollowedInfoV1', JSON.stringify({ 'aircraft:400abc':{ type:'aircraft', name:'BAW12K', detail:'Airbus A320neo' } }));
  });
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout: 5000 });
  await page.locator('[data-workspace="live"]').click();
  const row = page.locator('#my-radar-list .my-radar-saved');
  await expect(row).toContainText('BAW12K');
  await expect(row).toContainText('Airbus A320neo · Not in range right now');
  await row.locator('[data-unsave]').click();
  await expect(page.locator('#my-radar-list')).toContainText('Nothing saved yet');
});

test('pinching the interface does not zoom the page', async ({ page }) => {
  const prevented = await page.evaluate(() => {
    const out = {};
    const gesture = new Event('gesturestart', { cancelable:true, bubbles:true }); document.body.dispatchEvent(gesture); out.gesture = gesture.defaultPrevented;
    const fire = target => { const e = new Event('touchmove', { cancelable:true, bubbles:true }); e.touches = [{}, {}]; target.dispatchEvent(e); return e.defaultPrevented; };
    out.chips = fire(document.querySelector('.layer-chips'));
    out.map = fire(document.querySelector('#map'));
    const one = new Event('touchmove', { cancelable:true, bubbles:true }); one.touches = [{}]; document.querySelector('.layer-chips').dispatchEvent(one); out.scroll = one.defaultPrevented;
    return out;
  });
  expect(prevented).toEqual({ gesture:true, chips:true, map:false, scroll:false });
});

test('on a phone the tab bar, map credits and locate button do not overlap', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout: 5000 });
  // The tab bar is 64px plus the iPhone home-bar inset (none in this browser).
  expect(await page.evaluate(() => [...document.styleSheets].some(sheet => { try { return [...sheet.cssRules].some(rule => rule.cssText.includes('--nav-h: calc(64px + env(safe-area-inset-bottom')); } catch { return false; } }))).toBe(true);
  const nav = await page.locator('.workspace-rail').boundingBox();
  const locate = await page.locator('#recenter').boundingBox();
  expect(Math.round(nav.y + nav.height)).toBe(844);
  expect(locate.y + locate.height).toBeLessThanOrEqual(nav.y - 26);
  for (const tab of await page.locator('.workspace-rail .workspace-tab:visible').all()) {
    const box = await tab.boundingBox();
    expect(box.y).toBeGreaterThanOrEqual(nav.y);
    expect(box.y + box.height).toBeLessThanOrEqual(nav.y + nav.height + 0.5);
  }
});

test('the camera view opens straight away and finds your location while it runs', async ({ page }) => {
  // Location arrives late (as it can on a phone); the view must not give up.
  await page.evaluate(() => {
    let watcher = null;
    window.__deliverFix = () => watcher?.({ coords:{ latitude:54.11, longitude:-2.45 } });
    Object.defineProperty(navigator,'geolocation',{configurable:true,value:{
      getCurrentPosition(){}, watchPosition(success){ watcher = success; return 7; }, clearWatch(){},
    }});
    window.DeviceOrientationEvent = class extends Event {};
    const stream = document.createElement('canvas').captureStream();
    Object.defineProperty(navigator, 'mediaDevices', { configurable:true, value:{ getUserMedia: async () => stream } });
  });
  await page.route('**/api/around**', route => route.fulfill({ json:{ vehicles:[
    { id:'nr:north', type:'train', name:'1A23', destination:'LEEDS', operator:'Northern', lat:54.119, lon:-2.45 } ] } }));
  // Straight from the Map panel, without choosing a layer first.
  await page.locator('[data-job="camera"]').click();
  await expect(page.locator('#ar-view')).toBeVisible();
  await expect(page.locator('#ar-direction')).toHaveText('Finding your location…');
  for (const kind of ['train','bus','aircraft']) await expect(page.locator(`[data-ar-kind="${kind}"]`)).toHaveAttribute('aria-pressed','true');
  await page.waitForTimeout(9000);  // longer than the old 8-second give-up
  await expect(page.locator('#ar-view')).toBeVisible();
  await page.evaluate(() => window.__deliverFix());
  await page.evaluate(() => { for (let i = 0; i < 25; i += 1) { const e = new Event('deviceorientation'); e.webkitCompassHeading = 0; e.beta = 90; window.dispatchEvent(e); } });
  await expect(page.locator('#ar-direction')).toHaveText('Looking N, 0° up');
  await expect(page.locator('.ar-label.ar-train')).toContainText('1A23 to Leeds');
});

test('the Weather layer shows the rain radar with its time, scale and credit, and can play back', async ({ page }) => {
  const frames = ['20261115T1145','20261115T1200','20261115T1215'].map(key => ({ key, time:`${key.slice(0,4)}-${key.slice(4,6)}-${key.slice(6,8)}T${key.slice(9,11)}:${key.slice(11,13)}:00Z` }));
  await page.route('**/api/weather/radar', route => route.fulfill({ json:{ available:true, frames, bounds:[[49.4,-11.5],[61.2,2.6]], attribution:'Rain radar: EUMETNET OPERA, CC BY 4.0' } }));
  await page.route('**/api/weather/radar/*.png', route => route.fulfill({ contentType:'image/png', body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==','base64') }));
  await expect(page.locator('#radar-key')).toBeHidden();
  await page.locator('[data-layer-chip="weather"]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-rain-radar','20261115T1215');
  await expect(page.locator('.leaflet-image-layer')).toHaveAttribute('src','/api/weather/radar/20261115T1215.png');
  await expect(page.locator('#radar-key')).toBeVisible();
  await expect(page.locator('#radar-time')).toContainText('Rain radar');
  await expect(page.locator('#radar-credit')).toHaveText('EUMETNET OPERA, CC BY 4.0');
  const row = page.locator('#sub-chips');
  await expect(row.locator('.sub-chip:visible')).toHaveText(['Rain radar','Play last 2 hours']);
  // Playback starts two hours back and ends on the latest frame.
  await row.locator('[data-sub-action="radar-play"]').click();
  await expect(page.locator('#map')).toHaveAttribute('data-rain-radar','20261115T1145');
  await expect(page.locator('#radar-time')).toContainText('Rain at');
  await expect(page.locator('#map')).toHaveAttribute('data-rain-radar','20261115T1215', { timeout:5000 });
  await expect(row.locator('[data-sub-action="radar-play"]')).toHaveAttribute('aria-pressed','false');
  // Rain radar can be switched off on its own, and goes with the Weather layer.
  await row.locator('[data-sub-action="radar"]').click();
  await expect(page.locator('.leaflet-image-layer')).toHaveCount(0);
  await expect(page.locator('#radar-key')).toBeHidden();
  await row.locator('[data-sub-action="radar"]').click();
  await expect(page.locator('.leaflet-image-layer')).toHaveCount(1);
  await page.locator('[data-layer-chip="weather"]').click();
  await expect(page.locator('.leaflet-image-layer')).toHaveCount(0);
  await expect(page.locator('#map')).not.toHaveAttribute('data-rain-radar', /./);
});

test('a station page link opens that station in the app', async ({ page }) => {
  await page.route('**/api/places/rail-station*MKC/board**', route => route.fulfill({ json:{
    place:{ id:'rail-station:MKC', kind:'place', place_type:'rail_station', name:'Milton Keynes Central', crs:'MKC', lat:52.034, lon:-0.774 },
    board:{ mode:'network_rail', departures:[{ scheduled:'10:14', expected:'10:14', destination:'Manchester Piccadilly', platform:'4', status:'On time', headcode:'1A23' }], arrivals:[] } } }));
  await page.goto('/?place=rail-station:MKC');
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central', { timeout:8000 });
  expect(new URL(page.url()).search).toBe('');
});

test('Radar+ offers monthly or yearly with a free trial, and tracks the checkout', async ({ page }) => {
  const checkouts = [];
  await page.route('**/api/billing/status', route => route.fulfill({ json:{ enabled:true, plan:'free', plus:false, features:[], price_display:'£3.99/month',
    yearly_available:true, yearly_price_display:'£24.99/year', trial_days:7 } }));
  await page.route('**/api/billing/checkout**', route => { checkouts.push(new URL(route.request().url()).searchParams.get('period')); return route.fulfill({ status:503, json:{ detail:'Test stop' } }); });
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.evaluate(() => document.getElementById('radar-plus')?.click());
  await expect(page.locator('#plus-trial')).toHaveText('Free for 7 days, then charged unless you cancel. Cancel any time.');
  await expect(page.locator('#radar-plus-yearly')).toHaveText('£24.99/year · best value');
  await expect(page.locator('#radar-plus-checkout')).toHaveText('£3.99/month');
  await page.locator('#radar-plus-yearly').click();
  await expect.poll(() => checkouts).toEqual(['year']);
  await page.locator('#radar-plus-checkout').click();
  await expect.poll(() => checkouts).toEqual(['year','month']);
});

test('without Radar+, spotter alerts point to Radar+ once it can be bought', async ({ page }) => {
  await page.route('**/api/billing/status', route => route.fulfill({ json:{ enabled:true, plan:'free', plus:false, features:[], price_display:'£3.99/month', trial_days:7 } }));
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.04,longitude:-0.76}});}}}));
  let subscribed = 0;
  await page.route('**/api/alerts/subscribe', route => { subscribed += 1; return route.fulfill({ json:{ ok:true } }); });
  await page.getByRole('button', { name: /Discover/ }).click();
  await page.locator('#spotter-toggle').click();
  await expect(page.locator('#watch-toast')).toContainText('Spotter alerts are part of Radar+');
  await expect(page.locator('#plus-dialog')).toHaveAttribute('open', '');
  expect(subscribed).toBe(0);
});

test('on an iPhone in Safari, alerts say up front they need the Home Screen app', async ({ page }) => {
  const iphone = 'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1';
  await page.addInitScript(ua => Object.defineProperty(navigator, 'userAgent', { get: () => ua }), iphone);
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  const savedHint = page.locator('.commute-alert-card [data-install-hint]');
  await expect(savedHint).toBeVisible();
  await expect(savedHint).toContainText('Alerts need Radar on your Home Screen');
  await expect(savedHint).toContainText('Add to Home Screen');
  await page.getByRole('button', { name: /Discover/ }).click();
  await expect(page.locator('#spotter-card [data-install-hint]')).toBeVisible();
  // Tapping turn-on says the same instead of failing quietly.
  await page.locator('#spotter-toggle').click();
  await expect(page.locator('#watch-toast')).toContainText('Home Screen');
});

test('on a computer or Android, no Home Screen message is shown', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('.commute-alert-card [data-install-hint]')).toBeHidden();
});

test('Google Tag Manager loads only after analytics are allowed, and never for advertising', async ({ page }) => {
  const fs = require('fs');
  const html = fs.readFileSync(require('path').join(__dirname, '..', 'app/static/index.html'), 'utf8')
    .replace('{{GTM_CONTAINER_ID}}', 'GTM-N2JVR3QZ').replace('{{GA4_MEASUREMENT_ID}}', '');
  await page.route('https://radar.test/', route => route.fulfill({ contentType:'text/html', body:html }));
  const loaded = [];
  await page.route('https://www.googletagmanager.com/**', route => { loaded.push(route.request().url()); return route.fulfill({ contentType:'text/javascript', body:'' }); });
  await page.evaluate(()=>{ try{ localStorage.removeItem('ukRadarAnalyticsConsentV1'); }catch{} });
  await page.goto('https://radar.test/');
  await expect(page.locator('#analytics-consent')).toBeVisible();
  expect(loaded).toEqual([]);
  await page.locator('#analytics-accept').click();
  await expect.poll(()=>loaded.length).toBe(1);
  expect(loaded[0]).toContain('gtm.js?id=GTM-N2JVR3QZ');
  const layer = await page.evaluate(()=>window.dataLayer.map(row=>row.event || (row[0]==='consent' ? row[2] : null)).filter(Boolean));
  expect(layer).toContainEqual(expect.objectContaining({ ad_storage:'denied', ad_personalization:'denied' }));
  expect(layer).toContain('gtm.js');
});

test('with Roadworks or Incidents on, a Traffic cameras chip sits beside Live traffic', async ({ page }) => {
  await page.locator('[data-layer-chip="incident"]').click();
  const chip = page.locator('#sub-chips [data-sub-action="webcams"]');
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.camera-map-marker')).toHaveCount(1);
});

test('free visitors see which features are Radar+ before they tap; subscribers do not', async ({ page }) => {
  await expect(page.locator('#traffic-quick')).not.toHaveClass(/needs-plus/);   // fixture visitor has Radar+
  await page.route('**/api/billing/status', route => route.fulfill({ json:{ enabled:true, plan:'free', plus:false, features:[], price_display:'£3.99/month', trial_days:7 } }));
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  for (const id of ['#traffic-quick', '#live-webcams', '#journey-planner', '#track-activity']) await expect(page.locator(id)).toHaveClass(/needs-plus/);
  const label = await page.locator('#traffic-quick strong').evaluate(el => getComputedStyle(el, '::after').content);
  expect(label).toBe('"Radar+"');
  await expect(page.locator('#station-boards')).not.toHaveClass(/needs-plus/);
});

test('a subscriber can put Radar+ on another device with a one-time code', async ({ page }) => {
  await page.route('**/api/billing/device-code', route => route.fulfill({ json:{ code:'ABCD-2345', expires_in:600 } }));
  await page.evaluate(() => document.getElementById('radar-plus')?.click());
  await expect(page.locator('#radar-plus-have')).toBeHidden();
  await page.locator('#radar-plus-device').click();
  await expect(page.locator('#radar-plus-code')).toHaveText('ABCD-2345');

  // The other device: no Radar+ yet, types the code.
  let plus = false; const typed = [];
  await page.route('**/api/billing/status', route => route.fulfill({ json: plus
    ? { enabled:true, plan:'plus', plus:true, subscription_status:'active', price_display:'£3.99/month', features:['traffic_pro','live_cameras'] }
    : { enabled:true, plan:'free', plus:false, features:[], price_display:'£3.99/month' } }));
  await page.route('**/api/billing/device-link', route => { typed.push(route.request().postDataJSON().code); plus = true; return route.fulfill({ json:{ ok:true } }); });
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.evaluate(() => document.getElementById('radar-plus')?.click());
  await page.locator('#radar-plus-have summary').click();
  await page.locator('#radar-plus-link-code').fill('abcd 2345');
  await page.locator('#radar-plus-link button[type="submit"]').click();
  await expect(page.locator('#watch-toast')).toContainText('Radar+ is now on for this device');
  expect(typed).toEqual(['abcd 2345']);
  await expect(page.locator('#radar-plus-device')).toBeVisible();
});

test('a military jet with no airline still says what it is under its callsign', async ({ page }) => {
  await page.locator('[data-layer-chip="aircraft"]').click();
  await page.locator('.aerial-map-marker.aerial-aircraft').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-subtitle')).toHaveText(/(Eurofighter Typhoon • ZK001|Boeing CH-47 Chinook • ZH900)/);
});

test('train alerts offer stations, not bus stops', async ({ page }) => {
  await page.locator('#station-boards').click();
  await page.locator('#save-place').click();
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('[data-job="bus"]').click();
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await page.locator('#save-place').click();
  await page.locator('#detail-close').click();
  await page.locator('[data-workspace="live"]').click();
  const places = page.locator('#commute-alert-place option');
  await page.locator('#commute-alert-mode').selectOption('driving');
  await expect(places).toHaveText(['Milton Keynes Central', 'Central Station Stop A']);
  await page.locator('#commute-alert-mode').selectOption('train');
  await expect(places).toHaveText(['Milton Keynes Central']);
});

test('if Radar+ cannot be checked for a moment, it says so and keeps trying instead of going blank', async ({ page }) => {
  let fail = true;
  await page.route('**/api/billing/status', route => fail
    ? route.fulfill({ status:502, body:'Bad gateway' })
    : route.fulfill({ json:{ enabled:true, plan:'free', plus:false, features:[], price_display:'£3.99/month', portal_login_url:'https://billing.stripe.com/p/login/test123' } }));
  await page.evaluate(() => document.getElementById('radar-plus')?.click());
  await expect(page.locator('#plus-note')).toHaveText('Couldn’t check Radar+ just now. Trying again…');
  fail = false;
  await page.evaluate(() => document.getElementById('plus-close')?.click());
  await page.evaluate(() => document.getElementById('radar-plus')?.click());
  // Back: and a subscriber on this device without Radar+ can still cancel, by email.
  await expect(page.locator('#radar-plus-portal-login')).toBeVisible();
  await expect(page.locator('#radar-plus-portal-login')).toHaveAttribute('href', 'https://billing.stripe.com/p/login/test123');
});

test('the Radar+ box fits a computer screen with nothing cut off', async ({ page }) => {
  await page.setViewportSize({ width:1024, height:900 });
  await page.route('**/api/billing/status', route => route.fulfill({ json:{ enabled:true, plan:'free', plus:false, features:[], price_display:'£3.99/month', yearly_available:true, yearly_price_display:'£24.99/year', trial_days:7 } }));
  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.evaluate(() => document.getElementById('radar-plus')?.click());
  await page.locator('#radar-plus-have summary').click();   // the code box open: it pushed the card past the edge
  await expect(page.locator('#radar-plus-link-code')).toBeVisible();
  const overflow = await page.evaluate(() => {
    const shell = document.querySelector('#plus-dialog .plus-shell');
    const card = document.querySelector('#plan-cards .highlight');
    return { shell: shell.scrollWidth - shell.clientWidth, card: card.scrollWidth - card.clientWidth };
  });
  expect(overflow).toEqual({ shell:0, card:0 });
});

test('search shows results as you type, and a bus route number finds the nearest buses on it', async ({ page }) => {
  await page.locator('#search').pressSequentially('5');      // no Enter, one character
  await expect(page.locator('#search-results .search-result').first()).toContainText('Bus 5');
  await expect(page.locator('#search-results')).toContainText('To Nuneaton');
  const asked = page.searchLog.at(-1);
  expect(asked.q).toBe('5');
  expect(Number(asked.lat)).not.toBeNaN();                  // sent where the map is looking
  await page.locator('#search').fill('77');
  await expect(page.locator('#search-results')).toContainText('No buses on route 77 are reporting right now.');
});

test('typing in search does not empty the map', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(page.locator('.marker-wrap-train').first()).toBeAttached();
  const trains = await page.locator('.marker-wrap-train').count();
  await page.locator('#search').fill('Market Bosworth');
  await page.waitForTimeout(100);
  await expect(page.locator('.marker-wrap-train')).toHaveCount(trains);
});

test('buses show their route number on the map, and where they are going once zoomed in', async ({ page }) => {
  await page.locator('[data-layer-chip="bus"]').click();
  const tag = page.locator('.marker-wrap-bus .bus-route-tag').first();
  await expect(tag).toContainText('7');
  await expect(tag.locator('.bus-destination')).toHaveCount(0);   // no destination known yet
  await page.evaluate(() => {
    const snapshot = JSON.parse(JSON.stringify(window.__fixtureSnapshot));
    for (const row of snapshot.activities) if (row.id === 'bus-live-7') { row.destination = 'NUNEATON BUS STATION STAND 4'; row.last_updated = '2026-10-05T10:00:30Z'; }
    window.__fixtureSocket.onmessage({ data: JSON.stringify(snapshot) });
  });
  const where = page.locator('.marker-wrap-bus .bus-route-tag .bus-destination').first();
  await expect(where).toHaveCount(1);
  await expect(where).toBeHidden();                               // zoomed out: just the number
  await page.evaluate(() => document.getElementById('map').classList.add('show-bus-destinations'));
  await expect(where).toBeVisible();
  await expect(where).toHaveText(' · Nuneaton');                  // tidied: no "Bus Station Stand 4"
});

test('tapping a bus shows no photo and does not ask for one', async ({ page }) => {
  const asked = [];
  page.on('request', request => { if (request.url().includes('/media')) asked.push(request.url()); });
  await page.locator('[data-layer-chip="bus"]').click();
  await page.locator('.marker-wrap-bus .marker-bubble').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('Bus');
  await page.waitForTimeout(300);
  expect(asked).toEqual([]);
  await expect(page.locator('#detail-photo')).toBeHidden();
});
