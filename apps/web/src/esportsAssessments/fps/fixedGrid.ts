import type { FpsSceneSnapshot, Size } from '../engineRuntime/types'
import type { GridGeometry } from './gridCandidates'
import { angularDistanceDeg, type AngleDirection } from './math'

export interface FixedGridBounds {
  readonly yaw_min_deg: number
  readonly yaw_max_deg: number
  readonly pitch_min_deg: number
  readonly pitch_max_deg: number
}
export interface FixedGridLayout {
  readonly candidates: readonly AngleDirection[]
  readonly bounds: FixedGridBounds
  readonly padding_deg: 1
}
const RAD = Math.PI / 180

/** Validate once and certify the entire continuous yaw/pitch rectangle, not a sample. */
export function createFixedGridLayout(p: GridGeometry, viewport: Size): FixedGridLayout {
  if (![viewport.width, viewport.height].every(value => Number.isFinite(value) && value > 0)) throw new Error('固定靶区 viewport 尺寸必须为有限正数')
  if (Math.abs(viewport.width - viewport.height * 16 / 9) > 1) throw new Error('固定靶区画布比例必须为 16:9（允许 1 CSS 像素舍入）')
  if (![p.target_count, p.grid_columns, p.grid_rows].every(value => Number.isSafeInteger(value) && value > 0)
    || ![p.target_diameter_deg, p.grid_spacing_deg, p.pitch_limit_deg].every(value => Number.isFinite(value) && value > 0)) throw new Error('固定靶区网格参数必须为有限正数，行列及目标数必须为整数')
  const yaw = (p.grid_columns - 1) * p.grid_spacing_deg / 2
  const pitch = (p.grid_rows - 1) * p.grid_spacing_deg / 2
  // The conservative frustum proof below applies to the registered small-angle
  // rectangles. Reject larger/unregistered geometry instead of overclaiming it.
  if (yaw > 18 || pitch > 12 || Math.abs(pitch - p.pitch_limit_deg) > 1e-10) throw new Error('固定靶区相机边界与登记 pitch 限制不匹配或超出已验证范围')
  const A = yaw * RAD, B = pitch * RAD, v = Math.atan(viewport.height / viewport.width)
  const horizontal = Math.asin((Math.cos(B) ** 2 * Math.cos(2 * A) - Math.sin(B) ** 2 - Math.cos(B) * Math.sin(2 * A)) / Math.SQRT2)
  const vertical = Math.asin(Math.sin(v - B) * Math.cos(B) * Math.cos(2 * A) - Math.cos(v - B) * Math.sin(B))
  const margin = Math.min(horizontal, vertical) / RAD - p.target_diameter_deg / 2
  if (!Number.isFinite(margin) || margin < 1) throw new Error('当前 viewport 视野不足以为固定靶区完整球体保留 1° 安全余量')
  const candidates: AngleDirection[] = []
  for (let row = 0; row < p.grid_rows; row++) for (let column = 0; column < p.grid_columns; column++) {
    candidates.push({ yaw_deg: (column - (p.grid_columns - 1) / 2) * p.grid_spacing_deg, pitch_deg: (row - (p.grid_rows - 1) / 2) * p.grid_spacing_deg })
  }
  // All retained centers belong to this same finite grid. Each excludes at
  // most this many cells, plus one independently excluded just-hit cell.
  const blocked = Math.max(...candidates.map(center => candidates.filter(candidate => angularDistanceDeg(center, candidate) < p.target_diameter_deg - 1e-8).length))
  if (candidates.length <= (p.target_count - 1) * blocked + 1) throw new Error('固定靶区候选位置不足，无法保证不重叠补位')
  return { candidates, bounds: { yaw_min_deg: -yaw, yaw_max_deg: yaw, pitch_min_deg: -pitch, pitch_max_deg: pitch }, padding_deg: 1 }
}

export function validateFixedGridViewport(p: GridGeometry, viewport: Size): void {
  createFixedGridLayout(p, viewport)
}

export function applyFixedGridLook(camera: FpsSceneSnapshot['camera'], dx: number, dy: number, bounds: FixedGridBounds): FpsSceneSnapshot['camera'] {
  if (![dx, dy].every(Number.isFinite)) return camera
  return { ...camera,
    yaw_deg: Math.max(bounds.yaw_min_deg, Math.min(bounds.yaw_max_deg, camera.yaw_deg + dx)),
    pitch_deg: Math.max(bounds.pitch_min_deg, Math.min(bounds.pitch_max_deg, camera.pitch_deg - dy)),
  }
}
