import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { GameEngine, InputAction, MobaDangerChoiceSnapshot, MobaFeedbackOutcome, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { buildDodgeChoicePlan } from '../ruleSchedule'
import { dodgeSafeCenters, firstCircleEntry } from './dodgeChoiceGeometry'
import { MobaEngine } from './world'

class DodgeChoiceEngine extends MobaEngine {
  private readonly raw: Extract<RawResult, { kind: 'dodge_choice' }>
  private readonly p: Extract<TaskConfig['params'], { kind: 'dodge_choice' }>
  private round = 0
  private phase: MobaDangerChoiceSnapshot['phase'] = 'waiting'
  private center: Vector2 | null = null
  private outcomes: readonly MobaFeedbackOutcome[] = []
  private entryAt = Infinity
  private movementBoundaryAt = Infinity
  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    super(config, seed, settings)
    if (this.params.kind !== 'dodge_choice') throw new Error('dodge-choice configuration required')
    this.p = this.params
    this.raw = { kind: 'dodge_choice', rounds: buildDodgeChoicePlan(seed, this.params).map(event => ({ ...event, hit: false, safe_entry_latency_ms: null })) }
  }
  protected nextEventAt(): number {
    const result = this.raw.rounds[this.round]
    this.movementBoundaryAt = this.nextMovementBoundaryAt()
    return Math.min(...[result.warning_at_ms, result.activation_at_ms, result.resolved_at_ms, this.entryAt,
      (this.round + 1) * this.p.window_ms, this.movementBoundaryAt].filter(at => at > this.now))
  }

  private isSafe(position: Vector2): boolean {
    return this.center !== null && Math.hypot(position.x - this.center.x, position.y - this.center.y) <= this.p.safe_radius - this.p.player_radius
  }

  protected simulate(from: number, to: number, previous: Vector2): void {
    const result = this.raw.rounds[this.round]
    if (to === this.entryAt) {
      result.safe_entry_latency_ms = to - result.warning_at_ms
      this.entryAt = Infinity
    }
    if (this.center && from >= result.warning_at_ms && from < result.resolved_at_ms) {
      // The circle is convex: an affine segment is safe iff both endpoints are safe.
      // At the excluded resolve endpoint continuity still makes an outside endpoint unsafe just before it.
      if (from >= result.activation_at_ms && (!this.isSafe(previous) || !this.isSafe(this.player))) result.hit = true
    }
    if (to === result.warning_at_ms) {
      this.center = dodgeSafeCenters(this.player, this.size)[result.safe_option_id]
      this.phase = 'warning'
      this.scheduleEntry()
    }
    if (to === result.activation_at_ms) {
      this.phase = 'active'
      if (!this.isSafe(this.player)) result.hit = true
    }
    if (to === result.resolved_at_ms) {
      this.phase = 'resolved'
      this.outcomes = [{ objective: 'dodge', round: this.round + 1, result: result.hit ? 'failure' : 'success', ...(result.hit ? { reason: 'hit' as const } : {}) }]
    }
    // Resolve the last half-open burst before advancing a round or ending at 60000.
    if (to === (this.round + 1) * this.p.window_ms && to < 60000) {
      this.round++
      this.center = null
      this.phase = 'waiting'
      this.entryAt = Infinity
    }
    if (to === this.movementBoundaryAt) this.scheduleEntry()
  }

  /** Solve each affine movement segment once, independent of render sampling. */
  private scheduleEntry(): void {
    this.entryAt = Infinity
    const result = this.raw.rounds[this.round]
    if (!this.center || this.now >= result.resolved_at_ms || result.safe_entry_latency_ms !== null) return
    const end = Math.min(this.nextMovementBoundaryAt(), result.resolved_at_ms)
    const fraction = firstCircleEntry(this.player, this.movedPosition((end - this.now) / 1000, end), this.center, this.p.safe_radius - this.p.player_radius)
    if (fraction === null) return
    const at = this.now + fraction * (end - this.now)
    if (at >= result.resolved_at_ms) return
    if (at === this.now) result.safe_entry_latency_ms = at - result.warning_at_ms
    else this.entryAt = at
  }

  protected acceptInput(action: InputAction): void {
    if (action.kind !== 'move' && !(this.commandPoint(action) && this.inWorld(action))) return
    this.movementInput(action)
    this.scheduleEntry()
  }
  scene(): MobaSceneSnapshot {
    const result = this.raw.rounds[this.round]
    const base = this.snapshot([], [], undefined, this.feedback({
      round: { current: this.round + 1, total: this.p.opportunity_count, status: result.hit ? 'failure' : this.phase === 'resolved' ? 'success' : 'active' },
      outcomes: this.outcomes,
    }))
    return { ...base, danger_choice: { kind: 'safe-circle-v3', phase: this.phase,
      safe_zone: this.center ? { position: { ...this.center }, radius: this.p.safe_radius } : null } }
  }
  finish(): RawResult { return structuredClone(this.raw) }
}

export function createDodgeChoiceEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  return new DodgeChoiceEngine(config, seed, settings)
}
