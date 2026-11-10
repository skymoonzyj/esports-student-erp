import { describe, expect, it, vi } from 'vitest'
import { PerspectiveCamera, Vector3 } from 'three'
import { previewTaskConfig, validateRaw } from '../contracts'
import type { FpsSceneSnapshot, GameEngine } from '../engineRuntime/types'
import { fixtureSettings } from '../testSupport/fixtures'
import { fixtureV4Config } from '../testSupport/fixturesV4'
import { pcFpsFactory } from '../suites/pcFps'
import { angularDistanceDeg, vectorToDirection } from './math'
import { validateFpsViewport } from './schedule'
import * as gridModule from './gridCandidates'

const viewport = { width: 1600, height: 900 }
function scene(game: GameEngine): FpsSceneSnapshot {
  const value = game.scene()
  if (value.kind !== 'fps') throw new Error('fps required')
  return value
}
function direction(target: FpsSceneSnapshot['targets'][number]) {
  return vectorToDirection({ x: target.position.x, y: target.position.y - 1.6, z: target.position.z })
}
function aim(game: GameEngine, at_ms: number, index = 0) {
  const view = scene(game), angle = direction(view.targets[index])
  game.input({ kind: 'look', at_ms, dx: angle.yaw_deg - view.camera.yaw_deg, dy: view.camera.pitch_deg - angle.pitch_deg })
}
function expectSphereVisible(view: FpsSceneSnapshot, target: FpsSceneSnapshot['targets'][number]) {
  const camera = new PerspectiveCamera(2 * Math.atan(viewport.height / viewport.width) * 180 / Math.PI, viewport.width / viewport.height, .01, 100)
  camera.position.set(0, 1.6, 0)
  camera.rotation.set(view.camera.pitch_deg * Math.PI / 180, -view.camera.yaw_deg * Math.PI / 180, 0, 'YXZ')
  camera.updateMatrixWorld()
  const center = new Vector3(target.position.x, target.position.y - 1.6, target.position.z).normalize()
  const right = new Vector3(0, 1, 0).cross(center).normalize(), up = center.clone().cross(right).normalize()
  const radius = target.angular_diameter_deg * Math.PI / 360
  for (let edge = 0; edge < 64; edge++) {
    const bearing = edge * Math.PI / 32
    const point = center.clone().multiplyScalar(Math.cos(radius)).addScaledVector(right, Math.sin(radius) * Math.cos(bearing)).addScaledVector(up, Math.sin(radius) * Math.sin(bearing)).multiplyScalar(10).add(new Vector3(0, 1.6, 0)).project(camera)
    expect(Math.abs(point.x)).toBeLessThanOrEqual(1 + 1e-8)
    expect(Math.abs(point.y)).toBeLessThanOrEqual(1 + 1e-8)
  }
}

