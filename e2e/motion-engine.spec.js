const { test, expect } = require('@playwright/test');
const fs = require('node:fs');
const path = require('node:path');

const engine = fs.readFileSync(path.resolve(__dirname, '../app/static/motion-engine.js'));

async function openEngine(page) {
  await page.route('**/*', route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/static/motion-engine.js') {
      return route.fulfill({ contentType: 'text/javascript', body: engine });
    }
    return route.fulfill({
      contentType: 'text/html',
      body: '<!doctype html><script src="/static/motion-engine.js"></script>',
    });
  });
  await page.goto('http://radar.test/motion-engine-test');
}

test('train report corrections remain on mapped railway geometry', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const now = Date.now();
    const path = [[52, -1], [52, -0.8], [52.2, -0.8]];
    const first = {
      id: 'train-1', type: 'train', lat: 52, lon: -1, estimate_path: path,
      estimate_started_at: new Date(now - 50000).toISOString(),
      estimate_due_at: new Date(now + 50000).toISOString(),
    };
    const initial = engine.present(first, now, false);
    const corrected = {
      ...first,
      estimate_started_at: new Date(now - 90000).toISOString(),
      estimate_due_at: new Date(now + 10000).toISOString(),
    };
    const during = engine.present(corrected, now + 900, false);
    const settled = engine.present(corrected, now + 5000, false);
    return {
      initial,
      during,
      settled,
      duringProjection: engine.nearestPathFraction(path, during.lat, during.lon),
    };
  });

  expect(result.initial.lon).toBeCloseTo(-0.8, 5);
  expect(result.duringProjection.distance).toBeLessThan(0.001);
  expect(result.settled.lat).toBeGreaterThan(result.during.lat);
  expect(result.settled.lon).toBeCloseTo(-0.8, 5);
});

test('aircraft freeze at their safe projection instead of jumping backwards', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const now = Date.now();
    const aircraft = {
      id: 'air-1', type: 'aircraft', lat: 52, lon: -1, bearing: 90, speed: 360,
      last_updated: new Date(now - 30000).toISOString(),
    };
    return {
      atThirtySeconds: engine.present(aircraft, now, false),
      atFortySeconds: engine.present(aircraft, now + 10000, false),
      reduced: engine.present(aircraft, now, true),
    };
  });

  expect(result.atThirtySeconds.lon).toBeGreaterThan(-0.99);
  expect(result.atFortySeconds.lon).toBeCloseTo(result.atThirtySeconds.lon, 6);
  expect(result.reduced.lon).toBe(-1);
});

test('buses move between BODS reports and stop projecting after one safe interval', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const now = Date.now();
    const bus = { id:'bus-7', type:'bus', lat:52, lon:-1, bearing:90, speed:10,
      last_updated:new Date(now-5000).toISOString() };
    return {
      moving:engine.present(bus,now,false),
      capped:engine.present(bus,now+30000,false),
      cappedLater:engine.present(bus,now+60000,false),
    };
  });
  expect(result.moving.lon).toBeGreaterThan(-1);
  expect(result.capped.lon).toBeGreaterThan(result.moving.lon);
  expect(result.cappedLater.lon).toBeCloseTo(result.capped.lon,6);
});

test('reduced motion keeps confirmed train coordinates', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const now = Date.now();
    return window.UKActivityMotionEngine.present({
      id: 'train-reduced', type: 'train', lat: 52, lon: -1,
      estimate_path: [[52, -1], [53, -1]],
      estimate_started_at: new Date(now - 50000).toISOString(),
      estimate_due_at: new Date(now + 50000).toISOString(),
    }, now, true);
  });

  expect(result.lat).toBe(52);
  expect(result.lon).toBe(-1);
});

test('a train without proven track geometry stays at its confirmed report', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const now = Date.now();
    return window.UKActivityMotionEngine.present({
      id: 'train-unresolved', type: 'train', lat: 52, lon: -1,
      reported_lat: 52, reported_lon: -1, next_lat: 53, next_lon: 0,
      estimate_started_at: new Date(now - 50000).toISOString(),
      estimate_due_at: new Date(now + 50000).toISOString(),
    }, now, false);
  });

  expect(result.lat).toBe(52);
  expect(result.lon).toBe(-1);
  expect(result.position_method).not.toBe('estimated');
});

test('a device clock that runs fast does not push trains ahead of the server estimate', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const deviceNow = Date.now();
    const serverNow = deviceNow - 40000; // device is 40 seconds fast
    engine.setServerTime(serverNow, deviceNow);
    const train = {
      id: 'train-clock', type: 'train', lat: 52, lon: -1, estimate_path: [[52, -1], [52, -0.9]],
      estimate_started_at: new Date(serverNow - 50000).toISOString(),
      estimate_due_at: new Date(serverNow + 50000).toISOString(),
    };
    return engine.present(train, deviceNow, false);
  });
  // Halfway along the leg on the server clock, not 90% of the way.
  expect(result.lon).toBeCloseTo(-0.95, 3);
});

