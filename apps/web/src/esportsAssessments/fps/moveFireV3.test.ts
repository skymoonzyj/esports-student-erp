import { describe, expect, it } from 'vitest'

import { taskConfigSchema, validateRaw, type RawResult, type TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, GameEngine, InputAction } from '../engineRuntime/types'
import { mobileFpsFactory } from '../suites/mobileFps'
import { aimHighlighted, pulse } from '../testSupport/fpsActions'
import { fixtureSettings, fixtureV3Config } from '../testSupport/fixtures'
import { angularDistanceDeg, isTargetFullyVisible, vectorToDirection } from './math'

const viewport = { width: 844, height: 390 }

function moveFireV3Config(difficulty = 3): TaskConfig {
  const config = fixtureV3Config('mobile-fps', 'move-fire')
  if (config.params.kind !== 'move_fire_v3') throw new Error('fixture')
  const [size, distance] = [
    [1.5, .7], [1.25, .85], [1, 1], [.85, 1.1], [.7, 1.2],
  ][difficulty - 1]
  return taskConfigSchema.parse({
    ...config,
    difficulty,
    params: {
      ...config.params,
      target_diameter_deg: 6 * size,
      checkpoint_distance_m: 1.2 * distance,
      target_offset_min_deg: 12 * distance,
      target_offset_max_deg: 20 * distance,
    },
  })
}

function createGame(difficulty = 3, seed = 123): GameEngine {
  return mobileFpsFactory.create(
    moveFireV3Config(difficulty), seed, fixtureSettings('mobile-fps'), viewport,
  )
}

function fpsScene(game: GameEngine): FpsSceneSnapshot {
  const scene = game.scene()
  if (scene.kind !== 'fps') throw new Error('expected fps')
  return scene
}

function moveFireRaw(game: GameEngine): Extract<RawResult, { kind: 'move_fire_v3' }> {
  const raw = game.finish()
  if (raw.kind !== 'move_fire_v3') throw new Error('new raw required')
  return raw
}

