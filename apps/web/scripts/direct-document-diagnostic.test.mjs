import assert from 'node:assert/strict'
import { test } from 'node:test'
import { EventEmitter } from 'node:events'
import { JSDOM } from 'jsdom'
import * as diagnostic from '../tests/helpers/directDocumentDiagnostic.mjs'
import { validateReadyPhase } from '../tests/helpers/readyChainDiagnostic.mjs'
const candidate = 'b4b3eb258e8a99033dfc668cd4c1313d3a5e4030'
const baseline = '95da4227ae831eeb4edfd75d3fc31bcdaa8882ae'
const resource = (category, target = 'none') => ({ category, target, encodedBytes: 10, transferBytes: 20,
  startMs: 1, requestStartMs: 2, responseStartMs: 3, responseEndMs: 4, cacheHit: false, status: 200 })

test('fixed plan retains eleven contexts and all thirty-three ordered phases without a retry input', () => {
  assert.equal(typeof diagnostic.directPlan, 'function')
  const plan = diagnostic.directPlan()
  assert.equal(plan.length, 33)
  assert.deepEqual(plan.slice(0, 3), ['cold', 'prime', 'warm'].map(phase => ({ scenario: 'housing', viewport: 'desktop', profile: 'throttled', cycle: 0, phase })))
  assert.equal(plan.filter(p => p.scenario === 'personnel' && p.profile === 'unthrottled').length, 21)
  assert.equal(plan.at(-1).cycle, 6)
})

const start = (cdp, id, path, time, type = 'Fetch') => cdp.emit('Network.requestWillBeSent', {
  requestId: id, frameId: 'private-frame', type, timestamp: time, wallTime: time + 1000,
  request: { url: `http://local${path}?secret=PRIVATE`, headers: { cookie: 'PRIVATE' } },
})
const finish = (cdp, id, time, status = 200) => {
  cdp.emit('Network.responseReceived', { requestId: id, timestamp: time, response: { status, headers: { 'Cache-Control': 'no-cache', secret: 'PRIVATE' } } })
  cdp.emit('Network.responseReceivedExtraInfo', { requestId: id, statusCode: status === 200 ? 304 : status, headers: { secret: 'PRIVATE' } })
  cdp.emit('Network.loadingFinished', { requestId: id, timestamp: time + 0.001 })
}
test('actual CDP listeners retain unfinished requests, late status and category counts without identities', () => {
  assert.equal(typeof diagnostic.observeDirectNetwork, 'function')
  const cdp = new EventEmitter()
  const probe = diagnostic.observeDirectNetwork(cdp, 'http://local', 'private-frame')
  probe.begin()
  start(cdp, 'private-document', '/people', 10, 'Document')
  start(cdp, 'private-list', '/api/v1/people', 10.01)
  start(cdp, 'private-other', '/api/v1/private-name', 10.02)
  finish(cdp, 'private-document', 10.03)
  finish(cdp, 'private-other', 10.04)
  const result = probe.snapshot(1010000)
  assert.equal(result.requests.length, 3)
  assert.equal(result.requests[1].category, 'people_list')
  assert.equal(result.requests[1].terminalEventMs, null)
  assert.equal(result.requests[1].status, null)
  assert.equal(result.requests[2].category, 'other_api')
  assert.equal(result.document.extraInfoStatus, 304)
  assert.equal(result.counts.people_list.started, 1)
  assert.equal(result.counts.people_list.completed, 0)
  assert.equal(result.counts.auth_me.started, 0)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|private-|http:|secret|"headers":|requestId/)
  probe.dispose()
  assert.equal(cdp.eventNames().length, 0)
})

test('network missing origin, failed requests, category overflow and malformed times never invent completion', () => {
  assert.equal(typeof diagnostic.observeDirectNetwork, 'function')
  const cdp = new EventEmitter(), probe = diagnostic.observeDirectNetwork(cdp, 'http://local', 'private-frame')
  probe.begin()
  assert.equal(probe.snapshot(null).unavailable, true)
  start(cdp, 'doc', '/housing', 10, 'Document')
  start(cdp, 'bad', '/api/v1/people', 9)
  cdp.emit('Network.loadingFailed', { requestId: 'bad', timestamp: 10.04, errorText: 'PRIVATE' })
  let result = probe.snapshot(1010000)
  assert.equal(result.requests[1].startMs, null)
  assert.equal(result.requests[1].failed, true)
  assert.equal(result.unavailable, true)
  for (let index = 0; index < 17; index++) start(cdp, `other-${index}`, '/api/v1/people', 10.1)
  result = probe.snapshot(1010000)
  assert.equal(result.overflow, true)
  assert.equal(result.requests.filter(r => r.category === 'people_list').length, 16)
  probe.dispose()
})

