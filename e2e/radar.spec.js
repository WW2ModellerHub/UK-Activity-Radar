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
  await page.locator('#near-me').click();
  await expect(page.locator('[data-layer-toggle="bus"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','52.036,-0.772');
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
  await expect(page.locator('#detail-placeboard')).toContainText('Arriva Midlands');
  await expect(page.locator('.place-marker')).toHaveCount(1);
  await expect(page.locator('.place-marker.place-bus_stop .bus-stop-sign')).toHaveCount(1);
  await expect(page.locator('#nearby-note')).toHaveClass(/hidden/);
  await page.locator('#detail-close').click();
  await expect(page.locator('.place-marker')).toHaveCount(0);
});

test('Today on the Radar shows verified live highlights', async ({ page }) => {
  await expect(page.locator('#today-radar')).toContainText('Steam Railtour 1Z80');
  await expect(page.locator('#today-radar')).toContainText('BEST NOW');
  await page.locator('#today-radar [data-today-activity="today-remote-1"]').click();
  await expect(page.locator('#detail-title')).toContainText('Steam Railtour 1Z80');
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','57.12,-3.15');
  await expect(page.locator('[data-layer-toggle="train"]')).toHaveAttribute('aria-pressed','true');
});

test('My Radar saves a real station locally', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('#saved-places-list')).toContainText('Milton Keynes Central');
  await expect(page.locator('#my-radar-area')).toContainText('saved area');
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
  await expect(page.locator('#commute-alert-note')).toContainText('train near Milton Keynes Central');
});

