import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { GameEngine, InputAction, MobaFeedbackOutcome, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { sweptCircleCollision } from './collision'
import { createRandom, createThreat, type ThreatPath } from './schedule'
import { MobaEngine } from './world'

class DodgeEngine extends MobaEngine {
  private readonly p: Extract<TaskConfig['params'], { kind: 'dodge' }>
  private readonly random: () => number
  private readonly raw: Extract<RawResult, { kind: 'dodge' }>
  private round = 0
  private path: ThreatPath
  private outcomes: readonly MobaFeedbackOutcome[] = []

  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    super(config, seed, settings)
    if (this.params.kind !== 'dodge') throw new Error('dodge configuration required')
    this.p = this.params
    this.random = createRandom(seed)
    this.raw = { kind: 'dodge', threats: Array.from({ length: this.p.opportunity_count }, () => ({ hit: false, response_ms: null })) }
    this.path = createThreat(this.player, this.p, this.random)
  }

  protected nextEventAt(): number {
    const launch = this.round * this.p.window_ms + this.p.warning_ms
    return this.now < launch ? launch : (this.round + 1) * this.p.window_ms
  }

  private projectileAt(time: number): Vector2 {
    const travelled = Math.max(0, time - this.round * this.p.window_ms - this.p.warning_ms) / 1000 * this.p.projectile_speed
    return { x: this.path.origin.x + this.path.direction.x * travelled, y: this.path.origin.y + this.path.direction.y * travelled }
  }

  protected simulate(from: number, to: number, previous: Vector2): void {
    const result = this.raw.threats[this.round]
    const start = this.round * this.p.window_ms
    if (result.response_ms === null && Math.hypot(this.player.x - previous.x, this.player.y - previous.y) > 1e-10) result.response_ms = from - start
    if (!result.hit && from >= start + this.p.warning_ms && sweptCircleCollision(this.projectileAt(from), this.projectileAt(to), this.p.projectile_radius, previous, this.player, this.p.player_radius)) {
      result.hit = true
      this.outcomes = [{ objective: 'dodge', round: this.round + 1, result: 'failure', reason: 'hit' }]
    }
    if (to === (this.round + 1) * this.p.window_ms && to < 60000) {
      if (!result.hit) this.outcomes = [{ objective: 'dodge', round: this.round + 1, result: 'success' }]
      this.round++
      this.path = createThreat(this.player, this.p, this.random)
    } else if (to === 60000 && !result.hit) {
      this.outcomes = [{ objective: 'dodge', round: this.round + 1, result: 'success' }]
    }
  }

  protected acceptInput(action: InputAction): void { this.movementInput(action) }

  scene(): MobaSceneSnapshot {
    const launch = this.round * this.p.window_ms + this.p.warning_ms
    const hit = this.raw.threats[this.round].hit
    return this.snapshot([], [{ id: `threat-${this.round}`, type: 'threat', position: this.projectileAt(this.now), origin: this.path.origin, target: this.path.target, radius: this.p.projectile_radius, state: this.now >= 60000 ? 'expired' : hit ? 'hit' : this.now < launch ? 'warning' : 'active' }], undefined, this.feedback({
      round: { current: this.round + 1, total: this.p.opportunity_count, status: hit ? 'failure' : this.now >= 60000 ? 'success' : 'active' },
      outcomes: this.outcomes,
    }))
  }

  finish(): RawResult { return structuredClone(this.raw) }
}

export function createDodgeEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  return new DodgeEngine(config, seed, settings)
}
