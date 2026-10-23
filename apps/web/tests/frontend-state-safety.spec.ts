import { createHash } from 'node:crypto'
import { existsSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import path from 'node:path'
import { test, expect, type Browser, type BrowserContext, type Page, type TestInfo, type Route } from '@playwright/test'
import { makeCnId, refocus, logout } from './helpers'
import { completeHeldCorrection } from './helpers/correctionCompletion.mjs'

type View = { name: string; width: number; height: number; touch: boolean }
const views: View[] = [{ name: 'desktop', width: 1440, height: 900, touch: false }, { name: 'touch', width: 390, height: 844, touch: true }]
const payload = (info: TestInfo) => info.config.metadata.stateSafety
const proof = (info: TestInfo, value: object) => info.annotations.push({ type: 'state-evidence', description: JSON.stringify(value) })
async function context(browser: Browser, info: TestInfo, view: View) {
  return browser.newContext({ baseURL: payload(info).baseURL, viewport: { width: view.width, height: view.height },
    isMobile: view.touch, hasTouch: view.touch, serviceWorkers: 'block' })
}
async function login(ctx: BrowserContext, file: string) {
  expect((await ctx.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(file, 'utf8')) })).status()).toBe(200)
  return (await (await ctx.request.get('/api/v1/auth/csrf')).json()).csrf_token as string
}
async function seedPerson(ctx: BrowserContext) {
  const response = await ctx.request.get('/api/v1/people', { params: { query: 'E2E协助种子1', page_size: 100 } })
  expect(response.status()).toBe(200)
  const person = (await response.json()).items.find((row: { full_name: string }) => row.full_name === 'E2E协助种子1')
  expect(person).toBeTruthy()
  return person
}
async function privateCode(info: TestInfo, sequence: number, destination: string, challengeId: string | null, purpose = 'registration') {
  const directory = payload(info).bridgeDir
  const request = path.join(directory, 'otp-request.json')
  const response = path.join(directory, 'otp-response.json')
  expect(existsSync(request) || existsSync(response)).toBe(false)
  const destinationAlias = 'dst_' + createHash('sha256').update('registration-test-destination\0' + destination).digest('hex').slice(0, 24)
  writeFileSync(request + '.tmp', JSON.stringify({ sequence, purpose, challengeId, destinationAlias }), { mode: 0o600 })
  renameSync(request + '.tmp', request)
  await expect.poll(() => existsSync(response), { timeout: 15000 }).toBe(true)
  const value = JSON.parse(readFileSync(response, 'utf8'))
  expect(value.sequence).toBe(sequence)
  expect(value.code).toMatch(/^\d{6}$/)
  unlinkSync(response)
  return value.code as string
}
async function filters(page: Page, view: View) {
  if (view.touch) await page.getByRole('button', { name: '打开筛选', exact: true }).click()
  return page.locator('.housingFilters')
}

