import path from 'node:path'
import { defineConfig } from '@playwright/test'
import { readPrivateJson } from './tests/talentOwnedConfig'

const file = process.env.FRONTEND_NAMED_CONFIG
if (!file) throw new Error('Owned release runner required')
const payload = readPrivateJson(file)
const origin = new URL(payload.baseURL)
if (payload.check !== 'release-window' || origin.protocol !== 'http:' || origin.hostname !== '127.0.0.1'
  || !origin.port || origin.pathname !== '/' || origin.search || origin.hash || origin.username || origin.password) throw new Error('Fixed owned release origin required')
export default defineConfig({
  testDir: './tests', testMatch: 'frontend-release-window.spec.ts', workers: 1, retries: 0,
  fullyParallel: false, timeout: 900000,
  outputDir: path.join(path.dirname(file), 'browser-output'),
  reporter: [['json', { outputFile: path.join(path.dirname(file), 'browser-results.json') }]],
  metadata: { releaseWindow: payload },
  projects: [{ name: 'release-window' }],
  use: { baseURL: payload.baseURL, browserName: 'chromium', headless: true,
    actionTimeout: 15000, navigationTimeout: 45000,
    serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
