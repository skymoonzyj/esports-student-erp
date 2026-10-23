import { describe, expect, it } from 'vitest'

import {
  clientToLogical,
  containsRect,
  overlaps,
  sameStageGeometry,
} from './geometry'

describe('runtime geometry', () => {
  it('maps CSS stage coordinates to fixed logical coordinates', () => {
    expect(clientToLogical(
      { x: 410, y: 245 },
      { x: 10, y: 20, width: 800, height: 450 },
      { width: 1000, height: 562.5 },
    )).toEqual({ x: 500, y: 281.25 })
  })

  it('does not treat scrolling alone as a stage geometry change', () => {
    expect(sameStageGeometry(
      { x: 0, y: 100, width: 800, height: 450 },
      { x: 0, y: 20, width: 800, height: 450 },
    )).toBe(true)
  })

  it('detects actual stage width or height changes', () => {
    const original = { x: 0, y: 0, width: 800, height: 450 }

    expect(sameStageGeometry(original, { ...original, width: 801 })).toBe(false)
    expect(sameStageGeometry(original, { ...original, height: 451 })).toBe(false)
  })

  it('uses non-overlapping edges and inclusive containment', () => {
    const bounds = { x: 0, y: 0, width: 100, height: 100 }
    const left = { x: 0, y: 0, width: 50, height: 100 }
    const right = { x: 50, y: 0, width: 50, height: 100 }

    expect(containsRect(bounds, left)).toBe(true)
    expect(containsRect(bounds, right)).toBe(true)
    expect(overlaps(left, right)).toBe(false)
  })
})
