import path from 'node:path'
import { defineConfig } from '@playwright/test'
import original from './playwright.config'
import { esportsOwnedConfig } from './tests/esportsOwnedConfig'

const payload = esportsOwnedConfig()
if (!payload) throw new Error('Owned esports runner required')
const entries = [
  ['esports-pc.spec.ts', 'esports-desktop-1440'],
  ['esports-touch.spec.ts', 'esports-touch-844x390'],
  ['esports-recovery.spec.ts', 'esports-desktop-1440'],
  ['esports-capabilities.spec.ts', 'esports-desktop-1440'],
  ['esports-capabilities.spec.ts', 'esports-touch-844x390'],
  ['esports-capabilities.spec.ts', 'esports-webkit-capabilities'],
] as const
const [spec, project] = entries[payload.entry as number]
const directory = path.dirname(process.env.FRONTEND_NAMED_CONFIG!)
export default defineConfig({
  testDir: './tests', workers: 1, retries: 0, fullyParallel: false, timeout: 120000,
  outputDir: path.join(directory, 'browser-output'),
  reporter: [['json', { outputFile: path.join(directory, 'browser-results.json') }]],
  metadata: { esportsGames: payload },
  projects: payload.mode !== 'audio' ? original.projects!.filter(item => item.name === project).map(item => ({ ...item, testMatch: spec }))
    : [{ name: 'audio-desktop', testMatch: 'frontend-loading.spec.ts', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
      { name: 'audio-touch', testMatch: 'frontend-loading.spec.ts', use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } }],
  use: { baseURL: payload.baseURL, serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
