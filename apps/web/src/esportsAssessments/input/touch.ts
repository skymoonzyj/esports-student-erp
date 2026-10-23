import type { ControlRect } from '../contracts'
import { clientToLogical, containsPoint } from '../engineRuntime/geometry'
import { isInputInWindow, type InputAction, type Rect, type Size, type Vector2 } from '../engineRuntime/types'
import { controlToRect } from '../settings/layout'

export interface TouchLayout {
  readonly bounds: Rect
  readonly logical_size: Size
  readonly sensitivity: number
  readonly controls: readonly ControlRect[]
  readonly aim_area?: Rect
  readonly get_skill_origin?: () => Vector2
}

export interface TouchPointerEvent extends Vector2 {
  readonly pointerId: number
  readonly at_ms: number
  readonly isPrimary?: boolean
}

export interface PointerCaptureOwner {
  capture(pointerId: number): void
  release(pointerId: number): void
}

type PointerRole = 'move' | 'fire' | 'aim' | 'point' | 'skill'

interface ActivePointer {
  readonly role: PointerRole
  readonly control?: ControlRect
  last: TouchPointerEvent
}

export class TouchInputRouter {
  private readonly active = new Map<number, ActivePointer>()

  constructor(
    private readonly layout: TouchLayout,
    private readonly emit: (action: InputAction) => void,
    private readonly capture?: PointerCaptureOwner,
  ) {
    if (layout.controls.some(control => control.id === 'skill') && !layout.get_skill_origin) {
      throw new Error('skill controls require a current logical origin')
    }
  }

  get activeCount(): number {
    return this.active.size
  }

  down(event: TouchPointerEvent): void {
    if (this.active.has(event.pointerId) || !this.validEvent(event) || !containsPoint(this.layout.bounds, event)) return

    const control = this.layout.controls.find(item =>
      containsPoint(controlToRect(item, this.layout.bounds), event),
    )
    if (control && this.controlClaimed(control.id)) return

    const role = control
      ? this.controlRole(control)
      : this.layout.aim_area && containsPoint(this.layout.aim_area, event)
        ? 'aim'
        : 'point'
    this.active.set(event.pointerId, { role, control, last: event })
    this.capture?.capture(event.pointerId)
    this.emitDown(role, control, event)
  }

  move(event: TouchPointerEvent): void {
    const pointer = this.active.get(event.pointerId)
    if (!pointer || !this.validEvent(event)) return

    if (pointer.role === 'aim') {
      this.emit({
        kind: 'look',
        at_ms: event.at_ms,
        dx: (event.x - pointer.last.x) / this.layout.bounds.width * 180 * this.layout.sensitivity,
        dy: (event.y - pointer.last.y) / this.layout.bounds.width * 180 * this.layout.sensitivity,
      })
    } else if (pointer.role === 'move' && pointer.control) {
      this.emitMove(pointer.control, event)
    } else if (pointer.role === 'skill') {
      const point = this.skillTarget(pointer.control, event)
      this.emit({ kind: 'skill', at_ms: event.at_ms, ...point, release: false })
    }
    pointer.last = event
  }

  up(event: TouchPointerEvent): void {
    const pointer = this.active.get(event.pointerId)
    if (!pointer) return

    if (this.validEvent(event)) {
      if (pointer.role === 'fire') {
        this.emit({ kind: 'fire', at_ms: event.at_ms, pressed: false })
      } else if (pointer.role === 'move') {
        this.emit({ kind: 'move', at_ms: event.at_ms, x: 0, y: 0 })
      } else if (pointer.role === 'skill') {
        this.emit({ kind: 'skill', at_ms: event.at_ms, ...this.skillTarget(pointer.control, event), release: true })
      }
    }
    this.forget(event.pointerId)
  }

