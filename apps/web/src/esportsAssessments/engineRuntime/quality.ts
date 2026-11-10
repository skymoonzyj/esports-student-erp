import {
  qualityInputSchema,
  type InputKind,
  type QualityInput,
} from '../contracts'

const LONG_FRAME_GAP_MS = 50
const LONG_FRAME_WARNING_TOTAL_MS = 3000
const FATAL_FRAME_GAP_MS = 500

export class FrameQualityTracker {
  constructor(private readonly durationMs = 60000) {
    if (![60000, 300000, 20000].includes(durationMs)) throw new Error('invalid quality duration')
  }
  private maxFrameGapMs = 0
  private longFrameGapTotalMs = 0
  private sawMouse = false
  private sawTouch = false
  private hidden = false
  private interrupted = false

  get longFrameWarning(): boolean {
    return this.longFrameGapTotalMs > LONG_FRAME_WARNING_TOTAL_MS
  }

  get fatalFrameGap(): boolean {
    return this.maxFrameGapMs > FATAL_FRAME_GAP_MS
  }

  recordFrameGap(gap_ms: number, observedDurationMs = gap_ms): void {
    if (!Number.isFinite(gap_ms) || gap_ms < 0 || gap_ms > this.durationMs) {
      throw new Error('invalid frame gap')
    }
    if (!Number.isFinite(observedDurationMs) || observedDurationMs < 0 || observedDurationMs > gap_ms) {
      throw new Error('invalid frame observation overlap')
    }
    this.maxFrameGapMs = Math.max(this.maxFrameGapMs, gap_ms)
    if (gap_ms > LONG_FRAME_GAP_MS) this.longFrameGapTotalMs += observedDurationMs
  }

  observeInput(kind: 'mouse' | 'touch'): void {
    if (kind === 'mouse') this.sawMouse = true
    else this.sawTouch = true
  }

  markHidden(): void {
    this.hidden = true
  }

  markInterrupted(): void {
    this.interrupted = true
  }

  snapshot(duration_ms: number): QualityInput {
    if (duration_ms > this.durationMs) throw new Error('quality duration exceeds configured window')
    return qualityInputSchema.parse({
      duration_ms,
      max_frame_gap_ms: this.maxFrameGapMs,
      long_frame_gap_total_ms: this.longFrameGapTotalMs,
      input_kind: this.inputKind(),
      hidden: this.hidden,
      interrupted: this.interrupted,
    })
  }

  private inputKind(): InputKind {
    if (this.sawMouse && this.sawTouch) return 'mixed'
    if (this.sawMouse) return 'mouse'
    if (this.sawTouch) return 'touch'
    return 'unknown'
  }
}
