import { describe, expect, it } from 'vitest'
import { recognitionSchedule, validateRaw, validateSchedule } from '../contracts'
import type { GameEngine } from '../engineRuntime/types'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { angularDistanceDeg, vectorToDirection } from './math'
import { createRecognitionEngine } from './recognition'

const viewport = { width: 800, height: 450 }

function recognition(seed = 123): GameEngine {
  return createRecognitionEngine(fixtureConfig('pc-fps', 'recognition'), seed, fixtureSettings('pc-fps'), viewport)
}

function aimAtVisibleTarget(game: GameEngine, at_ms: number): void {
  game.advanceTo(at_ms)
  const scene = game.scene()
  if (scene.kind !== 'fps' || scene.targets.length !== 1) throw new Error('expected one visible FPS target')
  const target = scene.targets[0]
  aimAtDirection(game, at_ms, vectorToDirection({
    x: target.position.x - scene.camera.position.x,
    y: target.position.y - scene.camera.position.y,
    z: target.position.z - scene.camera.position.z,
  }))
}

function aimAtDirection(game: GameEngine, at_ms: number, direction: { yaw_deg: number; pitch_deg: number }): void {
  game.advanceTo(at_ms)
  const scene = game.scene()
  if (scene.kind !== 'fps') throw new Error('expected FPS scene')
  const dx = ((direction.yaw_deg - scene.camera.yaw_deg + 540) % 360) - 180
  game.input({ kind: 'look', at_ms, dx, dy: scene.camera.pitch_deg - direction.pitch_deg })
}

describe('recognition engine', () => {
  it('no input yields fifteen enemy misses and zero friendly shots', () => {
    const config = fixtureConfig('pc-fps', 'recognition')
    const game = createRecognitionEngine(config, 123, fixtureSettings('pc-fps'), viewport)
    game.advanceTo(60000)
    const raw = game.finish()
    if (raw.kind !== 'recognition') throw new Error('wrong raw kind')

    expect(raw.trials.filter(trial => trial.target_kind === 'enemy')).toHaveLength(15)
    expect(raw.trials.filter(trial => trial.target_kind === 'friend')).toHaveLength(15)
    expect(raw.trials.every(trial => !trial.shot && !trial.hit && trial.latency_ms === 1200)).toBe(true)
    expect(validateRaw(config, raw)).toEqual(raw)
    expect(() => validateSchedule(config, raw, 123)).not.toThrow()
    expect(game.finish()).toBe(raw)
  })

  it('uses the frozen seeded kind order, exact distance bins, and never resets the camera', () => {
    const seed = 0
    const game = recognition(seed)
    game.input({ kind: 'look', at_ms: 0, dx: 170, dy: -70 })
    const kinds: string[] = []
    const distances: number[] = []

    for (let round = 0; round < 30; round++) {
      game.advanceTo(round * 2000)
      const scene = game.scene()
      if (scene.kind !== 'fps') throw new Error('wrong scene kind')
      expect(scene.targets).toHaveLength(1)
      expect(scene.camera.yaw_deg).toBe(170)
      expect(scene.camera.pitch_deg).toBe(70)
      kinds.push(scene.targets[0].type)
      const position = scene.targets[0].position
      distances.push(Math.round(angularDistanceDeg(
        { yaw_deg: 0, pitch_deg: 0 },
        vectorToDirection({ x: position.x, y: position.y - 1.6, z: position.z }),
      )))
    }

    expect(kinds).toEqual(recognitionSchedule(seed))
    for (const distance of [10, 20, 30]) expect(distances.filter(value => value === distance)).toHaveLength(10)
  })

  it('lets only the first actual shot decide an enemy round', () => {
    const seed = 123
    const round = recognitionSchedule(seed).findIndex(kind => kind === 'enemy')
    const start = round * 2000
    const game = recognition(seed)

    game.advanceTo(start)
    const firstScene = game.scene()
    if (firstScene.kind !== 'fps') throw new Error('wrong scene kind')
    const firstTarget = firstScene.targets[0]
    const targetDirection = vectorToDirection({
      x: firstTarget.position.x - firstScene.camera.position.x,
      y: firstTarget.position.y - firstScene.camera.position.y,
      z: firstTarget.position.z - firstScene.camera.position.z,
    })

    game.input({ kind: 'look', at_ms: start, dx: 90, dy: 0 })
    game.input({ kind: 'fire', at_ms: start + 10, pressed: true })
    game.input({ kind: 'fire', at_ms: start + 11, pressed: false })
    aimAtDirection(game, start + 210, targetDirection)
    game.input({ kind: 'fire', at_ms: start + 210, pressed: true })
    game.input({ kind: 'fire', at_ms: start + 211, pressed: false })

    const raw = game.finish()
    if (raw.kind !== 'recognition') throw new Error('wrong raw kind')
    expect(raw.trials[round]).toEqual({ target_kind: 'enemy', shot: true, hit: false, latency_ms: 10 })
  })

  it('counts an empty first shot at a friend as a wrong shot', () => {
    const seed = 123
    const round = recognitionSchedule(seed).findIndex(kind => kind === 'friend')
    const start = round * 2000
    const game = recognition(seed)

    game.input({ kind: 'look', at_ms: start, dx: 90, dy: 0 })
    game.input({ kind: 'fire', at_ms: start + 25, pressed: true })
    game.input({ kind: 'fire', at_ms: start + 26, pressed: false })

    const raw = game.finish()
    if (raw.kind !== 'recognition') throw new Error('wrong raw kind')
    expect(raw.trials[round]).toEqual({ target_kind: 'friend', shot: true, hit: false, latency_ms: 25 })
  })

  it('uses real angular hit testing for an aimed enemy shot', () => {
    const seed = 123
    const round = recognitionSchedule(seed).findIndex(kind => kind === 'enemy')
    const start = round * 2000
    const game = recognition(seed)

    aimAtVisibleTarget(game, start + 100)
    game.input({ kind: 'fire', at_ms: start + 100, pressed: true })
    game.input({ kind: 'fire', at_ms: start + 101, pressed: false })

    const raw = game.finish()
    if (raw.kind !== 'recognition') throw new Error('wrong raw kind')
    expect(raw.trials[round]).toEqual({ target_kind: 'enemy', shot: true, hit: true, latency_ms: 100 })
  })

  it('hides the target at 1200 ms and rejects blank-period shots without delaying the next round', () => {
    const game = recognition(123)
    game.advanceTo(1199)
    expect(game.scene().kind === 'fps' && game.scene().targets).toHaveLength(1)
    game.advanceTo(1200)
    expect(game.scene().kind === 'fps' && game.scene().targets).toHaveLength(0)

    game.input({ kind: 'fire', at_ms: 1999, pressed: true })
    game.advanceTo(2000)
    const raw = game.finish()
    if (raw.kind !== 'recognition') throw new Error('wrong raw kind')
    expect(raw.trials[0].shot).toBe(false)
    expect(raw.trials[1].shot).toBe(true)
    expect(raw.trials[1].latency_ms).toBe(0)
  })
})
