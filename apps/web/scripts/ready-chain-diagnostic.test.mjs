import assert from 'node:assert/strict'
import { test } from 'node:test'
import { JSDOM } from 'jsdom'
import { classifyReadyResource, installReadyProbe, validateReadyPhase, readyPhaseComplete, readyJourneyComplete } from '../tests/helpers/readyChainDiagnostic.mjs'

const resource = category => ({ category, target: 'none', encodedBytes: 100, transferBytes: 200, startMs: 1, requestStartMs: 2, responseStartMs: 3, responseEndMs: 4, cacheHit: false, status: 200 })
const cost = () => ({ requests: 2, encodedBytes: 200, transferBytes: 400, scriptDurationMs: 2 })
const stage = name => ({ name, ms: 5 })
function phase() {
  return { scenario: 'login', phase: 'warm', profile: 'throttled', overflow: false,
    resources: [resource('document'), resource('auth_me')],
    stages: ['goto_done', 'heading', 'identifier', 'password', 'submit', 'ready', 'settled'].map(stage),
    dom: ['heading', 'shell', 'fallback', 'updating'].map(kind => ({ kind, visible: kind === 'heading', ms: 4 })), fcpMs: 3,
    document: { status: 200, extraInfoStatus: 304, noCache: true }, cost: cost(), journeyCost: cost(), unavailable: false }
}

test('fixed resource categories erase paths, queries, identities and unknown endpoint details', () => {
  assert.equal(classifyReadyResource('http://local/api/v1/people?name=PRIVATE', 'http://local'), 'people_list')
  assert.equal(classifyReadyResource('http://local/assets/HousingOverviewPage-abc.js', 'http://local'), 'leaf_js')
  assert.equal(classifyReadyResource('http://local/api/v1/people/PRIVATE', 'http://local'), 'other')
  assert.equal(classifyReadyResource('http://external/secret', 'http://local'), null)
})
test('only the people CSS owner naming gets a JS category, without inventing a housing owner or dependency map', () => {
  for (const [pathname, expected] of [
    ['/assets/people-aB_12-xY.js?private=PRIVATE', 'css_owner_js'],
    ['/assets/housing-aB_12-xY.js', 'other_script'],
    ['/assets/PeoplePage-aB_12-xY.js', 'leaf_js'],
    ['/assets/people-aB_12-xY.css', 'shared_css'],
    ['/assets/api-aB_12-xY.js', 'other_script'],
    ['/assets/jsx-runtime-aB_12-xY.js', 'other_script'],
    ['/assets/people/nested.js', 'other_script'],
  ]) assert.equal(classifyReadyResource(`http://local${pathname}`, 'http://local'), expected)
  assert.equal(classifyReadyResource('http://external/assets/people-aB_12-xY.js', 'http://local'), null)
  const p = phase(); p.resources.push(resource('css_owner_js'))
  assert.notEqual(validateReadyPhase(p), null)
  p.resources.at(-1).category = 'leaf_dependency_js'
  assert.equal(validateReadyPhase(p), null)
})
test('closed nested schema refuses raw identities, unknown stages and invalid finite types', () => {
  assert.deepEqual(validateReadyPhase(phase()), phase())
  for (const mutate of [p => { p.url = 'PRIVATE' }, p => { p.resources[0].requestId = 'PRIVATE' },
    p => { p.stages[0].name = 'PRIVATE' }, p => { p.stages[0].ms = Infinity },
    p => { p.dom[0].visible = 'true' }, p => { p.document.status = 999 },
    p => { p.resources[0].startMs = -1 }]) {
    const p = phase(); mutate(p); assert.equal(validateReadyPhase(p), null)
  }
})
test('preparation accounting is finite, closed and cannot omit earlier document costs', () => {
  assert.notEqual(validateReadyPhase(phase()), null)
  for (const mutate of [p => { delete p.cost }, p => { p.journeyCost.encodedBytes = Infinity },
    p => { p.cost.requests = 513 }, p => { p.cost.scriptDurationMs = -1 },
    p => { p.cost.rawUrl = 'PRIVATE' }, p => { p.resources[0].target = 'PRIVATE' },
    p => { p.resources[0].transferBytes = -1 }]) {
    const p = phase(); mutate(p); assert.equal(validateReadyPhase(p), null)
  }
})
test('missing script metrics are explicit only on unavailable evidence and can never pass', () => {
  const p = phase()
  p.cost.scriptDurationMs = null; p.journeyCost.scriptDurationMs = null
  assert.equal(validateReadyPhase(p), null)
  p.unavailable = true
  assert.notEqual(validateReadyPhase(p), null)
  assert.equal(readyPhaseComplete(p), false)
  p.cost.scriptDurationMs = Infinity
  assert.equal(validateReadyPhase(p), null)
})
test('missing necessary stages, explicit unavailable and overflow cannot pass diagnosis', () => {
  assert.equal(readyPhaseComplete(phase()), true)
  const p = phase(); p.stages = p.stages.filter(s => s.name !== 'password')
  assert.equal(readyPhaseComplete(p), false)
  for (const field of ['unavailable', 'overflow']) assert.equal(readyPhaseComplete({ ...phase(), [field]: true }), false)
  const absent = phase(); absent.resources = [resource('document')]
  assert.equal(readyPhaseComplete(absent), false)
  const explicitNull = phase(); explicitNull.stages[0].ms = null
  assert.notEqual(validateReadyPhase(explicitNull), null)
  assert.equal(readyPhaseComplete(explicitNull), false)
  assert.equal(readyPhaseComplete({ ...phase(), dom: [] }), false)
})