describe('v4 independent multi-target engines', () => {
  it('rejects the unsafe seed101 replay viewport before issuing an attempt', () => {
    const config = fixtureV4Config('pc-fps', 'gridshot'), size = { width: 2500, height: 500 }
    expect(() => validateFpsViewport(config, size)).toThrow(/viewport|视野/)
    expect(() => pcFpsFactory.create(config, 101, fixtureSettings('pc-fps'), size)).toThrow(/viewport|视野/)
  })

  it.each([viewport, { width: 2560, height: 1080 }])('retains the full target set throughout the seed101 legal-shot replay on supported geometry %j', size => {
    const config = fixtureV4Config('pc-fps', 'gridshot')
    const game = pcFpsFactory.create(config, 101, fixtureSettings('pc-fps'), size)
    let state = 101
    const next = () => { state = (Math.imul(state, 1664525) + 1013904223) >>> 0; return state }
    for (let n = 0; n < 1200; n++) {
      const view = scene(game), index = next() % 3
      next()
      const off = n % 2 === 0 ? 0 : (state / 2 ** 32 - .5) * 5
      const target = direction(view.targets[index])
      game.input({ kind: 'look', at_ms: n * 50, dx: target.yaw_deg + off - view.camera.yaw_deg, dy: view.camera.pitch_deg - target.pitch_deg })
      expect(() => game.input({ kind: 'fire', at_ms: n * 50, pressed: true })).not.toThrow()
      game.input({ kind: 'fire', at_ms: n * 50 + .1, pressed: false })
      expect(scene(game).targets).toHaveLength(3)
    }
  })

  it('keeps targets and shot observations atomic if replacement geometry unexpectedly fails closed', () => {
    const game = pcFpsFactory.create(fixtureV4Config('pc-fps', 'gridshot'), 101, fixtureSettings('pc-fps'), viewport)
    aim(game, 0)
    const before = scene(game)
    const unavailable = vi.spyOn(gridModule, 'gridCandidates').mockReturnValueOnce([])
    try {
      expect(() => game.input({ kind: 'fire', at_ms: 0, pressed: true })).toThrow(/viewport|视野/)
      expect(scene(game).targets).toEqual(before.targets)
      expect(scene(game).feedback).toEqual({ kind: 'multi_target', shots: 0, hits: 0 })
    } finally { unavailable.mockRestore() }
  })

  it.each([['gridshot', 3], ['sixshot', 6]] as const)('%s starts with %i disjoint targets and replaces only the hit target', (id, count) => {
    const config = fixtureV4Config('pc-fps', id)
    const game = pcFpsFactory.create(config, 123, fixtureSettings('pc-fps'), viewport)
    const before = scene(game).targets
    expect(before).toHaveLength(count)
    aim(game, 0)
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    const after = scene(game).targets
    expect(after).toHaveLength(count)
    expect(after.filter(target => before.some(old => old.id === target.id))).toHaveLength(count - 1)
    for (const target of before.slice(1)) expect(after).toContainEqual(target)
    const replacement = after.find(target => !before.some(old => old.id === target.id))!
    expect(angularDistanceDeg(direction(replacement), direction(before[0]))).toBeGreaterThan(1e-5)
    for (let a = 0; a < after.length; a++) for (let b = a + 1; b < after.length; b++) {
      expect(angularDistanceDeg(direction(after[a]), direction(after[b]))).toBeGreaterThanOrEqual(after[a].angular_diameter_deg - 1e-8)
    }
    game.advanceTo(10000)
    const raw = game.finish()
    expect(raw).toEqual({ kind: 'multi_target', shots: [{ at_ms: 0, hit: true }] })
    expect(validateRaw(config, raw)).toEqual(raw)
    expect(Object.isFrozen(raw)).toBe(true)
    expect(game.finish()).toBe(raw)
    game.input({ kind: 'fire', at_ms: 12000, pressed: true })
    expect(game.finish()).toBe(raw)
  })

  it('keeps all targets after a miss; minimum gap does not autofire a rejected press', () => {
    const game = pcFpsFactory.create(fixtureV4Config('pc-fps', 'gridshot'), 123, fixtureSettings('pc-fps'), viewport)
    const initial = scene(game).targets
    game.input({ kind: 'look', at_ms: 0, dx: 170, dy: 0 })
    for (const at_ms of [0, 49, 50, 59999, 60000]) {
      game.input({ kind: 'fire', at_ms, pressed: true })
      game.input({ kind: 'fire', at_ms: at_ms + .1, pressed: false })
    }
    expect(scene(game).targets.map(target => ({ ...target, state: 'highlighted' }))).toEqual(initial)
    expect(game.finish()).toEqual({ kind: 'multi_target', shots: [{ at_ms: 0, hit: false }, { at_ms: 50, hit: false }, { at_ms: 59999, hit: false }] })
  })

  it.each(['gridshot', 'sixshot'])('%s supports every difficulty and refills repeatedly without overlap', id => {
    for (let difficulty = 1; difficulty <= 5; difficulty++) {
      const config = previewTaskConfig(fixtureV4Config('pc-fps', id), difficulty)
      for (const size of [viewport, { width: 2560, height: 1080 }]) {
        expect(() => validateFpsViewport(config, size)).not.toThrow()
        const game = pcFpsFactory.create(config, 345, fixtureSettings('pc-fps'), size)
        for (let shot = 0; shot < 120; shot++) {
          const at_ms = shot * 100
          aim(game, at_ms)
          game.input({ kind: 'fire', at_ms, pressed: true })
          game.input({ kind: 'fire', at_ms: at_ms + 1, pressed: false })
          const view = scene(game)
          for (let a = 0; a < view.targets.length; a++) for (let b = a + 1; b < view.targets.length; b++) {
            expect(angularDistanceDeg(direction(view.targets[a]), direction(view.targets[b]))).toBeGreaterThanOrEqual(view.targets[a].angular_diameter_deg - 1e-8)
          }
        }
        expect(game.finish().kind).toBe('multi_target')
      }
    }
  })

  it.each([-70, 70])('refills a hit from exactly %i pitch in wrapped yaw with full sphere visibility', pitch => {
    const game = pcFpsFactory.create(previewTaskConfig(fixtureV4Config('pc-fps', 'gridshot'), 1), 345, fixtureSettings('pc-fps'), viewport)
    let reached = false
    for (let shot = 0; shot < 300; shot++) {
      const view = scene(game)
      const index = view.targets.reduce((best, target, at) => Math.sign(pitch) * direction(target).pitch_deg > Math.sign(pitch) * direction(view.targets[best]).pitch_deg ? at : best, 0)
      const targetAngle = direction(view.targets[index]), at_ms = shot * 50
      reached = Math.abs(targetAngle.pitch_deg - pitch) < 4.5
      const aimPitch = reached ? pitch : Math.max(-70, Math.min(70, targetAngle.pitch_deg + Math.sign(pitch) * 4.4))
      game.input({ kind: 'look', at_ms, dx: targetAngle.yaw_deg - view.camera.yaw_deg + 720, dy: view.camera.pitch_deg - aimPitch })
      const before = scene(game).targets
      game.input({ kind: 'fire', at_ms, pressed: true })
      game.input({ kind: 'fire', at_ms: at_ms + 1, pressed: false })
      const after = scene(game), replacement = after.targets.find(target => !before.some(old => old.id === target.id))
      expect(replacement).toBeDefined()
      expectSphereVisible(after, replacement!)
      if (reached) { expect(after.camera.pitch_deg).toBe(pitch); break }
    }
    expect(reached).toBe(true)
  })

  it('is render-chunk invariant and rejects physically insufficient viewports', () => {
    const config = fixtureV4Config('pc-fps', 'sixshot')
    const run = (hz: number) => {
      const game = pcFpsFactory.create(config, 123, fixtureSettings('pc-fps'), viewport)
      for (let tick = 0; tick < hz * 60; tick++) game.advanceTo(tick * 1000 / hz)
      return { raw: game.finish(), scene: game.scene() }
    }
    expect(run(144)).toEqual(run(30))
    expect(() => validateFpsViewport(config, { width: 100000, height: 360 })).toThrow(/viewport|视野/)
  })
})
