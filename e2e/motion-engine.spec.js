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
