import type { Size, Vector2 } from '../engineRuntime/types'

export function dodgeSafeCenters(player: Vector2, size: Size): readonly Vector2[] {
  const sx = player.x <= size.width / 2 ? 1 : -1
  const sy = player.y <= size.height / 2 ? 1 : -1
  return [0, 30, 60, 90].map(degrees => ({
    x: player.x + sx * (20 + 100 * Math.cos(degrees * Math.PI / 180)),
    y: player.y + sy * (20 + 100 * Math.sin(degrees * Math.PI / 180)),
  }))
}

export function firstCircleEntry(from: Vector2, to: Vector2, center: Vector2, radius: number): number | null {
  const x = from.x - center.x, y = from.y - center.y
  const c = x * x + y * y - radius * radius
  if (c <= 0) return 0
  const dx = to.x - from.x, dy = to.y - from.y
  const a = dx * dx + dy * dy
  if (a === 0) return null
  const b = 2 * (x * dx + y * dy)
  const discriminant = b * b - 4 * a * c
  if (discriminant < 0) return null
  const entry = (-b - Math.sqrt(discriminant)) / (2 * a)
  return entry >= 0 && entry <= 1 ? entry : null
}
