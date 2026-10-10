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
  await expect(page.locator('.ticker-item')).toContainText(['London Express', 'Bus 7']);
});

test('Useful and Cool workspaces alter visible layers and render Cool badges', async ({ page }) => {
  await expect(page.locator('.marker-bubble')).toHaveCount(3);
  await page.getByRole('button', { name: /Cool/ }).click();
  await expect(page.locator('body')).toHaveAttribute('data-experience', 'cool');
  await expect(page.locator('.cool-map-marker')).toHaveCount(2);
  await expect(page.locator('.cool-badge').filter({ hasText: 'STEAM' }).first()).toBeVisible();
  await expect(page.locator('.cool-badge').filter({ hasText: 'NAVAL' }).first()).toBeVisible();
});

test('left workspace drawer collapses and reopens', async ({ page }) => {
  await page.locator('#workspace-collapse').click();
  await expect(page.locator('#workspace-shell')).toHaveClass(/collapsed/);
  await page.getByRole('button', { name: /Cool/ }).click();
  await expect(page.locator('#workspace-shell')).not.toHaveClass(/collapsed/);
  await expect(page.locator('[data-workspace-panel="cool"]')).toBeVisible();
});

test('activity marker hover shows facts and click opens and closes detail', async ({ page }) => {
  const marker = page.locator('.marker-bubble').filter({ hasText: '🚆' }).first();
  await marker.evaluate(el => el.closest('.leaflet-marker-icon').dispatchEvent(new MouseEvent('mouseenter')));
  await expect(page.locator('.activity-tooltip')).toContainText('London Express');
  await marker.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-sidebar')).not.toHaveClass(/is-empty/);
  await expect(page.locator('#detail-title')).toContainText('London Express');
  await page.locator('#detail-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
});

test('Station Board opens a real fixture station and departure', async ({ page }) => {
  await page.locator('#station-boards').click();
  await expect(page.locator('#detail-title')).toContainText('Milton Keynes Central');
  await expect(page.locator('#detail-placeboard')).toContainText('LIVE STATION BOARD');
  await expect(page.locator('#detail-placeboard')).toContainText('Manchester Piccadilly');
});

test('clicking a bus stop opens live timetable information', async ({ page }) => {
  const stop = page.locator('.place-marker').filter({ hasText: '🚏' });
  await expect(stop).toBeVisible();
  await stop.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('Central Station Stop A');
  await expect(page.locator('#detail-placeboard')).toContainText('Live predicted buses');
  await expect(page.locator('#detail-placeboard')).toContainText('City Centre');
});

test('TomTom flow toggle and weather radar expose active map state', async ({ page }) => {
  await page.locator('#traffic-quick').click();
  await expect(page.locator('#traffic-mode')).toHaveValue('combined');
  await expect(page.locator('#map')).toHaveAttribute('data-traffic', 'combined');
  await expect(page.locator('#traffic-legend')).not.toHaveClass(/hidden/);
  await page.locator('#weather-radar').click();
  await expect(page.locator('#weather-radar')).toHaveClass(/active/);
  await expect(page.locator('#weather-radar-legend')).not.toHaveClass(/hidden/);
  await expect(page.locator('#weather-radar-time')).toContainText('Frame');
});

test('maritime marker opens vessel details', async ({ page }) => {
  await page.getByRole('button', { name: /Cool/ }).click();
  const naval = page.locator('.cool-map-marker').filter({ hasText: 'NAVAL' });
  await naval.evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-title')).toContainText('HMS TESTER');
  await expect(page.locator('#detail-fields')).toContainText('235000001');
});

test('Live Audio loads a specialist stream and play control updates player', async ({ page }) => {
  await page.locator('.workspace-tab[data-workspace="live"]').click();
  await expect(page.locator('#map-radio-list')).toContainText('Fixture Coast Radio');
  await page.locator('[data-radio-stream]').click();
  await expect(page.locator('#radio-now')).toContainText('Fixture Coast Radio');
  await expect(page.locator('#radio-player')).toHaveAttribute('src', 'https://audio.invalid/live.mp3');
  await expect(page.locator('#radio-player')).toHaveAttribute('data-played', 'true');
});

test('mobile viewport keeps core navigation, map, and details usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator('#map')).toBeVisible();
  await expect(page.locator('#workspace-shell')).toBeVisible();
  await page.locator('.marker-bubble').filter({ hasText: '🚆' }).first().evaluate(el => el.dispatchEvent(new MouseEvent('click', { bubbles:true })));
  await expect(page.locator('#detail-sidebar')).toBeVisible();
  const box = await page.locator('#detail-sidebar').boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  await page.locator('#detail-close').click();
  await expect(page.locator('#detail-sidebar')).toHaveClass(/is-empty/);
});
