import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import { test, expect, type Page, type TestInfo, type Locator } from '@playwright/test'
import { observePeopleControlStyle } from './helpers/peopleControlStyle'

type Payload = { baseURL: string; credentialsFile: string; studentCredentialsFile: string }
const web = path.resolve(import.meta.dirname, '..')
const css = (file: string) => readFileSync(path.join(web, 'src', file), 'utf8')
const foundation = () => css('styles/tokens.css') + css('styles/global.css') +
  (existsSync(path.join(web, 'src/styles/shared-controls.css')) ? css('styles/shared-controls.css') : '')
test.beforeEach(async ({ context }, info) => {
  const payload = info.config.metadata.styleOrder as Payload | undefined
  test.skip(!payload, 'Owned named runner only; not a performance test')
  const response = await context.request.post('/api/v1/auth/login', {
    data: JSON.parse(readFileSync(payload!.credentialsFile, 'utf8')),
  })
  expect(response.status()).toBe(200)
})

function evidence(info: TestInfo, value: Record<string, unknown>) {
  info.annotations.push({ type: 'style-evidence', description: JSON.stringify(value) })
}
async function overflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1)
}
async function focused(control: Locator) {
  // Focus-visible is keyboard-modality dependent. A mouse click followed by
  // element.focus() is not evidence about keyboard accessibility.
  await control.page().keyboard.press('Tab')
  await control.focus()
  const style = await control.evaluate(element => {
    const s = getComputedStyle(element)
    return { width: parseFloat(s.outlineWidth), style: s.outlineStyle, shadow: s.boxShadow }
  })
  expect(style.width >= 2 && style.style !== 'none' || style.shadow !== 'none').toBe(true)
}
async function button(control: Locator, height: number) {
  await expect(control).toBeVisible()
  expect(await control.evaluate(element => parseFloat(getComputedStyle(element).minHeight))).toBe(height)
  await focused(control)
}
async function dialogLayout(page: Page) {
  const viewport = page.viewportSize()!
  const layout = await page.locator('.dialogBackdrop').evaluate(element => {
    const s = getComputedStyle(element)
    const rect = element.getBoundingClientRect()
    return { position: s.position, zIndex: s.zIndex, display: s.display,
      padding: s.paddingTop, background: s.backgroundColor, blur: s.backdropFilter,
      x: rect.x, y: rect.y, width: rect.width, height: rect.height }
  })
  expect(layout).toEqual({ position: 'fixed', zIndex: '100', display: 'grid',
    padding: '20px', background: 'rgba(14, 19, 33, 0.58)', blur: 'blur(5px)',
    x: 0, y: 0, width: viewport.width, height: viewport.height })
  const actions = page.locator('.settingsDialog .dialogActions')
  await expect(actions).toHaveCSS('display', viewport.width <= 767 ? 'grid' : 'flex')
  await expect(actions).toHaveCSS('gap', '9px')
  await expect(actions).toHaveCSS('margin-top', '12px')
  if (viewport.width <= 767) {
    expect(await actions.evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2)
    await expect(actions.locator('button').first()).toHaveCSS('flex-grow', '1')
  }
  return { backdrop: layout, actionsDisplay: await actions.evaluate(element => getComputedStyle(element).display) }
}
async function isolated(page: Page, selector: string, domain: string) {
  // Clone ONLY markup actually produced by the authorized production route.
  // No arbitrary classes/elements are manufactured to obtain a failing fixture.
  const html = await page.locator(selector).first().evaluate(element => element.outerHTML)
  const fixture = await page.context().newPage()
  await fixture.setContent(html)
  await fixture.addStyleTag({ content: foundation() + css(domain) })
  return fixture
}
async function people(page: Page) {
  await expect(page.getByRole('heading', { name: '新建人员', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '保存人员', exact: true }).click()
  await expect(page.locator('.fieldError').first()).toBeVisible()
}
async function peopleControls(page: Page, info: TestInfo) {
  const input = page.locator('#person-name')
  await expect(input).toHaveAttribute('aria-invalid', 'true')
  const { before, diagnostic } = await observePeopleControlStyle(input)
  evidence(info, { case: 'people-controls-before-comparison', ...diagnostic })
  expect(before).toEqual({ minHeight: '44px', border: 'rgb(180, 35, 61)', borderWidth: '1px' })
  await focused(input)
  expect(await input.evaluate(element => getComputedStyle(element).borderTopColor)).toBe('rgb(122, 115, 255)')
  expect(await page.locator('.fieldError').first().evaluate(element => getComputedStyle(element).fontSize)).toBe('11px')
  const columns = await page.locator('.formGrid').first().evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)
  expect(columns).toBe(page.viewportSize()!.width < 768 ? 1 : 2)
  await button(page.locator('.personFormActions .secondaryButton'), 44)
  await overflow(page)
  return before
}

