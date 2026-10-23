import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import { test } from 'node:test'
import { EventEmitter } from 'node:events'
import { URL } from 'node:url'
import { createServer } from 'node:http'
import process from 'node:process'
import { JSDOM } from 'jsdom'
import { assembleDirectPhase, captureDirect, directFailure, directRunComplete, installDirectWitnesses, observeDirectNetwork, validateDirectPhase } from '../tests/helpers/directDocumentDiagnostic.mjs'
import { classifyReadyResource, installReadyProbe } from '../tests/helpers/readyChainDiagnostic.mjs'
import { loadingProfiles } from '../playwright.loading.config.ts'
import { projectDirectReport } from './run-direct-document.mjs'
const hook = registerHooks({ resolve(specifier, context, next) {
  return next(specifier === '../../playwright.loading.config' && context.parentURL?.endsWith('/directDocumentJourney.ts') ? `${specifier}.ts` : specifier, context)
} })
const journey = await import('../tests/helpers/directDocumentJourney.ts')
hook.deregister()
const candidate = 'b4b3eb258e8a99033dfc668cd4c1313d3a5e4030'
const baseline = '95da4227ae831eeb4edfd75d3fc31bcdaa8882ae'

async function run(actionError = null, brokenObservation = false, sourceCommit = candidate, omitAsset = null, invalidBytes = false, terminalBeforeResponse = false) {
  assert.equal(typeof journey.observeDirectDocuments, 'function')
  const effects = [], info = { config: { metadata: { directDocument: { baseURL: 'http://local', credentialsFile: new URL('../package.json', import.meta.url), fingerprint: { sourceCommit } } } }, annotations: [] }
  let open = 0, error
  const browser = { newContext: async ({ viewport }) => {
    assert.equal(open++, 0, 'contexts must not overlap')
    effects.push(['context', viewport.width])
    const dom = new JSDOM('<body></body>', { url: 'http://local', runScripts: 'outside-only' }), w = dom.window
    w.HTMLElement.prototype.getClientRects = () => [{}]
    let init, clock = 0, path, navigation = 0, entries = []
    Object.defineProperty(w.performance, 'timeOrigin', { value: 1010000 })
    w.performance.now = () => clock
    w.performance.getEntriesByType = type => entries.filter(e => e.entryType === type)
    w.performance.getEntriesByName = () => [{ startTime: 4 }]
    const cdp = new EventEmitter()
    cdp.send = async (name, payload) => {
      if (name === 'Page.getFrameTree') return { frameTree: { frame: { id: 'private-frame' } } }
      if (name === 'Performance.getMetrics') {
        if (brokenObservation) throw new Error('PRIVATE_METRIC')
        return { metrics: [{ name: 'ScriptDuration', value: 0.01 }] }
      }
      effects.push([name, payload])
      return {}
    }
    cdp.detach = async () => { assert.equal(cdp.eventNames().length, 0); effects.push(['detach']) }
    const locator = (name = 'card') => ({ _apiName: 'Locator', _selector: 'unit-fixture',
      _expect: async expression => { effects.push(['assert', name, expression]); return { matches: true, received: { value: true }, log: [], timedOut: false } },
      first() { return this }, getByLabel: value => locator(value), getByRole: (_role, opts) => locator(opts.name),
      click: async () => { effects.push(['click', name]); clock += 1 },
    })
    const navigate = async (target, kind) => {
      effects.push([kind, target]); path = target; navigation++; clock = 0
      w.document.body.innerHTML = ''
      w.eval(init)
      const endpoints = [target, '/api/v1/auth/me', '/api/v1/auth/csrf', '/api/v1/portal/context',
        ...(target === '/housing' ? ['/api/v1/housing/catalog', '/api/v1/housing/overview'] : ['/api/v1/people', '/api/v1/people/counts'])]
      entries = endpoints.map((endpoint, index) => {
        const id = `PRIVATE-${navigation}-${index}`, time = 10 + index / 1000
        cdp.emit('Network.requestWillBeSent', { requestId: id, frameId: 'private-frame', type: index ? 'Fetch' : 'Document', timestamp: time, wallTime: 1000 + time, request: { url: `http://local${endpoint}` } })
        cdp.emit('Network.responseReceived', { requestId: id, timestamp: time + (terminalBeforeResponse ? 0.02 : 0.01),
          response: { status: 200, headers: { 'cache-control': 'max-age=3600' }, timing: { requestTime: time, receiveHeadersEnd: 5 } } })
        cdp.emit('Network.loadingFinished', { requestId: id, timestamp: time + (terminalBeforeResponse ? 0.01 : 0.02) })
        if (!index) cdp.emit('Network.responseReceivedExtraInfo', { requestId: id, statusCode: 200 })
        return { name: `http://local${endpoint}`, entryType: index ? 'resource' : 'navigation', startTime: index, requestStart: index + 1,
          responseStart: index + 10, responseEnd: index + 20, encodedBodySize: 10, transferSize: 20, decodedBodySize: 30, responseStatus: 200 }
      })
      const assets = sourceCommit === baseline ? ['index-abc.js', 'index-abc.css']
        : ['index-abc.js', 'index-abc.css', 'AppShell-abc.js', 'HousingOverviewPage-abc.js', 'housing-abc.css', 'PeoplePage-abc.js', 'people-abc.css', 'people-abc.js']
      entries.push(...assets.filter(name => name !== omitAsset).map(name => ({ name: `http://local/assets/${name}`, entryType: 'resource',
        startTime: 1, requestStart: 2, responseStart: 3, responseEnd: 4, encodedBodySize: invalidBytes ? -1 : 10, transferSize: 20, decodedBodySize: 30, responseStatus: 200 })))
      clock = 30
      w.document.body.innerHTML = `<div class="appShell"><h1>${target === '/housing' ? '住宿房态' : '人员中心'}</h1><article class="housingRoomCard"></article><div class="peopleTable" role="table" aria-label="人员名单"></div></div>`
      await Promise.resolve()
      if (actionError) throw actionError
    }
    const page = { setDefaultTimeout: () => {}, viewportSize: () => viewport,
      getByRole: (_role, opts) => locator(opts.name), getByLabel: locator, getByText: locator, locator,
      evaluate: async (fn, arg) => w.eval(`(${fn.toString()})(${JSON.stringify(arg)})`),
      goto: async target => navigate(target, 'goto'), reload: async () => navigate(path, 'reload'),
      waitForTimeout: async value => { assert.equal(value, 2000); clock = 2030; effects.push(['settle']) } }
    return { addInitScript: async ({ content }) => { init = content }, newPage: async () => page, newCDPSession: async () => cdp,
      request: { post: async target => { effects.push(['auth', target]); return { status: () => 200 } } },
      close: async () => { open--; effects.push(['close']); dom.window.close() } }
  } }
  try { await journey.observeDirectDocuments(browser, info) } catch (caught) { error = caught }
  assert.equal(open, 0)
  const raw = info.annotations.find(a => a.type === 'direct-document-evidence')?.description
  assert.ok(raw)
  assert.doesNotMatch(raw, /PRIVATE|http:|requestId|"headers":|timeOrigin/)
  const failures = JSON.parse(info.annotations.find(a => a.type === 'direct-document-failures')?.description ?? 'null')
  const rejections = JSON.parse(info.annotations.find(a => a.type === 'direct-document-rejections')?.description ?? 'null')
  return { error, phases: JSON.parse(raw), failures, rejections, effects }
}

