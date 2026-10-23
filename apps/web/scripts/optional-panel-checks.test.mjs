import { test } from 'node:test'
import assert from 'node:assert/strict'
import { assertOwnRequestDelta } from './optional-panel-checks.mjs'

test('accepts exactly one new own request and one submission POST', () => {
  assert.doesNotThrow(() => assertOwnRequestDelta(['existing'], ['existing', 'submitted'], 'submitted', 1))
})
test('rejects a second request with a different ID despite the known ID appearing once', () => {
  assert.throws(() => assertOwnRequestDelta(['existing'], ['existing', 'submitted', 'duplicate-other-id'], 'submitted', 2))
})
test('rejects duplicate submission POSTs even when only one record survived', () => {
  assert.throws(() => assertOwnRequestDelta(['existing'], ['existing', 'submitted'], 'submitted', 2))
})
test('rejects an apparent +1 total that replaced an existing record', () => {
  assert.throws(() => assertOwnRequestDelta(['existing'], ['submitted', 'other'], 'submitted', 1))
})
