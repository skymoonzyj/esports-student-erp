import { readFileSync } from 'node:fs'
import path from 'node:path'
import { defineConfig } from '@playwright/test'

const configFile = process.env.FRONTEND_NAMED_CONFIG
if (!configFile) throw new Error('Owned named runner required')
const payload = JSON.parse(readFileSync(configFile, 'utf8'))
if (payload.check !== 'direct-document-diagnostic') throw new Error('Unknown named check')

export default defineConfig({
  testDir: './tests', testMatch: 'frontend-direct-document.spec.ts',
  workers: 1, retries: 0, fullyParallel: false, timeout: 900000,
  outputDir: path.join(path.dirname(configFile), 'browser-output'),
  reporter: [['json', { outputFile: path.join(path.dirname(configFile), 'browser-results.json') }]],
  metadata: { directDocument: payload },
  use: { baseURL: payload.baseURL, browserName: 'chromium', headless: true,
    serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
