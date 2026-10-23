import { describe, expect, it } from 'vitest'
import { angularDistanceDeg, directionToVector, isTargetFullyVisible, nearestAngularHit, sampleVisibleDirection, vectorToDirection, verticalFovDeg } from './math'
import { createRng } from './schedule'

describe('FPS angular geometry', () => {
  it('converts spherical directions without conflating yaw/pitch Euclidean distance', () => {
    expect(directionToVector({ yaw_deg: 90, pitch_deg: 0 }).x).toBeCloseTo(1)
    expect(vectorToDirection({ x: 0, y: 1, z: -1 }).pitch_deg).toBeCloseTo(45)
    expect(angularDistanceDeg({ yaw_deg: 0, pitch_deg: 60 }, { yaw_deg: 90, pitch_deg: 60 })).toBeCloseTo(41.409622, 5)
    expect(verticalFovDeg({ width: 800, height: 450 })).toBeCloseTo(58.7155, 3)
  })

  it.each([70, -70, 0])('keeps exact cone distances visible and reachable from pitch %s', pitch_deg => {
    for (const yaw_deg of [-179, -44, 0, 44, 179]) {
      const reference = { yaw_deg, pitch_deg }
      for (const viewport of [{ width: 800, height: 450 }, { width: 560, height: 280 }, { width: 844, height: 390 }]) {
        for (const distance of [7, 17, 24, 36]) {
          const candidate = sampleVisibleDirection(reference, distance, 6, viewport, createRng(123))
          expect(angularDistanceDeg(reference, candidate)).toBeCloseTo(distance, 8)
          expect(Math.abs(candidate.pitch_deg)).toBeLessThanOrEqual(70 + 1e-8)
          expect(isTargetFullyVisible(candidate, 3, reference, viewport)).toBe(true)
        }
      }
    }
  })

  it('has a deterministic valid fallback even when every random bearing points out of view', () => {
    const reference = { yaw_deg: 0, pitch_deg: 70 }
    const candidate = sampleVisibleDirection(reference, 36, 4, { width: 844, height: 390 }, () => .25)
    expect(angularDistanceDeg(reference, candidate)).toBeCloseTo(36, 8)
    expect(isTargetFullyVisible(candidate, 2, reference, { width: 844, height: 390 })).toBe(true)
    expect(candidate.pitch_deg).toBeLessThanOrEqual(70)
  })

  it('rejects impossible viewports instead of shrinking registered diameters', () => {
    expect(() => sampleVisibleDirection({ yaw_deg: 0, pitch_deg: 0 }, 30, 6, { width: 100000, height: 450 }, createRng(1))).toThrow(/视野|viewport/)
  })

  it('selects only the nearest intersected visible target', () => {
    const camera = { yaw_deg: 0, pitch_deg: 0, position: { x: 0, y: 0, z: 0 } }
    const targets = [20, 10].map((distance, index) => ({ id: String(index), position: { x: 0, y: 0, z: -distance }, angular_diameter_deg: 4, type: 'neutral' as const, state: 'active' as const }))
    expect(nearestAngularHit(camera, targets)?.id).toBe('1')
    expect(nearestAngularHit({ ...camera, yaw_deg: 3 }, targets)).toBeNull()
  })
})
