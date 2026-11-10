import { describe, expect, it } from 'vitest'
import { PerspectiveCamera, Vector3 } from 'three'
import { fixtureV5Config } from '../testSupport/fixturesV5'
import { applyFixedGridLook, createFixedGridLayout, validateFixedGridViewport } from './fixedGrid'
import { INITIAL_CAMERA } from './math'

const viewport = { width: 1600, height: 900 }
const rad = Math.PI / 180

describe('fixed world grid geometry', () => {
  // Catches camera-relative/cone grids, skipped cells and unstable ordering.
  it.each([['gridshot', 15, 18, 9], ['sixshot', 25, 12, 12]] as const)('%s fixes every cell in row-major world angles and keeps every center reachable', (id, count, yaw, pitch) => {
    const p = fixtureV5Config(id).params
    if (p.kind !== 'multi_target') throw new Error('fixture')
    const layout = createFixedGridLayout(p, viewport)
    expect(layout.candidates).toHaveLength(count)
    expect(layout.bounds).toEqual({ yaw_min_deg: -yaw, yaw_max_deg: yaw, pitch_min_deg: -pitch, pitch_max_deg: pitch })
    expect(layout.padding_deg).toBe(1)
    expect(layout.candidates[0]).toEqual({ yaw_deg: -yaw, pitch_deg: -pitch })
    expect(layout.candidates.at(-1)).toEqual({ yaw_deg: yaw, pitch_deg: pitch })
    for (const [i, candidate] of layout.candidates.entries()) {
      expect(candidate).toEqual({ yaw_deg: (i % 5 - 2) * p.grid_spacing_deg, pitch_deg: (Math.floor(i / 5) - (p.grid_rows - 1) / 2) * p.grid_spacing_deg })
      expect(applyFixedGridLook(INITIAL_CAMERA, candidate.yaw_deg, -candidate.pitch_deg, layout.bounds)).toMatchObject(candidate)
    }
  })

  // Independent Three projection catches signs, pitch/yaw coupling and clipped sphere edges.
  it.each(['gridshot', 'sixshot'] as const)('%s keeps the full 64-point sphere outline at least 1 degree inside every camera plane', id => {
    let minimumMargin = Infinity
    for (let difficulty = 1; difficulty <= 5; difficulty++) {
      const p = fixtureV5Config(id, difficulty).params
      if (p.kind !== 'multi_target') throw new Error('fixture')
      const { candidates, bounds } = createFixedGridLayout(p, viewport)
      const verticalHalf = Math.atan(9 / 16)
      const camera = new PerspectiveCamera(2 * verticalHalf / rad, 16 / 9, .01, 100)
      camera.position.set(0, 1.6, 0)
      for (const yaw of [-1, -.5, 0, .37, 1]) for (const pitch of [-1, -.4, 0, .61, 1]) {
        camera.rotation.set(pitch * bounds.pitch_max_deg * rad, -yaw * bounds.yaw_max_deg * rad, 0, 'YXZ')
        camera.updateMatrixWorld()
        for (const target of candidates) {
          const center = new Vector3(Math.sin(target.yaw_deg * rad) * Math.cos(target.pitch_deg * rad), Math.sin(target.pitch_deg * rad), -Math.cos(target.yaw_deg * rad) * Math.cos(target.pitch_deg * rad))
          const right = new Vector3(0, 1, 0).cross(center).normalize(), up = center.clone().cross(right).normalize()
          const radius = p.target_diameter_deg * rad / 2
          for (let edge = 0; edge < 64; edge++) {
            const angle = edge * 2 * Math.PI / 64
            const world = center.clone().multiplyScalar(Math.cos(radius)).addScaledVector(right, Math.sin(radius) * Math.cos(angle)).addScaledVector(up, Math.sin(radius) * Math.sin(angle)).multiplyScalar(10).add(camera.position)
            const projected = world.clone().project(camera)
            if (Math.abs(projected.x) > 1 || Math.abs(projected.y) > 1) throw new Error(`clipped ${id} sphere outline`)
            const view = world.applyMatrix4(camera.matrixWorldInverse).normalize()
            const margin = Math.asin(Math.min((-view.z - Math.abs(view.x)) / Math.SQRT2, -view.z * Math.sin(verticalHalf) - Math.abs(view.y) * Math.cos(verticalHalf))) / rad
            minimumMargin = Math.min(minimumMargin, margin)
          }
        }
      }
    }
    expect(minimumMargin).toBeGreaterThanOrEqual(1)
    // Hand-derived conservative bounds from the approved continuous-range proof.
    const A = (id === 'gridshot' ? 18 : 12) * rad, B = (id === 'gridshot' ? 9 : 12) * rad
    const v = Math.atan(9 / 16)
    const H = Math.asin((Math.cos(B) ** 2 * Math.cos(2 * A) - Math.sin(B) ** 2 - Math.cos(B) * Math.sin(2 * A)) / Math.SQRT2)
    const V = Math.asin(Math.sin(v - B) * Math.cos(B) * Math.cos(2 * A) - Math.cos(v - B) * Math.sin(B))
    const bound = Math.min(H, V) / rad - (id === 'gridshot' ? 4.5 : 1.125)
    expect(bound).toBeGreaterThan(id === 'gridshot' ? 2.98 : 2.78)
    expect(minimumMargin).toBeGreaterThanOrEqual(bound - 1e-9)
  })

  it('clamps huge finite deltas without wrapping or retaining overflow; ignores any nonfinite delta', () => {
    const p = fixtureV5Config('gridshot').params
    if (p.kind !== 'multi_target') throw new Error('fixture')
    const { bounds } = createFixedGridLayout(p, viewport)
    const edge = applyFixedGridLook(INITIAL_CAMERA, Number.MAX_VALUE, -Number.MAX_VALUE, bounds)
    expect(edge).toMatchObject({ yaw_deg: 18, pitch_deg: 9 })
    expect(applyFixedGridLook(edge, -.25, .5, bounds)).toMatchObject({ yaw_deg: 17.75, pitch_deg: 8.5 })
    expect(applyFixedGridLook(edge, -Number.MAX_VALUE, Number.MAX_VALUE, bounds)).toMatchObject({ yaw_deg: -18, pitch_deg: -9 })
    for (const [dx, dy] of [[NaN, 1], [1, NaN], [Infinity, 0], [0, -Infinity]]) expect(applyFixedGridLook(edge, dx, dy, bounds)).toEqual(edge)
  })

  it('rejects invalid, non-16:9 or insufficient geometry but tolerates one CSS pixel of rounding', () => {
    const p = fixtureV5Config('gridshot', 1).params
    if (p.kind !== 'multi_target') throw new Error('fixture')
    for (const size of [viewport, { width: 801, height: 450 }, { width: 799.2, height: 450 }, { width: 1600.25, height: 900.25 }]) expect(() => validateFixedGridViewport(p, size)).not.toThrow()
    for (const size of [{ width: NaN, height: 900 }, { width: 1600, height: Infinity }, { width: 0, height: 900 }, { width: -1, height: 900 }, { width: 801.01, height: 450 }, { width: 2560, height: 1080 }]) expect(() => validateFixedGridViewport(p, size)).toThrow(/尺寸|比例|视野|viewport/)
    expect(() => createFixedGridLayout({ ...p, target_diameter_deg: 30 }, viewport)).toThrow(/余量|视野/)
    expect(() => createFixedGridLayout({ ...p, pitch_limit_deg: 8 }, viewport)).toThrow(/边界|pitch/)
    const dpr1 = { ...viewport, dpr: 1 }, dpr2 = { ...viewport, dpr: 2 }
    expect(createFixedGridLayout(p, dpr1)).toEqual(createFixedGridLayout(p, dpr2))
  })
})
