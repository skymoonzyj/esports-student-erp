import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { test } from 'node:test'
import { chromium } from '@playwright/test'
import { completeHeldCorrection } from '../tests/helpers/correctionCompletion.mjs'
/* global document, Event */

test('completion witness detects publication only after the genuine module finishes evaluating', async () => {
  const server = createServer((request, response) => {
    if (request.url === '/assets/CorrectionDraft-fixture.js') {
      response.setHeader('Content-Type', 'text/javascript')
      response.end('document.documentElement.dataset.fixtureWaiting="true"; await new Promise(resolve => document.addEventListener("fixture-evaluate", resolve, {once:true})); document.body.append(document.createElement("textarea")); export const CorrectionDraft = true;')
    } else response.end('<!doctype html><body>Bounded module lifecycle fixture</body>')
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  const browser = await chromium.launch({ headless: true })
  try {
    const page = await browser.newPage()
    let held
    let requests = 0
    let acceptHeld
    const intercepted = new Promise(resolve => { acceptHeld = resolve })
    await page.route('**/assets/CorrectionDraft-fixture.js', route => { requests++; held = route; acceptHeld() })
    await page.goto(`http://127.0.0.1:${server.address().port}/`)
    // Original consumer begins first. The completion helper must reuse this import.
    await page.evaluate(() => { void import('/assets/CorrectionDraft-fixture.js') })
    await intercepted
    const response = page.waitForResponse(reply => reply.request() === held.request())
    const completed = completeHeldCorrection(page, held)
    let settled = false
    void completed.then(() => { settled = true })
    await (await response).finished()
    assert.equal(await page.locator('textarea').count(), 0, 'the old immediate absence check falsely passes')
    assert.equal(settled, false, 'HTTP completion alone is not module evaluation completion')
    await page.waitForFunction(() => document.documentElement.dataset.fixtureWaiting === 'true')
    // Explicit lifecycle event, not a sleep or substituted module implementation.
    await page.evaluate(() => document.dispatchEvent(new Event('fixture-evaluate')))
    assert.deepEqual(await completed, { moduleEvaluated: true, responseFinished: true })
    assert.equal(requests, 1, 'module-map witness reuses the original request')
    await assert.rejects(async () => assert.equal(await page.locator('textarea').count(), 0),
      error => error.code === 'ERR_ASSERTION' && error.actual === 1)
  } finally {
    await browser.close()
    await new Promise(resolve => server.close(resolve))
  }
})
