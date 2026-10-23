import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import { URL } from 'node:url'
import { JSDOM } from 'jsdom'
import { readyJourneyComplete, readyPhaseComplete, validateReadyPhase } from '../tests/helpers/readyChainDiagnostic.mjs'

// Node strips the real helper's TypeScript; resolve its one extensionless
// config import without copying or replacing the capture/error-control code.
const hook = registerHooks({ resolve(specifier, context, next) {
  return next(specifier === '../../playwright.loading.config' && context.parentURL?.endsWith('/readyChainJourney.ts')
    ? `${specifier}.ts` : specifier, context)
} })
const { observeReadyChains } = await import('../tests/helpers/readyChainJourney.ts')
hook.deregister()

async function failedCapture(actionError) {
  const dom = new JSDOM('<body><h1>登录工作台</h1></body>', { url: 'http://local', runScripts: 'outside-only' })
  const scope = dom.window
  scope.HTMLElement.prototype.getClientRects = () => [{}]
  scope.performance.now = () => 10
  const entry = (name, entryType) => ({ name, entryType, startTime: 0, requestStart: 1, responseStart: 2,
    responseEnd: 3, encodedBodySize: 80, transferSize: 100, decodedBodySize: 120, responseStatus: 200 })
  scope.performance.getEntriesByType = type => type === 'resource' ? [entry('http://local/api/v1/auth/me', 'resource')]
    : type === 'navigation' ? [entry('http://local/login', 'navigation')] : []
  scope.performance.getEntriesByName = () => [{ startTime: 4 }]
  const metricError = new Error('METRIC_PRIVATE_FAILURE')
  const events = new Map()
  const cdp = {
    on: (name, callback) => events.set(name, callback),
    send: async name => {
      if (name === 'Performance.getMetrics') throw metricError
      if (name === 'Page.getFrameTree') return { frameTree: { frame: { id: 'private-frame' } } }
      return {}
    },
    detach: async () => {},
  }
  // Only the external browser boundary is doubled. Real Playwright assertions,
  // the serialized probe, action, finally block and evidence writer all run.
  const locator = () => ({ _apiName: 'Locator', _selector: 'unit-fixture',
    _expect: async expression => {
      assert.ok(['to.be.visible', 'to.be.editable', 'to.be.enabled'].includes(expression))
      return { matches: true, received: { value: true }, log: [], timedOut: false }
    } })
  const page = {
    setDefaultTimeout: () => {},
    getByRole: locator, getByLabel: locator,
    evaluate: async (fn, arg) => scope.eval(`(${fn.toString()})(${JSON.stringify(arg)})`),
    goto: async () => {
      events.get('Network.requestWillBeSent')({ type: 'Document', frameId: 'private-frame', requestId: 'private-request' })
      events.get('Network.responseReceived')({ requestId: 'private-request', response: { status: 200, headers: { 'cache-control': 'no-cache' } } })
      events.get('Network.responseReceivedExtraInfo')({ requestId: 'private-request', statusCode: 200 })
      if (actionError) throw actionError
    },
    waitForTimeout: async duration => { assert.equal(duration, 2000) },
  }
  const context = { addInitScript: async ({ content }) => scope.eval(content),
    newPage: async () => page, newCDPSession: async () => cdp, close: async () => dom.window.close() }
  const info = { config: { metadata: { routeLoading: { baseURL: 'http://local' } } }, annotations: [] }
  let error
  try { await observeReadyChains({ newContext: async () => context }, { width: 1440, height: 900 }, info) }
  catch (caught) { error = caught }
  const evidence = info.annotations.find(item => item.type === 'ready-chain-evidence')?.description
  assert.ok(evidence, 'the actual outer finally must retain the attempted phase')
  assert.doesNotMatch(evidence, /PRIVATE|private-|http:|requestId|headers|stack|message/)
  return { error, metricError, phases: JSON.parse(evidence) }
}

