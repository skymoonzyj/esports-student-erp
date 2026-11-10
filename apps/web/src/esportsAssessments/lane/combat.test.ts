import { describe, expect, it } from 'vitest'
import { resolveDamage, type DamageTarget } from './combat'

const creep = (health: number): DamageTarget => ({ id: 'enemy-0-0', enemyIndex: 0, health, hitAt: null, diedAt: null, windup: 100 })
describe('stable lane damage attribution', () => {
  it('settles a lethal hero hit before same-tick creep overkill exactly once, independent of queue arrival', () => {
    const target = creep(54)
    const deaths = resolveDamage([
      { sourceOrder: 3, order: 1, target, damage: 24, byPlayer: false },
      { sourceOrder: 0, order: 2, target, damage: 54, byPlayer: true },
    ], 1000)
    expect(target).toMatchObject({ health: 0, hitAt: 1000, diedAt: 1000, windup: null })
    expect(deaths).toEqual([{ id: 'enemy-0-0', unit_id: 0, by_player: true }])
    expect(resolveDamage([{ sourceOrder: 0, order: 3, target, damage: 54, byPlayer: true }], 1020)).toEqual([])
  })

  it('records creep ownership when hero damage is nonlethal, with a hand-written 70 + 18 + 24 timeline', () => {
    const target = creep(100)
    expect(resolveDamage([{ sourceOrder: 0, order: 0, target, damage: 70, byPlayer: true }], 300)).toEqual([])
    expect(target.health).toBe(30)
    expect(resolveDamage([{ sourceOrder: 1, order: 0, target, damage: 18, byPlayer: false }], 320)).toEqual([])
    expect(target.health).toBe(12)
    expect(resolveDamage([{ sourceOrder: 4, order: 1, target, damage: 24, byPlayer: false }], 800)).toEqual([{ id: 'enemy-0-0', unit_id: 0, by_player: false }])
    expect(target.health).toBe(0)
    expect(target.diedAt).toBe(800)
  })
})
