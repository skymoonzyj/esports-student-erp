import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useEffect, useState } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { AssessmentHost } from './AssessmentHost'
import type { AssessmentSessionController } from './useAssessmentSession'
import { fixtureAttempt, fixtureConfig, fixtureCurrentConfig, fixtureSuiteConfig } from './testSupport/fixtures'
import { fixtureV5Config } from './testSupport/fixturesV5'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'
import { laneWorldToScreen } from './lane/projection'
import type { TaskConfig } from './contracts'
import { pcMobaFactory } from './suites/pcMoba'
import { pcFpsFactory } from './suites/pcFps'
import { mobileMobaFactory } from './suites/mobileMoba'
import { mobileFpsFactory } from './suites/mobileFps'
import type { GameEngine, GameFactory, InputAction } from './engineRuntime/types'
import type * as FpsStageExports from './fps/FpsStage'

const fpsStageDuration = vi.hoisted(() => vi.fn<(durationMs?: number) => void>())
const laneStageFailure = vi.hoisted(() => ({ fail: false }))
vi.mock('./lane/LaneStage', () => ({ LaneStage: ({ canvasRef, inputHandlers, onReady, onError }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object; onReady: () => void; onError: (error: { message: string }) => void }) => {
  useEffect(() => { if (laneStageFailure.fail) onError({ message: 'WebGL 初始化失败' }); else onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="MOBA 测评场景" data-lane-stage="true" {...inputHandlers} />
} }))

vi.mock('./moba/MobaStage', () => ({ MobaStage: ({ canvasRef, inputHandlers, onReady }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object; onReady: () => void }) => {
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="MOBA 测评场景" {...inputHandlers} />
} }))
vi.mock('./fps/FpsStage', async importOriginal => ({ ...await importOriginal<typeof FpsStageExports>(), FpsStage: ({ canvasRef, inputHandlers, onReady, durationMs }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object; onReady: () => void; durationMs?: number }) => {
  fpsStageDuration(durationMs)
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="FPS 测试场景" {...inputHandlers} />
} }))
const config = fixtureConfig('pc-moba', 'dodge')
function controller(): AssessmentSessionController {
  return { accountId: 'owner', canRestart: true, ready: true, busy: false, error: null, revoked: false, volatile: false, leaseMs: 90000, saved: null, pending: null, serverView: null,
    start: vi.fn(async () => ({ attempt: fixtureAttempt({ suite_id: config.suite_id, test_id: config.test_id, config, settings: { sensitivity: 1, input_kind: 'mouse', mouse_mode: null, controls: [] } }), leaseMs: 90000 })), submit: vi.fn(), interrupt: vi.fn(), leave: vi.fn(async () => true), reconcile: vi.fn(), retry: vi.fn() }
}
beforeEach(() => {
  fpsStageDuration.mockClear()
  laneStageFailure.fail = false
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) })
})
afterEach(() => {
  vi.restoreAllMocks(); vi.useRealTimers(); vi.unstubAllGlobals()
  for (const key of ['pointerLockElement', 'exitPointerLock', 'fullscreenElement', 'exitFullscreen']) Reflect.deleteProperty(document, key)
  Reflect.deleteProperty(HTMLElement.prototype, 'requestFullscreen')
  Reflect.deleteProperty(HTMLElement.prototype, 'scrollIntoView')
})

function fullscreenHarness(protectedNavigation = false, task: TaskConfig = fixtureCurrentConfig('pc-fps', 'precision'), mode: 'formal' | 'practice' = 'formal') {
  let fullscreen: Element | null = null
  let locked: Element | null = null
  let rect = { x: 0, y: 0, width: 1000, height: 562.5 }
  let frameId = 0
  const frames = new Map<number, FrameRequestCallback>()
  vi.stubGlobal('requestAnimationFrame', (frame: FrameRequestCallback) => { frames.set(++frameId, frame); return frameId })
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id) })
  vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(() => ({ ...rect, top: rect.y, left: rect.x, right: rect.x + rect.width, bottom: rect.y + rect.height, toJSON: () => ({}) }))
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, get: () => fullscreen })
  Object.defineProperty(document, 'pointerLockElement', { configurable: true, get: () => locked })
  Object.defineProperty(document, 'exitPointerLock', { configurable: true, value: () => { locked = null } })
  const changeFullscreen = (element: Element | null) => { fullscreen = element; document.dispatchEvent(new Event('fullscreenchange')) }
  const exit = vi.fn(async () => { changeFullscreen(null) })
  Object.defineProperty(document, 'exitFullscreen', { configurable: true, value: exit })
  const request = vi.fn(async function (this: HTMLElement) { changeFullscreen(this) })
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', { configurable: true, value: request })
  const session = controller()
  if (protectedNavigation) {
    session.serverView = fixtureAttempt({ suite_id: task.suite_id, test_id: task.test_id, config: task })
    vi.mocked(session.leave).mockResolvedValue(false)
  }
  vi.mocked(session.start).mockImplementation(async value => {
    session.serverView = fixtureAttempt({ suite_id: task.suite_id, test_id: task.test_id, config: task, settings: value.settings, environment: value.environment })
    return { attempt: session.serverView, leaseMs: 90000 }
  })
  let refresh = () => {}
  function HostView() {
    const [, setRevision] = useState(0)
    refresh = () => setRevision(revision => revision + 1)
    return <AssessmentHost config={task} factory={pcFpsFactory} session={session} mode={mode} onBack={vi.fn()} />
  }
  const element = <HostView />
  const router = protectedNavigation ? createMemoryRouter([{ path: '/test', element }, { path: '/list', element: <h1>项目列表</h1> }], { initialEntries: ['/test'] }) : null
  const view = render(router ? <RouterProvider router={router} /> : element)
  const canvas = screen.getByLabelText('FPS 测试场景')
  const pointerRequest = vi.fn(async () => { locked = canvas })
  Object.defineProperty(canvas, 'requestPointerLock', { configurable: true, value: pointerRequest })
  return {
    ...view, router, session, request, exit, pointerRequest, host: screen.getByRole('region', { name: '测试场景' }),
    refresh() { act(refresh) },
    change(element: Element | null) { act(() => { changeFullscreen(element) }) },
    resize(width: number, height: number) { rect = { ...rect, width, height } },
    async settle() {
      for (let i = 0; i < 4; i++) await act(async () => { const batch = [...frames.values()]; frames.clear(); for (const frame of batch) frame(performance.now()) })
    },
  }
}

it.each(['formal', 'practice'] as const)('rejects an invalid v5 actual canvas before the %s allocation boundary', async mode => {
  const host = fullscreenHarness(false, fixtureV5Config('gridshot'), mode)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  host.resize(1000, 600)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: mode === 'formal' ? '开始正式测试' : '开始练习' })) })
  expect(screen.getByRole('alert')).toHaveTextContent(/viewport|16:9|视野/)
  expect(host.host).toHaveAttribute('data-host-phase', 'preflight')
  expect(host.session.start).not.toHaveBeenCalled()
})

