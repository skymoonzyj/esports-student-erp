import { act, cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, expect, it, vi } from 'vitest'
import { UncertaintyPanel } from './UncertaintyPanel'
import { renderAssessmentPage, studentFixture } from '../esportsAssessments/testSupport/renderAssessmentPage'

const run = '11111111-1111-4111-8111-111111111111', digest = 'a'.repeat(64)
const request = { request_id: '22222222-2222-4222-8222-222222222222', run_id: run, declaration_digest: digest, expires_at: '2099-01-01T00:00:00.000Z' }
const receipt = { version: 1, archive_id: '33333333-3333-4333-8333-333333333333', run_id: run, connector_id: '44444444-4444-4444-8444-444444444444', manifest_digest: 'b'.repeat(64), declaration_digest: digest, stop_operation_id: '55555555-5555-4555-8555-555555555555', status: 'execution_unknown', closed_at: '2026-11-04T00:00:00.000Z' }
const row = { run_id: run, declaration_digest: digest, subject_name: '合成参测者', mode: 'manual', created_at: '2026-11-04T00:00:00Z', state: 'pending_review' }
const account = { ...studentFixture, is_super_admin: true, permissions: ['reactionx.manage', 'reactionx.conduct'], scoped_permissions: [] }
const page = { items: [row], total: 1, offset: 0, limit: 20 }
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers() })
function mount() { return renderAssessmentPage(<UncertaintyPanel />, { initialEntries: ['/'], account }) }
async function openConfirmation() {
  await userEvent.click(await screen.findByRole('button', { name: '核对并填写原因' }))
  await userEvent.type(screen.getByLabelText('异常归档原因'), '原连接器恢复后人工核对原始证据')
  await userEvent.click(screen.getByRole('button', { name: '发起归档核对' }))
  return screen.findByRole('dialog', { name: '再次确认异常归档' })
}

it.each([
  { ...account, is_super_admin: false },
  { ...account, permissions: ['reactionx.manage'] },
  { ...account, permissions: ['reactionx.conduct'] },
])('makes no private requests without all actual-superadmin/manage/conduct guards (%j)', async denied => {
  const urls: string[] = []
  vi.stubGlobal('fetch', vi.fn(async input => { urls.push(String(input)); return Response.json(page) }))
  renderAssessmentPage(<UncertaintyPanel />, { initialEntries: ['/'], account: denied })
  await act(async () => { await Promise.resolve() })
  expect(screen.queryByRole('heading', { name: '执行未知待核对' })).not.toBeInTheDocument()
  expect(urls).toEqual([])
})

it('shows minimal original facts and requires two separate steps without claiming result or local delivery', async () => {
  const writes: { url: string; body: unknown }[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') { writes.push({ url, body: JSON.parse(String(init.body)) }); return Response.json(url.endsWith('/request') ? request : receipt) }
    return Response.json(page)
  }))
  mount()
  expect(await screen.findByText('合成参测者')).toBeVisible()
  expect(screen.getByText(run)).toBeVisible()
  expect(screen.getByText(digest)).toBeVisible()
  expect(screen.queryByText(/第 0 名|成绩已保存|分数：/)).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole('button', { name: '核对并填写原因' }))
  expect(screen.getByRole('button', { name: '发起归档核对' })).toBeDisabled()
  await userEvent.type(screen.getByLabelText('异常归档原因'), '原连接器恢复后人工核对原始证据')
  await userEvent.click(screen.getByRole('button', { name: '发起归档核对' }))
  const dialog = await screen.findByRole('dialog', { name: '再次确认异常归档' })
  expect(writes).toHaveLength(1)
  expect(within(dialog).getByText(/原设备实际关灯/)).toBeVisible()
  await userEvent.click(within(dialog).getByRole('button', { name: '确认归档为执行未知' }))
  expect(await screen.findByText('执行状态未知，需复测')).toBeVisible()
  expect(screen.getByText(/后台已归档/)).toBeVisible()
  expect(screen.getByText(/尚不能确认原连接器已收到并持久化/)).toBeVisible()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(writes.map(write => write.body)).toEqual([{ declaration_digest: digest, reason: '原连接器恢复后人工核对原始证据', confirmation: true }, { request_id: request.request_id, confirmation: true }])
})

it('does not allow duplicate writes while either step is pending', async () => {
  let resolveRequest!: (response: Response) => void, resolveConfirm!: (response: Response) => void
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method !== 'POST') return Response.json(page)
    writes.push(url)
    return new Promise<Response>(resolve => { if (url.endsWith('/request')) resolveRequest = resolve; else resolveConfirm = resolve })
  }))
  mount()
  await userEvent.click(await screen.findByRole('button', { name: '核对并填写原因' }))
  await userEvent.type(screen.getByLabelText('异常归档原因'), '原连接器恢复后人工核对原始证据')
  await userEvent.dblClick(screen.getByRole('button', { name: '发起归档核对' }))
  expect(writes).toHaveLength(1)
  expect(screen.getByRole('button', { name: '发起归档核对' })).toBeDisabled()
  await act(async () => resolveRequest(Response.json(request)))
  await userEvent.dblClick(await screen.findByRole('button', { name: '确认归档为执行未知' }))
  expect(writes).toHaveLength(2)
  expect(screen.getByRole('button', { name: '确认归档为执行未知' })).toBeDisabled()
  await act(async () => resolveConfirm(Response.json(receipt)))
  expect(await screen.findByText('执行状态未知，需复测')).toBeVisible()
})

