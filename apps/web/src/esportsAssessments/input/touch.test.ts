import { describe, expect, it, vi } from 'vitest'

import type { ControlRect } from '../contracts'
import type { InputAction } from '../engineRuntime/types'
import { TouchInputRouter, type TouchLayout } from './touch'

const controls: ControlRect[] = [
  { id: 'move', kind: 'joystick', x: 60 / 640, y: 240 / 360, width: 96, height: 96 },
  { id: 'fire', kind: 'button', x: 590 / 640, y: 100 / 360, width: 48, height: 48 },
]
const layout: TouchLayout = {
  bounds: { x: 0, y: 0, width: 640, height: 360 },
  logical_size: { width: 1000, height: 562.5 },
  sensitivity: 1,
  controls,
  aim_area: { x: 320, y: 0, width: 320, height: 360 },
}

describe('TouchInputRouter', () => {
  it('keeps independent fingers and clears cancelled controls', () => {
    const emit = vi.fn<(action: InputAction) => void>()
    const router = new TouchInputRouter(layout, emit)

    router.down({ pointerId: 1, x: 60, y: 240, at_ms: 0 })
    router.down({ pointerId: 2, x: 400, y: 160, at_ms: 0, isPrimary: false })
    router.down({ pointerId: 3, x: 590, y: 100, at_ms: 0, isPrimary: false })

    expect(router.activeCount).toBe(3)
    router.cancel(25)
    expect(router.activeCount).toBe(0)
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ kind: 'fire', pressed: false }))
    expect(emit).toHaveBeenCalledWith(expect.objectContaining({ kind: 'move', x: 0, y: 0 }))
  })

  it('emits sensitivity-adjusted angular look deltas', () => {
    const actions: InputAction[] = []
    const router = new TouchInputRouter({ ...layout, sensitivity: 2 }, action => actions.push(action))
    router.down({ pointerId: 7, x: 400, y: 160, at_ms: 10 })

    router.move({ pointerId: 7, x: 464, y: 192, at_ms: 20 })

    expect(actions).toContainEqual({ kind: 'look', at_ms: 20, dx: 36, dy: 18 })
  })

  it('converts point and skill input to logical coordinates', () => {
    const actions: InputAction[] = []
    const mobaLayout: TouchLayout = {
      ...layout,
      controls: [{ id: 'skill', kind: 'button', x: 0.75, y: 0.5, width: 64, height: 64 }],
      aim_area: undefined,
      get_skill_origin: () => ({ x: 500, y: 281.25 }),
    }
    const router = new TouchInputRouter(mobaLayout, action => actions.push(action))

    router.down({ pointerId: 1, x: 320, y: 180, at_ms: 1 })
    router.down({ pointerId: 2, x: 480, y: 180, at_ms: 2 })
    router.up({ pointerId: 2, x: 640, y: 360, at_ms: 3 })

    expect(actions).toContainEqual({ kind: 'point', at_ms: 1, x: 500, y: 281.25, button: 0 })
    expect(actions).toContainEqual({ kind: 'skill', at_ms: 3, x: 750, y: 562.5, release: true })
  })

  it('emits explicit skill cancellation without releasing a projectile', () => {
    const actions: InputAction[] = []
    const router = new TouchInputRouter({
      ...layout,
      controls: [{ id: 'skill', kind: 'button', x: 0.75, y: 0.5, width: 64, height: 64 }],
      aim_area: undefined,
      get_skill_origin: () => ({ x: 500, y: 281.25 }),
    }, action => actions.push(action))
    router.down({ pointerId: 2, x: 480, y: 180, at_ms: 2 })

    router.cancel({ pointerId: 2, x: 500, y: 180, at_ms: 4 })

    expect(actions.at(-1)).toEqual({
      kind: 'skill', at_ms: 4, x: 531.25, y: 281.25, release: false, cancel: true,
    })
  })

  it('does not continue a start gesture into the formal input window', () => {
    const actions: InputAction[] = []
    const router = new TouchInputRouter(layout, action => actions.push(action))
    router.down({ pointerId: 3, x: 590, y: 100, at_ms: -1 })
    router.down({ pointerId: 3, x: 590, y: 100, at_ms: 0 })
    router.cancel(1)
    router.up({ pointerId: 3, x: 590, y: 100, at_ms: 1 })
    router.down({ pointerId: 4, x: 590, y: 100, at_ms: 60_000 })

    expect(actions.filter(action => action.kind === 'fire')).toEqual([
      { kind: 'fire', at_ms: 0, pressed: true },
      { kind: 'fire', at_ms: 1, pressed: false },
    ])
    expect(router.activeCount).toBe(0)
  })

  it('ignores pointer downs outside the game stage', () => {
    const actions: InputAction[] = []
    const router = new TouchInputRouter({ ...layout, aim_area: undefined }, action => actions.push(action))

    router.down({ pointerId: 9, x: 641, y: 180, at_ms: 1 })

    expect(actions).toEqual([])
    expect(router.activeCount).toBe(0)
  })

  it('owns pointer capture for every assigned pointer', () => {
    const capture = vi.fn()
    const release = vi.fn()
    const router = new TouchInputRouter(layout, () => undefined, { capture, release })

    router.down({ pointerId: 11, x: 60, y: 240, at_ms: 1 })
    router.up({ pointerId: 11, x: 60, y: 240, at_ms: 2 })

    expect(capture).toHaveBeenCalledWith(11)
    expect(release).toHaveBeenCalledWith(11)
  })

  it('maps skill drags in all directions from the current player origin', () => {
    const actions: InputAction[] = []
    const router = new TouchInputRouter({
      ...layout,
      controls: [{ id: 'skill', kind: 'button', x: 0.75, y: 0.5, width: 64, height: 64 }],
      aim_area: undefined,
      get_skill_origin: () => ({ x: 500, y: 281.25 }),
    }, action => actions.push(action))
    router.down({ pointerId: 12, x: 480, y: 180, at_ms: 1 })

    router.move({ pointerId: 12, x: 416, y: 180, at_ms: 2 })
    router.move({ pointerId: 12, x: 544, y: 180, at_ms: 3 })
    router.move({ pointerId: 12, x: 480, y: 116, at_ms: 4 })
    router.move({ pointerId: 12, x: 480, y: 244, at_ms: 5 })

    expect(actions.filter(action => action.kind === 'skill').map(action => ({ x: action.x, y: action.y })))
      .toEqual([
        { x: 500, y: 281.25 },
        { x: 400, y: 281.25 },
        { x: 600, y: 281.25 },
        { x: 500, y: 181.25 },
        { x: 500, y: 381.25 },
      ])
  })

  it('bulk cancellation uses the current elapsed time instead of a stale pointer time', () => {
    const actions: InputAction[] = []
    const router = new TouchInputRouter(layout, action => actions.push(action))
    router.down({ pointerId: 13, x: 590, y: 100, at_ms: 10 })

    router.cancel(250)

    expect(actions.at(-1)).toEqual({ kind: 'fire', at_ms: 250, pressed: false })
  })
})
