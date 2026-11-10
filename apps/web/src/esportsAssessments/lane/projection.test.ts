import { describe, expect, it } from 'vitest'
import { Vector3 } from 'three'
import { createLaneCamera, laneScreenToWorld, laneWorldToScreen } from './projection'

describe('lane fixed orthographic projection', () => {
  it('matches real Three camera projection and round-trips the playable plane', () => {
    const camera = createLaneCamera()
    for (const point of [{ x: 0, y: 0 }, { x: 500, y: 281.25 }, { x: 1000, y: 562.5 }, { x: 220, y: 410 }]) {
      const projected = new Vector3(point.x, 0, point.y).project(camera)
      const screen = laneWorldToScreen(point)
      expect(screen.x).toBeCloseTo((projected.x + 1) * 500)
      expect(screen.y).toBeCloseTo((1 - projected.y) * 281.25)
      expect(laneScreenToWorld(screen)?.x).toBeCloseTo(point.x)
      expect(laneScreenToWorld(screen)?.y).toBeCloseTo(point.y)
    }
  })
  it('rejects letterbox margins, outside-plane clicks and nonfinite points', () => {
    for (const point of [{ x: -1, y: 200 }, { x: 1001, y: 200 }, { x: 500, y: 0 }, { x: 500, y: 562.5 }, { x: NaN, y: 20 }, { x: 20, y: Infinity }]) expect(laneScreenToWorld(point)).toBeNull()
    expect(laneScreenToWorld({ x: 0, y: 281.25 })).toEqual({ x: 0, y: 281.25 })
  })
})
