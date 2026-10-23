import { nextU32, taskConfigSchema, validateSettings, type ControlSettings, type RawResult, type TaskConfig } from '../contracts'
import { isInputInWindow, type GameEngine, type InputAction, type MobaFeedback, type MobaSceneSnapshot, type Size, type Vector2 } from '../engineRuntime/types'

export interface AttackState {
  readonly started_at_ms: number | null
  readonly cancelled: boolean
  readonly resolved: boolean
}

export function advanceAttack(state: AttackState, now_ms: number, windup_ms: number): { state: AttackState; landed: boolean } {
  if (state.started_at_ms === null || state.cancelled || state.resolved || now_ms < state.started_at_ms + windup_ms) return { state, landed: false }
  return { state: { ...state, resolved: true }, landed: true }
}

/** Resolve completion before applying movement at exactly the same timestamp. */
export function cancelAttack(state: AttackState, now_ms: number, windup_ms: number): { state: AttackState; landed: boolean } {
  const advanced = advanceAttack(state, now_ms, windup_ms)
  if (advanced.state.started_at_ms === null || advanced.state.resolved || advanced.state.cancelled) return advanced
  return { state: { ...advanced.state, cancelled: true }, landed: false }
}

/** Speed is logical units/second; dt_seconds is explicitly seconds, not milliseconds. */
export function moveToward(position: Vector2, target: Vector2, speed: number, dt_seconds: number): Vector2 {
  const dx = target.x - position.x, dy = target.y - position.y
  const distance = Math.hypot(dx, dy)
  if (distance === 0) return { ...position }
  const fraction = Math.min(1, Math.max(0, speed * dt_seconds) / distance)
  return { x: position.x + dx * fraction, y: position.y + dy * fraction }
}

export function clampPosition(position: Vector2, radius: number, size: Size): Vector2 {
  return { x: Math.max(radius, Math.min(size.width - radius, position.x)), y: Math.max(radius, Math.min(size.height - radius, position.y)) }
}

export type MobaParams = Extract<TaskConfig['params'], { logical_width: number }>