it.each([false, true])('uses the valid v5 canvas, not outer screen geometry (fullscreen=%s)', async fullscreen => {
  const host = fullscreenHarness(false, fixtureV5Config('sixshot'))
  if (fullscreen) {
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
    host.resize(1440, 810)
    await host.settle()
  }
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  expect(host.host).toHaveAttribute('data-host-phase', 'trial')
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(host.session.start).toHaveBeenCalledWith(expect.objectContaining({ environment: expect.objectContaining({ width_css_px: fullscreen ? 1440 : 1000, height_css_px: fullscreen ? 810 : 562.5 }) }))
  expect(host.host).toHaveAttribute('data-host-phase', 'countdown')
  host.resize(1200, 675)
  fireEvent(window, new Event('resize'))
  expect(host.host).toHaveAttribute('data-host-phase', 'interrupted')
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
})

it('reserves a stable outside-canvas toolbar slot for v5 shooting feedback before and during trial', async () => {
  const host = fullscreenHarness(false, fixtureV5Config('gridshot'))
  const toolbar = screen.getByRole('banner')
  expect(toolbar).toHaveStyle({ height: '64px', flexWrap: 'nowrap' })
  const slot = screen.getByLabelText('固定靶区反馈栏')
  expect(slot).toHaveStyle({ height: '44px' })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  const feedback = screen.getByLabelText('FPS 实时反馈')
  expect(toolbar.contains(feedback)).toBe(true)
  expect(feedback).not.toHaveStyle({ position: 'absolute' })
  expect(feedback).toHaveTextContent('0.0 / 20.0 秒')
  expect(host.host).toHaveAttribute('data-host-phase', 'trial')
  expect(slot).toHaveStyle({ height: '44px' })
})

it.each([
  ['pc-fps', 'precision', pcFpsFactory, true], ['pc-moba', 'dodge', pcMobaFactory, false],
  ['mobile-fps', 'move-fire', mobileFpsFactory, false], ['mobile-moba', 'skill-aim', mobileMobaFactory, false],
] as const)('only_pc_fps_has_fullscreen_action: %s', (suite, testId, factory, visible) => {
  render(<AssessmentHost config={fixtureCurrentConfig(suite, testId)} factory={factory} session={controller()} onBack={vi.fn()} />)
  expect(Boolean(screen.queryByRole('button', { name: '进入全屏' }))).toBe(visible)
})

it('request_does_not_start_attempt and captures only settled stage dimensions', async () => {
  const host = fullscreenHarness()
  fireEvent.click(screen.getByRole('button', { name: '进入全屏' }))
  expect(host.request).toHaveBeenCalledOnce()
  expect(host.request.mock.instances[0]).toBe(host.host)
  expect(host.pointerRequest).not.toHaveBeenCalled()
  expect(host.session.start).not.toHaveBeenCalled()
  expect(host.session.interrupt).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
  host.resize(1440, 810)
  await host.settle()
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeEnabled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(host.session.start).toHaveBeenCalledWith(expect.objectContaining({ environment: expect.objectContaining({ width_css_px: 1440, height_css_px: 810 }) }))
})

it('denial_keeps_window_mode usable without creating an attempt', async () => {
  const host = fullscreenHarness()
  host.request.mockRejectedValueOnce(new Error('denied'))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  expect(screen.getByRole('alert')).toHaveTextContent('全屏')
  expect(screen.getByRole('button', { name: '进入全屏' })).toBeEnabled()
  expect(host.session.start).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  expect(host.host).toHaveAttribute('data-host-phase', 'trial')
})

it('pending_blocks_duplicate_and_start until both native request and layout settle', async () => {
  const host = fullscreenHarness()
  let resolve!: () => void
  host.request.mockImplementationOnce(() => new Promise<void>(done => { resolve = done }))
  const button = screen.getByRole('button', { name: '进入全屏' })
  fireEvent.click(button); fireEvent.click(button)
  fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' }))
  await host.settle()
  expect(button).toBeDisabled()
  expect(host.request).toHaveBeenCalledOnce()
  expect(host.pointerRequest).not.toHaveBeenCalled()
  host.change(host.host)
  await host.settle()
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
  await act(async () => resolve())
  await host.settle()
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeEnabled()
})

it('active_phase_blocks_toggle and fullscreen_exit_interrupts_once_without_submit', async () => {
  const host = fullscreenHarness()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  host.host.scrollTop = 500
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  expect(host.host.scrollTop).toBe(0)
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '退出全屏' }))
  expect(host.exit).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(host.host).toHaveAttribute('data-host-phase', 'countdown')
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeDisabled()
  host.change(null)
  fireEvent(document, new Event('pointerlockchange')); fireEvent(window, new Event('resize')); host.change(null)
  expect(host.host).toHaveAttribute('data-host-phase', 'interrupted')
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
})

