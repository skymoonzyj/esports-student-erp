import { readFileSync } from 'node:fs'
import { test, expect, type Page } from '@playwright/test'
import { observeReadyChains } from './helpers/readyChainJourney'

async function navigate(page: Page, path: string) {
  await page.locator(`a[href="${path}"]`).first().evaluate(element => (element as HTMLAnchorElement).click())
}

test.beforeEach(async ({ context }, info) => {
  const payload = info.config.metadata.routeLoading
  test.skip(!payload, 'Owned route-loading runner only; non-performance proof')
  const response = await context.request.post('/api/v1/auth/login', {
    data: JSON.parse(readFileSync(payload.credentialsFile, 'utf8')),
  })
  expect(response.status()).toBe(200)
  expect((await response.json()).user.is_super_admin).toBe(true)
})

test('failed shared report CSS blocks a distinct Practice consumer without an unstyled mount', async ({ page }, info) => {
  let blocked = 0
  let documents = 0
  await page.route('**/assets/reports-*.css', route => { blocked++; return route.abort('failed') })
  await page.goto('/esports-assessments')
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
  page.on('request', request => { if (request.resourceType() === 'document') documents++ })
  await page.getByRole('link', { name: '查看评测记录', exact: true }).click()
  await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('heading', { name: '天赋评测 2', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '端游 FPS 自由练习', exact: true }).click()
  await expect(page).toHaveURL(/\/esports-assessments\/practice\?suite=pc-fps$/)
  await expect(page.getByRole('heading', { name: '自由练习', exact: true }).or(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true }))).toBeVisible()
  info.annotations.push({ type: 'route-evidence', description: JSON.stringify({ stage: 'distinct-consumer-result', unstyledPracticeMounted: await page.getByRole('heading', { name: '自由练习', exact: true }).count() > 0, blocked, documents }) })
  await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '自由练习', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '重试加载', exact: true })).toBeDisabled()
  expect(blocked).toBe(1)
  expect(documents).toBe(0)
  await navigate(page, '/people')
  await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
  info.annotations.push({ type: 'route-evidence', description: JSON.stringify({ sharedCssFailure: true, distinctConsumerBlocked: true, blocked, documents }) })
})

