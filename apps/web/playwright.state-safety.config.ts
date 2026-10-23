import path from 'node:path'
import { defineConfig } from '@playwright/test'
import { readPrivateJson } from './tests/talentOwnedConfig'

const file = process.env.FRONTEND_NAMED_CONFIG
if (!file) throw new Error('Owned state runner required')
const payload = readPrivateJson(file)
const origin = new URL(payload.baseURL)
if (payload.check !== 'state-safety' || origin.protocol !== 'http:' || origin.hostname !== '127.0.0.1'
  || !origin.port || origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) throw new Error('Fixed owned state origin required')
export default defineConfig({
  testDir: './tests', testMatch: 'frontend-state-safety.spec.ts', workers: 1, retries: 0,
  fullyParallel: false, timeout: 120000,
  outputDir: path.join(path.dirname(file), 'browser-output'),
  reporter: [['json', { outputFile: path.join(path.dirname(file), 'browser-results.json') }]],
  metadata: { stateSafety: payload },
  use: { browserName: 'chromium', headless: true, actionTimeout: 15000, navigationTimeout: 30000,
    serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
