import { createHash, randomBytes, randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { expect, request, test, type APIRequestContext, type APIResponse, type Page, type Request } from '@playwright/test'
import type { CommandEnvelope, ResultEnvelope, ResultReceipt, RunManifest } from '../src/reactionx/contracts'
import type { ArchiveRequest, UncertaintyReceipt } from '../src/reactionx/uncertainty'
import { e2eCredentialTuple } from './e2eCredentials'
import { loginAsAdmin, loginWithIdentifier } from './helpers'

// This is synthetic HTTP/UI acceptance, not a Windows runtime or light driver.
// No route interception, direct DB outcome insertion or fake server receipt.
test.describe.configure({ mode: 'serial' })
test.use({ trace: 'off', screenshot: 'off', video: 'off' })
test.setTimeout(90_000)

const WEB = '/api/v1/reactionx', MACHINE = '/api/v1/reactionx-client'
const PAGE = '/talent-tests/reactionx'
const SUBJECTS = [1, 2, 3].map(index => `e8300000-0000-4000-8000-${String(index).padStart(12, '0')}`)
const fixtures = JSON.parse(readFileSync(fileURLToPath(new URL('../../../shared/reactionx/v1/fixtures.json', import.meta.url)), 'utf8')) as {
  cases: { name: string; manifest: RunManifest; result: ResultEnvelope }[]
}
function fixture(mode: 'manual' | 'standard') {
  const value = fixtures.cases.find(item => item.name === mode)
  if (!value) throw new Error('RX fixture missing')
  return structuredClone(value)
}
type Run = { run_id: string; manifest: RunManifest; status: string; stop_confirmed: boolean; result_saved: boolean }
type Pair = { pairing_id: string; challenge: string; verification_phrase: string }
function checkpoint(phase: string) { test.info().annotations.push({ type: 'reactionx.phase.v1', description: phase }) }
async function json<T>(response: APIResponse, expected = 200): Promise<T> {
  // Never print bodies or request headers on errors; raw reporters are private too.
  if (response.status() !== expected) throw new Error(`RX HTTP status ${response.status()} expected ${expected}`)
  return await response.json() as T
}
async function csrf(page: Page) {
  return (await json<{ csrf_token: string }>(await page.request.get('/api/v1/auth/csrf'))).csrf_token
}
async function browserPost<T>(page: Page, path: string, data: unknown, expected = 200) {
  return json<T>(await page.request.post(WEB + path, { data, headers: { 'X-CSRF-Token': await csrf(page) } }), expected)
}
async function logout(page: Page) {
  // A click/anonymous React render alone is not persisted session revocation.
  const response = page.waitForResponse(value => value.url().endsWith('/api/v1/auth/logout') && value.status() === 204)
  await page.getByRole('button', { name: '退出登录', exact: true }).click()
  await response
  await expect(page.getByRole('heading', { name: '登录工作台', exact: true })).toBeVisible()
}

class FakeConnector {
  readonly secret = randomBytes(32).toString('base64url')
  readonly instance = randomUUID()
  id = ''
  tab = ''
  timer: ReturnType<typeof setInterval> | undefined
  constructor(readonly api: APIRequestContext) {}
  headers() { return { Authorization: `ReactionX ${this.id}.${this.secret}` } }
  async claim(pairing: Pair) {
    const value = await json<{ connector_id: string }>(await this.api.post(MACHINE + '/pairings/claim', { data: {
      challenge: pairing.challenge, credential_digest: createHash('sha256').update(this.secret).digest('hex'),
      client_info: { origin: 'https://skymoonzyj.l.cd:18080', name: 'ReactionX synthetic connector' },
    } }))
    this.id = value.connector_id
    await json(await this.api.post(`${MACHINE}/pairings/${pairing.pairing_id}/confirm`, {
      headers: this.headers(), data: { verification_phrase: pairing.verification_phrase },
    }))
  }
  async heartbeat(disconnected = false) {
    return json<{ run: Run | null }>(await this.api.post(MACHINE + '/heartbeat', {
      headers: this.headers(), data: { protocol_version: 1, connector_version: '0.3.0', instance_id: this.instance,
        devices: fixture('manual').manifest.devices.map(device => ({ ...device, label: `合成灯 ${device.display_number}`, connected: !disconnected })),
      },
    }))
  }
  async online() {
    await this.heartbeat()
    this.timer = setInterval(() => { void this.heartbeat().catch(() => undefined) }, 1500)
  }
  pause() { if (this.timer) clearInterval(this.timer); this.timer = undefined }
  async next(kind: string): Promise<CommandEnvelope> {
    let command: CommandEnvelope | undefined
    await expect.poll(async () => {
      const body = await json<{ commands: CommandEnvelope[] }>(await this.api.get(MACHINE + '/commands', { headers: this.headers() }))
      command = body.commands.find(value => value.kind === kind)
      return !!command
    }, { timeout: 12_000, intervals: [150, 300, 500] }).toBe(true)
    if (!command) throw new Error('RX command absent')
    return command
  }
  async ack(command: CommandEnvelope, state: 'ready' | 'running' | 'stopped_confirmed' | null, run?: Run) {
    return json<{ operation_id: string; run_status: string; status: string }>(await this.api.post(`${MACHINE}/commands/${command.operation_id}/ack`, {
      headers: this.headers(), data: { status: state === 'running' ? 'accepted' : 'completed', run_state: state,
        off_confirmed_device_keys: state === 'stopped_confirmed' ? run!.manifest.devices.map(device => device.device_key) : [],
      },
    }))
  }
  envelope(run: Run, mode: 'manual' | 'standard', reaction = 200): ResultEnvelope {
    const body = fixture(mode).result
    body.run_id = run.run_id; body.manifest_digest = run.manifest.manifest_digest
    if (mode === 'standard') {
      // The complete frozen 30-task fixture has 60 real synthetic activation/hit
      // events. Adjust timing, never truncate or append a fabricated terminal.
      body.events.forEach((event, index) => {
        const activation = Math.floor(index / 2)
        event.host_elapsed_ms = activation * (500 + reaction) + (index % 2 ? reaction : 0)
        if (event.kind === 'hit') event.device_ticks_10ms = reaction / 10
      })
      body.end.active_elapsed_ms = body.events.at(-1)!.host_elapsed_ms
    }
    return body
  }
  async upload(run: Run, body: ResultEnvelope) {
    return json<ResultReceipt>(await this.api.post(`${MACHINE}/runs/${run.run_id}/result`, { headers: this.headers(), data: body }))
  }
  async close() { this.pause(); await this.api.dispose() }
}

async function connect(page: Page, operator: 'admin' | 'life_teacher' = 'admin') {
  page.setDefaultTimeout(15_000)
  checkpoint('login')
  if (operator === 'admin') await loginAsAdmin(page)
  else await loginWithIdentifier(page, ...e2eCredentialTuple(operator))
  await page.goto(PAGE)
  const unavailable = page.getByText('安装包尚未就绪', { exact: true })
  await expect(unavailable).toBeVisible()
  const created = page.waitForResponse(response => response.url().endsWith(WEB + '/pairings') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '配对本机连接器', exact: true }).click()
  const response = await created
  if (response.status() !== 200) throw new Error('RX pairing creation failed')
  const pairing = await response.json() as Pair
  checkpoint('pair_created')
  const machine = new FakeConnector(await request.newContext({ baseURL: process.env.E2E_API_URL, timeout: 10_000 }))
  try {
  machine.tab = (response.request().postDataJSON() as { browser_tab_id: string }).browser_tab_id
  await machine.claim(pairing)
  checkpoint('machine_claimed')
  const confirmed = page.waitForResponse(value => value.url().endsWith(`${WEB}/pairings/${pairing.pairing_id}/confirm`) && value.request().method() === 'POST')
  await page.getByRole('button', { name: '确认两端信息一致', exact: true }).click()
  if ((await confirmed).status() !== 200) throw new Error('RX browser pairing confirmation failed')
  checkpoint('browser_confirmed')
  await machine.online()
  checkpoint('machine_online')
  await expect(page.getByText('本机连接器', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  await expect(page.getByRole('button', { name: '准备自由训练', exact: true })).toBeVisible()
  checkpoint('local_ui_ready')
  return machine
  } catch (error) { await machine.close(); throw error }
}

async function manualReady(page: Page, machine: FakeConnector, acknowledgeStart = true) {
  checkpoint('manual_setup')
  await page.getByRole('searchbox', { name: '搜索授权参测人员' }).fill('ReactionX 合成人员')
  await expect(page.getByRole('option', { name: 'ReactionX 合成人员 1', exact: true })).toBeAttached()
  await page.getByRole('combobox', { name: /^参测人员/ }).selectOption(SUBJECTS[0])
  for (const checkbox of await page.locator('fieldset').getByRole('checkbox').all()) await checkbox.check()
  await page.getByLabel('已核对参测人员、设备顺序及现场布局；自由训练不参与排名。').check()
  const prepared = page.waitForResponse(response => response.url().endsWith(WEB + '/runs/prepare'))
  await page.getByRole('button', { name: '准备自由训练', exact: true }).click()
  const response = await prepared
  if (response.status() !== 202) throw new Error('RX manual prepare failed')
  const run = await response.json() as Run
  checkpoint('manual_prepared')
  await machine.ack(await machine.next('prepare'), 'ready')
  checkpoint('manual_ready')
  await page.getByRole('button', { name: '开始训练', exact: true }).click()
  const start = await machine.next('start')
  if (acknowledgeStart) {
    await machine.ack(start, 'running')
    await expect(page.getByRole('button', { name: /^点亮第 1 位/ })).toBeVisible()
    checkpoint('manual_running')
  } else checkpoint('uncertain_start_delivered')
  return { run, start }
}

test('RX-UI-001 actual manual UI accountless subject stop lost receipt replay report', async ({ page }) => {
  const machine = await connect(page)
  try {
    const { run } = await manualReady(page, machine)
    await page.getByRole('button', { name: /^点亮第 1 位/ }).click()
    await machine.ack(await machine.next('manual_activate'), null)
    checkpoint('manual_action')
    await page.getByRole('button', { name: '停止并确认熄灯', exact: true }).click()
    const stop = await machine.next('stop')
    await machine.ack(stop, 'stopped_confirmed', run)
    checkpoint('manual_stop')
    const body = machine.envelope(run, 'manual')
    const first = await machine.upload(run, body)
    // Deliberately ignore the first application ACK; resend exact original bytes.
    const replay = await machine.upload(run, body)
    expect(replay).toEqual(first)
    const recovered = await json<ResultReceipt>(await machine.api.get(`${MACHINE}/runs/${run.run_id}/receipt?digest=${first.digest}`, { headers: machine.headers() }))
    expect(recovered).toEqual(first)
    checkpoint('manual_receipt')
    await page.getByRole('button', { name: '刷新已保存报告', exact: true }).click()
    await expect(page.getByRole('link', { name: '查看实体灯报告' }).first()).toBeVisible()
    await page.getByRole('link', { name: '查看实体灯报告' }).first().click()
    await expect(page.getByRole('heading', { name: '实体灯训练报告', exact: true })).toBeVisible()
    await expect(page.getByText('自由训练 · 不参与排名', { exact: true })).toBeVisible()
    const report = await json<{ subject_person_id: string; ranking_eligible: boolean }>(await page.request.get(WEB + `/results/${first.result_id}`))
    expect(report.subject_person_id).toBe(SUBJECTS[0]); expect(report.ranking_eligible).toBe(false)
    checkpoint('manual_report')
  } finally { await machine.close() }
})

test('RX-HTTP-002 isolated formal natural completion same browser session anonymous top three', async ({ page }) => {
  const machine = await connect(page)
  try {
    const ids: string[] = []
    checkpoint('formal_runs')
    for (const [index, subject] of SUBJECTS.entries()) {
      const template = fixture('standard').manifest
      const run = await browserPost<Run>(page, '/runs/prepare', { operation_id: randomUUID(), tab_id: machine.tab,
        person_id: subject, connector_id: machine.id, mode: 'standard', config: template.config,
        devices: template.devices, layout: template.layout }, 202)
      await machine.ack(await machine.next('prepare'), 'ready')
      await browserPost(page, `/runs/${run.run_id}/start`, { operation_id: randomUUID(), tab_id: machine.tab }, 202)
      const start = await machine.next('start')
      await machine.ack(start, 'running')
      const saved = await machine.upload(run, machine.envelope(run, 'standard', 200 + index * 100))
      await machine.ack(start, 'stopped_confirmed', run)
      ids.push(saved.result_id)
    }
    const rank = await json<{ participant_count: number; current: { rank: number }; top_three: { rank: number; gaps: unknown[] }[] }>(await page.request.get(WEB + `/results/${ids[2]}/ranking`))
    expect(rank.participant_count).toBe(3); expect(rank.current.rank).toBe(3)
    expect(rank.top_three.map(row => row.rank)).toEqual([1, 2, 3])
    for (const row of rank.top_three) { expect(Object.keys(row).sort()).toEqual(['gaps', 'is_self', 'rank', 'tie_count', 'values']); expect(row.gaps).toHaveLength(2) }
    await page.goto(`/talent-tests/reactionx/reports/${ids[2]}`)
    await expect(page.getByRole('table', { name: '同组历史最佳前三' })).toBeVisible()
    await expect(page.getByText('匿名参测者', { exact: true })).toHaveCount(2)
    await expect(page.getByText('ReactionX 合成人员 1', { exact: true })).toHaveCount(0)
  } finally { await machine.close() }
})

test('RX-UI-003 disconnect cannot prove OFF revoke and ordinary actor denial', async ({ page }) => {
  const machine = await connect(page)
  try {
    const { run, start } = await manualReady(page, machine)
    machine.pause()
    checkpoint('disconnect')
    await machine.heartbeat(true)
    const status = await json<Run>(await page.request.get(WEB + `/runs/${run.run_id}`))
    expect(status.stop_confirmed).toBe(false)
    const body = machine.envelope(run, 'manual'); body.events = []; body.end = { kind: 'disconnected', active_elapsed_ms: 0 }
    const saved = await machine.upload(run, body)
    expect(saved.run_id).toBe(run.run_id)
    await machine.ack(start, 'stopped_confirmed', run)
    const response = await page.request.delete(`${WEB}/connectors/${machine.id}`, { headers: { 'X-CSRF-Token': await csrf(page) } })
    expect(response.status()).toBe(204)
    expect((await machine.api.get(MACHINE + '/status', { headers: machine.headers() })).status()).toBe(401)
    await logout(page)
    await loginWithIdentifier(page, ...e2eCredentialTuple('coach'))
    await page.goto(PAGE)
    await expect(page.getByRole('button', { name: '配对本机连接器', exact: true })).toHaveCount(0)
    expect((await page.request.get(WEB + '/people')).status()).toBe(403)
  } finally { await machine.close() }
})

test('RX-UI-004 identity switch clears current document control and private reports', async ({ page }) => {
  const machine = await connect(page)
  try {
    const { run } = await manualReady(page, machine)
    checkpoint('identity_switch')
    // Pending execution is not completed by logout. Safety ACK still needs real
    // connector OFF evidence; no browser receipt or elapsed-time shortcut.
    await logout(page)
    await machine.ack(await machine.next('stop'), 'stopped_confirmed', run)
    machine.pause()
    await loginWithIdentifier(page, ...e2eCredentialTuple('coach'))
    await page.goto(PAGE)
    await expect(page.getByRole('button', { name: /^点亮第/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: '开始训练', exact: true })).toHaveCount(0)
    await expect(page.getByText('ReactionX 合成人员 1', { exact: true })).toHaveCount(0)
    expect((await page.request.get(WEB + `/runs/${run.run_id}`)).status()).toBe(403)
  } finally { await machine.close() }
})

test('RX-UI-005 actual two-step admin archive preserves unknown delivery without result or rank', async ({ page }) => {
  // Guarded second synthetic superadmin has its own real pairing budget. Do not
  // reset/bypass the first admin's five/minute throttle or share old credentials.
  const machine = await connect(page, 'life_teacher')
  try {
    const { run, start } = await manualReady(page, machine, false)
    await expect(page.getByRole('button', { name: /^点亮第/ })).toHaveCount(0)
    await page.getByRole('button', { name: '停止并确认熄灯', exact: true }).click()
    const stop = await machine.next('stop')
    const offAck = await machine.ack(stop, 'stopped_confirmed', run)
    expect(offAck.run_status).toBe('awaiting_sync')
    const unknown = await json<Run>(await page.request.get(WEB + `/runs/${run.run_id}`))
    expect(unknown.status).toBe('awaiting_sync'); expect(unknown.stop_confirmed).toBe(true); expect(unknown.result_saved).toBe(false)
    const localEvidence = { version: 1, manifest: run.manifest, delivered_start: start, delivered_stop: stop,
      stop_ack: offAck, off_device_keys: run.manifest.devices.map(device => device.device_key) }
    const declaration = { version: 1, kind: 'start_delivery_unknown', run_id: run.run_id,
      actor_account_id: run.manifest.actor_account_id, subject_person_id: run.manifest.subject_person_id,
      connector_id: machine.id, manifest_digest: run.manifest.manifest_digest, stop_operation_id: stop.operation_id,
      local_evidence_digest: createHash('sha256').update(JSON.stringify(localEvidence)).digest('hex') }
    // This strict declaration is flat safe integers/strings; sorted UTF-16
    // field names produce its wire canonical JSON without loading Vite assets.
    const digest = createHash('sha256').update(JSON.stringify(declaration, Object.keys(declaration).sort())).digest('hex')
    const declared = await json<{ state: string; declaration_digest: string; receipt: null }>(await machine.api.post(
      `${MACHINE}/runs/${run.run_id}/uncertainty`, { headers: machine.headers(), data: declaration }))
    expect(declared).toEqual({ state: 'pending_review', declaration_digest: digest, receipt: null })
    checkpoint('uncertainty_declared')
    const panel = page.getByRole('region', { name: '执行未知待核对', exact: true })
    await panel.getByRole('button', { name: '刷新执行未知记录', exact: true }).click()
    await expect(panel.getByText(run.run_id, { exact: true })).toBeVisible()
    await expect(panel.getByRole('table')).toHaveCount(0)
    await panel.getByRole('button', { name: '核对并填写原因', exact: true }).click()
    const reason = 'Synthetic original device OFF and unknown delivery checked'
    await expect(panel.getByRole('button', { name: '发起归档核对', exact: true })).toBeDisabled()
    await panel.getByLabel('异常归档原因', { exact: true }).fill(reason)
    const requested = page.waitForResponse(response => response.url().endsWith(`${WEB}/runs/${run.run_id}/uncertainty/request`) && response.request().method() === 'POST')
    await panel.getByRole('button', { name: '发起归档核对', exact: true }).click()
    const requestResponse = await requested
    if (requestResponse.status() !== 200) throw new Error('RX archive request failed')
    const archiveRequest = await requestResponse.json() as ArchiveRequest
    expect(archiveRequest.run_id).toBe(run.run_id); expect(archiveRequest.declaration_digest).toBe(digest)
    const dialog = page.getByRole('dialog', { name: '再次确认异常归档', exact: true })
    await expect(dialog).toBeVisible()
    await expect(dialog.getByRole('button', { name: '返回，不归档', exact: true })).toBeFocused()
    const pending = await json<{ state: string; receipt: null }>(await machine.api.get(
      `${MACHINE}/runs/${run.run_id}/uncertainty?declaration_digest=${digest}`, { headers: machine.headers() }))
    expect(pending.state).toBe('pending_review'); expect(pending.receipt).toBeNull()
    checkpoint('archive_requested')
    const confirmed = page.waitForResponse(response => response.url().endsWith(`${WEB}/runs/${run.run_id}/uncertainty/confirm`) && response.request().method() === 'POST')
    await dialog.getByRole('button', { name: '确认归档为执行未知', exact: true }).click()
    const confirmResponse = await confirmed
    if (confirmResponse.status() !== 200) throw new Error('RX archive confirmation failed')
    const receipt = await confirmResponse.json() as UncertaintyReceipt
    expect(receipt.run_id).toBe(run.run_id); expect(receipt.status).toBe('execution_unknown')
    expect(receipt.connector_id).toBe(machine.id); expect(receipt.manifest_digest).toBe(run.manifest.manifest_digest)
    expect(receipt.declaration_digest).toBe(digest); expect(receipt.stop_operation_id).toBe(stop.operation_id)
    expect(Object.keys(receipt).sort()).toEqual(['archive_id', 'closed_at', 'connector_id', 'declaration_digest', 'manifest_digest', 'run_id', 'status', 'stop_operation_id', 'version'])
    await expect(panel.getByRole('heading', { name: '执行状态未知，需复测', exact: true })).toBeVisible()
    await expect(panel.getByText(/^后台已归档，原记录和审计保留/)).toBeVisible()
    await expect(panel.getByText(/^尚不能确认原连接器已收到并持久化归档凭据/)).toBeVisible()
    await expect(panel.getByText('成绩已保存', { exact: true })).toHaveCount(0)
    await expect(panel.getByRole('table')).toHaveCount(0)
    await expect(dialog).toHaveCount(0)
    const runPanel = page.getByRole('region', { name: '训练状态', exact: true })
    await expect(runPanel.getByRole('status')).toHaveText('执行状态未知，需复测')
    await expect(runPanel.getByText(/^保存状态：/)).toHaveCount(0)
    let terminalPolls = 0
    const countTerminalPolls = (value: Request) => {
      if (value.method() === 'GET' && new URL(value.url()).pathname === `${WEB}/runs/${run.run_id}`) terminalPolls += 1
    }
    page.on('request', countTerminalPolls)
    try {
      // The ordinary run query polls every2s; archived state must stop it.
      await page.waitForTimeout(2500)
      expect(terminalPolls).toBe(0)
    } finally { page.off('request', countTerminalPolls) }
    const replay = await browserPost<UncertaintyReceipt>(page, `/runs/${run.run_id}/uncertainty/confirm`, { request_id: archiveRequest.request_id, confirmation: true })
    expect(replay).toEqual(receipt)
    const retrieved = await json<{ state: string; receipt: UncertaintyReceipt }>(await machine.api.get(
      `${MACHINE}/runs/${run.run_id}/uncertainty?declaration_digest=${digest}`, { headers: machine.headers() }))
    expect(retrieved.state).toBe('archived'); expect(retrieved.receipt).toEqual(receipt)
    // A rejected late upload must not turn unknown delivery into a saved score.
    const late = machine.envelope(run, 'manual'); late.events = []; late.end = { kind: 'process_interrupted', active_elapsed_ms: 0 }
    const rejected = await machine.api.post(`${MACHINE}/runs/${run.run_id}/result`, { headers: machine.headers(), data: late })
    expect(rejected.status()).toBe(409)
    expect((await rejected.json() as { code: string }).code).toBe('reactionx_uncertain_closed')
    const after = await json<{ receipt: UncertaintyReceipt }>(await machine.api.get(
      `${MACHINE}/runs/${run.run_id}/uncertainty?declaration_digest=${digest}`, { headers: machine.headers() }))
    expect(after.receipt).toEqual(receipt)
    const listing = await json<{ items: { receipt: ResultReceipt }[] }>(await page.request.get(WEB + `/results?person_id=${SUBJECTS[0]}`))
    expect(listing.items.some(item => item.receipt.run_id === run.run_id)).toBe(false)
    const pendingList = await json<{ total: number }>(await page.request.get(WEB + '/uncertainties?offset=0&limit=20'))
    expect(pendingList.total).toBe(0)
    checkpoint('archive_confirmed')
  } finally { await machine.close() }
})