test('real journey executes fixed same-context auth/cold/prime/warm with original predicates, click and cache transitions', async () => {
  const { error, phases, effects } = await run()
  assert.equal(error, undefined)
  assert.equal(directRunComplete(phases, candidate), true)
  assert.equal(effects.filter(e => e[0] === 'auth').length, 11)
  assert.equal(effects.filter(e => e[0] === 'reload').length, 11)
  assert.equal(effects.filter(e => e[0] === 'goto').length, 22)
  assert.equal(effects.filter(e => e[0] === 'settle').length, 33)
  assert.deepEqual(effects.filter(e => e[0] === 'click'), Array(3).fill(['click', '打开筛选']))
  assert.deepEqual(effects.filter(e => e[0] === 'Network.setCacheDisabled').map(e => e[1].cacheDisabled), Array.from({ length: 11 }, () => [true, false]).flat())
  assert.deepEqual(effects.filter(e => e[0] === 'Emulation.setCPUThrottlingRate').map(e => e[1].rate), [4, 4, 4, 4, 1, 1, 1, 1, 1, 1, 1])
  assert.deepEqual(phases[0].stages.map(s => s.name), ['goto_done', 'heading', 'room_card', 'filter_search', 'filter_apply', 'ready', 'settled'])
  assert.deepEqual(phases[6].stages.map(s => s.name), ['goto_done', 'heading', 'table', 'person_search', 'person_query', 'updating_absent', 'ready', 'settled'])
})
test('real journey retains failed phase, original action error and cleanup when observation also fails', async () => {
  const original = new Error('PRIVATE_ACTION')
  const { error, phases, failures, effects } = await run(original, true)
  assert.equal(error, original)
  assert.equal(phases.length, 1)
  assert.equal(phases[0].unavailable, true)
  assert.equal(directRunComplete(phases, candidate), false)
  assert.equal(effects.filter(e => e[0] === 'context').length, 1)
  assert.deepEqual(failures?.map(f => [f.kind, f.stage, f.phaseIndex]), [['action_exception', 'navigate', 0], ['observer_exception', 'metrics', 0]])
})

