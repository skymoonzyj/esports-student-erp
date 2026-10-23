import assert from 'node:assert/strict'
import { test } from 'node:test'
import { JSDOM } from 'jsdom'
import { readPcPreflight, validatePcPreflight } from '../tests/helpers/esportsPreflightDiagnostic.mjs'

function fixture(probe, reasons = []) {
  const document = new JSDOM('<section aria-label="测试场景" data-host-phase="preflight"><section aria-label="设备检查"><details><pre></pre></details></section><p role="alert">Never export this alert</p><button>检查设备并进入试练</button></section>').window.document
  document.querySelector('pre').textContent = typeof probe === 'string' ? probe : JSON.stringify(probe)
  for (const reason of reasons) {
    const element = document.createElement('li')
    element.setAttribute('data-capability-reason', reason)
    document.querySelector('[aria-label="设备检查"]').append(element)
  }
  return document
}
const allowed = { input_kind: 'mouse', width_css_px: 799.9, height_css_px: 450.2,
  pointer_lock_supported: true, pointer_lock_active: true, raw_mouse_requested: false,
  raw_mouse_confirmed: false, webgl2_supported: true, private_ignored: 'never-export' }
test('missing probe is explicitly unavailable, not a valid negative capability', () => {
  const result = readPcPreflight(new JSDOM('<body></body>').window.document)
  assert.equal(result.probe_present, false)
  assert.equal(result.probe_parse_valid, false)
  assert.equal(result.capability_types_valid, false)
  assert.equal(result.width_css_px, null)
  assert.deepEqual(validatePcPreflight(result), result)
})
test('malformed JSON and illegal field types remain unavailable without exporting input', () => {
  for (const value of ['not-json-secret', { ...allowed, input_kind: 'private-input', width_css_px: '123', pointer_lock_active: 'private-value' }]) {
    const result = readPcPreflight(fixture(value))
    assert.equal(result.capability_types_valid, false)
    assert.equal(result.input_kind_valid, false)
    assert.equal(result.dimensions_valid, false)
    assert.doesNotMatch(JSON.stringify(result), /private-|secret|never-export/)
    assert.deepEqual(validatePcPreflight(result), result)
  }
})
test('actual saved probe projection retains typed capability and precise threshold evidence only', () => {
  const result = readPcPreflight(fixture(allowed, ['stage_size', 'pointer_lock_inactive']))
  assert.equal(result.probe_present && result.probe_parse_valid && result.capability_types_valid, true)
  assert.equal(result.pointer_lock_active, true)
  assert.equal(result.input_kind, 'mouse')
  assert.equal(result.width_css_px, 799)
  assert.equal(result.height_css_px, 450)
  assert.equal(result.width_ge_800, false)
  assert.equal(result.height_ge_450, true)
  assert.equal(result.reason_stage_size, true)
  assert.equal(result.reason_pointer_lock_inactive, true)
  assert.equal(result.initial_button_count, 1)
  assert.equal(result.initial_button_enabled, true)
  assert.equal(result.alert_present, true)
  assert.doesNotMatch(JSON.stringify(result), /never-export|Never export/)
  assert.deepEqual(validatePcPreflight(result), result)
})
test('unknown reason is only a boolean; strict annotation validation rejects extra keys and types', () => {
  const result = readPcPreflight(fixture(allowed, ['private-reason-value']))
  assert.equal(result.reason_unknown_present, true)
  assert.doesNotMatch(JSON.stringify(result), /private-reason-value/)
  assert.equal(validatePcPreflight({ ...result, private_extra: 'secret' }), null)
  assert.equal(validatePcPreflight({ ...result, pointer_lock_active: 'true' }), null)
  assert.equal(validatePcPreflight({ ...result, width_css_px: 900000 }), null)
  assert.equal(validatePcPreflight({ ...result, input_kind: 'private' }), null)
})
