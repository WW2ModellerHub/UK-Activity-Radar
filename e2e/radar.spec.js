const { test, expect } = require('@playwright/test');
const { installAppFixture } = require('./support/app-fixture');

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

test('Buses near me enables buses and opens the nearest live stop board', async ({ page }) => {
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:52.036,longitude:-0.772}});}}}));
  await page.locator('#near-me').click();
  await expect(page.locator('[data-layer-toggle="bus"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','52.036,-0.772');
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
  await expect(page.locator('#detail-placeboard')).toContainText('Arriva Midlands');
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

test('live stream deltas update one activity and remove ended activity without a full snapshot', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('[data-layer-toggle="bus"]').click();
  await expect(page.locator('.marker-bubble')).toHaveCount(3);
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
  await expect(page.locator('.marker-bubble')).toHaveCount(2);
  await expect(page.locator('#map')).toHaveAttribute('data-activity-count','2');
  await expect.poll(() => page.evaluate(() => window.__leafletStats.markerMoves)).toBeGreaterThan(movesBefore);
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
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚆' })).toHaveCount(2);
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚌' })).toHaveCount(0);
  await page.locator('[data-layer-toggle="bus"]').click();
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚌' })).toHaveCount(1);
  await page.locator('[data-layer-toggle="train"]').click();
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚆' })).toHaveCount(0);
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
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚆' })).toHaveCount(2);

  await page.locator('.workspace-tab[data-workspace="settings"]').click();
  await expect.poll(() => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  )).toEqual(['train']);

  await page.locator('#heat-mode').selectOption('bus');
  await expect.poll(() => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  )).toEqual(['train', 'bus']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveAttribute('data-point-count', '1');
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚆' })).toHaveCount(2);
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚌' })).toHaveCount(0);

  await page.locator('#heat-mode').selectOption('off');
  await expect.poll(() => page.evaluate(() =>
    window.__fixtureSocketMessages.filter(row => row.action === 'viewport').at(-1)?.types
  )).toEqual(['train']);
  await expect(page.locator('.leaflet-heat-layer')).toHaveCount(0);
  await expect(page.locator('.marker-bubble').filter({ hasText: '🚆' })).toHaveCount(2);
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
  await expect(page.locator('.cool-map-marker')).toHaveCount(1);
  await expect(page.locator('.cool-marker-wrap').first()).toHaveAttribute('data-clustered','false');
  await expect(page.locator('.cool-badge').filter({ hasText: 'STEAM' }).first()).toBeVisible();
  // Cool markers come from the national Cool feed, not only the current
  // viewport snapshot, so zooming out cannot silently discard a category.
  await expect(page.locator('.aerial-map-marker')).toHaveCount(3);
  await expect(page.locator('.aerial-marker-wrap').first()).toHaveAttribute('data-clustered', 'false');
  await expect(page.locator('#ranking-title')).toHaveText(/Cool stuff now • UK/);
  await expect(page.locator('#ranking')).toContainText('RAF AWACS SENTINEL01');
  await expect(page.locator('#ranking')).not.toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('.ticker-items')).not.toContainText('RNLI RESCUE CONTEXT');
  await expect(page.locator('#cool-summary')).toContainText('4 matched live');
  await expect(page.locator('#cool-summary .cool-find-row')).toHaveCount(3);
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
  await expect(page.locator('.cool-map-marker')).toHaveCount(1);
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

test('Discover renders its live view and private Field Guide without missions or requesting location', async ({ page }) => {
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
  expect(await page.evaluate(() => window.__geolocationRequests)).toBe(0);
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
  expect(await page.evaluate(() => window.__geolocationRequests)).toBe(0);

  await page.reload();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|FIXTURE/, { timeout:5000 });
  await page.locator('[data-workspace="cool"]').click();
  await page.locator('[data-cool-view="guide"]').click();
  await expect(page.locator('#field-guide-grid .field-guide-card').filter({ hasText:'Steam Train' })).toHaveClass(/discovered/);
  await expect(page.locator('#field-guide-progress')).toContainText('1 of 3 discovered');
  expect(await page.evaluate(() => Number(sessionStorage.getItem('__radarTestGeolocationRequests') || 0))).toBe(0);
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
  await expect(page.locator('#save-discovery')).toHaveText('◇ Save discovery');
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
  await page.locator('.marker-bubble').filter({ hasText: '⚠️' }).evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('M1 collision');
  await expect(page.locator('#detail-photo')).toContainText('Nearest live camera');
  await expect(page.locator('#detail-photo')).toContainText('1.2 km');
});

test('live detail can track a moving object across snapshots', async ({ page }) => {
  await page.locator('[data-layer-toggle="bus"]').click();
  await page.locator('.marker-bubble').filter({ hasText: '🚌' }).evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
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
  await expect(page.locator('#track-activity')).toContainText('Stop tracking');
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
  const marker=page.locator('.aircraft-shape-helicopter').first();
  await expect(marker).toBeVisible();
  await expect(marker.locator('.rotorcraft-silhouette .map-helicopter-svg')).toHaveCount(1);
  await expect(marker.locator('.rotor-disc')).toHaveCount(1);
  await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src','/static/assets/aircraft/chinook-raf.jpg');
  await expect(page.locator('#detail-photo')).toContainText('Reference photo of this aircraft type');
  await expect(page.locator('#detail-photo')).toContainText('Adrian Pingstone');
  await expect(page.locator('#detail-photo .aircraft-reference-visual')).toHaveCount(0);
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
  await page.evaluate(()=>Object.defineProperty(navigator,'geolocation',{configurable:true,value:{getCurrentPosition(success){success({coords:{latitude:54.11,longitude:-2.45}});}}}));
  await page.locator('#overhead-now').click();
  await expect(page.locator('#map')).toHaveAttribute('data-map-center','54.11,-2.45');
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','true');
  await expect(page.locator('#overhead-now')).toContainText('Aircraft received within 50 miles');
  await expect(page.locator('.aerial-map-marker').first()).toBeVisible();
  await page.locator('#overhead-now').click();
  await expect(page.locator('[data-layer-toggle="aircraft"]')).toHaveAttribute('aria-pressed','false');
  await expect(page.locator('#overhead-now')).toContainText('Aircraft received within 50 miles');
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
  await expect(page.locator('#detail-photo img')).toHaveAttribute('src','https://upload.wikimedia.org/fixture-ryanair.jpg');
  await expect(page.locator('#detail-photo .image-truth')).toContainText('this aircraft registration');
  await expect(page.locator('#detail-photo')).toContainText('CC BY-SA 4.0');
});

