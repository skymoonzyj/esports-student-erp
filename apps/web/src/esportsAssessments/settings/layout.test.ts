import { describe, expect, it } from 'vitest'

import type { ControlRect } from '../contracts'
import {
  controlToRect,
  defaultControlSettings,
  normalizeSensitivity,
  validateLayout,
} from './layout'

const bounds = { x: 0, y: 0, width: 640, height: 360 }
const wholeStage = { usable: bounds, reserved: [] }

function control(overrides: Partial<ControlRect> = {}): ControlRect {
  return {
    id: 'fire',
    kind: 'button',
    x: 0.85,
    y: 0.75,
    width: 64,
    height: 64,
    ...overrides,
  }
}

describe('control layout', () => {
  it('converts stored center ratios and CSS diameters to a stage rectangle', () => {
    expect(controlToRect(control(), bounds)).toEqual({
      x: 512,
      y: 238,
      width: 64,
      height: 64,
    })
  })

  it('rejects controls outside the usable safe area on either landscape side', () => {
    const safety = {
      usable: { x: 24, y: 0, width: 592, height: 360 },
      reserved: [],
    }

    expect(validateLayout([control({ x: 0.04 })], bounds, safety)).not.toEqual([])
    expect(validateLayout([control({ x: 0.96 })], bounds, safety)).not.toEqual([])
  })

  it('rejects overlap between controls and with map or exit regions', () => {
    const controls = [
      control({ id: 'attack', x: 0.8 }),
      control({ id: 'skill', x: 0.82 }),
    ]
    expect(validateLayout(controls, bounds, wholeStage)).toEqual(
      expect.arrayContaining([expect.stringContaining('重叠')]),
    )

    const reserved = [{ x: 500, y: 220, width: 100, height: 100 }]
    expect(validateLayout([control()], bounds, { ...wholeStage, reserved })).toEqual(
      expect.arrayContaining([expect.stringContaining('保留区域')]),
    )
  })

  it('rejects server-invalid button and joystick diameters', () => {
    expect(validateLayout([control({ width: 47, height: 47 })], bounds, wholeStage)).not.toEqual([])
    expect(validateLayout([
      control({ id: 'move', kind: 'joystick', width: 161, height: 161 }),
    ], bounds, wholeStage)).not.toEqual([])
  })

  it('builds server-compatible defaults for each control family', () => {
    expect(defaultControlSettings('pc-fps')).toMatchObject({
      sensitivity: 1,
      input_kind: 'mouse',
      mouse_mode: 'relative',
      controls: [],
    })
    expect(defaultControlSettings('mobile-fps').controls.map(item => item.id)).toEqual(['move', 'fire'])
    expect(defaultControlSettings('mobile-moba').controls.map(item => item.id)).toEqual(['move', 'attack', 'skill'])
  })

  it('normalizes sensitivity to the 0.5-2 range in 0.05 steps', () => {
    expect(normalizeSensitivity(0.49)).toBe(0.5)
    expect(normalizeSensitivity(1.024)).toBe(1)
    expect(normalizeSensitivity(1.026)).toBe(1.05)
    expect(normalizeSensitivity(2.01)).toBe(2)
  })
})
