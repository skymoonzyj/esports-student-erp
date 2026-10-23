import { readFileSync } from 'node:fs'
import { test, expect, request, type APIRequestContext, type Page, type TestInfo } from '@playwright/test'
import { assertOwnRequestDelta } from '../scripts/optional-panel-checks.mjs'

const personnel = '/api/v1/personnel-intake'
const procurement = '/api/v1/procurement'
const payloadFor = (info: TestInfo) => info.config.metadata.optionalPanels
const evidence = (info: TestInfo, observations: Record<string, boolean | number>) =>
  info.annotations.push({ type: 'optional-evidence', description: JSON.stringify(observations) })
async function session(baseURL: string, credentialsFile: string) {
  const api = await request.newContext({ baseURL })
  const response = await api.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(credentialsFile, 'utf8')) })
  expect(response.status()).toBe(200)
  return { api, headers: { 'X-CSRF-Token': (await response.json()).csrf_token as string } }
}
async function get(api: APIRequestContext, url: string) {
  const response = await api.get(url)
  expect(response.status()).toBe(200)
  return response.json()
}
async function exactPurchase(api: APIRequestContext, name: string) {
  const list = await get(api, `${procurement}?query=${encodeURIComponent(name)}`)
  expect(list.total).toBe(1)
  expect(list.items).toHaveLength(1)
  expect(list.items[0].item_name).toBe(name)
  return list.items[0]
}
function identity(sequence: number) {
  const base = `11010519900102${sequence}`
  const weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
  return base + '10X98765432'[[...base].reduce((sum, digit, index) => sum + Number(digit) * weights[index], 0) % 11]
}
async function fillAdult(page: Page, name: string, sequence: number) {
  for (const [label, value] of [['姓名', name], ['身份证', identity(sequence)], ['出生日期', '1990-01-02'],
    ['手机号', `19900008${sequence}`], ['邮箱', `optional-${sequence}@example.com`]]) {
    await page.getByLabel(label, { exact: true }).fill(value)
  }
  await page.getByLabel('性别', { exact: true }).selectOption('male')
}

test.beforeEach(async ({ context }, info) => {
  const payload = payloadFor(info)
  test.skip(!payload, 'Owned optional-panels runner only')
  const response = await context.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(payload.credentialsFile, 'utf8')) })
  expect(response.status()).toBe(200)
  expect((await response.json()).user.is_super_admin).toBe(true)
})

test('distinct procurement consumers retain sticky real CSS failure and parent filter draft', async ({ page }, info) => {
  const payload = payloadFor(info)
  let blocked = 0, mutations = 0
  page.on('request', item => { if (new URL(item.url()).pathname.startsWith('/api/v1/') && !['GET', 'HEAD', 'OPTIONS'].includes(item.method())) mutations++ })
  await page.route('**/assets/procurement-dialogs-*.css', async route => {
    blocked++
    const missing = await route.fetch({ url: `${payload.baseURL}/assets/task10-missing-procurement.css` })
    expect(missing.status()).toBe(404)
    await route.fulfill({ response: missing })
  })
  await page.goto('/procurement')
  await expect(page.getByRole('heading', { name: '日常采购', exact: true })).toBeVisible()
  await page.getByLabel('搜索物品').fill('retained filter draft')
  await page.getByRole('button', { name: '新增采购', exact: true }).click()
  await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toHaveCount(1)
  await page.getByRole('button', { name: '上传发票', exact: true }).click()
  await expect(page.getByText('页面样式资源无法局部重试，请刷新页面。', { exact: true })).toHaveCount(2)
  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByLabel('搜索物品')).toHaveValue('retained filter draft')
  await page.getByLabel('搜索物品').fill('still usable')
  expect(blocked).toBe(1)
  expect(mutations).toBe(0)
  evidence(info, { realCss404: true, distinctConsumerBlocked: true, parentDraftPreserved: true, blocked, mutations })
})

