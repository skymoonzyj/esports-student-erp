import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type * as LazyModule from '../app/loading/lazyModule'
import type { SessionView, TestId } from './contracts'

vi.mock('../auth/AuthProvider', () => ({ useAuth: () => ({ user: { account_id: 'loading-account' }, status: 'authenticated' }) }))

const observed = vi.hoisted(() => ({
  loaders: new Map<string, ReturnType<typeof vi.fn>>(),
  delay: null as Promise<void> | null,
  submit: vi.fn(),
  session: null as SessionView | null,
}))
vi.mock('../app/loading/lazyModule', async original => {
  const actual = await original<typeof LazyModule>()
  return { ...actual, createLazyModule: (id: string, loader: Parameters<typeof actual.createLazyModule>[1]) => {
    const run = vi.fn(async () => { if (observed.delay) await observed.delay; return loader() })
    observed.loaders.set(id, run)
    return actual.createLazyModule(id, run)
  } }
})
vi.mock('./useTalentSession', () => ({ useTalentSession: () => ({
  session: observed.session, pendingResult: null, readState: 'ready', saveState: 'idle',
  statusText: null, retryAfterSeconds: 0, submit: observed.submit, retry: vi.fn(), reread: vi.fn(),
}) }))

const ids: TestId[] = ['reactiontime', 'aim', 'sequence', 'number-memory', 'verbal-memory', 'chimp', 'memory', 'dynamic-vision']
function session(): SessionView {
  return {
    id: 'loading-session', person_id: 'loading-person', status: 'in_progress',
    created_at: '2026-10-19T00:00:00Z', completed_at: null, abandoned_at: null, report_id: null, results: [],
    rules: {
      catalog_version: 'talent-training-v3', scoring_version: 'talent-score-v3', report_version: 'talent-report-v3', test_ids: ids,
      tests: {
        reactiontime: { engine_version: 'reactiontime-clone-v1', anchors: [500, 275, 225, 175, 150, 100], source_label: '训练', unit: 'ms' },
        aim: { engine_version: 'aim-clone-v1', anchors: [1000, 550, 450, 350, 300, 250], source_label: '训练', unit: 'ms' },
        sequence: { engine_version: 'sequence-clone-v1', anchors: [0, 8, 10, 18, 25, 35], source_label: '训练', unit: 'level' },
        'number-memory': { engine_version: 'number-memory-clone-v1', anchors: [0, 7, 9, 13, 16, 20], source_label: '训练', unit: 'digits' },
        'verbal-memory': { engine_version: 'verbal-memory-clone-v1', anchors: [0, 20, 50, 100, 200, 300], source_label: '训练', unit: 'count' },
        chimp: { engine_version: 'chimp-clone-v1', anchors: [0, 9, 11, 15, 20, 25], source_label: '训练', unit: 'count' },
        memory: { engine_version: 'memory-clone-v1', anchors: [0, 6, 10, 18, 25, 35], source_label: '训练', unit: 'level' },
        'dynamic-vision': { kind: 'dynamic_vision', engine_version: 'dynamic-vision-v2', source_label: '内部试行训练规则', unit: 'count', base_points_per_correct: 10, max_score: 100, levels: [
          { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
          { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
          { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
          { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
          { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
          { level: 6, digit_count: 7, nominal_display_ms: 36, coefficient_tenths: 22 },
        ] },
      },
    },
  }
}
async function host(testId: TestId) {
  const { GameHost } = await import('./GameHost')
  return render(<MemoryRouter><GameHost accountId="loading-account" personId="loading-person" sessionId="loading-session" testId={testId} /></MemoryRouter>)
}
beforeEach(() => {
  vi.resetModules()
  observed.loaders.clear(); observed.delay = null; observed.submit.mockReset(); observed.session = session()
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} })
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ width: 800, height: 420, x: 0, y: 0, top: 0, right: 800, bottom: 420, left: 0, toJSON: () => ({}) })
})
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

