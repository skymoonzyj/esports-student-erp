/** Serial production-browser measurements. No trace/HAR/screenshots/raw URLs. */
/* global console, location, performance, process, URL, window */
import { createHash } from 'node:crypto'
import { readFile, rename, writeFile } from 'node:fs/promises'
import { chromium, expect as playwrightExpect } from '@playwright/test'
import loadingConfig, { loadingProfiles } from '../playwright.loading.config.ts'
import { BASELINE_COMMIT, compareSummaries, initializeLoadingObservers, summarizeRuns, sumInitialScriptBytes, validateEvidence } from './loading-metrics.mjs'

const readJSON = async path => JSON.parse(await readFile(path, 'utf8'))
const save = async (path, value) => {
  await writeFile(`${path}.tmp`, JSON.stringify(value, null, 2), { mode: 0o600 })
  await rename(`${path}.tmp`, path)
}
const expect = playwrightExpect.configure({ timeout: 60000 })
const viewports = Object.fromEntries(loadingConfig.projects.map(project => [project.name, project.use.viewport]))
const profiles = loadingProfiles
const measurementConfig = { viewports, profiles, cpuSlowdown: { throttled: 4, unthrottled: 1 },
  windowAfterReadyMs: 2000, validSamplesPerGroup: 7, retryPolicy: 'none; failures retained and matrix fails',
  ready: { login: 'auth restoration ended, identifier/password/submit enabled',
    housing: 'accepted room query rendered, filter usable', personnel: 'accepted list rendered, search usable' } }
const browserIdentity = async browser => ({ version: browser.version(), playwright: JSON.parse(await readFile(
  new URL('../node_modules/@playwright/test/package.json', import.meta.url), 'utf8')).version })

if (process.argv[2] === '--preflight-baseline') {
  const baseline = await readJSON(process.argv[3])
  const expected = { sourceCommit: BASELINE_COMMIT, measurementConfig,
    proxySha256: process.argv[4], proxyImage: process.argv[5] }
  // Refuse incomplete or changed conditions before even probing a browser.
  validateEvidence(baseline, expected)
  const probe = await chromium.launch({ headless: loadingConfig.use.headless })
  try { validateEvidence(baseline, { ...expected, browser: await browserIdentity(probe) }) }
  finally { await probe.close() }
  process.exit(0)
}

if (process.argv[2] === '--compare') {
  const directory = process.argv[3]
  const baseline = await readJSON(`${directory}/baseline.json`)
  const candidate = await readJSON(`${directory}/candidate.json`)
  validateEvidence(baseline, { sourceCommit: BASELINE_COMMIT, measurementConfig })
  validateEvidence(candidate, { measurementConfig, browser: baseline.fingerprint.browser,
    proxySha256: baseline.fingerprint.proxySha256, proxyImage: baseline.fingerprint.images.proxy })
  const comparison = Object.fromEntries(Object.entries(baseline.summaries).map(([key, summary]) =>
    [key, compareSummaries(summary, candidate.summaries[key])]))
  const coldLogin = Object.entries(comparison).filter(([key]) => key.startsWith('login/') && key.includes('/cold/'))
  await save(`${directory}/comparison.json`, { comparison,
    jsTargetMet: coldLogin.every(([, value]) => value.jsTargetMet),
    readyRegressionPassed: Object.values(comparison).every(value => value.readyRegressionPassed),
    sampleCounts: { baseline: baseline.runs.length, candidate: candidate.runs.length, groups: 40, perGroup: 7 },
    failureReasons: { baseline: summarizeRuns(baseline.runs).failureReasons, candidate: summarizeRuns(candidate.runs).failureReasons } })
  process.exit(coldLogin.every(([, value]) => value.jsTargetMet)
    && Object.values(comparison).every(value => value.readyRegressionPassed) ? 0 : 1)
}

const config = await readJSON(process.argv[2])
const parsed = new URL(config.baseURL)
if (parsed.protocol !== 'http:' || parsed.hostname !== '127.0.0.1' || !parsed.port) throw new Error('loopback required')
const credentials = await readJSON(config.credentialsFile)
const paths = { login: '/login', housing: '/housing', personnel: '/people' }
const links = { housing: '房态管理', personnel: '人员中心' }
const runs = []
const collectorErrors = []
const browser = await chromium.launch({ headless: loadingConfig.use.headless })
const browserFingerprint = await browserIdentity(browser)
const collectorSha256 = createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex')
const playwrightConfigSha256 = createHash('sha256').update(await readFile(new URL('../playwright.loading.config.ts', import.meta.url))).digest('hex')

