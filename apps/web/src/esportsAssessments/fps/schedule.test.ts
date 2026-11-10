import { describe, expect, it } from 'vitest'
import { createDistanceSchedule, createRng, FpsTimeline, validateFpsViewport } from './schedule'
import { fixtureV4Config } from '../testSupport/fixturesV4'
import { fixtureV5Config } from '../testSupport/fixturesV5'

describe('FPS scheduling', () => {
  it('preflights exact v5 fixed boards as actual 16:9 canvases without restricting historical grids or tracking', () => {
    const wide = { width: 2560, height: 1080 }
    for (const id of ['gridshot', 'sixshot'] as const) {
      expect(() => validateFpsViewport(fixtureV5Config(id), { width: 800.5, height: 450 })).not.toThrow()
      expect(() => validateFpsViewport(fixtureV5Config(id), wide)).toThrow(/比例|viewport/)
      expect(() => validateFpsViewport(fixtureV4Config('pc-fps', id), wide)).not.toThrow()
    }
    for (const config of [fixtureV4Config('pc-fps', 'spheretrack90'), fixtureV5Config('spheretrack90')]) expect(() => validateFpsViewport(config, { width: 1600.5, height: 900 })).not.toThrow()
  })

  it('preserves registered bin counts and deterministic seed order', () => {
    const schedule = createDistanceSchedule([10, 20, 30], 10, 123)
    expect(schedule).toEqual(createDistanceSchedule([10, 20, 30], 10, 123))
    for (const value of [10, 20, 30]) expect(schedule.filter(x => x === value)).toHaveLength(10)
    const a = createRng(0), b = createRng(0)
    expect([a(), a(), a()]).toEqual([b(), b(), b()])
    expect(() => createRng(-1)).toThrow()
  })

  it('integrates precisely 7200 fixed intervals independent of render calls', () => {
    for (const hz of [30, 60, 120, 144]) {
      const intervals: [number, number][] = []
      const clock = new FpsTimeline({ onAdvance: (from, to) => intervals.push([from, to]) })
      for (let frame = 1; frame <= hz * 60; frame++) clock.advanceTo(frame * 1000 / hz)
      expect(intervals).toHaveLength(7200)
      expect(intervals[0]).toEqual([0, 1000 / 120])
      expect(intervals.at(-1)?.[1]).toBe(60000)
    }
  })

  it('caps held and flooded fire at 300 actual shots excluding the end boundary', () => {
    const shots: number[] = []
    const clock = new FpsTimeline({ onShot: at => { shots.push(at); return true } })
    clock.input({ kind: 'fire', at_ms: 0, pressed: true })
    for (let at_ms = 1; at_ms < 60000; at_ms++) clock.input({ kind: 'fire', at_ms, pressed: true })
    clock.advanceTo(60000)
    clock.input({ kind: 'fire', at_ms: 60000, pressed: true })
    expect(shots).toHaveLength(300)
    expect(shots.slice(0, 3)).toEqual([0, 200, 400])
    expect(shots.at(-1)).toBe(59800)
  })

  it('ignores late and invalid inputs without rolling back, and releases at the given time', () => {
    const shots: number[] = []
    const clock = new FpsTimeline({ onShot: at => { shots.push(at); return true } })
    clock.advanceTo(100)
    clock.input({ kind: 'fire', at_ms: 99, pressed: true })
    clock.input({ kind: 'fire', at_ms: -1, pressed: true })
    clock.input({ kind: 'fire', at_ms: 100, pressed: true })
    clock.input({ kind: 'fire', at_ms: 150, pressed: false })
    clock.advanceTo(1000)
    expect(shots).toEqual([100])
  })

  it('does not consume fire cooldown on rejected blank-period shots', () => {
    const shots: number[] = []
    const clock = new FpsTimeline({ onShot: at => {
      if (at < 2000) return false
      shots.push(at)
      return true
    } })
    clock.input({ kind: 'fire', at_ms: 1999, pressed: true })
    clock.advanceTo(2000)
    expect(shots).toEqual([2000])
  })

  it('finishes the previous round before same-boundary input and shooting', () => {
    const events: string[] = []
    const clock = new FpsTimeline({
      onAdvance: (_from, to) => { if (to === 3000) events.push('round-ended') },
      onInput: action => { if (action.at_ms === 3000) events.push('input') },
      onShot: at => { if (at === 3000) events.push('shot'); return true },
    })
    clock.input({ kind: 'fire', at_ms: 3000, pressed: true })
    expect(events).toEqual(['round-ended', 'input', 'shot'])
  })
})
