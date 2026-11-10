import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { ConnectionPanel } from './ConnectionPanel'
import { RunPanel } from './RunPanel'
import { ReactionxPage, PracticeSetup } from './ReactionxPage'
import { renderAssessmentPage, studentFixture } from '../esportsAssessments/testSupport/renderAssessmentPage'
import type { RunView, ConnectorView } from './api'
import { documentControl } from './api'
import fixtures from '../../../../shared/reactionx/v1/fixtures.json'

afterEach(() => { cleanup(); documentControl.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks() })
const noPackage = { available: false, release_id: null, version: null, source_commit: null, protocol_version: null, filename: null, sha256: null, size: null, signature_status: null, distribution_status: 'unavailable' as const }
it('distinguishes undetected from uninstalled and never offers an unverified installer', () => {
  render(<ConnectionPanel windows canManage release={noPackage} connectors={[]} pairedConnectorId={null} busy={false} pairing={null} claim={null} error={null} onPair={vi.fn()} onConfirm={vi.fn()} onCancel={vi.fn()} onDownload={vi.fn()} />)
  expect(screen.getByText(/未检测到本标签页已确认的连接器/)).toBeVisible()
  expect(screen.getByText(/不代表未安装/)).toBeVisible()
  expect(screen.getByText('安装包尚未就绪')).toBeVisible()
  expect(screen.queryByRole('link', { name: /下载/ })).not.toBeInTheDocument()
})
it('does not mistake another tab connector for this computer or allow non-Windows control', () => {
  const connector = { connector_id: 'other', name: '另一台电脑', online: true, compatible: true, locally_confirmed: true, devices: [], active_run_id: null, attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [], protocol_version: 1, connector_version: '0.3.0' } satisfies ConnectorView
  render(<ConnectionPanel windows={false} canManage release={noPackage} connectors={[connector]} pairedConnectorId={null} busy={false} pairing={null} claim={null} error={null} onPair={vi.fn()} onConfirm={vi.fn()} onCancel={vi.fn()} onDownload={vi.fn()} />)
  expect(screen.getByText(/未检测到本标签页已确认的连接器/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '配对本机连接器' })).not.toBeInTheDocument()
  expect(screen.getByText(/仅支持 Windows/)).toBeVisible()
})
it('does not offer re-pairing while another document has unresolved physical ownership', () => {
  const connector = { connector_id: 'other', name: '原连接器', online: false, compatible: true, locally_confirmed: true, devices: [], active_run_id: 'active-run', attention_required: true, unsafe_operation_id: null, unsafe_device_keys: [], protocol_version: 1, connector_version: '0.3.0' } satisfies ConnectorView
  render(<ConnectionPanel windows canManage release={noPackage} connectors={[connector]} pairedConnectorId={null} busy={false} pairing={null} claim={null} error={null} onPair={vi.fn()} onConfirm={vi.fn()} onCancel={vi.fn()} onDownload={vi.fn()} />)
  expect(screen.getByRole('button', { name: '配对本机连接器' })).toBeDisabled()
})
it('requires both pairing confirmations and treats expiry as no control', () => {
  render(<ConnectionPanel windows canManage release={noPackage} connectors={[]} pairedConnectorId={null} busy={false} pairing={{ pairing_id: 'pair', challenge: 'private', pair_uri: 'reactionx-erp://pair?challenge=private', expires_at: '2020-01-01T00:00:00Z', verification_phrase: 'SYNTHETIC' }} claim={null} error={null} onPair={vi.fn()} onConfirm={vi.fn()} onCancel={vi.fn()} onDownload={vi.fn()} />)
  expect(screen.getByText(/配对已超时/)).toBeVisible()
  expect(screen.queryByRole('button', { name: /确认两端/ })).not.toBeInTheDocument()
})
it.each([
  ['stopping', false, false, '停止请求已发送，尚未确认灯已熄灭'],
  ['unconfirmed', false, false, '停止未确认'],
  ['awaiting_sync', true, false, '待同步'],
  ['completed', true, true, '已保存'],
])('shows %s without inventing a rank', (status, stop_confirmed, result_saved, label) => {
  const run = { status, stop_confirmed, result_saved, manifest: { mode: 'manual', devices: [] } } as unknown as RunView
  render(<RunPanel run={run} canOperate={false} busy={false} onStart={vi.fn()} onStop={vi.fn()} onActivate={vi.fn()} />)
  expect(screen.getByRole('status')).toHaveTextContent(label)
  expect(screen.getByText(/不参与排名/)).toBeVisible()
  expect(screen.queryByText(/第 0 名/)).not.toBeInTheDocument()
})
it('requires an explicit recovery reason and second confirmation without changing original actor', async () => {
  const writes: { url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') {
      writes.push({ url, body: JSON.parse(String(init.body)) })
      return Response.json(url.endsWith('/confirm') ? { authorization_id: 'grant', run_id: '11111111-1111-4111-8111-111111111111', digest: 'a'.repeat(64), kind: 'superadmin_recovery', status: 'authorized', expires_at: '2027-01-01T00:00:00Z' } : { request_id: '22222222-2222-4222-8222-222222222222', run_id: '11111111-1111-4111-8111-111111111111', digest: 'a'.repeat(64), status: 'confirmation_required', expires_at: '2027-01-01T00:00:00Z' })
    }
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  await userEvent.type(await screen.findByLabelText('待同步运行编号'), '11111111-1111-4111-8111-111111111111')
  await userEvent.type(screen.getByLabelText('原始结果 SHA-256'), 'a'.repeat(64))
  await userEvent.click(screen.getByLabelText(/我已在原连接器核对/))
  expect(screen.getByRole('button', { name: '申请管理员恢复' })).toBeDisabled()
  await userEvent.type(screen.getByLabelText('管理员恢复原因'), '原操作员已停用，保留原始包等待恢复')
  await userEvent.click(screen.getByRole('button', { name: '申请管理员恢复' }))
  expect(await screen.findByRole('button', { name: '再次确认授权恢复' })).toBeVisible()
  expect(writes).toHaveLength(1)
  expect(screen.queryByText('结果已保存')).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '再次确认授权恢复' }))
  expect(await screen.findByText(/已授权原连接器重传/)).toBeVisible()
  expect(writes[0].body).toEqual({ digest: 'a'.repeat(64), reason: '原操作员已停用，保留原始包等待恢复', confirmation: true })
  expect(writes[1].body).toEqual({ confirmation: true })
})
it('ordinary report readers see no operational controls and no management requests', async () => {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async input => { urls.push(String(input)); return Response.json({ items: [], next_cursor: null }) }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/talent-tests/reactionx'], account: { ...studentFixture, permissions: ['talent_tests.read'], scoped_permissions: [] } })
  expect(await screen.findByText(/当前账号仅可查看已授权报告/)).toBeVisible()
  expect(screen.queryByRole('button', { name: /配对|准备训练|扫描/ })).not.toBeInTheDocument()
  expect(urls.every(url => url.includes('/reactionx/results'))).toBe(true)
})
it('mounts execution-unknown review only for the actual superadmin operational workspace', async () => {
  vi.stubGlobal('fetch', vi.fn(async input => {
    const url = String(input)
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  expect(await screen.findByRole('heading', { name: '执行未知待核对' })).toBeVisible()
  expect(await screen.findByText('暂无执行未知待核对记录')).toBeVisible()
})
it('manual preparation stays unavailable when there is no current-document local pairing', async () => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  vi.stubGlobal('fetch', vi.fn(async input => {
    const url = String(input)
    if (url.endsWith('/releases/current')) return Response.json(noPackage)
    if (url.includes('/results')) return Response.json({ items: [], next_cursor: null })
    return Response.json([])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/talent-tests/reactionx'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  await act(async () => { await Promise.resolve() })
  expect(await screen.findByText('安装包尚未就绪')).toBeVisible()
  expect(screen.queryByRole('button', { name: '准备自由训练' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '刷新状态' }))
  expect(screen.queryByRole('button', { name: '开始训练' })).not.toBeInTheDocument()
})
it('keeps unclaimed pairing 404 pending until two-end confirmation instead of blocking the document', async () => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  let polls = 0
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input); urls.push(url)
    if (url.endsWith('/pairings') && init?.method === 'POST') return Response.json({ pairing_id: 'test-pair', challenge: 'a'.repeat(43), pair_uri: `reactionx-erp://pair?challenge=${'a'.repeat(43)}`, expires_at: '2099-01-01T00:00:00Z', verification_phrase: 'SYNTHETIC' })
    if (url.endsWith('/pairings/test-pair')) { polls += 1; return polls === 1 ? Response.json({}, { status: 404 }) : Response.json({ pairing_id: 'test-pair', connector_id: 'synthetic-local', verification_phrase: 'SYNTHETIC', expires_at: '2099-01-01T00:00:00Z', status: 'pending', browser_confirmed: false, device_confirmed: true, actor_account_id: studentFixture.account_id, actor_name: '合成用户', origin: 'https://erp.invalid' }) }
    if (url.endsWith('/releases/current')) return Response.json(noPackage)
    return Response.json([])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  await userEvent.click(await screen.findByRole('button', { name: '配对本机连接器' }))
  expect(await screen.findByRole('button', { name: '唤起本机连接器' })).toBeVisible()
  expect(await screen.findByText(/连接器：已确认/, {}, { timeout: 3000 })).toBeVisible()
  expect(screen.getByRole('button', { name: '确认两端信息一致' })).toBeEnabled()
  expect(screen.queryByRole('button', { name: '准备自由训练' })).not.toBeInTheDocument()
  expect(urls.filter(url => url.endsWith('/pairings/test-pair')).length).toBeGreaterThan(1)
})
it('prepares only a confirmed authorized subject and ordered opaque devices with verified protocol colors', async () => {
  const connector: ConnectorView = { connector_id: 'local', name: '合成连接器', online: true, compatible: true, locally_confirmed: true, protocol_version: 1, connector_version: '0.3.0', devices: [{ device_key: 'a', display_number: 1, protocol_version: 3, order: 0, label: '左灯', connected: true }, { device_key: 'b', display_number: 1, protocol_version: 4, order: 1, label: '右灯', connected: true }], active_run_id: null, attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [] }
  vi.stubGlobal('fetch', vi.fn(async () => Response.json([{ id: studentFixture.person_id, name: '合成参测者', nickname: null }])))
  const prepare = vi.fn()
  renderAssessmentPage(<PracticeSetup connector={connector} account='synthetic' modes={[{ mode: 'manual', available: true, verification_status: 'practice_only', reason: null }]} busy={false} onPrepare={prepare} onDevice={vi.fn()} />, { initialEntries: ['/'], account: studentFixture })
  await userEvent.selectOptions(await screen.findByLabelText('参测人员'), await screen.findByRole('option', { name: '合成参测者' }))
  await userEvent.click(screen.getByLabelText(/左灯/)); await userEvent.click(screen.getByLabelText(/右灯/))
  await userEvent.click(screen.getByRole('button', { name: '上移第 2 位' }))
  await userEvent.selectOptions(screen.getByLabelText('灯光颜色'), screen.getByRole('option', { name: '蓝色' }))
  expect(screen.getByRole('button', { name: '准备自由训练' })).toBeDisabled()
  await userEvent.click(screen.getByLabelText(/已核对参测人员/))
  await userEvent.click(screen.getByRole('button', { name: '准备自由训练' }))
  expect(prepare).toHaveBeenCalledTimes(1)
  expect(prepare.mock.calls[0][0]).toMatchObject({ person_id: studentFixture.person_id, mode: 'manual', config: { color: 4, lamp_count: 2 }, devices: [{ device_key: 'b', display_number: 1, order: 0 }, { device_key: 'a', display_number: 1, order: 1 }], layout: { posture: 'standing', positions: [{ device_key: 'b', x_cm: 0, y_cm: 0, z_cm: 100 }, { device_key: 'a', x_cm: 100, y_cm: 0, z_cm: 100 }] } })
  expect(prepare.mock.calls[0][0]).not.toHaveProperty('actor_account_id')
})

it('renews only this documents proven run and never starts on mount or foreground', async () => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  const proof = documentControl.forAccount(studentFixture.account_id)
  proof.connectorId = 'synthetic-local'; proof.runId = 'synthetic-run'
  const writes: { url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') { writes.push({ url, body: JSON.parse(String(init.body)) }); return Response.json({ lease_remaining_ms: 15000, renew_after_ms: 5000 }) }
    if (url.endsWith('/connectors')) return Response.json([{ connector_id: 'synthetic-local', name: '合成连接器', online: true, compatible: true, locally_confirmed: true, protocol_version: 1, connector_version: '0.3.0', devices: [], active_run_id: 'synthetic-run', attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [] }])
    if (url.endsWith('/runs/synthetic-run')) return Response.json({ run_id: 'synthetic-run', connector_id: 'synthetic-local', status: 'ready', manifest: fixtures.cases[0].manifest, lease_expires_at: '2099-01-01T00:00:00Z', lease_remaining_ms: 15000, stop_confirmed: false, result_saved: false })
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  expect(await screen.findByRole('button', { name: '开始训练' })).toBeEnabled()
  expect(writes).toEqual([])
  window.dispatchEvent(new Event('focus'))
  await waitFor(() => expect(writes.length).toBeGreaterThan(0), { timeout: 6500 })
  expect(writes.every(item => item.url.endsWith('/lease'))).toBe(true)
  expect(writes[0].body).toEqual({ tab_id: proof.tabId })
  await act(async () => { await documentControl.release() })
  expect(screen.queryByRole('button', { name: '开始训练' })).not.toBeInTheDocument()
}, 10000)

it('reads an old run in a fresh document without renewing or exposing start or stop controls', async () => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input); if (init?.method === 'POST') writes.push(url)
    if (url.endsWith('/connectors')) return Response.json([{ connector_id: 'old-document', name: '其他标签页', online: true, compatible: true, locally_confirmed: true, protocol_version: 1, connector_version: '0.3.0', devices: [], active_run_id: 'old-run', attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [] }])
    if (url.endsWith('/runs/old-run')) return Response.json({ run_id: 'old-run', connector_id: 'old-document', status: 'ready', manifest: fixtures.cases[0].manifest, lease_expires_at: '2099-01-01T00:00:00Z', lease_remaining_ms: 15000, stop_confirmed: false, result_saved: false })
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  await userEvent.click(await screen.findByRole('button', { name: '查看已有运行状态' }))
  expect(await screen.findByText(/本标签页没有可用控制权/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '开始训练' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '停止并确认熄灯' })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '刷新状态' }))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5100)) })
  expect(writes).toEqual([])
}, 10000)