test('real serialized DOM observer witnesses visible card/table and updating transitions, not installation success', async () => {
  assert.equal(typeof diagnostic.installDirectWitnesses, 'function')
  const dom = new JSDOM('<body><p>正在更新…</p><div>PRIVATE</div></body>', { url: 'http://local', runScripts: 'outside-only' })
  const w = dom.window
  w.HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}] }
  w.eval(`(${diagnostic.installDirectWitnesses.toString()})()`)
  w.document.body.insertAdjacentHTML('beforeend', '<article class="housingRoomCard"></article><div class="peopleTable" role="table" aria-label="人员名单"></div>')
  w.document.querySelector('p').remove()
  await Promise.resolve()
  const result = w.__directWitnesses.snapshot()
  assert.equal(result.dom.some(d => d.kind === 'room_card' && d.visible), true)
  assert.equal(result.dom.some(d => d.kind === 'table' && d.visible), true)
  assert.deepEqual(Array.from(result.dom.filter(d => d.kind === 'updating'), d => d.visible), [true, false])
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|人员|正在更新/)
  w.__directWitnesses.dispose(); dom.window.close()
})

function phase() {
  const request = category => ({ category, startMs: 1, responseEventMs: 2, terminalEventMs: 3,
    terminalSource: 'loading_finished', terminalResponseRelation: 'after', headersEndMs: 2, headersTimingState: 'available',
    headersStartRelation: 'after', terminalHeadersRelation: 'after', status: 200, extraInfoStatus: 200, failed: false })
  const requests = ['document', 'auth_me', 'portal', 'people_list', 'people_count'].map(request)
  const counts = Object.fromEntries(['document', 'auth_me', 'auth_csrf', 'portal', 'catalog', 'overview', 'people_list', 'people_count', 'other_api'].map(k => [k, { started: requests.filter(r => r.category === k).length, completed: requests.filter(r => r.category === k).length, failed: 0 }]))
  const cost = { requests: 5, encodedBytes: 10, transferBytes: 20, scriptDurationMs: 1 }
  return { scenario: 'personnel', viewport: 'mobile', profile: 'unthrottled', cycle: 0, phase: 'warm',
    overflow: false, unavailable: false, resources: [resource('document'), resource('other_script'), resource('other_css'), resource('shell_js'),
      resource('leaf_js', 'housing'), resource('shared_css', 'housing'), resource('leaf_js', 'personnel'), resource('shared_css', 'personnel'), resource('css_owner_js', 'personnel')],
    stages: ['goto_done', 'heading', 'table', 'person_search', 'person_query', 'updating_absent', 'ready', 'settled'].map(name => ({ name, ms: 10 })),
    dom: [{ kind: 'heading', visible: true, ms: 5 }], fcpMs: 2, document: { status: 200, extraInfoStatus: 304, noCache: false }, cost, journeyCost: cost,
    network: { requests, counts, overflow: false, unavailable: false, document: { status: 200, extraInfoStatus: 304, noCache: false } },
    witnesses: { overflow: false, dom: [{ kind: 'table', visible: true, ms: 5 }, { kind: 'updating', visible: false, ms: 6 }] } }
}

function observedPhase({ responseTime = 10.25, terminalTime = 10.125, terminal = 'Network.loadingFinished',
  terminalFirst = false, timing, timeOrigin = 1010000 } = {}) {
  const cdp = new EventEmitter(), probe = diagnostic.observeDirectNetwork(cdp, 'http://local', 'private-frame')
  probe.begin()
  try {
    for (const [index, path] of ['/people', '/api/v1/auth/me', '/api/v1/portal/context', '/api/v1/people', '/api/v1/people/counts'].entries()) {
      const id = `PRIVATE-${index}`
      start(cdp, id, path, 10, index ? 'Fetch' : 'Document')
      const response = () => cdp.emit('Network.responseReceived', { requestId: id, timestamp: responseTime,
        response: { status: 200, headers: { 'Cache-Control': 'no-cache', secret: 'PRIVATE' }, timing } })
      const end = () => { if (terminal) cdp.emit(terminal, { requestId: id, timestamp: terminalTime, errorText: 'PRIVATE' }) }
      if (terminalFirst) { end(); response() } else { response(); end() }
    }
    const p = phase(), cell = { scenario: p.scenario, viewport: p.viewport, profile: p.profile, cycle: p.cycle }
    const snapshot = { resources: p.resources, stages: p.stages, dom: p.dom, fcpMs: p.fcpMs, overflow: p.overflow,
      cost: p.cost, journeyCost: p.journeyCost }
    return diagnostic.assembleDirectPhase(cell, p.phase, { snapshot, witnesses: p.witnesses }, 0.001, probe.snapshot(timeOrigin), false)
  } finally { probe.dispose() }
}

