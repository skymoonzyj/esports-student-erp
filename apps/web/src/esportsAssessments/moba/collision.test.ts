import { expect, it } from 'vitest'
import { pointInCircle, sweptCircleCollision } from './collision'

it('detects crossing trajectories even when neither endpoint overlaps', () => {
  expect(sweptCircleCollision({ x: -100, y: 0 }, { x: 100, y: 0 }, 10, { x: 0, y: -100 }, { x: 0, y: 100 }, 12)).toBe(true)
  expect(sweptCircleCollision({ x: -100, y: 30 }, { x: 100, y: 30 }, 10, { x: 0, y: 0 }, { x: 0, y: 0 }, 12)).toBe(false)
})
it('includes tangent and zero-motion overlap', () => {
  expect(sweptCircleCollision({ x: -100, y: 22 }, { x: 100, y: 22 }, 10, { x: 0, y: 0 }, { x: 0, y: 0 }, 12)).toBe(true)
  expect(pointInCircle({ x: 3, y: 4 }, { x: 0, y: 0 }, 5)).toBe(true)
  expect(sweptCircleCollision({ x: 0, y: 0 }, { x: 0, y: 0 }, 10, { x: 0, y: 0 }, { x: 0, y: 0 }, 12)).toBe(true)
})
