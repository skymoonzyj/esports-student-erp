import { readFile } from 'node:fs/promises'
import { randomUUID } from 'node:crypto'
import { test, expect, type BrowserContext, type Page, type TestInfo, type Route } from '@playwright/test'

type Payload = { baseURL: string; credentialsFile: string; restrictedCredentialsFile: string }
function config(info: TestInfo): Payload { return info.config.metadata.moduleRecovery as Payload }
test.beforeEach(async ({ browserName }, info) => { test.skip(!info.config.metadata.moduleRecovery, `Owned named runner only (${browserName}); not a performance test`) })

async function login(context: BrowserContext, file: string) {
  const credentials = JSON.parse(await readFile(file, 'utf8'))
  const response = await context.request.post('/api/v1/auth/login', { data: credentials })
  expect(response.status()).toBe(200)
}

async function ready(page: Page, context: BrowserContext, info: TestInfo) {
  await login(context, config(info).credentialsFile)
  await page.addInitScript(() => {
    const state = { preloadErrors: 0, prevented: 0 }
    Object.assign(window, { __moduleFault: state })
    window.addEventListener('vite:preloadError', event => {
      state.preloadErrors++
      queueMicrotask(() => { if (event.defaultPrevented) state.prevented++ })
    })
  })
  await page.goto('/')
  await expect(page.getByRole('link', { name: '天赋评测 2', exact: true }).first()).toBeVisible()
}

function observe(page: Page) {
  const counts = { versionProbes: 0, logouts: 0, documents: 0, failedModuleRequests: 0 }
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path === '/build-info.json') counts.versionProbes++
    if (path === '/api/v1/auth/logout') counts.logouts++
    if (request.resourceType() === 'document') counts.documents++
  })
  page.on('requestfailed', request => {
    if (/\/assets\/AssessmentHome-[^/]+\.js$/.test(new URL(request.url()).pathname)) counts.failedModuleRequests++
  })
  return counts
}

function evidence(info: TestInfo, value: Record<string, unknown>) {
  info.annotations.push({ type: 'fault-evidence', description: JSON.stringify(value) })
}

async function assertNoRecovery(page: Page) {
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toHaveCount(0)
  await expect(page.getByText('系统已更新', { exact: true })).toHaveCount(0)
}

test('offline module import remains local; cached rejection never loops or reloads', async ({ page, context }, info) => {
  await ready(page, context, info)
  // Warm the now-lazy authorized parent/CSS without loading Home. Otherwise a
  // whole-document offline fault correctly hits nonretryable parent CSS first.
  await page.goto('/esports-assessments/history')
  await expect(page.getByRole('heading', { name: '评测记录', exact: true })).toBeVisible()
  expect(await page.evaluate(() => performance.getEntriesByType('resource').some(entry => /\/assets\/AssessmentHome-[^/]+\.js$/.test(entry.name)))).toBe(false)
  const counts = observe(page)
  await context.setOffline(true)
  await page.getByRole('link', { name: '天赋评测 2', exact: true }).first().click()
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toBeVisible()
  await expect(page.getByText('系统已更新', { exact: true })).toHaveCount(0)
  await expect.poll(() => counts.versionProbes).toBe(1)
  await context.setOffline(false)
  await page.getByRole('button', { name: '重试加载', exact: true }).click()
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toBeVisible()
  await expect.poll(() => counts.versionProbes).toBe(2)
  expect(counts.failedModuleRequests).toBeGreaterThanOrEqual(1)
  expect(counts.documents).toBe(0)
  expect(counts.logouts).toBe(0)
  await expect(page.getByRole('link', { name: '人员中心', exact: true }).first()).toBeVisible()
  const vite = await page.evaluate(() => (window as typeof window & { __moduleFault: { preloadErrors: number; prevented: number } }).__moduleFault)
  expect(vite.preloadErrors).toBeGreaterThan(0)
  expect(vite.prevented).toBe(0)
  evidence(info, { fault: 'real-browser-offline-production-import', ...counts, vite, localFailureUI: true, noUpdateClaim: true })
})