test('deep links retain real shared controls with only their own domain CSS', async ({ page, browser }, info) => {
  let submittedMutations = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) submittedMutations++
  })
  await page.goto('/asset-scan/synthetic-style-check')
  await expect(page.getByRole('heading', { name: '资产信息', exact: true })).toBeVisible()
  const scan = await isolated(page, '.assetScanPage', 'assets/assets.css')
  const scanBefore = await scan.locator('.secondaryButton').evaluate(element => getComputedStyle(element).minHeight)
  evidence(info, { case: 'asset-scan-isolated', observedMinHeight: scanBefore, expectedMinHeight: 44 })
  await button(scan.locator('.secondaryButton'), 44)
  expect(await scan.locator('.pageEyebrow').evaluate(element => getComputedStyle(element).fontSize)).toBe('10px')
  await overflow(scan)
  await scan.close()

  await page.goto('/assets')
  await page.getByRole('button', { name: '新增资产', exact: true }).click()
  await page.getByLabel('资产名称', { exact: true }).fill(' ')
  await page.getByRole('button', { name: '保存资产', exact: true }).click()
  await expect(page.getByText('请填写资产名称', { exact: true })).toBeVisible()
  const assetError = await isolated(page, '.assetDialogBackdrop', 'assets/assets.css')
  expect(await assetError.locator('.formError').evaluate(element => {
    const s = getComputedStyle(element)
    return { fontSize: s.fontSize, color: s.color, margin: s.marginTop }
  })).toEqual({ fontSize: '13px', color: 'rgb(180, 35, 61)', margin: '4px' })
  await button(assetError.locator('.secondaryButton'), 44)
  await overflow(assetError)
  await assetError.close()

  const payload = info.config.metadata.styleOrder as Payload
  const student = await browser.newContext({ baseURL: payload.baseURL, viewport: page.viewportSize()! })
  try {
    const response = await student.request.post('/api/v1/auth/login', {
      data: JSON.parse(readFileSync(payload.studentCredentialsFile, 'utf8')),
    })
    expect(response.status()).toBe(200)
    const portal = await student.request.get('/api/v1/portal/context')
    expect(portal.status()).toBe(200)
    expect((await portal.json()).adult_guardian_invitations_available).toBe(true)
    const studentPage = await student.newPage()
    await studentPage.goto('/me/guardian-invitations')
    await expect(studentPage.getByRole('heading', { name: '成年学员监护邀请', exact: true })).toBeVisible()
    const registration = await isolated(studentPage, '.portalPage', 'registration/registration.css')
    await button(registration.locator('.secondaryButton'), 44)
    expect(await registration.locator('.pageEyebrow').evaluate(element => getComputedStyle(element).fontWeight)).toBe('800')
    await overflow(registration)
  } finally {
    await student.close()
  }

  await page.goto('/people/new')
  await people(page)
  const form = await isolated(page, '.personFormPage', 'people/people.css')
  const fields = await peopleControls(form, info)
  await form.close()

  // Keep the actual backend authorization check. Replace only an authorized
  // GET's rows with synthetic invoice data so the real dual-class dialog can
  // render without creating financial data or invoking any mutation.
  await page.route('**/api/v1/procurement?*', async route => {
    const response = await route.fetch()
    expect(response.status()).toBe(200)
    const body = await response.json()
    await route.fulfill({ response, json: { ...body, total: 1, items: [{
      id: 'synthetic-style-record', purchase_date: '2026-01-01', item_name: '合成样式样本',
      purchase_channel: 'offline', product_url: null, quantity: '1', unit_price_cents: 100,
      total_price_cents: 100, settlement_center_id: 'synthetic-center', settlement_center_name: '合成中心',
      uses_reserve_fund: false, is_reimbursed: false, is_settled: false, settled_on: null,
      has_invoice: true, note: null, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
      invoice: { id: 'synthetic-invoice', amount_cents: 100, invoice_date: '2026-01-01',
        invoice_number: null, original_filename: 'synthetic.pdf', mime_type: 'application/pdf', created_at: '2026-01-01T00:00:00Z' },
    }] } })
  })
  await page.goto('/procurement')
  await expect(page.getByRole('heading', { name: '日常采购', exact: true })).toBeVisible()
  await page.getByRole('button', { name: '移除合成样式样本的发票', exact: true }).click()
  const procurement = await isolated(page, '.procurementPage', 'procurement/procurement.css')
  await button(procurement.locator('.procurementDialog .secondaryButton'), 40)
  await button(procurement.locator('.primaryButton.dangerButton'), 40)
  // global primaryButton retains its 150ms transition; assert settled cascade,
  // not an interpolated color immediately after attaching the fixture CSS.
  await expect(procurement.locator('.primaryButton.dangerButton')).toHaveCSS('background-color', 'rgb(180, 35, 61)')
  expect(await procurement.locator('.primaryButton.dangerButton').evaluate(element => {
    const s = getComputedStyle(element)
    return { background: s.backgroundColor, padding: s.paddingLeft, fontWeight: s.fontWeight }
  })).toEqual({ background: 'rgb(180, 35, 61)', padding: '14px', fontWeight: '720' })
  await overflow(procurement)
  await procurement.close()

  await page.goto('/settings/accounts')
  await page.getByRole('button', { name: '删除账号', exact: true }).first().click()
  await expect(page.getByRole('heading', { name: '确认删除账号', exact: true })).toBeVisible()
  await dialogLayout(page)
  const danger = await isolated(page, '.dialogBackdrop', 'settings/settings.css')
  const settingsLayout = await dialogLayout(danger)
  // Preserve settings.css's existing max-width767 dialogActions override.
  const settingsDangerHeight = page.viewportSize()!.width <= 767 ? 44 : 36
  await button(danger.locator('.dangerButton'), settingsDangerHeight)
  expect(await danger.locator('.dangerButton').evaluate(element => getComputedStyle(element).backgroundColor)).toBe('rgb(255, 240, 242)')
  await danger.locator('.dangerButton').hover()
  expect(await danger.locator('.dangerButton').evaluate(element => getComputedStyle(element).borderTopColor)).toBe('rgb(180, 35, 61)')
  await overflow(danger)
  await danger.close()

  await page.goto('/register')
  await expect(page.getByRole('heading', { name: '创建账号', exact: true })).toBeVisible()
  const start = await isolated(page, '.registrationPage', 'registration/registration.css')
  await focused(start.locator('input').first())
  await overflow(start)
  await start.close()
  expect(submittedMutations).toBe(0)
  evidence(info, { case: 'isolated-real-route-matrix', viewport: page.viewportSize(), fields,
    secondaryMinHeight: 44, procurementMinHeight: 40, dangerMinHeight: settingsDangerHeight,
    procurementDualClassMinHeight: 40, errorFontSize: 13, horizontalOverflow: false,
    syntheticAuthorizedProcurementGET: true, submittedMutations, settingsLayout })
})