test('actual journey preserves the original action error when final metrics also fail', async () => {
  const actionError = new Error('ACTION_PRIVATE_FAILURE')
  const result = await failedCapture(actionError)
  assert.equal(result.error, actionError)
  assert.equal(result.phases.length, 1)
  const phase = result.phases[0]
  assert.notEqual(phase, null)
  assert.equal(phase.unavailable, true)
  assert.equal(phase.cost.scriptDurationMs, null)
  assert.equal(phase.journeyCost.scriptDurationMs, null)
  assert.notEqual(validateReadyPhase(phase), null)
  assert.equal(readyPhaseComplete(phase), false)
})

test('actual journey records explicit unavailable evidence and fails closed on metrics-only failure', async () => {
  const result = await failedCapture()
  assert.notEqual(result.error, result.metricError)
  assert.match(result.error?.message ?? '', /fixed ready-chain phase must be complete and bounded/)
  assert.equal(result.phases.length, 1)
  const phase = result.phases[0]
  assert.notEqual(phase, null)
  assert.equal(phase.unavailable, true)
  assert.equal(phase.cost.scriptDurationMs, null)
  assert.equal(phase.journeyCost.scriptDurationMs, null)
  assert.equal(phase.stages.some(stage => stage.name === 'ready'), true)
  assert.equal(phase.stages.some(stage => stage.name === 'settled'), true)
  assert.notEqual(validateReadyPhase(phase), null)
  assert.equal(readyPhaseComplete(phase), false)
})

async function completedJourney(earlyPrivate = false) {
  let contexts = 0
  const browser = { newContext: async () => {
    const scenario = ['login', 'housing', 'personnel', 'housing', 'personnel'][contexts++]
    const dom = new JSDOM('<body></body>', { url: 'http://local', runScripts: 'outside-only' })
    const scope = dom.window
    scope.HTMLElement.prototype.getClientRects = () => [{}]
    let clock = 50, leg = 'setup', init = '', entries = []
    const entry = (path, startTime, responseEnd, entryType = 'resource') => ({ name: `http://local${path}`, entryType,
      startTime, requestStart: startTime + 1, responseStart: responseEnd - 1, responseEnd,
      encodedBodySize: 80, transferSize: 100, decodedBodySize: 120, responseStatus: 200 })
    scope.performance.now = () => clock
    scope.performance.getEntriesByType = type => entries.filter(item => item.entryType === type && item.responseEnd <= clock)
    scope.performance.getEntriesByName = () => [{ startTime: 4 }]
    const events = new Map()
    const cdp = { on: (name, callback) => events.set(name, callback), detach: async () => {}, send: async name => {
      if (name === 'Page.getFrameTree') return { frameTree: { frame: { id: 'private-frame' } } }
      return name === 'Performance.getMetrics' ? { metrics: [{ name: 'ScriptDuration', value: clock / 1000 }] } : {}
    } }
    const locator = (role, { name } = {}) => ({ _apiName: 'Locator', _selector: 'unit-fixture',
      _expect: async expression => {
        assert.ok(['to.be.visible', 'to.be.editable', 'to.be.enabled', 'to.have.count'].includes(expression))
        return { matches: true, received: { value: true }, log: [], timedOut: false }
      },
      isVisible: async () => role !== 'button', filter() { return this }, first() { return this },
      getByRole: (...args) => locator(...args), getByLabel: () => locator(),
      click: async () => {
        if (name === '工作台') { leg = 'back'; clock = 220 }
        else { leg = leg === 'back' ? 'again' : 'first'; clock = leg === 'first' ? 120 : 240 }
        scope.document.body.innerHTML = `<div class="appShell"><h1>${name === '工作台' ? name : scenario === 'housing' ? '住宿房态' : '人员中心'}</h1></div>`
      },
    })
    const page = { setDefaultTimeout: () => {}, viewportSize: () => ({ width: 1440, height: 900 }),
      getByRole: locator, getByLabel: () => locator(), getByText: () => locator(), locator: () => locator(),
      evaluate: async (fn, arg) => scope.eval(`(${fn.toString()})(${JSON.stringify(arg)})`),
      goto: async () => {
        clock = 50; leg = 'setup'
        entries = [entry('/', 0, 5, 'navigation'), entry('/api/v1/auth/me', 1, 10), entry('/assets/AppShell-a.js', 1, 10)]
        if (scenario !== 'login') entries.push(
          ...['HousingOverviewPage-a.js', 'housing-a.css', 'PeoplePage-a.js', 'people-a.js', 'people-a.css'].map(path => entry(`/assets/${path}`, 20, 150)),
          ...[110, 230].flatMap(start => (scenario === 'housing' ? ['/api/v1/housing/catalog', '/api/v1/housing/overview']
            : ['/api/v1/people', '/api/v1/people/counts']).map(path => entry(path, start, start + 30))),
          ...(earlyPrivate ? [entry('/api/v1/people?name=PRIVATE', 30, 180)] : []))
        scope.document.body.innerHTML = `<div class="appShell"><h1>${scenario === 'login' ? '登录工作台' : '工作台'}</h1></div>`
        scope.eval(init)
        events.get('Network.requestWillBeSent')({ type: 'Document', frameId: 'private-frame', requestId: 'private-request' })
        events.get('Network.responseReceived')({ requestId: 'private-request', response: { status: 200, headers: { 'cache-control': 'no-cache' } } })
        events.get('Network.responseReceivedExtraInfo')({ requestId: 'private-request', statusCode: 200 })
      },
      reload: async () => page.goto(),
      waitForTimeout: async duration => { assert.equal(duration, 2000); clock = leg === 'setup' ? 100 : leg === 'first' ? 200 : 300 },
    }
    return { addInitScript: async ({ content }) => { init = content }, newPage: async () => page,
      newCDPSession: async () => cdp, close: async () => dom.window.close(), request: { post: async () => ({ status: () => 200 }) } }
  } }
  // An existing public JSON file is an opaque request fixture; no credentials,
  // network, browser process, timeout sleep or application server are used.
  const info = { config: { metadata: { routeLoading: { baseURL: 'http://local',
    credentialsFile: new URL('../package.json', import.meta.url) } } }, annotations: [] }
  await observeReadyChains(browser, { width: 1440, height: 900 }, info)
  const evidence = info.annotations.find(item => item.type === 'ready-chain-evidence').description
  assert.doesNotMatch(evidence, /PRIVATE|private-|http:|requestId|headers|earlier/)
  return JSON.parse(evidence)
}