test('actual observer and assembler retain a terminal event before the response event without inventing a negative download interval', () => {
  const p = observedPhase({ timing: { requestTime: 10.03125, receiveHeadersEnd: 31.25 } }), rejected = []
  assert.ok(diagnostic.validateDirectPhase(p, reason => rejected.push(reason)), JSON.stringify(rejected))
  assert.deepEqual(rejected, [])
  assert.deepEqual(p.network.requests[0], { category: 'document', startMs: 0, responseEventMs: 250, terminalEventMs: 125,
    terminalSource: 'loading_finished', terminalResponseRelation: 'before', headersEndMs: 62.5, headersTimingState: 'available',
    headersStartRelation: 'after', terminalHeadersRelation: 'after', status: 200, extraInfoStatus: null, failed: false })
  assert.equal(diagnostic.directPhaseComplete(p, candidate), true)
})

for (const [terminalTime, terminalMs, relation] of [[10.125, 125, 'before'], [10.25, 250, 'equal'], [10.375, 375, 'after']]) {
  test(`source event relation ${relation} and exact values do not depend on callback delivery order`, () => {
    const delivered = observedPhase({ terminalTime }), permuted = observedPhase({ terminalTime, terminalFirst: true })
    assert.deepEqual(permuted, delivered)
    assert.equal(diagnostic.validateDirectPhase(delivered), delivered)
    assert.equal(delivered.network.requests[0].responseEventMs, 250)
    assert.equal(delivered.network.requests[0].terminalEventMs, terminalMs)
    assert.equal(delivered.network.requests[0].terminalResponseRelation, relation)
    assert.equal(delivered.network.requests[0].headersEndMs, null, 'no response-event or ResourceTiming fallback')
    assert.equal(delivered.network.requests[0].headersTimingState, 'unavailable')
    assert.equal(diagnostic.directPhaseComplete(delivered, candidate), true)
  })
}

test('headers versus raw event order stays visible without clamping or a new cross-source ordering assumption', () => {
  const beforeStart = observedPhase({ timeOrigin: 1009000, timing: { requestTime: 9.875, receiveHeadersEnd: 0 } })
  const afterTerminal = observedPhase({ timing: { requestTime: 10.375, receiveHeadersEnd: 0 } })
  assert.deepEqual([beforeStart.network.requests[0].startMs, beforeStart.network.requests[0].headersEndMs,
    beforeStart.network.requests[0].headersStartRelation], [1000, 875, 'before'])
  assert.deepEqual([afterTerminal.network.requests[0].terminalEventMs, afterTerminal.network.requests[0].headersEndMs,
    afterTerminal.network.requests[0].terminalHeadersRelation], [125, 375, 'before'])
  for (const p of [beforeStart, afterTerminal]) {
    assert.equal(diagnostic.validateDirectPhase(p), p)
    assert.equal(diagnostic.directPhaseComplete(p, candidate), true, 'completion is evidence presence, not a physical interval claim')
  }
})

test('observed failure and missing terminal remain explicit, counted and incomplete', () => {
  for (const [terminal, source, end, relation, failed] of [
    ['Network.loadingFailed', 'loading_failed', 125, 'before', true], [null, 'unavailable', null, 'unavailable', false],
  ]) {
    const p = observedPhase({ terminal }), row = p.network.requests[0]
    assert.equal(diagnostic.validateDirectPhase(p), p)
    assert.deepEqual([row.terminalSource, row.terminalEventMs, row.terminalResponseRelation, row.failed], [source, end, relation, failed])
    assert.deepEqual(p.network.counts.document, { started: 1, completed: 0, failed: failed ? 1 : 0 })
    assert.equal(diagnostic.directPhaseComplete(p, candidate), false)
  }
})