// An eager import, wrong selected loader, or speculative prefetch must fail.
it.each(ids)('host_loads_only_selected_game: %s', async testId => {
  await host(testId)
  const names: Partial<Record<TestId, string>> = { reactiontime: '反应时间测试区域', aim: '点击目标开始', 'number-memory': '开始数字记忆', chimp: '开始黑猩猩测试', memory: '开始视觉记忆' }
  await screen.findByRole('button', { name: names[testId] ?? '开始测试' })
  expect(observed.loaders.has(`talent-game-${testId}`)).toBe(true)
  expect(observed.loaders.get(`talent-game-${testId}`)).toHaveBeenCalledTimes(1)
  for (const id of ids.filter(id => id !== testId)) expect(observed.loaders.get(`talent-game-${id}`)).not.toHaveBeenCalled()
})

it('loading_is_not_attempt_time: five real reaction responses exclude a 5000ms import delay', async () => {
  let ready!: () => void
  observed.delay = new Promise(resolve => { ready = resolve })
  await host('reactiontime')
  vi.useFakeTimers()
  await act(async () => { vi.advanceTimersByTime(5000) })
  expect(screen.queryByTestId('reaction-game')).not.toBeInTheDocument()
  expect(observed.submit).not.toHaveBeenCalled()
  await act(async () => { ready() })
  // Import evaluation is asynchronous independently of the controlled game clock.
  vi.useRealTimers()
  const target = await screen.findByTestId('reaction-game')
  vi.useFakeTimers()
  vi.spyOn(Math, 'random').mockReturnValue(0)
  const press = () => fireEvent.pointerDown(target, { isPrimary: true, button: 0, pointerType: 'mouse' })
  for (let sample = 0; sample < 5; sample++) {
    press()
    await act(async () => { vi.advanceTimersByTime(2520) })
    expect(screen.getByRole('heading', { name: '点击！' })).toBeVisible()
    await act(async () => { vi.advanceTimersByTime(200) })
    press()
  }
  expect(observed.submit).toHaveBeenCalledTimes(1)
  expect(observed.submit.mock.calls[0][0].raw.samples_ms).toHaveLength(5)
  expect(observed.submit.mock.calls[0][0].raw.samples_ms.every((ms: number) => ms >= 200 && ms <= 216)).toBe(true)
  expect(observed.submit.mock.calls[0][0].duration_ms).toBe(13600)
  press()
  expect(observed.submit).toHaveBeenCalledTimes(1)
})

it('history and reports never invoke game loaders', async () => {
  await import('./TalentHistoryPage')
  await import('./TalentReportPage')
  expect([...observed.loaders.values()].filter(loader => loader.mock.calls.length)).toEqual([])
})

it('loading never starts a dynamic-vision attempt before the real user gesture', async () => {
  let ready!: () => void
  observed.delay = new Promise(resolve => { ready = resolve })
  const { DynamicVisionGame } = await import('./lazyGames')
  const computedStyle = window.getComputedStyle.bind(window)
  vi.spyOn(window, 'getComputedStyle').mockImplementation(element => {
    const style = computedStyle(element)
    Object.defineProperty(style, 'fontSize', { configurable: true, value: '56px' })
    return style
  })
  const onAttemptStarted = vi.fn(), onComplete = vi.fn(), onInterrupted = vi.fn()
  render(<DynamicVisionGame onAttemptStarted={onAttemptStarted} onComplete={onComplete} onInterrupted={onInterrupted} />)
  vi.useFakeTimers()
  await act(async () => { vi.advanceTimersByTime(5000) })
  expect(screen.queryByRole('button', { name: '开始测试' })).not.toBeInTheDocument()
  expect(onAttemptStarted).not.toHaveBeenCalled()
  expect(onComplete).not.toHaveBeenCalled()
  await act(async () => { ready() })
  vi.useRealTimers()
  const start = await screen.findByRole('button', { name: '开始测试' })
  expect(onAttemptStarted).not.toHaveBeenCalled()
  fireEvent.click(start)
  expect(onAttemptStarted).toHaveBeenCalledTimes(1)
  expect(onComplete).not.toHaveBeenCalled()
})
