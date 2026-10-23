import { expect, it } from 'vitest'
import { createRandom, createThreat, reachablePoint } from './schedule'
import { fixtureConfig } from '../testSupport/fixtures'

it('produces repeatable schedules with exact distances and legal checkpoints at every corner', () => {
  const a = createRandom(123), b = createRandom(123)
  expect(Array.from({ length: 30 }, a)).toEqual(Array.from({ length: 30 }, b))
  for (const x of [12, 500, 988]) for (const y of [12, 281.25, 550.5]) {
    const point = reachablePoint({ x, y }, 120, 12, { width: 1000, height: 562.5 }, a)
    expect(Math.hypot(point.x - x, point.y - y)).toBeCloseTo(120, 8)
    expect(point.x).toBeGreaterThanOrEqual(12)
    expect(point.x).toBeLessThanOrEqual(988)
    expect(point.y).toBeGreaterThanOrEqual(12)
    expect(point.y).toBeLessThanOrEqual(550.5)
  }
})
it('provides a reachable perpendicular escape even at edges and rejects infeasible threats', () => {
  const p = fixtureConfig('mobile-moba', 'dodge').params
  if (p.kind !== 'dodge') throw new Error('config')
  for (const x of [12, 988]) for (const y of [12, 550.5]) {
    const threat = createThreat({ x, y }, p, createRandom(99))
    expect(Math.hypot(threat.origin.x - x, threat.origin.y - y)).toBeCloseTo(240, 8)
    expect(threat.escape.x).toBeGreaterThanOrEqual(12)
    expect(threat.escape.x).toBeLessThanOrEqual(988)
    expect(threat.escape.y).toBeGreaterThanOrEqual(12)
    expect(threat.escape.y).toBeLessThanOrEqual(550.5)
    expect(Math.hypot(threat.escape.x - x, threat.escape.y - y) / 180 * 1000).toBeLessThan(p.warning_ms)
  }
  expect(() => createThreat({ x: 500, y: 281.25 }, { ...p, projectile_speed: 1 }, createRandom(1))).toThrow()
})
