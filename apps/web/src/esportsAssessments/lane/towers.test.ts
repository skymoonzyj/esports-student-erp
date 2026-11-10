import { describe, expect, it } from 'vitest'
import { LaneSimulation } from './simulation'
import { fixtureMobaV5Config } from '../testSupport/fixturesMobaV5'
import { validateRaw } from '../contracts'
import type { CombatStats } from './constants'

const params = (() => {
  const value = fixtureMobaV5Config().params
  if (value.kind !== 'lane_last_hit_towers') throw new Error('Expected v5 fixture')
  return value
})()

// Scenario construction stays entirely in tests. Exercise only public input,
// advanceTo, snapshot and raw after arranging a small deterministic battlefield.
interface TestUnit {
  id: string; order: number; enemyIndex: number | null; team: 'ally' | 'enemy'; kind: 'hero' | 'melee' | 'ranged'
  position: { x: number; y: number }; stats: CombatStats; health: number; target: string | null
  windup: number | null; readyAt: number; diedAt: number | null
}
interface TestMissile {
  id: string; order: number; sourceOrder: number; team: 'ally' | 'enemy'; targetId: string
  position: { x: number; y: number }; speed: number; damage: number; byPlayer: boolean
  kind?: 'tower'; targetLife?: number
}
function arena(mode: 'melee' | 'ranged' = 'melee') {
  const game = new LaneSimulation(mode, 1, params)
  const state = game as unknown as { units: TestUnit[]; missiles: TestMissile[]; heroLife: number }
  const hero = state.units[0]
  const minion = state.units.find(unit => unit.id === 'ally-0-0')!
  minion.stats = { ...minion.stats, speed: 0 }
  state.units = [hero, minion]
  return { game, state, hero, minion }
}

