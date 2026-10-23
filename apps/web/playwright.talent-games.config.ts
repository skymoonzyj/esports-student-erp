import path from 'node:path'
import { defineConfig } from '@playwright/test'
import { talentOwnedConfig } from './tests/talentOwnedConfig'

const payload = talentOwnedConfig()
if (!payload) throw new Error('Owned talent runner required')
const entries = [
  ['talent-tests.spec.ts', 'desktop-1440', 1440, 900],
  ['talent-touch.spec.ts', 'touch-mobile-390', 390, 844],
  ['talent-dynamic-vision.spec.ts', 'desktop-1440', 1440, 900],
  ['talent-dynamic-vision.spec.ts', 'desktop-1024', 1024, 768],
  ['talent-dynamic-vision.spec.ts', 'tablet-768', 768, 1024],
  ['talent-dynamic-vision.spec.ts', 'mobile-360', 360, 800],
  ['talent-dynamic-vision.spec.ts', 'touch-mobile-390', 390, 844],
] as const
const [spec, project, width, height] = entries[payload.entry as number]
const directory = path.dirname(process.env.FRONTEND_NAMED_CONFIG!)
export default defineConfig({
  testDir: './tests', testMatch: payload.mode === 'existing' ? spec : 'frontend-talent-resources.spec.ts',
  ...(payload.mode === 'history' ? { grep: /cold (?:history|authorized report)/ } : payload.mode === 'resources' ? { grep: /cold selected resources:/ } : {}),
  workers: 1, retries: 0, fullyParallel: false, timeout: 120000,
  outputDir: path.join(directory, 'browser-output'),
  reporter: [['json', { outputFile: path.join(directory, 'browser-results.json') }]],
  metadata: { talentGames: payload },
  projects: [{ name: project, use: { viewport: { width, height }, ...(project === 'touch-mobile-390' ? { hasTouch: true, isMobile: true } : {}) } }],
  use: { baseURL: payload.baseURL, browserName: 'chromium', headless: true,
    serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
