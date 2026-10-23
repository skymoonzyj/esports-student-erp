import { defineConfig } from '@playwright/test'

export const loadingProfiles = {
  throttled: { offline: false, downloadThroughput: 1250000, uploadThroughput: 250000, latency: 150 },
  unthrottled: { offline: false, downloadThroughput: -1, uploadThroughput: -1, latency: 0 },
}

// No automatic server, artifacts, parallelism, or retries in performance runs.
// Imported directly by the supervised collector as its viewport/browser contract.
export default defineConfig({
  testDir: './scripts',
  testMatch: /loading\.spec\.ts/,
  workers: 1,
  retries: 0,
  fullyParallel: false,
  use: { browserName: 'chromium', headless: true, trace: 'off', screenshot: 'off', video: 'off' },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'mobile', use: { viewport: { width: 390, height: 844 } } },
  ],
})
