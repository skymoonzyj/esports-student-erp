import type { Vector2 } from '../engineRuntime/types'

export interface LaneUnitSnapshot {
  readonly id: string
  readonly team: 'ally' | 'enemy'
  readonly kind: 'hero' | 'melee' | 'ranged'
  readonly position: Vector2
  readonly facing_rad: number
  readonly health: number
  readonly max_health: number
  readonly state: 'idle' | 'moving' | 'windup' | 'attacking' | 'dying'
  readonly attack_progress: number
  readonly hit_at_ms: number | null
}

export interface LaneSceneState {
  readonly attack_mode: 'melee' | 'ranged'
  readonly units: readonly LaneUnitSnapshot[]
  readonly projectiles: readonly {
    readonly id: string
    readonly team: 'ally' | 'enemy'
    readonly position: Vector2
    readonly target: Vector2
    readonly kind?: 'tower'
  }[]
  readonly selected_target_id: string | null
  readonly move_destination: Vector2 | null
  readonly wave: number
  readonly wave_count: 10 | 15
  readonly last_hits: number
  readonly enemy_deaths: number
  readonly missed: number
  readonly towers?: readonly LaneTowerSnapshot[]
  readonly hero_status?: {
    readonly health: number
    readonly max_health: number
    readonly deaths: number
    readonly respawn_at_ms: number | null
  }
}

export interface LaneTowerSnapshot {
  readonly id: string
  readonly team: 'ally' | 'enemy'
  readonly position: Vector2
  readonly facing_rad: number
  readonly state: 'idle' | 'windup' | 'attacking'
  readonly attack_progress: number
  readonly target_id: string | null
}
