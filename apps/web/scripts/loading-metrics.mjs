import { createHash } from 'node:crypto'
import { isDeepStrictEqual } from 'node:util'

/** Self-contained so the exact initializer runs in the browser and in tests. */
export function initializeLoadingObservers(target, Observer) {
  target.__loading = { lcp: null, longTasks: null }
  for (const [type, key] of [['largest-contentful-paint', 'lcp'], ['longtask', 'longTasks']]) {
    if (!Observer?.supportedEntryTypes?.includes(type)) continue
    try {
      target.__loading[key] = []
      new Observer(list => target.__loading[key]?.push(...list.getEntries().map(entry =>
        ({ startTime: entry.startTime, duration: entry.duration })))).observe({ type, buffered: true })
    } catch { target.__loading[key] = null }
  }
}

/**
 * @typedef {{kind: string, encodedBodySize: number, cacheHit: boolean}} ResourceMetric
 * @typedef {'ready_timeout'|'http_error'|'page_error'|'network_error'|'compression_missing'|'measurement_error'|'login_failed'} ErrorCategory
 * @typedef {{scenario: string, viewport: string, cacheMode: string, networkProfile: string,
 * ok: boolean, jsEncodedBytes: number|null, cssEncodedBytes: number|null,
 * requestCount: number|null, readyMs: number|null, fcpMs: number|null,
 * lcpMs: number|null, scriptMs: number|null, longTaskCount: number|null,
 * errors: ErrorCategory[]}} RunMetric
 */

/** Pure response-body accounting. CDP transfer/header sizes are never inputs.
 * @param {ResourceMetric[]} resources
 * @returns {number}
 */
export function sumInitialScriptBytes(resources) {
  return resources.filter(resource => resource.kind === 'script' && !resource.cacheHit)
    .reduce((total, resource) => total + resource.encodedBodySize, 0)
}

export const METRICS = ['jsEncodedBytes', 'cssEncodedBytes', 'requestCount', 'readyMs',
  'fcpMs', 'lcpMs', 'scriptMs', 'longTaskCount']
export const ERROR_CATEGORIES = ['ready_timeout', 'http_error', 'page_error',
  'network_error', 'compression_missing', 'measurement_error', 'login_failed']

export function summarizeRuns(runs) {
  const valid = runs.filter(run => run.ok)
  const result = { validCount: valid.length, failedCount: runs.length - valid.length, failureReasons: {} }
  for (const run of runs.filter(run => !run.ok)) {
    for (const error of new Set(run.errors.map(error => ERROR_CATEGORIES.includes(error) ? error : 'measurement_error'))) {
      result.failureReasons[error] = (result.failureReasons[error] ?? 0) + 1
    }
  }
  for (const key of METRICS) {
    const values = valid.map(run => run[key]).filter(Number.isFinite).sort((a, b) => a - b)
    const middle = Math.floor(values.length / 2)
    result[key] = { median: values.length ? (values.length % 2 ? values[middle]
      : (values[middle - 1] + values[middle]) / 2) : null,
    max: values.length ? values.at(-1) : null, sampleCount: values.length }
  }
  return result
}

export function compareSummaries(baseline, candidate) {
  const reduction = baseline.jsEncodedBytes.median > 0 && candidate.jsEncodedBytes.median !== null
    ? (baseline.jsEncodedBytes.median - candidate.jsEncodedBytes.median) / baseline.jsEncodedBytes.median * 100 : null
  const regression = baseline.readyMs.median > 0 && candidate.readyMs.median !== null
    ? (candidate.readyMs.median - baseline.readyMs.median) / baseline.readyMs.median * 100 : null
  return { jsTargetMet: reduction !== null && reduction >= 40,
    readyRegressionPassed: regression !== null && regression <= 10,
    jsReductionPercent: reduction, readyRegressionPercent: regression,
    baselineSampleCount: baseline.validCount, candidateSampleCount: candidate.validCount }
}

export const BASELINE_COMMIT = '95da4227ae831eeb4edfd75d3fc31bcdaa8882ae'

/** Validate raw samples as well as summaries: summary-only/partial evidence is not acceptance. */
export function validateEvidence(evidence, expected = {}) {
  const refuse = () => { throw new Error('loading evidence incomplete or incompatible') }
  const keys = []
  for (const viewport of ['desktop', 'mobile']) for (const profile of ['throttled', 'unthrottled']) {
    for (const scenario of ['login', 'housing', 'personnel']) {
      for (const mode of scenario === 'login' ? ['cold', 'warm'] : ['cold', 'warm', 'module-first', 'module-again']) {
        keys.push([scenario, viewport, mode, profile].join('/'))
      }
    }
  }
  if (evidence?.schemaVersion !== 1 || !Array.isArray(evidence.runs) || evidence.runs.length !== 280
    || !Array.isArray(evidence.collectorErrors) || evidence.collectorErrors.length
    || !evidence.summaries || !isDeepStrictEqual(Object.keys(evidence.summaries).sort(), keys.toSorted())) refuse()
  const groups = new Map(keys.map(key => [key, []]))
  for (const run of evidence.runs) {
    const key = [run?.scenario, run?.viewport, run?.cacheMode, run?.networkProfile].join('/')
    if (!groups.has(key) || run.ok !== true || !Array.isArray(run.errors) || run.errors.length
      || METRICS.some(metric => !(metric in run) || (run[metric] !== null
        && (!Number.isFinite(run[metric]) || run[metric] < 0))) || run.readyMs === null) refuse()
    groups.get(key).push(run)
  }
  for (const [key, runs] of groups) {
    if (runs.length !== 7 || !isDeepStrictEqual(evidence.summaries[key], summarizeRuns(runs))) refuse()
  }
  const fingerprint = evidence.fingerprint
  if (!fingerprint || !/^[0-9a-f]{40}$/.test(fingerprint.sourceCommit ?? '')
    || fingerprint.productionWebTarget !== true || !fingerprint.browser?.version || !fingerprint.browser?.playwright
    || !/^[0-9a-f]{64}$/.test(fingerprint.proxySha256 ?? '')
    || !/^sha256:[0-9a-f]{64}$/.test(fingerprint.images?.proxy ?? '')
    || !evidence.measurementConfig || fingerprint.measurementConfigSha256 !== createHash('sha256')
      .update(JSON.stringify(evidence.measurementConfig)).digest('hex')) refuse()
  for (const field of ['sourceCommit', 'browser', 'proxySha256']) {
    if (field in expected && !isDeepStrictEqual(fingerprint[field], expected[field])) refuse()
  }
  if ('proxyImage' in expected && fingerprint.images.proxy !== expected.proxyImage) refuse()
  if ('measurementConfig' in expected && !isDeepStrictEqual(evidence.measurementConfig, expected.measurementConfig)) refuse()
  return true
}
