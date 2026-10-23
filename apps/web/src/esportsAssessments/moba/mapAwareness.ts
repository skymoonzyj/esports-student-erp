import type { ControlSettings, RawResult, TaskConfig } from '../contracts'
import { containsPoint } from '../engineRuntime/geometry'
import type { GameEngine, InputAction, MobaFeedbackOutcome, MobaSceneSnapshot, Rect, Vector2 } from '../engineRuntime/types'
import { pointInCircle } from './collision'
import { createRandom } from './schedule'
import { MobaEngine } from './world'

class MapEngine extends MobaEngine {
  private readonly p: Extract<TaskConfig['params'], { kind: 'map' }>
  private readonly raw: Extract<RawResult, { kind: 'map' }>
  private readonly targets: Vector2[]
  private readonly quadrants: (0 | 1 | 2 | 3)[]
  private readonly bounds: Rect
  private outcomes: readonly MobaFeedbackOutcome[] = []

  constructor(config: TaskConfig, seed: number, settings: ControlSettings) {
    super(config, seed, settings)
    if (this.params.kind !== 'map') throw new Error('map configuration required')
    this.p = this.params
    this.bounds = { x: this.p.minimap_x, y: this.p.minimap_y, width: this.p.minimap_width, height: this.p.minimap_height }
    this.raw = { kind: 'map', main_hits: Array(this.p.opportunity_count).fill(false), events: Array.from({ length: this.p.event_count }, () => ({ response: 'missed', latency_ms: this.p.event_window_ms })), unprompted_count: 0 }
    const random = createRandom(seed)
    // Main targets stay in the open central region, never under the minimap.
    this.targets = Array.from({ length: this.p.opportunity_count }, () => ({ x: 200 + random() * 500, y: 160 + random() * 220 }))
    this.quadrants = Array.from({ length: this.p.event_count }, () => Math.floor(random() * 4) as 0 | 1 | 2 | 3)
  }

  protected simulate(from: number, to: number): void {
    const mainBoundary = Math.floor(to / this.p.window_ms)
    if (to > 0 && to === mainBoundary * this.p.window_ms) {
      const index = Math.min(this.p.opportunity_count - 1, mainBoundary - 1)
      if (!this.raw.main_hits[index]) this.outcomes = [{ objective: 'main-target', round: index + 1, result: 'failure', reason: 'missed' }]
    }
    for (let index = 0; index < this.p.event_count; index++) {
      const end = this.p.event_start_ms + index * this.p.event_interval_ms + this.p.event_window_ms
      if (from < end && to >= end && this.raw.events[index].response === 'missed') {
        this.outcomes = [{ objective: 'map-event', round: index + 1, result: 'failure', reason: 'missed' }]
      }
    }
  }

  private eventIndex(): number | null {
    const index = Math.floor((this.now - this.p.event_start_ms) / this.p.event_interval_ms)
    return index >= 0 && index < this.p.event_count && this.now < this.p.event_start_ms + index * this.p.event_interval_ms + this.p.event_window_ms ? index : null
  }

  protected acceptInput(action: InputAction): void {
    if (action.kind !== 'point') { this.movementInput(action); return }
    if (!this.inWorld(action)) return
    if (containsPoint(this.bounds, action)) {
      if (action.button !== 0) return
      const index = this.eventIndex()
      if (index === null) { this.raw.unprompted_count = Math.min(60000, this.raw.unprompted_count + 1); return }
      if (this.raw.events[index].response !== 'missed') return
      const quadrant = Number(action.x >= this.bounds.x + this.bounds.width / 2) + 2 * Number(action.y >= this.bounds.y + this.bounds.height / 2)
      this.raw.events[index] = { response: quadrant === this.quadrants[index] ? 'correct' : 'wrong', latency_ms: this.now - this.p.event_start_ms - index * this.p.event_interval_ms }
      this.outcomes = [{ objective: 'map-event', round: index + 1, result: quadrant === this.quadrants[index] ? 'success' : 'failure', ...(quadrant !== this.quadrants[index] ? { reason: 'wrong' as const } : {}) }]
      return
    }
    if (action.button === 2) { this.movementInput(action); return }
    const index = Math.floor(this.now / this.p.window_ms)
    if (!this.raw.main_hits[index] && pointInCircle(action, this.targets[index], this.p.target_radius)) {
      this.raw.main_hits[index] = true
      this.outcomes = [{ objective: 'main-target', round: index + 1, result: 'success' }]
    }
  }

  scene(): MobaSceneSnapshot {
    const round = Math.min(this.p.opportunity_count - 1, Math.floor(this.now / this.p.window_ms))
    const index = this.eventIndex()
    const quadrant = index === null ? 0 : this.quadrants[index]
    const hit = this.raw.main_hits[round]
    return this.snapshot([{ id: `main-${round}`, type: 'main', position: this.targets[round], radius: this.p.target_radius, state: this.now >= 60000 ? 'resolved' : hit ? 'hit' : 'active' }], [], {
      visible: true,
      bounds: this.bounds,
      event: index === null ? null : { id: `map-${index}`, quadrant, position: { x: this.bounds.x + this.bounds.width * (quadrant % 2 === 0 ? .25 : .75), y: this.bounds.y + this.bounds.height * (quadrant < 2 ? .25 : .75) }, state: this.raw.events[index].response === 'missed' ? 'active' : 'resolved' },
    }, this.feedback({
      round: { current: round + 1, total: this.p.opportunity_count, status: hit ? 'success' : this.now >= 60000 ? 'failure' : 'active' },
      outcomes: this.outcomes,
    }))
  }

  finish(): RawResult { return structuredClone(this.raw) }
}

export function createMapEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  return new MapEngine(config, seed, settings)
}