test('optional report-panel CSS failure preserves the person parent and reveal preview', async ({ page }, info) => {
  let blocked = 0, mutations = 0
  await page.route('**/assets/reports-*.css', route => { blocked++; return route.abort('failed') })
  page.on('request', request => { if (new URL(request.url()).pathname.startsWith('/api/v1/') && !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutations++ })
  await page.goto('/people')
  await page.getByLabel('姓名或昵称').fill('')
  await page.locator('.peopleNameLink').first().click()
  await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toBeVisible()
  await expect(page.getByRole('heading', { name: '业务档案', exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '天赋评测 2 报告' })).toHaveCount(0)
  await page.getByRole('button', { name: '查看完整身份证', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(blocked).toBe(1)
  expect(mutations).toBe(0)
  info.annotations.push({ type: 'route-evidence', description: JSON.stringify({ nestedPanelFailure: true, parentUsable: true, blocked, mutations }) })
})

test('superadmin navigation preserves shell and auth while preparing only the authorized pair', async ({ page }, info) => {
  let authRestores = 0, documents = 0, peopleQueries = 0, privateBeforeNavigation = 0, buildProbes = 0
  let navigated = false
  page.on('request', request => {
    const path = new URL(request.url()).pathname
    if (path === '/api/v1/auth/me') authRestores++
    if (/^\/api\/v1\/people(?:\/counts)?$/.test(path)) peopleQueries++
    if (!navigated && /^\/api\/v1\/(housing\/(catalog|overview)|people(?:\/counts)?)$/.test(path)) privateBeforeNavigation++
    if (path === '/build-info.json') buildProbes++
    if (request.resourceType() === 'document') documents++
  })
  const authority = page.waitForResponse('**/api/v1/auth/me').then(async response => {
    expect(response.status()).toBe(200)
    const user = await response.json()
    expect(user.permissions).toContain('people.list')
    expect([...user.permissions, ...user.scoped_permissions]).toContain('housing.read')
  })
  const capability = page.waitForResponse('**/api/v1/portal/context').then(async response => {
    expect(response.status()).toBe(200)
    expect((await response.json()).housing_management_available).toBe(true)
  })
  let releaseCss!: () => void, cssRequested!: () => void
  const heldCss = new Promise<void>(resolve => { releaseCss = resolve })
  const cssStarted = new Promise<void>(resolve => { cssRequested = resolve })
  let cssContinuation: Promise<void> | undefined
  await page.route('**/assets/people-*.css', route => {
    cssRequested()
    cssContinuation = heldCss.then(() => route.continue())
    return cssContinuation
  })
  const pageResponse = page.waitForResponse(response => /\/assets\/PeoplePage-[^/]+\.js$/.test(new URL(response.url()).pathname))
  const housingPrepared = Promise.all([
    page.waitForResponse(response => /\/assets\/HousingOverviewPage-[^/]+\.js$/.test(new URL(response.url()).pathname)),
    page.waitForResponse(response => /\/assets\/housing-[^/]+\.css$/.test(new URL(response.url()).pathname)),
  ]).then(async responses => {
    for (const response of responses) { expect(response.status()).toBe(200); expect(await response.finished()).toBeNull() }
  })
  // Attach rejection handlers before any navigation/assertion can fail.
  const preparation = Promise.all([authority, capability, housingPrepared, pageResponse])
  void preparation.catch(() => undefined)
  let shell: Awaited<ReturnType<ReturnType<Page['locator']>['elementHandle']>>
  let firstMount: Promise<boolean>
  const accountErrorStyle = async () => {
    await page.getByRole('button', { name: '开通账号', exact: true }).click()
    // No person selected: existing local validation, never an account mutation.
    await page.getByRole('button', { name: '确认开通', exact: true }).click()
    const style = await page.getByRole('alert').evaluate(element => {
      const css = getComputedStyle(element)
      return [css.fontSize, css.padding, css.borderRadius, css.color, css.backgroundColor]
    })
    await page.getByRole('dialog').getByRole('button', { name: '取消', exact: true }).click()
    return style
  }
  let accountStyleBefore: string[]
  try {
    await page.goto('/settings/accounts', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { name: '账号管理', exact: true })).toBeVisible()
    shell = await page.locator('.appShell').elementHandle()
    await preparation
    expect(privateBeforeNavigation).toBe(0)
    expect(buildProbes).toBe(0)
    accountStyleBefore = await accountErrorStyle()
    await expect(page.locator('.peoplePage, .housingPage')).toHaveCount(0)
    await expect.poll(() => page.evaluate(() => [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')]
      .some(link => /\/assets\/housing-[^/]+\.css$/.test(new URL(link.href).pathname) && Boolean(link.sheet) && !link.disabled))).toBe(true)
  // A first-mount witness catches even a transient unstyled mount before release.
    firstMount = page.evaluate(() => new Promise<boolean>(resolve => {
    const observer = new MutationObserver(() => {
      if (!document.querySelector('.peoplePage')) return
      const css = [...document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"]')]
        .find(link => /\/assets\/people-[^/]+\.css$/.test(new URL(link.href).pathname))
      observer.disconnect()
      resolve(Boolean(css?.sheet && !css.disabled))
    })
    observer.observe(document.documentElement, { childList: true, subtree: true })
  })).then(value => value, () => false)
    navigated = true
    await navigate(page, '/people')
    await cssStarted
    const response = await pageResponse
    expect(response.status()).toBe(200)
    expect(await response.finished()).toBeNull()
    await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toHaveCount(0)
    expect(peopleQueries).toBe(0)
  } finally {
    releaseCss()
    try {
      // Removing an active interceptor may itself continue the request. Wait
      // for this callback first; if no request started, there is nothing to await.
      if (cssContinuation) await cssContinuation
    } finally {
      await page.unroute('**/assets/people-*.css')
    }
  }
  await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
  expect(await firstMount).toBe(true)
  expect(await shell!.evaluate(element => element === document.querySelector('.appShell'))).toBe(true)
  const costs = await page.evaluate(() => {
    const resources = performance.getEntriesByType('resource') as PerformanceResourceTiming[]
    const owner = resources.filter(r => /\/people-[^/]+\.js$/.test(r.name))
    const css = resources.find(r => /\/people-[^/]+\.css$/.test(r.name))!
    const entry = resources.find(r => /\/PeoplePage-[^/]+\.js$/.test(r.name))!
    return { ownerRequests: owner.length, ownerEncodedBytes: owner.reduce((sum, r) => sum + r.encodedBodySize, 0), cssDurationMs: css.duration, cssBeforePageGapMs: entry.startTime - css.responseEnd,
      cssPageOverlap: entry.startTime < css.responseEnd && css.startTime < entry.responseEnd,
      unrelatedLoaded: resources.some(r => /\/(?:ProcurementPage|AssessmentSessionPage|GameHost)-[^/]+\.js$/.test(r.name)) }
  })
  expect(costs.ownerRequests).toBe(1)
  expect(costs.ownerEncodedBytes).toBeGreaterThan(0)
  expect(costs.cssPageOverlap).toBe(true)
  expect(costs.unrelatedLoaded).toBe(false)
  await page.goBack()
  await expect(page.getByRole('heading', { name: '账号管理', exact: true })).toBeVisible()
  expect(await accountErrorStyle()).toEqual(accountStyleBefore)
  await page.goForward()
  await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
  expect(await shell!.evaluate(element => element === document.querySelector('.appShell'))).toBe(true)
  await page.locator('.peopleNameLink').first().click()
  await expect(page.getByRole('region', { name: '天赋评测 2 报告', exact: true })).toBeVisible()
  const panel = await page.getByRole('region', { name: '天赋评测 2 报告', exact: true }).elementHandle()
  await page.getByRole('button', { name: '查看完整身份证', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(await panel!.evaluate(element => element === document.querySelector('.assessmentReports'))).toBe(true)
  await page.locator('.revealDialog').getByRole('button', { name: '取消', exact: true }).click()
  await page.goBack()
  await expect(page.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
  await navigate(page, '/housing')
  await expect(page.getByRole('heading', { name: '住宿房态', exact: true })).toBeVisible()
  expect(await shell!.evaluate(element => element === document.querySelector('.appShell'))).toBe(true)
  expect(authRestores).toBe(1)
  expect(documents).toBe(1)
  info.annotations.push({ type: 'route-evidence', description: JSON.stringify({ actualSuperadmin: true, shellStable: true, nestedSuccessStable: true, cssHeldWithoutMountOrQuery: true, firstMountWithCss: true, authorizedPairPrepared: true, accountStylesStable: true, privateBeforeNavigation, buildProbes, authRestores, documents, ...costs }) })
})

for (const actor of ['restricted', 'student'] as const) test(`${actor} genuine allowed and denied routes retain current capabilities`, async ({ browser, page }, info) => {
  const payload = info.config.metadata.routeLoading
  const context = await browser.newContext({ baseURL: payload.baseURL, viewport: page.viewportSize()! })
  try {
    const response = await context.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(actor === 'restricted' ? payload.restrictedCredentialsFile : payload.studentCredentialsFile, 'utf8')) })
    expect(response.status()).toBe(200)
    const user = (await response.json()).user
    expect(user.is_super_admin).toBe(false)
    const rolePage = await context.newPage()
    const loaded: string[] = []
    rolePage.on('request', request => { if (request.resourceType() === 'script') loaded.push(new URL(request.url()).pathname) })
    if (actor === 'restricted') {
      expect(user.permissions).toContain('people.list')
      expect([...user.permissions, ...user.scoped_permissions]).not.toContain('esports_assessments.take')
      expect([...user.permissions, ...user.scoped_permissions]).not.toContain('esports_assessments.read')
      await rolePage.goto('/people')
      await expect(rolePage.getByRole('heading', { name: '人员中心', exact: true })).toBeVisible()
      await rolePage.goto('/esports-assessments')
      await expect(rolePage).toHaveURL(/\/$/)
      expect(loaded.some(file => /\/AssessmentHome-/.test(file))).toBe(false)
    } else {
      expect([...user.permissions, ...user.scoped_permissions]).not.toContain('accounts.read')
      const portal = await context.request.get('/api/v1/portal/context')
      expect(portal.status()).toBe(200)
      expect((await portal.json()).adult_guardian_invitations_available).toBe(true)
      await rolePage.goto('/me/guardian-invitations')
      await expect(rolePage.getByRole('heading', { name: '成年学员监护邀请', exact: true })).toBeVisible()
      await rolePage.goto('/settings/accounts')
      await expect(rolePage).toHaveURL(/\/$/)
      expect(loaded.some(file => /\/AccountsPage-/.test(file))).toBe(false)
    }
    info.annotations.push({ type: 'route-evidence', description: JSON.stringify({ fixtureActor: actor, actualSuperadmin: user.is_super_admin, allowedRoute: true, deniedRoute: true, deniedLoaderAbsent: true, verifiedCapability: actor === 'student' ? 'adult_guardian_invitations_available' : 'people.list' }) })
  } finally { await context.close() }
})

test('cold public entry does not prefetch shell or unrelated business pages', async ({ browser, page }, info) => {
  const payload = info.config.metadata.routeLoading
  const observed: { route: string; scripts: number }[] = []
  for (const [route, heading, own] of [['/login', '登录工作台', ''], ['/register', '创建账号', 'RegistrationStartPage'], ['/forgot-password', '找回密码', 'ForgotPasswordPage'], ['/activate-profile', '激活已有档案', 'ActivateProfilePage']]) {
    const context = await browser.newContext({ baseURL: payload.baseURL, viewport: page.viewportSize()! })
    try {
      const publicPage = await context.newPage(), scripts: string[] = []
      let finishRestore!: () => void
      if (route === '/register') {
        const held = new Promise<void>(resolve => { finishRestore = resolve })
        await publicPage.route('**/api/v1/auth/me', async request => {
          const response = await request.fetch()
          expect(response.status()).toBe(401)
          await held
          await request.fulfill({ response })
        })
      }
      publicPage.on('request', request => { if (request.resourceType() === 'script') scripts.push(new URL(request.url()).pathname) })
      await publicPage.goto(route)
      await expect(publicPage.getByRole('heading', { name: heading, exact: true })).toBeVisible()
      if (route === '/register') {
        await publicPage.getByRole('radio', { name: /学员.*创建学员/ }).check()
        await publicPage.getByPlaceholder('请输入邀请码', { exact: true }).fill('DRAFT42')
        const input = await publicPage.getByPlaceholder('请输入邀请码', { exact: true }).elementHandle()
        const restored = publicPage.waitForResponse('**/api/v1/auth/me')
        finishRestore(); await restored
        await publicPage.waitForLoadState('networkidle')
        await expect(publicPage.getByPlaceholder('请输入邀请码', { exact: true })).toHaveValue('DRAFT42')
        expect(await input!.evaluate(element => element.isConnected)).toBe(true)
      }
      expect(scripts.some(file => /\/(?:AppShell|PeoplePage|HousingOverviewPage|ProcurementPage|AssessmentHome|AssessmentSessionPage|GameHost)-/.test(file))).toBe(false)
      for (const entry of ['RegistrationStartPage', 'ForgotPasswordPage', 'ActivateProfilePage']) expect(scripts.some(file => file.includes(`/${entry}-`))).toBe(entry === own)
      observed.push({ route, scripts: scripts.length })
    } finally { await context.close() }
  }
  info.annotations.push({ type: 'route-evidence', description: JSON.stringify({ publicEntries: observed, delayedAnonymousRestoreDraftRetained: true, unrelatedPrefetch: false }) })
})

test('fixed passive ready-chain diagnosis preserves original document and SPA sequences', async ({ browser, page }, info) => {
  await observeReadyChains(browser, page.viewportSize()!, info)
})
