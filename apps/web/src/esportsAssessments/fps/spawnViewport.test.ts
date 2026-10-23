import { Object3D, PerspectiveCamera, Vector3 } from 'three'
import { describe, expect, it } from 'vitest'
import { previewTaskConfig, taskConfigSchema, type TaskConfig } from '../contracts'
import type { FpsSceneSnapshot, GameEngine, Size } from '../engineRuntime/types'
import { mobileFpsFactory } from '../suites/mobileFps'
import { pcFpsFactory } from '../suites/pcFps'
import { fixtureConfig, fixtureSettings, fixtureV3Config } from '../testSupport/fixtures'
import { validateFpsViewport } from './schedule'

const cases = [
  ['pc-fps', 'precision', 2000], ['mobile-fps', 'precision', 2000],
  ['pc-fps', 'switching', 3000], ['mobile-fps', 'switching', 3000],
  ['pc-fps', 'recognition', 2000], ['mobile-fps', 'move-fire', 3000],
] as const

function currentConfig(suite: string, test: string, difficulty = 3): TaskConfig {
  const base = taskConfigSchema.parse({ ...fixtureConfig(suite, test), catalog_version: `${suite}-v2`, engine_version: `${suite}-v2` })
  return previewTaskConfig(base, difficulty)
}

function create(suite: string, test: string, viewport: Size, difficulty = 3, seed = 123): GameEngine {
  const factory = suite === 'pc-fps' ? pcFpsFactory : mobileFpsFactory
  return factory.create(currentConfig(suite, test, difficulty), seed, fixtureSettings(suite), viewport)
}

function sceneOf(game: GameEngine): FpsSceneSnapshot {
  const scene = game.scene()
  if (scene.kind !== 'fps') throw new Error('FPS scene required')
  return scene
}

// Project the actual billboard disk into the renderer's perspective camera.
// Deliberately do not use the spawn helper or its visibility predicate as the oracle.
function expectVisible(scene: FpsSceneSnapshot, viewport: Size): void {
  const camera = new PerspectiveCamera(2 * Math.atan(viewport.height / viewport.width) * 180 / Math.PI, viewport.width / viewport.height, .01, 1000)
  camera.position.set(scene.camera.position.x, scene.camera.position.y, scene.camera.position.z)
  camera.rotation.set(scene.camera.pitch_deg * Math.PI / 180, -scene.camera.yaw_deg * Math.PI / 180, 0, 'YXZ')
  camera.updateMatrixWorld()
  expect(scene.targets.length).toBeGreaterThan(0)
  for (const target of scene.targets) {
    const disk = new Object3D()
    disk.position.set(target.position.x, target.position.y, target.position.z)
    disk.lookAt(camera.position)
    disk.scale.setScalar(disk.position.distanceTo(camera.position) * Math.tan(target.angular_diameter_deg * Math.PI / 360))
    disk.updateMatrixWorld()
    for (let edge = 0; edge < 64; edge++) {
      const bearing = edge * Math.PI / 32
      const projected = new Vector3(Math.cos(bearing), Math.sin(bearing), 0).applyMatrix4(disk.matrixWorld).project(camera)
      expect(Math.abs(projected.x), `${target.id} horizontal edge`).toBeLessThanOrEqual(1 + 1e-8)
      expect(Math.abs(projected.y), `${target.id} vertical edge`).toBeLessThanOrEqual(1 + 1e-8)
      expect(projected.z).toBeGreaterThan(-1)
      expect(projected.z).toBeLessThan(1)
    }
  }
}

