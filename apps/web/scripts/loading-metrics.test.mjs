import assert from 'node:assert/strict'
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import process from 'node:process'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
/* global URL */

const implementation = new URL('./loading-metrics.mjs', import.meta.url)
function evidenceFixture(js = 1000, ready = 100) {
  const measurementConfig = {
    viewports: { desktop: { width: 1440, height: 900 }, mobile: { width: 390, height: 844 } },
    profiles: {
      throttled: { offline: false, downloadThroughput: 1250000, uploadThroughput: 250000, latency: 150 },
      unthrottled: { offline: false, downloadThroughput: -1, uploadThroughput: -1, latency: 0 },
    }, cpuSlowdown: { throttled: 4, unthrottled: 1 }, windowAfterReadyMs: 2000,
    validSamplesPerGroup: 7, retryPolicy: 'none; failures retained and matrix fails',
    ready: { login: 'auth restoration ended, identifier/password/submit enabled',
      housing: 'accepted room query rendered, filter usable', personnel: 'accepted list rendered, search usable' },
  }
  const result = { schemaVersion: 1, measurementConfig, collectorErrors: [], runs: [], summaries: {},
    fingerprint: { sourceCommit: '95da4227ae831eeb4edfd75d3fc31bcdaa8882ae', productionWebTarget: true,
      browser: { version: 'fixture-browser', playwright: 'fixture-playwright' }, proxySha256: 'a'.repeat(64),
      images: { proxy: 'sha256:' + 'b'.repeat(64) },
      measurementConfigSha256: createHash('sha256').update(JSON.stringify(measurementConfig)).digest('hex') } }
  for (const viewport of ['desktop', 'mobile']) for (const networkProfile of ['throttled', 'unthrottled']) {
    for (const scenario of ['login', 'housing', 'personnel']) {
      for (const cacheMode of scenario === 'login' ? ['cold', 'warm'] : ['cold', 'warm', 'module-first', 'module-again']) {
        const spa = cacheMode.startsWith('module')
        const metric = { scenario, viewport, networkProfile, cacheMode, ok: true, errors: [],
          jsEncodedBytes: js, cssEncodedBytes: 20, requestCount: 4, readyMs: ready,
          fcpMs: spa ? null : 50, lcpMs: spa ? null : 80, scriptMs: 10, longTaskCount: 1 }
        result.runs.push(...Array.from({ length: 7 }, () => ({ ...metric })))
        const summary = { validCount: 7, failedCount: 0, failureReasons: {} }
        for (const name of ['jsEncodedBytes', 'cssEncodedBytes', 'requestCount', 'readyMs', 'fcpMs', 'lcpMs', 'scriptMs', 'longTaskCount']) {
          summary[name] = { median: metric[name], max: metric[name], sampleCount: metric[name] === null ? 0 : 7 }
        }
        result.summaries[[scenario, viewport, cacheMode, networkProfile].join('/')] = summary
      }
    }
  }
  return result
}
test('loading metrics implementation exists', () => assert.ok(existsSync(implementation)))
if (existsSync(implementation)) {
  const { sumInitialScriptBytes, summarizeRuns, compareSummaries, initializeLoadingObservers } = await import(implementation)
  test('unsupported observation remains null, while supported empty long tasks means zero', () => {
    const target = {}
    class Observer {
      static supportedEntryTypes = ['longtask']
      observe() {}
    }
    initializeLoadingObservers(target, Observer)
    assert.deepEqual(target.__loading, { lcp: null, longTasks: [] })
    class ThrowingObserver extends Observer { observe() { throw new Error('unavailable') } }
    initializeLoadingObservers(target, ThrowingObserver)
    assert.deepEqual(target.__loading, { lcp: null, longTasks: null })
  })
  test('counts entry and shared script response bodies, not CSS or cached transfer', () => {
    const entryAndSharedChunks = [
      { kind: 'script', encodedBodySize: 100, cacheHit: false },
      { kind: 'script', encodedBodySize: 200, cacheHit: false },
      { kind: 'css', encodedBodySize: 50, cacheHit: false },
      { kind: 'script', encodedBodySize: 900, cacheHit: true },
    ]
    assert.equal(sumInitialScriptBytes(entryAndSharedChunks), 300)
  })
  const run = (readyMs, jsEncodedBytes = 1000) => ({ scenario: 'login', viewport: 'desktop',
    cacheMode: 'cold', networkProfile: 'throttled', ok: true, jsEncodedBytes,
    cssEncodedBytes: 30, requestCount: 4, readyMs, fcpMs: null, lcpMs: null,
    scriptMs: 10, longTaskCount: 2, errors: [] })
  test('seven valid samples retain slowest and failures without inventing unavailable metrics', () => {
    const summary = summarizeRuns([1, 2, 3, 100, 5, 6, 7].map(value => run(value)).concat(
      { ...run(0), ok: false, errors: ['ready_timeout'] }))
    assert.equal(summary.validCount, 7)
    assert.equal(summary.failedCount, 1)
    assert.deepEqual(summary.readyMs, { median: 5, max: 100, sampleCount: 7 })
    assert.deepEqual(summary.fcpMs, { median: null, max: null, sampleCount: 0 })
    assert.deepEqual(summary.failureReasons, { ready_timeout: 1 })
    assert.equal(summary.requestCount.median, 4)
  })
  test('requires at least forty percent script reduction and permits exactly ten percent ready regression', () => {
    const base = summarizeRuns(Array.from({ length: 7 }, () => run(100)))
    const reducedBy40Percent = summarizeRuns(Array.from({ length: 7 }, () => run(110, 600)))
    assert.equal(compareSummaries(base, reducedBy40Percent).jsTargetMet, true)
    assert.equal(compareSummaries(base, reducedBy40Percent).readyRegressionPassed, true)
    assert.equal(compareSummaries(base, reducedBy40Percent).jsReductionPercent, 40)
    assert.equal(compareSummaries(base, reducedBy40Percent).candidateSampleCount, 7)
    assert.equal(compareSummaries(base, summarizeRuns([run(110.01, 601)])).jsTargetMet, false)
    assert.equal(compareSummaries(base, summarizeRuns([run(110.01, 601)])).readyRegressionPassed, false)
  })
  test('missing or zero baseline measurements are not fabricated percentage successes', () => {
    const result = compareSummaries(summarizeRuns([]), summarizeRuns([]))
    assert.equal(result.jsReductionPercent, null)
    assert.equal(result.jsTargetMet, false)
    assert.equal(result.readyRegressionPassed, false)
  })
  test('unexpected exception text cannot enter public failure summaries', () => {
    const summary = summarizeRuns([{ ...run(0), ok: false, errors: ['private-error-text'] }])
    assert.deepEqual(summary.failureReasons, { measurement_error: 1 })
  })
  test('comparison CLI emits actual percentages/sample counts and fails a ready regression', () => {
    const directory = mkdtempSync(join(tmpdir(), 'loading-comparison-test-'))
    const key = 'login/desktop/cold/throttled'
    const cli = fileURLToPath(new URL('./measure-loading.mjs', import.meta.url))
    try {
      writeFileSync(join(directory, 'baseline.json'), JSON.stringify(evidenceFixture()))
      writeFileSync(join(directory, 'candidate.json'), JSON.stringify(evidenceFixture(600, 110)))
      assert.equal(spawnSync(process.execPath, [cli, '--compare', directory]).status, 0)
      const comparison = JSON.parse(readFileSync(join(directory, 'comparison.json'), 'utf8')).comparison[key]
      assert.deepEqual(comparison, { jsTargetMet: true, readyRegressionPassed: true,
        jsReductionPercent: 40, readyRegressionPercent: 10, baselineSampleCount: 7, candidateSampleCount: 7 })
      const report = JSON.parse(readFileSync(join(directory, 'comparison.json'), 'utf8'))
      assert.equal(report.jsTargetMet, true)
      assert.equal(report.readyRegressionPassed, true)
      assert.deepEqual(report.sampleCounts, { baseline: 280, candidate: 280, groups: 40, perGroup: 7 })
      assert.deepEqual(report.failureReasons, { baseline: {}, candidate: {} })
      writeFileSync(join(directory, 'candidate.json'), JSON.stringify(evidenceFixture(601, 100)))
      assert.equal(spawnSync(process.execPath, [cli, '--compare', directory]).status, 1)
      writeFileSync(join(directory, 'candidate.json'), JSON.stringify(evidenceFixture(600, 111)))
      assert.equal(spawnSync(process.execPath, [cli, '--compare', directory]).status, 1)
      assert.equal(JSON.parse(readFileSync(join(directory, 'comparison.json'), 'utf8')).comparison[key].readyRegressionPassed, false)
    } finally { rmSync(directory, { recursive: true, force: true }) }
  })
  test('comparison rejects incomplete, failed, forged-summary and incompatible evidence instead of reporting success', async t => {
    const cli = fileURLToPath(new URL('./measure-loading.mjs', import.meta.url))
    const mutations = [
      value => { value.runs.pop() },
      value => { delete value.summaries['personnel/mobile/module-again/unthrottled'] },
      value => { value.runs[0].ok = false; value.runs[0].errors = ['page_error'] },
      value => { value.collectorErrors = ['measurement_error'] },
      value => { value.summaries['login/desktop/cold/throttled'].readyMs.median = 1 },
      value => { value.fingerprint.browser.version = 'other-browser' },
      value => { value.fingerprint.browser.playwright = 'other-playwright' },
      value => { value.fingerprint.proxySha256 = 'c'.repeat(64) },
      value => { value.fingerprint.images.proxy = 'sha256:' + 'd'.repeat(64) },
      value => { value.measurementConfig.profiles.throttled.latency = 1 },
      value => { value.fingerprint.measurementConfigSha256 = 'e'.repeat(64) },
    ]
    for (const target of ['baseline', 'candidate']) for (const [index, mutate] of mutations.entries()) await t.test(`${target} mutation ${index}`, () => {
      const directory = mkdtempSync(join(tmpdir(), 'loading-reject-test-'))
      try {
        const baseline = evidenceFixture()
        const candidate = evidenceFixture(600, 110)
        mutate(target === 'baseline' ? baseline : candidate)
        writeFileSync(join(directory, 'baseline.json'), JSON.stringify(baseline))
        writeFileSync(join(directory, 'candidate.json'), JSON.stringify(candidate))
        assert.notEqual(spawnSync(process.execPath, [cli, '--compare', directory]).status, 0, `mutation ${index} must refuse`)
        assert.equal(existsSync(join(directory, 'comparison.json')), false)
      } finally { rmSync(directory, { recursive: true, force: true }) }
    })
  })
}
