import { describe, expect, it } from 'vitest'

import { isInputInWindow } from './types'
import type {
  GameEngine,
  GameFactory,
  InputAction,
  SceneSnapshot,
} from './types'

type FactoryViewport = Parameters<GameFactory['create']>[3]

describe('runtime contracts', () => {
  it('keeps render snapshots discriminated from result metrics', () => {
    const scenes: readonly SceneSnapshot[] = [
      {
        kind: 'fps',
        at_ms: 60_000,
        camera: {
          yaw_deg: 12,
          pitch_deg: -4,
          position: { x: 0, y: 1.6, z: 0 },
        },
        targets: [{
          id: 'target-1',
          position: { x: 1, y: 2, z: -10 },
          angular_diameter_deg: 4,
          type: 'enemy',
          state: 'highlighted',
        }],
        checkpoints: [],
      },
      {
        kind: 'moba',
        at_ms: 60_000,
        logical_size: { width: 1000, height: 562.5 },
        player: {
          id: 'player',
          position: { x: 500, y: 281.25 },
          radius: 12,
          facing_rad: 0,
        },
        targets: [],
        projectiles: [{
          id: 'threat-1',
          type: 'threat',
          position: { x: 400, y: 281.25 },
          origin: { x: 260, y: 281.25 },
          target: { x: 500, y: 281.25 },
          radius: 10,
          state: 'warning',
        }],
        minimap: {
          visible: false,
          bounds: { x: 820, y: 0, width: 180, height: 110 },
          event: null,
        },
        feedback: {
          version: 'v2',
          task_id: 'dodge',
          round: { current: 1, total: 30, status: 'active' },
          outcomes: [],
        },
      },
    ]

    expect(scenes.map(scene => scene.kind)).toEqual(['fps', 'moba'])
    expect('metrics' in scenes[0]).toBe(false)
  })

  it('supports an explicit skill cancellation without adding another action kind', () => {
    const actions: readonly InputAction[] = [
      { kind: 'look', at_ms: 1, dx: 2, dy: -1 },
      { kind: 'move', at_ms: 2, x: 1, y: 0 },
      { kind: 'fire', at_ms: 3, pressed: true },
      { kind: 'point', at_ms: 4, x: 500, y: 200, button: 2 },
      { kind: 'skill', at_ms: 5, x: 600, y: 200, release: false, cancel: true },
    ]

    expect(actions.map(action => action.kind)).toEqual([
      'look',
      'move',
      'fire',
      'point',
      'skill',
    ])
    expect(actions[4]).toMatchObject({ release: false, cancel: true })
  })

  it('allows engines to finish at the simulation boundary', () => {
    const advances: number[] = []
    const engine: GameEngine = {
      input: () => undefined,
      advanceTo: at => advances.push(at),
      scene: () => ({
        kind: 'fps',
        at_ms: 60_000,
        camera: { yaw_deg: 0, pitch_deg: 0, position: { x: 0, y: 0, z: 0 } },
        targets: [],
        checkpoints: [],
      }),
      finish: () => ({
        kind: 'tracking',
        coverage_ms: 0,
        loss_count: 0,
        reacquisition_ms: [],
      }),
    }

    engine.advanceTo(60_000)

    expect(advances).toEqual([60_000])
    expect(engine.finish().kind).toBe('tracking')
  })

  it('accepts input only in the half-open formal interval', () => {
    const action = (at_ms: number): InputAction => ({
      kind: 'fire',
      at_ms,
      pressed: true,
    })

    expect(isInputInWindow(action(0))).toBe(true)
    expect(isInputInWindow(action(59_999.999))).toBe(true)
    expect(isInputInWindow(action(-0.001))).toBe(false)
    expect(isInputInWindow(action(60_000))).toBe(false)
    expect(isInputInWindow(action(Number.NaN))).toBe(false)
  })

  it('requires the frozen stage viewport when a game is created', () => {
    const viewport: FactoryViewport = { width: 800, height: 450 }

    expect(viewport).toEqual({ width: 800, height: 450 })
  })
})
