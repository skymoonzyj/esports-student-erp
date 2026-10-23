import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { GameEngine, InputAction, MobaFeedbackOutcome, MobaFeedbackProgress, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { pointInCircle } from './collision'
import { createRandom } from './schedule'
import { advanceAttack, cancelAttack, MobaEngine, type AttackState } from './world'

class LastHitEngine extends MobaEngine {
  private readonly p: Extract<TaskConfig['params'], { kind: 'last_hit' }>
  private readonly starts: number[]
  private readonly raw: Extract<RawResult, { kind: 'last_hit' }>
  private wave = 0
  private attack: AttackState = { started_at_ms: null, cancelled: false, resolved: false }
  private lastAttack = -Infinity
  private outcomes: readonly MobaFeedbackOutcome[] = []

  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    super(config, seed, settings)
    if (this.params.kind !== 'last_hit') throw new Error('last-hit configuration required')
    this.p = this.params
    this.starts = this.p.window_starts_ms.flatMap(start => [start, start, start])
    const random = createRandom(seed)
    for (let i = this.starts.length - 1; i > 0; i--) {
      const j = Math.floor(random() * (i + 1))
      ;[this.starts[i], this.starts[j]] = [this.starts[j], this.starts[i]]
    }
    this.raw = { kind: 'last_hit', waves: Array.from({ length: this.p.opportunity_count }, () => ({ success: false, early: 0, late: 0, offset_ms: null })) }
  }

  private target(): Vector2 {
    // A timing task, not a chase: keep a distinct nearby minion inside the world.
    const offset = this.p.attack_range / 2
    return { x: this.player.x + (this.player.x + offset <= this.size.width - 20 ? offset : -offset), y: Math.max(20, Math.min(this.size.height - 20, this.player.y)) }
  }

  protected nextEventAt(): number {
    const arrival = this.attack.started_at_ms === null || this.attack.cancelled || this.attack.resolved ? Infinity : this.attack.started_at_ms + this.p.attack_windup_ms
    return Math.min(arrival, (this.wave + 1) * this.p.window_ms)
  }

  private recordArrival(): void {
    const wave = this.raw.waves[this.wave]
    if (wave.success) return
    const offset = this.now - this.wave * this.p.window_ms - this.starts[this.wave]
    if (offset < 0) {
      wave.early++
      this.outcomes = [{ objective: 'last-hit', round: this.wave + 1, result: 'failure', reason: 'early' }]
    } else if (offset >= this.p.last_hit_window_ms) {
      wave.late++
      this.outcomes = [{ objective: 'last-hit', round: this.wave + 1, result: 'failure', reason: 'late' }]
    } else {
      wave.success = true
      wave.offset_ms = offset
      this.outcomes = [{ objective: 'last-hit', round: this.wave + 1, result: 'success' }]
    }
  }

  protected simulate(): void {
    if (this.now === (this.wave + 1) * this.p.window_ms) {
      // A windup belongs to its original wave; the next minion cannot inherit it.
      if (!this.raw.waves[this.wave].success) this.outcomes = [{ objective: 'last-hit', round: this.wave + 1, result: 'failure', reason: 'missed' }]
      this.attack = { started_at_ms: null, cancelled: false, resolved: false }
      if (this.now < 60000) this.wave++
      return
    }
    const next = advanceAttack(this.attack, this.now, this.p.attack_windup_ms)
    this.attack = next.state
    if (next.landed) this.recordArrival()
  }

  protected acceptInput(action: InputAction): void {
    if (this.commandPoint(action) && pointInCircle(action, this.target(), 20)) {
      if (this.raw.waves[this.wave].success || this.now - this.lastAttack < this.p.attack_interval_ms) return
      this.stopMoving()
      this.attack = { started_at_ms: this.now, cancelled: false, resolved: false }
      this.lastAttack = this.now
      return
    }
    if (this.movementInput(action)) {
      const wasCancelled = this.attack.cancelled
      const next = cancelAttack(this.attack, this.now, this.p.attack_windup_ms)
      this.attack = next.state
      if (next.landed) this.recordArrival()
      if (!wasCancelled && this.attack.cancelled) this.outcomes = [{ objective: 'last-hit', round: this.wave + 1, result: 'failure', reason: 'cancelled' }]
    }
  }

  scene(): MobaSceneSnapshot {
    const elapsed = this.now - this.wave * this.p.window_ms
    const start = this.starts[this.wave]
    const success = this.raw.waves[this.wave].success
    const ended = elapsed >= start + this.p.last_hit_window_ms
    const health = success || ended ? 0 : elapsed < start
      ? 1 - .89 * elapsed / start
      : .1 - .09 * (elapsed - start) / this.p.last_hit_window_ms
    const progress: MobaFeedbackProgress = this.attack.cancelled ? 'attack-cancelled'
      : this.attack.started_at_ms === null ? 'attack-ready'
      : this.attack.resolved ? 'attack-completed' : 'attack-windup'
    const outcomes = !success && ended && !this.outcomes.some(outcome => outcome.round === this.wave + 1 && outcome.reason === 'late')
      ? [{ objective: 'last-hit', round: this.wave + 1, result: 'failure', reason: 'missed' } as const]
      : this.outcomes
    return this.snapshot([{ id: `minion-${this.wave}`, type: 'minion', position: this.target(), radius: 20,
      state: success ? 'hit' : ended ? 'resolved' : 'active', health_ratio: health }], [], undefined, this.feedback({
      round: { current: this.wave + 1, total: this.p.opportunity_count, status: success ? 'success' : ended ? 'failure' : 'active' },
      progress,
      outcomes,
    }))
  }

  finish(): RawResult { return structuredClone(this.raw) }
}

export function createLastHitEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  return new LastHitEngine(config, seed, settings)
}