for (const [name, timing, state, expected] of [
  ['missing', undefined, 'unavailable', null], ['null', null, 'unavailable', null],
  ['sentinel', { requestTime: 10, receiveHeadersEnd: -1 }, 'unavailable', null],
  ['fractional milliseconds', { requestTime: 10.03125, receiveHeadersEnd: 31.25 }, 'available', 62.5],
  ['genuine zero', { requestTime: 10, receiveHeadersEnd: 0 }, 'available', 0],
  ['object without fields', {}, 'invalid', null], ['array', [], 'invalid', null], ['string', 'PRIVATE', 'invalid', null],
  ['null requestTime', { requestTime: null, receiveHeadersEnd: 0 }, 'invalid', null],
  ['null offset', { requestTime: 10, receiveHeadersEnd: null }, 'invalid', null],
  ['negative requestTime', { requestTime: -1, receiveHeadersEnd: 0 }, 'invalid', null],
  ['negative non-sentinel', { requestTime: 10, receiveHeadersEnd: -2 }, 'invalid', null],
  ['NaN requestTime', { requestTime: NaN, receiveHeadersEnd: 0 }, 'invalid', null],
  ['infinite offset', { requestTime: 10, receiveHeadersEnd: Infinity }, 'invalid', null],
  ['string requestTime', { requestTime: '10', receiveHeadersEnd: 0 }, 'invalid', null],
  ['string offset', { requestTime: 10, receiveHeadersEnd: '0' }, 'invalid', null],
  ['outside retained bounds', { requestTime: 101, receiveHeadersEnd: 0 }, 'invalid', null],
]) test(`detailed headers timing ${name} retains its availability without inventing a boundary`, () => {
  const p = observedPhase({ timing }), row = p.network.requests[0]
  assert.equal(diagnostic.validateDirectPhase(p), p)
  assert.deepEqual([row.headersTimingState, row.headersEndMs], [state, expected])
  if (expected === null) assert.deepEqual([row.headersStartRelation, row.terminalHeadersRelation], ['unavailable', 'unavailable'])
  assert.equal(diagnostic.directPhaseComplete(p, candidate), state !== 'invalid')
  assert.doesNotMatch(JSON.stringify(p), /PRIVATE|requestTime|receiveHeadersEnd|requestId|"headers":/)
})

test('detailed headers timing never coerces malformed numeric objects', () => {
  for (const field of ['requestTime', 'receiveHeadersEnd']) {
    let coerced = false
    const p = observedPhase({ timing: { requestTime: 10, receiveHeadersEnd: 0,
      [field]: { [Symbol.toPrimitive]() { coerced = true; return field === 'requestTime' ? 10 : 0 } } } })
    assert.equal(p.network.requests[0].headersTimingState, 'invalid')
    assert.equal(p.network.requests[0].headersEndMs, null)
    assert.equal(coerced, false)
  }
})

test('a later response without timing cannot reuse another response headers boundary', () => {
  const cdp = new EventEmitter(), probe = diagnostic.observeDirectNetwork(cdp, 'http://local', 'private-frame')
  probe.begin()
  try {
    start(cdp, 'doc', '/people', 10, 'Document')
    cdp.emit('Network.responseReceived', { requestId: 'doc', timestamp: 10.125,
      response: { status: 200, timing: { requestTime: 10, receiveHeadersEnd: 62.5 } } })
    assert.equal(probe.snapshot(1010000).requests[0].headersEndMs, 62.5)
    cdp.emit('Network.responseReceived', { requestId: 'doc', timestamp: 10.25, response: { status: 200 } })
    const row = probe.snapshot(1010000).requests[0]
    assert.equal(row.responseEventMs, 250)
    assert.equal(row.headersEndMs, null)
    assert.equal(row.headersTimingState, 'unavailable')
  } finally { probe.dispose() }
})

test('malformed event times keep actual terminal source but cannot count as completion', () => {
  for (const terminalTime of [null, NaN, Infinity, '10.125', 9, 101]) {
    const p = observedPhase({ terminalTime }), row = p.network.requests[0]
    assert.equal(diagnostic.validateDirectPhase(p), p)
    assert.deepEqual([row.terminalEventMs, row.terminalSource, row.terminalResponseRelation], [null, 'loading_finished', 'unavailable'])
    assert.equal(p.network.counts.document.completed, 0)
    assert.equal(diagnostic.directPhaseComplete(p, candidate), false)
  }
})

test('missing document clock leaves detailed timing unavailable instead of projecting zero', () => {
  const p = observedPhase({ timeOrigin: null, timing: { requestTime: 10, receiveHeadersEnd: 62.5 } }), row = p.network.requests[0]
  assert.equal(diagnostic.validateDirectPhase(p), p)
  assert.equal(p.network.unavailable, true)
  assert.deepEqual([row.startMs, row.headersEndMs, row.headersTimingState], [null, null, 'unavailable'])
  assert.equal(diagnostic.directPhaseComplete(p, candidate), false)
})

test('finite observed response-before-start remains a concrete semantic rejection', () => {
  const p = observedPhase({ timeOrigin: 1009000, responseTime: 9.875 }), rejected = []
  assert.deepEqual([p.network.requests[0].startMs, p.network.requests[0].responseEventMs], [1000, 875])
  assert.equal(diagnostic.validateDirectPhase(p, reason => rejected.push(reason)), null)
  assert.deepEqual(rejected, [{ validator: 'direct', field: 'network.request.response_event', rule: 'response_before_start', itemIndex: 0 }])
})

