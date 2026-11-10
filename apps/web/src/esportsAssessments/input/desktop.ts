import type { SuiteId } from '../contracts'
import { clientToLogical, containsPoint } from '../engineRuntime/geometry'
import { isInputInWindow, type InputAction, type Rect, type Size } from '../engineRuntime/types'

export interface DesktopInputLayout {
  readonly suite_id: Extract<SuiteId, 'pc-fps' | 'pc-moba'>
  readonly sensitivity: number
  readonly bounds: Rect
  readonly logical_size: Size
  readonly duration_ms?: number
}

export interface DesktopMoveEvent {
  readonly movementX: number
  readonly movementY: number
  readonly at_ms: number
}

export interface DesktopPointerEvent {
  readonly pointerId: number
  readonly button: number
  readonly x: number
  readonly y: number
  readonly at_ms: number
}

export class DesktopInputRouter {
  private firePressed = false

  constructor(
    private readonly layout: DesktopInputLayout,
    private readonly emit: (action: InputAction) => void,
  ) {}

  move(event: DesktopMoveEvent): void {
    if (this.layout.suite_id !== 'pc-fps' || !this.validAt(event.at_ms)) return
    if (![event.movementX, event.movementY].every(Number.isFinite)) return
    this.emit({
      kind: 'look',
      at_ms: event.at_ms,
      dx: event.movementX * 0.1 * this.layout.sensitivity,
      dy: event.movementY * 0.1 * this.layout.sensitivity,
    })
  }

  down(event: DesktopPointerEvent): void {
    if (!this.validPointer(event)) return
    if (this.layout.suite_id === 'pc-fps') {
      if (event.button === 0 && !this.firePressed) {
        this.firePressed = true
        this.emit({ kind: 'fire', at_ms: event.at_ms, pressed: true })
      }
      return
    }

    if (!containsPoint(this.layout.bounds, event)) return

    if (event.button === 0 || event.button === 2) {
      const point = clientToLogical(event, this.layout.bounds, this.layout.logical_size)
      this.emit({ kind: 'point', at_ms: event.at_ms, ...point, button: event.button })
    }
  }

  up(event: DesktopPointerEvent): void {
    if (this.layout.suite_id === 'pc-fps' && event.button === 0 && this.firePressed) {
      this.firePressed = false
      if (this.validAt(event.at_ms)) this.emit({ kind: 'fire', at_ms: event.at_ms, pressed: false })
    }
  }

  cancel(at_ms: number): void {
    if (!this.firePressed) return
    this.firePressed = false
    if (this.validAt(at_ms)) this.emit({ kind: 'fire', at_ms, pressed: false })
  }

  private validPointer(event: DesktopPointerEvent): boolean {
    return Number.isInteger(event.pointerId)
      && Number.isFinite(event.x)
      && Number.isFinite(event.y)
      && this.validAt(event.at_ms)
  }

  private validAt(at_ms: number): boolean {
    return isInputInWindow({ kind: 'fire', at_ms, pressed: false }, this.layout.duration_ms)
  }
}

export interface PointerLockTarget {
  requestPointerLock(options?: { unadjustedMovement?: boolean }): Promise<void> | void
}

export interface PointerLockEventSource {
  addEventListener(type: 'pointerlockchange' | 'pointerlockerror', listener: () => void): void
  removeEventListener(type: 'pointerlockchange' | 'pointerlockerror', listener: () => void): void
}

export interface PointerLockRequestContext {
  readonly events: PointerLockEventSource
  readonly isActive: () => boolean
  readonly signal?: AbortSignal
}

export interface PointerLockResult {
  readonly pointer_lock_active: boolean
  readonly raw_mouse_requested: boolean
  readonly raw_mouse_confirmed: boolean
  readonly mouse_mode: 'raw' | 'relative' | null
}