test('activity marker hover shows facts and click opens and closes detail', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  const marker = page.locator('.marker-bubble').filter({ hasText: '🚆' }).first();
  await marker.evaluate(el => el.closest('.leaflet-marker-icon').dispatchEvent(new MouseEvent('mouseenter')));
  await expect(page.locator('.activity-tooltip')).toContainText('London Express');
  await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-sidebar')).not.toHaveClass(/is-empty/);
  await expect(page.locator('#detail-title')).toContainText('London Express');
  await page.locator('#detail-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
});

test('clicked trains and buses load source-backed route intelligence', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble').filter({ hasText: '🚆' }).first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-intelligence')).toContainText('JOURNEY');
  await expect(page.locator('#detail-intelligence')).toContainText('Watford Junction');

  await page.locator('#detail-close').click();
  await page.locator('[data-layer-toggle="bus"]').click();
  await page.locator('.marker-bubble').filter({ hasText: '🚌' }).first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-intelligence')).toContainText('LIVE BUS JOURNEY');
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
  await expect(page.locator('#detail-placeboard')).toContainText('DEPARTURES & ARRIVALS');
  await expect(page.locator('#detail-placeboard')).toContainText('Manchester Piccadilly');
  await expect(page.locator('#detail-placeboard')).toContainText('View live');
  await page.locator('#detail-placeboard [data-live-id="train-live-1"]').click();
  await expect(page.locator('#detail-title')).toContainText('London Express');
  await expect(page.locator('#detail-intelligence')).toContainText('IDENTIFIED ROLLING STOCK');
  await expect(page.locator('#detail-intelligence')).toContainText('Class 43');
  await expect(page.locator('.rolling-stock-card')).toHaveAttribute('data-livery-theme', 'intercity');
  await expect(page.locator('.rolling-stock-card')).toContainText('InterCity');
  await expect(page.locator('.transport-run-card')).toHaveAttribute('data-livery-theme', 'east-midlands');
  await expect(page.locator('#detail-panel')).toHaveClass(/operator-theme-train/);
  await expect(page.locator('#detail-operator')).toContainText('Fixture Rail');
});

test('scheduled station-board train opens its published route without a live marker', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#detail-placeboard')).toContainText('View route');
  await page.locator('#detail-placeboard [data-train-uid="C12345"]').click();
  await expect(page.locator('#detail-title')).toContainText('1Z80');
  await expect(page.locator('#detail-intelligence')).toContainText('JOURNEY');
  await expect(page.locator('#detail-intelligence')).toContainText('Coventry');
  await expect(page.locator('#detail-intelligence')).toContainText('Published timetable • no live map position');
});

test('Share live page works directly from a selected activity', async ({ page }) => {
  await page.locator('[data-layer-toggle="train"]').click();
  await page.locator('.marker-bubble').filter({hasText:'🚆'}).first().evaluate(el=>el.dispatchEvent(new MouseEvent('click',{bubbles:true})));
  await page.locator('#share-activity').click();
  await expect.poll(()=>page.evaluate(()=>window.__sharedPayload?.url || '')).toContain('/live/train-live-1');
  await expect.poll(()=>page.evaluate(()=>window.__sharedPayload?.text || '')).toContain('London Express');
});

test('clicking a bus stop opens live timetable information', async ({ page }) => {
  await page.locator('.useful-details summary').click();
  await page.locator('#place-mode').selectOption('all');
  const stop = page.locator('.place-marker').filter({ hasText: '🚏' });
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
  await page.locator('.marker-bubble').filter({ hasText: '🚆' }).first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-sidebar')).toBeVisible();
  const box = await page.locator('#detail-sidebar').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.height).toBeLessThan(500);
  expect(await page.locator('.transport-run-card').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  const mobileRail = await page.locator('#workspace-shell').boundingBox();
  expect(mobileRail.width).toBeLessThanOrEqual(52);
  expect(box.x).toBeGreaterThanOrEqual(60);
  const mapBox = await page.locator('#map').boundingBox();
  expect(mapBox.y + mapBox.height).toBeGreaterThan(820);
  await page.locator('#detail-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
  await page.locator('[data-workspace="cool"]').click();
  expect(await page.locator('.cool-filter').evaluateAll(cards => cards.every(card => card.scrollWidth <= card.clientWidth))).toBe(true);
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
  await expect.poll(async()=>Number((await page.locator('#workspace-shell').boundingBox()).width)).toBeLessThan(80);
  const shell=await page.locator('#workspace-shell').boundingBox();
  expect(shell.x).toBeLessThanOrEqual(12);
  expect(shell.height).toBeGreaterThan(150);
  await expect(page.locator('[data-workspace="live"]')).toBeVisible();
  await page.locator('#workspace-collapse').click();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
});