test('event source, timing state and relation enums never coerce array or object tokens', () => {
  for (const [key, token] of [['terminalSource', 'loading_finished'], ['headersTimingState', 'available'],
    ['terminalResponseRelation', 'after'], ['headersStartRelation', 'after'], ['terminalHeadersRelation', 'after']]) {
    for (const value of [[token], {}]) {
      let coerced = false
      value[Symbol.toPrimitive] = () => { coerced = true; return token }
      const p = phase(); p.network.requests[0][key] = value
      assert.equal(diagnostic.validateDirectPhase(p), null)
      assert.equal(coerced, false)
    }
  }
})

test('closed direct schema permits genuine baseline policy but rejects missing/incomplete/invalid evidence and raw private fields', () => {
  assert.equal(typeof diagnostic.directPhaseComplete, 'function')
  assert.equal(diagnostic.directPhaseComplete(phase(), candidate), true)
  for (const change of [p => { delete p.network }, p => { p.network.requests.pop() },
    p => { p.network.requests[3].terminalEventMs = null }, p => { p.network.requests[3].status = null },
    p => { p.network.requests[0].startMs = -1 }, p => { p.network.requests[0].terminalEventMs = Infinity },
    p => { p.network.requests[0].raw = 'PRIVATE' }, p => { p.witnesses.dom[0].ms = -1 },
    p => { p.witnesses.dom = [] }, p => { p.witnesses.dom[1].ms = null }, p => { p.unavailable = true }]) {
    const p = phase(); change(p); assert.equal(diagnostic.directPhaseComplete(p, candidate), false)
  }
})

test('candidate cannot complete when a required static boundary is omitted, even with all API and DOM evidence', () => {
  const p = phase()
  for (const removed of p.resources) {
    const missing = { ...p, resources: p.resources.filter(r => r !== removed) }
    assert.notEqual(diagnostic.validateDirectPhase(missing), null, 'retain bounded incomplete evidence')
    assert.equal(diagnostic.directPhaseComplete(missing, candidate), false, `missing ${removed.category}/${removed.target}`)
  }
  assert.equal(diagnostic.directPhaseComplete({ ...p, resources: [] }, candidate), false)
})

test('baseline requires its actual monolithic JS/CSS without pretending it had candidate leaf or shared chunks', () => {
  const p = { ...phase(), resources: ['document', 'other_script', 'other_css'].map(c => resource(c)) }
  assert.equal(diagnostic.directPhaseComplete(p, baseline), true)
  assert.equal(diagnostic.directPhaseComplete(p, candidate), false, 'never infer baseline contract from missing split resources')
  assert.equal(diagnostic.directPhaseComplete(p, undefined), false, 'source identity is required')
  for (const removed of p.resources) assert.equal(diagnostic.directPhaseComplete({ ...p, resources: p.resources.filter(r => r !== removed) }, baseline), false)
})

test('null static start/request/response/end boundaries cannot pass either fixed source contract', () => {
  for (const source of [baseline, candidate]) {
    const p = phase()
    if (source === baseline) p.resources = ['document', 'other_script', 'other_css'].map(c => resource(c))
    for (let index = 0; index < p.resources.length; index++) for (const field of ['startMs', 'requestStartMs', 'responseStartMs', 'responseEndMs']) {
      const missing = globalThis.structuredClone(p); missing.resources[index][field] = null
      assert.notEqual(diagnostic.validateDirectPhase(missing), null)
      assert.equal(diagnostic.directPhaseComplete(missing, source), false, `${source}/${index}/${field}`)
    }
  }
})

test('capture preserves original action failure despite observation and sink failures', async () => {
  assert.equal(typeof diagnostic.captureDirect, 'function')
  const original = new Error('PRIVATE_ACTION')
  let saved
  await assert.rejects(diagnostic.captureDirect(async () => { throw original }, async () => { throw new Error('PRIVATE_OBSERVER') }, value => { saved = value; throw new Error('PRIVATE_SINK') }), error => error === original)
  assert.equal(saved, null)
  await assert.rejects(diagnostic.captureDirect(async () => {}, async () => null, () => {}), /direct document evidence incomplete/)
})

for (const [observe, expected] of [[async () => { throw new TypeError('PRIVATE') }, 'observer_exception'],
  [async () => ({ raw: 'PRIVATE' }), 'validation_rejected']]) {
  test(`capture independently records ${expected} even when phase is null`, async () => {
    const failures = []; let saved
    await assert.rejects(diagnostic.captureDirect(async () => {}, observe, p => { saved = p }, candidate,
      (kind, error) => failures.push({ kind, name: error?.name })))
    assert.equal(saved, null)
    assert.equal(failures[0]?.kind, expected)
    assert.equal(failures[0]?.name, expected === 'observer_exception' ? 'TypeError' : undefined)
  })
}

