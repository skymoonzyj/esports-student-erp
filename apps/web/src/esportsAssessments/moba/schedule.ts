import { nextU32, type TaskConfig } from '../contracts'
import type { Size, Vector2 } from '../engineRuntime/types'

export function createRandom(seed: number): () => number {
  nextU32(seed)
  let value = seed
  return () => { value = nextU32(value); return value / 0x100000000 }
}

function inside(point: Vector2, radius: number, size: Size): boolean {
  return point.x >= radius && point.y >= radius && point.x <= size.width - radius && point.y <= size.height - radius
}

/** Keep the exact requested distance. Reject directions, never shrink a checkpoint. */
export function reachablePoint(position: Vector2, distance: number, radius: number, size: Size, random: () => number): Vector2 {
  const start = random() * Math.PI * 2
  for (let i = 0; i < 64; i++) {
    const angle = start + i * Math.PI * 2 / 64
    const point = { x: position.x + distance * Math.cos(angle), y: position.y + distance * Math.sin(angle) }
    if (inside(point, radius, size)) return point
  }
  throw new Error('no legal checkpoint at the registered distance')
}

export interface ThreatPath {
  readonly origin: Vector2
  readonly target: Vector2
  readonly direction: Vector2
  readonly escape: Vector2
}

export function createThreat(position: Vector2, p: Extract<TaskConfig['params'], { kind: 'dodge' }>, random: () => number): ThreatPath {
  if (p.warning_ms + p.spawn_distance / p.projectile_speed * 1000 > p.window_ms) throw new Error('threat cannot arrive within round')
  const size = { width: p.logical_width, height: p.logical_height }
  // Reach an entirely clear perpendicular line before launch, even from a corner.
  const escapeDistance = p.player_radius + p.projectile_radius + 1
  if (escapeDistance / p.player_speed * 1000 > p.warning_ms) throw new Error('no guaranteed warning-time escape')
  const start = random() * Math.PI * 2
  for (let i = 0; i < 64; i++) {
    const angle = start + i * Math.PI * 2 / 64
    const direction = { x: Math.cos(angle), y: Math.sin(angle) }
    for (const side of [-1, 1]) {
      const escape = { x: position.x - direction.y * escapeDistance * side, y: position.y + direction.x * escapeDistance * side }
      if (inside(escape, p.player_radius, size)) return {
        origin: { x: position.x - direction.x * p.spawn_distance, y: position.y - direction.y * p.spawn_distance },
        target: { ...position }, direction, escape,
      }
    }
  }
  throw new Error('no legal escape route')
}
