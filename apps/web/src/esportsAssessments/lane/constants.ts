export const LANE_SIZE = { width: 1000, height: 562.5 } as const
export const STEP_MS = 20
export const DURATION_MS = 300_000
export const WAVE_INTERVAL_MS = 30_000
export const WAVE_COUNT = 10
export const SEPARATION_RADIUS = 14
export const PICK_RADIUS = 26
export const DEATH_VISIBLE_MS = 500

export interface CombatStats {
  speed: number
  damage: number
  interval: number
  windup: number
  range: number
  projectileSpeed: number
  health: number
}
export const HERO_STATS: Record<'melee' | 'ranged', CombatStats> = {
  melee: { speed: 250, damage: 70, interval: 1100, windup: 300, range: 55, projectileSpeed: 0, health: 1 },
  ranged: { speed: 250, damage: 54, interval: 1250, windup: 450, range: 210, projectileSpeed: 500, health: 1 },
}
export const CREEP_STATS: Record<'melee' | 'ranged', CombatStats> = {
  melee: { speed: 85, damage: 18, interval: 1000, windup: 250, range: 28, projectileSpeed: 0, health: 400 },
  ranged: { speed: 75, damage: 24, interval: 1300, windup: 300, range: 150, projectileSpeed: 320, health: 240 },
}
