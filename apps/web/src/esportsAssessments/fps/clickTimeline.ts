import { isInputInWindow, type InputAction } from '../engineRuntime/types'

/** Independent v4 edge-triggered input clock; legacy FpsTimeline stays frozen. */
export class ClickFpsTimeline {
  at_ms = 0
  private held = false
  private nextAllowed = 0
  constructor(private readonly onShot: (at: number) => void, private readonly onInput?: (action: InputAction) => void) {}
  input(action: InputAction): void {
    if (!isInputInWindow(action) || action.at_ms < this.at_ms) return
    this.advanceTo(action.at_ms)
    this.onInput?.(action)
    if (action.kind !== 'fire') return
    if (!action.pressed) { this.held = false; return }
    if (this.held) return
    this.held = true
    if (action.at_ms < this.nextAllowed) return
    this.onShot(action.at_ms)
    this.nextAllowed = action.at_ms + 50
  }
  advanceTo(at: number): void {
    if (Number.isFinite(at) && at >= this.at_ms) this.at_ms = Math.min(60000, at)
  }
}