export async function requestDesktopPointerLock(
  target: PointerLockTarget,
  raw_mouse_requested: boolean,
  context: PointerLockRequestContext,
): Promise<PointerLockResult> {
  if (context.signal?.aborted) return inactivePointerLock(raw_mouse_requested)

  const requested = await requestPointerLockOnce(
    target,
    raw_mouse_requested ? { unadjustedMovement: true } : undefined,
    context,
  )
  if (requested.status === 'aborted') return inactivePointerLock(raw_mouse_requested)

  if (requested.status === 'locked') {
    const raw_mouse_confirmed = raw_mouse_requested && requested.promiseBased
    return {
      pointer_lock_active: true,
      raw_mouse_requested,
      raw_mouse_confirmed,
      mouse_mode: raw_mouse_confirmed ? 'raw' : 'relative',
    }
  }

  if (!raw_mouse_requested || !requested.promiseBased) {
    return inactivePointerLock(raw_mouse_requested)
  }

  const fallback = await requestPointerLockOnce(target, undefined, context)
  if (fallback.status !== 'locked') return inactivePointerLock(raw_mouse_requested)
  return {
    pointer_lock_active: true,
    raw_mouse_requested: true,
    raw_mouse_confirmed: false,
    mouse_mode: 'relative',
  }
}

type PointerLockAttempt = {
  readonly status: 'locked' | 'error' | 'aborted'
  readonly promiseBased: boolean
}

async function requestPointerLockOnce(
  target: PointerLockTarget,
  options: { unadjustedMovement: boolean } | undefined,
  context: PointerLockRequestContext,
): Promise<PointerLockAttempt> {
  const observation = observeLegacyPointerLock(context)
  let response: Promise<void> | void
  try {
    response = options ? target.requestPointerLock(options) : target.requestPointerLock()
  } catch {
    observation.dispose()
    return { status: 'error', promiseBased: false }
  }

  if (isPromiseLike(response)) {
    observation.dispose()
    const status = await settlePointerLockPromise(response, context.signal)
    return {
      status: status === 'resolved' && context.isActive() ? 'locked' : status === 'aborted' ? 'aborted' : 'error',
      promiseBased: true,
    }
  }

  const status = await observation.promise
  return { status, promiseBased: false }
}

function observeLegacyPointerLock(context: PointerLockRequestContext): {
  readonly promise: Promise<'locked' | 'error' | 'aborted'>
  readonly dispose: () => void
} {
  let settled = false
  let resolveStatus!: (status: 'locked' | 'error' | 'aborted') => void
  const promise = new Promise<'locked' | 'error' | 'aborted'>(resolve => {
    resolveStatus = resolve
  })
  const cleanup = () => {
    context.events.removeEventListener('pointerlockchange', onChange)
    context.events.removeEventListener('pointerlockerror', onError)
    context.signal?.removeEventListener('abort', onAbort)
  }
  const finish = (status: 'locked' | 'error' | 'aborted') => {
    if (settled) return
    settled = true
    cleanup()
    resolveStatus(status)
  }
  const onChange = () => finish(context.isActive() ? 'locked' : 'error')
  const onError = () => finish('error')
  const onAbort = () => finish('aborted')

  context.events.addEventListener('pointerlockchange', onChange)
  context.events.addEventListener('pointerlockerror', onError)
  context.signal?.addEventListener('abort', onAbort, { once: true })
  if (context.signal?.aborted) finish('aborted')

  return { promise, dispose: () => finish('aborted') }
}

function isPromiseLike(value: Promise<void> | void): value is Promise<void> {
  return value !== undefined && typeof value.then === 'function'
}

function settlePointerLockPromise(
  promise: Promise<void>,
  signal?: AbortSignal,
): Promise<'resolved' | 'rejected' | 'aborted'> {
  return new Promise(resolve => {
    let settled = false
    const finish = (status: 'resolved' | 'rejected' | 'aborted') => {
      if (settled) return
      settled = true
      signal?.removeEventListener('abort', onAbort)
      resolve(status)
    }
    const onAbort = () => finish('aborted')
    signal?.addEventListener('abort', onAbort, { once: true })
    if (signal?.aborted) finish('aborted')
    void promise.then(() => finish('resolved'), () => finish('rejected'))
  })
}

function inactivePointerLock(raw_mouse_requested: boolean): PointerLockResult {
  return {
    pointer_lock_active: false,
    raw_mouse_requested,
    raw_mouse_confirmed: false,
    mouse_mode: null,
  }
}

export function shouldPreventStageContextMenu(suite: SuiteId, insideStage: boolean): boolean {
  return suite === 'pc-moba' && insideStage
}
