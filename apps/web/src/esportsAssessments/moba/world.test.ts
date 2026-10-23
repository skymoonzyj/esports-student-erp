import { describe, expect, it } from 'vitest'
import { advanceAttack, cancelAttack, clampPosition, moveToward } from './world'
import { createDodgeEngine } from './dodge'
import { createRhythmEngine } from './rhythm'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'

describe('MOBA movement and attacks', () => {
  it('moves in seconds without overshooting and clamps the entire player inside the world', () => {
    const moved = moveToward({ x: 0, y: 0 }, { x: 3, y: 4 }, 2, 1)
    expect(moved.x).toBeCloseTo(1.2, 10)
    expect(moved.y).toBeCloseTo(1.6, 10)
    expect(moveToward({ x: 0, y: 0 }, { x: 3, y: 4 }, 20, 1)).toEqual({ x: 3, y: 4 })
    expect(clampPosition({ x: -1, y: 800 }, 12, { width: 1000, height: 562.5 })).toEqual({ x: 12, y: 550.5 })
  })
  it('resolves once at the exact windup boundary and never resolves cancelled or idle attacks', () => {
    const attack = { started_at_ms: 0, cancelled: false, resolved: false }
    expect(advanceAttack(attack, 249.999, 250).landed).toBe(false)
    const first = advanceAttack(attack, 250, 250)
    expect(first.landed).toBe(true)
    expect(advanceAttack(first.state, 251, 250).landed).toBe(false)
    expect(advanceAttack({ ...attack, cancelled: true }, 300, 250).landed).toBe(false)
    expect(advanceAttack({ ...attack, started_at_ms: null }, 300, 250).landed).toBe(false)
    expect(attack.resolved).toBe(false)
  })
  it('cancels only strictly before completion, resolving boundary movement first', () => {
    const attack = { started_at_ms: 0, cancelled: false, resolved: false }
    expect(cancelAttack(attack, 249, 250)).toEqual({ state: { ...attack, cancelled: true }, landed: false })
    expect(cancelAttack(attack, 250, 250)).toEqual({ state: { ...attack, resolved: true }, landed: true })
  })
  it('keeps the entire non-map world playable without an unrelated minimap overlay', () => {
    for (const game of [createDodgeEngine(fixtureConfig('pc-moba', 'dodge'), 123, fixtureSettings('pc-moba')), createRhythmEngine(fixtureConfig('pc-moba', 'orb-walk'), 123, fixtureSettings('pc-moba'))]) {
      const scene = game.scene()
      if (scene.kind !== 'moba') throw new Error('scene')
      expect(scene.logical_size).toEqual({ width: 1000, height: 562.5 })
      expect(scene.minimap.visible).toBe(false)
    }
  })
})
