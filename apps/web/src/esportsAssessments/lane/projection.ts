import { OrthographicCamera } from 'three'
import type { Vector2 } from '../engineRuntime/types'

const HALF_HEIGHT = 281.25
const COS_30 = Math.sqrt(3) / 2

/** The same immutable view is used at every DPR and CSS size. */
export function createLaneCamera(): OrthographicCamera {
  const camera = new OrthographicCamera(-500, 500, HALF_HEIGHT, -HALF_HEIGHT, 1, 2400)
  camera.position.set(500, 866.025403784, HALF_HEIGHT + 500)
  camera.lookAt(500, 0, HALF_HEIGHT)
  camera.updateMatrixWorld(true)
  return camera
}

export function laneScreenToWorld(point: Vector2): Vector2 | null {
  if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x < 0 || point.x > 1000 || point.y < 0 || point.y > 562.5) return null
  const y = (point.y - HALF_HEIGHT) / COS_30 + HALF_HEIGHT
  if (y < -1e-8 || y > 562.5 + 1e-8) return null
  return { x: point.x, y: Math.max(0, Math.min(562.5, y)) }
}

export function laneWorldToScreen(point: Vector2, height = 0): Vector2 {
  return { x: point.x, y: (point.y - HALF_HEIGHT) * COS_30 + HALF_HEIGHT - height * .5 }
}
