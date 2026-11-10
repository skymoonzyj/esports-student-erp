import type {
  ControlSettings,
  RawResult,
  TaskConfig,
} from '../contracts'
import type { LaneSceneState } from '../lane/types'

export interface Vector2 {
  readonly x: number
  readonly y: number
}

export interface Vector3 extends Vector2 {
  readonly z: number
}

export interface Size {
  readonly width: number
  readonly height: number
}

export interface Rect extends Vector2, Size {}

export type InputAction =
  | { readonly kind: 'look'; readonly at_ms: number; readonly dx: number; readonly dy: number }
  | { readonly kind: 'move'; readonly at_ms: number; readonly x: number; readonly y: number }
  | { readonly kind: 'fire'; readonly at_ms: number; readonly pressed: boolean }
  | { readonly kind: 'point'; readonly at_ms: number; readonly x: number; readonly y: number; readonly button: 0 | 2; /** Lane body pick: omitted = ground fallback; null = blocked body/force movement. */ readonly target_id?: string | null }
  | { readonly kind: 'skill'; readonly at_ms: number; readonly x: number; readonly y: number; readonly release: boolean; readonly cancel?: boolean }

export type FpsTargetType = 'neutral' | 'enemy' | 'friend'
export type FpsTargetState = 'hidden' | 'active' | 'highlighted' | 'hit' | 'missed'

export interface FpsTargetSnapshot {
  readonly id: string
  readonly position: Vector3
  readonly angular_diameter_deg: number
  readonly type: FpsTargetType
  readonly state: FpsTargetState
  /** V4 sphere silhouette; omitted for frozen legacy billboard rendering. */
  readonly shape?: 'sphere'
}

export interface FpsCheckpointSnapshot {
  readonly id: string
  readonly position: Vector3
  readonly radius: number
  readonly state: 'hidden' | 'active' | 'reached'
}

export interface FpsMovementFeedback {
  readonly state: 'ready' | 'moving' | 'stationary-hit' | 'hit'
  readonly qualifying_ms: number
}

export interface FpsSceneSnapshot {
  readonly kind: 'fps'
  readonly at_ms: number
  readonly camera: {
    readonly yaw_deg: number
    readonly pitch_deg: number
    readonly position: Vector3
  }
  readonly targets: readonly FpsTargetSnapshot[]
  readonly checkpoints: readonly FpsCheckpointSnapshot[]
  /** Present only for engines that implement the v3 movement qualification. */
  readonly movement_feedback?: FpsMovementFeedback
  /** Observed v4 gameplay only, not a locally fabricated final score. */
  readonly feedback?:
    | { readonly kind: 'multi_target'; readonly shots: number; readonly hits: number }
    | { readonly kind: 'spheretrack90'; readonly covered: boolean }
}

export type MobaTargetType = 'enemy' | 'minion' | 'main' | 'checkpoint'
export type MobaTargetState = 'hidden' | 'active' | 'warning' | 'hit' | 'resolved'

export interface MobaTargetSnapshot {
  readonly id: string
  readonly position: Vector2
  readonly radius: number
  readonly type: MobaTargetType
  readonly state: MobaTargetState
  readonly health_ratio?: number
}

export interface MobaProjectileSnapshot {
  readonly id: string
  readonly type: 'threat' | 'skill'
  readonly position: Vector2
  readonly origin: Vector2
  readonly target: Vector2
  readonly radius: number
  readonly state: 'warning' | 'active' | 'hit' | 'expired'
}

export interface MobaDangerChoiceSnapshot {
  readonly kind: 'safe-circle-v3'
  readonly phase: 'waiting' | 'warning' | 'active' | 'resolved'
  readonly safe_zone: null | { readonly position: Vector2; readonly radius: number }
}

export type MobaFeedbackProgress = 'attack-ready' | 'attack-windup' | 'attack-completed' | 'attack-cancelled'
export type MobaFeedbackObjective = 'last-hit' | 'attack' | 'checkpoint' | 'dodge' | 'main-target' | 'map-event' | 'skill-aim'
export type MobaFeedbackReason = 'early' | 'late' | 'missed' | 'cancelled' | 'hit' | 'wrong'

export interface MobaFeedbackOutcome {
  readonly objective: MobaFeedbackObjective
  /** One-based occurrence number for the objective. */
  readonly round: number
  readonly result: 'success' | 'failure'
  readonly reason?: MobaFeedbackReason
}

export interface MobaFeedback {
  readonly version: 'v2'
  readonly task_id: 'last-hit' | 'orb-walk' | 'dodge' | 'map-awareness' | 'skill-aim' | 'move-attack'
  readonly round: {
    /** One-based current gameplay round. */
    readonly current: number
    readonly total: number
    readonly status: 'active' | 'success' | 'failure'
  }
  readonly progress?: MobaFeedbackProgress
  /** Latest observed objective outcome(s), never a future schedule disclosure. */
  readonly outcomes: readonly MobaFeedbackOutcome[]
}

export interface MobaSceneSnapshot {
  readonly kind: 'moba'
  readonly at_ms: number
  readonly logical_size: Size
  readonly player: {
    readonly id: string
    readonly position: Vector2
    readonly radius: number
    readonly facing_rad: number
  }
  readonly targets: readonly MobaTargetSnapshot[]
  readonly projectiles: readonly MobaProjectileSnapshot[]
  readonly minimap: {
    /** Explicit scene visibility; a visible map remains present between marker events. */
    readonly visible: boolean
    readonly bounds: Rect
    readonly event: null | {
      readonly id: string
      readonly quadrant: 0 | 1 | 2 | 3
      readonly position: Vector2
      readonly state: 'active' | 'resolved'
    }
  }
  /** Current-engine task feedback. Frozen v1 snapshots omit this property. */
  readonly feedback?: MobaFeedback
  /** Present only for engines that implement the v3 safe-circle choice. */
  readonly danger_choice?: MobaDangerChoiceSnapshot
  /** Exclusive to the deterministic pc-moba-v4 lane simulation. */
  readonly lane?: LaneSceneState
}

export type SceneSnapshot = FpsSceneSnapshot | MobaSceneSnapshot

export interface GameEngine {
  input(action: InputAction): void
  advanceTo(elapsed_ms: number): void
  scene(): SceneSnapshot
  finish(): RawResult
}

export interface GameFactory {
  create(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine
}

export function isInputInWindow(action: InputAction, duration_ms = 60_000): boolean {
  return Number.isFinite(action.at_ms) && action.at_ms >= 0 && action.at_ms < duration_ms
}
