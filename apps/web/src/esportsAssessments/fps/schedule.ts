import { nextU32, shuffled, taskConfigSchema, validateRaw, validateSettings, type ControlSettings, type RawResult, type TaskConfig } from '../contracts'
import { isInputInWindow, type InputAction, type Size } from '../engineRuntime/types'
import { INITIAL_CAMERA, isTargetFullyVisible, sampleVisibleDirection, verticalFovDeg } from './math'

export function createRng(seed: number): () => number {
  nextU32(seed)
  let state = seed
  return () => { state = nextU32(state); return state / 0x100000000 }
}

export function createDistanceSchedule(distances: readonly number[], repetitions: number, seed: number): number[] {
  return shuffled(distances.flatMap(value => Array<number>(repetitions).fill(value)), seed)
}

/** Public preflight: call before acquiring a server attempt/ticket. No randomness is consumed. */
export function validateFpsViewport(config: TaskConfig, viewport: Size): void {
  const p = taskConfigSchema.parse(config).params
  verticalFovDeg(viewport)
  if (!config.suite_id.endsWith('fps')) throw new Error('FPS 套系配置不匹配')
  if (p.kind === 'tracking') {
    for (const yaw_deg of [-p.horizontal_bound_deg, p.horizontal_bound_deg]) {
      for (const pitch_deg of [-p.vertical_bound_deg, p.vertical_bound_deg]) {
        if (!isTargetFullyVisible({ yaw_deg, pitch_deg }, p.target_diameter_deg / 2, INITIAL_CAMERA, viewport)) throw new Error('当前 viewport 视野不足以完整显示跟踪区域')
      }
    }
  } else if (p.kind === 'aim' || p.kind === 'recognition') {
    const maximum = p.angle_distances_deg.length
      ? Math.max(...p.angle_distances_deg)
      : p.kind === 'aim'
        ? config.catalog_version === `${config.suite_id}-v3` && config.test_id === 'switching'
          ? p.target_separation_deg
          : p.target_separation_deg / 2
        : 0
    sampleVisibleDirection(INITIAL_CAMERA, maximum, p.target_diameter_deg, viewport, () => 0)
  } else if (p.kind === 'move_fire') {
    sampleVisibleDirection(INITIAL_CAMERA, 0, p.target_diameter_deg, viewport, () => 0)
  } else if (p.kind === 'move_fire_v3') {
    for (const pitch_deg of [-p.pitch_limit_deg, 0, p.pitch_limit_deg]) {
      sampleVisibleDirection(
        { ...INITIAL_CAMERA, pitch_deg },
        p.target_offset_max_deg,
        p.target_diameter_deg,
        viewport,
        () => 0,
        p.pitch_limit_deg,
      )
    }
  }
}

export function validateFpsSetup(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): void {
  validateFpsViewport(config, viewport)
  nextU32(seed)
  validateSettings(config, settings)
}

export function freezeResult(config: TaskConfig, raw: RawResult): RawResult {
  const result = validateRaw(config, raw)
  const freeze = (value: unknown) => {
    if (value && typeof value === 'object') {
      Object.values(value).forEach(freeze)
      Object.freeze(value)
    }
  }
  freeze(result)
  return result
}

interface TimelineCallbacks {
  /** Opt-out for tick-only callbacks. Do not disable for timestamped pose coverage. */
  readonly integrateAtInput?: boolean
  readonly onAdvance?: (from_ms: number, to_ms: number) => void
  readonly onInput?: (action: InputAction) => void
  /** Return false when the scene does not accept a shot (e.g. recognition blank time). */
  readonly onShot?: (at_ms: number) => boolean
}

/** Render calls never split integration. Only fixed ticks and timestamped input/fire events do. */
export class FpsTimeline {
  at_ms = 0
  private integratedAt = 0
  private nextTick = 1
  private held = false
  private nextFire = Infinity
  private nextAllowedFire = 0

  constructor(private readonly callbacks: TimelineCallbacks, private readonly fireIntervalMs = 200) {
    if (!Number.isFinite(fireIntervalMs) || fireIntervalMs < 200) throw new Error('invalid fire interval')
  }

  advanceTo(elapsed_ms: number): void {
    if (!Number.isFinite(elapsed_ms) || elapsed_ms < this.at_ms) return
    const end = Math.min(60000, elapsed_ms)
    while (true) {
      const tickTime = this.nextTick <= 7200 ? this.nextTick * 1000 / 120 : Infinity
      const shotTime = this.held && this.callbacks.onShot && this.nextFire < 60000 ? this.nextFire : Infinity
      const next = Math.min(tickTime, shotTime)
      if (next > end + 1e-7) break
      this.integrate(next)
      if (next === tickTime) this.nextTick++
      if (next === shotTime) this.fire(next)
    }
    this.at_ms = end
  }

  input(action: InputAction): void {
    if (!isInputInWindow(action) || action.at_ms < this.at_ms) return
    this.advanceTo(action.at_ms)
    if (this.callbacks.integrateAtInput !== false) this.integrate(action.at_ms)
    this.callbacks.onInput?.(action)
    if (action.kind !== 'fire' || !this.callbacks.onShot) return
    if (!action.pressed) { this.held = false; this.nextFire = Infinity; return }
    if (this.held) return
    this.held = true
    if (action.at_ms >= this.nextAllowedFire) this.fire(action.at_ms)
    else this.nextFire = this.nextAllowedFire
  }

  private integrate(to: number): void {
    if (to <= this.integratedAt) return
    this.callbacks.onAdvance?.(this.integratedAt, to)
    this.integratedAt = to
  }

  private fire(at: number): void {
    if (this.callbacks.onShot?.(at)) {
      this.nextAllowedFire = at + this.fireIntervalMs
      this.nextFire = this.nextAllowedFire
    } else {
      // A hidden/consumed opportunity is not a shot. Retry at the next fixed
      // boundary so a blank period cannot consume the next round's opening.
      this.nextFire = (Math.floor(at * 120 / 1000 + 1e-7) + 1) * 1000 / 120
    }
  }
}