test('confirmed version difference offers manual refresh and cancellation preserves the page', async ({ page, context }, info) => {
  await ready(page, context, info)
  const counts = observe(page)
  await page.route('**/assets/AssessmentHome-*.js', route => route.abort('failed'))
  await page.route('**/build-info.json', route => route.fulfill({ json: { schema_version: 1, build_id: 'synthetic-next-release' } }))
  await page.getByRole('link', { name: '天赋评测 2', exact: true }).first().click()
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toBeVisible()
  await expect(page.getByText('系统已更新', { exact: true })).toBeVisible()
  const dialogPromise = page.waitForEvent('dialog')
  const click = page.getByRole('button', { name: '刷新页面', exact: true }).click()
  const dialog = await dialogPromise
  expect(dialog.message()).toBe('刷新会丢失尚未保存的内容，是否继续？')
  await dialog.dismiss()
  await click
  await expect(page.getByText('系统已更新', { exact: true })).toBeVisible()
  expect(counts.documents).toBe(0)
  expect(counts.logouts).toBe(0)
  expect(counts.versionProbes).toBe(1)
  expect(counts.failedModuleRequests).toBeGreaterThanOrEqual(1)
  evidence(info, { fault: 'production-import-abort-plus-synthetic-new-public-version', ...counts, refreshCancelled: true, updateClaimOnlyAfterProbe: true })
})

