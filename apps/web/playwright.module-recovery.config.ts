import { readFileSync } from 'node:fs'
import path from 'node:path'
import { defineConfig } from '@playwright/test'

const configFile = process.env.FRONTEND_NAMED_CONFIG
if (!configFile) throw new Error('Owned named runner required')
const payload = JSON.parse(readFileSync(configFile, 'utf8'))
if (payload.check !== 'module-recovery') throw new Error('Unknown named check')

export default defineConfig({
  testDir: './tests',
  testMatch: 'frontend-module-recovery.spec.ts',
  workers: 1, retries: 0, fullyParallel: false, timeout: 60000,
  outputDir: path.join(path.dirname(configFile), 'browser-output'),
  reporter: [['json', { outputFile: path.join(path.dirname(configFile), 'browser-results.json') }]],
  metadata: { moduleRecovery: payload },
  use: {
    baseURL: payload.baseURL, browserName: 'chromium', headless: true,
    actionTimeout: 15000, navigationTimeout: 30000,
    viewport: { width: 1440, height: 900 }, serviceWorkers: 'block',
    trace: 'off', screenshot: 'off', video: 'off',
  },
})
