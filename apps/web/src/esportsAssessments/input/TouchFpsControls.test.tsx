import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { InputAction } from '../engineRuntime/types'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { activeMobileFpsControls } from '../suites/mobileFps'
import { TouchFpsControls } from './TouchFpsControls'
import { TouchInputRouter } from './touch'

const bounds = { x: 0, y: 0, width: 640, height: 360 }

describe('mobile FPS touch controls', () => {
  it('renders the same reordered frozen controls as a non-intercepting visual overlay', () => {
    const settings = fixtureSettings('mobile-fps')
    const controls = activeMobileFpsControls(
      fixtureConfig('mobile-fps', 'move-fire'),
      { ...settings, controls: [...settings.controls].reverse() },
    )
    const view = render(<TouchFpsControls controls={controls} />)
    const overlay = view.container.firstElementChild as HTMLElement
    const rendered = [...overlay.querySelectorAll<HTMLElement>('[data-control-id]')]

    expect(overlay).toHaveStyle({ pointerEvents: 'none' })
    expect(overlay).toHaveAttribute('aria-hidden', 'true')
    expect(rendered.map(element => element.dataset.controlId)).toEqual(['fire', 'move'])
    expect(rendered[0]).toHaveStyle({ left: '85%', top: '75%', width: '64px', height: '64px' })
    expect(rendered[1]).toHaveStyle({ left: '15%', top: '75%', width: '120px', height: '120px' })
  })

  it('keeps move, aim and fire pointers independent when one finger lifts', () => {
    const controls = activeMobileFpsControls(
      fixtureConfig('mobile-fps', 'move-fire'), fixtureSettings('mobile-fps'),
    )
    const actions: InputAction[] = []
    const router = new TouchInputRouter({
      bounds,
      logical_size: { width: 1000, height: 562.5 },
      sensitivity: 1,
      controls,
      aim_area: { x: 320, y: 0, width: 320, height: 360 },
    }, action => actions.push(action))

    router.down({ pointerId: 1, x: 96, y: 270, at_ms: 0 })
    router.down({ pointerId: 2, x: 400, y: 100, at_ms: 0, isPrimary: false })
    router.down({ pointerId: 3, x: 544, y: 270, at_ms: 0, isPrimary: false })
    router.move({ pointerId: 1, x: 144, y: 270, at_ms: 10 })
    router.move({ pointerId: 2, x: 464, y: 100, at_ms: 10, isPrimary: false })
    router.up({ pointerId: 3, x: 544, y: 270, at_ms: 10, isPrimary: false })
    router.move({ pointerId: 1, x: 96, y: 222, at_ms: 20 })

    expect(router.activeCount).toBe(2)
    expect(actions).toContainEqual({ kind: 'fire', at_ms: 0, pressed: true })
    expect(actions).toContainEqual({ kind: 'fire', at_ms: 10, pressed: false })
    expect(actions).toContainEqual({ kind: 'look', at_ms: 10, dx: 18, dy: 0 })
    expect(actions.at(-1)).toEqual({ kind: 'move', at_ms: 20, x: 0, y: -.8 })

    router.cancel(25)
    expect(router.activeCount).toBe(0)
    router.down({ pointerId: 4, x: 544, y: 270, at_ms: 26 })
    expect(router.activeCount).toBe(1)
    expect(actions.at(-1)).toEqual({ kind: 'fire', at_ms: 26, pressed: true })
  })

  it('does not expose or route a move control outside move-fire', () => {
    const controls = activeMobileFpsControls(
      fixtureConfig('mobile-fps', 'tracking'), fixtureSettings('mobile-fps'),
    )
    const actions: InputAction[] = []
    const router = new TouchInputRouter({
      bounds,
      logical_size: { width: 1000, height: 562.5 },
      sensitivity: 1,
      controls,
      aim_area: bounds,
    }, action => actions.push(action))
    const view = render(<TouchFpsControls controls={controls} />)

    router.down({ pointerId: 1, x: 96, y: 270, at_ms: 0 })
    router.move({ pointerId: 1, x: 144, y: 270, at_ms: 10 })

    expect(view.container.querySelector('[data-control-id="move"]')).not.toBeInTheDocument()
    expect(actions).toContainEqual({ kind: 'look', at_ms: 10, dx: 13.5, dy: 0 })
    expect(actions.some(action => action.kind === 'move')).toBe(false)
  })
})
