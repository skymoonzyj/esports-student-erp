import type { Size, Vector3 } from '../engineRuntime/types'
import { directionOnCone, directionToVector, isTargetFullyVisible, verticalFovDeg, type AngleDirection } from './math'

export interface GridGeometry {
  readonly target_count: number
  readonly target_diameter_deg: number
  readonly grid_columns: number
  readonly grid_rows: number
  readonly grid_spacing_deg: number
  readonly pitch_limit_deg: number
}
export function gridCandidates(p: GridGeometry, reference: AngleDirection, viewport: Size): AngleDirection[] {
  const candidates: AngleDirection[] = []
  for (let row = 0; row < p.grid_rows; row++) {
    for (let column = 0; column < p.grid_columns; column++) {
      const x = (column - (p.grid_columns - 1) / 2) * p.grid_spacing_deg
      const y = (row - (p.grid_rows - 1) / 2) * p.grid_spacing_deg
      const candidate = directionOnCone(reference, Math.hypot(x, y), Math.atan2(y, x))
      if (Math.abs(candidate.pitch_deg) <= p.pitch_limit_deg + 1e-10
        && isTargetFullyVisible(candidate, p.target_diameter_deg / 2, reference, viewport)) candidates.push(candidate)
    }
  }
  return candidates
}

const dot = (a: Vector3, b: Vector3) => a.x * b.x + a.y * b.y + a.z * b.z

/** Maximum cells an arbitrarily positioned retained sphere can exclude.
 * A maximum spherical-cap overlap occurs at a cell center or at an intersection
 * of two equal-radius cap boundaries. Checking all such vertices is bounded.
 */
function maximumBlocked(candidates: readonly AngleDirection[], radiusDeg: number): number {
  const points = candidates.map(directionToVector), centers = [...points]
  const threshold = Math.cos(radiusDeg * Math.PI / 180)
  for (let i = 0; i < points.length; i++) for (let j = i + 1; j < points.length; j++) {
    const a = points[i], b = points[j], product = dot(a, b)
    const factor = threshold / (1 + product)
    const base = { x: (a.x + b.x) * factor, y: (a.y + b.y) * factor, z: (a.z + b.z) * factor }
    const remaining = 1 - dot(base, base)
    if (remaining < -1e-10) continue
    const cross = { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x }
    const length = Math.hypot(cross.x, cross.y, cross.z)
    if (length < 1e-12) continue
    const scale = Math.sqrt(Math.max(0, remaining)) / length
    for (const sign of [-1, 1]) centers.push({ x: base.x + sign * cross.x * scale, y: base.y + sign * cross.y * scale, z: base.z + sign * cross.z * scale })
  }
  return Math.max(0, ...centers.map(center => points.filter(point => dot(center, point) >= threshold - 1e-10).length))
}

/** Certify a fixed subset throughout an entire continuous camera-pitch interval. */
function validThroughoutPitch(candidate: AngleDirection, from: number, to: number, limit: number): boolean {
  const vector = directionToVector(candidate), a = -vector.z, b = vector.y
  const angles = [from, to]
  const extremum = Math.atan2(a, b)
  for (let k = -2; k <= 2; k++) {
    const angle = extremum + k * Math.PI
    if (angle > from && angle < to) angles.push(angle)
  }
  const bound = Math.sin(limit)
  return angles.every(angle => Math.abs(a * Math.sin(angle) + b * Math.cos(angle)) <= bound + 1e-12)
}

export function validateGridViewport(p: GridGeometry, viewport: Size): void {
  verticalFovDeg(viewport)
  // Frustum inclusion and pair distances are camera-rotation invariant. For
  // each pitch half-range certify a continuously valid subset, then bound ALL
  // arbitrary retained-target arrangements, rather than one initial packing.
  const candidates = gridCandidates(p, { yaw_deg: 0, pitch_deg: 0 }, viewport)
  const limit = p.pitch_limit_deg * Math.PI / 180
  for (const [from, to] of [[-limit, 0], [0, limit]]) {
    const safe = candidates.filter(candidate => validThroughoutPitch(candidate, from, to, limit))
    const blocked = maximumBlocked(safe, p.target_diameter_deg)
    // n-1 retained targets exclude at most blocked cells each; the just-hit
    // center can coincide with at most one further cell. Strictly more remains.
    if (safe.length <= (p.target_count - 1) * blocked + 1) {
      throw new Error('当前 viewport 视野不足以完整显示登记网格及替换靶，请调整场地尺寸')
    }
  }
}