test('action and observer failures both survive independently without replacing original action error', async () => {
  const original = new RangeError('PRIVATE_ACTION'), observer = new TypeError('PRIVATE_OBSERVER'), failures = []
  await assert.rejects(diagnostic.captureDirect(async () => { throw original }, async () => { throw observer }, () => {}, candidate,
    (kind, error) => failures.push({ kind, error })), error => error === original)
  assert.deepEqual(failures, [{ kind: 'action_exception', error: original }, { kind: 'observer_exception', error: observer }])
  await assert.rejects(diagnostic.captureDirect(async () => { throw original }, async () => null, () => {}, candidate,
    () => { throw new Error('PRIVATE_SINK') }), error => error === original)
})

test('failure projection retains only fixed error names and bounded known-file coordinates, never malicious data', () => {
  assert.equal(typeof diagnostic.directFailure, 'function')
  const error = new TypeError('PRIVATE http://secret cookie=SECRET')
  error.stack = 'PRIVATE\n    at action (/PRIVATE/apps/web/tests/helpers/directDocumentJourney.ts:63:26)\n    at x (http://PRIVATE/evil.js:2:3)\n    at x (/PRIVATE/run-direct-document.mjs:99999999:3)'
  const result = diagnostic.directFailure('action_exception', 'navigate', 0, error)
  assert.deepEqual(result, { kind: 'action_exception', stage: 'navigate', phaseIndex: 0, errorName: 'TypeError',
    frames: [{ file: 'directDocumentJourney.ts', line: 63, column: 26 }] })
  error.name = 'actionError PRIVATE'
  assert.equal(diagnostic.directFailure('action_exception', 'navigate', 0, error).errorName, 'unknown')
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|SECRET|http:|cookie|stack|message/)
  const hostile = new Proxy({}, { get() { throw new Error('PRIVATE') } })
  assert.equal(diagnostic.directFailure('observer_exception', 'snapshot', 0, hostile).errorName, 'unknown')
})

test('failure sanitizer samples an untrusted name once and rejects unbounded failure annotations', () => {
  let reads = 0
  const error = { get name() { return reads++ === 0 ? 'TypeError' : 'PRIVATE_CHANGED_NAME' } }
  const result = diagnostic.directFailure('observer_exception', 'snapshot', 0, error)
  assert.equal(result.errorName, 'TypeError')
  assert.equal(reads, 1)
  assert.deepEqual(diagnostic.validateDirectFailures([result]), [result])
  for (const changed of [{ ...result, message: 'PRIVATE' }, { ...result, stage: 'PRIVATE' }, { ...result, errorName: 'actionError' },
    { ...result, phaseIndex: 33 }, { ...result, frames: [{ file: 'PRIVATE', line: 1, column: 1 }] },
    { ...result, frames: [{ file: 'directDocumentJourney.ts', line: 1, column: 10001 }] }]) {
    assert.equal(diagnostic.validateDirectFailures([changed]), null)
  }
  assert.equal(diagnostic.validateDirectFailures(Array(9).fill(result)), null)
})