it.each([true, false])('late_request_after_unmount_is_owner_scoped (own=%s)', async own => {
  const host = fullscreenHarness()
  let resolve!: () => void
  host.request.mockImplementationOnce(() => new Promise<void>(done => { resolve = done }))
  fireEvent.click(screen.getByRole('button', { name: '进入全屏' }))
  host.unmount()
  host.change(own ? host.host : document.body)
  await act(async () => resolve())
  expect(host.exit).toHaveBeenCalledTimes(own ? 1 : 0)
  expect(host.session.start).not.toHaveBeenCalled()
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it('exits fullscreen before allocation without starting, and waits for window layout', async () => {
  const host = fullscreenHarness()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  fireEvent.click(screen.getByRole('button', { name: '退出全屏' }))
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
  await host.settle()
  expect(screen.getByRole('button', { name: '进入全屏' })).toBeEnabled()
  expect(host.session.start).not.toHaveBeenCalled()
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it('cancels preparation when fullscreen exits while pointer-lock acquisition is pending', async () => {
  const host = fullscreenHarness()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  let resolve!: () => void
  host.pointerRequest.mockImplementationOnce(() => new Promise<void>(done => { resolve = done }))
  fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' }))
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeDisabled()
  host.change(null)
  await act(async () => resolve())
  expect(host.host).toHaveAttribute('data-host-phase', 'interrupted')
  expect(host.session.start).not.toHaveBeenCalled()
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it('defers a starting-phase fullscreen interruption until allocation settles, then interrupts once', async () => {
  const host = fullscreenHarness()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  expect(screen.getByRole('button', { name: '结束试练' }).closest('header')).not.toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  let resolve!: (value: Awaited<ReturnType<typeof host.session.start>>) => void
  vi.mocked(host.session.start).mockImplementationOnce(() => new Promise(done => { resolve = done }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(host.host).toHaveAttribute('data-host-phase', 'starting')
  host.change(null); fireEvent(document, new Event('pointerlockchange'))
  expect(host.session.interrupt).not.toHaveBeenCalled()
  await act(async () => resolve({ attempt: fixtureAttempt(), leaseMs: 90000 }))
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
})

it.each([true, false])('holds a cancelled fullscreen operation through settlement and cleanup after failed leave (own=%s)', async own => {
  const host = fullscreenHarness(true)
  let resolve!: () => void
  host.request.mockImplementationOnce(() => new Promise<void>(done => { resolve = done }))
  fireEvent.click(screen.getByRole('button', { name: '进入全屏' }))
  await act(async () => { void host.router!.navigate('/list') })
  fireEvent.click(screen.getByRole('button', { name: '确认退出' }))
  await screen.findByText('退出未完成，请保留本页并重新对账。')
  expect(host.session.leave).toHaveBeenCalledOnce()
  expect(host.router!.state.location.pathname).toBe('/test')
  fireEvent.click(screen.getByRole('button', { name: '继续测试' }))
  await host.settle()
  expect(screen.getByRole('button', { name: '进入全屏' })).toBeDisabled()

  // Reconciliation clears recovery while the obsolete native request is still pending.
  vi.mocked(host.session.reconcile).mockImplementation(async () => { host.session.serverView = null; return null })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新对账' })) })
  host.refresh()
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '进入全屏' }))
  fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' }))
  expect(host.request).toHaveBeenCalledOnce()
  expect(host.pointerRequest).not.toHaveBeenCalled()
  let resolveExit!: () => void
  if (own) host.exit.mockImplementationOnce(() => new Promise<void>(done => { resolveExit = () => { host.change(null); done() } }))
  host.change(own ? host.host : document.body)
  await act(async () => resolve())
  if (own) {
    await host.settle()
    expect(host.exit).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
    await act(async () => resolveExit())
  }
  await host.settle()
  expect(host.exit).toHaveBeenCalledTimes(own ? 1 : 0)
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeEnabled()
  expect(host.session.start).not.toHaveBeenCalled()
  expect(host.session.interrupt).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  expect(host.request).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeEnabled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(host.host).toHaveAttribute('data-host-phase', 'countdown')
  expect(host.session.start).toHaveBeenCalledOnce()
  expect(host.exit).toHaveBeenCalledTimes(own ? 1 : 0)
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it('holds an acquired fullscreen cleanup exit through failed leave until native exit settles', async () => {
  const host = fullscreenHarness(true)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeEnabled()
  let resolveExit!: () => void
  host.exit.mockImplementationOnce(() => new Promise<void>(done => { resolveExit = () => { host.change(null); done() } }))
  await act(async () => { void host.router!.navigate('/list') })
  fireEvent.click(screen.getByRole('button', { name: '确认退出' }))
  await screen.findByText('退出未完成，请保留本页并重新对账。')
  fireEvent.click(screen.getByRole('button', { name: '继续测试' }))
  await host.settle()
  expect(host.exit).toHaveBeenCalledOnce()
  expect(screen.getByRole('button', { name: '退出全屏' })).toBeDisabled()
  vi.mocked(host.session.reconcile).mockImplementation(async () => { host.session.serverView = null; return null })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '重新对账' })) })
  host.refresh()
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '退出全屏' }))
  fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' }))
  expect(host.exit).toHaveBeenCalledOnce()
  expect(host.pointerRequest).not.toHaveBeenCalled()
  await act(async () => resolveExit())
  await host.settle()
  expect(screen.getByRole('button', { name: '进入全屏' })).toBeEnabled()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '进入全屏' })) })
  await host.settle()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(host.request).toHaveBeenCalledTimes(2)
  expect(host.exit).toHaveBeenCalledOnce()
  expect(host.host).toHaveAttribute('data-host-phase', 'countdown')
  expect(host.session.start).toHaveBeenCalledOnce()
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it.each([
  ['pc-fps', 'precision', pcFpsFactory],
  ['pc-moba', 'dodge', pcMobaFactory],
  ['mobile-fps', 'move-fire', mobileFpsFactory],
  ['mobile-moba', 'skill-aim', mobileMobaFactory],
] as const)('keeps real %s control settings disabled until recovery is ready and during busy work', (suite, testId, factory) => {
  const task = fixtureCurrentConfig(suite, testId)
  const session = controller()
  const onBack = vi.fn()
  const view = (ready: boolean, busy: boolean) => <AssessmentHost config={task} factory={factory} session={{ ...session, ready, busy }} onBack={onBack} />
  const { rerender } = render(view(false, false))
  const controls = () => {
    const fields = screen.getByRole('region', { name: '操作设置' }).querySelectorAll('input, select, textarea')
    expect(fields.length).toBeGreaterThan(0)
    return fields
  }

  for (const control of controls()) expect(control).toBeDisabled()
  rerender(view(false, true))
  for (const control of controls()) expect(control).toBeDisabled()
  rerender(view(true, false))
  for (const control of controls()) expect(control).toBeEnabled()
  rerender(view(true, true))
  for (const control of controls()) expect(control).toBeDisabled()
})

it('suppresses browser nonprimary down/up defaults only inside the active PC MOBA canvas and routes right-click once', async () => {
  const inputs: InputAction[] = []
  const factory: GameFactory = { create(...args) {
    const engine = pcMobaFactory.create(...args)
    return { advanceTo: time => engine.advanceTo(time), scene: () => engine.scene(), finish: () => engine.finish(), input(action) { inputs.push(action); engine.input(action) } }
  } }
  render(<AssessmentHost config={config} factory={factory} session={controller()} onBack={vi.fn()} />)
  const canvas = screen.getByLabelText('MOBA 测评场景')
  function mouse(type: string, button: number, target: Element = canvas) {
    const event = Object.assign(new MouseEvent(type, { bubbles: true, cancelable: true, button, clientX: 500, clientY: 280 }), { pointerType: 'mouse', pointerId: 1 })
    fireEvent(target, event)
    return event.defaultPrevented
  }
  expect(mouse('mouseup', 4)).toBe(false)
  expect(mouse('contextmenu', 2)).toBe(false)
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  for (const button of [1, 2, 3, 4]) {
    for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'auxclick']) expect(mouse(type, button)).toBe(true)
  }
  expect(mouse('contextmenu', 2)).toBe(true)
  expect(mouse('mouseup', 4, document.body)).toBe(false)
  expect(mouse('mousedown', 0)).toBe(false)
  expect(inputs.filter(action => action.kind === 'point')).toEqual([expect.objectContaining({ kind: 'point', button: 2 })])
  fireEvent.click(screen.getByText('结束试练'))
  expect(mouse('mouseup', 4)).toBe(false)
  expect(mouse('contextmenu', 2)).toBe(false)
})

it.each([['current', false], ['legacy', true]] as const)('exposes touch movement and attack overlays only for the %s mobile map control contract', (version, visible) => {
  const task = version === 'current' ? fixtureCurrentConfig('mobile-moba', 'map-awareness') : fixtureConfig('mobile-moba', 'map-awareness')
  render(<AssessmentHost config={task} factory={mobileMobaFactory} session={controller()} onBack={vi.fn()} />)
  for (const label of ['移动', '攻击']) expect(Boolean(screen.queryByText(label, { selector: 'div' }))).toBe(visible)
  expect(Boolean(screen.queryByRole('region', { name: '操作设置' }))).toBe(visible)
})

