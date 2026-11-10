import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { useEffect } from 'react'
import { Routes, Route } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AssessmentSessionPage } from './AssessmentSessionPage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import catalog from './testSupport/catalog-v1.json'
import currentCatalog from './testSupport/catalog-v2.json'
import catalogV3 from './testSupport/catalog-v3.json'
import catalogV5 from './testSupport/catalog-v5.json'
import laneV4 from './testSupport/catalog-moba-v4.json'
import laneV5 from './testSupport/catalog-moba-v5.json'
import { fixtureV4SuiteConfig } from './testSupport/fixturesV4'
import { fixtureAttempt, fixturePending, fixtureRaw, fixtureSuiteConfig } from './testSupport/fixtures'
import { loadPending, savePending } from './pending'
import { suiteConfigSchema, type SessionView } from './contracts'
import { pcFpsFactory } from './suites/pcFps'
import { mobileFpsFactory } from './suites/mobileFps'
import type * as FpsStageExports from './fps/FpsStage'

vi.mock('./availability', () => ({ ASSESSMENT_SUITE_AVAILABILITY: { 'pc-fps': true, 'mobile-fps': true, 'pc-moba': true, 'mobile-moba': true } }))
vi.mock('./moba/MobaStage', () => ({ MobaStage: ({ canvasRef, onReady }: { canvasRef: React.Ref<HTMLCanvasElement>; onReady: () => void }) => {
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="MOBA 测评场景" />
} }))
vi.mock('./fps/FpsStage', async importOriginal => ({ ...await importOriginal<typeof FpsStageExports>(), FpsStage: ({ canvasRef, onReady }: { canvasRef: React.Ref<HTMLCanvasElement>; onReady: () => void }) => {
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="FPS 测试场景" />
} }))
const id = '33333333-3333-4333-8333-333333333333'
const session: SessionView = { id, suite_id: 'pc-fps', suite_config: fixtureSuiteConfig('pc-fps'), status: 'in_progress', members: ['precision', 'tracking', 'switching', 'recognition'], results: [], report_id: null }
function mount(canTake = true, routeId = id, view = session, catalogAvailable = true) {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
    const path = String(input); requests.push(path)
    const data = path.endsWith('/context') ? { server_now: '2026-09-21T00:00:00Z', can_take: canTake, can_read_self: true, can_read_reports: true, available_suites: [view.suite_id], own_in_progress_sessions: view.status === 'in_progress' ? [view] : [], active_run: null }
      : path.endsWith('/suites') ? { items: catalogAvailable ? [currentCatalog[view.suite_id]] : [] } : path.includes('/recovery?') ? { items: [], next_cursor: null, visible_total: 0 } : view
    return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
  }))
  const mounted = renderAssessmentPage(<Routes><Route path="/esports-assessments/sessions/:sessionId" element={<AssessmentSessionPage />} /></Routes>, { initialEntries: [`/esports-assessments/sessions/${routeId}`], account: studentFixture })
  return { requests, ...mounted }
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); Reflect.deleteProperty(document, 'pointerLockElement'); Reflect.deleteProperty(document, 'exitPointerLock') })
it('allows any unsaved item first and does not increase progress on selection', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '敌我识别' }))
  expect(await screen.findByRole('heading', { name: '敌我识别' })).toBeVisible()
  fireEvent.click(screen.getByText('返回项目列表'))
  expect(await screen.findByText('已保存 0 / 4')).toBeVisible()
})
it('shows exactly the frozen three v4 tasks and a three-item completion denominator', async () => {
  const frozen = fixtureV4SuiteConfig()
  mount(true, id, { ...session, suite_config: frozen, members: frozen.test_ids })
  expect(await screen.findByText('已保存 0 / 3')).toBeVisible()
  for (const name of ['网格射击', '六目标射击', '90°范围跟枪']) expect(screen.getByRole('button', { name })).toBeVisible()
  expect(screen.queryByRole('button', { name: '敌我识别' })).toBeNull()
  expect(screen.getByText(/本轮保留旧版三项规则/)).toBeVisible()
  expect(screen.queryByText(/旧版四项/)).toBeNull()
})
it.each([laneV4, laneV5])('keeps $catalog_version a single five-minute lane task without a legacy four-item warning', async source => {
  const frozen = suiteConfigSchema.parse(source)
  mount(true, id, { ...session, suite_id: 'pc-moba', suite_config: frozen, members: frozen.test_ids })
  expect(await screen.findByText('已保存 0 / 1')).toBeVisible()
  expect(screen.getByRole('button', { name: '五分钟兵线补刀' })).toBeVisible()
  expect(screen.getByText(/本轮只有一个五分钟兵线补刀/)).toBeVisible()
  expect(screen.queryByText(/旧版四项|每项正式运行 60 秒/)).toBeNull()
})
it('shows the v5 fixed-board triad without a legacy four-item warning', async () => {
  const frozen = suiteConfigSchema.parse(catalogV5['pc-fps'])
  mount(true, id, { ...session, suite_config: frozen, members: frozen.test_ids })
  expect(await screen.findByText('已保存 0 / 3')).toBeVisible()
  expect(screen.queryByText(/旧版四项|旧版三项/)).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '网格射击' }))
  expect(await screen.findByText(/目标固定在靶区内，视角到边缘会停止转动/)).toBeVisible()
  expect(screen.queryByText(/旧版四项/)).toBeNull()
})
it.each(['v1', 'v2', 'v3'] as const)('explains that an old desktop %s round remains frozen rather than silently becoming the new triad', async version => {
  const snapshot = version === 'v3' ? suiteConfigSchema.parse(catalogV3['pc-fps']) : fixtureSuiteConfig('pc-fps', version === 'v1' ? 'v1' : 'current')
  mount(true, id, { ...session, suite_config: snapshot })
  expect(await screen.findByText(/本轮保留旧版四项规则/)).toBeVisible()
  expect(screen.getByText(/不会自动删除或替换已保存成绩/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '定位精度' }))
  expect(await screen.findByText(/本轮保留旧版四项规则/)).toBeVisible()
})
it('does not offer a host after effective participation permission is revoked', async () => {
  mount(false)
  expect(await screen.findByText(/没有参加本专项的权限/)).toBeVisible()
  expect(screen.queryByRole('button', { name: '敌我识别' })).toBeNull()
})
it('does not use a frozen session snapshot to bypass a missing live catalog item', async () => {
  const { requests } = mount(true, `${id}?test=precision`, session, false)
  expect(await screen.findByRole('heading', { name: '此专项暂未开放' })).toBeVisible()
  expect(screen.queryByRole('region', { name: '测试场景' })).toBeNull()
  expect(screen.queryByRole('button', { name: '检查设备并进入试练' })).toBeNull()
  expect(requests.some(path => path.endsWith('/attempts'))).toBe(false)
})
it('asks for a refresh when the live catalog version is unknown', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
    const path = String(input)
    const data = path.endsWith('/context')
      ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-fps'], own_in_progress_sessions: [session], active_run: null }
      : path.endsWith('/suites')
        ? { items: [{ ...currentCatalog['pc-fps'], catalog_version: 'pc-fps-v99' }] }
        : session
    return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
  }))
  renderAssessmentPage(<Routes><Route path="/esports-assessments/sessions/:sessionId" element={<AssessmentSessionPage />} /></Routes>, { initialEntries: [`/esports-assessments/sessions/${id}`], account: studentFixture })
  expect(await screen.findByRole('alert')).toHaveTextContent('评测目录版本无法识别或暂时不可用，请刷新页面后重试。')
  expect(screen.getByRole('button', { name: '刷新目录' })).toBeVisible()
})
it('rejects malformed route identity without requesting a session', async () => {
  const { requests } = mount(true, 'not-a-uuid')
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('轮次不存在或无权访问'))
  expect(requests.some(path => path.includes('/sessions/'))).toBe(false)
})