test('four procurement dialogs retain real mutations and response-loss purchase idempotency', async ({ page }, info) => {
  const payload = payloadFor(info)
  const setup = await session(payload.baseURL, payload.credentialsFile)
  const observer = await session(payload.baseURL, payload.credentialsFile)
  try {
    const name = `Optional purchase ${info.project.name}`
    const centerResponse = await setup.api.post(`${procurement}/settlement-centers`, { headers: setup.headers, data: { name } })
    expect(centerResponse.status()).toBe(201)
    const center = await centerResponse.json()
    const scripts: string[] = []
    page.on('request', item => { if (item.resourceType() === 'script') scripts.push(item.url()) })
    await page.goto('/procurement')
    await expect(page.getByRole('heading', { name: '日常采购', exact: true })).toBeVisible()
    expect(scripts.some(url => /\/ProcurementDialogs-[^/]+\.js$/.test(url))).toBe(false)
    await page.getByRole('button', { name: '新增采购', exact: true }).click()
    const dialog = page.getByRole('dialog', { name: '录入日常采购', exact: true })
    await dialog.getByLabel('物品名称').fill(name)
    await dialog.getByLabel('单价（元）').fill('10')
    await dialog.getByLabel('归属结算中心').selectOption(center.id)
    expect(await dialog.locator('.procurementFormGrid').first().evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(info.project.name === 'desktop' ? 2 : 1)
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    const attempts: Record<string, unknown>[] = []
    let originalId = ''
    await page.route(`**${procurement}`, async route => {
      if (route.request().method() !== 'POST') return route.continue()
      attempts.push(route.request().postDataJSON())
      const response = await route.fetch()
      expect(response.status()).toBe(201)
      const result = await response.json()
      if (attempts.length === 1) {
        originalId = result.id
        await pending
        return route.abort('failed')
      }
      expect(result.id).toBe(originalId)
      return route.fulfill({ response })
    })
    await dialog.getByRole('button', { name: '保存采购记录', exact: true }).dblclick()
    await expect.poll(() => originalId.length).toBeGreaterThan(0)
    expect(attempts).toHaveLength(1)
    release()
    await expect(dialog.getByText('采购记录暂时无法保存', { exact: true })).toBeVisible()
    await expect(dialog.getByLabel('物品名称')).toHaveValue(name)
    await dialog.getByRole('button', { name: '保存采购记录', exact: true }).click()
    await expect(dialog).toHaveCount(0)
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
    expect(attempts[0].operation_key).toMatch(/^[a-f0-9-]{36}$/)
    const purchase = await exactPurchase(observer.api, name)
    expect(purchase.id).toBe(originalId)
    expect(purchase.total_price_cents).toBe(1000)
    const row = page.getByRole('row').filter({ hasText: name })
    let settlements = 0, reimbursements = 0, invoices = 0
    page.on('request', item => {
      const path = new URL(item.url()).pathname
      if (item.method() === 'PATCH' && path.endsWith('/settlement')) settlements++
      if (item.method() === 'PATCH' && path.endsWith('/reimbursement')) reimbursements++
      if (item.method() === 'POST' && path === `${procurement}/invoices`) invoices++
    })
    await row.getByRole('button', { name: '确认结算', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '确认已结算', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await row.getByRole('button', { name: '标记已报销', exact: true }).click()
    await page.getByRole('dialog').getByRole('button', { name: '确认已报销', exact: true }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await row.getByRole('button', { name: '关联发票', exact: true }).click()
    const invoice = page.getByRole('dialog', { name: '上传并匹配发票', exact: true })
    await invoice.getByLabel('发票文件').setInputFiles({ name: 'invoice.png', mimeType: 'image/png',
      buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGNkYPjPwMDAxAAGAAsfAQMU4wsAAAAAAElFTkSuQmCC', 'base64') })
    const uploaded = page.waitForResponse(response => new URL(response.url()).pathname === `${procurement}/invoices` && response.request().method() === 'POST')
    await invoice.getByRole('button', { name: '确认上传发票', exact: true }).click()
    expect((await uploaded).status()).toBe(201)
    await expect(invoice).toHaveCount(0)
    const result = await exactPurchase(observer.api, name)
    expect(result).toMatchObject({ id: originalId, is_settled: true, is_reimbursed: true, has_invoice: true, total_price_cents: 1000 })
    expect(result.invoice.id).toBeTruthy()
    expect([settlements, reimbursements, invoices]).toEqual([1, 1, 1])
    for (const format of ['csv', 'xlsx']) {
      const response = await observer.api.post(`${procurement}/exports`, { headers: observer.headers,
        data: { format, scope: 'current', query: name, purpose: 'Optional panel exact record format check' } })
      expect(response.status()).toBe(200)
      const body = await response.body()
      expect(response.headers()['content-disposition']).toContain(`.${format}`)
      if (format === 'csv') { expect([...body.subarray(0, 3)]).toEqual([239, 187, 191]); expect(response.headers()['content-type']).toContain('text/csv') }
      else { expect(body.subarray(0, 2).toString()).toBe('PK'); expect(response.headers()['content-type']).toContain('spreadsheetml') }
    }
    evidence(info, { deferredBeforeOpen: true, purchaseAttempts: attempts.length, sameReplayPayload: true, exactPurchaseCount: 1, settlements, reimbursements, invoices, exportFormatsPreserved: true })
  } finally { await setup.api.dispose(); await observer.api.dispose() }
})

test('real asset QR opens only on demand without mutations and keeps parent filter draft', async ({ page }, info) => {
  const payload = payloadFor(info)
  const setup = await session(payload.baseURL, payload.credentialsFile)
  const observer = await session(payload.baseURL, payload.credentialsFile)
  try {
    const name = `Optional asset ${info.project.name}`
    const response = await setup.api.post('/api/v1/assets', { headers: setup.headers, data: { name, serial_number: 'PRIVATE-OPTIONAL', location: 'Synthetic room' } })
    expect(response.status()).toBe(201)
    const asset = await response.json()
    const scripts: string[] = []
    let mutations = 0
    page.on('request', item => {
      if (item.resourceType() === 'script') scripts.push(item.url())
      if (new URL(item.url()).pathname.startsWith('/api/v1/assets') && !['GET', 'HEAD', 'OPTIONS'].includes(item.method())) mutations++
    })
    await page.goto('/assets')
    await expect(page.getByRole('heading', { name: '资产管理', exact: true })).toBeVisible()
    expect(scripts.some(url => /\/AssetQrDialog-[^/]+\.js$/.test(url))).toBe(false)
    await page.getByLabel('搜索资产').fill(name)
    await page.getByRole('button', { name: '查询', exact: true }).click()
    await page.locator('.assetTableRow').filter({ hasText: name }).getByRole('button', { name: '二维码', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await expect(dialog.locator('.assetQrCode svg')).toHaveCount(1)
    await expect(dialog.locator('.assetQrUrl')).toHaveText(`扫码地址：${payload.baseURL}/asset-scan/${encodeURIComponent(asset.asset_code)}`)
    await expect(page.locator('.assetQrDialogBackdrop')).toHaveCSS('position', 'fixed')
    await dialog.getByRole('button', { name: '关闭', exact: true }).click()
    await expect(page.getByLabel('搜索资产')).toHaveValue(name)
    const own = await get(observer.api, `/api/v1/assets?query=${encodeURIComponent(name)}`)
    expect(own.total).toBe(1)
    expect(own.items[0].id).toBe(asset.id)
    const publicScan = await request.newContext({ baseURL: payload.baseURL })
    try {
      const scan = await get(publicScan, `/api/v1/assets/scan/${encodeURIComponent(asset.asset_code)}`)
      expect(scan.name).toBe(name)
      expect(scan).not.toHaveProperty('serial_number')
      expect(scan).not.toHaveProperty('person_name')
    } finally { await publicScan.dispose() }
    expect(mutations).toBe(0)
    evidence(info, { deferredBeforeOpen: true, realQrRendered: true, currentOriginScan: true, parentFilterPreserved: true, exactAssetCount: 1, publicPrivacy: true, mutations })
  } finally { await setup.api.dispose(); await observer.api.dispose() }
})

test('independent coach submission and admin review retain actual-open correction and real retry', async ({ page, browser }, info) => {
  const payload = payloadFor(info)
  const observer = await session(payload.baseURL, payload.credentialsFile)
  const owner = await browser.newContext({ baseURL: payload.baseURL, viewport: info.project.use.viewport,
    hasTouch: info.project.name === 'touch', isMobile: info.project.name === 'touch' })
  try {
    const signedIn = await owner.request.post('/api/v1/auth/login', { data: JSON.parse(readFileSync(payload.coachCredentialsFile, 'utf8')) })
    expect(signedIn.status()).toBe(200)
    expect((await signedIn.json()).user.is_super_admin).toBe(false)
    const ownerPage = await owner.newPage()
    // Complete actor-owned sets, not only the ID of the response we happened to see.
    // This observer session is separate from the mounted browser's rotating CSRF.
    const ownerObserver = await session(payload.baseURL, payload.coachCredentialsFile)
    let ownBefore: { total: number; items: { id: string }[] }
    try { ownBefore = await get(ownerObserver.api, `${personnel}/requests?view=mine&page_size=100`) }
    finally { await ownerObserver.api.dispose() }
    expect(ownBefore.total).toBe(ownBefore.items.length)
    let submissionPosts = 0
    ownerPage.on('request', item => {
      if (new URL(item.url()).pathname === `${personnel}/requests` && item.method() === 'POST') submissionPosts++
    })
    const sequence = info.project.name === 'desktop' ? 751 : 753
    const name = `Optional assisted ${info.project.name}`
    await ownerPage.goto('/people/assisted-requests')
    await ownerPage.getByRole('button', { name: '新建申请', exact: true }).click()
    await fillAdult(ownerPage, name, sequence)
    await ownerPage.getByRole('checkbox', { name: 'E2E协助独立教练', exact: true }).check()
    const submitted = ownerPage.waitForResponse(response => new URL(response.url()).pathname === `${personnel}/requests` && response.request().method() === 'POST')
    await ownerPage.getByRole('button', { name: '提交申请', exact: true }).dblclick()
    const submittedResponse = await submitted
    expect(submittedResponse.status()).toBe(201)
    const intake = await submittedResponse.json()
    expect(intake.status).toBe('pending')
    expect(intake.approved_person_id).toBeNull()
    await page.goto('/people/assisted-review')
    await page.getByRole('button', { name: '查看申请 1', exact: true }).click()
    const approvedResponse = page.waitForResponse(response => new URL(response.url()).pathname === `${personnel}/requests/${intake.id}/approve`)
    await page.getByRole('button', { name: '审核通过', exact: true }).click()
    const approved = await approvedResponse
    expect(approved.status()).toBe(200)
    const personId = (await approved.json()).person_id
    const before = await get(observer.api, `${personnel}/people/${personId}/editor-context`)
    expect(before.allowed_actions).toContain('correct_profile')
    const scripts: string[] = []
    let contexts = 0
    page.on('request', item => {
      if (item.resourceType() === 'script') scripts.push(item.url())
      if (new URL(item.url()).pathname === `${personnel}/people/${personId}/editor-context`) contexts++
    })
    await page.goto(`/people/${personId}`)
    await expect(page.getByRole('button', { name: '更正本人及监护资料', exact: true })).toBeVisible()
    expect(scripts.some(url => /\/CorrectionDraft-[^/]+\.js$/.test(url))).toBe(false)
    expect(contexts).toBe(1)
    await page.getByRole('button', { name: '更正本人及监护资料', exact: true }).click()
    await expect(page.getByLabel('姓名', { exact: true })).toHaveValue('')
    expect(contexts).toBe(1)
    await fillAdult(page, `${name} corrected`, sequence)
    await page.getByLabel('资料更正原因', { exact: true }).fill('Synthetic complete facts verification')
    const attempts: Record<string, unknown>[] = []
    let firstApproval = '', release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    await page.route(`**${personnel}/people/${personId}/corrections`, async route => {
      attempts.push(route.request().postDataJSON())
      const response = await route.fetch()
      expect(response.status()).toBe(200)
      const result = await response.json()
      if (attempts.length === 1) { firstApproval = result.approval_id; await pending; return route.abort('failed') }
      expect(result.approval_id).toBe(firstApproval)
      return route.fulfill({ response })
    })
    await page.getByRole('button', { name: '提交更正', exact: true }).dblclick()
    await expect.poll(() => firstApproval.length).toBeGreaterThan(0)
    expect(attempts).toHaveLength(1)
    release()
    await expect(page.getByText(/操作未成功/)).toBeVisible()
    await expect(page.getByLabel('姓名', { exact: true })).toHaveValue(`${name} corrected`)
    await page.getByRole('button', { name: '提交更正', exact: true }).click()
    await expect(page.getByText('资料更正成功。', { exact: true })).toBeVisible()
    expect(attempts).toHaveLength(2)
    expect(attempts[1]).toEqual(attempts[0])
    const after = await get(observer.api, `${personnel}/people/${personId}/editor-context`)
    expect(after.profile_version).toBe(before.profile_version + 1)
    expect(after.current_coach_person_ids).toEqual(before.current_coach_person_ids)
    expect((await get(observer.api, `/api/v1/people/${personId}`)).full_name).toBe(`${name} corrected`)
    const finalOwnerObserver = await session(payload.baseURL, payload.coachCredentialsFile)
    let ownAfter: { total: number; items: { id: string }[] }
    try { ownAfter = await get(finalOwnerObserver.api, `${personnel}/requests?view=mine&page_size=100`) }
    finally { await finalOwnerObserver.api.dispose() }
    expect(ownAfter.total).toBe(ownAfter.items.length)
    expect(ownAfter.total - ownBefore.total).toBe(1)
    assertOwnRequestDelta(ownBefore.items.map(item => item.id), ownAfter.items.map(item => item.id), intake.id, submissionPosts)
    evidence(info, { independentOwnerReviewer: true, ownRequestDelta: ownAfter.total - ownBefore.total, submissionPosts, deferredCorrection: true, singleInitialContext: true,
      correctionAttempts: attempts.length, sameRetryPayload: true, oneVersionIncrement: true, persistedCorrection: true })
  } finally { await owner.close(); await observer.api.dispose() }
})