function groupKey(run) { return [run.scenario, run.viewport, run.cacheMode, run.networkProfile].join('/') }
async function persist() {
  const groups = Object.groupBy(runs, groupKey)
  await save(config.output, { schemaVersion: 1, fingerprint: { ...config.fingerprint,
    browser: browserFingerprint, collectorSha256, playwrightConfigSha256,
    measurementConfigSha256: createHash('sha256').update(JSON.stringify(measurementConfig)).digest('hex') },
  measurementConfig, runs, collectorErrors,
  summaries: Object.fromEntries(Object.entries(groups).map(([key, group]) => [key, summarizeRuns(group)])) })
  await save(`${config.output}.progress.json`, { sampled: runs.length, expected: 280,
    valid: runs.filter(run => run.ok).length, failed: runs.filter(run => !run.ok).length,
    completedGroups: Object.entries(groups).filter(([, group]) => group.length === 7).map(([key]) => key),
    failedGroups: Object.entries(groups).filter(([, group]) => group.some(run => !run.ok)).map(([key]) => key),
    collectorErrors })
}

async function newContext(viewport) {
  const context = await browser.newContext({ viewport, serviceWorkers: 'block', baseURL: config.baseURL })
  // Unlike routing, an init script does not disable the HTTP cache.
  await context.addInitScript({ content: `(${initializeLoadingObservers.toString()})(window, globalThis.PerformanceObserver)` })
  return context
}

async function authenticate(context) {
  const response = await context.request.post('/api/v1/auth/login', { data: credentials })
  if (response.status() !== 200) throw new Error('login_failed')
}

async function ready(page, scenario) {
  if (scenario === 'login') {
    await expect(page.getByRole('heading', { name: '登录工作台', exact: true })).toBeVisible()
    await expect(page.getByLabel('手机号或邮箱', { exact: true })).toBeEditable()
    await expect(page.getByLabel('密码', { exact: true })).toBeEditable()
    await expect(page.getByRole('button', { name: '登录', exact: true })).toBeEnabled()
  } else if (scenario === 'housing') {
    await expect(page.getByRole('heading', { name: '住宿房态', exact: true })).toBeVisible()
    await expect(page.locator('.housingRoomCard').first()).toBeVisible()
    if (page.viewportSize().width < 720) {
      await page.getByRole('button', { name: '打开筛选', exact: true }).click()
    }
    await expect(page.locator('.housingFilters').getByLabel('搜索房间', { exact: true })).toBeEditable()
    await expect(page.locator('.housingFilters').getByRole('button', { name: '应用筛选', exact: true })).toBeEnabled()
  } else {
    await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
    await expect(page.getByRole('table', { name: '人员名单', exact: true })).toBeVisible()
    await expect(page.getByLabel('姓名或昵称', { exact: true })).toBeEditable()
    await expect(page.getByRole('button', { name: '查询', exact: true })).toBeEnabled()
    await expect(page.getByText('正在更新…', { exact: true })).toHaveCount(0)
  }
}

async function navigateSPA(page, destination) {
  // Application links preserve the router/query cache. No History API spoofing.
  const closeFilter = page.getByRole('button', { name: '关闭筛选', exact: true })
  if (await closeFilter.isVisible()) await closeFilter.click()
  const visibleLink = page.getByRole('link', { name: destination, exact: true }).filter({ visible: true }).first()
  if (!await visibleLink.isVisible() && page.viewportSize().width < 720) {
    await page.getByRole('button', { name: '更多', exact: true }).click()
  }
  await page.getByRole('link', { name: destination, exact: true }).filter({ visible: true }).first().click()
  // Parent navigation items expand children and intentionally keep the mobile
  // More sheet open; complete the normal close-button interaction before ready.
  const closeMore = page.getByRole('button', { name: '关闭更多导航', exact: true })
  if (await closeMore.isVisible()) await closeMore.click()
}