it('continues pending status reads but stops periodic run reads after non-result uncertainty archive without writes', async () => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  const runId = '11111111-1111-4111-8111-111111111111', connectorId = '22222222-2222-4222-8222-222222222222'
  const proof = documentControl.forAccount(studentFixture.account_id)
  proof.connectorId = connectorId; proof.runId = runId
  let runReads = 0
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') { writes.push(url); return Response.json({}) }
    if (url.endsWith(`/runs/${runId}`)) {
      runReads++
      return Response.json({ run_id: runId, connector_id: connectorId, status: runReads === 1 ? 'awaiting_sync' : 'uncertain_closed', manifest: fixtures.cases[0].manifest, lease_expires_at: '2026-11-04T00:00:00.000Z', lease_remaining_ms: 0, stop_confirmed: true, result_saved: false })
    }
    if (url.endsWith('/connectors')) return Response.json([{ connector_id: connectorId, name: '合成归档连接器', online: true, compatible: true, locally_confirmed: true, protocol_version: 1, connector_version: '0.3.0', devices: [], active_run_id: runId, attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [] }])
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  await waitFor(() => expect(runReads).toBe(2), { timeout: 3500 })
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 2300)) })
  expect(runReads).toBe(2)
  expect(writes).toEqual([])
}, 8000)

