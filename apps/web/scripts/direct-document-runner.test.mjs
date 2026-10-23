import assert from 'node:assert/strict'
import { test } from 'node:test'
import * as runner from './run-direct-document.mjs'

test('report projection never copies private titles, URLs, errors or malformed annotations into evidence', () => {
  assert.equal(typeof runner.projectDirectReport, 'function')
  const report = { suites: [{ specs: [{ title: 'PRIVATE', tests: [{ status: 'unexpected',
    annotations: [{ type: 'direct-document-evidence', description: JSON.stringify([{ url: 'http://PRIVATE', headers: 'PRIVATE' }]) },
      { type: 'direct-browser-version', description: '151.0.7922.34' }],
    results: [{ status: 'failed', errors: [{ message: 'PRIVATE', stack: 'PRIVATE' }] }] }] }] }] }
  const result = runner.projectDirectReport(report, 1)
  assert.equal(result.ok, false)
  assert.deepEqual(result.phases, [null])
  assert.equal(result.notRunPhases, 32)
  assert.equal(result.browserVersion, '151.0.7922.34')
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|http:|headers|stack|message/)
  report.suites[0].specs[0].tests[0].annotations[0].description = 'PRIVATE invalid JSON'
  assert.equal(runner.projectDirectReport(report, 0).ok, false)
  assert.equal(runner.projectDirectReport({}, 0).ok, false)
})

test('missing, duplicate and incomplete browser attempts cannot become a successful arm', () => {
  assert.equal(typeof runner.projectDirectReport, 'function')
  const row = { status: 'expected', annotations: [{ type: 'direct-document-evidence', description: '[]' }], results: [{ status: 'passed' }] }
  const report = { suites: [{ specs: [{ tests: [row] }] }] }
  assert.equal(runner.projectDirectReport(report, 0).ok, false)
  row.annotations.push({ type: 'direct-document-evidence', description: '[]' })
  assert.equal(runner.projectDirectReport(report, 0).ok, false)
  report.suites[0].specs[0].tests.push(row)
  assert.equal(runner.projectDirectReport(report, 0).ok, false)
})

test('final phase projection rejection remains distinguishable from independent action and observer evidence', () => {
  const failures = [{ kind: 'action_exception', stage: 'navigate', phaseIndex: 0, errorName: 'TypeError',
    frames: [{ file: 'directDocumentJourney.ts', line: 63, column: 26 }] },
  { kind: 'observer_exception', stage: 'snapshot', phaseIndex: 0, errorName: 'Error', frames: [] }]
  const row = { status: 'unexpected', results: [{ status: 'failed', errors: [{ message: 'PRIVATE' }] }], annotations: [
    { type: 'direct-document-evidence', description: JSON.stringify([{ raw: 'PRIVATE' }]) },
    { type: 'direct-document-failures', description: JSON.stringify(failures) },
  ] }
  const report = { suites: [{ specs: [{ tests: [row] }] }] }
  const result = runner.projectDirectReport(report, 1)
  assert.equal(result.ok, false)
  assert.deepEqual(result.failures?.slice(0, 2), failures)
  assert.equal(result.failures?.some(f => f.kind === 'projection_rejected' && f.stage === 'phase_projection'), true)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|message|stack/)
  row.annotations[1].description = JSON.stringify([{ ...failures[0], message: 'PRIVATE' }])
  const malicious = runner.projectDirectReport(report, 1)
  assert.equal(malicious.failures?.some(f => f.kind === 'projection_rejected' && f.stage === 'failure_projection'), true)
  assert.doesNotMatch(JSON.stringify(malicious), /PRIVATE|message|stack/)
})

test('malformed report envelopes retain a bounded projection rejection instead of losing the failure output', () => {
  for (const report of [null, { suites: [null] }, { suites: [{ specs: [null] }] },
    { suites: [{ specs: [{ tests: [{ status: 'expected', annotations: [
      { type: 'direct-document-evidence', description: '[]' }, { type: 'direct-document-failures', description: '[]' },
    ], results: [null] }] }] }] },
    { suites: [{ specs: [{ tests: [{ annotations: [null], results: [] }] }] }] }]) {
    const result = runner.projectDirectReport(report, 1)
    assert.equal(result.ok, false)
    assert.equal(result.failures.some(f => f.kind === 'projection_rejected'), true)
  }
})

test('closed first-rejection annotation survives null phase but rejects missing, malicious or unbounded metadata', () => {
  const rejection = { schemaVersion: 1, phaseIndex: 0, validator: 'ready_projection', field: 'resource.encoded_bytes', rule: 'integer_bounds', itemIndex: 5 }
  const annotations = [
    { type: 'direct-document-evidence', description: '[null]' },
    { type: 'direct-document-failures', description: '[]' },
    { type: 'direct-document-rejections', description: JSON.stringify([rejection]) },
  ]
  const report = { suites: [{ specs: [{ tests: [{ status: 'unexpected', results: [{ status: 'failed' }], annotations }] }] }] }
  assert.deepEqual(runner.projectDirectReport(report, 1).rejections, [rejection])
  for (const bad of [{ ...rejection, value: 'PRIVATE' }, { ...rejection, field: 'PRIVATE' }, { ...rejection, rule: 'PRIVATE' },
    { ...rejection, validator: 'PRIVATE' }, { ...rejection, itemIndex: 128 }, { ...rejection, phaseIndex: 33 }]) {
    annotations[2].description = JSON.stringify([bad])
    const result = runner.projectDirectReport(report, 1)
    assert.deepEqual(result.rejections, [])
    assert.equal(result.failures.some(f => f.stage === 'rejection_projection'), true)
    assert.doesNotMatch(JSON.stringify(result), /PRIVATE|value/)
  }
  annotations[2].description = JSON.stringify([rejection, rejection])
  assert.equal(runner.projectDirectReport(report, 1).failures.some(f => f.stage === 'rejection_projection'), true)
  annotations.pop()
  assert.equal(runner.projectDirectReport(report, 1).failures.some(f => f.stage === 'rejection_projection'), true)
})
