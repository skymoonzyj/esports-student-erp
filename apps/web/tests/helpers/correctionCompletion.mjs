import { URL } from 'node:url'
/* global requestAnimationFrame */

/** Complete only the already-intercepted real correction import; no caller URL.
 * @param {import('@playwright/test').Page} page
 * @param {import('@playwright/test').Route} held
 */
export async function completeHeldCorrection(page, held) {
  const request = held.request()
  const url = new URL(request.url())
  if (url.origin !== new URL(page.url()).origin || url.search || url.hash
    || !/^\/assets\/CorrectionDraft-[A-Za-z0-9_-]+\.js$/.test(url.pathname)
    || request.resourceType() !== 'script') throw new Error('Expected already-held same-origin correction module')
  const response = page.waitForResponse(reply => reply.request() === request)
  await held.continue()
  const actual = await response
  if (actual.status() !== 200 || await actual.finished() !== null) throw new Error('Correction module did not finish successfully')
  // The browser module map joins the original in-flight import. This awaits actual
  // evaluation (including dependencies), not just HTTP200 or download completion.
  await page.evaluate(async exactHeldUrl => {
    await import(/* @vite-ignore */ exactHeldUrl)
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  }, request.url())
  return { moduleEvaluated: true, responseFinished: true }
}