for (const [index, view] of views.entries()) {
  test(`${view.name}: actual housing focus retains filter/page and real revocation purges rooms`, async ({ browser }, info) => {
    const admin = await context(browser, info, view)
    const user = await context(browser, info, view)
    let grant: string | undefined
    const csrf = await login(admin, payload(info).credentialsFile)
    const endpoint = '/api/v1/accounts/e1100000-0000-4000-8000-000000000002/permission-overrides'
    try {
      const response = await admin.request.post(endpoint, { headers: { 'X-CSRF-Token': csrf }, data: {
        permission_id: '20000000-0000-4000-8000-000000000037', effect: 'allow', scope_kind: 'global', grant_reason: 'Synthetic loading focus acceptance',
      } })
      expect(response.status()).toBe(201)
      grant = (await response.json()).id
      await login(user, payload(info).restrictedCredentialsFile)
      const page = await user.newPage()
      await page.goto('/housing')
      await expect(page.getByRole('heading', { name: '住宿房态', exact: true })).toBeVisible()
      const form = await filters(page, view)
      await form.getByLabel('搜索房间').fill('4-')
      await form.getByRole('button', { name: '应用筛选', exact: true }).click()
      const pagination = page.getByRole('navigation', { name: '房态分页' })
      await pagination.getByRole('button', { name: '下一页', exact: true }).click()
      await expect(pagination).toContainText('第 2 /')
      const before = await page.locator('.housingRoomCard').count()
      expect(before).toBeGreaterThan(0)
      await refocus(page)
      await expect(pagination).toContainText('第 2 /')
      await expect(page.locator('.housingRoomCard')).toHaveCount(before)
      await expect((await filters(page, view)).getByLabel('搜索房间')).toHaveValue('4-')
      expect((await admin.request.delete(endpoint + '/' + grant, { headers: { 'X-CSRF-Token': csrf } })).status()).toBe(204)
      grant = undefined
      await refocus(page)
      await expect(page.locator('.housingRoomCard')).toHaveCount(0)
      expect((await user.request.get('/api/v1/housing/overview')).status()).toBe(403)
      proof(info, { realPermissionGrantAndRevocation: true, filterAndSecondPagePreserved: true, focus: 'existing-headless-refocus-helper' })
    } finally {
      if (grant) expect((await admin.request.delete(endpoint + '/' + grant, { headers: { 'X-CSRF-Token': csrf } })).status()).toBe(204)
      await user.close(); await admin.close()
    }
  })

  test(`${view.name}: delayed genuine correction import cannot publish after logout or account replacement`, async ({ browser }, info) => {
    for (const replace of [false, true]) {
      const ctx = await context(browser, info, view)
      try {
        await login(ctx, payload(info).credentialsFile)
        const originalAccount = (await (await ctx.request.get('/api/v1/auth/me')).json()).account_id
        const person = await seedPerson(ctx)
        const page = await ctx.newPage()
        let held: Route | undefined
        await page.route('**/assets/CorrectionDraft-*.js', route => { held = route })
        await page.goto('/people/' + person.id)
        await page.getByRole('button', { name: '更正本人及监护资料', exact: true }).click()
        await expect.poll(() => Boolean(held)).toBe(true)
        await logout(page)
        if (replace) {
          const actor = JSON.parse(readFileSync(payload(info).restrictedCredentialsFile, 'utf8'))
          await page.getByLabel('手机号或邮箱', { exact: true }).fill(actor.identifier)
          await page.getByLabel('密码', { exact: true }).fill(actor.password)
          await page.getByRole('button', { name: '登录', exact: true }).click()
          await expect(page.getByRole('heading', { name: '工作台', exact: true })).toBeVisible()
        }
        const completion = await completeHeldCorrection(page, held!)
        expect(completion).toEqual({ moduleEvaluated: true, responseFinished: true })
        await expect(page.getByLabel('资料更正原因', { exact: true })).toHaveCount(0)
        await expect(page.getByRole('button', { name: '提交更正', exact: true })).toHaveCount(0)
        const me = await ctx.request.get('/api/v1/auth/me')
        if (replace) {
          expect(me.status()).toBe(200)
          const actor = await me.json()
          expect(actor.account_id).toBe('e1100000-0000-4000-8000-000000000002')
          expect(actor.account_id).not.toBe(originalAccount)
          expect(actor.is_super_admin).toBe(false)
        }
        else expect(me.status()).toBe(401)
      } finally { await ctx.close() }
    }
    proof(info, { delayedRealImport: true, moduleEvaluationWitness: true, logoutAndReplacementNoStaleEditor: true })
  })

  test(`${view.name}: unsaved intake survives focus and an independent local report failure stays editable`, async ({ browser }, info) => {
    const ctx = await context(browser, info, view)
    try {
      await login(ctx, payload(info).credentialsFile)
      const page = await ctx.newPage()
      let submits = 0
      page.on('request', request => { if (request.method() === 'POST' && /personnel-intake/.test(new URL(request.url()).pathname)) submits++ })
      await page.goto('/people/assisted-create')
      const name = page.getByRole('region', { name: '本人资料', exact: true }).getByLabel('姓名', { exact: true })
      await name.fill('Synthetic unsaved intake')
      const node = await name.elementHandle()
      await refocus(page)
      await expect(name).toHaveValue('Synthetic unsaved intake')
      expect(await node!.evaluate(element => element.isConnected)).toBe(true)
      // A separate genuine PersonDetail sibling failure; intake is never submitted.
      const person = await seedPerson(ctx)
      await page.route('**/assets/reports-*.css', route => route.abort('failed'))
      await page.goto('/people/' + person.id)
      await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toBeVisible()
      await page.getByRole('button', { name: '更正本人及监护资料', exact: true }).click()
      const reason = page.getByRole('region', { name: '协助档案资料更正', exact: true }).locator('textarea')
      await reason.fill('Synthetic editable correction')
      await refocus(page)
      await expect(reason).toHaveValue('Synthetic editable correction')
      await reason.fill('Synthetic still editable')
      await expect(reason).toHaveValue('Synthetic still editable')
      expect(submits).toBe(0)
      proof(info, { intakeNodePreservedOnFocus: true, localCssFailureIndependentEditorUsable: true, submissions: submits })
    } finally { await ctx.close() }
  })

  test(`${view.name}: real private email registration preserves OTP/profile drafts and creates exactly one account`, async ({ browser }, info) => {
    const ctx = await context(browser, info, view)
    const admin = await context(browser, info, view)
    const email = index === 0 ? 'registration-adult-e2e@example.com' : 'registration-loading-touch@example.com'
    const name = index === 0 ? 'E2E邮箱自助注册学员' : 'E2E自助注册成年学员'
    const password = 'E2EMail26Aa'
    try {
      await login(admin, payload(info).credentialsFile)
      const people = async () => (await (await admin.request.get('/api/v1/people', { params: { query: name, page_size: 100 } })).json()).items.filter((row: { full_name: string }) => row.full_name === name)
      expect(await people()).toHaveLength(0)
      await ctx.setExtraHTTPHeaders({ 'X-Forwarded-For': '203.0.113.1', Forwarded: 'for=203.0.113.1', 'X-Real-IP': '203.0.113.1' })
      const page = await ctx.newPage()
      let finalized = 0
      page.on('request', request => { if (request.method() === 'POST' && new URL(request.url()).pathname.endsWith('/registration/me/finalize')) finalized++ })
      await page.goto('/register')
      await page.getByRole('radio', { name: /^学员/ }).click()
      await page.getByRole('radio', { name: '邮箱', exact: true }).click()
      await page.locator('#registration-identifier').fill(email)
      await refocus(page)
      await expect(page.locator('#registration-identifier')).toHaveValue(email)
      const challengeResponse = page.waitForResponse(response => response.request().method() === 'POST' && /\/registration\/applications\/[^/]+\/challenge$/.test(new URL(response.url()).pathname))
      await page.getByRole('button', { name: '发送验证码', exact: true }).click()
      const challenge = await challengeResponse
      expect(challenge.status()).toBe(201)
      const code = await privateCode(info, index + 1, email, (await challenge.json()).challenge_id)
      const otp = page.getByLabel('验证码', { exact: true })
      await otp.fill(code)
      const otpNode = await otp.elementHandle()
      await refocus(page)
      expect(await otpNode!.evaluate(element => element.isConnected)).toBe(true)
      await expect(otp).toHaveValue(code)
      await page.getByRole('button', { name: '验证并继续', exact: true }).click()
      await expect(page.getByRole('heading', { name: '填写实名资料', exact: true })).toBeVisible()
      await page.getByLabel('姓名', { exact: true }).fill(name)
      await page.getByLabel('身份证', { exact: true }).fill(index === 0 ? makeCnId('20020102', 222) : makeCnId('20010102', 221))
      await page.getByLabel('密码', { exact: true }).fill(password)
      await page.getByLabel('确认密码', { exact: true }).fill(password)
      await refocus(page)
      await expect(page.getByLabel('姓名', { exact: true })).toHaveValue(name)
      await expect(page.getByLabel('密码', { exact: true })).toHaveValue(password)
      await page.getByLabel(/我已阅读并同意实名资料处理条款/).check()
      const finalization = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith('/registration/me/finalize'))
      await page.getByRole('button', { name: '提交实名资料', exact: true }).click()
      expect((await finalization).status()).toBe(200)
      await expect(page.getByText('账号注册完成', { exact: true })).toBeVisible()
      expect(finalized).toBe(1)
      expect(await people()).toHaveLength(1)
      expect((await ctx.request.post('/api/v1/auth/login', { data: { identifier: email, password } })).status()).toBe(200)
      const me = await (await ctx.request.get('/api/v1/auth/me')).json()
      expect(me.is_super_admin).toBe(false)
      expect(me.person_id).toBe((await people())[0].id)
      expect((await ctx.request.get('/api/v1/accounts')).status()).toBe(403)
      proof(info, { firstCodeFromPrivateMailbox: true, headerSpoofDidNotSelectSmtp: true, otpAndProfileDraftPreserved: true, finalizationPosts: finalized, exactNewPeople: 1, actualLogin: true })
    } finally { await ctx.close(); await admin.close() }
  })
}