test('a train that loses its estimate glides to the confirmed report instead of teleporting', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const now = Date.now();
    const moving = {
      id: 'train-glide', type: 'train', lat: 52, lon: -1, estimate_path: [[52, -1], [52, -0.98]],
      estimate_started_at: new Date(now - 50000).toISOString(),
      estimate_due_at: new Date(now + 50000).toISOString(),
    };
    const before = engine.present(moving, now, false);
    const confirmed = { id: 'train-glide', type: 'train', lat: 52, lon: -0.98, position_method: 'network_report' };
    const start = engine.present(confirmed, now + 100, false);
    const during = engine.present(confirmed, now + 500, false);
    const settled = engine.present(confirmed, now + 5000, false);
    return { before, start, during, settled };
  });
  expect(result.before.lon).toBeCloseTo(-0.99, 4);
  expect(result.start.lon).toBeCloseTo(-0.99, 4);
  expect(result.during.lon).toBeGreaterThan(-0.99);
  expect(result.during.lon).toBeLessThan(-0.98);
  expect(result.settled.lon).toBe(-0.98);
  expect(result.settled.position_method).toBe('network_report');
});

test('a slightly late report holds the train instead of reversing it along the track', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const now = Date.now();
    const path = [[52, -1], [52, -0.98]]; // ~1.4 km
    const first = {
      id: 'train-hold', type: 'train', lat: 52, lon: -1, estimate_path: path,
      estimate_started_at: new Date(now - 60000).toISOString(),
      estimate_due_at: new Date(now + 40000).toISOString(),
    };
    const shown = engine.present(first, now, false);
    // A new leg says the train is only 40% along: hold rather than reverse.
    const late = { ...first, estimate_started_at: new Date(now - 40000).toISOString(), estimate_due_at: new Date(now + 60000).toISOString() };
    const held = engine.present(late, now + 500, false);
    const stillHeld = engine.present(late, now + 15000, false);
    const resumed = engine.present(late, now + 40000, false);
    return { shown, held, stillHeld, resumed };
  });
  expect(result.held.lon).toBeCloseTo(result.shown.lon, 6);
  expect(result.stillHeld.lon).toBeCloseTo(result.shown.lon, 6);
  expect(result.resumed.lon).toBeGreaterThan(result.shown.lon);
});

test('aircraft are shown between their two latest reports, a report-gap behind, never ahead', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const t0 = Date.now() - 20000;
    const plane = (seconds, lon) => ({ id:'air-i', type:'aircraft', lat:52, lon, bearing:90, speed:360,
      last_updated:new Date(t0 + seconds * 1000).toISOString() });
    engine.present(plane(0, -1.0), t0 + 1000, false);
    // Second report 10 s later, 0.1° east, seen as it arrives (the map runs
    // every frame). Shown 8 s behind (the gap, capped at 8 s): at t0+17.5 s
    // it is at t0+9.5 s, 95% of the way to the second report.
    engine.present(plane(10, -0.9), t0 + 10500, false);
    const halfway = engine.present(plane(10, -0.9), t0 + 17500, false);
    const atNewest = engine.present(plane(10, -0.9), t0 + 20000 + 2500, false);
    return { halfway: halfway.lon, atNewest: atNewest.lon };
  });
  expect(result.halfway).toBeCloseTo(-0.905, 4);
  expect(result.atNewest).toBeGreaterThan(-0.91);       // reaching the newest report, plus a little projection
  expect(result.atNewest).toBeLessThan(-0.85);
});

test('a new aircraft report is blended in over a few seconds instead of snapping', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const t0 = Date.now() - 60000;
    const first = { id:'air-b', type:'aircraft', lat:52, lon:-1, bearing:90, speed:360, last_updated:new Date(t0).toISOString() };
    const before = engine.present(first, t0 + 20000, false);              // projected 20 s east
    // The next report shows it turned: it is well short of the projection.
    const second = { ...first, lon:-0.99, bearing:0, last_updated:new Date(t0 + 20000).toISOString() };
    const justAfter = engine.present(second, t0 + 20000, false);
    const later = engine.present(second, t0 + 20000 + 3000, false);
    const raw = engine.present(second, t0 + 20000 + 60000, false);
    return { before: before.lon, justAfter: justAfter.lon, later: later.lon, rawLon: raw.lon };
  });
  expect(result.justAfter).toBeCloseTo(result.before, 4);             // no snap
  // Settled: shown 8 s behind (the cap), between the two reports.
  expect(result.later).toBeCloseTo(-1 + 0.01 * (15 / 20), 4);
});

test('a bus pulling in at a stop eases back to its report rather than snapping', async ({ page }) => {
  await openEngine(page);
  const result = await page.evaluate(() => {
    const engine = window.UKActivityMotionEngine;
    engine.reset();
    const t0 = Date.now() - 60000;
    const moving = { id:'bus-s', type:'bus', lat:52, lon:-1, bearing:90, speed:10, last_updated:new Date(t0).toISOString() };
    const before = engine.present(moving, t0 + 15000, false);             // projected 150 m east
    const stopped = { ...moving, lon:-0.9995, speed:0, last_updated:new Date(t0 + 15000).toISOString() };
    const justAfter = engine.present(stopped, t0 + 15000, false);
    const settled = engine.present(stopped, t0 + 15000 + 3500, false);
    return { before: before.lon, justAfter: justAfter.lon, settled: settled.lon };
  });
  expect(result.justAfter).toBeCloseTo(result.before, 5);
  expect(result.settled).toBeCloseTo(-0.9995, 6);
});
