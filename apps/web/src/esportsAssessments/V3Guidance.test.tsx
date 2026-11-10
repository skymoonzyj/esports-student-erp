import { act, fireEvent, render, screen } from '@testing-library/react'
import { useEffect } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AssessmentHost } from './AssessmentHost'
import { MobaFeedback } from './MobaFeedback'
import { MobaInstructions } from './MobaInstructions'
import type { AssessmentSessionController } from './useAssessmentSession'
import type { GameFactory } from './engineRuntime/types'
import { fixtureAttempt, fixtureSettings, fixtureV3Config } from './testSupport/fixtures'
import { fixtureV4Config } from './testSupport/fixturesV4'
import { mobileFpsFactory } from './suites/mobileFps'
import { pcFpsFactory } from './suites/pcFps'
import { pcMobaFactory } from './suites/pcMoba'
import { mobileMobaFactory } from './suites/mobileMoba'

vi.mock('./moba/MobaStage', () => ({ MobaStage: ({ canvasRef, inputHandlers, onReady }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object; onReady: () => void }) => {
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="MOBA 测评场景" {...inputHandlers} />
} }))
vi.mock('./fps/FpsStage', () => ({ FpsStage: ({ canvasRef, inputHandlers, onReady }: { canvasRef: React.Ref<HTMLCanvasElement>; inputHandlers: object; onReady: () => void }) => {
  useEffect(() => { onReady() }, [onReady])
  return <canvas ref={canvasRef} aria-label="FPS 测试场景" {...inputHandlers} />
} }))

function controller(config = fixtureV3Config('pc-moba', 'dodge')): AssessmentSessionController {
  return {
    accountId: 'owner', canRestart: true, ready: true, busy: false, error: null, revoked: false,
    volatile: false, leaseMs: 90000, saved: null, pending: null, serverView: null,
    start: vi.fn(async () => ({ attempt: fixtureAttempt({ suite_id: config.suite_id, test_id: config.test_id, config }), leaseMs: 90000 })),
    submit: vi.fn(), interrupt: vi.fn(), leave: vi.fn(async () => true), reconcile: vi.fn(), retry: vi.fn(),
  }
}

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) })
})
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('explains the two-target v3 switching sequence instead of generic repeated aiming', () => {
  const config = fixtureV3Config('pc-fps', 'switching')
  render(<AssessmentHost config={config} factory={pcFpsFactory} session={controller(config)} onBack={vi.fn()} />)
  expect(screen.getByText(/首次命中后第二靶出现/)).toBeVisible()
  expect(screen.getByText(/第二靶.*从首次命中时刻开始/)).toBeVisible()
})
it.each(['gridshot', 'sixshot'])('explains individual clicks and retained targets for %s', test => {
  const config = fixtureV4Config('pc-fps', test)
  render(<AssessmentHost config={config} factory={pcFpsFactory} session={controller(config)} onBack={vi.fn()} />)
  expect(screen.getByText(/每次左键按下只打一枪/)).toBeVisible()
  expect(screen.getByText(/长按不连发/)).toBeVisible()
  expect(screen.getByText(/其余目标保留/)).toBeVisible()
})
it('does not ask the no-fire spheretrack90 participant to shoot', () => {
  const config = fixtureV4Config('pc-fps', 'spheretrack90')
  render(<AssessmentHost config={config} factory={pcFpsFactory} session={controller(config)} onBack={vi.fn()} />)
  expect(screen.getByText(/无需开火.*准星覆盖目标/)).toBeVisible()
  expect(screen.queryByText(/移动鼠标瞄准，左键射击/)).toBeNull()
})
it('keeps the provisional ERP disclaimer visible after a v4 result is saved', () => {
  const config = fixtureV4Config('pc-fps', 'gridshot')
  const session = controller(config)
  session.saved = { mode: 'practice', result_id: '55555555-5555-4555-8555-555555555555', scored: { metrics: { hits: 20 }, score: null, grade: null, quality_flags: [] }, session: null }
  render(<AssessmentHost config={config} factory={pcFpsFactory} session={session} mode="practice" onBack={vi.fn()} />)
  expect(screen.getByText(/ERP 暂定场景规则/)).toBeVisible()
})

it('states the registered duration and half-speed threshold for v3 moving hits', () => {
  const config = fixtureV3Config('mobile-fps', 'move-fire')
  render(<AssessmentHost config={config} factory={mobileFpsFactory} session={controller(config)} onBack={vi.fn()} />)
  expect(screen.getByText(/移动中命中才有效/)).toBeVisible()
  expect(screen.getByText(/持续 100 毫秒/)).toBeVisible()
  expect(screen.getByText(/满速的一半/)).toBeVisible()
})

