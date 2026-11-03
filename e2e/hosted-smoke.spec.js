const { test, expect } = require('@playwright/test');

test('hosted launch surface is healthy and commercially configured', async ({ page, request }) => {
  const healthResponse = await request.get('/api/health');
  expect(healthResponse.ok()).toBeTruthy();
  const health = await healthResponse.json();
  expect(health.ok).toBe(true);
  expect(health.commercial_profile).toBe(true);
  expect(health.base_map_ready).toBe(true);
  expect(health.storage_healthy).toBe(true);
  expect(health.billing_enabled).toBe(true);
  expect(health.seller_details_ready).toBe(true);

  const readinessResponse = await request.get('/api/readiness');
  expect(readinessResponse.ok()).toBeTruthy();
  const readiness = await readinessResponse.json();
  expect(readiness.launch.ready, JSON.stringify(readiness.launch)).toBe(true);
  expect(readiness.launch.blockers).toEqual([]);
  expect(readiness.operations.storage.healthy).toBe(true);
  expect(readiness.operations.request_budgets.tiles.remaining).toBeGreaterThan(0);

  await page.goto('/');
  await expect(page.locator('#map')).toBeVisible();
  await expect(page.locator('.leaflet-container')).toBeVisible();
  await expect(page.locator('#mode-pill')).toHaveText(/LIVE|RECONNECTING/, {timeout:15_000});
  await expect(page.locator('#product-summary')).toContainText('Radar+ £3.99/month');
});

test('hosted legal pages contain final seller, billing and contact copy', async ({ page }) => {
  await page.goto('/terms');
  await expect(page.locator('body')).toContainText('£3.99 per month');
  await expect(page.locator('body')).toContainText('Stripe');
  await expect(page.locator('body')).not.toContainText('pending');
  await expect(page.locator('a[href^="mailto:"]')).toHaveCount(2);

  await page.goto('/privacy');
  await expect(page.locator('body')).toContainText('Radar+ subscriptions and Stripe');
  await expect(page.locator('body')).toContainText('Full card numbers');
  await expect(page.locator('body')).not.toContainText('pending');
});
