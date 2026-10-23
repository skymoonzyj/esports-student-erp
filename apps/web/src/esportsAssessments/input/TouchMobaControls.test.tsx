import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { fixtureSettings } from '../testSupport/fixtures'
import { validateLayout } from '../settings/layout'
import { TouchMobaControls } from './TouchMobaControls'

it('renders only a pointer-transparent visual overlay using frozen relative centers and CSS sizes', () => {
  const pointer = vi.fn()
  const controls = fixtureSettings('mobile-moba').controls
  const before = structuredClone(controls)
  const view = render(<div onPointerDown={pointer}><TouchMobaControls controls={controls} /></div>)
  expect(screen.queryByRole('button')).toBeNull()
  expect(screen.getByText('移动')).toHaveStyle({ left: '15%', top: '75%', width: '120px', height: '120px', pointerEvents: 'none' })
  expect(screen.getByText('攻击')).toHaveStyle({ left: '85%', top: '75%', width: '64px', height: '64px' })
  expect(screen.getByText('技能')).toHaveStyle({ left: '70%', top: '65%' })
  const overlay = screen.getByText('移动').parentElement!
  expect(overlay).toHaveAttribute('aria-hidden', 'true')
  expect(overlay).toHaveStyle({ pointerEvents: 'none' })
  fireEvent.pointerDown(screen.getByText('技能'))
  expect(pointer).toHaveBeenCalledTimes(1)
  view.rerender(<TouchMobaControls controls={controls} disabled />)
  expect(screen.getByText('技能').parentElement).toHaveStyle({ opacity: '0.45', pointerEvents: 'none' })
  expect(controls).toEqual(before)
})

it('keeps the default mobile controls clear of the map and honors shared layout rejection', () => {
  const bounds = { x: 0, y: 0, width: 1000, height: 562.5 }
  const safety = { usable: bounds, reserved: [{ x: 820, y: 0, width: 180, height: 110 }] }
  expect(validateLayout(fixtureSettings('mobile-moba').controls, bounds, safety)).toEqual([])
  expect(validateLayout([{ id: 'skill', kind: 'button', x: .9, y: .1, width: 64, height: 64 }], bounds, safety).length).toBeGreaterThan(0)
})