test('real journey uses the bound source contract and refuses missing candidate style evidence without fallback', async () => {
  const original = await run(null, false, baseline, null, false, true)
  assert.equal(original.error, undefined)
  assert.equal(directRunComplete(original.phases, baseline), true)
  assert.equal(directRunComplete(original.phases, candidate), false)
  const report = { suites: [{ specs: [{ tests: [{ status: 'expected', results: [{ status: 'passed' }], annotations: [
    { type: 'direct-document-evidence', description: JSON.stringify(original.phases) },
    { type: 'direct-browser-version', description: '151.0.7922.34' },
    { type: 'direct-document-failures', description: '[]' },
    { type: 'direct-document-rejections', description: '[]' },
  ] }] }] }] }
  assert.equal(projectDirectReport(report, 0, baseline).ok, true)
  const retained = projectDirectReport(report, 0, baseline).phases[0].network.requests[0]
  assert.deepEqual([retained.responseEventMs, retained.terminalEventMs, retained.headersEndMs], [20, 10, 5])
  assert.equal(retained.terminalResponseRelation, 'before', 'report preserves event semantics without turning them into a duration')
  assert.equal(retained.terminalSource, 'loading_finished')
  const withoutFailureChannel = globalThis.structuredClone(report)
  withoutFailureChannel.suites[0].specs[0].tests[0].annotations.splice(2, 1)
  assert.equal(projectDirectReport(withoutFailureChannel, 0, baseline).ok, false)
  assert.equal(projectDirectReport(report, 0, candidate).ok, false)
  assert.equal(projectDirectReport(report, 0).ok, false)
  const missing = await run(null, false, candidate, 'housing-abc.css')
  assert.match(missing.error?.message ?? '', /direct document evidence incomplete/)
  assert.equal(missing.phases.length, 1)
  assert.equal(missing.effects.filter(e => e[0] === 'context').length, 1)
})

test('actual journey independently annotates the first invalid assembled resource field', async () => {
  const result = await run(null, false, baseline, null, true)
  assert.ok(result.error)
  assert.deepEqual(result.phases, [null])
  assert.deepEqual(result.rejections, [{ schemaVersion: 1, phaseIndex: 0, validator: 'ready_projection',
    field: 'resource.encoded_bytes', rule: 'integer_bounds', itemIndex: 5 }])
})