describe('FPS v2 current-view spawning', () => {
  it.each(cases)('%s %s spawns the entire target/group inside the turned view', (suite, test, interval) => {
    for (const viewport of [{ width: 1440, height: 900 }, { width: 844, height: 390 }]) {
      for (const pitch of [-70, 0, 70]) {
        for (let difficulty = 1; difficulty <= 5; difficulty++) {
          const game = create(suite, test, viewport, difficulty)
          game.input({ kind: 'look', at_ms: interval - 1, dx: 179, dy: -pitch })
          game.advanceTo(interval)
          const scene = sceneOf(game)
          expect(scene.camera.yaw_deg).toBe(179)
          expect(scene.camera.pitch_deg).toBe(pitch)
          expectVisible(scene, viewport)
        }
      }
    }
  })

  it.each(cases)('%s %s freezes a spawned target rather than dragging it with the camera', (suite, test, interval) => {
    const viewport = { width: 844, height: 390 }
    const game = create(suite, test, viewport)
    game.input({ kind: 'look', at_ms: interval - 1, dx: 120, dy: -50 })
    game.advanceTo(interval)
    const spawned = sceneOf(game)
    expectVisible(spawned, viewport)
    game.input({ kind: 'look', at_ms: interval + 1, dx: 90, dy: 30 })
    const turned = sceneOf(game)
    expect(turned.targets).toEqual(spawned.targets)
    expect(turned.camera.yaw_deg).toBe(-150)
    expect(turned.camera.pitch_deg).toBe(20)
    expect(sceneOf(game)).toEqual(turned)
  })

  it.each(['pc-fps', 'mobile-fps'])('%s precision does not drift offscreen across all 30 stationary-camera rounds', suite => {
    const viewport = { width: 844, height: 390 }
    const game = create(suite, 'precision', viewport)
    const angles: number[] = []
    for (let round = 0; round < 30; round++) {
      game.advanceTo(round * 2000)
      const scene = sceneOf(game)
      expectVisible(scene, viewport)
      const target = scene.targets[0].position
      angles.push(Math.round(new Vector3(target.x, target.y - 1.6, target.z).angleTo(new Vector3(0, 0, -1)) * 180 / Math.PI))
    }
    for (const angle of suite === 'pc-fps' ? [10, 20, 30] : [8, 16, 24]) expect(angles.filter(value => value === angle)).toHaveLength(10)
    const result = game.finish()
    if (result.kind !== 'aim') throw new Error('aim result required')
    expect(result.trials).toHaveLength(30)
    expect(result.shots).toBe(0)
  })

  it.each(cases)('%s %s is deterministic across render rates and repeated scene reads', (suite, test, interval) => {
    const viewport = { width: 844, height: 390 }
    const run = (hz: number) => {
      const game = create(suite, test, viewport)
      let nextFrame = 1
      const snapshots: FpsSceneSnapshot[] = []
      for (let round = 0; round < 60000 / interval; round++) {
        const inputAt = round * interval + 1
        while (nextFrame * 1000 / hz < inputAt) {
          game.advanceTo(nextFrame++ * 1000 / hz)
          game.scene()
          game.scene()
        }
        game.input({ kind: 'look', at_ms: inputAt, dx: round % 2 ? -170 : 170, dy: round % 2 ? 60 : -60 })
        const boundary = (round + 1) * interval
        while (nextFrame * 1000 / hz < boundary) {
          game.advanceTo(nextFrame++ * 1000 / hz)
          game.scene()
        }
        game.advanceTo(boundary)
        const scene = sceneOf(game)
        if (boundary < 60000) expectVisible(scene, viewport)
        snapshots.push(scene)
      }
      return { snapshots, result: game.finish() }
    }
    expect(run(144)).toEqual(run(30))
  })

  it('mobile move-fire uses the new round player position and pitch, while keeping the checkpoint on the ground', () => {
    const viewport = { width: 844, height: 390 }
    const game = create('mobile-fps', 'move-fire', viewport)
    game.input({ kind: 'look', at_ms: 1, dx: 110, dy: -70 })
    game.input({ kind: 'move', at_ms: 1, x: 1, y: 0 })
    game.input({ kind: 'move', at_ms: 3000, x: 0, y: 0 })
    const scene = sceneOf(game)
    expectVisible(scene, viewport)
    expect(Math.hypot(scene.camera.position.x, scene.camera.position.z)).toBeGreaterThan(5)
    expect(scene.checkpoints[0].position.y).toBe(0)
    expect(Math.hypot(scene.checkpoints[0].position.x - scene.camera.position.x, scene.checkpoints[0].position.z - scene.camera.position.z)).toBeCloseTo(1.2)
    game.input({ kind: 'fire', at_ms: 3000, pressed: true })
    game.input({ kind: 'fire', at_ms: 3001, pressed: false })
    const result = game.finish()
    if (result.kind !== 'move_fire') throw new Error('move-fire result required')
    expect(result.rounds[1].hit).toBe(true)
  })

  it.each(['pc-fps', 'mobile-fps'])('%s tracking remains the registered fixed seeded path in both versions', suite => {
    const viewport = { width: 844, height: 390 }
    const factory = suite === 'pc-fps' ? pcFpsFactory : mobileFpsFactory
    const oldGame = factory.create(fixtureConfig(suite, 'tracking'), 123, fixtureSettings(suite), viewport)
    const newGame = create(suite, 'tracking', viewport)
    for (const game of [oldGame, newGame]) {
      game.input({ kind: 'look', at_ms: 1000, dx: 160, dy: -60 })
      game.advanceTo(12345)
    }
    expect(newGame.scene()).toEqual(oldGame.scene())
    expect(newGame.finish()).toEqual(oldGame.finish())
  })
})

describe('v3 viewport admission', () => {
  it.each([
    ['pc-fps', 'switching'],
    ['mobile-fps', 'switching'],
    ['mobile-fps', 'move-fire'],
  ] as const)('accepts every registered %s/%s difficulty at supported viewports', (suite, test) => {
    for (const viewport of [{ width: 844, height: 390 }, { width: 640, height: 360 }, { width: 1440, height: 900 }]) {
      for (let difficulty = 1; difficulty <= 5; difficulty++) {
        expect(() => validateFpsViewport(previewTaskConfig(fixtureV3Config(suite, test), difficulty), viewport)).not.toThrow()
      }
    }
  })

  it.each([
    ['pc-fps', 'switching'],
    ['mobile-fps', 'switching'],
    ['mobile-fps', 'move-fire'],
  ] as const)('rejects %s/%s geometry before an attempt on an unusable viewport', (suite, test) => {
    expect(() => validateFpsViewport(fixtureV3Config(suite, test), { width: 100000, height: 360 })).toThrow(/viewport|视野/)
  })
})
