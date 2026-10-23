import { describe, expect, it } from 'vitest'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { validateRaw } from '../contracts'
import { createFpsEngine } from './index'
import { vectorToDirection } from './math'

describe('switching engine', () => {
  it.each(['pc-fps', 'mobile-fps'])('keeps all forty windows even without first-target hits: %s', suite => {
    const config = fixtureConfig(suite, 'switching')
    const game = createFpsEngine(config, 123, fixtureSettings(suite), { width: 844, height: 390 })
    game.advanceTo(1500)
    expect(game.scene().targets[1].state).toBe('highlighted')
    game.advanceTo(60000)
    const raw = game.finish()
    if (raw.kind !== 'aim') throw new Error('raw')
    expect(raw.trials).toHaveLength(40)
    expect(raw.trials.every(t => !t.hit && t.latency_ms === 1500)).toBe(true)
    expect(validateRaw(config, raw)).toEqual(raw)
  })

  it('counts wrong-target shots and activates second target on the exact window boundary', () => {
    const game = createFpsEngine(fixtureConfig('pc-fps', 'switching'), 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
    const scene = game.scene()
    if (scene.kind !== 'fps') throw new Error('scene')
    const p = scene.targets[1].position
    const angle = vectorToDirection({ x: p.x, y: p.y - 1.6, z: p.z })
    game.input({ kind: 'look', at_ms: 0, dx: angle.yaw_deg, dy: -angle.pitch_deg })
    game.input({ kind: 'fire', at_ms: 0, pressed: true })
    game.input({ kind: 'fire', at_ms: 1, pressed: false })
    game.input({ kind: 'fire', at_ms: 1500, pressed: true })
    game.input({ kind: 'fire', at_ms: 1501, pressed: false })
    const raw = game.finish()
    if (raw.kind !== 'aim') throw new Error('raw')
    expect(raw.wrong_target_shots).toBe(1)
    expect(raw.shots).toBe(2)
    expect(raw.trials[0]).toEqual({ hit: false, latency_ms: 1500 })
    expect(raw.trials[1]).toEqual({ hit: true, latency_ms: 0 })
  })
})
