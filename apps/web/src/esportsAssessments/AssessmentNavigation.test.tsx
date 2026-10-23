import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useEffect } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { setCsrfToken } from '../api/client'
import { AssessmentHost } from './AssessmentHost'
import { useAssessmentSession } from './useAssessmentSession'
import { fixtureAttempt, fixtureConfig, fixturePending, fixtureRaw, fixtureSuiteConfig } from './testSupport/fixtures'
import { pcMobaFactory } from './suites/pcMoba'
import { clearAssessmentPending, loadPending, savePending } from './pending'

const account = '11111111-1111-4111-8111-111111111111'
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { account_id: '11111111-1111-4111-8111-111111111111' } }) }))
vi.mock('./moba/MobaStage', () => ({ MobaStage: ({ canvasRef, inputHandlers, onReady }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object; onReady: () => void }) => {
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="MOBA 测评场景" {...inputHandlers} />
} }))
const config = fixtureConfig('pc-moba', 'dodge')
const view = fixtureAttempt({ suite_id: config.suite_id, test_id: config.test_id, config, settings: { sensitivity: 1, input_kind: 'mouse', mouse_mode: null, controls: [] } })
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
let failInterrupt = false
let startReply: ((response: Response) => void) | undefined
let delayStart = false
let interrupted = false
function Page() {
  const session = useAssessmentSession({ mode: 'formal', suite_id: config.suite_id, session_id: view.session_id!, test_id: config.test_id })
  return <AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={() => undefined} />
}
function mount() {
  const router = createMemoryRouter([{ path: '/list', element: <h1>项目列表</h1> }, { path: '/test', element: <Page /> }, { path: '/esports-assessments/reports/:id', element: <h1>完整报告</h1> }], { initialEntries: ['/list', '/test'], initialIndex: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
  return router
}
async function trial() {
  await waitFor(() => expect(screen.getByText('检查设备并进入试练')).toBeEnabled())
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
}
beforeEach(() => {
  failInterrupt = false; delayStart = false; interrupted = false; startReply = undefined
  clearAssessmentPending(); setCsrfToken('test')
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) })
  vi.stubGlobal('requestAnimationFrame', () => 1)
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/interrupt')) {
      if (failInterrupt) return json({ detail: 'offline' }, 500)
      interrupted = true; return json({ ...view, state: 'interrupted' })
    }
    if (init?.method === 'POST') return delayStart ? new Promise(resolve => { startReply = resolve }) : json(view)
    return url.includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: { ...view, state: interrupted ? 'interrupted' : 'running' }, saved: null })
  }))
})
afterEach(() => { clearAssessmentPending(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('blocks browser Back during trial; Continue retains the stage and Exit performs the requested navigation', async () => {
  const router = mount()
  await trial()
  const canvas = screen.getByLabelText('MOBA 测评场景')
  await act(() => router.navigate(-1))
  expect(screen.getByRole('dialog', { name: '离开当前测试？' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '继续测试' }))
  expect(router.state.location.pathname).toBe('/test')
  expect(screen.getByLabelText('MOBA 测评场景')).toBe(canvas)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'trial')
  await act(() => router.navigate(-1))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认退出' })) })
  expect(screen.getByRole('heading', { name: '项目列表' })).toBeInTheDocument()
  expect(interrupted).toBe(false)
})

it('keeps a failed formal exit on the test route and permits retry after reconciliation succeeds', async () => {
  const router = mount()
  await trial(); fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  failInterrupt = true
  await act(() => router.navigate(-1))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认退出' })) })
  expect(router.state.location.pathname).toBe('/test')
  expect(screen.getByRole('dialog')).toHaveTextContent('退出未完成')
  expect(interrupted).toBe(false)
  failInterrupt = false
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认退出' })) })
  expect(router.state.location.pathname).toBe('/list')
  expect(interrupted).toBe(true)
})

it('keeps starting blocked until its late response is interrupted', async () => {
  const router = mount()
  await trial(); fireEvent.click(screen.getByText('结束试练'))
  delayStart = true
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  await act(() => router.navigate(-1))
  fireEvent.click(screen.getByRole('button', { name: '确认退出' }))
  expect(router.state.location.pathname).toBe('/test')
  await act(async () => { startReply!(json(view)) })
  await waitFor(() => expect(router.state.location.pathname).toBe('/list'))
  expect(interrupted).toBe(true)
})

it('allows a confirmed pending exit without deleting the recoverable result or interrupting it', async () => {
  const pending = fixturePending({ account_id: account, suite_id: config.suite_id, test_id: config.test_id, attempt_id: view.id, session_id: view.session_id, raw: fixtureRaw(config.suite_id, config.test_id), created_at: new Date().toISOString() })
  savePending(pending)
  const router = mount()
  await waitFor(() => expect(screen.getByText('手动重试保存')).toBeEnabled())
  await act(() => router.navigate(-1))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认退出' })) })
  expect(router.state.location.pathname).toBe('/list')
  expect(loadPending(account)).toEqual([pending])
  expect(interrupted).toBe(false)
})

it('warns before reload only while an unsaved assessment is active', async () => {
  mount()
  const intro = new Event('beforeunload', { cancelable: true })
  fireEvent(window, intro)
  expect(intro.defaultPrevented).toBe(false)
  await trial()
  const active = new Event('beforeunload', { cancelable: true })
  fireEvent(window, active)
  expect(active.defaultPrevented).toBe(true)
  fireEvent.click(screen.getByText('结束试练'))
  const complete = new Event('beforeunload', { cancelable: true })
  fireEvent(window, complete)
  expect(complete.defaultPrevented).toBe(false)
})

it('does not offer a destructive exit when pending storage is volatile', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  const pending = fixturePending({ account_id: account, suite_id: config.suite_id, test_id: config.test_id, attempt_id: view.id, session_id: view.session_id, raw: fixtureRaw(config.suite_id, config.test_id), created_at: new Date().toISOString() })
  savePending(pending)
  const router = mount()
  await waitFor(() => expect(screen.getByText('手动重试保存')).toBeEnabled())
  await act(() => router.navigate(-1))
  expect(screen.getByRole('button', { name: '确认退出' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '继续测试' }))
  expect(router.state.location.pathname).toBe('/test')
  expect(loadPending(account)).toEqual([pending])
  expect(interrupted).toBe(false)
})

it('still cleans up a later delayed start after a previously failed exit was cancelled', async () => {
  const router = mount()
  await trial(); fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  failInterrupt = true
  await act(() => router.navigate(-1))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认退出' })) })
  fireEvent.click(screen.getByRole('button', { name: '继续测试' }))
  failInterrupt = false
  await act(async () => { fireEvent.click(screen.getByText('结束未完成尝试并重新开始')) })
  expect(interrupted).toBe(true)
  interrupted = false; delayStart = true
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  fireEvent(window, new Event('blur'))
  await act(async () => { startReply!(json(view)) })
  await waitFor(() => expect(interrupted).toBe(true))
})

it('automatically opens the final report after the fourth saved result without a stale pending exit dialog', async () => {
  const pending = fixturePending({ account_id: account, suite_id: config.suite_id, test_id: config.test_id, attempt_id: view.id, session_id: view.session_id, raw: fixtureRaw(config.suite_id, config.test_id), created_at: new Date().toISOString() })
  savePending(pending)
  const reportId = '77777777-7777-4777-8777-777777777777'
  const resultId = '55555555-5555-4555-8555-555555555555'
  const scored = { metrics: { accuracy: 1 }, score: 100, grade: 'high', quality_flags: [] }
  const members = ['last-hit', 'orb-walk', 'dodge', 'map-awareness']
  const saved = { mode: 'formal', result_id: resultId, scored, session: { id: view.session_id, suite_id: view.suite_id, suite_config: fixtureSuiteConfig(view.suite_id), status: 'completed', members, results: members.map((test_id, index) => ({ test_id, result_id: test_id === 'dodge' ? resultId : `66666666-6666-4666-8666-66666666666${index}`, scored })), report_id: reportId } }
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => String(input).endsWith('/result') ? json(saved) : json({ attempt: view, saved: null })))
  const router = mount()
  await waitFor(() => expect(screen.getByText('手动重试保存')).toBeEnabled())
  await act(async () => { fireEvent.click(screen.getByText('手动重试保存')) })
  expect(loadPending(account)).toEqual([])
  expect(router.state.location.pathname).toBe(`/esports-assessments/reports/${reportId}`)
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  expect(loadPending(account)).toEqual([])
})

it.each([401, 403])('does not trap a revoked session on the route after exit receives HTTP %s', async status => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith('/interrupt')) return json({ detail: 'denied' }, status)
    if (init?.method === 'POST') return json(view)
    return url.includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: view, saved: null })
  }))
  const router = mount()
  await trial(); fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  await act(() => router.navigate(-1))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认退出' })) })
  expect(router.state.location.pathname).toBe('/list')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
})
