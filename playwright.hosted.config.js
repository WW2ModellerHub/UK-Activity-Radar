const { defineConfig, devices } = require('@playwright/test');

const baseURL = String(process.env.RADAR_BASE_URL || '').replace(/\/$/, '');
if (!/^https:\/\//.test(baseURL)) {
  throw new Error('RADAR_BASE_URL must be the deployed HTTPS origin, for example https://radar.example.com');
}

module.exports = defineConfig({
  testDir: './e2e',
  testMatch: 'hosted-smoke.spec.js',
  fullyParallel: false,
  workers: 1,
  retries: 1,
  reporter: [['list']],
  timeout: 45_000,
  use: {
    baseURL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    ...devices['Desktop Chrome'],
  },
});