async function countdownHarness(leaseMs = 90000) {
  let now = 0
  let frame: FrameRequestCallback | undefined
  let rect = { x: 0, y: 0, width: 1000, height: 562.5 }
  vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(() => ({ ...rect, top: rect.y, left: rect.x, right: rect.x + rect.width, bottom: rect.y + rect.height, toJSON: () => ({ ...rect }) }))
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const session = controller()
  vi.mocked(session.start).mockImplementation(async () => {
    session.serverView = fixtureAttempt({ suite_id: config.suite_id, test_id: config.test_id, config, settings: { sensitivity: 1, input_kind: 'mouse', mouse_mode: null, controls: [] } })
    return { attempt: session.serverView, leaseMs }
  })
  const engines: { engine: GameEngine; inputs: InputAction[] }[] = []
  const factory: GameFactory = { create(...args) {
    const engine = pcMobaFactory.create(...args)
    const inputs: InputAction[] = []
    engines.push({ engine, inputs })
    return { advanceTo: time => engine.advanceTo(time), scene: () => engine.scene(), finish: () => engine.finish(), input(action) { inputs.push(action); engine.input(action) } }
  } }
  render(<AssessmentHost config={config} factory={factory} session={session} onBack={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'countdown')
  expect(screen.getByRole('region', { name: '测试场景' })).not.toHaveAttribute('data-run-start-ms')
  return {
    session, engines,
    resize(next: Partial<typeof rect>, notify = true) { rect = { ...rect, ...next }; if (notify) fireEvent(window, new Event('resize')) },
    frame(at: number, animationTimestamp = at) { act(() => { now = at; frame?.(animationTimestamp) }) },
    point(at: number) { now = at; fireEvent(screen.getByLabelText('MOBA 测评场景'), Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'mouse', clientX: rect.x + 500, clientY: rect.y + 280, button: 2 })) },
  }
}

it.each([[960, 540], [799, 449]])('interrupts countdown on actual stage resize to %s × %s without creating a formal engine', async (width, height) => {
  const host = await countdownHarness()
  const previewEngines = host.engines.length
  host.resize({ width, height })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'interrupted')
  for (let now = 200; now <= 3200; now += 200) host.frame(now)
  expect(host.engines).toHaveLength(previewEngines)
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
})

it('rechecks frozen dimensions before the first formal frame even without a resize notification', async () => {
  const host = await countdownHarness()
  const previewEngines = host.engines.length
  for (let now = 200; now <= 2800; now += 200) host.frame(now)
  host.resize({ width: 700, height: 393.75 }, false)
  host.frame(3000)
  expect(host.engines).toHaveLength(previewEngines)
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('场地尺寸')
})

it('allows countdown origin-only movement while retaining frozen dimensions', async () => {
  const host = await countdownHarness()
  const previews = host.engines.length
  host.resize({ x: 25, y: -80 })
  for (let now = 200; now <= 3000; now += 200) host.frame(now)
  expect(host.engines).toHaveLength(previews + 1)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'running')
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it.each([3200, 3300])('anchors an exact 63-second lease to the countdown deadline despite a first run frame at %sms', async firstFrame => {
  const host = await countdownHarness(63000)
  for (let now = 200; now <= 2800; now += 200) host.frame(now)
  host.frame(firstFrame)
  const formal = host.engines.at(-1)!
  expect(formal.engine.scene().at_ms).toBe(firstFrame - 3000)
  const region = screen.getByRole('region', { name: '测试场景' })
  expect(region).toHaveAttribute('data-run-start-ms', '3000')
  expect(region).toHaveAttribute('data-run-deadline-ms', '63000')
  expect(region).not.toHaveAttribute('data-run-finish-ms')
  for (let now = firstFrame + 200; now < 63000; now += 200) host.frame(now)
  host.point(62999)
  expect(formal.inputs).toHaveLength(1)
  host.point(63000)
  expect(formal.inputs).toHaveLength(1)
  host.frame(63000)
  expect(region).toHaveAttribute('data-run-finish-ms', '63000')
  expect(host.session.submit).toHaveBeenCalledOnce()
  expect(vi.mocked(host.session.submit).mock.calls[0][1]).toMatchObject({ duration_ms: 60000, max_frame_gap_ms: firstFrame - 2800, long_frame_gap_total_ms: 60000 })
  expect(formal.engine.scene().at_ms).toBe(60000)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'pending')
  expect(host.session.interrupt).not.toHaveBeenCalled()
})

it('rejects a real 501ms countdown crossing gap without allocating a formal engine or saving', async () => {
  const host = await countdownHarness(63000)
  const previews = host.engines.length
  for (let now = 200; now <= 2800; now += 200) host.frame(now)
  host.frame(3301)
  expect(host.engines).toHaveLength(previews)
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('帧间隙超过 500ms')
})

it('does not mislabel a backwards countdown timestamp as a long frame stall', async () => {
  const host = await countdownHarness()
  host.frame(-1)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'interrupted')
  expect(screen.getByRole('alert')).toHaveTextContent('计时时间异常')
  expect(screen.getByRole('alert')).not.toHaveTextContent('超过 500ms')
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
})

it('uses the same current monotonic clock when the first RAF timestamp precedes countdown allocation', async () => {
  const host = await countdownHarness()
  host.frame(1, -3)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'countdown')
  expect(host.session.interrupt).not.toHaveBeenCalled()
  for (let time = 200; time <= 3000; time += 200) host.frame(time, time - 4)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'running')
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-run-start-ms', '3000')
  expect(host.session.submit).not.toHaveBeenCalled()
})

it('still rejects a real 501ms callback delay when the RAF timestamp itself appears timely', async () => {
  const host = await countdownHarness()
  host.frame(501, 100)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'interrupted')
  expect(screen.getByRole('alert')).toHaveTextContent('帧间隙超过 500ms')
  expect(host.session.interrupt).toHaveBeenCalledOnce()
  expect(host.session.submit).not.toHaveBeenCalled()
})

it('clears ephemeral timing proof when a measured run is interrupted', async () => {
  const host = await countdownHarness()
  for (let now = 200; now <= 3000; now += 200) host.frame(now)
  const region = screen.getByRole('region', { name: '测试场景' })
  expect(region).toHaveAttribute('data-run-start-ms', '3000')
  host.resize({ width: 900 })
  expect(region).toHaveAttribute('data-host-phase', 'interrupted')
  for (const field of ['start', 'deadline', 'finish']) expect(region).not.toHaveAttribute(`data-run-${field}-ms`)
})

