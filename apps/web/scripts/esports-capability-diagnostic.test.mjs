import test from 'node:test'
import assert from 'node:assert/strict'
import { EventEmitter } from 'node:events'
import { setImmediate } from 'node:timers'

const diagnostic = await import('../tests/helpers/esportsCapabilityDiagnostic.mjs').catch(() => ({}))
test('fixed discriminator accepts only original desktop pair, never arbitrary entry or audio', () => {
  assert.equal(typeof diagnostic.fixedCapabilityDiagnostic, 'function')
  assert.equal(diagnostic.fixedCapabilityDiagnostic({ entry: 3, mode: 'capability-diagnostic' }), true)
  for (const entry of [-1, 0, 1, 2, 4, 5, '3']) assert.equal(diagnostic.fixedCapabilityDiagnostic({ entry, mode: 'capability-diagnostic' }), false)
  assert.equal(diagnostic.fixedCapabilityDiagnostic({ entry: 3, mode: 'audio' }), false)
})
test('response projection strips identifiers and maps unknown state to finite unavailable values', () => {
  assert.equal(typeof diagnostic.projectCapabilityResponse, 'function')
  assert.deepEqual(diagnostic.projectCapabilityResponse('context', { active_run: { id: 'SECRET', mode: 'practice', suite_id: 'pc-fps', test_id: 'precision' }, account: 'PRIVATE' }),
    { active: true, mode: 'practice', suite: 'pc-fps', test: 'precision', state: 'unknown', body_valid: true })
  const absent = diagnostic.projectCapabilityResponse('context', { active_run: null })
  assert.equal(absent.active, false)
  assert.equal(absent.body_valid, true)
  for (const raw of [null, {}, { active_run: { mode: 'SECRET', suite_id: 'PRIVATE', test_id: 'TOKEN' } }]) {
    const result = diagnostic.projectCapabilityResponse('context', raw)
    assert.equal(result.body_valid, false)
    assert.doesNotMatch(JSON.stringify(result), /SECRET|PRIVATE|TOKEN/)
  }
  assert.equal(diagnostic.projectCapabilityResponse('reconcile', { attempt: { state: 'running' } }).state, 'running')
  assert.equal(diagnostic.projectCapabilityResponse('interrupt', { state: 'interrupted' }).state, 'interrupted')
})
test('real client v1 paths are accepted and the unversioned prefix is ignored', () => {
  const page = new EventEmitter(), record = diagnostic.createCapabilityDiagnostic(() => 1)
  record.begin(page, 'desktop')
  for (const [method, path] of [
    ['GET', '/api/v1/esports-assessments/context'],
    ['GET', '/api/v1/esports-assessments/practice-runs/00000000-0000-4000-8000-000000000001'],
    ['POST', '/api/v1/esports-assessments/practice-runs/00000000-0000-4000-8000-000000000001/interrupt'],
    ['GET', '/api/esports-assessments/context'],
    ['GET', '/api/esports-assessments/practice-runs/00000000-0000-4000-8000-000000000001'],
    ['POST', '/api/esports-assessments/practice-runs/00000000-0000-4000-8000-000000000001/interrupt'],
  ]) {
    const before = record.snapshot('desktop').events.length
    page.emit('request', { url: () => `http://127.0.0.1:1${path}`, method: () => method })
    assert.equal(record.snapshot('desktop').events.length - before, path.startsWith('/api/v1/') ? 1 : 0)
  }
  record.mark(page, 'teardown')
  const value = record.snapshot('desktop')
  assert.deepEqual(value.events.filter(event => event.kind === 'start').map(event => event.category), ['context', 'reconcile', 'interrupt'])
  assert.ok(diagnostic.validateCapabilityDiagnostic(value))
  assert.doesNotMatch(JSON.stringify(value), /http:|00000000|\/api/)
})
test('passive recorder retains cross-case close and late response without waiting or sending requests', async () => {
  assert.equal(typeof diagnostic.createCapabilityDiagnostic, 'function')
  let now = 10
  const record = diagnostic.createCapabilityDiagnostic(() => now++)
  const first = new EventEmitter(), second = new EventEmitter()
  record.begin(first, 'desktop')
  const request = { url: () => 'http://127.0.0.1:1/api/v1/esports-assessments/practice-runs/00000000-0000-4000-8000-000000000001', method: () => 'GET' }
  first.emit('request', request)
  record.mark(first, 'interrupted_ui')
  record.mark(first, 'teardown')
  first.emit('close')
  record.begin(second, 'nonphone')
  first.emit('response', { request: () => request, status: () => 200 })
  // The real observer reads only an already received response, never fetches.
  request.response = async () => ({ json: async () => ({ attempt: { state: 'running', id: 'SECRET' } }) })
  first.emit('requestfinished', request)
  const context = { url: () => 'http://127.0.0.1:1/api/v1/esports-assessments/context', method: () => 'GET', response: async () => ({ json: async () => ({ active_run: { id: 'SECRET', mode: 'practice', suite_id: 'pc-fps', test_id: 'precision' } }) }) }
  second.emit('request', context)
  second.emit('response', { request: () => context, status: () => 200 })
  second.emit('requestfinished', context)
  await new Promise(resolve => setImmediate(resolve))
  record.selection(second, 'suite', 'failed', { count: 1, visible: true, enabled: false, aria_disabled: null })
  record.mark(second, 'teardown')
  const value = record.snapshot('nonphone')
  assert.equal(diagnostic.validateCapabilityDiagnostic(value)?.case, 'nonphone')
  assert.equal(value.events.find(e => e.kind === 'selection').selection, 'suite')
  assert.equal(value.events.find(e => e.kind === 'selection').enabled, false)
  const close = value.events.findIndex(e => e.kind === 'closed')
  assert.ok(close > value.events.findIndex(e => e.kind === 'interrupted_ui'))
  assert.ok(close < value.events.findIndex(e => e.case === 'nonphone'))
  assert.equal(value.events.find(e => e.category === 'context' && e.kind === 'complete').active, true)
  assert.doesNotMatch(JSON.stringify(value), /SECRET|http:|00000000/)
})
test('closed evidence rejects missing, unknown fields, malformed enums, unbounded or absent lifecycle', () => {
  assert.equal(typeof diagnostic.createCapabilityDiagnostic, 'function')
  const record = diagnostic.createCapabilityDiagnostic(() => 1)
  const page = new EventEmitter()
  record.begin(page, 'desktop'); record.mark(page, 'teardown')
  const value = record.snapshot('desktop')
  assert.ok(diagnostic.validateCapabilityDiagnostic(value))
  assert.equal(diagnostic.validateCapabilityDiagnostic(null), null)
  for (const bad of [{ ...value, raw: 'SECRET' }, { ...value, events: [] },
    { ...value, events: [{ ...value.events[0], ms: Infinity }] },
    { ...value, events: [{ ...value.events[0], kind: 'SECRET' }] },
    { ...value, overflow: true }]) assert.equal(diagnostic.validateCapabilityDiagnostic(bad), null)
  for (let i = 0; i < 100; i++) record.mark(page, 'interrupted_ui')
  assert.equal(record.snapshot('desktop').events.length, 96)
  assert.equal(diagnostic.validateCapabilityDiagnostic(record.snapshot('desktop')), null)
  assert.equal(diagnostic.capabilityPairValid([]), false)
})