test('loopback Chromium validates a normal throttled assembled snapshot and independent failure projection without ERP',
  { skip: process.env.DIRECT_DOCUMENT_LOOPBACK !== '1' }, async () => {
    const { chromium } = await import('@playwright/test')
    const server = createServer((request, response) => {
      if (request.url === '/fixture.js') { response.writeHead(200, { 'Content-Type': 'text/javascript' }); response.end('globalThis.fixtureLoaded = true'); return }
      if (request.url === '/fixture.css') { response.writeHead(200, { 'Content-Type': 'text/css' }); response.end('body { color: #123; }'); return }
      response.writeHead(200, { 'Content-Type': 'text/html' })
      response.end('<!doctype html><title>fixture</title><link rel="stylesheet" href="/fixture.css"><script src="/fixture.js"></script><body><p>fixture</p></body>')
    })
    let browser, context, network
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    try {
      const origin = `http://127.0.0.1:${server.address().port}`
      browser = await chromium.launch({ headless: true })
      context = await browser.newContext({ serviceWorkers: 'block', viewport: { width: 1440, height: 900 } })
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort())
      await context.addInitScript({ content: `(${installReadyProbe.toString()})('housing', ${classifyReadyResource.toString()});(${installDirectWitnesses.toString()})()` })
      const page = await context.newPage(), cdp = await context.newCDPSession(page), failures = [], pageErrors = []
      page.on('pageerror', error => pageErrors.push(directFailure('observer_exception', 'init', 0, error)))
      network = observeDirectNetwork(cdp, origin, (await cdp.send('Page.getFrameTree')).frameTree.frame.id)
      await cdp.send('Network.enable'); await cdp.send('Performance.enable')
      await cdp.send('Network.emulateNetworkConditions', loadingProfiles.throttled)
      await cdp.send('Emulation.setCPUThrottlingRate', { rate: 4 }); await cdp.send('Network.setCacheDisabled', { cacheDisabled: true }); network.begin()
      await page.goto(origin, { waitUntil: 'domcontentloaded', timeout: 60000 }); await page.waitForTimeout(2000)
      const boundary = await page.evaluate(() => ({ snapshot: globalThis.__readyChain?.snapshot(), witnesses: globalThis.__directWitnesses?.snapshot(), timeOrigin: globalThis.performance.timeOrigin }))
      const metric = await cdp.send('Performance.getMetrics').then(result => result.metrics.find(m => m.name === 'ScriptDuration')?.value)
      const evidence = network.snapshot(boundary.timeOrigin)
      const normal = assembleDirectPhase({ scenario: 'housing', viewport: 'desktop', profile: 'throttled', cycle: 0 }, 'cold', boundary, metric, evidence, false)
      const normalRejections = [], accepted = validateDirectPhase(normal, reason => normalRejections.push({ schemaVersion: 1, phaseIndex: 0, ...reason })) !== null
      process.stdout.write(`Normal loopback validation: ${JSON.stringify({ accepted, rejections: normalRejections })}\n`)
      assert.equal(accepted, true, 'normal real-browser assembly must validate; report fixed reason before any repair')
      let saved, original
      await assert.rejects(captureDirect(async () => {
        try { await page.evaluate(() => { throw new TypeError('PRIVATE_SYNTHETIC_ACTION') }) } catch (error) { original = error; throw error }
      }, async () => { await page.evaluate(() => { throw new ReferenceError('PRIVATE_SYNTHETIC_OBSERVER') }) }, p => { saved = p }, baseline,
      (kind, error) => failures.push(directFailure(kind, kind === 'action_exception' ? 'navigate' : 'snapshot', 0, error))), error => error === original)
      const report = { suites: [{ specs: [{ tests: [{ status: 'unexpected', results: [{ status: 'failed' }], annotations: [
        { type: 'direct-document-evidence', description: JSON.stringify([saved]) },
        { type: 'direct-document-failures', description: JSON.stringify(failures) },
        { type: 'direct-document-rejections', description: '[]' },
        { type: 'direct-browser-version', description: browser.version() },
      ] }] }] }] }
      const result = projectDirectReport(report, 1, baseline)
      assert.equal(result.ok, false)
      assert.deepEqual(result.failures.slice(0, 2).map(f => f.kind), ['action_exception', 'observer_exception'])
      assert.doesNotMatch(JSON.stringify(result), /PRIVATE|SYNTHETIC|http:|message|stack/)
      assert.ok(boundary.snapshot)
      assert.equal(boundary.witnesses.dom.some(d => d.kind === 'room_card' && !d.visible), true)
      assert.deepEqual(pageErrors, [])
      assert.equal(evidence.requests.filter(r => r.category === 'document').length, 1)
      process.stdout.write(`Loopback characterization: ${JSON.stringify({ browserVersion: browser.version(), failureKinds: result.failures.map(f => f.kind),
        errorNames: failures.map(f => f.errorName), documentStartUnavailable: evidence.requests[0].startMs === null,
        documentTerminalUnavailable: evidence.requests[0].terminalEventMs === null,
        terminalSource: evidence.requests[0].terminalSource, terminalResponseRelation: evidence.requests[0].terminalResponseRelation,
        headersTimingState: evidence.requests[0].headersTimingState, headersStartRelation: evidence.requests[0].headersStartRelation,
        terminalHeadersRelation: evidence.requests[0].terminalHeadersRelation, pageErrors })}\n`)
    } finally {
      network?.dispose()
      await context?.close()
      await browser?.close()
      await new Promise(resolve => server.close(resolve))
    }
  })