it('does not allocate until trial ends and preserves the canvas through starting/countdown', async () => {
  const session = controller()
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  const canvas = screen.getByLabelText('MOBA 测评场景')
  fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' }))
  await screen.findByRole('button', { name: '结束试练' })
  expect(session.start).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  fireEvent.click(screen.getByRole('button', { name: '开始正式测试' }))
  await waitFor(() => expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'countdown'))
  expect(screen.getByLabelText('MOBA 测评场景')).toBe(canvas)
})
it('passes the actual 20-second trial then 60-second formal duration to the FPS stage', async () => {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  vi.stubGlobal('innerWidth', 844)
  vi.stubGlobal('innerHeight', 390)
  let now = 0
  let frame: FrameRequestCallback | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const task = fixtureCurrentConfig('mobile-fps', 'precision')
  const session = controller()
  vi.mocked(session.start).mockImplementation(async request => {
    session.serverView = fixtureAttempt({ suite_id: task.suite_id, test_id: task.test_id, config: task, settings: request.settings, environment: request.environment })
    return { attempt: session.serverView, leaseMs: 90000 }
  })
  render(<AssessmentHost config={task} factory={mobileFpsFactory} session={session} onBack={vi.fn()} />)
  const enter = screen.getByText('检查设备并进入试练')
  fireEvent(enter, Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'touch' }))
  await act(async () => { fireEvent.click(enter, { detail: 1 }) })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'trial')
  expect(fpsStageDuration).toHaveBeenLastCalledWith(20000)
  expect(session.start).not.toHaveBeenCalled()
  fireEvent.click(screen.getByText('结束试练'))
  expect(fpsStageDuration).toHaveBeenLastCalledWith(20000)
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  for (now = 200; now <= 3000; now += 200) act(() => { frame?.(now) })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'running')
  expect(fpsStageDuration).toHaveBeenLastCalledWith(60000)
  expect(task.duration_ms).toBe(60000)
})
it('refuses a 62-second lease and reconciles instead of starting a clock', async () => {
  const session = controller()
  vi.mocked(session.start).mockResolvedValue({ attempt: fixtureAttempt(), leaseMs: 62000 })
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  fireEvent.click(screen.getByText('检查设备并进入试练'))
  await screen.findByText('结束试练')
  fireEvent.click(screen.getByText('结束试练'))
  fireEvent.click(screen.getByText('开始正式测试'))
  await waitFor(() => expect(session.interrupt).toHaveBeenCalledOnce())
  expect(screen.getByRole('alert')).toHaveTextContent('占用时间不足')
})
it('ignores a pointer press carried from trial onto a newly exposed button', async () => {
  const session = controller()
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  fireEvent.click(screen.getByText('检查设备并进入试练'))
  await screen.findByText('结束试练')
  fireEvent.click(screen.getByText('结束试练'))
  fireEvent.click(screen.getByText('开始正式测试'), { detail: 1 })
  expect(session.start).not.toHaveBeenCalled()
})
it('interrupts when hidden during countdown and does not save raw', async () => {
  const session = controller()
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  fireEvent.click(screen.getByText('检查设备并进入试练'))
  await screen.findByText('结束试练')
  fireEvent.click(screen.getByText('结束试练'))
  fireEvent.click(screen.getByText('开始正式测试'))
  await waitFor(() => expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'countdown'))
  await act(async () => { vi.spyOn(document, 'hidden', 'get').mockReturnValue(true); fireEvent(document, new Event('visibilitychange')) })
  expect(session.interrupt).toHaveBeenCalled()
  expect(session.submit).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('页面进入后台')
})

it('finishes exactly one 60-second run, releases held controls before offering save, and never credits the trial', async () => {
  let now = 0
  let frame: FrameRequestCallback | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const session = controller()
  const view = fixtureAttempt({ suite_id: config.suite_id, test_id: config.test_id, config, settings: { sensitivity: 1, input_kind: 'mouse', mouse_mode: null, controls: [] } })
  vi.mocked(session.start).mockImplementation(async () => { session.serverView = view; return { attempt: view, leaseMs: 90000 } })
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  act(() => { now = 100; frame?.(now) })
  fireEvent.click(screen.getByText('结束试练'))
  expect(session.submit).not.toHaveBeenCalled()
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  for (now = 300; now <= 62900; now += 200) act(() => { frame?.(now) })
  act(() => { now = 63300; frame?.(now) })
  expect(session.submit).toHaveBeenCalledOnce()
  expect(vi.mocked(session.submit).mock.calls[0][1].duration_ms).toBe(60000)
  expect(vi.mocked(session.submit).mock.calls[0][1].long_frame_gap_total_ms).toBe(60000)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-run-start-ms', '3100')
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-run-deadline-ms', '63100')
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-run-finish-ms', '63300')
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'pending')
  expect(session.interrupt).not.toHaveBeenCalled()
})

it.each([['v4', 'melee'], ['v4', 'ranged'], ['v5', 'melee'], ['v5', 'ranged']] as const)('runs signed %s %s lane for exactly five minutes, inverse-projects late left input, and retains frame quality', async (version, attackMode) => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  let now = 0
  let frame: FrameRequestCallback | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const task = version === 'v4' ? fixtureMobaV4Config(attackMode) : fixtureMobaV5Config(attackMode)
  const session = controller()
  const inputs: InputAction[] = []
  const factory: GameFactory = { create(...args) {
    const engine = pcMobaFactory.create(...args)
    return { advanceTo: time => engine.advanceTo(time), scene: () => engine.scene(), finish: () => engine.finish(), input: action => { inputs.push(action); engine.input(action) } }
  } }
  vi.mocked(session.start).mockImplementation(async value => {
    session.serverView = fixtureAttempt({ suite_id: task.suite_id, test_id: task.test_id, config: task, settings: value.settings, environment: value.environment })
    return { attempt: session.serverView, leaseMs: 330000 }
  })
  render(<AssessmentHost config={task} factory={factory} session={session} onBack={vi.fn()} />)
  expect(screen.getByLabelText('MOBA 测评场景')).toHaveAttribute('data-lane-stage', 'true')
  expect(screen.getByLabelText('兵线实时反馈')).toHaveTextContent(version === 'v4' ? '波次 0 / 10' : '波次 0 / 15')
  if (version === 'v5') expect(screen.getByLabelText('兵线实时反馈')).toHaveTextContent('生命 1000 / 1000')
  else expect(screen.getByLabelText('兵线实时反馈')).not.toHaveTextContent('生命')
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  act(() => { now = 100; frame?.(now) })
  fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  expect(session.start).toHaveBeenCalledWith(expect.objectContaining({ attack_mode: attackMode }))
  expect(session.start).toHaveBeenCalledWith(expect.objectContaining({ environment: expect.objectContaining({ webgl2_supported: true }) }))
  for (now = 300; now <= 303100; now += 200) {
    act(() => { frame?.(now) })
    if (now === 123100) {
      expect(session.submit).not.toHaveBeenCalled()
      expect(screen.getByLabelText('兵线实时反馈')).toHaveTextContent('剩余 3:00')
      const point = laneWorldToScreen({ x: 400, y: 420 })
      fireEvent.pointerDown(screen.getByLabelText('MOBA 测评场景'), { pointerId: 1, pointerType: 'mouse', button: 0, clientX: point.x, clientY: point.y })
    }
  }
  expect(inputs).toContainEqual(expect.objectContaining({ kind: 'point', at_ms: 120000, x: 400, y: 420, button: 0 }))
  expect(session.submit).toHaveBeenCalledOnce()
  const [raw, quality] = vi.mocked(session.submit).mock.calls[0]
  expect(raw.kind).toBe(version === 'v4' ? 'lane_last_hit' : 'lane_last_hit_towers')
  expect(quality).toMatchObject({ duration_ms: 300000, long_frame_gap_total_ms: 300000, input_kind: 'mouse' })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-run-deadline-ms', '303100')
  expect(session.interrupt).not.toHaveBeenCalled()
})

