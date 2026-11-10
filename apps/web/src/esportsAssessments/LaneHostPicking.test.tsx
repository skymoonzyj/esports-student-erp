import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type * as ThreeModule from 'three'
import { AssessmentHost } from './AssessmentHost'
import { pcMobaFactory } from './suites/pcMoba'
import { fixtureAttempt } from './testSupport/fixtures'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { laneWorldToScreen } from './lane/projection'
import type { AssessmentSessionController } from './useAssessmentSession'
import type { GameEngine, GameFactory, MobaSceneSnapshot } from './engineRuntime/types'

vi.mock('three', async original => ({ ...await original<typeof ThreeModule>(), WebGLRenderer: class {
  setPixelRatio() {} setSize() {} render() {} dispose() {}
} }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it.each(['melee', 'ranged'] as const)('routes actual Host pointer input through the rendered crowded body to the %s engine', async mode => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, width: 1000, height: 562.5, top: 0, left: 0, right: 1000, bottom: 562.5, toJSON: () => ({}) })
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as WebGL2RenderingContext)
  let now = 0
  let frame: FrameRequestCallback | undefined
  vi.spyOn(performance, 'now').mockImplementation(() => now)
  vi.stubGlobal('requestAnimationFrame', (next: FrameRequestCallback) => { frame = next; return 1 })
  vi.stubGlobal('cancelAnimationFrame', () => { frame = undefined })
  const config = fixtureMobaV4Config(mode)
  let engine: GameEngine | undefined
  const factory: GameFactory = { create(...args) { engine = pcMobaFactory.create(...args); return engine } }
  const session: AssessmentSessionController = {
    accountId: 'owner', canRestart: true, ready: true, busy: false, error: null, revoked: false, volatile: false, leaseMs: 330000, saved: null, pending: null, serverView: null,
    start: async settings => {
      session.serverView = fixtureAttempt({ suite_id: 'pc-moba', test_id: 'last-hit', config, settings: settings.settings, environment: settings.environment })
      return { attempt: session.serverView, leaseMs: 330000 }
    }, submit: vi.fn(), interrupt: vi.fn(), leave: async () => true, reconcile: async () => null, retry: async () => {},
  }
  render(<AssessmentHost config={config} factory={factory} session={session} onBack={() => {}} />)
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '检查设备并进入试练' })) })
  fireEvent.click(screen.getByRole('button', { name: '结束试练' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '开始正式测试' })) })
  for (now = 200; now <= 33600; now += 200) act(() => { frame?.(now) })
  now = 33600
  const before = engine!.scene() as MobaSceneSnapshot
  expect(before.at_ms).toBe(30600)
  const target = before.lane!.units.find(unit => unit.id === 'enemy-1-2')!
  const point = laneWorldToScreen(target.position, 27)
  fireEvent.pointerDown(screen.getByLabelText('MOBA 兵线测评场景'), { pointerId: 1, pointerType: 'mouse', button: 0, clientX: point.x, clientY: point.y })
  expect((engine!.scene() as MobaSceneSnapshot).lane!.selected_target_id).toBe('enemy-1-2')
  expect(session.interrupt).not.toHaveBeenCalled()
  expect(session.submit).not.toHaveBeenCalled()
})