test('existing approved accountless fixture activates its original person once through private email', async ({ browser }, info) => {
  const admin = await context(browser, info, views[0])
  const ctx = await context(browser, info, views[1])
  try {
    await login(admin, payload(info).credentialsFile)
    const person = await seedPerson(admin)
    expect(person.account_status).toBe('not_activated')
    const page = await ctx.newPage()
    await page.goto('/activate-profile')
    await page.getByLabel('本人邮箱', { exact: true }).fill('assisted-seed-1@example.com')
    const start = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/auth/profile-activation/start')
    await page.getByRole('button', { name: '请求邮箱验证码', exact: true }).click()
    expect((await start).status()).toBe(200)
    const code = await privateCode(info, 3, 'assisted-seed-1@example.com', null, 'assisted_activation')
    await page.getByLabel('验证码', { exact: true }).fill(code)
    await refocus(page)
    await expect(page.getByLabel('验证码', { exact: true })).toHaveValue(code)
    await page.getByRole('button', { name: '验证邮箱', exact: true }).click()
    await page.getByLabel('新密码', { exact: true }).fill('E2EMail26Aa')
    await page.getByRole('button', { name: '完成激活', exact: true }).click()
    await expect(page).toHaveURL(/\/login$/)
    expect((await ctx.request.get('/api/v1/auth/me')).status()).toBe(401)
    expect((await ctx.request.post('/api/v1/auth/login', { data: { identifier: 'assisted-seed-1@example.com', password: 'E2EMail26Aa' } })).status()).toBe(200)
    expect((await (await ctx.request.get('/api/v1/auth/me')).json()).person_id).toBe(person.id)
    expect((await seedPerson(admin)).account_status).toBe('active')
    proof(info, { originalPersonActivated: true, noImplicitLogin: true, actualExplicitLogin: true })
  } finally { await ctx.close(); await admin.close() }
})