it('never allocates a formal lane ticket when its WebGL stage fails before readiness', () => {
  laneStageFailure.fail = true
  const session = controller()
  render(<AssessmentHost config={fixtureMobaV4Config()} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  expect(screen.getByRole('alert')).toHaveTextContent('WebGL 初始化失败')
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' }))
  expect(session.start).not.toHaveBeenCalled()
})

it('keeps formal v5 running through actual tower damage, death and five-second respawn without replaying dead input', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  let now = 0
  let frame: FrameRequestCallback | undefined
  let engine: GameEngine | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const task = fixtureMobaV5Config()
  const session = controller()
  const factory: GameFactory = { create(...args) { engine = pcMobaFactory.create(...args); return engine } }
  vi.mocked(session.start).mockImplementation(async value => {
    session.serverView = fixtureAttempt({ suite_id: 'pc-moba', test_id: 'last-hit', config: task, settings: value.settings, environment: value.environment })
    return { attempt: session.serverView, leaseMs: 330000 }
  })
  render(<AssessmentHost config={task} factory={factory} session={session} onBack={vi.fn()} />)
  const feedback = screen.getByLabelText('兵线实时反馈')
  const slotCount = feedback.children.length
  expect(feedback).toHaveTextContent('生命 1000 / 1000')
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  for (now = 20; now <= 3000; now += 20) act(() => { frame?.(now) })
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'running')
  const canvas = screen.getByLabelText('MOBA 测评场景')
  const point = laneWorldToScreen({ x: 930, y: 400 })
  const move = () => fireEvent.pointerDown(canvas, { pointerId: 1, pointerType: 'mouse', button: 0, clientX: point.x, clientY: point.y })
  move()
  let deathAt = 0
  for (; now < 30000; now += 20) {
    act(() => { frame?.(now) })
    if (feedback.textContent?.includes('生命 0 / 1000')) { deathAt = now; break }
  }
  expect(deathAt, JSON.stringify(engine!.scene())).toBeGreaterThan(3000)
  expect(feedback).toHaveTextContent('死亡 1')
  expect(feedback).toHaveTextContent('复活倒计时 5 秒')
  expect(feedback.children).toHaveLength(slotCount)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'running')
  move()
  for (now = deathAt + 20; now <= deathAt + 5000; now += 20) act(() => { frame?.(now) })
  expect(feedback).toHaveTextContent('生命 1000 / 1000')
  expect(feedback).not.toHaveTextContent('复活倒计时')
  for (; now <= deathAt + 10000; now += 20) act(() => { frame?.(now) })
  const scene = engine!.scene()
  expect(scene.kind === 'moba' && scene.player.position).toEqual({ x: 180, y: 420 })
  expect(feedback).toHaveTextContent('死亡 1')
  expect(feedback.children).toHaveLength(slotCount)
  expect(session.interrupt).not.toHaveBeenCalled()
  expect(session.submit).not.toHaveBeenCalled()
})

it('focuses the complete lane stage synchronously before trial and formal environment allocation, never after running starts', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  const events: string[] = []
  const focus = vi.fn(() => { events.push('focus') })
  Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { configurable: true, value: focus })
  const task = fixtureMobaV4Config()
  const session = controller()
  vi.mocked(session.start).mockImplementation(async value => {
    events.push('allocate')
    session.serverView = fixtureAttempt({ suite_id: task.suite_id, test_id: task.test_id, config: task, settings: value.settings, environment: value.environment })
    return { attempt: session.serverView, leaseMs: 330000 }
  })
  render(<AssessmentHost config={task} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  expect(focus).toHaveBeenCalledWith({ block: 'start', inline: 'nearest', behavior: 'instant' })
  expect(screen.getByLabelText('兵线实时反馈')).toHaveTextContent('试练 ≤20秒')
  expect(focus.mock.instances[0]).toBe(screen.getByRole('region', { name: '测试场景' }))
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  expect(events).toEqual(['focus', 'focus', 'allocate'])
})

it('rejects invalid lane viewport before preparation or a formal ticket', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({ x: 0, y: 0, width: 1000, height: 600, top: 0, left: 0, right: 1000, bottom: 600, toJSON: () => ({}) })
  const session = controller()
  render(<AssessmentHost config={fixtureMobaV4Config()} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  expect(screen.getByRole('alert')).toHaveTextContent('16:9')
  expect(session.start).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: '结束试练' })).toBeNull()
})

it('interrupts a countdown frame stall instead of silently extending the lease', async () => {
  let now = 0
  let frame: FrameRequestCallback | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const session = controller()
  vi.mocked(session.start).mockImplementation(async () => { session.serverView = fixtureAttempt({ suite_id: config.suite_id, test_id: config.test_id, config, settings: { sensitivity: 1, input_kind: 'mouse', mouse_mode: null, controls: [] } }); return { attempt: session.serverView, leaseMs: 90000 } })
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} onBack={vi.fn()} />)
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  fireEvent.click(screen.getByText('结束试练'))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  act(() => { now = 4000; frame?.(now) })
  expect(session.interrupt).toHaveBeenCalledOnce()
  expect(screen.getByRole('alert')).toHaveTextContent('帧间隙')
})

it('requests Pointer Lock synchronously in the gesture, waits for it, and exits a late aborted acquisition', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  const session = controller()
  const { unmount } = render(<AssessmentHost config={fixtureConfig('pc-fps', 'precision')} factory={pcFpsFactory} session={session} onBack={vi.fn()} />)
  const canvas = screen.getByLabelText('FPS 测试场景')
  let locked: Element | null = null
  Object.defineProperty(document, 'pointerLockElement', { configurable: true, get: () => locked })
  const exit = vi.fn(() => { locked = null })
  Object.defineProperty(document, 'exitPointerLock', { configurable: true, value: exit })
  let resolve!: () => void
  const request = vi.fn(() => new Promise<void>(done => { resolve = done }))
  Object.defineProperty(canvas, 'requestPointerLock', { configurable: true, value: request })
  fireEvent.click(screen.getByText('检查设备并进入试练'))
  expect(request).toHaveBeenCalledOnce()
  expect(session.start).not.toHaveBeenCalled()
  expect(screen.queryByText('结束试练')).toBeNull()
  unmount()
  await act(async () => { locked = canvas; resolve() })
  expect(exit).toHaveBeenCalledOnce()
  expect(session.start).not.toHaveBeenCalled()
})

