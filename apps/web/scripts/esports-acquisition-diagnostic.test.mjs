import assert from 'node:assert/strict'
import { test } from 'node:test'
import { JSDOM } from 'jsdom'
import { installPcClickWitness, readPcClickWitness, validateAcquisitionDiagnostic, unavailableNative } from '../tests/helpers/esportsAcquisitionDiagnostic.mjs'

test('missing passive state is unavailable and does not invent focus or activation', () => {
  const window = new JSDOM('<body></body>').window
  assert.equal(readPcClickWitness(window), null)
  assert.equal(installPcClickWitness(window), false)
})
test('passive witness records only the exact original button and never promotes untrusted input', () => {
  const window = new JSDOM('<section aria-label="测试场景" data-host-phase="preflight"><button>检查设备并进入试练</button><button>private unrelated button</button></section>').window
  assert.equal(installPcClickWitness(window), true)
  window.document.querySelectorAll('button')[1].click()
  assert.equal(readPcClickWitness(window).click_count, 0)
  // Unit fixture only; the production spec retains its original real locator click.
  window.document.querySelector('button').click()
  const result = readPcClickWitness(window)
  assert.equal(result.click_count, 1)
  assert.equal(result.click_trusted, false)
  assert.equal(result.click_activation, false)
  assert.doesNotMatch(JSON.stringify(result), /private|检查/)
  readPcClickWitness(window, true)
  assert.equal(readPcClickWitness(window), null)
})
function fixture() {
  const window = new JSDOM('<section aria-label="测试场景" data-host-phase="preflight"><button>检查设备并进入试练</button></section>').window
  installPcClickWitness(window)
  return { configured_headed_valid: true, configured_headed: true, configured_custom_executable: false,
    configured_executable_available: true, configured_in_owned_cache: true, fixture_chromium: true,
    fixture_version_valid: true, fixture_version: [145, 0, 1, 2], before: unavailableNative(),
    failure: unavailableNative(), passive: readPcClickWitness(window) }
}
test('closed acquisition schema accepts fixed evidence and explicit native unavailability', () => {
  const value = fixture()
  assert.deepEqual(validateAcquisitionDiagnostic(value), value)
  assert.equal(value.before.foreground_matches_owned, null)
  assert.equal(value.before.executable_matches_configured, null)
  assert.deepEqual(validateAcquisitionDiagnostic({ ...value, passive: null }), { ...value, passive: null })
})
test('invalid types, raw identities and unknown nested fields are rejected without disclosure', () => {
  const valid = fixture()
  for (const invalid of [
    { ...valid, path: '/private/user/path' },
    { ...valid, fixture_version: 'private browser UA' },
    { ...valid, before: { ...valid.before, pid: 123 } },
    { ...valid, failure: { ...valid.failure, foreground_matches_owned: 'true' } },
    { ...valid, passive: { ...valid.passive, click_count: -1 } },
    { ...valid, passive: { ...valid.passive, click_trusted: 'true' } },
  ]) assert.equal(validateAcquisitionDiagnostic(invalid), null)
})
