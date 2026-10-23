import { describe, expect, it } from 'vitest'

import { SimulationClock } from './clock'

describe('SimulationClock', () => {
  it.each([30, 60, 120])('emits 7200 fixed ticks over 60 seconds at %iHz', hz => {
    const startedAt = 12_345
    const clock = new SimulationClock(startedAt)
    const ticks: number[] = []

    clock.frame(startedAt)
    for (let frame = 1; frame <= hz * 60; frame += 1) {
      const result = clock.frame(startedAt + frame * 1000 / hz)
      ticks.push(...result.tick_times_ms)
    }

    expect(ticks).toHaveLength(7200)
    expect(ticks[0]).toBeCloseTo(1000 / 120, 10)
    expect(ticks[7199]).toBe(60_000)
    expect(clock.frame(startedAt + 60_001)).toMatchObject({
      elapsed_ms: 60_000,
      tick_times_ms: [],
      interrupted: false,
      finished: true,
    })
  })

  it('allows a 500ms frame gap', () => {
    const clock = new SimulationClock(0)

    const frame = clock.frame(500)

    expect(frame.interrupted).toBe(false)
    expect(frame.tick_times_ms).toHaveLength(60)
    expect(frame.frame_gap_ms).toBe(500)
  })

  it('interrupts above 500ms without catching up ticks', () => {
    const clock = new SimulationClock(0)

    const frame = clock.frame(500.01)

    expect(frame).toMatchObject({
      elapsed_ms: 500.01,
      tick_times_ms: [],
      frame_gap_ms: 500.01,
      interrupted: true,
      finished: false,
    })
    expect(clock.frame(510).tick_times_ms).toEqual([])
  })

  it('interrupts if the monotonic clock moves backwards', () => {
    const clock = new SimulationClock(100)
    clock.frame(150)

    expect(clock.frame(149)).toMatchObject({
      tick_times_ms: [],
      interrupted: true,
      finished: false,
    })
  })
})