it('reacquires Pointer Lock from the formal-start gesture and rejects a lost lock after allocation', async () => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  const session = controller()
  render(<AssessmentHost config={fixtureConfig('pc-fps', 'precision')} factory={pcFpsFactory} session={session} onBack={vi.fn()} />)
  const canvas = screen.getByLabelText('FPS 测试场景')
  let locked: Element | null = null
  Object.defineProperty(document, 'pointerLockElement', { configurable: true, get: () => locked })
  Object.defineProperty(document, 'exitPointerLock', { configurable: true, value: () => { locked = null } })
  const request = vi.fn(async () => { locked = canvas })
  Object.defineProperty(canvas, 'requestPointerLock', { configurable: true, value: request })
  await act(async () => { fireEvent.click(screen.getByText('检查设备并进入试练')) })
  fireEvent.click(screen.getByText('结束试练'))
  expect(locked).toBeNull()
  let resolve!: (result: { attempt: ReturnType<typeof fixtureAttempt>; leaseMs: number }) => void
  vi.mocked(session.start).mockImplementation(() => new Promise(done => { resolve = done }))
  await act(async () => { fireEvent.click(screen.getByText('开始正式测试')) })
  expect(request).toHaveBeenCalledTimes(2)
  await act(async () => { locked = null; resolve({ attempt: fixtureAttempt(), leaseMs: 90000 }) })
  expect(session.interrupt).toHaveBeenCalledOnce()
  expect(screen.getByRole('alert')).toHaveTextContent('设备状态变化')
})

it.each(['relative', 'raw', 'fallback'] as const)('freezes the gesture-selected %s mouse request separately from actual lock mode', async choice => {
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  const session = controller()
  vi.mocked(session.start).mockImplementation(async settings => ({ attempt: fixtureAttempt({ settings: settings.settings, environment: settings.environment }), leaseMs: 90000 }))
  render(<AssessmentHost config={fixtureConfig('pc-fps', 'precision')} factory={pcFpsFactory} session={session} onBack={vi.fn()} />)
  const canvas = screen.getByLabelText('FPS 测试场景')
  let locked: Element | null = null
  Object.defineProperty(document, 'pointerLockElement', { configurable: true, get: () => locked })
  Object.defineProperty(document, 'exitPointerLock', { configurable: true, value: () => { locked = null } })
  const request = vi.fn(async (options?: { unadjustedMovement?: boolean }) => {
    if (choice === 'fallback' && options?.unadjustedMovement) throw new Error('raw unsupported')
    locked = canvas
  })
  Object.defineProperty(canvas, 'requestPointerLock', { configurable: true, value: request })
  fireEvent.change(screen.getByLabelText('鼠标输入模式'), { target: { value: choice === 'relative' ? 'relative' : 'raw' } })
  fireEvent.click(screen.getByText('检查设备并进入试练'))
  expect(request).toHaveBeenCalledTimes(1) // The native request stays in the initiating gesture.
  expect(request.mock.calls[0][0]).toEqual(choice === 'relative' ? undefined : { unadjustedMovement: true })
  await screen.findByText('结束试练')
  fireEvent.click(screen.getByText('结束试练'))
  request.mockClear()
  fireEvent.click(screen.getByText('开始正式测试'))
  expect(request).toHaveBeenCalledTimes(1)
  expect(request.mock.calls[0][0]).toEqual(choice === 'relative' ? undefined : { unadjustedMovement: true })
  await waitFor(() => expect(session.start).toHaveBeenCalledOnce())
  const frozen = vi.mocked(session.start).mock.calls[0][0]
  expect(frozen.settings.mouse_mode).toBe(choice === 'raw' ? 'raw' : 'relative')
  expect(frozen.environment).toMatchObject({ raw_mouse_requested: choice !== 'relative', raw_mouse_confirmed: choice === 'raw', pointer_lock_active: true })
  expect(request).toHaveBeenCalledTimes(choice === 'fallback' ? 2 : 1)
})

it('routes non-primary mobile fingers once and advances actual input time before skill origin lookup', async () => {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone')
  let now = 0
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', () => 1)
  vi.stubGlobal('cancelAnimationFrame', () => undefined)
  let engine!: GameEngine
  const inputs: InputAction[] = []
  const factory: GameFactory = { create(...args) {
    engine = mobileMobaFactory.create(...args)
    const target = engine
    return { advanceTo: time => target.advanceTo(time), scene: () => target.scene(), finish: () => target.finish(), input(action) { inputs.push(action); target.input(action) } }
  } }
  const session = controller()
  render(<AssessmentHost config={fixtureConfig('mobile-moba', 'skill-aim')} factory={factory} session={session} mode="practice" onBack={vi.fn()} />)
  const button = screen.getByText('检查设备并进入试练')
  const pointer = (target: HTMLElement, type: string, id: number, x: number, y: number) => fireEvent(target, Object.assign(new Event(type, { bubbles: true }), { pointerId: id, pointerType: 'touch', clientX: x, clientY: y, button: 0, isPrimary: id === 1 }))
  pointer(button, 'pointerdown', 1, 0, 0)
  await act(async () => { fireEvent.click(button, { detail: 1 }) })
  expect(screen.getByText('结束试练')).toBeVisible()
  const canvas = screen.getByLabelText('MOBA 测评场景')
  now = 100
  pointer(canvas, 'pointerdown', 1, 180, 421.875)
  now = 800
  pointer(canvas, 'pointerdown', 2, 720, 421.875)
  expect(inputs.map(input => input.kind)).toEqual(['move', 'skill'])
  const currentScene = engine.scene()
  if (currentScene.kind !== 'moba') throw new Error('expected MOBA')
  expect(currentScene.at_ms).toBe(800)
  expect(currentScene.player.position.x).toBeGreaterThan(500)
  expect(inputs[1]).toMatchObject({ kind: 'skill', at_ms: 800, x: currentScene.player.position.x, y: currentScene.player.position.y })
  pointer(canvas, 'pointercancel', 2, 720, 421.875)
  expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'interrupted')
  expect(session.submit).not.toHaveBeenCalled()
})

it('offers a clean return rather than reusing an expired practice URL identity', async () => {
  const session = controller()
  session.serverView = fixtureAttempt({ mode: 'practice', session_id: null, state: 'expired' })
  session.canRestart = false
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={session} mode="practice" onBack={vi.fn()} />)
  await waitFor(() => expect(screen.getByRole('region', { name: '测试场景' })).toHaveAttribute('data-host-phase', 'interrupted'))
  expect(screen.getByRole('button', { name: '返回项目列表重新开始' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '检查设备并进入试练' })).toBeNull()
})

it('requires phone landscape independently of the contained 16:9 canvas shape', async () => {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone')
  vi.stubGlobal('innerWidth', 390)
  vi.stubGlobal('innerHeight', 844)
  const session = controller()
  render(<AssessmentHost config={fixtureConfig('mobile-moba', 'skill-aim')} factory={mobileMobaFactory} session={session} mode="practice" onBack={vi.fn()} />)
  const button = screen.getByText('检查设备并进入试练')
  fireEvent(button, Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'touch' }))
  await act(async () => { fireEvent.click(button, { detail: 1 }) })
  expect(screen.queryByText('结束试练')).toBeNull()
  expect(screen.getByRole('alert')).toHaveTextContent('横屏')
})

