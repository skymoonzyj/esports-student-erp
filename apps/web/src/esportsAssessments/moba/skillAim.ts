import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import type { GameEngine, InputAction, MobaFeedbackOutcome, MobaProjectileSnapshot, MobaSceneSnapshot, Vector2 } from '../engineRuntime/types'
import { sweptCircleCollision } from './collision'
import { createRandom, reachablePoint } from './schedule'
import { MobaEngine } from './world'

interface SkillProjectile {
  readonly origin: Vector2
  readonly direction: Vector2
  readonly releasedAt: number
  state: 'active' | 'hit' | 'expired'
  stoppedAt: number | null
}

/** Earliest contact inside a swept step, after the shared swept-collision predicate succeeds. */
function contactFraction(a0: Vector2, a1: Vector2, b0: Vector2, b1: Vector2, radius: number): number {
  const x = a0.x - b0.x, y = a0.y - b0.y
  const dx = a1.x - b1.x - x, dy = a1.y - b1.y - y
  const c = x * x + y * y - radius * radius
  if (c <= 0) return 0
  const a = dx * dx + dy * dy
  if (a === 0) return 0
  const b = 2 * (x * dx + y * dy)
  return Math.max(0, Math.min(1, (-b - Math.sqrt(Math.max(0, b * b - 4 * a * c))) / (2 * a)))
}