test('serialized passive probe caps real resource projection and exposes overflow without raw values', () => {
  const dom = new JSDOM('<body></body>', { url: 'http://local', runScripts: 'outside-only' })
  const scope = dom.window
  scope.performance.getEntriesByType = type => type === 'resource' ? Array.from({ length: 17 }, () => ({
    name: 'http://local/api/v1/people?name=PRIVATE', entryType: 'resource', startTime: 1,
    requestStart: 2, responseStart: 3, responseEnd: 4, transferSize: 100, encodedBodySize: 80, decodedBodySize: 100, responseStatus: 200,
  })) : []
  scope.performance.getEntriesByName = () => []
  scope.eval(`(${installReadyProbe.toString()})('personnel', ${classifyReadyResource.toString()})`)
  const result = scope.__readyChain.snapshot()
  assert.equal(result.overflow, true)
  assert.equal(result.resources.length, 16)
  assert.equal(result.resources[0].encodedBytes, 80)
  assert.equal(result.journeyCost.requests, 17)
  assert.equal(result.journeyCost.encodedBytes, 1360)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|http:|name=/)
  scope.__readyChain.dispose(); dom.window.close()
})
test('serialized probe backfills preparation crossing setup without moving its origin or duplicating bytes', () => {
  const dom = new JSDOM('<body></body>', { url: 'http://local', runScripts: 'outside-only' })
  const scope = dom.window
  let clock = 90
  const entry = (path, startTime, responseEnd, encodedBodySize = 80, transferSize = 100) => ({
    name: `http://local${path}`, entryType: 'resource', startTime, requestStart: startTime + 1,
    responseStart: responseEnd - 1, responseEnd, encodedBodySize, transferSize, decodedBodySize: 120, responseStatus: 200,
  })
  const entries = [entry('/assets/AppShell-abc.js', 1, 5, 40, 60),
    ...['HousingOverviewPage-abc.js', 'housing-abc.css', 'PeoplePage-abc.js', 'people-abc.js', 'people-abc.css']
      .map(path => entry(`/assets/${path}`, 20, 120)),
    entry('/api/v1/housing/catalog', 110, 130, 60, 90), entry('/api/v1/housing/overview', 112, 140, 60, 90)]
  const document = { ...entry('/', 0, 4, 50, 75), entryType: 'navigation' }
  scope.performance.now = () => clock
  scope.performance.getEntriesByType = type => type === 'navigation' ? [document]
    : type === 'resource' ? entries.filter(item => item.responseEnd <= clock) : []
  scope.performance.getEntriesByName = () => []
  scope.eval(`(${installReadyProbe.toString()})('housing', ${classifyReadyResource.toString()})`)
  const setup = scope.__readyChain.snapshot()
  assert.equal(setup.cost.requests, 2)
  clock = 100; scope.__readyChain.begin(true)
  clock = 200
  const first = scope.__readyChain.snapshot()
  assert.ok(Array.isArray(first.earlier), 'completed crossing resources must retain their earlier phase')
  assert.equal(first.earlier.length, 1)
  assert.deepEqual(JSON.parse(JSON.stringify(first.earlier[0].cost)), { requests: 7, encodedBytes: 490, transferBytes: 635 })
  assert.deepEqual(JSON.parse(JSON.stringify(first.cost)), { requests: 2, encodedBytes: 120, transferBytes: 180 })
  assert.deepEqual(JSON.parse(JSON.stringify(first.journeyCost)), { requests: 9, encodedBytes: 610, transferBytes: 815 })
  const preparation = first.earlier[0].resources.filter(item => item.target !== 'none')
  assert.equal(preparation.length, 5)
  assert.equal(preparation.every(item => item.startMs === 20 && item.responseEndMs === 120), true)
  assert.equal(first.resources.every(item => ['catalog', 'overview'].includes(item.category)), true)
  assert.equal(first.resources[0].startMs, 10)
  assert.equal(setup.cost.requests, 2, 'later resource accounting must not mutate the original snapshot')
  assert.doesNotMatch(JSON.stringify(first), /http:|assets\/|api\/|AppShell/)
  clock = 210; scope.__readyChain.begin(true)
  clock = 220
  const back = scope.__readyChain.snapshot()
  assert.equal(back.earlier.length, 2)
  assert.deepEqual(JSON.parse(JSON.stringify(back.earlier[0].cost)), { requests: 7, encodedBytes: 490, transferBytes: 635 })
  assert.deepEqual(JSON.parse(JSON.stringify(back.earlier[1].cost)), { requests: 2, encodedBytes: 120, transferBytes: 180 })
  assert.equal(back.cost.requests, 0)
  scope.__readyChain.dispose(); dom.window.close()
})
test('serialized probe retains a setup private query even when it completes only after first begins', () => {
  const dom = new JSDOM('<body></body>', { url: 'http://local', runScripts: 'outside-only' })
  const scope = dom.window
  let clock = 90
  scope.performance.now = () => clock
  scope.performance.getEntriesByType = type => type === 'resource' && clock >= 120 ? [{
    name: 'http://local/api/v1/people?name=PRIVATE', entryType: 'resource', startTime: 20,
    requestStart: 21, responseStart: 119, responseEnd: 120, transferSize: 100,
    encodedBodySize: 80, decodedBodySize: 120, responseStatus: 200,
  }] : []
  scope.performance.getEntriesByName = () => []
  scope.eval(`(${installReadyProbe.toString()})('personnel', ${classifyReadyResource.toString()})`)
  assert.equal(scope.__readyChain.snapshot().resources.length, 0)
  clock = 100; scope.__readyChain.begin(true)
  clock = 130
  const first = scope.__readyChain.snapshot()
  assert.ok(Array.isArray(first.earlier), 'late private queries must not escape setup ownership')
  assert.equal(first.earlier[0].resources[0].category, 'people_list')
  assert.equal(first.earlier[0].resources[0].startMs, 20)
  assert.equal(first.resources.length, 0)
  assert.equal(first.earlier[0].cost.requests, 1)
  assert.doesNotMatch(JSON.stringify(first), /PRIVATE|http:|name=/)
  scope.__readyChain.dispose(); dom.window.close()
})
test('resource128, DOM32 and each API16 caps reject over-limit evidence', () => {
  const p = phase(); p.resources = Array.from({ length: 129 }, () => resource('other_script'))
  assert.equal(validateReadyPhase(p), null)
  p.resources = Array.from({ length: 17 }, () => resource('portal'))
  assert.equal(validateReadyPhase(p), null)
  p.resources = []; p.dom = Array.from({ length: 33 }, () => ({ kind: 'heading', visible: true, ms: 1 }))
  assert.equal(validateReadyPhase(p), null)
})