it('keeps the canvas mounted but disables new allocation after suite withdrawal', () => {
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={controller()} allowStart={false} onBack={vi.fn()} />)
  expect(screen.getByLabelText('MOBA 测评场景')).toBeVisible()
  expect(screen.getByRole('button', { name: '检查设备并进入试练' })).toBeDisabled()
})

it.each([
  ['mobile-moba', 'skill-aim', 'practice'], ['mobile-moba', 'skill-aim', 'formal'],
  ['mobile-fps', 'precision', 'practice'], ['mobile-fps', 'precision', 'formal'],
] as const)('settles %s %s %s immersion before freezing geometry and keeps it through saving', async (suite, test, mode) => {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  vi.stubGlobal('innerWidth', 1200)
  vi.stubGlobal('innerHeight', 800)
  let now = 0
  let frame: FrameRequestCallback | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  // jsdom has no layout. Model the real container expansion and canvas CSS size,
  // so a runtime allocated before the immersive viewport commit uses the old size.
  vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(function (this: HTMLElement) {
    const immersive = Boolean(this.closest('.assessmentHostImmersive'))
    const width = this.tagName === 'CANVAS' ? Number.parseFloat(this.parentElement!.style.width) : immersive ? 1000 : 800
    const y = immersive ? 58 : -120
    return { x: 0, y, width, height: width * 9 / 16, top: y, left: 0, right: width, bottom: y + width * 9 / 16, toJSON: () => ({}) }
  })
  const task = fixtureConfig(suite, test)
  const realFactory = suite === 'mobile-moba' ? mobileMobaFactory : mobileFpsFactory
  const factory: GameFactory = { create: vi.fn((...args: Parameters<GameFactory['create']>) => realFactory.create(...args)) }
  const session = controller()
  vi.mocked(session.start).mockImplementation(async request => {
    session.serverView = fixtureAttempt({ mode, session_id: mode === 'formal' ? '33333333-3333-4333-8333-333333333333' : null, suite_id: suite, test_id: test, config: task, settings: request.settings, environment: request.environment })
    return { attempt: session.serverView, leaseMs: 90000 }
  })
  const onBack = vi.fn()
  const rendered = render(<AssessmentHost config={task} factory={factory} session={session} mode={mode} onBack={onBack} />)
  const host = screen.getByRole('region', { name: '测试场景' })
  const canvas = screen.getByLabelText(suite === 'mobile-moba' ? 'MOBA 测评场景' : 'FPS 测试场景')
  expect(host).not.toHaveClass('assessmentHostImmersive')
  const enter = screen.getByText('检查设备并进入试练')
  fireEvent(enter, Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'touch' }))
  await act(async () => { fireEvent.click(enter, { detail: 1 }) })
  expect(host).toHaveClass('assessmentHostImmersive')
  expect(host).toHaveAttribute('data-host-phase', 'trial')
  expect(vi.mocked(factory.create).mock.calls.at(-1)?.[3]).toMatchObject({ width: 1000, height: 562.5 })
  const settled = canvas.getBoundingClientRect()
  act(() => { now = 100; frame?.(now) })
  expect(host).toHaveAttribute('data-host-phase', 'trial')
  fireEvent.click(screen.getByText('结束试练'))
  expect(host).toHaveClass('assessmentHostImmersive')
  expect(host).toHaveAttribute('data-host-phase', 'preflight')
  expect(screen.getByLabelText('灵敏度')).toBeEnabled()
  host.scrollTop = 300 // Settings/start controls remain reachable below the scene.
  await act(async () => { fireEvent.click(screen.getByText(mode === 'formal' ? '开始正式测试' : '开始练习')) })
  expect(host.scrollTop).toBe(0)
  expect(host).toHaveAttribute('data-host-phase', 'countdown')
  expect(vi.mocked(session.start).mock.calls[0][0].environment).toMatchObject({ width_css_px: 1000, height_css_px: 562.5 })
  for (now = 300; now <= 63100; now += 200) act(() => { frame?.(now) })
  expect(host).toHaveAttribute('data-host-phase', 'pending')
  expect(host).toHaveClass('assessmentHostImmersive')
  expect(session.submit).toHaveBeenCalledOnce()
  expect(session.interrupt).not.toHaveBeenCalled()
  const resultId = '55555555-5555-4555-8555-555555555555'
  session.saved = mode === 'practice'
    ? { mode, result_id: resultId, scored: { metrics: {}, score: null, grade: null, quality_flags: [] }, session: null }
    : { mode, result_id: resultId, scored: { metrics: {}, score: 50, grade: 'medium', quality_flags: [] }, session: { id: '33333333-3333-4333-8333-333333333333', suite_id: suite, suite_config: fixtureSuiteConfig(suite), status: 'in_progress', members: suite === 'mobile-moba' ? ['dodge', 'skill-aim', 'move-attack', 'map-awareness'] : ['precision', 'tracking', 'switching', 'move-fire'], results: [{ result_id: resultId, test_id: test, scored: { metrics: {}, score: 50, grade: 'medium', quality_flags: [] } }], report_id: null } }
  rendered.rerender(<AssessmentHost config={task} factory={factory} session={session} mode={mode} onBack={onBack} />)
  expect(host).toHaveClass('assessmentHostImmersive')
  expect(host).toHaveAttribute('data-host-phase', 'saved')
  expect(canvas).toBe(screen.getByLabelText(suite === 'mobile-moba' ? 'MOBA 测评场景' : 'FPS 测试场景'))
  expect(canvas.getBoundingClientRect()).toMatchObject({ x: settled.x, y: settled.y, width: settled.width, height: settled.height })
  fireEvent.click(screen.getByRole('button', { name: '返回项目列表' }))
  expect(onBack).toHaveBeenCalledOnce()
})

it.each(['formal', 'practice'] as const)('keeps the mobile %s field in place after a trial interruption', async mode => {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone')
  const session = controller()
  render(<AssessmentHost config={fixtureConfig('mobile-moba', 'skill-aim')} factory={mobileMobaFactory} session={session} mode={mode} onBack={vi.fn()} />)
  const enter = screen.getByText('检查设备并进入试练')
  fireEvent(enter, Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'touch' }))
  await act(async () => { fireEvent.click(enter, { detail: 1 }) })
  fireEvent.click(screen.getByRole('button', { name: '退出本项' }))
  const host = screen.getByRole('region', { name: '测试场景' })
  expect(host).toHaveAttribute('data-host-phase', 'interrupted')
  expect(host).toHaveClass('assessmentHostImmersive')
  expect(screen.getByRole('button', { name: '返回项目列表' })).toBeEnabled()
  expect(screen.getByLabelText('灵敏度')).toBeEnabled()
  expect(session.start).not.toHaveBeenCalled()
  expect(session.interrupt).not.toHaveBeenCalled()
})