test('actual journey backfills crossing resources while keeping original phase durations and order', async () => {
  const phases = await completedJourney()
  assert.equal(readyJourneyComplete(phases), true)
  assert.equal(phases.length, 19)
  for (const index of [3, 7, 11, 15]) {
    const setup = phases[index], first = phases[index + 1]
    assert.deepEqual(setup.stages, [{ name: 'goto_done', ms: 50 }, { name: 'workbench', ms: 50 }, { name: 'settled', ms: 100 }])
    assert.deepEqual(setup.cost, { requests: 8, encodedBytes: 640, transferBytes: 800, scriptDurationMs: 100 })
    assert.deepEqual(setup.journeyCost, setup.cost)
    assert.equal(setup.fcpMs, 4)
    assert.equal(setup.resources.filter(resource => resource.target !== 'none').length, 5)
    assert.equal(setup.resources.find(resource => resource.category === 'leaf_js').responseEndMs, 150)
    assert.deepEqual(first.cost, { requests: 2, encodedBytes: 160, transferBytes: 200, scriptDurationMs: 100 })
    assert.equal(first.stages.find(stage => stage.name === 'ready').ms, 20)
    assert.equal(first.stages.find(stage => stage.name === 'settled').ms, 100)
  }
})

test('actual journey rejects setup private queries even when their completion is observed during first', async () => {
  const phases = await completedJourney(true)
  assert.equal(readyJourneyComplete(phases), false)
  assert.equal(phases[3].resources.some(resource => resource.category === 'people_list' && resource.startMs === 30), true)
  assert.equal(phases[4].resources.some(resource => resource.category === 'people_list'), false)
})
