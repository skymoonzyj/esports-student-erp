import { readFileSync } from 'node:fs'
import path from 'node:path'
import { defineConfig } from '@playwright/test'

const configFile = process.env.FRONTEND_NAMED_CONFIG
if (!configFile) throw new Error('Owned named runner required')
const payload = JSON.parse(readFileSync(configFile, 'utf8'))
if (payload.check !== 'optional-panels') throw new Error('Unknown named check')
const url = new URL(payload.baseURL)
if (url.protocol !== 'http:' || url.hostname !== '127.0.0.1' || !url.port) throw new Error('Owned loopback required')

export default defineConfig({
  testDir: './tests', testMatch: 'frontend-optional-panels.spec.ts',
  workers: 1, retries: 0, fullyParallel: false, timeout: 90000,
  outputDir: path.join(path.dirname(configFile), 'browser-output'),
  reporter: [['json', { outputFile: path.join(path.dirname(configFile), 'browser-results.json') }]],
  metadata: { optionalPanels: payload },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 900 } } },
    { name: 'touch', use: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } },
  ],
  use: { baseURL: payload.baseURL, browserName: 'chromium', headless: true,
    serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