test('settings CSS cannot change the real people reveal dialog action layout', async ({ page }, info) => {
  let mutationRequests = 0
  page.on('request', request => {
    if (new URL(request.url()).pathname.startsWith('/api/v1/') &&
      !['GET', 'HEAD', 'OPTIONS'].includes(request.method())) mutationRequests++
  })
  await page.goto('/people')
  await page.locator('.peopleNameLink').first().click()
  await page.getByRole('button', { name: '查看完整身份证', exact: true }).click()
  const actualDisplay = await page.locator('.revealDialog .dialogActions').evaluate(element => getComputedStyle(element).display)
  const direct = await isolated(page, '.dialogBackdrop', 'people/people.css')
  const actions = direct.locator('.revealDialog .dialogActions')
  await expect(actions).toHaveCSS('display', 'flex')
  await direct.addStyleTag({ content: css('settings/settings.css') })
  const afterSettingsDisplay = await actions.evaluate(element => getComputedStyle(element).display)
  await page.goto('/settings/accounts')
  await expect(page.getByRole('heading', { name: '账号管理', exact: true })).toBeVisible()
  await page.locator('a[href="/people"]').first().evaluate(element => (element as HTMLAnchorElement).click())
  await page.locator('.peopleNameLink').first().click()
  await page.getByRole('button', { name: '查看完整身份证', exact: true }).click()
  const afterSettingsNavigationDisplay = await page.locator('.revealDialog .dialogActions').evaluate(element => getComputedStyle(element).display)
  expect(mutationRequests).toBe(0)
  evidence(info, { case: 'settings-to-people-dialog-CSS-order', viewport: page.viewportSize(),
    actualDisplay, directPeopleDisplay: 'flex', afterSettingsDisplay, afterSettingsNavigationDisplay, mutationRequests })
  await expect(actions).toHaveCSS('display', 'flex')
  expect(actualDisplay).toBe('flex')
  expect(afterSettingsNavigationDisplay).toBe('flex')
  await overflow(page)
  await overflow(direct)
  await direct.close()
})

for (const sequence of [
  ['procurement', 'people', 'assets'],
  ['assets', 'people', 'procurement'],
] as const) {
  test(`domain styles remain stable in ${sequence.join(' -> ')} order`, async ({ page }, info) => {
    const observations: Record<string, unknown>[] = []
    for (const [index, domain] of sequence.entries()) {
      const route = `/${domain}`
      if (!index) await page.goto(route)
      else await page.locator(`a[href="${route}"]`).first().evaluate(element => (element as HTMLAnchorElement).click())
      if (domain === 'people') {
        await expect(page.locator('a[href="/people/new"]').first()).toBeAttached()
        await page.locator('a[href="/people/new"]').first().evaluate(element => (element as HTMLAnchorElement).click())
        await people(page)
        observations.push({ domain, ...await peopleControls(page, info) })
      } else {
        const root = page.locator(domain === 'assets' ? '.assetsPage' : '.procurementPage')
        await expect(root).toBeVisible()
        const control = root.locator('.secondaryButton').first()
        await button(control, domain === 'assets' ? 44 : 40)
        await overflow(page)
        observations.push({ domain, minHeight: await control.evaluate(element => getComputedStyle(element).minHeight) })
      }
    }
    evidence(info, { case: 'real-SPA-navigation-order', viewport: page.viewportSize(), observations, horizontalOverflow: false })
  })
}