function moveTowardCheckpoint(game: GameEngine, at_ms: number): void {
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

function expectOnlyFiniteNumbers(value: unknown): void {
  if (typeof value === 'number') {
    expect(Number.isFinite(value)).toBe(true)
    return
  }
  if (Array.isArray(value)) value.forEach(expectOnlyFiniteNumbers)
  else if (value && typeof value === 'object') Object.values(value).forEach(expectOnlyFiniteNumbers)
}

describe('move-fire v3 engine', () => {
  it.each([[99, false], [100, true]] as const)('requires continuous movement %i ms', (at, hit) => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    aimHighlighted(game, at)
    pulse(game, at)

    const raw = moveFireRaw(game)
    expect(raw.rounds[0].hit).toBe(hit)
    expect(raw.rounds[0].hit_latency_ms).toBe(hit ? 100 : null)
  })

  it('keeps a stationary-hit target active so a later qualified shot can hit it', () => {
    const game = createGame()
    aimHighlighted(game, 0)
    pulse(game, 0)

    expect(fpsScene(game).targets[0].state).toBe('highlighted')
    expect(fpsScene(game).movement_feedback).toEqual({ state: 'stationary-hit', qualifying_ms: 0 })

    game.input({ kind: 'move', at_ms: 100, x: 1, y: 0 })
    aimHighlighted(game, 200)
    pulse(game, 200)

    expect(fpsScene(game).targets[0].state).toBe('hit')
    expect(fpsScene(game).movement_feedback).toEqual({ state: 'hit', qualifying_ms: 100 })
    expect(moveFireRaw(game).rounds[0]).toEqual({
      hit: true,
      reached: false,
      hit_latency_ms: 200,
      hit_speed_m_s: 2,
      pre_hit_moving_ms: 100,
    })
  })

  it('does not turn a stationary shot followed only by checkpoint movement into a joint success', () => {
    const game = createGame()
    aimHighlighted(game, 0)
    pulse(game, 0)
    moveTowardCheckpoint(game, 1)
    game.input({ kind: 'move', at_ms: 501, x: 0, y: 0 })

    const raw = moveFireRaw(game)
    expect(raw.rounds[0]).toEqual({
      hit: false,
      reached: true,
      hit_latency_ms: null,
      hit_speed_m_s: null,
      pre_hit_moving_ms: null,
    })
    expect(raw.rounds.filter(round => round.hit && round.reached)).toHaveLength(0)
  })

  it.each([[.99, false], [1, true]] as const)('uses actual %.2f m/s movement at the speed boundary', (speed, hit) => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: speed / 2, y: 0 })
    aimHighlighted(game, 100)
    pulse(game, 100)

    const round = moveFireRaw(game).rounds[0]
    expect(round.hit).toBe(hit)
    expect(round.hit_speed_m_s).toBe(hit ? 1 : null)
  })

  it('keeps exact 1 m/s movement eligible in later rounds with accumulated world coordinates', () => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: .5, y: 0 })
    aimHighlighted(game, 3100)
    pulse(game, 3100)
    aimHighlighted(game, 57100)
    pulse(game, 57100)

    const raw = moveFireRaw(game)
    expect(raw.rounds[1]).toMatchObject({
      hit: true,
      hit_latency_ms: 100,
      hit_speed_m_s: 1,
      pre_hit_moving_ms: 100,
    })
    expect(raw.rounds[19]).toMatchObject({
      hit: true,
      hit_latency_ms: 100,
      hit_speed_m_s: 1,
      pre_hit_moving_ms: 100,
    })
  })

  it('keeps a tiny late full-speed segment finite and bounded by physical player speed', () => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    aimHighlighted(game, 57100.000001)
    pulse(game, 57100.000001)

    const round = moveFireRaw(game).rounds[19]
    expect(round.hit).toBe(true)
    expect(round.hit_speed_m_s).toBe(2)
    expect(round.hit_latency_ms).toBe(100.000001)
    expect(round.pre_hit_moving_ms).toBe(100.000001)
  })

  it.each([
    ['a repeated full-speed sample', 1, 0],
    ['a full-speed direction change', 0, 1],
  ] as const)('preserves current physical speed across %s at the shot timestamp', (_label, x, y) => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    aimHighlighted(game, 100)
    game.input({ kind: 'move', at_ms: 100, x, y })
    pulse(game, 100)

    expect(moveFireRaw(game).rounds[0]).toMatchObject({
      hit: true,
      hit_latency_ms: 100,
      hit_speed_m_s: 2,
      pre_hit_moving_ms: 100,
    })
  })

  it('resets continuous qualification immediately on a brief stop', () => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    game.input({ kind: 'move', at_ms: 60, x: 0, y: 0 })
    game.input({ kind: 'move', at_ms: 61, x: 1, y: 0 })
    aimHighlighted(game, 101)

    expect(fpsScene(game).movement_feedback).toEqual({ state: 'moving', qualifying_ms: 40 })
    pulse(game, 101)
    expect(moveFireRaw(game).rounds[0].hit).toBe(false)
  })

  it('resets earlier qualification after any below-threshold movement segment', () => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    game.input({ kind: 'move', at_ms: 100, x: .495, y: 0 })
    game.input({ kind: 'move', at_ms: 101, x: 1, y: 0 })
    aimHighlighted(game, 200)

    expect(fpsScene(game).movement_feedback).toEqual({ state: 'moving', qualifying_ms: 99 })
    pulse(game, 200)
    expect(moveFireRaw(game).rounds[0].hit).toBe(false)
  })

  it('does not fabricate movement when movement and shooting start at the same timestamp', () => {
    const game = createGame()
    aimHighlighted(game, 0)
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    game.input({ kind: 'fire', at_ms: 0, pressed: true })

    expect(fpsScene(game).movement_feedback).toEqual({ state: 'stationary-hit', qualifying_ms: 0 })
    game.input({ kind: 'fire', at_ms: 1, pressed: false })
    expect(moveFireRaw(game).rounds[0].hit).toBe(false)
  })

  it('resets movement qualification before the next round starts', () => {
    const game = createGame()
    game.input({ kind: 'move', at_ms: 2900, x: 1, y: 0 })
    game.advanceTo(3000)

    expect(fpsScene(game).movement_feedback).toEqual({ state: 'ready', qualifying_ms: 0 })
    aimHighlighted(game, 3100)
    pulse(game, 3100)

    const raw = moveFireRaw(game)
    expect(raw.rounds[0].hit).toBe(false)
    expect(raw.rounds[1].hit_latency_ms).toBe(100)
    expect(raw.rounds[1].pre_hit_moving_ms).toBe(100)
  })

  it('records movement-only and stationary-shooting-only rounds independently', () => {
    const moving = createGame()
    moveTowardCheckpoint(moving, 0)
    moving.input({ kind: 'move', at_ms: 500, x: 0, y: 0 })
    expect(moveFireRaw(moving).rounds[0]).toEqual({
      hit: false,
      reached: true,
      hit_latency_ms: null,
      hit_speed_m_s: null,
      pre_hit_moving_ms: null,
    })

    const shooting = createGame()
    aimHighlighted(shooting, 0)
    pulse(shooting, 0)
    const shootingRaw = moveFireRaw(shooting)
    expect(shootingRaw.rounds[0]).toEqual({
      hit: false,
      reached: false,
      hit_latency_ms: null,
      hit_speed_m_s: null,
      pre_hit_moving_ms: null,
    })
    expect(shootingRaw.shots).toBe(1)
  })

  it('finishes a fixed 20-by-3-second result with zero shots and finite bounded fields', () => {
    const config = moveFireV3Config()
    const raw = moveFireRaw(createGame())

    expect(raw.rounds).toHaveLength(20)
    expect(raw.shots).toBe(0)
    expect(raw.rounds.every(round => !round.hit && !round.reached)).toBe(true)
    expect(validateRaw(config, raw)).toEqual(raw)
    expectOnlyFiniteNumbers(raw)
  })

  it('replays movement identically at 30, 60, and 120 Hz', () => {
    const results = [30, 60, 120].map(hz => {
      const game = createGame()
      moveTowardCheckpoint(game, 0)
      const actions: InputAction[] = [{ kind: 'move', at_ms: 500, x: 0, y: 0 }]
      let nextAction = 0
      for (let frame = 1; frame <= hz * 60; frame += 1) {
        const at = frame * 1000 / hz
        while (nextAction < actions.length && actions[nextAction].at_ms <= at) {
          game.input(actions[nextAction++])
        }
        game.advanceTo(at)
      }
      return moveFireRaw(game)
    })

    expect(results[0]).toEqual(results[1])
    expect(results[1]).toEqual(results[2])
  })

  it('keeps successful-hit measurement fields identical at 30, 60, and 120 Hz', () => {
    const rounds = [30, 60, 120].map(hz => {
      const game = createGame()
      game.input({ kind: 'move', at_ms: 0, x: .5, y: 0 })
      for (let frame = 1; frame <= hz * 3.1; frame += 1) {
        game.advanceTo(frame * 1000 / hz)
      }
      aimHighlighted(game, 3100)
      pulse(game, 3100)
      return moveFireRaw(game).rounds[1]
    })

    expect(rounds[0]).toEqual({
      hit: true,
      reached: false,
      hit_latency_ms: 100,
      hit_speed_m_s: 1,
      pre_hit_moving_ms: 100,
    })
    expect(rounds[1]).toEqual(rounds[0])
    expect(rounds[2]).toEqual(rounds[0])
  })

  it.each([1, 2, 3, 4, 5])('uses the registered current-view offset and fixed world target at practice level %i', difficulty => {
    const config = moveFireV3Config(difficulty)
    const game = createGame(difficulty)
    const initial = fpsScene(game)
    const target = initial.targets[0]
    const direction = vectorToDirection({
      x: target.position.x - initial.camera.position.x,
      y: target.position.y - initial.camera.position.y,
      z: target.position.z - initial.camera.position.z,
    })
    if (config.params.kind !== 'move_fire_v3') throw new Error('fixture')
    const offset = angularDistanceDeg(initial.camera, direction)

    expect(offset).toBeGreaterThanOrEqual(config.params.target_offset_min_deg - 1e-10)
    expect(offset).toBeLessThanOrEqual(config.params.target_offset_max_deg + 1e-10)
    expect(offset).toBeGreaterThan(target.angular_diameter_deg / 2)
    expect(isTargetFullyVisible(direction, target.angular_diameter_deg / 2, initial.camera, viewport)).toBe(true)

    game.input({ kind: 'look', at_ms: 0, dx: 20, dy: -10 })
    game.input({ kind: 'move', at_ms: 0, x: 1, y: 0 })
    game.advanceTo(100)
    expect(fpsScene(game).targets[0].position).toEqual(target.position)
  })

  it('samples a new round target from the camera view at that round boundary', () => {
    const game = createGame()
    game.input({ kind: 'look', at_ms: 2900, dx: 30, dy: -20 })
    game.advanceTo(3000)
    const scene = fpsScene(game)
    const target = scene.targets[0]
    const direction = vectorToDirection({
      x: target.position.x - scene.camera.position.x,
      y: target.position.y - scene.camera.position.y,
      z: target.position.z - scene.camera.position.z,
    })
    const offset = angularDistanceDeg(scene.camera, direction)

    expect(offset).toBeGreaterThanOrEqual(12 - 1e-10)
    expect(offset).toBeLessThanOrEqual(20 + 1e-10)
    expect(isTargetFullyVisible(direction, target.angular_diameter_deg / 2, scene.camera, viewport)).toBe(true)
  })
})