it('keeps an active host mounted across final-session invalidation so its guarded report navigation can finish', async () => {
  const { queryClient } = mount()
  fireEvent.click(await screen.findByRole('button', { name: '敌我识别' }))
  expect(await screen.findByRole('heading', { name: '敌我识别' })).toBeVisible()
  await act(async () => {
    queryClient.setQueryData(['esports-assessments', studentFixture.account_id, 'formal-session', id], { ...session, status: 'completed', report_id: '66666666-6666-4666-8666-666666666666' })
    await new Promise(resolve => setTimeout(resolve, 0))
  })
  expect(screen.getByRole('heading', { name: '敌我识别' })).toBeVisible()
})

it.each([false, true])('reconciles a fresh selected saved item after a lost response (final item: %s) before removing only its pending result', async final => {
  const pending = fixturePending({ created_at: new Date().toISOString() })
  const unrelated = fixturePending({ created_at: new Date().toISOString(), attempt_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', operation_key: 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', test_id: 'tracking', raw: fixtureRaw('pc-fps', 'tracking') })
  expect(savePending(pending).persisted).toBe(true)
  expect(savePending(unrelated).persisted).toBe(true)
  const scored = { metrics: { shots: 17 }, score: 42, grade: 'medium', quality_flags: [] }
  const resultId = '55555555-5555-4555-8555-555555555555'
  const reportId = '66666666-6666-4666-8666-666666666666'
  const view = { ...session, status: final ? 'completed' : 'in_progress', report_id: final ? reportId : null,
    results: (final ? session.members : ['precision']).map((test_id, index) => ({ test_id, result_id: index === 0 ? resultId : `55555555-5555-4555-8555-55555555555${index}`, scored })) }
  const saved = { mode: 'formal', result_id: resultId, scored, session: view }
  const requests: { path: string; method: string }[] = []
  let confirmSaved!: () => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const path = String(input)
    requests.push({ path, method: init?.method ?? 'GET' })
    if (path.endsWith(`/attempts/${pending.attempt_id}`)) return new Promise<Response>(resolve => {
      confirmSaved = () => resolve(new Response(JSON.stringify({ attempt: fixtureAttempt({ state: 'submitted' }), saved }), { headers: { 'Content-Type': 'application/json' } }))
    })
    const data = path.endsWith('/context') ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-fps'], own_in_progress_sessions: final ? [] : [view], active_run: null }
      : path.endsWith('/suites') ? { items: [catalog['pc-fps']] } : path.endsWith(`/sessions/${id}`) ? view : null
    if (!data || (init?.method && init.method !== 'GET')) throw new Error(`unexpected request: ${path}`)
    return new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } })
  }))
  renderAssessmentPage(<Routes><Route path="/esports-assessments/sessions/:sessionId" element={<AssessmentSessionPage />} /><Route path="/esports-assessments/reports/:reportId" element={<h1>已进入正式报告</h1>} /></Routes>, { initialEntries: [`/esports-assessments/sessions/${id}?test=precision`], account: studentFixture })
  await waitFor(() => expect(requests.some(request => request.path.endsWith(`/attempts/${pending.attempt_id}`))).toBe(true))
  expect(loadPending(studentFixture.account_id)).toHaveLength(2)
  expect(screen.queryByRole('button', { name: '检查设备并进入试练' })).toBeNull()
  expect(screen.getByRole('button', { name: '手动重试保存' })).toBeDisabled()
  expect(screen.queryByRole('region', { name: '结果已保存' })).toBeNull()
  expect(screen.queryByText('已进入正式报告')).toBeNull()
  await act(async () => { confirmSaved() })
  if (final) expect(await screen.findByText('已进入正式报告')).toBeVisible()
  else {
    const result = await screen.findByRole('region', { name: '结果已保存' })
    expect(within(result).getByText('射击次数')).toBeVisible()
    expect(within(result).getByText('17 次')).toBeVisible()
    expect(within(result).getByText(/42 分/)).toBeVisible()
    fireEvent.click(within(result).getByRole('button', { name: '继续下一项' }))
    expect(await screen.findByText('已保存 1 / 4')).toBeVisible()
  }
  expect(loadPending(studentFixture.account_id)).toEqual([unrelated])
  expect(requests.filter(request => request.method !== 'GET')).toEqual([])
})

