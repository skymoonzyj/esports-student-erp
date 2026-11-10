import { PerspectiveCamera, Vector3 } from 'three'
import { expect, it } from 'vitest'
import { gridCandidates, validateGridViewport } from './gridCandidates'

const grid = { target_count: 3, target_diameter_deg: 9, grid_columns: 5, grid_rows: 3, grid_spacing_deg: 10, pitch_limit_deg: 70 }

it('keeps constant geometry in normal and ultrawide views, including wrapped yaw and ±70 pitch', () => {
  for (const viewport of [{ width: 1600, height: 900 }, { width: 2560, height: 1080 }]) {
    expect(() => validateGridViewport(grid, viewport)).not.toThrow()
    for (const pitch_deg of [-70, 0, 70]) {
      const reference = { yaw_deg: 539, pitch_deg }
      const candidates = gridCandidates(grid, reference, viewport)
      expect(candidates.length).toBeGreaterThanOrEqual(4)
      const camera = new PerspectiveCamera(2 * Math.atan(viewport.height / viewport.width) * 180 / Math.PI, viewport.width / viewport.height, .01, 100)
      camera.rotation.set(pitch_deg * Math.PI / 180, -539 * Math.PI / 180, 0, 'YXZ')
      camera.updateMatrixWorld()
      for (const angle of candidates) {
        expect(Math.abs(angle.pitch_deg)).toBeLessThanOrEqual(70 + 1e-8)
        const yaw = angle.yaw_deg * Math.PI / 180, pitch = angle.pitch_deg * Math.PI / 180
        const center = new Vector3(Math.sin(yaw) * Math.cos(pitch), Math.sin(pitch), -Math.cos(yaw) * Math.cos(pitch))
        // Full spherical silhouette, independent of the production frustum helper.
        const right = new Vector3(0, 1, 0).cross(center).normalize()
        const up = center.clone().cross(right).normalize()
        for (let edge = 0; edge < 64; edge++) {
          const bearing = edge * Math.PI / 32, radius = 4.5 * Math.PI / 180
          const point = center.clone().multiplyScalar(Math.cos(radius)).addScaledVector(right, Math.sin(radius) * Math.cos(bearing)).addScaledVector(up, Math.sin(radius) * Math.sin(bearing)).project(camera)
          expect(Math.abs(point.x)).toBeLessThanOrEqual(1 + 1e-8)
          expect(Math.abs(point.y)).toBeLessThanOrEqual(1 + 1e-8)
        }
      }
    }
  }
})

it('rejects insufficient space before play instead of shrinking or dropping targets', () => {
  expect(() => validateGridViewport(grid, { width: 100000, height: 360 })).toThrow(/viewport|视野/)
  expect(() => validateGridViewport({ ...grid, target_count: 6, target_diameter_deg: 30 }, { width: 1600, height: 900 })).toThrow(/viewport|视野/)
})
