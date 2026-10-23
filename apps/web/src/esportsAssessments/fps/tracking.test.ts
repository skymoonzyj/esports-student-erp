import { describe, expect, it } from 'vitest'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { validateRaw } from '../contracts'
import type { InputAction } from '../engineRuntime/types'
import { createFpsEngine } from './index'
import { vectorToDirection } from './math'

describe('tracking engine', () => {
  it.each(['pc-fps', 'mobile-fps'])('never grants full coverage for no input and is frame-rate independent: %s', suite => {
    const config = fixtureConfig(suite, 'tracking')
    const results = [30, 60, 120, 144].map(hz => {
      const game = createFpsEngine(config, 123, fixtureSettings(suite), { width: 844, height: 390 })
      const first = game.scene()
      expect(first.targets[0].position.x).not.toBe(0)
      for (let frame = 1; frame <= hz * 60; frame++) game.advanceTo(frame * 1000 / hz)
      const raw = game.finish()
      expect(validateRaw(config, raw)).toEqual(raw)
      if (raw.kind !== 'tracking') throw new Error('raw')
      expect(raw.coverage_ms).toBeLessThan(60000)
      expect(game.finish()).toBe(raw)
      return { raw, scene: game.scene() }
    })
    expect(results.every(value => JSON.stringify(value) === JSON.stringify(results[0]))).toBe(true)
  })

  it('measures aiming coverage without firing, records loss and reacquisition', () => {
    const game = createFpsEngine(fixtureConfig('pc-fps', 'tracking'), 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
    for (let tick = 0; tick < 7200; tick++) {
      const scene = game.scene()
      if (scene.kind !== 'fps') throw new Error('scene')
      const p = scene.targets[0].position
      const angle = vectorToDirection({ x: p.x, y: p.y - 1.6, z: p.z })
      const miss = tick >= 120 && tick < 240
      game.input({ kind: 'look', at_ms: tick * 1000 / 120, dx: (miss ? angle.yaw_deg + 90 : angle.yaw_deg) - scene.camera.yaw_deg, dy: scene.camera.pitch_deg - angle.pitch_deg })
      game.advanceTo((tick + 1) * 1000 / 120)
    }
    const raw = game.finish()
    if (raw.kind !== 'tracking') throw new Error('raw')
    expect(raw.coverage_ms).toBeCloseTo(59000, 5)
    expect(raw.loss_count).toBe(1)
    expect(raw.reacquisition_ms[0]).toBeCloseTo(1000, 5)
  })

  it('keeps high-frequency mouse input within the fixed-step raw loss bounds', () => {
    const config = fixtureConfig('pc-fps', 'tracking')
    const game = createFpsEngine(config, 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
    for (let at = 0; at < 60000; at++) {
      const scene = game.scene()
      if (scene.kind !== 'fps') throw new Error('scene')
      const p = scene.targets[0].position
      const angle = vectorToDirection({ x: p.x, y: p.y - 1.6, z: p.z })
      game.input({ kind: 'look', at_ms: at, dx: angle.yaw_deg + (at % 2 ? 90 : 0) - scene.camera.yaw_deg, dy: scene.camera.pitch_deg - angle.pitch_deg })
    }
    expect(() => validateRaw(config, game.finish())).not.toThrow()
    const raw = game.finish()
    if (raw.kind !== 'tracking') throw new Error('raw')
    expect(raw.loss_count).toBeLessThanOrEqual(7200)
    expect(raw.coverage_ms).toBeCloseTo(30000, 4)
  })

  it('integrates only the 0.2ms on-target pulses around ticks, regardless of render frequency', () => {
    const config = fixtureConfig('pc-fps', 'tracking'), settings = fixtureSettings('pc-fps')
    const viewport = { width: 800, height: 450 }
    const reference = createFpsEngine(config, 123, settings, viewport)
    const actions: InputAction[] = []
    let yaw = 0, pitch = 0
    const aim = (at_ms: number, onTarget: boolean) => {
      reference.advanceTo(at_ms)
      const scene = reference.scene()
      if (scene.kind !== 'fps') throw new Error('scene')
      const p = scene.targets[0].position
      const angle = vectorToDirection({ x: p.x, y: p.y - 1.6, z: p.z })
      const nextYaw = angle.yaw_deg + (onTarget ? 0 : 90)
      actions.push({ kind: 'look', at_ms, dx: nextYaw - yaw, dy: pitch - angle.pitch_deg })
      yaw = nextYaw; pitch = angle.pitch_deg
    }
    aim(0, false)
    for (let tick = 1; tick <= 7200; tick++) {
      aim(tick * 1000 / 120 - .1, true)
      if (tick < 7200) aim(tick * 1000 / 120 + .1, false)
    }
    const results = [30, 60, 120, 144].map(hz => {
      const game = createFpsEngine(config, 123, settings, viewport)
      let cursor = 0
      for (let frame = 1; frame <= hz * 60; frame++) {
        const at = frame * 1000 / hz
        while (cursor < actions.length && actions[cursor].at_ms <= at) game.input(actions[cursor++])
        game.advanceTo(at)
      }
      const raw = game.finish()
      if (raw.kind !== 'tracking') throw new Error('raw')
      // 7199 complete 0.2ms pulses plus the final 0.1ms before 60000.
      expect(raw.coverage_ms).toBeCloseTo(1439.9, 4)
      expect(raw.loss_count).toBe(7199)
      expect(validateRaw(config, raw)).toEqual(raw)
      return raw
    })
    expect(results.every(result => JSON.stringify(result) === JSON.stringify(results[0]))).toBe(true)
  })

  it('samples only the first true loss within one tick bucket without dropping coverage intervals', () => {
    const game = createFpsEngine(fixtureConfig('pc-fps', 'tracking'), 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
    for (let at_ms = 0; at_ms <= 5; at_ms++) {
      const scene = game.scene()
      if (scene.kind !== 'fps') throw new Error('scene')
      game.input({ kind: 'look', at_ms, dx: (at_ms % 2 ? 100 : 10) - scene.camera.yaw_deg, dy: scene.camera.pitch_deg })
    }
    const raw = game.finish()
    if (raw.kind !== 'tracking') throw new Error('raw')
    expect(raw.coverage_ms).toBe(3)
    expect(raw.loss_count).toBe(1)
    expect(raw.reacquisition_ms).toEqual([1])
  })

  it('keeps an ongoing loss across buckets single and records its actual first reacquisition', () => {
    const game = createFpsEngine(fixtureConfig('pc-fps', 'tracking'), 123, fixtureSettings('pc-fps'), { width: 800, height: 450 })
    for (const [at_ms, yaw] of [[0, 10], [1, 100], [20, 10], [21, 100]]) {
      const scene = game.scene()
      if (scene.kind !== 'fps') throw new Error('scene')
      game.input({ kind: 'look', at_ms, dx: yaw - scene.camera.yaw_deg, dy: scene.camera.pitch_deg })
    }
    const raw = game.finish()
    if (raw.kind !== 'tracking') throw new Error('raw')
    expect(raw.coverage_ms).toBe(2)
    expect(raw.loss_count).toBe(2)
    expect(raw.reacquisition_ms).toEqual([19])
  })
})
