import { describe, expect, it } from 'vitest'

import { taskConfigSchema, type TaskConfig } from '../contracts'
import type { FpsSceneSnapshot } from '../engineRuntime/types'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { createMoveFireEngine, distance } from './moveFire'

const viewport = { width: 844, height: 390 }

function moveFireConfig(difficulty = 3): TaskConfig {
  const config = fixtureConfig('mobile-fps', 'move-fire')
  if (config.params.kind !== 'move_fire') throw new Error('fixture')
  const [size, checkpointDistance] = [
    [1.5, .84], [1.25, 1.02], [1, 1.2], [.85, 1.32], [.7, 1.44],
  ][difficulty - 1]
  return taskConfigSchema.parse({
    ...config,
    difficulty,
    params: {
      ...config.params,
      target_diameter_deg: 6 * size,
      checkpoint_distance_m: checkpointDistance,
    },
  })
}

function fpsScene(game: ReturnType<typeof createMoveFireEngine>): FpsSceneSnapshot {
  const scene = game.scene()
  if (scene.kind !== 'fps') throw new Error('wrong scene')
  return scene
}

function moveTowardCheckpoint(game: ReturnType<typeof createMoveFireEngine>, at_ms: number): void {
  const scene = fpsScene(game)
  const checkpoint = scene.checkpoints[0]
  const dx = checkpoint.position.x - scene.camera.position.x
  const dz = checkpoint.position.z - scene.camera.position.z
  const yaw = scene.camera.yaw_deg * Math.PI / 180
  const right = dx * Math.cos(yaw) + dz * Math.sin(yaw)
  const forward = dx * Math.sin(yaw) - dz * Math.cos(yaw)
  const length = Math.hypot(right, forward)
  game.input({ kind: 'move', at_ms, x: right / length, y: -forward / length })
}

