import { describe, expect, it } from 'vitest'
import { dodgeSafeCenters, firstCircleEntry } from './dodgeChoiceGeometry'

describe('safe-circle geometry', () => {
  it('keeps every candidate visible, reachable, exclusive and away from the starting player on a 101 by 101 grid', () => {
    for (let x = 0; x <= 100; x++) for (let y = 0; y <= 100; y++) {
      const p = { x: 12 + 976 * x / 100, y: 12 + 538.5 * y / 100 }
      const centers = dodgeSafeCenters(p, { width: 1000, height: 562.5 })
      expect(centers).toHaveLength(4)
      for (const [i, c] of centers.entries()) {
        expect(c.x).toBeGreaterThanOrEqual(32)
        expect(c.x).toBeLessThanOrEqual(968)
        expect(c.y).toBeGreaterThanOrEqual(32)
        expect(c.y).toBeLessThanOrEqual(530.5)
        expect(Math.hypot(c.x - p.x, c.y - p.y)).toBeGreaterThan(20)
        expect(Math.hypot(c.x - p.x, c.y - p.y) / .18).toBeLessThan(713)
        for (const other of centers.slice(i + 1)) expect(Math.hypot(c.x - other.x, c.y - other.y)).toBeGreaterThan(40)
      }
    }
  })

  it.each([
    [{ x: -40, y: 0 }, { x: 40, y: 0 }, .25],
    [{ x: -40, y: 20 }, { x: 40, y: 20 }, .5],
    [{ x: -40, y: 0 }, { x: -20, y: 0 }, 1],
    [{ x: 0, y: 0 }, { x: 0, y: 0 }, 0],
    [{ x: 20, y: 0 }, { x: 20, y: 0 }, 0],
    [{ x: 21, y: 0 }, { x: 21, y: 0 }, null],
    [{ x: -40, y: 21 }, { x: 40, y: 21 }, null],
    [{ x: -40, y: 0 }, { x: -50, y: 0 }, null],
  ])('finds the first closed-circle entry from %j to %j', (from, to, expected) => {
    expect(firstCircleEntry(from, to, { x: 0, y: 0 }, 20)).toBe(expected)
  })
})