/** Shared deterministic timeline. Rendering may sample arbitrarily; simulation never skips a 120 Hz boundary. */
export abstract class MobaEngine implements GameEngine {
  protected readonly config: TaskConfig
  protected readonly params: MobaParams
  protected readonly size: Size
  protected now = 0
  protected player: Vector2
  protected facing = 0
  private destination: Vector2 | null = null
  private direction: Vector2 = { x: 0, y: 0 }
  private movementOrigin: Vector2
  private movementStartedAt = 0

  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    this.config = taskConfigSchema.parse(config)
    validateSettings(this.config, settings)
    nextU32(seed)
    if (!('logical_width' in this.config.params)) throw new Error('MOBA configuration required')
    this.params = this.config.params
    this.size = { width: this.params.logical_width, height: this.params.logical_height }
    this.player = { x: this.size.width / 2, y: this.size.height / 2 }
    this.movementOrigin = this.player
  }

  input(action: InputAction): void {
    if (!isInputInWindow(action) || action.at_ms < this.now) return
    if (('x' in action && !Number.isFinite(action.x)) || ('y' in action && !Number.isFinite(action.y))) return
    this.advanceTo(action.at_ms)
    this.acceptInput(action)
  }

  advanceTo(elapsed_ms: number): void {
    if (!Number.isFinite(elapsed_ms) || elapsed_ms <= this.now) return
    const end = Math.min(60000, elapsed_ms)
    while (this.now < end) {
      const tick = Math.floor((this.now + 1e-7) * 120 / 1000) + 1
      const to = Math.min(end, tick * 1000 / 120, this.nextEventAt())
      if (to <= this.now) throw new Error('non-advancing MOBA timeline')
      const from = this.now, previous = this.player
      this.player = this.movedPosition((to - from) / 1000, to)
      if (Math.hypot(this.player.x - previous.x, this.player.y - previous.y) > 1e-10) this.facing = Math.atan2(this.player.y - previous.y, this.player.x - previous.x)
      this.now = to
      this.simulate(from, to, previous)
    }
  }

  protected nextEventAt(): number { return Infinity }
  /** Only opt-in engines slice at direction changes caused by a destination or wall. */
  protected nextMovementBoundaryAt(): number {
    const origin = this.params.kind === 'dodge_choice' ? this.movementOrigin : this.player
    const start = this.params.kind === 'dodge_choice' ? this.movementStartedAt : this.now
    if (this.destination) {
      const distance = Math.hypot(this.destination.x - origin.x, this.destination.y - origin.y)
      const at = start + distance / this.params.player_speed * 1000
      return at > this.now ? at : Infinity
    }
    const radius = this.params.player_radius
    const xWall = this.direction.x > 0 ? this.size.width - radius : radius
    const yWall = this.direction.y > 0 ? this.size.height - radius : radius
    const xAt = this.direction.x === 0 ? Infinity : start + (xWall - origin.x) / (this.direction.x * this.params.player_speed) * 1000
    const yAt = this.direction.y === 0 ? Infinity : start + (yWall - origin.y) / (this.direction.y * this.params.player_speed) * 1000
    return Math.min(xAt > this.now ? xAt : Infinity, yAt > this.now ? yAt : Infinity)
  }
  protected abstract simulate(from: number, to: number, previousPlayer: Vector2): void
  protected abstract acceptInput(action: InputAction): void
  abstract scene(): MobaSceneSnapshot
  abstract finish(): RawResult

  protected movementInput(action: InputAction): boolean {
    if (action.kind === 'move') {
      this.movementOrigin = this.player
      this.movementStartedAt = this.now
      const length = Math.hypot(action.x, action.y)
      this.direction = { x: action.x / Math.max(1, length), y: action.y / Math.max(1, length) }
      this.destination = null
      return this.isMoving()
    }
    if (this.commandPoint(action) && this.inWorld(action)) {
      this.movementOrigin = this.player
      this.movementStartedAt = this.now
      this.destination = clampPosition(action, this.params.player_radius, this.size)
      this.direction = { x: 0, y: 0 }
      return this.isMoving()
    }
    return false
  }

  protected isMoving(): boolean {
    const next = this.movedPosition(1 / 120)
    return Math.hypot(next.x - this.player.x, next.y - this.player.y) > 1e-10
  }

  protected stopMoving(): void {
    this.movementOrigin = this.player; this.movementStartedAt = this.now
    this.destination = null; this.direction = { x: 0, y: 0 }
  }

  protected inWorld(point: Vector2): boolean {
    return point.x >= 0 && point.x <= this.size.width && point.y >= 0 && point.y <= this.size.height
  }

  /** Registered PC MOBA v2/v3 accept primary commands without changing v1 or map semantics. */
  protected commandPoint(action: InputAction): action is Extract<InputAction, { kind: 'point' }> {
    return action.kind === 'point' && (action.button === 2
      || (action.button === 0 && this.config.suite_id === 'pc-moba' && ['pc-moba-v2', 'pc-moba-v3'].includes(this.config.engine_version) && this.config.test_id !== 'map-awareness'))
  }

  protected feedback(value: Omit<MobaFeedback, 'version' | 'task_id'>): MobaFeedback | undefined {
    if (![`${this.config.suite_id}-v2`, `${this.config.suite_id}-v3`].includes(this.config.engine_version)) return undefined
    return { version: 'v2', task_id: this.config.test_id as MobaFeedback['task_id'], ...value }
  }

  protected movedPosition(dt_seconds: number, at_ms = this.now + dt_seconds * 1000): Vector2 {
    // New safe-circle rules use an input-time anchor, so rendering cannot accumulate position drift.
    // Frozen engines deliberately retain their original incremental integration.
    const origin = this.params.kind === 'dodge_choice' ? this.movementOrigin : this.player
    const elapsed = this.params.kind === 'dodge_choice' ? (at_ms - this.movementStartedAt) / 1000 : dt_seconds
    return clampPosition(this.destination
      ? moveToward(origin, this.destination, this.params.player_speed, elapsed)
      : { x: origin.x + this.direction.x * this.params.player_speed * elapsed, y: origin.y + this.direction.y * this.params.player_speed * elapsed }, this.params.player_radius, this.size)
  }

  protected snapshot(
    targets: MobaSceneSnapshot['targets'] = [],
    projectiles: MobaSceneSnapshot['projectiles'] = [],
    minimap: MobaSceneSnapshot['minimap'] = { visible: false, bounds: { x: 820, y: 0, width: 180, height: 110 }, event: null },
    feedback?: MobaFeedback,
  ): MobaSceneSnapshot {
    return structuredClone({ kind: 'moba', at_ms: this.now, logical_size: this.size, player: { id: 'player', position: this.player, radius: this.params.player_radius, facing_rad: this.facing }, targets, projectiles, minimap, ...(feedback ? { feedback } : {}) })
  }
}
