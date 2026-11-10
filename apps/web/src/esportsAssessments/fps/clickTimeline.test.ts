import { expect, it } from 'vitest'
import { ClickFpsTimeline } from './clickTimeline'

it('fires once per press, discards a too-close press, never schedules held shots, and cuts off strictly at 60s', () => {
  const shots: number[] = []
  const timeline = new ClickFpsTimeline(at => shots.push(at))
  timeline.input({ kind: 'fire', at_ms: 0, pressed: true })
  timeline.advanceTo(1000)
  timeline.input({ kind: 'fire', at_ms: 1001, pressed: true })
  timeline.input({ kind: 'fire', at_ms: 1002, pressed: false })
  timeline.input({ kind: 'fire', at_ms: 1010, pressed: true })
  timeline.input({ kind: 'fire', at_ms: 1011, pressed: false })
  timeline.input({ kind: 'fire', at_ms: 1059, pressed: true })
  timeline.input({ kind: 'fire', at_ms: 1060, pressed: false })
  timeline.input({ kind: 'fire', at_ms: 1061, pressed: true })
  timeline.input({ kind: 'fire', at_ms: 1062, pressed: false })
  timeline.input({ kind: 'fire', at_ms: 59999, pressed: true })
  timeline.input({ kind: 'fire', at_ms: 60000, pressed: false })
  timeline.input({ kind: 'fire', at_ms: 60000, pressed: true })
  timeline.advanceTo(70000)
  expect(shots).toEqual([0, 1010, 1061, 59999])
  expect(timeline.at_ms).toBe(60000)
})

it('accepts the exact 50ms boundary, ignores invalid and backwards events', () => {
  const shots: number[] = []
  const timeline = new ClickFpsTimeline(at => shots.push(at))
  for (const at of [0, 50, 100]) {
    timeline.input({ kind: 'fire', at_ms: at, pressed: true })
    timeline.input({ kind: 'fire', at_ms: at + 1, pressed: false })
  }
  for (const at of [-1, NaN, Infinity, 99]) timeline.input({ kind: 'fire', at_ms: at, pressed: true })
  expect(shots).toEqual([0, 50, 100])
})