async function measure(page, cdp, scenario, viewport, cacheMode, networkProfile, spa = false) {
  const errors = new Set()
  let gzipScripts = 0
  let scriptResponses = 0
  const response = response => {
    const url = new URL(response.url())
    if (url.origin !== config.baseURL) return
    const expected401 = scenario === 'login' && url.pathname === '/api/v1/auth/me' && response.status() === 401
    if (response.status() >= 400 && !expected401) errors.add('http_error')
    if (url.pathname.endsWith('.js')) {
      scriptResponses++
      if (response.headers()['content-encoding'] === 'gzip') gzipScripts++
    }
  }
  const pageError = () => errors.add('page_error')
  const requestError = request => { if (request.failure()?.errorText !== 'net::ERR_ABORTED') errors.add('network_error') }
  page.on('response', response)
  page.on('pageerror', pageError)
  page.on('requestfailed', requestError)
  const run = { scenario, viewport, cacheMode, networkProfile, ok: false, jsEncodedBytes: null,
    cssEncodedBytes: null, requestCount: null, readyMs: null, fcpMs: null, lcpMs: null,
    scriptMs: null, longTaskCount: null, errors: [] }
  const before = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(metric => [metric.name, metric.value]))
  const start = spa ? await page.evaluate(() => performance.now()) : 0
  try {
    if (spa) await navigateSPA(page, links[scenario])
    else await page.goto(paths[scenario], { waitUntil: 'domcontentloaded', timeout: 60000 })
    await ready(page, scenario)
    run.readyMs = await page.evaluate(start => performance.now() - start, start)
    await page.waitForTimeout(2000)
    const collected = await page.evaluate(({ start, spa }) => {
      const end = performance.now()
      const entries = [...performance.getEntriesByType('resource'), ...(!spa ? performance.getEntriesByType('navigation') : [])]
        .filter(entry => entry.startTime >= start && entry.startTime <= end && new URL(entry.name).origin === location.origin)
      const resources = entries.map(entry => ({
        kind: new URL(entry.name).pathname.endsWith('.js') ? 'script'
          : new URL(entry.name).pathname.endsWith('.css') ? 'css' : 'other',
        encodedBodySize: entry.encodedBodySize,
        decodedBodySize: entry.decodedBodySize,
        cacheHit: entry.transferSize === 0 && entry.decodedBodySize > 0,
      }))
      const lcp = window.__loading?.lcp ?? []
      const tasks = window.__loading?.longTasks
      return { resources, fcpMs: spa ? null : performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? null,
        lcpMs: spa ? null : lcp.at(-1)?.startTime ?? null,
        longTaskCount: tasks ? tasks.filter(entry => entry.startTime >= start && entry.startTime <= end).length : null }
    }, { start, spa })
    const after = Object.fromEntries((await cdp.send('Performance.getMetrics')).metrics.map(metric => [metric.name, metric.value]))
    Object.assign(run, { jsEncodedBytes: sumInitialScriptBytes(collected.resources),
      cssEncodedBytes: collected.resources.filter(resource => resource.kind === 'css' && !resource.cacheHit)
        .reduce((total, resource) => total + resource.encodedBodySize, 0),
      requestCount: collected.resources.length, fcpMs: collected.fcpMs, lcpMs: collected.lcpMs,
      scriptMs: Number.isFinite(after.ScriptDuration) ? Math.max(0, after.ScriptDuration - (spa ? before.ScriptDuration : 0)) * 1000 : null,
      longTaskCount: collected.longTaskCount, resources: collected.resources,
      compression: { scriptResponses, gzipScripts, compressedBodies: collected.resources.filter(resource =>
        resource.kind === 'script' && resource.decodedBodySize > resource.encodedBodySize).length } })
    if (!spa && cacheMode === 'cold' && (!gzipScripts || !run.compression.compressedBodies)) errors.add('compression_missing')
  } catch (error) {
    errors.add(error.name === 'TimeoutError' || error.name === 'AssertionError'
      || error.matcherResult || error.message?.includes('Timed out') ? 'ready_timeout' : 'measurement_error')
  } finally {
    page.off('response', response)
    page.off('pageerror', pageError)
    page.off('requestfailed', requestError)
  }
  run.errors = [...errors].sort()
  run.ok = errors.size === 0 && run.readyMs !== null
  runs.push(run)
  await persist()
  console.log(`Loading sample ${runs.length}/280 ${groupKey(run)} ${run.ok ? 'valid' : run.errors.join(',')}`)
  return run
}