function modulePhase(scenario, name, profile) {
  const p = { ...phase(), scenario, phase: name, profile }
  p.stages = (name === 'setup' ? ['goto_done', 'workbench', 'settled'] : name === 'back' ? ['nav_clicked', 'workbench']
    : ['nav_clicked', 'heading', 'ready', 'settled', ...(scenario === 'housing'
      ? ['room_card', 'filter_search', 'filter_apply'] : ['table', 'person_search', 'person_query', 'updating_absent'])]).map(stage)
  p.resources = name === 'setup' ? ['housing', 'personnel'].flatMap(target =>
    ['leaf_js', 'shared_css', ...(target === 'personnel' ? ['css_owner_js'] : [])].map(category => ({ ...resource(category), target })))
    : (scenario === 'housing' ? ['catalog', 'overview'] : ['people_list', 'people_count']).map(resource)
  return p
}

test('only the original eleven-phase prefix plus eight ordered throttled phases can complete a journey', () => {
  const phases = ['cold', 'prime', 'warm'].map(name => ({ ...phase(), phase: name }))
  for (const profile of ['unthrottled', 'throttled']) {
    for (const scenario of ['housing', 'personnel']) for (const name of ['setup', 'first', 'back', 'again']) {
      phases.push(modulePhase(scenario, name, profile))
    }
  }
  assert.equal(readyJourneyComplete(phases), true)
  assert.equal(readyJourneyComplete(phases.slice(0, 11)), false)
  assert.equal(readyJourneyComplete(phases.slice(0, 18)), false)
  assert.equal(readyJourneyComplete([...phases, phases[18]]), false)
  assert.equal(readyJourneyComplete(phases.toReversed()), false)
  assert.equal(readyJourneyComplete(Array(19).fill(phase())), false)
  const duplicate = globalThis.structuredClone(phases); duplicate[11] = duplicate[3]
  assert.equal(readyJourneyComplete(duplicate), false)
  const swapped = globalThis.structuredClone(phases); [swapped[11], swapped[15]] = [swapped[15], swapped[11]]
  assert.equal(readyJourneyComplete(swapped), false)
  const wrongProfile = globalThis.structuredClone(phases); wrongProfile[0].profile = 'unthrottled'
  assert.equal(readyJourneyComplete(wrongProfile), false)
  const missingPrepared = globalThis.structuredClone(phases)
  missingPrepared[3].resources = missingPrepared[3].resources.filter(r => r.target !== 'personnel')
  assert.equal(readyJourneyComplete(missingPrepared), false)
  const earlyQuery = globalThis.structuredClone(phases); earlyQuery[3].resources.push(resource('people_list'))
  assert.equal(readyJourneyComplete(earlyQuery), false)
})

