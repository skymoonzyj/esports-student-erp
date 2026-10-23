import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { GameEngine, InputAction, MobaFeedbackOutcome, MobaFeedbackProgress, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { pointInCircle } from './collision'
import { createRandom, reachablePoint } from './schedule'
import { advanceAttack, cancelAttack, MobaEngine, type AttackState } from './world'

class RhythmEngine extends MobaEngine {
  private readonly p: Extract<TaskConfig['params'], { kind: 'rhythm' }>
  private readonly random: () => number
  private readonly raw: Extract<RawResult, { kind: 'rhythm' }>
  private round = 0
  private checkpoint: Vector2
  private target: Vector2
  private attack: AttackState = { started_at_ms: null, cancelled: false, resolved: false }
  private lastAttack = -Infinity
  private outcomes: readonly MobaFeedbackOutcome[] = []

  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    super(config, seed, settings)
    if (this.params.kind !== 'rhythm') throw new Error('rhythm configuration required')
    this.p = this.params
    this.random = createRandom(seed)
    this.raw = { kind: 'rhythm', rounds: Array.from({ length: this.p.opportunity_count }, () => ({ attacked: false, reached: false, cancelled_count: 0 })) }
    this.checkpoint = this.newCheckpoint()
    this.target = this.newTarget()
  }

  private newCheckpoint(): Vector2 { return reachablePoint(this.player, this.p.checkpoint_distance, this.p.player_radius, this.size, this.random) }
  private newTarget(): Vector2 {
    const start = this.random()
    for (let i = 0; i < 64; i++) {
      const target = reachablePoint(this.player, this.p.attack_range / 2, 20, this.size, () => (start + i / 64) % 1)
      if (!pointInCircle(target, this.checkpoint, 20 + this.p.checkpoint_tolerance)) return target
    }
    throw new Error('no non-overlapping attack target')
  }

  protected nextEventAt(): number {
    const arrival = this.attack.started_at_ms === null || this.attack.cancelled || this.attack.resolved ? Infinity : this.attack.started_at_ms + this.p.attack_windup_ms
    return Math.min(arrival, (this.round + 1) * this.p.window_ms)
  }

  private resolveAttack(): void {
    const next = advanceAttack(this.attack, this.now, this.p.attack_windup_ms)
    this.attack = next.state
    if (next.landed) {
      this.raw.rounds[this.round].attacked = true
      this.outcomes = [{ objective: 'attack', round: this.round + 1, result: 'success' }]
    }
  }

  protected simulate(): void {
    const end = (this.round + 1) * this.p.window_ms
    if (this.now < end) this.resolveAttack()
    if (this.now === end) {
      this.raw.rounds[this.round].reached = pointInCircle(this.player, this.checkpoint, this.p.checkpoint_tolerance)
      const result = this.raw.rounds[this.round]
      this.outcomes = [
        { objective: 'attack', round: this.round + 1, result: result.attacked ? 'success' : 'failure', ...(!result.attacked ? { reason: result.cancelled_count ? 'cancelled' as const : 'missed' as const } : {}) },
        { objective: 'checkpoint', round: this.round + 1, result: result.reached ? 'success' : 'failure', ...(!result.reached ? { reason: 'missed' as const } : {}) },
      ]
      this.attack = { started_at_ms: null, cancelled: false, resolved: false }
      if (this.now < 60000) {
        this.round++
        this.checkpoint = this.newCheckpoint()
        this.target = this.newTarget()
      }
    }
  }

  protected acceptInput(action: InputAction): void {
    const attacks = (action.kind === 'fire' && action.pressed) || (this.commandPoint(action) && pointInCircle(action, this.target, 20))
    if (attacks) {
      if (this.raw.rounds[this.round].attacked || this.now - this.lastAttack < this.p.attack_interval_ms || !pointInCircle(this.player, this.target, this.p.attack_range)) return
      // A desktop target command replaces pathing; a mobile attack button does not release the movement finger.
      if (action.kind === 'point') this.stopMoving()
      this.attack = { started_at_ms: this.now, cancelled: false, resolved: false }
      this.lastAttack = this.now
      if (this.isMoving()) {
        this.attack = cancelAttack(this.attack, this.now, this.p.attack_windup_ms).state
        this.raw.rounds[this.round].cancelled_count++
        this.outcomes = [{ objective: 'attack', round: this.round + 1, result: 'failure', reason: 'cancelled' }]
      }
      return
    }
    if (this.movementInput(action)) {
      const wasCancelled = this.attack.cancelled
      const next = cancelAttack(this.attack, this.now, this.p.attack_windup_ms)
      this.attack = next.state
      if (next.landed) {
        this.raw.rounds[this.round].attacked = true
        this.outcomes = [{ objective: 'attack', round: this.round + 1, result: 'success' }]
      }
      if (!wasCancelled && this.attack.cancelled) {
        this.raw.rounds[this.round].cancelled_count++
        this.outcomes = [{ objective: 'attack', round: this.round + 1, result: 'failure', reason: 'cancelled' }]
      }
    }
  }

  scene(): MobaSceneSnapshot {
    const progress: MobaFeedbackProgress = this.attack.cancelled ? 'attack-cancelled'
      : this.attack.started_at_ms === null ? 'attack-ready'
      : this.attack.resolved ? 'attack-completed' : 'attack-windup'
    const final = this.now >= 60000
    const result = this.raw.rounds[this.round]
    return this.snapshot([
      { id: `enemy-${this.round}`, type: 'enemy', position: this.target, radius: 20, state: this.now >= 60000 ? 'resolved' : this.raw.rounds[this.round].attacked ? 'hit' : 'active' },
      { id: `checkpoint-${this.round}`, type: 'checkpoint', position: this.checkpoint, radius: this.p.checkpoint_tolerance, state: this.now >= 60000 ? 'resolved' : 'active' },
    ], [], undefined, this.feedback({
      round: { current: this.round + 1, total: this.p.opportunity_count, status: !final ? 'active' : result.attacked && result.reached ? 'success' : 'failure' },
      progress,
      outcomes: this.outcomes,
    }))
  }

  finish(): RawResult { return structuredClone(this.raw) }
}

export function createRhythmEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  return new RhythmEngine(config, seed, settings)
}
