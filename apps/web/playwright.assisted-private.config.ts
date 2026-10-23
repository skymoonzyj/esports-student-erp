import path from 'node:path'
import { defineConfig } from '@playwright/test'
import original from './playwright.config'
import { assistedPrivateConfig } from './tests/assistedPrivateConfig'

const payload = assistedPrivateConfig()
if (!payload) throw new Error('Private functional suite required')
const specs: Record<string, string[]> = {
  assisted: ['assisted-personnel.spec.ts'], team: ['team-management.spec.ts'],
  registration: ['self-service-registration.spec.ts', 'self-service-registration-privacy.spec.ts'],
  'housing-occupancy': ['housing-occupancy-lifecycle.spec.ts', 'housing-occupancy-revocation.spec.ts'],
  'housing-reporting': ['housing-reporting-lifecycle.spec.ts', 'housing-reporting-authorization.spec.ts'],
}
const projects = ['assisted', 'team'].includes(payload.suite) ? ['desktop-1440', 'mobile-360']
  : ['desktop-1440', 'desktop-1024', 'tablet-768', 'mobile-360']
export default defineConfig({ ...original, testMatch: specs[payload.suite], workers: 1, retries: 0,
  reporter: [['json', { outputFile: path.join(path.dirname(process.env.ASSISTED_PRIVATE_CONFIG!), 'browser-results.json') }]],
  projects: original.projects!.filter(item => projects.includes(item.name!)),
  use: { ...original.use, baseURL: payload.baseURL, serviceWorkers: 'block', trace: 'off', screenshot: 'off', video: 'off' },
})