class SkillAimEngine extends MobaEngine {
  private readonly p: Extract<TaskConfig['params'], { kind: 'skill_aim' }>
  private readonly random: () => number
  private readonly raw: Extract<RawResult, { kind: 'skill_aim' }>
  private round = 0
  private targetStart: Vector2
  private targetDirection = 1
  private projectile: SkillProjectile | null = null
  private armed = false
  private aim: Vector2 | null = null
  private outcomes: readonly MobaFeedbackOutcome[] = []

  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    super(config, seed, settings)
    if (this.params.kind !== 'skill_aim') throw new Error('skill-aim configuration required')
    this.p = this.params
    this.random = createRandom(seed)
    this.raw = { kind: 'skill_aim', trials: Array.from({ length: this.p.opportunity_count }, () => ({ released: false, hit: false, angle_error_deg: null, release_latency_ms: null, hit_latency_ms: null })) }
    this.targetStart = this.newTarget()
  }

  private newTarget(): Vector2 {
    const position = reachablePoint(this.player, 260, this.p.target_radius, this.size, this.random)
    this.targetDirection = this.random() < .5 ? -1 : 1
    return position
  }

  private targetAt(time: number): Vector2 {
    const min = this.p.target_radius, span = this.size.width - min * 2
    const displacement = this.targetStart.x - min + this.targetDirection * this.p.target_speed * (time - this.round * this.p.window_ms) / 1000
    const phase = ((displacement % (span * 2)) + span * 2) % (span * 2)
    return { x: min + (phase <= span ? phase : span * 2 - phase), y: this.targetStart.y }
  }

  private projectileAt(time: number): Vector2 {
    const projectile = this.projectile!
    const end = projectile.stoppedAt === null ? time : Math.min(time, projectile.stoppedAt)
    const travelled = Math.min(this.p.max_distance, Math.max(0, end - projectile.releasedAt) / 1000 * this.p.projectile_speed)
    return { x: projectile.origin.x + projectile.direction.x * travelled, y: projectile.origin.y + projectile.direction.y * travelled }
  }

  protected nextEventAt(): number {
    const start = this.round * this.p.window_ms
    const firstDistance = this.targetDirection > 0 ? this.size.width - this.p.target_radius - this.targetStart.x : this.targetStart.x - this.p.target_radius
    const firstBounce = start + firstDistance / this.p.target_speed * 1000
    const bounceInterval = (this.size.width - this.p.target_radius * 2) / this.p.target_speed * 1000
    const nextBounce = this.now < firstBounce - 1e-7 ? firstBounce : firstBounce + (Math.floor((this.now - firstBounce + 1e-7) / bounceInterval) + 1) * bounceInterval
    const expires = this.projectile?.state === 'active' ? this.projectile.releasedAt + this.p.max_distance / this.p.projectile_speed * 1000 : Infinity
    return Math.min(start + this.p.window_ms, nextBounce, expires)
  }

  protected simulate(from: number, to: number): void {
    const end = (this.round + 1) * this.p.window_ms
    const projectile = this.projectile
    if (projectile?.state === 'active') {
      const p0 = this.projectileAt(from), p1 = this.projectileAt(to)
      const t0 = this.targetAt(from), t1 = this.targetAt(to)
      if (sweptCircleCollision(p0, p1, this.p.projectile_radius, t0, t1, this.p.target_radius)) {
        const at = from + (to - from) * contactFraction(p0, p1, t0, t1, this.p.projectile_radius + this.p.target_radius)
        if (at < end) {
          projectile.state = 'hit'
          projectile.stoppedAt = at
          this.raw.trials[this.round].hit = true
          this.raw.trials[this.round].hit_latency_ms = at - this.round * this.p.window_ms
          this.outcomes = [{ objective: 'skill-aim', round: this.round + 1, result: 'success' }]
        }
      }
      if (projectile.state === 'active' && to >= projectile.releasedAt + this.p.max_distance / this.p.projectile_speed * 1000) {
        projectile.state = 'expired'
        this.outcomes = [{ objective: 'skill-aim', round: this.round + 1, result: 'failure', reason: 'missed' }]
      }
    }
    if (to === end) {
      if (!this.raw.trials[this.round].hit) this.outcomes = [{ objective: 'skill-aim', round: this.round + 1, result: 'failure', reason: 'missed' }]
      this.projectile = null
      if (to < 60000) { this.round++; this.targetStart = this.newTarget() }
      else { this.armed = false; this.aim = null }
    }
  }

  protected acceptInput(action: InputAction): void {
    if (action.kind !== 'skill') { this.movementInput(action); return }
    if (action.cancel) {
      if (this.armed && !this.raw.trials[this.round].released) this.outcomes = [{ objective: 'skill-aim', round: this.round + 1, result: 'failure', reason: 'cancelled' }]
      this.armed = false; this.aim = null; return
    }
    if (!action.release) { this.armed = true; this.aim = { x: action.x, y: action.y }; return }
    if (!this.armed) return
    this.armed = false
    this.aim = null
    if (this.raw.trials[this.round].released) return
    const dx = action.x - this.player.x, dy = action.y - this.player.y
    const length = Math.hypot(dx, dy)
    if (!Number.isFinite(length) || length === 0) return
    const direction = { x: dx / length, y: dy / length }
    const target = this.targetAt(this.now)
    const targetX = target.x - this.player.x, targetY = target.y - this.player.y
    const targetDistance = Math.hypot(targetX, targetY)
    const cosine = targetDistance === 0 ? 1 : (direction.x * targetX + direction.y * targetY) / targetDistance
    this.raw.trials[this.round] = { released: true, hit: false, angle_error_deg: Math.acos(Math.max(-1, Math.min(1, cosine))) * 180 / Math.PI, release_latency_ms: this.now - this.round * this.p.window_ms, hit_latency_ms: null }
    this.projectile = { origin: { ...this.player }, direction, releasedAt: this.now, state: 'active', stoppedAt: null }
  }

  scene(): MobaSceneSnapshot {
    const projectiles: MobaProjectileSnapshot[] = []
    if (this.projectile) {
      const p = this.projectile
      projectiles.push({ id: `skill-${this.round}`, type: 'skill', origin: p.origin, position: this.projectileAt(this.now), target: { x: p.origin.x + p.direction.x * this.p.max_distance, y: p.origin.y + p.direction.y * this.p.max_distance }, radius: this.p.projectile_radius, state: p.state })
    } else if (this.armed && this.aim && Math.hypot(this.aim.x - this.player.x, this.aim.y - this.player.y) > 0) {
      projectiles.push({ id: `skill-preview-${this.round}`, type: 'skill', origin: this.player, position: this.player, target: this.aim, radius: this.p.projectile_radius, state: 'warning' })
    }
    const trial = this.raw.trials[this.round]
    const failed = this.projectile?.state === 'expired' || this.now >= 60000
    return this.snapshot([{ id: `skill-target-${this.round}`, position: this.targetAt(this.now), radius: this.p.target_radius, type: 'enemy', state: this.now >= 60000 ? 'resolved' : trial.hit ? 'hit' : 'active' }], projectiles, undefined, this.feedback({
      round: { current: this.round + 1, total: this.p.opportunity_count, status: trial.hit ? 'success' : failed ? 'failure' : 'active' },
      outcomes: this.outcomes,
    }))
  }

  finish(): RawResult { return structuredClone(this.raw) }
}

export function createSkillAimEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  return new SkillAimEngine(config, seed, settings)
}