it.each([[false, false], [false, true], [true, false], [true, true]])('keeps the existing completed-item entry without matching pending (final item: %s, unrelated cache: %s)', async (final, withUnrelated) => {
  const unrelated = fixturePending({ created_at: new Date().toISOString(), test_id: 'tracking', raw: fixtureRaw('pc-fps', 'tracking') })
  if (withUnrelated) savePending(unrelated)
  const reportId = '66666666-6666-4666-8666-666666666666'
  const view: SessionView = { ...session, status: final ? 'completed' : 'in_progress', report_id: final ? reportId : null,
    results: (final ? session.members : ['precision'] as const).map((test_id, index) => ({ test_id, result_id: `55555555-5555-4555-8555-55555555555${index}`, scored: { metrics: { shots: 17 }, score: 42, grade: 'medium', quality_flags: [] } })) }
  const { requests } = mount(true, `${id}?test=precision`, view)
  if (final) expect(await screen.findByRole('link', { name: '查看正式报告' })).toHaveAttribute('href', `/esports-assessments/reports/${reportId}`)
  else expect(await screen.findByText('已保存 1 / 4')).toBeVisible()
  expect(screen.queryByRole('region', { name: '测试场景' })).toBeNull()
  expect(requests.some(path => path.includes('/attempts/') || path.includes('/recovery?'))).toBe(false)
  expect(loadPending(studentFixture.account_id)).toEqual(withUnrelated ? [unrelated] : [])
})