it.each([
  ['ready', '等待有效移动'], ['moving', '已连续有效移动 64 ms'],
  ['stationary-hit', '本次命中无效：命中前未满足移动条件'], ['hit', '本次为有效移动命中'],
] as const)('keeps %s movement feedback in the reserved toolbar without inventing a score', async (state, expected) => {
  vi.spyOn(navigator, 'userAgent', 'get').mockReturnValue('iPhone')
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  vi.stubGlobal('innerWidth', 844)
  vi.stubGlobal('innerHeight', 390)
  const config = fixtureV3Config('mobile-fps', 'move-fire')
  const factory: GameFactory = { create(...args) {
    const engine = mobileFpsFactory.create(...args)
    return {
      advanceTo: value => engine.advanceTo(value), input: action => engine.input(action), finish: () => engine.finish(),
      scene: () => {
        const scene = engine.scene()
        if (scene.kind !== 'fps') throw new Error('expected FPS scene')
        return { ...scene, movement_feedback: { state, qualifying_ms: 64 } }
      },
    }
  } }
  render(<AssessmentHost config={config} factory={factory} session={controller(config)} mode="practice" onBack={vi.fn()} />)
  const enter = screen.getByRole('button', { name: '检查设备并进入试练' })
  fireEvent(enter, Object.assign(new Event('pointerdown', { bubbles: true }), { pointerId: 1, pointerType: 'touch' }))
  await act(async () => { fireEvent.click(enter, { detail: 1 }) })
  const feedback = screen.getByRole('region', { name: '移动射击反馈' })
  expect(feedback).toHaveTextContent('移动中命中才有效')
  expect(feedback).toHaveTextContent(expected)
  expect(feedback).not.toHaveTextContent(/成功率|分数/)
  // Mobile only reserves enough vertical space for the toolbar, not a row
  // below the canvas. Both feedback texts must occupy that reserved header.
  expect(feedback.closest('.assessmentHostToolbar')).not.toBeNull()
})

it('explains full safe-circle entry and staying safe through the v3 burst', () => {
  render(<MobaInstructions config={fixtureV3Config('pc-moba', 'dodge')} />)
  const guide = screen.getByRole('region', { name: '本项测试说明' })
  expect(guide).toHaveTextContent('完整进入安全圆')
  expect(guide).toHaveTextContent('爆发期间保持安全')
  expect(guide).not.toHaveTextContent('首次移动时间')
})

it('uses the config-aware host label for v3 danger-area dodge', () => {
  const config = fixtureV3Config('pc-moba', 'dodge')
  render(<AssessmentHost config={config} factory={pcMobaFactory} session={controller(config)} onBack={vi.fn()} />)
  expect(screen.getByRole('heading', { name: '危险区域识别与躲避' })).toBeVisible()
  expect(screen.queryByRole('heading', { name: '威胁躲避' })).not.toBeInTheDocument()
})

it('keeps unchanged v3 MOBA controls on their explicit v2-compatible guidance', () => {
  const pc = render(<MobaInstructions config={fixtureV3Config('pc-moba', 'orb-walk')} />)
  expect(screen.getByRole('region', { name: '本项测试说明' })).toHaveTextContent('左键或右键单击')
  pc.unmount()
  render(<MobaInstructions config={fixtureV3Config('mobile-moba', 'skill-aim')} />)
  expect(screen.getByRole('region', { name: '本项测试说明' })).toHaveTextContent('蓝色瞄准线')
})

it('keeps direct-touch map controls for explicitly registered mobile MOBA v3', () => {
  const config = fixtureV3Config('mobile-moba', 'map-awareness')
  render(<AssessmentHost config={config} factory={mobileMobaFactory} session={controller(config)} onBack={vi.fn()} />)
  expect(screen.getByText('本项直接点选，无需摇杆或攻击按钮设置。')).toBeVisible()
  expect(screen.queryByRole('region', { name: '操作设置' })).not.toBeInTheDocument()
})

it('uses danger-choice phases in a fixed feedback container without moving its node', () => {
  const game = pcMobaFactory.create(fixtureV3Config('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba'), { width: 1000, height: 562.5 })
  game.advanceTo(418)
  const warning = game.scene()
  if (warning.kind !== 'moba') throw new Error('expected MOBA scene')
  const view = render(<MobaFeedback scene={warning} />)
  const region = screen.getByRole('region', { name: '测试反馈' })
  expect(region).toHaveClass('assessmentMobaFeedback')
  expect(region).toHaveTextContent('完整进入安全圆')
  game.advanceTo(1418)
  const active = game.scene()
  if (active.kind !== 'moba') throw new Error('expected MOBA scene')
  view.rerender(<MobaFeedback scene={active} />)
  expect(screen.getByRole('region', { name: '测试反馈' })).toBe(region)
  expect(region).toHaveTextContent('爆发期间保持安全')
})