it.each(['start', 'manual-activate'])('prioritizes Stop over a pending %s and keeps late responses from restoring stimulation or lease', async pendingKind => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  const proof = documentControl.forAccount(studentFixture.account_id)
  proof.connectorId = 'priority-local'; proof.runId = 'priority-run'
  let pendingSignal: AbortSignal | null | undefined
  let finishPending!: (response: Response) => void
  let finishStop!: (response: Response) => void
  const pendingResponse = new Promise<Response>(resolve => { finishPending = resolve })
  const stopResponse = new Promise<Response>(resolve => { finishStop = resolve })
  const writes: string[] = []
  let state = pendingKind === 'start' ? 'ready' : 'running'
  let pollFailure = false
  let polls = 0
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') {
      writes.push(url)
      if (url.endsWith(`/${pendingKind}`)) { pendingSignal = init.signal; return pendingResponse }
      if (url.endsWith('/stop')) return stopResponse
      return Response.json({ lease_remaining_ms: 15000, renew_after_ms: 5000 })
    }
    if (url.endsWith('/connectors')) return Response.json([{ connector_id: 'priority-local', name: '合成连接器', online: true, compatible: true, locally_confirmed: true, protocol_version: 1, connector_version: '0.3.0', devices: [], active_run_id: 'priority-run', attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [] }])
    if (url.endsWith('/runs/priority-run')) { polls++; return pollFailure ? Response.json({}, { status: 503 }) : Response.json({ run_id: 'priority-run', connector_id: 'priority-local', status: state, manifest: fixtures.cases[0].manifest, lease_expires_at: '2099-01-01T00:00:00Z', lease_remaining_ms: 15000, stop_confirmed: false, result_saved: false }) }
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  const account = { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] }
  const mounted = renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account })
  await userEvent.click(await screen.findByRole('button', { name: pendingKind === 'start' ? '开始训练' : '点亮第 1 位 · 灯 1' }))
  await waitFor(() => expect(writes.some(url => url.endsWith(`/${pendingKind}`))).toBe(true))
  if (pendingKind === 'start') {
    pollFailure = true
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 5100)) })
    expect(writes.some(url => url.endsWith('/lease'))).toBe(false)
  }
  const stop = screen.getByRole('button', { name: '停止并确认熄灯' })
  expect(stop).toBeEnabled()
  await userEvent.click(stop)
  pollFailure = false
  await waitFor(() => expect(writes.some(url => url.endsWith('/stop'))).toBe(true))
  expect(pendingSignal?.aborted).toBe(true)
  state = 'running' // Deliberately stale/racing status, never permission to resume.
  await act(async () => { finishPending(Response.json({ run_status: 'running' })); await pendingResponse })
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5100)) })
  expect(polls).toBeGreaterThan(1)
  expect(writes.some(url => url.endsWith('/lease'))).toBe(false)
  expect(screen.queryByRole('button', { name: '开始训练' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /点亮第/ })).not.toBeInTheDocument()
  state = 'unconfirmed'
  await act(async () => { finishStop(Response.json({ detail: 'private-stop-failure' }, { status: 503 })); await stopResponse })
  expect(await screen.findByRole('alert')).toHaveTextContent('请求失败')
  expect(screen.getByRole('button', { name: '停止并确认熄灯' })).toBeEnabled()
  expect(screen.queryByText('private-stop-failure')).not.toBeInTheDocument()
  mounted.unmount()
  state = 'running'
  renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account })
  expect(await screen.findByText(/已禁止新刺激和租约续期/)).toBeVisible()
  expect(screen.queryByRole('button', { name: /点亮第|开始训练/ })).not.toBeInTheDocument()
}, 18000)