test('hung real version check reaches the native five-second timeout and leaves local recovery usable', async ({ page, context }, info) => {
  await ready(page, context, info)
  await page.goto('/esports-assessments/history')
  await expect(page.getByRole('heading', { name: '评测记录', exact: true })).toBeVisible()
  const counts = observe(page)
  let held: Route | undefined
  let started = 0
  await page.route('**/assets/AssessmentHome-*.js', route => route.abort('failed'))
  await page.route('**/build-info.json', route => { held = route; started = performance.now() })
  // Install the positive abort witness before initiating the original product fetch.
  // No clock manipulation and no route.abort until the browser has cancelled it.
  const timeout = page.waitForEvent('requestfailed', {
    predicate: request => request === held?.request(), timeout: 10000,
  })
  await page.getByRole('link', { name: '天赋评测 2', exact: true }).first().click()
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toBeVisible()
  const failed = await timeout
  const elapsed = performance.now() - started
  expect(failed.failure()?.errorText).toContain('ERR_ABORTED')
  expect(elapsed).toBeGreaterThanOrEqual(4900)
  expect(elapsed).toBeLessThan(10000)
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
  await expect(page.getByText('系统已更新', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试加载', exact: true })).toBeEnabled()
  expect(counts.versionProbes).toBe(1)
  expect(counts.documents).toBe(0)
  expect(counts.logouts).toBe(0)
  expect((await context.request.get('/api/v1/auth/me')).status()).toBe(200)
  await held!.abort('aborted')
  await page.unroute('**/build-info.json')
  const nextProbe = page.waitForResponse(response => new URL(response.url()).pathname === '/build-info.json')
  await page.getByRole('button', { name: '重试加载', exact: true }).click()
  const restored = await nextProbe
  expect(restored.status()).toBe(200)
  expect(await restored.finished()).toBeNull()
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toBeVisible()
  await expect(page.getByText('系统已更新', { exact: true })).toHaveCount(0)
  await page.getByRole('link', { name: '人员中心', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
  expect(counts.documents).toBe(0)
  expect(counts.logouts).toBe(0)
  evidence(info, { fault: 'real-five-second-version-fetch-timeout', elapsedMs: Math.round(elapsed),
    ...counts, actualBrowserAbort: true, noUpdateClaim: true, localRetryUsable: true, otherRouteUsable: true })
})

test('real history stylesheet preload failure cannot mount an unstyled page on retry', async ({ page, context }, info) => {
  await ready(page, context, info)
  const counts = observe(page)
  let blockedStylesheets = 0
  await page.route('**/assets/reports-*.css', route => { blockedStylesheets++; return route.abort('failed') })
  await page.getByRole('link', { name: '天赋评测 2', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '查看评测记录', exact: true }).click()
  await expect(page.getByText('页面模块加载失败，请检查网络后重试', { exact: true })).toBeVisible()
  await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '重试加载', exact: true })).toBeDisabled()
  await expect(page.getByRole('heading', { name: '评测记录', exact: true })).toHaveCount(0)
  expect(blockedStylesheets).toBe(1)
  await expect.poll(() => counts.versionProbes).toBe(1)
  expect(counts.documents).toBe(0)
  expect(counts.logouts).toBe(0)
  const vite = await page.evaluate(() => (window as typeof window & { __moduleFault: { preloadErrors: number; prevented: number } }).__moduleFault)
  expect(vite.preloadErrors).toBeGreaterThan(0)
  expect(vite.prevented).toBe(0)
  evidence(info, { fault: 'real-production-history-CSS-preload-abort', ...counts, blockedStylesheets, vite, unstyledChildMounted: false, localRetryDisabled: true })
})

test('mounted business UI retains real CSRF403 handling without module recovery', async ({ page, context }, info) => {
  await ready(page, context, info)
  const counts = observe(page)
  const replies: Array<{ status: number; code: string }> = []
  // Corrupt this exact mutation on BOTH the initial request and the client's
  // existing bounded CSRF retry. Never forward a valid retry that could write.
  await page.route('**/api/v1/esports-assessments/sessions', async route => {
    if (route.request().method() !== 'POST') return route.continue()
    await route.continue({ headers: { ...route.request().headers(), 'x-csrf-token': 'synthetic-intentionally-invalid' } })
  })
  page.on('response', async response => {
    if (new URL(response.url()).pathname === '/api/v1/esports-assessments/sessions' && response.request().method() === 'POST') {
      const body = await response.json()
      replies.push({ status: response.status(), code: body.code })
    }
  })
  await page.getByRole('link', { name: '天赋评测 2', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '端游 FPS 正式测试', exact: true }).click()
  await expect(page.getByText('创建失败，请手动重试；将保留原操作键。', { exact: true })).toBeVisible()
  await expect.poll(() => replies.length).toBe(2)
  expect(replies).toEqual([{ status: 403, code: 'csrf_invalid' }, { status: 403, code: 'csrf_invalid' }])
  await assertNoRecovery(page)
  expect(counts.versionProbes).toBe(0)
  expect(counts.documents).toBe(0)
  expect(counts.logouts).toBe(0)
  evidence(info, { fault: 'real-CSRF403-in-mounted-production-Home-not-revocation', replies, ...counts, originalBusinessUI: true, successfulMutations: 0 })
})

test('restricted actor keeps outer route guard and actual backend permission403', async ({ page, context }, info) => {
  await login(context, config(info).restrictedCredentialsFile)
  const counts = observe(page)
  await page.goto('/esports-assessments')
  await expect(page).toHaveURL(config(info).baseURL + '/')
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toHaveCount(0)
  // AuthProvider's session restore rotates the login CSRF token. Obtain the
  // current token in this same cookie context only after the guard has settled.
  const csrfResponse = await context.request.get('/api/v1/auth/csrf')
  expect(csrfResponse.status()).toBe(200)
  const csrf = (await csrfResponse.json()).csrf_token as string
  const response = await context.request.post('/api/v1/esports-assessments/sessions', {
    headers: { 'X-CSRF-Token': csrf }, data: { suite_id: 'pc-fps', operation_key: randomUUID() },
  })
  expect(response.status()).toBe(403)
  const problem = await response.json()
  expect(problem.code).toBe('esports_forbidden')
  await assertNoRecovery(page)
  expect(counts.versionProbes).toBe(0)
  expect(counts.logouts).toBe(0)
  evidence(info, { fault: 'restricted-guard-plus-direct-permission-check-not-mounted403', status: response.status(), code: problem.code, guardDenied: true, versionProbes: counts.versionProbes, logouts: counts.logouts })
})
