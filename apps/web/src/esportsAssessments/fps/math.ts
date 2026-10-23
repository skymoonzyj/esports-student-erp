import type { FpsSceneSnapshot, FpsTargetSnapshot, Size, Vector3 } from '../engineRuntime/types'

export interface AngleDirection { readonly yaw_deg: number; readonly pitch_deg: number }
const RAD = Math.PI / 180
export const INITIAL_CAMERA = { yaw_deg: 0, pitch_deg: 0, position: { x: 0, y: 1.6, z: 0 } } as const

export function directionToVector(direction: AngleDirection): Vector3 {
  const yaw = direction.yaw_deg * RAD, pitch = direction.pitch_deg * RAD
  return { x: Math.sin(yaw) * Math.cos(pitch), y: Math.sin(pitch), z: -Math.cos(yaw) * Math.cos(pitch) }
}

export function vectorToDirection(vector: Vector3): AngleDirection {
  return { yaw_deg: Math.atan2(vector.x, -vector.z) / RAD, pitch_deg: Math.atan2(vector.y, Math.hypot(vector.x, vector.z)) / RAD }
}

function dot(a: Vector3, b: Vector3): number { return a.x * b.x + a.y * b.y + a.z * b.z }

export function angularDistanceDeg(a: AngleDirection, b: AngleDirection): number {
  return Math.acos(Math.min(1, Math.max(-1, dot(directionToVector(a), directionToVector(b))))) / RAD
}

export function verticalFovDeg(viewport: Size, horizontalFov = 90): number {
  if (![viewport.width, viewport.height].every(x => Number.isFinite(x) && x > 0)) throw new Error('无效 viewport 尺寸')
  return 2 * Math.atan(Math.tan(horizontalFov * RAD / 2) * viewport.height / viewport.width) / RAD
}

/** A spherical circular target is entirely inside all four camera frustum planes. */
export function isTargetFullyVisible(target: AngleDirection, radiusDeg: number, reference: AngleDirection, viewport: Size): boolean {
  const forward = directionToVector(reference)
  const right = directionToVector({ yaw_deg: reference.yaw_deg + 90, pitch_deg: 0 })
  const up = directionToVector({ yaw_deg: reference.yaw_deg, pitch_deg: reference.pitch_deg + 90 })
  const vector = directionToVector(target)
  const z = dot(vector, forward), x = dot(vector, right), y = dot(vector, up)
  const v = verticalFovDeg(viewport) * RAD / 2
  const margin = Math.sin(radiusDeg * RAD)
  return z > 0 && z * Math.SQRT1_2 - Math.abs(x) * Math.SQRT1_2 >= margin - 1e-12
    && z * Math.sin(v) - Math.abs(y) * Math.cos(v) >= margin - 1e-12
}

export function directionOnCone(reference: AngleDirection, distanceDeg: number, bearingRad: number): AngleDirection {
  const forward = directionToVector(reference)
  const right = directionToVector({ yaw_deg: reference.yaw_deg + 90, pitch_deg: 0 })
  const up = directionToVector({ yaw_deg: reference.yaw_deg, pitch_deg: reference.pitch_deg + 90 })
  const c = Math.cos(distanceDeg * RAD), s = Math.sin(distanceDeg * RAD)
  const u = Math.cos(bearingRad), v = Math.sin(bearingRad)
  return vectorToDirection({ x: c * forward.x + s * (u * right.x + v * up.x), y: c * forward.y + s * (u * right.y + v * up.y), z: c * forward.z + s * (u * right.z + v * up.z) })
}

export function sampleVisibleDirection(reference: AngleDirection, distanceDeg: number, diameterDeg: number, viewport: Size, random: () => number, pitchLimit = 70): AngleDirection {
  const valid = (candidate: AngleDirection) => Math.abs(candidate.pitch_deg) <= pitchLimit + 1e-10
    && isTargetFullyVisible(candidate, diameterDeg / 2, reference, viewport)
  for (let attempt = 0; attempt < 48; attempt++) {
    const candidate = directionOnCone(reference, distanceDeg, random() * 2 * Math.PI)
    if (valid(candidate)) return candidate
  }
  // Horizontal great-circle bearings move a boundary-pitch reference back toward
  // the equator. Their vertical frustum margin still includes perspective foreshortening.
  for (const bearing of [0, Math.PI, -Math.PI / 2, Math.PI / 2]) {
    const candidate = directionOnCone(reference, distanceDeg, bearing)
    if (valid(candidate)) return candidate
  }
  throw new Error('当前 viewport 视野无法完整显示登记靶径及角距离，请调整场地尺寸')
}

export function targetPosition(direction: AngleDirection, origin: Vector3 = INITIAL_CAMERA.position, distance = 10): Vector3 {
  const vector = directionToVector(direction)
  return { x: origin.x + vector.x * distance, y: origin.y + vector.y * distance, z: origin.z + vector.z * distance }
}

export function applyLook(camera: FpsSceneSnapshot['camera'], dx: number, dy: number, pitchLimit = 70): FpsSceneSnapshot['camera'] {
  if (![dx, dy].every(Number.isFinite)) return camera
  return { ...camera, yaw_deg: ((camera.yaw_deg + dx + 180) % 360 + 360) % 360 - 180, pitch_deg: Math.max(-pitchLimit, Math.min(pitchLimit, camera.pitch_deg - dy)) }
}

export function nearestAngularHit(camera: FpsSceneSnapshot['camera'], targets: readonly FpsTargetSnapshot[]): FpsTargetSnapshot | null {
  let nearest: FpsTargetSnapshot | null = null, nearestDistance = Infinity
  for (const target of targets) {
    if (target.state === 'hidden' || target.state === 'hit' || target.state === 'missed') continue
    const relative = { x: target.position.x - camera.position.x, y: target.position.y - camera.position.y, z: target.position.z - camera.position.z }
    const distance = Math.hypot(relative.x, relative.y, relative.z)
    if (distance < nearestDistance && angularDistanceDeg(camera, vectorToDirection(relative)) <= target.angular_diameter_deg / 2 + 1e-10) {
      nearest = target
      nearestDistance = distance
    }
  }
  return nearest
}