it('requires explicit confirmation to abandon a round and preserves its identity in the request', async () => {
  mount()
  fireEvent.click(await screen.findByRole('button', { name: '放弃本轮' }))
  const request = vi.mocked(fetch)
  expect(request.mock.calls.some(([url]) => String(url).endsWith('/abandon'))).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '确认放弃本轮' }))
  await waitFor(() => expect(request.mock.calls.some(([url, init]) => String(url).endsWith(`/sessions/${id}/abandon`) && init?.method === 'POST')).toBe(true))
})

async function enterTrial(suite: 'pc-fps' | 'mobile-fps') {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue(suite === 'mobile-fps' ? 'iPhone' : 'Desktop')
  vi.stubGlobal('requestAnimationFrame', () => 1)
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
  const canvas = screen.getByLabelText('FPS 测试场景')
  let locked: Element | null = null
  Object.defineProperty(document, 'pointerLockElement', { configurable: true, get: () => locked })
  Object.defineProperty(document, 'exitPointerLock', { configurable: true, value: () => { locked = null } })
  Object.defineProperty(canvas, 'requestPointerLock', { configurable: true, value: async () => { locked = canvas } })
  const button = screen.getByRole('button', { name: '检查设备并进入试练' })
  await waitFor(() => expect(button).toBeEnabled())
  if (suite === 'mobile-fps') fireEvent(button, Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'touch' }))
  await act(async () => { fireEvent.click(button, { detail: suite === 'mobile-fps' ? 1 : 0 }) })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'trial')
}

it.each([
  ['pc-fps', 'v1'], ['pc-fps', 'current'], ['mobile-fps', 'v1'], ['mobile-fps', 'current'],
] as const)('uses the frozen %s %s task for trial even when the catalog is current', async (suite, version) => {
  const snapshot = fixtureSuiteConfig(suite, version)
  const create = vi.spyOn(suite === 'pc-fps' ? pcFpsFactory : mobileFpsFactory, 'create')
  const view: SessionView = { ...session, suite_id: suite, suite_config: snapshot, members: snapshot.test_ids }
  const { requests } = mount(true, id, view)
  fireEvent.click(await screen.findByRole('button', { name: '定位精度' }))
  await screen.findByRole('heading', { name: '定位精度' })
  await enterTrial(suite)
  expect(create.mock.calls.at(-1)?.[0]).toEqual(snapshot.tasks.precision)
  expect(requests.some(path => path.endsWith('/attempts'))).toBe(false)
})

it.each([
  ['pc-fps', 'v1'], ['pc-fps', 'current'], ['mobile-fps', 'v1'], ['mobile-fps', 'current'],
] as const)('shows a %s %s legacy notice only in the list and preparation, never during trial', async (suite, version) => {
  const snapshot = fixtureSuiteConfig(suite, version)
  mount(true, id, { ...session, suite_id: suite, suite_config: snapshot, members: snapshot.test_ids })
  await screen.findByRole('heading', { name: '正式评测项目' })
  const message = '本轮使用旧版目标规则。完成本轮后新建，或放弃本轮后新建，可使用屏内刷新。放弃会保留已保存项目，但本轮不再生成报告。'
  expect(screen.queryByText(message, { exact: false }) !== null).toBe(version === 'v1')
  if (suite === 'mobile-fps' && version === 'v1') expect(screen.getByText(message)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '定位精度' }))
  await screen.findByRole('heading', { name: '定位精度' })
  expect(screen.queryByText(message, { exact: false }) !== null).toBe(version === 'v1')
  if (suite === 'mobile-fps' && version === 'v1') expect(screen.getByText(message)).toBeVisible()
  await enterTrial(suite)
  expect(screen.queryByText(message, { exact: false })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  expect(screen.queryByText(message, { exact: false }) !== null).toBe(version === 'v1')
})

it.each([
  ['pc-moba', 'v1'], ['pc-moba', 'current'], ['mobile-moba', 'v1'], ['mobile-moba', 'current'],
] as const)('explains why a frozen %s %s round may not use the new controls', async (suite, version) => {
  const snapshot = fixtureSuiteConfig(suite, version)
  mount(true, id, { ...session, suite_id: suite, suite_config: snapshot, members: snapshot.test_ids })
  await screen.findByRole('heading', { name: '正式评测项目' })
  expect(screen.queryByText(/本轮使用旧版 MOBA 操作/)!== null).toBe(version === 'v1')
  fireEvent.click(screen.getByRole('button', { name: '威胁躲避' }))
  await screen.findByRole('heading', { name: '威胁躲避' })
  expect(screen.queryByText(/本轮使用旧版 MOBA 操作/)!== null).toBe(version === 'v1')
})