test('throttled module first retains private ready evidence while setup owns approved preparation costs', () => {
  const housing = modulePhase('housing', 'first', 'throttled')
  assert.equal(readyPhaseComplete(housing), true)
  const people = modulePhase('personnel', 'first', 'throttled')
  assert.equal(readyPhaseComplete(people), true)
  people.resources = people.resources.filter(r => r.category !== 'people_list')
  assert.equal(readyPhaseComplete(people), false)
  for (const mutate of [p => { p.profile = 'custom' }, p => { p.resources[0].responseEndMs = null },
    p => { p.stages = p.stages.filter(s => s.name !== 'room_card') }, p => { p.unavailable = true },
    p => { p.resources.push({ ...resource('css_owner_js'), path: 'PRIVATE' }) }]) {
    const p = globalThis.structuredClone(housing); mutate(p)
    assert.equal(readyPhaseComplete(p), false)
  }
})
test('passive DOM observer records only fixed visibility transitions and disconnects', async () => {
  const dom = new JSDOM('<body><p role="status">正在加载页面…</p><div>PRIVATE</div></body>', { url: 'http://local' })
  const scope = dom.window
  scope.HTMLElement.prototype.getClientRects = function () { return this.hidden ? [] : [{}] }
  scope.performance.getEntriesByType = () => []
  scope.performance.getEntriesByName = () => []
  installReadyProbe('housing', classifyReadyResource, scope)
  scope.document.body.insertAdjacentHTML('beforeend', '<h1>住宿房态</h1>')
  scope.document.querySelector('p').remove()
  await Promise.resolve()
  const result = scope.__readyChain.snapshot()
  assert.equal(result.dom.some(x => x.kind === 'fallback' && x.visible), true)
  assert.equal(result.dom.some(x => x.kind === 'fallback' && !x.visible), true)
  assert.equal(result.dom.some(x => x.kind === 'heading' && x.visible), true)
  assert.doesNotMatch(JSON.stringify(result), /PRIVATE|住宿|正在加载/)
  scope.__readyChain.dispose()
  assert.equal(scope.__readyChain, undefined)
  dom.window.close()
})
