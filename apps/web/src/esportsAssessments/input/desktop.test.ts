import { describe, expect, it, vi } from 'vitest'

import type { InputAction } from '../engineRuntime/types'
import {
  DesktopInputRouter,
  requestDesktopPointerLock,
  shouldPreventStageContextMenu,
} from './desktop'
import type { PointerLockEventSource } from './desktop'

class FakePointerLockEvents implements PointerLockEventSource {
  private readonly listeners = {
    pointerlockchange: new Set<() => void>(),
    pointerlockerror: new Set<() => void>(),
  }

  addEventListener(type: 'pointerlockchange' | 'pointerlockerror', listener: () => void): void {
    this.listeners[type].add(listener)
  }

  removeEventListener(type: 'pointerlockchange' | 'pointerlockerror', listener: () => void): void {
    this.listeners[type].delete(listener)
  }

  dispatch(type: 'pointerlockchange' | 'pointerlockerror'): void {
    for (const listener of [...this.listeners[type]]) listener()
  }

  get listenerCount(): number {
    return this.listeners.pointerlockchange.size + this.listeners.pointerlockerror.size
  }
}

describe('DesktopInputRouter', () => {
  it('keeps lane point input live through five minutes without extending legacy input', () => {
    const actions: InputAction[] = []
    const router = new DesktopInputRouter({ suite_id: 'pc-moba', sensitivity: 1, duration_ms: 300000,
      bounds: { x: 0, y: 0, width: 1000, height: 562.5 }, logical_size: { width: 1000, height: 562.5 } }, action => actions.push(action))
    for (const at_ms of [60000, 299999, 300000]) router.down({ pointerId: 1, button: 0, x: 400, y: 300, at_ms })
    expect(actions.map(action => action.at_ms)).toEqual([60000, 299999])
  })
  it('converts mouse movement to sensitivity-adjusted angular deltas', () => {
    const actions: InputAction[] = []
    const router = new DesktopInputRouter({
      suite_id: 'pc-fps',
      sensitivity: 1.5,
      bounds: { x: 0, y: 0, width: 800, height: 450 },
      logical_size: { width: 1000, height: 562.5 },
    }, action => actions.push(action))

    router.move({ movementX: 10, movementY: -4, at_ms: 20 })

    expect(actions).toHaveLength(1)
    expect(actions[0]).toMatchObject({ kind: 'look', at_ms: 20, dx: 1.5 })
    expect(actions[0]?.kind === 'look' && actions[0].dy).toBeCloseTo(-0.6, 10)
  })

  it('maps PC MOBA mouse buttons to logical points', () => {
    const actions: InputAction[] = []
    const router = new DesktopInputRouter({
      suite_id: 'pc-moba',
      sensitivity: 1,
      bounds: { x: 10, y: 20, width: 800, height: 450 },
      logical_size: { width: 1000, height: 562.5 },
    }, action => actions.push(action))

    router.down({ pointerId: 1, button: 2, x: 410, y: 245, at_ms: 10 })

    expect(actions).toEqual([{ kind: 'point', at_ms: 10, x: 500, y: 281.25, button: 2 }])
  })

  it('accepts locked FPS fire when the frozen cursor coordinate is outside the stage', () => {
    const actions: InputAction[] = []
    const router = new DesktopInputRouter({
      suite_id: 'pc-fps',
      sensitivity: 1,
      bounds: { x: 100, y: 100, width: 800, height: 450 },
      logical_size: { width: 1000, height: 562.5 },
    }, action => actions.push(action))

    router.down({ pointerId: 1, button: 0, x: 50, y: 50, at_ms: 10 })

    expect(actions).toEqual([{ kind: 'fire', at_ms: 10, pressed: true }])
  })

  it('ignores point input outside the PC MOBA stage', () => {
    const actions: InputAction[] = []
    const router = new DesktopInputRouter({
      suite_id: 'pc-moba',
      sensitivity: 1,
      bounds: { x: 10, y: 20, width: 800, height: 450 },
      logical_size: { width: 1000, height: 562.5 },
    }, action => actions.push(action))

    router.down({ pointerId: 1, button: 2, x: 9, y: 245, at_ms: 10 })

    expect(actions).toEqual([])
  })

  it('clears held fire and ignores inputs at 60000ms', () => {
    const actions: InputAction[] = []
    const router = new DesktopInputRouter({
      suite_id: 'pc-fps',
      sensitivity: 1,
      bounds: { x: 0, y: 0, width: 800, height: 450 },
      logical_size: { width: 1000, height: 562.5 },
    }, action => actions.push(action))
    router.down({ pointerId: 1, button: 0, x: 0, y: 0, at_ms: 0 })

    router.cancel(1)
    router.down({ pointerId: 2, button: 0, x: 0, y: 0, at_ms: 60_000 })

    expect(actions).toEqual([
      { kind: 'fire', at_ms: 0, pressed: true },
      { kind: 'fire', at_ms: 1, pressed: false },
    ])
  })
})

