import { describe, expect, it } from 'vitest'
import { taskConfigSchema, validateRaw } from '../contracts'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import type { InputAction } from '../engineRuntime/types'
import { createFpsEngine, validateFpsViewport } from './index'
import { angularDistanceDeg, isTargetFullyVisible, vectorToDirection } from './math'
import { fixtureV4Config } from '../testSupport/fixturesV4'
import { fixtureV5Config } from '../testSupport/fixturesV5'

describe('FPS factory and full registered configurations', () => {
  it.each(['gridshot', 'sixshot'] as const)('routes only %s exact v5 to the fixed-board engine while preserving v4 rotation', id => {
    const viewport = { width: 1600, height: 900 }, settings = fixtureSettings('pc-fps')
    for (const [config, maximum] of [[fixtureV5Config(id), id === 'gridshot' ? 18 : 12], [fixtureV4Config('pc-fps', id), 170]] as const) {
      const engine = createFpsEngine(config, 123, settings, viewport)
      engine.input({ kind: 'look', at_ms: 0, dx: 170, dy: 0 })
      const view = engine.scene()
      if (view.kind !== 'fps') throw new Error('scene')
      expect(view.camera.yaw_deg).toBe(maximum)
    }
  })

  it('keeps v5 and v4 spheretrack90 scenes and raw results identical for the same inputs', () => {
    const configs = [fixtureV4Config('pc-fps', 'spheretrack90'), fixtureV5Config('spheretrack90')]
    const replay = configs.map(config => {
      const game = createFpsEngine(config, 123, fixtureSettings('pc-fps'), { width: 1600, height: 900 })
      const views = [game.scene()]
      for (const action of [{ kind: 'look', at_ms: 0, dx: 170, dy: -100 }, { kind: 'look', at_ms: 101, dx: -345, dy: 12 }, { kind: 'fire', at_ms: 102, pressed: true }] as const) {
        game.input(action)
        views.push(game.scene())
      }
      game.advanceTo(15000)
      views.push(game.scene())
      return { views, raw: game.finish(), final: game.scene() }
    })
    expect(replay[0]).toEqual(replay[1])
  })

  it.each(['pc-fps', 'mobile-fps'])('runs every tracking/switching practice configuration in %s', suite => {
    for (const test of ['tracking', 'switching']) {
      for (let difficulty = 1; difficulty <= 5; difficulty++) {
        const base = fixtureConfig(suite, test)
        const [size, speed, distance] = [[1.5, .6, .7], [1.25, .8, .85], [1, 1, 1], [.85, 1.15, 1.1], [.7, 1.3, 1.2]][difficulty - 1]
        const p = base.params
        if (p.kind !== 'tracking' && p.kind !== 'aim') throw new Error('fixture')
        const config = taskConfigSchema.parse({ ...base, difficulty, params: { ...p, target_diameter_deg: p.target_diameter_deg * size, ...(p.kind === 'tracking' ? { speed_deg_s: p.speed_deg_s * speed } : { target_separation_deg: p.target_separation_deg * distance }) } })
        const viewport = suite === 'pc-fps' ? { width: 800, height: 450 } : { width: 844, height: 390 }
        const game = createFpsEngine(config, 0, fixtureSettings(suite), viewport)
        for (let at = 0; at < 60000; at += 100) {
          game.advanceTo(at)
          const scene = game.scene()
          if (scene.kind !== 'fps') throw new Error('scene')
          const angles = scene.targets.map(target => vectorToDirection({ x: target.position.x, y: target.position.y - 1.6, z: target.position.z }))
          expect(angles.every(angle => isTargetFullyVisible(angle, config.params.kind === 'aim' || config.params.kind === 'tracking' ? config.params.target_diameter_deg / 2 : 0, { yaw_deg: 0, pitch_deg: 0 }, viewport))).toBe(true)
          if (p.kind === 'tracking') {
            expect(Math.abs(angles[0].yaw_deg)).toBeLessThanOrEqual(30 + 1e-9)
            expect(Math.abs(angles[0].pitch_deg)).toBeLessThanOrEqual(15 + 1e-9)
          } else expect(angularDistanceDeg(angles[0], angles[1])).toBeCloseTo(p.target_separation_deg * distance, 8)
        }
        const raw = game.finish()
        expect(validateRaw(config, raw)).toEqual(raw)
      }
    }
  })

  it('rejects invalid project/configuration/settings and infeasible viewport before starting', () => {
    const config = fixtureConfig('pc-fps', 'precision'), settings = fixtureSettings('pc-fps')
    expect(() => validateFpsViewport(config, { width: 100000, height: 450 })).toThrow(/viewport/)
    expect(() => createFpsEngine(config, 0, settings, { width: 0, height: 450 })).toThrow()
    expect(() => createFpsEngine(config, 0, fixtureSettings('mobile-fps'), { width: 800, height: 450 })).toThrow()
    expect(() => createFpsEngine(fixtureConfig('pc-fps', 'recognition'), 0, settings, { width: 800, height: 450 })).toThrow(/项目/)
  })

  it('replays identical timestamped input across render frequencies and never exceeds 300 shots', () => {
    const actions: InputAction[] = [
      { kind: 'fire', at_ms: 0, pressed: true },
      { kind: 'look', at_ms: 11, dx: 20, dy: -10 },
      { kind: 'look', at_ms: 2000, dx: -30, dy: 15 },
      { kind: 'look', at_ms: 2111, dx: 50, dy: -150 },
    ]
    const results = [30, 60, 120, 144].map(hz => {
      const game = createFpsEngine(fixtureConfig('pc-fps', 'precision'), 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
      let nextAction = 0
      for (let frame = 1; frame <= hz * 60; frame++) {
        const at = frame * 1000 / hz
        while (nextAction < actions.length && actions[nextAction].at_ms <= at) game.input(actions[nextAction++])
        game.advanceTo(at)
      }
      const raw = game.finish()
      if (raw.kind !== 'aim') throw new Error('raw')
      expect(raw.shots).toBe(300)
      const original = game.scene()
      if (original.kind !== 'fps') throw new Error('scene')
      expect(original.camera.pitch_deg).toBe(70)
      return raw
    })
    expect(results.every(result => JSON.stringify(result) === JSON.stringify(results[0]))).toBe(true)
  })

  it('does not allow scene consumers to change the next engine snapshot', () => {
    const game = createFpsEngine(fixtureConfig('pc-fps', 'precision'), 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
    const scene = game.scene()
    if (scene.kind !== 'fps') throw new Error('scene')
    const before = JSON.stringify(scene)
    Object.assign(scene.camera.position, { x: 999 })
    Object.assign(scene.targets[0].position, { x: 999 })
    expect(JSON.stringify(game.scene())).toBe(before)
  })
})