it('clears private run data and document control after a denied status poll without restoring stimuli or lease', async () => {
  Object.defineProperty(navigator, 'userAgent', { configurable: true, value: 'Windows NT 10.0' })
  const proof = documentControl.forAccount(studentFixture.account_id)
  proof.connectorId = 'private-local'; proof.runId = 'private-run'
  let denied = false
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') writes.push(url)
    if (url.endsWith('/connectors')) return Response.json([{ connector_id: 'private-local', name: 'Private synthetic connector', online: true, compatible: true, locally_confirmed: true, protocol_version: 1, connector_version: '0.3.0', devices: [], active_run_id: 'private-run', attention_required: false, unsafe_operation_id: null, unsafe_device_keys: [] }])
    if (url.endsWith('/runs/private-run')) return denied ? Response.json({}, { status: 403 }) : Response.json({ run_id: 'private-run', connector_id: 'private-local', status: 'running', manifest: fixtures.cases[0].manifest, lease_expires_at: '2099-01-01T00:00:00Z', lease_remaining_ms: 15000, stop_confirmed: false, result_saved: false })
    return Response.json(url.includes('/uncertainties?') ? { items: [], total: 0, offset: 0, limit: 20 } : url.endsWith('/releases/current') ? noPackage : [])
  }))
  const { queryClient } = renderAssessmentPage(<ReactionxPage />, { initialEntries: ['/'], account: { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] } })
  expect(await screen.findByRole('button', { name: '点亮第 1 位 · 灯 1' })).toBeVisible()
  denied = true
  await userEvent.click(screen.getByRole('button', { name: '刷新状态' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('记录不存在或无权操作')
  await waitFor(() => expect(queryClient.getQueryCache().getAll().some(query => query.state.data && JSON.stringify(query.state.data).includes('private-run'))).toBe(false))
  expect(documentControl.isCurrent(proof)).toBe(false)
  expect(screen.queryByRole('button', { name: /点亮第|开始训练|停止并确认熄灯/ })).not.toBeInTheDocument()
  expect(screen.queryByText('Private synthetic connector')).not.toBeInTheDocument()
  denied = false
  await userEvent.click(screen.getByRole('button', { name: '刷新状态' }))
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 5100)) })
  expect(writes).toEqual([])
  expect(screen.queryByRole('button', { name: /点亮第|开始训练|停止并确认熄灯/ })).not.toBeInTheDocument()
}, 10000)