test('pair proof requires both real contexts, first teardown/close and second selection, not merely passing cases', async () => {
  const record = diagnostic.createCapabilityDiagnostic(() => 1)
  const makeContext = () => ({ url: () => 'http://127.0.0.1:1/api/v1/esports-assessments/context', method: () => 'GET',
    response: async () => ({ json: async () => ({ active_run: null }) }) })
  const pages = [new EventEmitter(), new EventEmitter()]
  const snapshots = []
  for (const [i, name] of ['desktop', 'nonphone'].entries()) {
    const page = pages[i], request = makeContext()
    record.begin(page, name)
    page.emit('request', request)
    page.emit('response', { request: () => request, status: () => 200 })
    page.emit('requestfinished', request)
    await new Promise(resolve => setImmediate(resolve))
    if (name === 'desktop') record.mark(page, 'interrupted_ui')
    else record.selection(page, 'project', 'failed', { count: 1, visible: true, enabled: false, aria_disabled: true })
    record.mark(page, 'teardown')
    snapshots.push({ capabilityDiagnostics: [record.snapshot(name)] })
    page.emit('close')
  }
  assert.equal(diagnostic.capabilityPairValid(snapshots), true)
  const unavailable = globalThis.structuredClone(snapshots)
  const failedSelection = unavailable[1].capabilityDiagnostics[0].events.find(event => event.kind === 'selection')
  failedSelection.count = null; failedSelection.visible = null; failedSelection.enabled = null; failedSelection.aria_disabled = null
  assert.equal(diagnostic.capabilityPairValid(unavailable), false)
  for (const kind of ['closed', 'interrupted_ui', 'selection']) {
    const bad = globalThis.structuredClone(snapshots)
    bad[1].capabilityDiagnostics[0].events = bad[1].capabilityDiagnostics[0].events.filter(event => event.kind !== kind)
    assert.equal(diagnostic.capabilityPairValid(bad), false)
  }
  for (const bad of [[snapshots[0]], [snapshots[1], snapshots[0]], [{ capabilityDiagnostics: [null] }, snapshots[1]]]) {
    assert.equal(diagnostic.capabilityPairValid(bad), false)
  }
  const passing = globalThis.structuredClone(snapshots)
  const selected = passing[1].capabilityDiagnostics[0].events.find(event => event.kind === 'selection')
  selected.outcome = 'passed'; selected.count = null; selected.visible = null; selected.enabled = null; selected.aria_disabled = null
  const position = passing[1].capabilityDiagnostics[0].events.indexOf(selected)
  passing[1].capabilityDiagnostics[0].events.splice(position, 0, { ...selected, selection: 'suite' })
  assert.equal(diagnostic.capabilityPairValid(passing), true)
})

test('failed requests retain finite status and no raw failure messages or unrelated traffic', () => {
  const page = new EventEmitter(), record = diagnostic.createCapabilityDiagnostic(() => 1)
  record.begin(page, 'desktop')
  const request = { url: () => 'http://127.0.0.1:1/api/v1/esports-assessments/practice-runs/00000000-0000-4000-8000-000000000001/interrupt', method: () => 'POST', failure: () => ({ errorText: 'SECRET' }) }
  page.emit('request', request)
  page.emit('response', { request: () => request, status: () => 409 })
  page.emit('requestfailed', request)
  page.emit('request', { url: () => 'http://127.0.0.1:1/api/people/SECRET', method: () => 'GET' })
  record.mark(page, 'teardown')
  const value = record.snapshot('desktop')
  assert.equal(value.events.length, 5)
  assert.equal(value.events[3].kind, 'failed')
  assert.equal(value.events[3].category, 'interrupt')
  assert.equal(value.events[3].status, 409)
  assert.doesNotMatch(JSON.stringify(value), /SECRET|http:|00000000/)
  assert.ok(diagnostic.validateCapabilityDiagnostic(value))
})