const rejectionCases = [
  ['root', p => { p.PRIVATE = 'http://PRIVATE' }, 'direct', 'root', 'exact_keys', null],
  ['identity', p => { p.cycle = 99 }, 'direct', 'identity', 'fixed_plan_identity', null],
  ['cost keys', p => { p.cost.PRIVATE = 'PRIVATE' }, 'ready_projection', 'cost', 'exact_keys', null],
  ['cost requests', p => { p.cost.requests = 0.5 }, 'ready_projection', 'cost.requests', 'integer_bounds', null],
  ['cost bytes', p => { p.cost.encodedBytes = -1 }, 'ready_projection', 'cost.encoded_bytes', 'integer_bounds', null],
  ['cost transfer', p => { p.cost.transferBytes = Infinity }, 'ready_projection', 'cost.transfer_bytes', 'integer_bounds', null],
  ['cost script', p => { p.cost.scriptDurationMs = -1 }, 'ready_projection', 'cost.script_duration', 'finite_time_or_null', null],
  ['cost null', p => { p.cost.scriptDurationMs = null }, 'ready_projection', 'cost.script_duration', 'null_requires_unavailable', null],
  ['journey cost', p => { p.journeyCost.requests = 513 }, 'ready_projection', 'journey_cost.requests', 'integer_bounds', null],
  ['document keys', p => { p.document.PRIVATE = 'PRIVATE' }, 'ready_projection', 'document', 'exact_keys', null],
  ['document status', p => { p.document.status = 99 }, 'ready_projection', 'document.status', 'status_or_null', null],
  ['document cache', p => { p.document.noCache = 'PRIVATE' }, 'ready_projection', 'document.no_cache', 'boolean_or_null', null],
  ['resource target', p => { p.resources[1].target = 'PRIVATE' }, 'ready_projection', 'resource.target', 'enum', 1],
  ['resource bytes', p => { p.resources[1].encodedBytes = -1 }, 'ready_projection', 'resource.encoded_bytes', 'integer_bounds', 1],
  ['resource time', p => { p.resources[1].responseEndMs = Infinity }, 'ready_projection', 'resource.response_end', 'finite_time_or_null', 1],
  ['DOM', p => { p.dom[0].visible = 'PRIVATE' }, 'ready_projection', 'dom.visible', 'boolean', 0],
  ['stage', p => { p.stages[0].ms = -1 }, 'ready_projection', 'stage.time', 'finite_time_or_null', 0],
  ['duplicate stage', p => { p.stages.push(p.stages[0]) }, 'ready_projection', 'stage.names', 'duplicate_stage', null],
  ['network keys', p => { p.network.PRIVATE = 'PRIVATE' }, 'direct', 'network.root', 'exact_keys', null],
  ['network document', p => { p.network.document.status = 201 }, 'direct', 'network.document', 'document_mismatch', null],
  ['network response order', p => { p.network.requests[0].responseEventMs = 0 }, 'direct', 'network.request.response_event', 'response_before_start', 0],
  ['network negative terminal time', p => { p.network.requests[0].terminalEventMs = -1 }, 'direct', 'network.request.terminal_event', 'finite_time_or_null', 0],
  ['network negative headers time', p => { p.network.requests[0].headersEndMs = -1 }, 'direct', 'network.request.headers_end', 'finite_time_or_null', 0],
  ['network terminal source', p => { p.network.requests[0].terminalSource = 'PRIVATE' }, 'direct', 'network.request.terminal_source', 'enum', 0],
  ['network headers availability', p => { p.network.requests[0].headersTimingState = 'PRIVATE' }, 'direct', 'network.request.headers_timing', 'enum', 0],
  ['network terminal source mismatch', p => { p.network.requests[0].terminalSource = 'loading_failed' }, 'direct', 'network.request.terminal_source', 'source_mismatch', 0],
  ['network unavailable terminal source', p => { p.network.requests[0].terminalSource = 'unavailable' }, 'direct', 'network.request.terminal_source', 'source_mismatch', 0],
  ['network failed flag mismatch', p => { p.network.requests[0].failed = true }, 'direct', 'network.request.terminal_source', 'source_mismatch', 0],
  ['network headers availability mismatch', p => { p.network.requests[0].headersTimingState = 'unavailable' }, 'direct', 'network.request.headers_timing', 'availability_mismatch', 0],
  ['network missing available headers', p => { p.network.requests[0].headersEndMs = null }, 'direct', 'network.request.headers_timing', 'availability_mismatch', 0],
  ...[['terminalResponseRelation', 'terminal_response_relation'], ['headersStartRelation', 'headers_start_relation'], ['terminalHeadersRelation', 'terminal_headers_relation']]
    .flatMap(([key, field]) => [
      [`network ${field} enum`, p => { p.network.requests[0][key] = 'PRIVATE' }, 'direct', `network.request.${field}`, 'enum', 0],
      [`network ${field} mismatch`, p => { p.network.requests[0][key] = 'before' }, 'direct', `network.request.${field}`, 'relation_mismatch', 0],
    ]),
  ['network count keys', p => { p.network.counts.document.PRIVATE = 'PRIVATE' }, 'direct', 'network.count.keys', 'exact_keys', 0],
  ['network started', p => { p.network.counts.document.started = 99 }, 'direct', 'network.count.started', 'count_mismatch', 0],
  ['network completed', p => { p.network.counts.document.completed = 99 }, 'direct', 'network.count.completed', 'count_mismatch', 0],
  ['network failed', p => { p.network.counts.document.failed = 99 }, 'direct', 'network.count.failed', 'count_mismatch', 0],
  ['witness', p => { p.witnesses.dom[0].visible = 'PRIVATE' }, 'direct', 'witness.dom.visible', 'boolean', 0],
]
for (const [name, mutate, validator, field, rule, itemIndex] of rejectionCases) test(`first rejection identifies ${name} without values`, () => {
  const p = phase(); p.cost = { ...p.cost }; p.journeyCost = { ...p.journeyCost }; mutate(p)
  const rejected = []
  assert.equal(diagnostic.validateDirectPhase(p, reason => rejected.push(reason)), null)
  assert.deepEqual(rejected, [{ validator, field, rule, itemIndex }])
  assert.notEqual(diagnostic.validateDirectRejections([{ schemaVersion: 1, phaseIndex: 0, ...rejected[0] }]), null)
  assert.doesNotMatch(JSON.stringify(rejected), /PRIVATE|http|message|value/)
})