test('shared live pages show broad facts without coordinates', async ({ page }) => {
  await page.goto('/live/train-live-1');
  await expect(page.locator('#share-card h1')).toContainText('London Express');
  await expect(page.locator('#share-card')).toContainText('Central Britain');
  await expect(page.locator('#share-card')).not.toContainText('52.0');
  await expect(page.locator('#share-card')).toContainText('No precise coordinates');
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

test('operator filter isolates live vehicles and Clear map resets it', async ({ page }) => {
  await page.locator('[data-layer-toggle="aircraft"]').click();
  await expect(page.locator('.aerial-aircraft')).toHaveCount(3);
  await page.locator('.useful-details summary').click();
  await expect(page.locator('#operator-filter')).toContainText('Ryanair');
  await page.locator('#operator-filter').selectOption('Ryanair');
  await expect(page.locator('.aerial-aircraft')).toHaveCount(1);
  await expect(page.locator('.aerial-aircraft')).toContainText('737-8200');
  await page.locator('#clear-map').click();
  await expect(page.locator('#operator-filter')).toHaveValue('all');
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

test('every density choice requests its live data and renders or explains the result', async ({ page }) => {
  await page.locator('.workspace-tab[data-workspace="settings"]').click();

  const lastViewportTypes = () => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  );

  await page.locator('#heat-mode').selectOption('train');
  await expect.poll(lastViewportTypes).toEqual(['train']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveAttribute('data-point-count', '2');
  await expect(page.locator('#bus-zoom-note')).toContainText('2 live points in rail density');

  await page.locator('#heat-mode').selectOption('bus');
  await expect.poll(lastViewportTypes).toEqual(['bus']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveAttribute('data-point-count', '1');

  await page.locator('#heat-mode').selectOption('incident');
  await expect.poll(lastViewportTypes).toEqual(['incident']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveAttribute('data-point-count', '1');

  await page.locator('#heat-mode').selectOption('road');
  await expect.poll(lastViewportTypes).toEqual(['road']);
  await expect(page.locator('#bus-zoom-note')).toContainText('No roadworks density data in this view');
  await expect(page.locator('.leaflet-heat-layer')).toHaveCount(0);

  await page.locator('#heat-mode').selectOption('weather');
  await expect.poll(lastViewportTypes).toEqual(['weather', 'flood']);
  await expect(page.locator('#bus-zoom-note')).toContainText('No weather density data in this view');

  await page.locator('#heat-mode').selectOption('all');
  await expect.poll(lastViewportTypes).toBeNull();
  await expect(page.locator('body')).toHaveAttribute('data-experience', 'settings');
  await expect(page.locator('.leaflet-heat-layer')).toHaveAttribute('data-point-count', '8');

  await page.locator('#heat-mode').selectOption('off');
  await expect.poll(lastViewportTypes).toEqual([]);
  await expect(page.locator('.leaflet-heat-layer')).toHaveCount(0);
});

test('density is additive and Off restores the selected Useful layers', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(page.locator('.marker-bubble.marker-train')).toHaveCount(2);

  await page.locator('.workspace-tab[data-workspace="settings"]').click();
  await expect.poll(() => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  )).toEqual(['train']);

  await page.locator('#heat-mode').selectOption('bus');
  await expect.poll(() => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  )).toEqual(['train', 'bus']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveAttribute('data-point-count', '1');
  await expect(page.locator('.marker-bubble.marker-train')).toHaveCount(2);
  await expect(page.locator('.marker-bubble.marker-bus')).toHaveCount(0);

  await page.locator('#heat-mode').selectOption('off');
  await expect.poll(() => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  )).toEqual(['train']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveCount(0);
  await expect(page.locator('.marker-bubble.marker-train')).toHaveCount(2);
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
  await expect(page.locator('.cool-badge').filter({ hasText: 'STEAM' }).first()).toBeVisible();
  // Cool markers come from the national Cool feed, not only the current
  // viewport snapshot, so zooming out cannot silently discard a category.
  await expect(page.locator('.aerial-map-marker')).toHaveCount(3);
  await expect(page.locator('.aerial-marker-wrap').first()).toHaveAttribute('data-clustered', 'false');
  await expect(page.locator('#ranking-title')).toHaveText(/Cool stuff now • UK/);
  await expect(page.locator('#ranking')).toContainText('RAF AWACS SENTINEL01');
  await expect(page.locator('#ranking')).toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('.ticker-items')).toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('#cool-summary')).toContainText('5 matched live');
  await expect(page.locator('#cool-summary .cool-find-row')).toHaveCount(4);
  await expect(page.locator('#cool-summary')).toContainText(/1\s*RESCUE/);
  await page.locator('.cool-filter[data-type="cool-rescue"]').click();
  await expect(page.locator('#ranking')).toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('#cool-summary')).toContainText('LIVE INCIDENT');
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
    explanation:'Steam-hauled movement.', icon:'♨', confidence:'high', freshness:'live', observed_at:'2026-09-28T10:00:00Z',
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
  await page.locator('.workspace-tab[data-workspace="settings"]').click();
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
  await expect(page.locator('#detail-photo')).toContainText('Eurofighter Typhoon');
  await expect(page.locator('#detail-photo .image-truth')).toContainText('Example of this aircraft type');
  await page.evaluate(() => window.__fixtureSocket.onmessage({ data: JSON.stringify(window.__fixtureSnapshot) }));
  await page.waitForTimeout(50);
  expect(mediaCalls).toBe(1);
});

test('known military helicopters show a bundled real photograph when live image lookup is unavailable', async ({ page }) => {
  await page.route('**/api/activity/aircraft-live-2/media', route => route.fulfill({ json:{ ok:false, reason:'no_match' } }));
  await page.locator('[data-layer-toggle="aircraft"]').click();
  const marker=page.locator('.aircraft-shape-chinook').first();
  await expect(marker).toBeVisible();
  await expect(marker.locator('.rotorcraft-silhouette .map-chinook-svg')).toHaveCount(1);
  await expect(marker.locator('.map-chinook-svg .ac-rotor')).toHaveCount(1);
  await expect(marker.locator('.map-chinook-svg .ac-body')).toHaveCount(1);
  await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src','/static/assets/aircraft/chinook-raf.jpg');
  await expect(page.locator('#detail-photo')).toContainText('Reference photo of this aircraft type');
  await expect(page.locator('#detail-photo')).toContainText('Adrian Pingstone');
  await expect(page.locator('#detail-photo .aircraft-reference-visual')).toHaveCount(0);
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

test('selected aircraft draws an honest browser-local live trail', async ({ page }) => {
  await page.goto('/');
  await page.locator('[data-workspace="cool"]').click();
  await page.locator('.aerial-map-marker.aerial-aircraft').first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-trail')).toContainText('Recording from the next reported position');
  await page.evaluate(() => {
    const moved={
      ...window.__fixtureSnapshot,
      activities:window.__fixtureSnapshot.activities.map(row=>row.id==='aircraft-live-1'?{...row,lat:Number(row.lat)+0.18,lon:Number(row.lon)+0.12,last_updated:'2026-10-05T10:01:00Z'}:row),
    };
    window.__fixtureSocket.onmessage({data:JSON.stringify(moved)});
  });
  await expect(page.locator('#detail-trail')).toContainText('observed positions');
  await expect(page.locator('#detail-trail')).toContainText('not a complete historical flight path');
  await expect(page.locator('#map')).toHaveAttribute('data-selected-trail-points','2');
});

test('What’s above me loads nearby aircraft and can be turned off', async ({ page }) => {
  await page.goto('/');
  let nearbyRequests=0;
  page.on('request',request=>{if(new URL(request.url()).pathname==='/api/aircraft/nearby')nearbyRequests+=1;});
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('#overhead-now').click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','54.11,-2.45');
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#overhead-now')).toContainText('live aircraft');
  await expect(page.locator('.aerial-map-marker').first()).toBeVisible();
  await expect.poll(()=>nearbyRequests,{timeout:10000}).toBeGreaterThan(1);
  await page.locator('#overhead-now').click();
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('#overhead-now')).toContainText('Aircraft received within 50 miles');
});

test('Discover clears above-me radius and returns to the full UK feed', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('#overhead-now').click();
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
  await page.getByRole('button',{name:/Go to location Leicester city centre/}).click();
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
  await expect(page.locator('#detail-fields [data-field="Route"]')).toContainText('London Stansted (STN) → Dublin (DUB)');
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src','https://upload.wikimedia.org/fixture-ryanair.jpg');
  await expect(page.locator('#detail-photo .image-truth')).toContainText('this aircraft registration');
  await expect(page.locator('#detail-photo')).toContainText('CC BY-SA 4.0');
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
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src','https://upload.wikimedia.org/fixture-arriva-bus.jpg');
  await expect(page.locator('#detail-photo')).toContainText('Fixture Transport Photographer');
});

test('journey planner resolves places and renders a real multimodal itinerary', async ({ page }) => {
  await page.locator('#journey-planner').click();
  await expect(page.locator('#journey-dialog')).toBeVisible();
  await page.locator('#journey-origin').fill('Milton Keynes');
  await page.locator('#journey-destination').fill('London');
  await page.locator('#journey-submit').click();
  await expect(page.locator('#journey-results')).toContainText('LIVE ROUTES');
  await expect(page.locator('#journey-results')).toContainText('West Coast');
  await expect(page.locator('#journey-results')).toContainText('London Euston');
  await expect(page.locator('#journey-results')).toContainText('36 min');
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

test('Incidents enables the live local incident overlay without unlocking Radar+ flow', async ({ page }) => {
  await page.locator('[data-layer-toggle="incident"]').click();
  await expect(page.locator('[data-layer-toggle="incident"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#map')).toHaveAttribute('data-traffic','incidents');
  await expect(page.locator('#traffic-style')).toHaveValue('incidents');
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

test('mobile nearby bar summarises the map and opens the Map panel', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.locator('[data-workspace="useful"]').click();
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await expect(page.locator('#nearby-bar')).toContainText('Choose what to show');
  await page.locator('[data-layer-chip="train"]').click();
  await expect(page.locator('#nearby-bar-text')).toContainText(/train/);
  await page.locator('#nearby-bar').click();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
  await expect(page.locator('#near-me')).toBeVisible();
});

test('mobile tabs put an open detail sheet away', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('#near-me').click();
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
  for (const selector of ['#near-me','#overhead-now']) {
    const box=await page.locator(selector).boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeLessThanOrEqual(66);
    expect(box.x+box.width).toBeLessThanOrEqual(390);
  }
  const shortcutStack=await page.locator('.location-quick-actions').boundingBox();
  expect(shortcutStack.height).toBeLessThanOrEqual(140);
  const openWorkspace=await page.locator('#workspace-shell').boundingBox();
  expect(openWorkspace.height).toBeLessThanOrEqual(540);
  await page.locator('#near-me').click();
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
  await page.locator('#save-place').click();
  await page.locator('[data-workspace="live"]').click();
  await expect(page.locator('[data-workspace-panel="live"] .experience-hero')).toContainText('Your regular stops');
  await expect(page.locator('#saved-places-list')).toContainText('Central Station Stop A');
  await expect(page.locator('#passing-next')).toContainText('City Centre');
  await expect(page.locator('.commute-alert-card')).toContainText('Travel alerts');
  await expect(page.locator('.alert-options')).not.toHaveAttribute('open','');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBe(true);
});

test('mobile nearby aircraft leaves the map visible and uses a compact toast', async ({ page }) => {
  await page.setViewportSize({ width:390, height:844 });
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('#overhead-now').click();
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await expect(page.locator('#watch-toast')).toContainText('positioned aircraft');
  const toast=await page.locator('#watch-toast').boundingBox();
  expect(toast.width).toBeLessThanOrEqual(370);
  expect(toast.height).toBeLessThan(90);
  // Collapsed on a phone means only the bottom tab bar remains over the map.
  await expect.poll(async()=>Number((await page.locator('#workspace-shell').boundingBox()).height)).toBeLessThanOrEqual(72);
  const shell=await page.locator('#workspace-shell').boundingBox();
  expect(shell.y+shell.height).toBeGreaterThanOrEqual(840);
  await expect(page.locator('[data-workspace="live"]')).toBeVisible();
  await page.locator('#nearby-bar').click();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
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
  await page.locator('#overhead-now').click();
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