describe('tower lane simulation', () => {
  it('does not add tower/life properties to the historical v4 public state or raw', () => {
    const game = new LaneSimulation('melee', 1)
    expect(game.snapshot()).not.toHaveProperty('towers')
    expect(game.snapshot()).not.toHaveProperty('hero_status')
    expect(game.raw()).toEqual({ kind: 'lane_last_hit', deaths: [], attacks: 0, cancelled_attacks: 0 })
    game.advanceTo(300000)
    expect(game.snapshot().wave_count).toBe(10)
    expect(game.raw().deaths.every(death => death.unit_id < 50)).toBe(true)
    expect(Object.keys(game.raw()).sort()).toEqual(['attacks', 'cancelled_attacks', 'deaths', 'kind'])
    expect(game.snapshot().units[0].health).toBe(1)
  })
  it('spawns 75 unique enemies at 0 through 280000, never at the excluded endpoint', () => {
    const game = new LaneSimulation('melee', 1, params)
    const ids = new Set<string>()
    for (let wave = 0; wave < 15; wave++) {
      game.advanceTo(wave * 20000)
      const fresh = game.snapshot().units.filter(unit => unit.id.startsWith(`enemy-${wave}-`))
      expect(fresh.map(unit => unit.kind)).toEqual(['melee', 'melee', 'melee', 'ranged', 'ranged'])
      fresh.forEach(unit => ids.add(unit.id))
    }
    expect(ids.size).toBe(75)
    game.advanceTo(299980)
    const before = game.snapshot(), raw = game.raw()
    game.advanceTo(300000)
    expect(game.snapshot()).toEqual(before)
    expect(game.raw()).toEqual(raw)
    expect(game.snapshot().wave_count).toBe(15)
    expect(game.snapshot().wave).toBe(15)
    expect(game.raw()).toMatchObject({ kind: 'lane_last_hit_towers', hero_damage_taken: 0, hero_death_times: [] })
    expect(game.raw().deaths.every(death => !death.by_player && death.unit_id < 75)).toBe(true)
  })

  it('uses actual tower damage, dies, keeps the world advancing and ignores dead-life commands', () => {
    const game = new LaneSimulation('melee', 1, params)
    expect(game.snapshot().units[0]).toMatchObject({ position: { x: 180, y: 420 }, health: 1000 })
    game.input({ kind: 'point', at_ms: 0, x: 930, y: 520, button: 0, target_id: null })
    let deathAt = 0, previousHealth = 1000, saw180 = false
    for (let at = 20; at < 20000; at += 20) {
      game.advanceTo(at)
      const health = game.snapshot().units[0].health
      saw180 ||= previousHealth - health === 180
      previousHealth = health
      if (health === 0) { deathAt = at; break }
    }
    expect(saw180).toBe(true)
    expect(deathAt).toBeGreaterThan(0)
    expect(game.snapshot()).toMatchObject({ move_destination: null, selected_target_id: null, hero_status: { deaths: 1, health: 0, respawn_at_ms: deathAt + 5000 } })
    const world = game.snapshot().units.filter(unit => unit.kind !== 'hero')
    game.input({ kind: 'point', at_ms: deathAt + 100, x: 900, y: 100, button: 0, target_id: null })
    game.advanceTo(deathAt + 4980)
    expect(game.snapshot().units[0].health).toBe(0)
    expect(game.snapshot().units.filter(unit => unit.kind !== 'hero')).not.toEqual(world)
    game.advanceTo(deathAt + 5000)
    expect(game.snapshot().units[0]).toMatchObject({ position: { x: 180, y: 420 }, health: 1000, state: 'idle' })
    game.advanceTo(deathAt + 6000)
    expect(game.snapshot().units[0].position).toEqual({ x: 180, y: 420 })
    expect(game.raw()).toMatchObject({ hero_damage_taken: 1000, hero_death_times: [deathAt] })
    expect(game.snapshot().towers).toHaveLength(4)
  })

  it('prefers a farther enemy minion to hero, locks it, and attacks every 1200ms after 300ms windup', () => {
    const { game, state, hero, minion } = arena()
    hero.position = { x: 710, y: 162.5 }
    minion.position = { x: 620, y: 162.5 }
    game.advanceTo(20)
    expect(game.snapshot().towers?.[2]).toMatchObject({ target_id: minion.id, state: 'windup' })
    const closer = { ...minion, id: 'ally-0-1', order: 2, position: { x: 715, y: 162.5 }, stats: { ...minion.stats } }
    state.units.push(closer)
    game.advanceTo(300)
    expect(game.snapshot().projectiles).toHaveLength(0)
    game.advanceTo(320)
    expect(game.snapshot().towers?.[2]).toMatchObject({ target_id: minion.id, state: 'attacking' })
    expect(game.snapshot().projectiles).toHaveLength(1)
    expect(game.snapshot().projectiles[0]).toMatchObject({ team: 'enemy', kind: 'tower' })
    game.advanceTo(480)
    expect(minion.health).toBe(220)
    expect(hero.health).toBe(1000)
    game.advanceTo(1500)
    expect(game.snapshot().projectiles).toHaveLength(0)
    game.advanceTo(1520)
    expect(game.snapshot().projectiles).toHaveLength(1)
    game.advanceTo(1680)
    expect(minion.health).toBe(40)
  })

  it('cancels windup on range exit, but released missiles follow outside range', () => {
    const { game, minion } = arena()
    minion.position = { x: 620, y: 162.5 }
    game.advanceTo(300)
    minion.position = { x: 470, y: 162.5 }
    game.advanceTo(320)
    expect(game.snapshot().towers?.[2]).toMatchObject({ state: 'idle', target_id: null })
    expect(game.snapshot().projectiles).toHaveLength(0)
    minion.position = { x: 620, y: 162.5 }
    game.advanceTo(340)
    game.advanceTo(640)
    expect(game.snapshot().projectiles).toHaveLength(1)
    minion.position = { x: 470, y: 162.5 }
    game.advanceTo(1040)
    expect(minion.health).toBe(220)
  })

  it('selects the nearest minion with stable order tie break and ignores same-team units', () => {
    const { game, state, minion } = arena()
    minion.position = { x: 620, y: 162.5 }
    const nearest = { ...minion, id: 'ally-0-1', order: 2, position: { x: 670, y: 162.5 } }
    const tied = { ...minion, id: 'ally-0-2', order: 3, position: { x: 770, y: 162.5 } }
    state.units.push(tied, nearest)
    game.advanceTo(20)
    expect(game.snapshot().towers?.[2].target_id).toBe('ally-0-1')
    expect(game.snapshot().towers?.slice(0, 2).map(tower => tower.target_id)).toEqual([null, null])
  })

  it('settles simultaneous tower hits once without awarding player last hits', () => {
    const { game, state, minion } = arena()
    minion.team = 'enemy'
    minion.id = 'enemy-0-0'
    minion.enemyIndex = 0
    minion.health = 100
    minion.position = { x: 400, y: 500 }
    state.missiles = [10001, 10000].map((sourceOrder, order) => ({ id: `t-${order}`, order,
      sourceOrder, team: 'ally', targetId: minion.id, position: { ...minion.position },
      speed: 650, damage: 180, byPlayer: false, kind: 'tower' }))
    game.advanceTo(20)
    expect(game.raw().deaths).toEqual([{ unit_id: 0, at_ms: 20, by_player: false }])
    expect(game.snapshot()).toMatchObject({ last_hits: 0, enemy_deaths: 1, missed: 1 })
  })

  it('rejects old-life tower missiles even when the hero is alive at the same identity', () => {
    const { game, state, hero } = arena()
    state.heroLife = 1
    state.missiles = [{ id: 'old-life', order: 0, sourceOrder: 10002, team: 'enemy', targetId: 'hero',
      position: { ...hero.position }, speed: 650, damage: 180, byPlayer: false, kind: 'tower', targetLife: 0 }]
    game.advanceTo(20)
    expect(game.snapshot().units[0].health).toBe(1000)
    expect(game.raw()).toMatchObject({ hero_damage_taken: 0 })
    expect(game.snapshot().projectiles).toHaveLength(0)
  })

  it('keeps a released player missile eligible to last-hit after its owner dies', () => {
    const { game, state, hero, minion } = arena('ranged')
    hero.health = 180
    hero.position = { x: 600, y: 520 }
    minion.team = 'enemy'
    minion.id = 'enemy-0-0'
    minion.enemyIndex = 0
    minion.health = 54
    minion.position = { x: 810, y: 520 }
    game.input({ kind: 'point', at_ms: 0, ...minion.position, target_id: minion.id, button: 0 })
    game.advanceTo(520)
    expect(game.raw().attacks).toBe(1)
    expect(game.snapshot().projectiles.some(projectile => !projectile.kind)).toBe(true)
    state.missiles.push({ id: 'lethal', order: 999, sourceOrder: 10002, team: 'enemy', targetId: 'hero',
      position: { ...hero.position }, speed: 650, damage: 180, byPlayer: false, kind: 'tower', targetLife: 0 })
    game.advanceTo(540)
    expect(game.snapshot().units[0].health).toBe(0)
    game.advanceTo(1000)
    expect(game.raw().deaths).toEqual([{ unit_id: 0, at_ms: 900, by_player: true }])
    expect(game.snapshot().last_hits).toBe(1)
  })

  it('clears an unreleased player windup on death and cannot attack from a corpse', () => {
    const { game, state, hero, minion } = arena('ranged')
    hero.position = { x: 600, y: 520 }
    minion.team = 'enemy'
    minion.id = 'enemy-0-0'
    minion.enemyIndex = 0
    minion.position = { x: 810, y: 520 }
    game.input({ kind: 'point', at_ms: 0, ...minion.position, target_id: minion.id, button: 0 })
    game.advanceTo(200)
    expect(game.snapshot().units[0].state).toBe('windup')
    state.missiles = Array.from({ length: 6 }, (_, order) => ({ id: `lethal-${order}`, order,
      sourceOrder: 10002, team: 'enemy', targetId: 'hero', position: { ...hero.position }, speed: 650,
      damage: 180, byPlayer: false, kind: 'tower', targetLife: 0 }))
    game.advanceTo(220)
    expect(game.snapshot()).toMatchObject({ selected_target_id: null, move_destination: null })
    game.advanceTo(1000)
    expect(game.raw().attacks).toBe(0)
    expect(game.snapshot().units[0]).toMatchObject({ health: 0, state: 'dying', attack_progress: 0 })
    expect(game.raw()).toMatchObject({ hero_damage_taken: 1000, hero_death_times: [220] })
  })

  it('does not resurrect at the excluded endpoint even when the exact respawn deadline is 300000', () => {
    const { game, state, hero } = arena()
    game.advanceTo(294980)
    state.missiles = Array.from({ length: 6 }, (_, order) => ({ id: `end-${order}`, order,
      sourceOrder: 10002, team: 'enemy', targetId: 'hero', position: { ...hero.position }, speed: 650,
      damage: 180, byPlayer: false, kind: 'tower', targetLife: 0 }))
    game.advanceTo(295000)
    expect(game.snapshot().hero_status).toMatchObject({ health: 0, deaths: 1, respawn_at_ms: 300000 })
    game.advanceTo(299980)
    const before = game.snapshot(), raw = game.raw()
    game.advanceTo(300000)
    expect(game.snapshot()).toEqual(before)
    expect(game.raw()).toEqual(raw)
  })

  it.each(['melee', 'ranged'] as const)('%s is deterministic across full-session frame partitions and seeds', mode => {
    const bulk = new LaneSimulation(mode, 1, params), split = new LaneSimulation(mode, 42, params)
    for (const at_ms of [0, 10123, 25001, 171345]) {
      const input = { kind: 'point', at_ms, x: 930, y: 520, button: 0, target_id: null } as const
      bulk.input(input)
      for (let at = split.at_ms + 137; at < at_ms; at += 137) split.advanceTo(at)
      split.input(input)
    }
    bulk.advanceTo(300000)
    for (let at = split.at_ms + 977; at < 300000; at += 977) split.advanceTo(at)
    split.advanceTo(300000)
    expect(split.snapshot()).toEqual(bulk.snapshot())
    expect(split.raw()).toEqual(bulk.raw())
    const raw = bulk.raw()
    expect(validateRaw(fixtureMobaV5Config(mode), raw)).toEqual(raw)
    expect(raw.deaths.every(death => death.unit_id >= 0 && death.unit_id < 75 && death.at_ms >= Math.floor(death.unit_id / 5) * 20000 && death.at_ms < 300000)).toBe(true)
    expect(new Set(raw.deaths.map(death => death.unit_id)).size).toBe(raw.deaths.length)
  })
})