describe('move-fire engine', () => {
  it('preserves joint outcomes by round through real scene input', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )

    for (let round = 0; round < 20; round += 1) {
      const start = round * 3000
      game.advanceTo(start)
      if (round < 10) {
        game.input({ kind: 'fire', at_ms: start, pressed: true })
        game.input({ kind: 'fire', at_ms: start + 1, pressed: false })
      } else {
        moveTowardCheckpoint(game, start)
        game.input({ kind: 'move', at_ms: start + 600, x: 0, y: 0 })
      }
      game.advanceTo(start + 3000)
    }

    const raw = game.finish()
    if (raw.kind !== 'move_fire') throw new Error('wrong raw kind')
    expect(raw.rounds).toHaveLength(20)
    expect(raw.rounds.slice(0, 10)).toEqual(Array.from(
      { length: 10 }, () => ({ hit: true, reached: false }),
    ))
    expect(raw.rounds.slice(10)).toEqual(Array.from(
      { length: 10 }, () => ({ hit: false, reached: true }),
    ))
    expect(raw.rounds.filter(round => round.hit && round.reached)).toHaveLength(0)
    expect(raw.shots).toBe(10)
  })

  it('finalizes old movement and hit state before same-boundary new input', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )
    moveTowardCheckpoint(game, 2570)

    game.input({ kind: 'move', at_ms: 3000, x: 0, y: 0 })
    game.input({ kind: 'fire', at_ms: 3000, pressed: true })
    game.input({ kind: 'fire', at_ms: 3001, pressed: false })
    const raw = game.finish()

    if (raw.kind !== 'move_fire') throw new Error('wrong raw kind')
    expect(raw.rounds[0]).toEqual({ hit: false, reached: true })
    expect(raw.rounds[1]).toEqual({ hit: true, reached: false })
  })

  it('uses each frozen practice distance once and keeps round geometry fixed', () => {
    for (const [difficulty, expectedDistance, expectedDiameter] of [
      [1, .84, 9], [2, 1.02, 7.5], [3, 1.2, 6], [4, 1.32, 5.1], [5, 1.44, 4.2],
    ] as const) {
      const game = createMoveFireEngine(
        moveFireConfig(difficulty), 123, fixtureSettings('mobile-fps'), viewport,
      )
      const initial = fpsScene(game)
      const target = initial.targets[0]
      const checkpoint = initial.checkpoints[0]
      expect(distance(initial.camera.position, checkpoint.position)).toBeCloseTo(expectedDistance, 10)
      expect(distance(initial.camera.position, target.position)).toBeCloseTo(10, 10)
      expect(target.angular_diameter_deg).toBeCloseTo(expectedDiameter, 10)

      game.input({ kind: 'look', at_ms: 0, dx: 45, dy: 0 })
      game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
      game.advanceTo(100)
      const moved = fpsScene(game)
      expect(moved.targets[0].position).toEqual(target.position)
      expect(moved.checkpoints[0].position).toEqual(checkpoint.position)
    }
  })

  it.each([
    [1, 2755],
    [2, 2665],
    [3, 2575],
    [4, 2515],
    [5, 2455],
  ] as const)('includes the exact checkpoint tolerance edge at difficulty %s', (difficulty, moveAt) => {
    const game = createMoveFireEngine(
      moveFireConfig(difficulty), 123, fixtureSettings('mobile-fps'), viewport,
    )
    moveTowardCheckpoint(game, moveAt)
    game.input({ kind: 'move', at_ms: 3000, x: 0, y: 0 })

    const raw = game.finish()
    if (raw.kind !== 'move_fire') throw new Error('wrong raw kind')
    expect(raw.rounds[0].reached).toBe(true)
  })

  it('uses the same inclusive edge for the live checkpoint state', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )
    moveTowardCheckpoint(game, 2574)
    game.input({ kind: 'move', at_ms: 2999, x: 0, y: 0 })

    expect(fpsScene(game).checkpoints[0].state).toBe('reached')
    const raw = game.finish()
    if (raw.kind !== 'move_fire') throw new Error('wrong raw kind')
    expect(raw.rounds[0].reached).toBe(true)
  })

  it('keeps a checkpoint one millimetre outside the tolerance unreached', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )
    moveTowardCheckpoint(game, 2575.5)
    game.input({ kind: 'move', at_ms: 3000, x: 0, y: 0 })

    const raw = game.finish()
    if (raw.kind !== 'move_fire') throw new Error('wrong raw kind')
    expect(raw.rounds[0].reached).toBe(false)
  })

  it('uses the seed to choose reproducible left and right checkpoints', () => {
    const create = (seed: number) => fpsScene(createMoveFireEngine(
      moveFireConfig(), seed, fixtureSettings('mobile-fps'), viewport,
    )).checkpoints[0].position

    expect(create(123)).toEqual(create(123))
    expect(Math.sign(create(1).x)).toBe(-1)
    expect(Math.sign(create(123456789).x)).toBe(1)
  })

  it('normalizes diagonal camera-relative movement and freezes the final result', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )
    game.input({ kind: 'move', at_ms: 0, x: 1, y: -1 })
    game.advanceTo(1000)
    const scene = fpsScene(game)
    expect(Math.hypot(scene.camera.position.x, scene.camera.position.z)).toBeCloseTo(2, 8)

    const raw = game.finish()
    expect(Object.isFrozen(raw)).toBe(true)
    expect(game.finish()).toBe(raw)
  })

  it('counts every accepted shot after the round target has already been hit', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    game.advanceTo(1000)
    game.input({ kind: 'fire', at_ms: 1000, pressed: false })

    const raw = game.finish()
    if (raw.kind !== 'move_fire') throw new Error('wrong raw kind')
    expect(raw.rounds[0].hit).toBe(true)
    expect(raw.shots).toBe(6)
  })

  it('does not expose mutable target or checkpoint positions through scene snapshots', () => {
    const game = createMoveFireEngine(
      moveFireConfig(), 123, fixtureSettings('mobile-fps'), viewport,
    )
    const first = fpsScene(game)
    const expectedTarget = { ...first.targets[0].position }
    const expectedCheckpoint = { ...first.checkpoints[0].position }

    ;(first.targets[0].position as { x: number }).x = 999
    ;(first.checkpoints[0].position as { z: number }).z = 999

    const next = fpsScene(game)
    expect(next.targets[0].position).toEqual(expectedTarget)
    expect(next.checkpoints[0].position).toEqual(expectedCheckpoint)
  })
})
