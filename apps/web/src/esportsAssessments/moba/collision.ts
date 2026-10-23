import type { Vector2 } from '../engineRuntime/types'

export function pointInCircle(point: Vector2, center: Vector2, radius: number): boolean {
  return Math.hypot(point.x - center.x, point.y - center.y) <= radius + 1e-8
}

/** Relative-motion segment against a circle, including tangent and zero-motion cases. */
export function sweptCircleCollision(a0: Vector2, a1: Vector2, aRadius: number, b0: Vector2, b1: Vector2, bRadius: number): boolean {
  const x = a0.x - b0.x, y = a0.y - b0.y
  const dx = a1.x - b1.x - x, dy = a1.y - b1.y - y
  const length2 = dx * dx + dy * dy
  const t = length2 === 0 ? 0 : Math.max(0, Math.min(1, -(x * dx + y * dy) / length2))
  return Math.hypot(x + t * dx, y + t * dy) <= aRadius + bRadius + 1e-8
}