it('clears private records and invalidates the confirmation on a server 403', async () => {
  vi.stubGlobal('fetch', vi.fn(async (input, init) => init?.method === 'POST' ? Response.json(String(input).endsWith('/request') ? request : { code: 'reactionx_uncertainty_conflict', detail: 'PRIVATE SECRET' }, { status: String(input).endsWith('/request') ? 200 : 403 }) : Response.json(page)))
  const { queryClient } = mount()
  const dialog = await openConfirmation()
  await userEvent.click(within(dialog).getByRole('button', { name: '确认归档为执行未知' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('无权操作')
  expect(screen.queryByText('合成参测者')).not.toBeInTheDocument()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText('PRIVATE SECRET')).not.toBeInTheDocument()
  await waitFor(() => expect(queryClient.getQueryCache().getAll().filter(query => query.queryKey.includes('uncertainties'))).toHaveLength(0))
})

it.each(['reactionx-auth-boundary', 'reactionx-before-logout'])('aborts late responses and clears private state on %s', async event => {
  let finish!: (response: Response) => void, signal: AbortSignal | null | undefined
  vi.stubGlobal('fetch', vi.fn(async (_input, init) => {
    if (init?.method !== 'POST') return Response.json(page)
    signal = init.signal
    return new Promise<Response>(resolve => { finish = resolve })
  }))
  const { queryClient } = mount()
  await userEvent.click(await screen.findByRole('button', { name: '核对并填写原因' }))
  await userEvent.type(screen.getByLabelText('异常归档原因'), '原连接器恢复后人工核对原始证据')
  await userEvent.click(screen.getByRole('button', { name: '发起归档核对' }))
  act(() => window.dispatchEvent(new CustomEvent(event, { detail: event.endsWith('boundary') ? 'other-account' : [] })))
  expect(signal?.aborted).toBe(true)
  expect(screen.queryByText('合成参测者')).not.toBeInTheDocument()
  await act(async () => finish(Response.json(request)))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(queryClient.getQueryCache().getAll().filter(query => query.queryKey.includes('uncertainties'))).toHaveLength(0)
})

it('requires a new first step when a confirmation expires in the open page', async () => {
  vi.stubGlobal('fetch', vi.fn(async (_input, init) => Response.json(init?.method === 'POST' ? { ...request, expires_at: new Date(Date.now() + 500).toISOString() } : page)))
  mount(); await openConfirmation()
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  expect(screen.getByRole('alert')).toHaveTextContent('已过期')
  expect(screen.queryByRole('button', { name: '确认归档为执行未知' })).not.toBeInTheDocument()
})

it('refreshes on foreground and discards old confirmation and stale identity before showing changed evidence', async () => {
  let changed = false
  vi.stubGlobal('fetch', vi.fn(async (_input, init) => Response.json(init?.method === 'POST' ? request : changed ? { ...page, items: [{ ...row, subject_name: '更新后授权参测者', declaration_digest: 'c'.repeat(64) }] } : page)))
  mount(); await openConfirmation(); changed = true
  act(() => window.dispatchEvent(new Event('focus')))
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(await screen.findByText('更新后授权参测者')).toBeVisible()
  expect(screen.queryByText('合成参测者')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('异常归档原因')).not.toBeInTheDocument()
})

it.each([
  { status: 409, message: '原连接器确认原设备实际关灯' },
  { status: 503, message: '状态尚未确认' },
  { status: 422, message: '有效处理原因' },
])('does not reuse a request after server rejection $status and never supplies a bypass', async ({ status, message }) => {
  let fail = true
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input)
    if (init?.method !== 'POST') return Response.json(page)
    writes.push(url)
    if (url.endsWith('/request')) return Response.json(request)
    return fail ? Response.json({ code: 'reactionx_uncertainty_conflict', detail: 'private device address' }, { status }) : Response.json(receipt)
  }))
  mount(); const dialog = await openConfirmation()
  await userEvent.click(within(dialog).getByRole('button', { name: '确认归档为执行未知' }))
  expect(await screen.findByRole('alert')).toHaveTextContent(message)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByText('private device address')).not.toBeInTheDocument()
  expect(screen.queryByText('执行状态未知，需复测')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /绕过|强制归档/ })).not.toBeInTheDocument()
  fail = false
  await userEvent.click(screen.getByRole('button', { name: '刷新执行未知记录' }))
  await openConfirmation()
  expect(writes.filter(url => url.endsWith('/request'))).toHaveLength(2)
})

it('never renders stale candidate identity after a failed list refresh', async () => {
  let fail = false
  vi.stubGlobal('fetch', vi.fn(async () => fail ? Response.json({}, { status: 503 }) : Response.json(page)))
  mount(); expect(await screen.findByText('合成参测者')).toBeVisible()
  fail = true
  await userEvent.click(screen.getByRole('button', { name: '刷新执行未知记录' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('状态尚未确认')
  expect(screen.queryByText('合成参测者')).not.toBeInTheDocument()
})

it('discards old request identity if another query refresh replaces the original evidence', async () => {
  let changed = false
  vi.stubGlobal('fetch', vi.fn(async (_input, init) => Response.json(init?.method === 'POST' ? request : changed ? { ...page, items: [{ ...row, declaration_digest: 'c'.repeat(64) }] } : page)))
  const { queryClient } = mount(); await openConfirmation(); changed = true
  await act(async () => { await queryClient.invalidateQueries({ queryKey: ['reactionx'] }) })
  expect(await screen.findByText('c'.repeat(64))).toBeVisible()
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('异常归档原因')).not.toBeInTheDocument()
})
