import { test } from '@playwright/test'
import { observeDirectDocuments } from './helpers/directDocumentJourney'

test('fixed direct document boundary evidence', async ({ browser }, info) => {
  info.annotations.push({ type: 'direct-browser-version', description: browser.version() })
  await observeDirectDocuments(browser, info)
})
