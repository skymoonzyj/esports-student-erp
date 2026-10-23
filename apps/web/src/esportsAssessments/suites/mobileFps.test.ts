import { describe, expect, it } from 'vitest'

import { controlSettingsSchema } from '../contracts'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { activeMobileFpsControls, mobileFpsFactory } from './mobileFps'

const viewport = { width: 844, height: 390 }

describe('mobile FPS suite factory', () => {
  it.each([
    ['precision', 'aim', 5],
    ['tracking', 'tracking', 6],
    ['switching', 'aim', 6],
    ['move-fire', 'move_fire', 6],
  ] as const)('creates registered mobile %s with its mobile target diameter', (testId, rawKind, diameter) => {
    const game = mobileFpsFactory.create(
      fixtureConfig('mobile-fps', testId), 123, fixtureSettings('mobile-fps'), viewport,
    )
    const scene = game.scene()
    if (scene.kind !== 'fps') throw new Error('wrong scene')
    expect(scene.targets[0].angular_diameter_deg).toBe(diameter)
    expect(game.finish().kind).toBe(rawKind)
  })

  it('rejects a PC suite instead of silently using mobile assembly', () => {
    expect(() => mobileFpsFactory.create(
      fixtureConfig('pc-fps', 'precision'), 123, fixtureSettings('pc-fps'), viewport,
    )).toThrow('mobile-fps')
  })

  it('filters the immutable stored controls for the current test without reordering them', () => {
    const base = fixtureSettings('mobile-fps')
    const reordered = controlSettingsSchema.parse({
      ...base,
      controls: [...base.controls].reverse(),
    })
    const before = structuredClone(reordered)

    expect(activeMobileFpsControls(fixtureConfig('mobile-fps', 'precision'), reordered)
      .map(control => control.id)).toEqual(['fire'])
    expect(activeMobileFpsControls(fixtureConfig('mobile-fps', 'move-fire'), reordered)
      .map(control => control.id)).toEqual(['fire', 'move'])
    expect(reordered).toEqual(before)
  })
})