  cancel(at_ms: number): void
  cancel(event: TouchPointerEvent): void
  cancel(eventOrAt: TouchPointerEvent | number): void {
    if (typeof eventOrAt !== 'number') {
      const pointer = this.active.get(eventOrAt.pointerId)
      if (pointer) this.cancelPointer(eventOrAt.pointerId, pointer, eventOrAt.at_ms, eventOrAt)
      return
    }

    if (!Number.isFinite(eventOrAt)) throw new Error('bulk cancellation requires a finite elapsed time')

    for (const [pointerId, pointer] of [...this.active]) {
      this.cancelPointer(pointerId, pointer, eventOrAt, pointer.last)
    }
  }

  private emitDown(role: PointerRole, control: ControlRect | undefined, event: TouchPointerEvent): void {
    if (role === 'fire') {
      this.emit({ kind: 'fire', at_ms: event.at_ms, pressed: true })
    } else if (role === 'move' && control) {
      this.emitMove(control, event)
    } else if (role === 'skill') {
      this.emit({ kind: 'skill', at_ms: event.at_ms, ...this.skillTarget(control, event), release: false })
    } else if (role === 'point') {
      this.emit({ kind: 'point', at_ms: event.at_ms, ...this.logicalPoint(event), button: 0 })
    }
  }

  private emitMove(control: ControlRect, event: TouchPointerEvent): void {
    const centerX = this.layout.bounds.x + control.x * this.layout.bounds.width
    const centerY = this.layout.bounds.y + control.y * this.layout.bounds.height
    const radius = control.width / 2
    let x = (event.x - centerX) / radius
    let y = (event.y - centerY) / radius
    const magnitude = Math.hypot(x, y)
    if (magnitude > 1) {
      x /= magnitude
      y /= magnitude
    }
    this.emit({ kind: 'move', at_ms: event.at_ms, x, y })
  }

  private cancelPointer(
    pointerId: number,
    pointer: ActivePointer,
    at_ms: number,
    position: Vector2,
  ): void {
    if (pointer.role === 'fire') {
      this.emit({ kind: 'fire', at_ms, pressed: false })
    } else if (pointer.role === 'move') {
      this.emit({ kind: 'move', at_ms, x: 0, y: 0 })
    } else if (pointer.role === 'skill') {
      this.emit({
        kind: 'skill',
        at_ms,
        ...this.skillTarget(pointer.control, position),
        release: false,
        cancel: true,
      })
    }
    this.forget(pointerId)
  }

  private logicalPoint(point: Vector2): Vector2 {
    const logical = clientToLogical(point, this.layout.bounds, this.layout.logical_size)
    return {
      x: Math.min(this.layout.logical_size.width, Math.max(0, logical.x)),
      y: Math.min(this.layout.logical_size.height, Math.max(0, logical.y)),
    }
  }

  private skillTarget(control: ControlRect | undefined, point: Vector2): Vector2 {
    if (!control || !this.layout.get_skill_origin) {
      throw new Error('skill controls require a current logical origin')
    }
    const origin = this.layout.get_skill_origin()
    const centerX = this.layout.bounds.x + control.x * this.layout.bounds.width
    const centerY = this.layout.bounds.y + control.y * this.layout.bounds.height
    return {
      x: origin.x + (point.x - centerX) * this.layout.logical_size.width / this.layout.bounds.width,
      y: origin.y + (point.y - centerY) * this.layout.logical_size.height / this.layout.bounds.height,
    }
  }

  private validEvent(event: TouchPointerEvent): boolean {
    return Number.isFinite(event.x)
      && Number.isFinite(event.y)
      && Number.isInteger(event.pointerId)
      && isInputInWindow({ kind: 'fire', at_ms: event.at_ms, pressed: false })
  }

  private controlRole(control: ControlRect): PointerRole {
    if (control.id === 'move') return 'move'
    if (control.id === 'skill') return 'skill'
    return 'fire'
  }

  private controlClaimed(controlId: ControlRect['id']): boolean {
    return [...this.active.values()].some(pointer => pointer.control?.id === controlId)
  }

  private forget(pointerId: number): void {
    this.active.delete(pointerId)
    this.capture?.release(pointerId)
  }
}