describe('desktop browser capabilities', () => {
  it('falls back from requested raw input while preserving requested and confirmed separately', async () => {
    const events = new FakePointerLockEvents()
    const requestPointerLock = vi.fn()
      .mockRejectedValueOnce(new Error('raw unavailable'))
      .mockResolvedValueOnce(undefined)

    const result = await requestDesktopPointerLock(
      { requestPointerLock },
      true,
      { events, isActive: () => true },
    )

    expect(result).toEqual({
      pointer_lock_active: true,
      raw_mouse_requested: true,
      raw_mouse_confirmed: false,
      mouse_mode: 'relative',
    })
  })

  it('reports confirmed raw input only after a successful active lock', async () => {
    const result = await requestDesktopPointerLock(
      { requestPointerLock: async () => undefined },
      true,
      { events: new FakePointerLockEvents(), isActive: () => true },
    )

    expect(result.raw_mouse_confirmed).toBe(true)
    expect(result.pointer_lock_active).toBe(true)
    expect(result.mouse_mode).toBe('raw')
  })

  it('awaits asynchronous lock change for a legacy void request', async () => {
    const events = new FakePointerLockEvents()
    let active = false
    const requestPointerLock = vi.fn(() => {
      queueMicrotask(() => {
        active = true
        events.dispatch('pointerlockchange')
      })
    })

    const result = await requestDesktopPointerLock(
      { requestPointerLock },
      false,
      { events, isActive: () => active },
    )

    expect(result).toEqual({
      pointer_lock_active: true,
      raw_mouse_requested: false,
      raw_mouse_confirmed: false,
      mouse_mode: 'relative',
    })
    expect(events.listenerCount).toBe(0)
  })

  it('returns inactive after an asynchronous legacy Pointer Lock error', async () => {
    const events = new FakePointerLockEvents()
    const requestPointerLock = vi.fn(() => {
      queueMicrotask(() => events.dispatch('pointerlockerror'))
    })

    const result = await requestDesktopPointerLock(
      { requestPointerLock },
      false,
      { events, isActive: () => false },
    )

    expect(result).toEqual({
      pointer_lock_active: false,
      raw_mouse_requested: false,
      raw_mouse_confirmed: false,
      mouse_mode: null,
    })
    expect(events.listenerCount).toBe(0)
  })

  it('never confirms raw input when a legacy void API ignores options', async () => {
    const events = new FakePointerLockEvents()
    let active = false
    const requestPointerLock = vi.fn(() => {
      queueMicrotask(() => {
        active = true
        events.dispatch('pointerlockchange')
      })
    })

    const result = await requestDesktopPointerLock(
      { requestPointerLock },
      true,
      { events, isActive: () => active },
    )

    expect(requestPointerLock).toHaveBeenCalledWith({ unadjustedMovement: true })
    expect(result).toMatchObject({
      pointer_lock_active: true,
      raw_mouse_requested: true,
      raw_mouse_confirmed: false,
      mouse_mode: 'relative',
    })
  })

  it('requests synchronously and cleans legacy listeners when cancelled', async () => {
    const events = new FakePointerLockEvents()
    const controller = new AbortController()
    const requestPointerLock = vi.fn(() => undefined)

    const pending = requestDesktopPointerLock(
      { requestPointerLock },
      false,
      { events, isActive: () => false, signal: controller.signal },
    )
    expect(requestPointerLock).toHaveBeenCalledTimes(1)
    expect(events.listenerCount).toBe(2)

    controller.abort()

    await expect(pending).resolves.toEqual({
      pointer_lock_active: false,
      raw_mouse_requested: false,
      raw_mouse_confirmed: false,
      mouse_mode: null,
    })
    expect(events.listenerCount).toBe(0)
  })

  it('prevents the context menu only inside the PC MOBA stage', () => {
    expect(shouldPreventStageContextMenu('pc-moba', true)).toBe(true)
    expect(shouldPreventStageContextMenu('pc-moba', false)).toBe(false)
    expect(shouldPreventStageContextMenu('pc-fps', true)).toBe(false)
  })
})