test('rejection schema preserves valid fixed string enums', () => {
  for (const validator of ['direct', 'ready_projection']) {
    const value = [{ schemaVersion: 1, phaseIndex: 0, validator, field: 'root', rule: 'exact_keys', itemIndex: null }]
    assert.equal(diagnostic.validateDirectRejections(value), value)
  }
})

test('rejection schema rejects array validator and rule tokens', () => {
  const value = [{ schemaVersion: 1, phaseIndex: 0, validator: ['direct'], field: 'root', rule: ['exact_keys'], itemIndex: null }]
  assert.equal(diagnostic.validateDirectRejections(value), null)
})

for (const [field, token] of [['validator', 'direct'], ['rule', 'exact_keys']]) for (const kind of ['array', 'object']) {
  test(`rejection schema rejects ${kind} ${field} before key coercion`, () => {
    let coerced = false
    const malformed = kind === 'array' ? [token] : {}
    malformed[Symbol.toPrimitive] = () => { coerced = true; return token }
    const value = [{ schemaVersion: 1, phaseIndex: 0, validator: 'direct', field: 'root', rule: 'exact_keys', itemIndex: null,
      [field]: malformed }]
    assert.equal(diagnostic.validateDirectRejections(value), null)
    assert.equal(coerced, false)
  })
}

test('optional rejection callbacks preserve positive/nullable contracts and cannot mask original failures', async () => {
  const p = phase(); p.unavailable = true; p.cost.scriptDurationMs = null; p.resources[0].responseEndMs = null
  p.network.requests[0].startMs = null; p.network.requests[0].headersStartRelation = 'unavailable'; p.network.unavailable = true
  let called = false
  assert.equal(diagnostic.validateDirectPhase(p, () => { called = true }), p)
  assert.equal(called, false)
  assert.equal(diagnostic.validateDirectPhase({ PRIVATE: 'PRIVATE' }, () => { throw new Error('PRIVATE') }), null)
  const projected = Object.fromEntries(Object.entries(phase()).filter(([key]) => !['viewport', 'cycle', 'network', 'witnesses'].includes(key)))
  projected.phase = 'first'; projected.cost = { ...projected.cost, requests: -1 }
  const rejected = []
  assert.equal(validateReadyPhase(projected, reason => rejected.push(reason)), null)
  assert.deepEqual(rejected, [{ validator: 'ready_projection', field: 'cost.requests', rule: 'integer_bounds', itemIndex: null }])
  assert.equal(validateReadyPhase(projected, () => { throw new Error('PRIVATE_CALLBACK') }), null)
  const original = new Error('PRIVATE_ACTION')
  await assert.rejects(diagnostic.captureDirect(async () => { throw original }, async () => ({ PRIVATE: 'PRIVATE' }), () => {}, candidate,
    () => {}, () => { throw new Error('PRIVATE_CALLBACK') }), error => error === original)
  await assert.rejects(diagnostic.captureDirect(async () => {}, async () => ({ PRIVATE: 'PRIVATE' }), () => {}, candidate,
    () => {}, () => { throw new Error('PRIVATE_CALLBACK') }), /direct document evidence incomplete/)
})

test('capture retains only the first rejection independently of null phase and callback exceptions', async () => {
  const rejected = [], p = phase(); p.cost.requests = -1; p.resources[0].target = 'PRIVATE'
  let saved
  await assert.rejects(diagnostic.captureDirect(async () => {}, async () => p, value => { saved = value }, candidate,
    () => {}, reason => rejected.push(reason)))
  assert.equal(saved, null)
  assert.deepEqual(rejected, [{ validator: 'ready_projection', field: 'cost.requests', rule: 'integer_bounds', itemIndex: null }])
})

test('shared journey assembler preserves the exact existing probe projection and nullable metric semantics', () => {
  assert.equal(typeof diagnostic.assembleDirectPhase, 'function')
  const p = phase(), cell = { scenario: p.scenario, viewport: p.viewport, profile: p.profile, cycle: p.cycle }
  const snapshot = { resources: p.resources, stages: p.stages, dom: p.dom, fcpMs: p.fcpMs, overflow: p.overflow,
    earlier: [{ PRIVATE: 'PRIVATE' }], cost: { requests: 5, encodedBytes: 10, transferBytes: 20 }, journeyCost: { requests: 5, encodedBytes: 10, transferBytes: 20 } }
  const observed = { snapshot, witnesses: p.witnesses }
  assert.deepEqual(diagnostic.assembleDirectPhase(cell, 'warm', observed, 0.001, p.network, false), p)
  const missingMetric = diagnostic.assembleDirectPhase(cell, 'warm', observed, undefined, p.network, false)
  assert.equal(missingMetric.unavailable, true)
  assert.equal(missingMetric.cost.scriptDurationMs, null)
  assert.equal(diagnostic.validateDirectPhase(missingMetric), missingMetric)
})
