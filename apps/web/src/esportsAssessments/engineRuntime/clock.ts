const DEFAULT_DURATION_MS = 60_000
const DEFAULT_TICK_RATE = 120
const MAX_FRAME_GAP_MS = 500
const TICK_EPSILON_MS = 1e-7

export interface ClockFrame {
  readonly elapsed_ms: number
  readonly tick_times_ms: readonly number[]
  readonly frame_gap_ms: number
  readonly interrupted: boolean
  readonly finished: boolean
}

export class SimulationClock {
  private readonly totalTicks: number
  private processedTicks = 0
  private lastFrameAt: number
  private interrupted = false
  private finished = false

  constructor(
    private readonly startedAt: number,
    private readonly durationMs = DEFAULT_DURATION_MS,
    private readonly tickRate = DEFAULT_TICK_RATE,
  ) {
    if (!Number.isFinite(startedAt) || !Number.isFinite(durationMs) || durationMs <= 0 || !Number.isInteger(tickRate) || tickRate <= 0) {
      throw new Error('invalid simulation clock configuration')
    }
    this.totalTicks = Math.round(durationMs * tickRate / 1000)
    this.lastFrameAt = startedAt
  }

  frame(now: number): ClockFrame {
    if (!Number.isFinite(now)) {
      return this.interrupt(0, this.elapsedAt(this.lastFrameAt))
    }

    const elapsed_ms = this.elapsedAt(now)
    if (this.interrupted || this.finished) {
      return {
        elapsed_ms,
        tick_times_ms: [],
        frame_gap_ms: 0,
        interrupted: this.interrupted,
        finished: this.finished,
      }
    }

    const frame_gap_ms = now - this.lastFrameAt
    if (frame_gap_ms < 0 || frame_gap_ms > MAX_FRAME_GAP_MS) {
      return this.interrupt(frame_gap_ms, elapsed_ms)
    }
    this.lastFrameAt = now

    const stepMs = 1000 / this.tickRate
    const targetTick = Math.min(
      this.totalTicks,
      Math.floor((elapsed_ms + TICK_EPSILON_MS) / stepMs),
    )
    const tick_times_ms: number[] = []
    for (let tick = this.processedTicks + 1; tick <= targetTick; tick += 1) {
      tick_times_ms.push(tick * 1000 / this.tickRate)
    }
    this.processedTicks = targetTick
    this.finished = elapsed_ms >= this.durationMs

    return {
      elapsed_ms,
      tick_times_ms,
      frame_gap_ms,
      interrupted: false,
      finished: this.finished,
    }
  }

  private elapsedAt(now: number): number {
    return Math.min(this.durationMs, Math.max(0, now - this.startedAt))
  }

  private interrupt(frame_gap_ms: number, elapsed_ms: number): ClockFrame {
    this.interrupted = true
    return {
      elapsed_ms,
      tick_times_ms: [],
      frame_gap_ms,
      interrupted: true,
      finished: false,
    }
  }
}