try {
  // Unthrottled selector smoke is separate evidence, never a matrix sample.
  // Verify the current production UI before spending time on 280 timed runs.
  const smoke = []
  for (const [viewportName, viewport] of Object.entries(viewports)) {
    for (const scenario of Object.keys(paths)) {
      const context = await newContext(viewport)
      try {
        if (scenario !== 'login') await authenticate(context)
        const page = await context.newPage()
        await page.goto(paths[scenario], { waitUntil: 'domcontentloaded' })
        await ready(page, scenario)
        smoke.push({ scenario, viewport: viewportName, navigation: 'document', ok: true })
      } catch {
        smoke.push({ scenario, viewport: viewportName, navigation: 'document', ok: false, errors: ['ready_timeout'] })
        throw new Error('measurement_error')
      } finally {
        await save(`${config.output}.smoke.json`, { smoke, performanceSamples: false })
        await context.close()
      }
    }
    for (const scenario of ['housing', 'personnel']) {
      const context = await newContext(viewport)
      try {
        await authenticate(context)
        const page = await context.newPage()
        await page.goto('/', { waitUntil: 'domcontentloaded' })
        await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
        for (const navigation of ['module-first', 'module-again']) {
          await navigateSPA(page, links[scenario])
          await ready(page, scenario)
          smoke.push({ scenario, viewport: viewportName, navigation, ok: true })
          await navigateSPA(page, '工作台')
          await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
        }
      } catch {
        smoke.push({ scenario, viewport: viewportName, navigation: 'module', ok: false, errors: ['ready_timeout'] })
        throw new Error('measurement_error')
      } finally {
        await save(`${config.output}.smoke.json`, { smoke, performanceSamples: false })
        await context.close()
      }
    }
  }
  console.log('Loading smoke: 14 document/SPA selector checks passed; begin timed matrix')
  for (const [viewportName, viewport] of Object.entries(viewports)) {
    for (const [profile, network] of Object.entries(profiles)) {
      for (let sample = 0; sample < 7; sample++) {
        for (const scenario of Object.keys(paths)) {
          const context = await newContext(viewport)
          try {
            if (scenario !== 'login') await authenticate(context)
            const page = await context.newPage()
            page.setDefaultTimeout(60000)
            const cdp = await context.newCDPSession(page)
            await cdp.send('Network.enable')
            await cdp.send('Performance.enable')
            await cdp.send('Network.emulateNetworkConditions', network)
            await cdp.send('Emulation.setCPUThrottlingRate', { rate: profile === 'throttled' ? 4 : 1 })
            await cdp.send('Network.setCacheDisabled', { cacheDisabled: true })
            await measure(page, cdp, scenario, viewportName, 'cold', profile)
            await cdp.send('Network.setCacheDisabled', { cacheDisabled: false })
            // Populate the cache without adding a measured sample, then retain
            // this same context/page for the warm document navigation.
            await page.reload({ waitUntil: 'domcontentloaded' })
            await ready(page, scenario)
            await page.waitForTimeout(2000)
            await measure(page, cdp, scenario, viewportName, 'warm', profile)
          } finally { await context.close() }
        }
        for (const scenario of ['housing', 'personnel']) {
          const context = await newContext(viewport)
          try {
            await authenticate(context)
            const page = await context.newPage()
            page.setDefaultTimeout(60000)
            const cdp = await context.newCDPSession(page)
            await cdp.send('Network.enable')
            await cdp.send('Performance.enable')
            await cdp.send('Network.emulateNetworkConditions', network)
            await cdp.send('Emulation.setCPUThrottlingRate', { rate: profile === 'throttled' ? 4 : 1 })
            await page.goto('/', { waitUntil: 'domcontentloaded' })
            await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
            await page.waitForTimeout(2000)
            await measure(page, cdp, scenario, viewportName, 'module-first', profile, true)
            await navigateSPA(page, '工作台')
            await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
            await measure(page, cdp, scenario, viewportName, 'module-again', profile, true)
          } finally { await context.close() }
        }
      }
    }
  }
} catch (error) {
  collectorErrors.push(error.message === 'login_failed' ? 'login_failed' : 'measurement_error')
  process.exitCode = 1
} finally {
  await persist()
  await browser.close()
}
const summaries = Object.groupBy(runs, groupKey)
if (Object.keys(summaries).length !== 40 || Object.values(summaries).some(group => group.filter(run => run.ok).length !== 7)) {
  process.exitCode = 1
}
