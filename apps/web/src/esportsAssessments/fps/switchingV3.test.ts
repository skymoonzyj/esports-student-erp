import { Object3D, PerspectiveCamera, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'
import { previewTaskConfig, validateRaw, type RawResult, type TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { mobileFpsFactory } from '../suites/mobileFps'
import { pcFpsFactory } from '../suites/pcFps'
import { aimHighlighted, pulse } from '../testSupport/fpsActions'
import { fixtureSettings, fixtureV3Config } from '../testSupport/fixtures'
import { angularDistanceDeg, vectorToDirection } from './math'

const suites = ['pc-fps', 'mobile-fps'] as const
const viewport = { width: 844, height: 390 }

function create(suite: typeof suites[number], config: TaskConfig = fixtureV3Config(suite, 'switching')): GameEngine {
  const factory = suite === 'pc-fps' ? pcFpsFactory : mobileFpsFactory
  return factory.create(config, 123, fixtureSettings(suite), viewport)
}

function sceneOf(game: GameEngine): FpsSceneSnapshot {
  const scene = game.scene()
  if (scene.kind !== 'fps') throw new Error('fps required')
  return scene
}

function aimRaw(result: RawResult) {
  if (result.kind !== 'aim') throw new Error('aim required')
  return result
}

// Independent renderer-space check: the spawn predicate is deliberately not the oracle.
function expectFullyProjected(scene: FpsSceneSnapshot, size: Size): void {
  const camera = new PerspectiveCamera(
    2 * Math.atan(size.height / size.width) * 180 / Math.PI,
    size.width / size.height,
    .01,
    1000,
  )
  camera.position.set(scene.camera.position.x, scene.camera.position.y, scene.camera.position.z)
  camera.rotation.set(
    scene.camera.pitch_deg * Math.PI / 180,
    -scene.camera.yaw_deg * Math.PI / 180,
    0,
    'YXZ',
  )
  camera.updateMatrixWorld()
  expect(scene.targets).toHaveLength(1)

  const target = scene.targets[0]
  const disk = new Object3D()
  disk.position.set(target.position.x, target.position.y, target.position.z)
  disk.lookAt(camera.position)
  disk.scale.setScalar(
    disk.position.distanceTo(camera.position)
      * Math.tan(target.angular_diameter_deg * Math.PI / 360),
  )
  disk.updateMatrixWorld()
  for (let edge = 0; edge < 32; edge++) {
    const bearing = edge * Math.PI / 16
    const projected = new Vector3(Math.cos(bearing), Math.sin(bearing), 0)
      .applyMatrix4(disk.matrixWorld)
      .project(camera)
    expect(Math.abs(projected.x), `${target.id} horizontal edge`).toBeLessThanOrEqual(1 + 1e-8)
    expect(Math.abs(projected.y), `${target.id} vertical edge`).toBeLessThanOrEqual(1 + 1e-8)
    expect(projected.z).toBeGreaterThan(-1)
    expect(projected.z).toBeLessThan(1)
  }
}

describe('v3 switching engine', () => {
  it.each(suites)('measures actual switching: %s', suite => {
    const game = create(suite)

    expect(game.scene().targets).toHaveLength(1)
    aimHighlighted(game, 200)
    pulse(game, 200)
    aimHighlighted(game, 1400)
    pulse(game, 1400)

    const raw = aimRaw(game.finish())
    expect(raw.trials.slice(0, 2)).toEqual([
      { hit: true, latency_ms: 200 },
      { hit: true, latency_ms: 1200 },
    ])
  })

  it.each(suites)('fails the whole group when the first target reaches its exclusive deadline: %s', suite => {
    const game = create(suite)
    game.advanceTo(1500)
    expect(sceneOf(game).targets).toHaveLength(0)
    game.advanceTo(2999)
    expect(sceneOf(game).targets).toHaveLength(0)
    game.advanceTo(3000)
    expect(sceneOf(game).targets).toHaveLength(1)

    const raw = aimRaw(game.finish())
    expect(raw.trials).toHaveLength(40)
    expect(raw.trials.slice(0, 2)).toEqual([
      { hit: false, latency_ms: 1500 },
      { hit: false, latency_ms: 1500 },
    ])
    expect(validateRaw(fixtureV3Config(suite, 'switching'), raw)).toEqual(raw)
  })

  it('accepts 1499ms on each target but rejects the second target at its 1500ms right edge', () => {
    const valid = create('pc-fps')
    aimHighlighted(valid, 1499)
    pulse(valid, 1499)
    aimHighlighted(valid, 2998)
    pulse(valid, 2998)
    expect(aimRaw(valid.finish()).trials.slice(0, 2)).toEqual([
      { hit: true, latency_ms: 1499 },
      { hit: true, latency_ms: 1499 },
    ])

    const expired = create('pc-fps')
    aimHighlighted(expired, 1499)
    pulse(expired, 1499)
    aimHighlighted(expired, 2998)
    expired.input({ kind: 'fire', at_ms: 2999, pressed: true })
    expired.input({ kind: 'fire', at_ms: 2999.5, pressed: false })
    expect(sceneOf(expired).targets).toHaveLength(0)
    const raw = aimRaw(expired.finish())
    expect(raw.shots).toBe(2)
    expect(raw.trials.slice(0, 2)).toEqual([
      { hit: true, latency_ms: 1499 },
      { hit: false, latency_ms: 1500 },
    ])
  })

  it('uses the shared 200ms cooldown during sustained fire', () => {
    const game = create('pc-fps')
    aimHighlighted(game, 0)
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    aimHighlighted(game, 1)
    game.advanceTo(60000)

    const raw = aimRaw(game.finish())
    expect(raw.shots).toBe(300)
    expect(raw.trials.slice(0, 2)).toEqual([
      { hit: true, latency_ms: 0 },
      { hit: true, latency_ms: 200 },
    ])
  })

  it('lets one shot hit only the first target and does not expose a ghost target', () => {
    const game = create('pc-fps')
    aimHighlighted(game, 0)
    pulse(game, 0)

    const scene = sceneOf(game)
    expect(scene.targets).toHaveLength(1)
    expect(scene.targets[0].id).toBe('switching-v3-1')
    const raw = aimRaw(game.finish())
    expect(raw.shots).toBe(1)
    expect(raw.trials.slice(0, 2)).toEqual([
      { hit: true, latency_ms: 0 },
      { hit: false, latency_ms: 1500 },
    ])
  })

  it('counts a blank-window shot without creating or hitting a target', () => {
    const game = create('pc-fps')
    game.advanceTo(1500)
    pulse(game, 1500)
    expect(sceneOf(game).targets).toHaveLength(0)

    const raw = aimRaw(game.finish())
    expect(raw.shots).toBe(1)
    expect(raw.trials.every(trial => !trial.hit)).toBe(true)
  })

  it('accepts the last pre-deadline input but ignores 60000ms without spawning group 21', () => {
    const game = create('pc-fps')
    game.advanceTo(59999)
    pulse(game, 59999)
    game.input({ kind: 'fire', at_ms: 60000, pressed: true })
    game.advanceTo(60000)

    expect(sceneOf(game).targets).toHaveLength(0)
    const raw = aimRaw(game.finish())
    expect(raw.shots).toBe(1)
    expect(raw.trials).toHaveLength(40)
  })

  it.each(suites)('is deterministic at 30/60/120Hz: %s', suite => {
    const results = [30, 60, 120].map(hz => {
      const game = create(suite)
      let first = false, second = false
      for (let frame = 1; frame <= hz * 60; frame++) {
        const at = frame * 1000 / hz
        if (!first && at >= 200) {
          aimHighlighted(game, 200)
          pulse(game, 200)
          first = true
        }
        if (!second && at >= 1400) {
          aimHighlighted(game, 1400)
          pulse(game, 1400)
          second = true
        }
        game.advanceTo(at)
        game.scene()
      }
      return aimRaw(game.finish())
    })

    expect(results[1]).toEqual(results[0])
    expect(results[2]).toEqual(results[0])
    expect(results[0].trials.slice(0, 2)).toEqual([
      { hit: true, latency_ms: 200 },
      { hit: true, latency_ms: 1200 },
    ])
  })

  it.each(suites)('spawns both targets fully in the current view across all five levels and pitch limits: %s', suite => {
    for (let difficulty = 1; difficulty <= 5; difficulty++) {
      const config = previewTaskConfig(fixtureV3Config(suite, 'switching'), difficulty)
      if (config.params.kind !== 'aim') throw new Error('aim config required')
      for (const pitch of [-70, 70]) {
        const game = create(suite, config)
        game.input({ kind: 'look', at_ms: 2999, dx: 179, dy: -pitch })
        game.advanceTo(3000)

        const first = sceneOf(game)
        expect(first.camera).toMatchObject({ yaw_deg: 179, pitch_deg: pitch })
        expectFullyProjected(first, viewport)
        const firstDirection = vectorToDirection({
          x: first.targets[0].position.x - first.camera.position.x,
          y: first.targets[0].position.y - first.camera.position.y,
          z: first.targets[0].position.z - first.camera.position.z,
        })
        expect(angularDistanceDeg(first.camera, firstDirection))
          .toBeCloseTo(config.params.target_separation_deg / 2, 8)

        aimHighlighted(game, 3001)
        pulse(game, 3001)
        const second = sceneOf(game)
        expectFullyProjected(second, viewport)
        const secondDirection = vectorToDirection({
          x: second.targets[0].position.x - second.camera.position.x,
          y: second.targets[0].position.y - second.camera.position.y,
          z: second.targets[0].position.z - second.camera.position.z,
        })
        expect(angularDistanceDeg(second.camera, secondDirection))
          .toBeCloseTo(config.params.target_separation_deg, 8)

        const frozen = second.targets[0]
        game.input({ kind: 'look', at_ms: 3002, dx: 10, dy: 10 })
        expect(sceneOf(game).targets[0]).toEqual(frozen)
        expect(aimRaw(game.finish()).trials).toHaveLength(40)
      }
    }
  })
})
