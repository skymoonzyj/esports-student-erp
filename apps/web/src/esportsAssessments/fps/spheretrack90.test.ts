import { describe, expect, it } from 'vitest'
import { validateRaw } from '../contracts'
import type { FpsSceneSnapshot, GameEngine, InputAction } from '../engineRuntime/types'
import { fixtureSettings } from '../testSupport/fixtures'
import { fixtureV4Config } from '../testSupport/fixturesV4'
import { pcFpsFactory } from '../suites/pcFps'
import { vectorToDirection } from './math'
import { validateFpsViewport } from './schedule'

const viewport = { width: 1600, height: 900 }
function create(size = viewport) { return pcFpsFactory.create(fixtureV4Config('pc-fps', 'spheretrack90'), 123, fixtureSettings('pc-fps'), size) }
function scene(game: GameEngine): FpsSceneSnapshot {
  const value = game.scene()
  if (value.kind !== 'fps') throw new Error('fps')
  return value
}
function angle(game: GameEngine) {
  const target = scene(game).targets[0].position
  return vectorToDirection({ x: target.x, y: target.y - 1.6, z: target.z })
}
describe('independent bounded spheretrack90', () => {
  it('changes seeded heading at the exact 2000ms cadence, smoothly over 200ms', () => {
    const game = create()
    const points = [1900, 1950, 2000, 2050, 2100, 2150, 2200, 2250, 2300].map(at => {
      game.advanceTo(at)
      return angle(game)
    })
    const heading = (index: number) => Math.atan2(points[index + 1].pitch_deg - points[index].pitch_deg, points[index + 1].yaw_deg - points[index].yaw_deg)
    const difference = (a: number, b: number) => Math.abs(Math.atan2(Math.sin(a - b), Math.cos(a - b)))
    expect(difference(heading(1), heading(5))).toBeGreaterThan(.1)
    expect(difference(heading(2), heading(3))).toBeGreaterThan(.01)
    expect(difference(heading(6), heading(7))).toBeLessThan(.1)
  })

  it('runs a deterministic bounded 120Hz path without treating 90° as an abrupt turn', () => {
    const game = create()
    let last = angle(game)
    expect(last).toEqual({ yaw_deg: 10, pitch_deg: 0 })
    for (let tick = 1; tick <= 7200; tick++) {
      game.advanceTo(tick * 1000 / 120)
      const next = angle(game)
      expect(Math.abs(next.yaw_deg)).toBeLessThanOrEqual(40 + 1e-8)
      expect(Math.abs(next.pitch_deg)).toBeLessThanOrEqual(15 + 1e-8)
      expect(Math.hypot(next.yaw_deg - last.yaw_deg, next.pitch_deg - last.pitch_deg)).toBeLessThanOrEqual(.25 + 1e-8)
      last = next
    }
    const raw = game.finish()
    expect(raw.kind).toBe('spheretrack90')
    if (raw.kind !== 'spheretrack90') throw new Error('raw')
    expect(raw.coverage_ms).toBeLessThan(10000)
    expect(validateRaw(fixtureV4Config('pc-fps', 'spheretrack90'), raw)).toEqual(raw)
    expect(Object.isFrozen(raw.reacquisition_ms)).toBe(true)
    expect(game.finish()).toBe(raw)
  })

  it('measures continuous aiming without firing and gives live covered feedback', () => {
    const game = create()
    for (let tick = 0; tick < 7200; tick++) {
      const view = scene(game), target = angle(game), miss = tick >= 120 && tick < 240
      game.input({ kind: 'look', at_ms: tick * 1000 / 120, dx: target.yaw_deg + (miss ? 90 : 0) - view.camera.yaw_deg, dy: view.camera.pitch_deg - target.pitch_deg })
      expect(scene(game).feedback).toEqual({ kind: 'spheretrack90', covered: !miss })
      game.advanceTo((tick + 1) * 1000 / 120)
    }
    const raw = game.finish()
    if (raw.kind !== 'spheretrack90') throw new Error('raw')
    expect(raw.coverage_ms).toBeCloseTo(59000, 5)
    expect(raw.loss_count).toBe(1)
    expect(raw.reacquisition_ms[0]).toBeCloseTo(1000, 5)
  })

  it('retains sub-tick on/off integration regardless of render chunks', () => {
    const reference = create(), actions: InputAction[] = []
    let yaw = 0, pitch = 0
    for (let at_ms = 0; at_ms < 100; at_ms++) {
      reference.advanceTo(at_ms)
      const target = angle(reference), nextYaw = target.yaw_deg + (at_ms % 2 ? 100 : 0)
      actions.push({ kind: 'look', at_ms, dx: nextYaw - yaw, dy: pitch - target.pitch_deg })
      yaw = nextYaw; pitch = target.pitch_deg
    }
    const run = (hz: number) => {
      const game = create()
      let cursor = 0
      for (let frame = 1; frame <= hz * 60; frame++) {
        const at = frame * 1000 / hz
        while (cursor < actions.length && actions[cursor].at_ms <= at) game.input(actions[cursor++])
        game.advanceTo(at)
        game.scene(); game.scene()
      }
      const raw = game.finish()
      if (raw.kind !== 'spheretrack90') throw new Error('raw')
      expect(raw.coverage_ms).toBe(50)
      expect(raw.loss_count).toBeLessThanOrEqual(12)
      return raw
    }
    expect(run(30)).toEqual(run(144))
  })

  it('keeps score/path viewport-independent and rejects a clipped registered range', () => {
    const first = create(), second = create({ width: 2560, height: 1080 })
    first.advanceTo(54321); second.advanceTo(54321)
    expect(first.scene()).toEqual(second.scene())
    expect(first.finish()).toEqual(second.finish())
    expect(() => validateFpsViewport(fixtureV4Config('pc-fps', 'spheretrack90'), { width: 4000, height: 360 })).toThrow(/viewport|视野/)
  })

  it('ignores firing, out-of-window input, and freezes the finished no-input result across render rates', () => {
    const run = (hz: number) => {
      const game = create()
      game.input({ kind: 'fire', at_ms: 0, pressed: true })
      game.input({ kind: 'look', at_ms: -1, dx: 90, dy: 0 })
      game.input({ kind: 'look', at_ms: NaN, dx: 90, dy: 0 })
      for (let frame = 1; frame <= hz * 60; frame++) game.advanceTo(frame * 1000 / hz)
      game.input({ kind: 'look', at_ms: 60000, dx: 90, dy: 0 })
      const raw = game.finish(), final = game.scene()
      game.input({ kind: 'look', at_ms: 1, dx: 90, dy: 0 })
      game.advanceTo(70000)
      expect(game.scene()).toEqual(final)
      expect(game.finish()).toBe(raw)
      return raw
    }
    expect(run(30)).toEqual(run(144))
  })
})
