import { describe, expect, it } from 'vitest'
import { LaneSimulation } from './simulation'
import { validateRaw } from '../contracts'
import { fixtureMobaV4Config } from '../testSupport/fixturesMobaV4'

describe('deterministic lane simulation', () => {
  it('spawns exactly three melee and two ranged per side at each of ten half-open waves', () => {
    const game = new LaneSimulation('melee', 7)
    for (let wave = 0; wave < 10; wave++) {
      game.advanceTo(wave * 30_000)
      const units = game.snapshot().units
      for (const team of ['ally', 'enemy'] as const) {
        const fresh = units.filter(unit => unit.id.startsWith(`${team}-${wave}-`))
        expect(fresh.map(unit => unit.kind)).toEqual(['melee', 'melee', 'melee', 'ranged', 'ranged'])
        expect(fresh.map(unit => unit.position)).toEqual([
          { x: team === 'ally' ? 100 : 900, y: 247.25 },
          { x: team === 'ally' ? 100 : 900, y: 281.25 },
          { x: team === 'ally' ? 100 : 900, y: 315.25 },
          { x: team === 'ally' ? 60 : 940, y: 264.25 },
          { x: team === 'ally' ? 60 : 940, y: 298.25 },
        ])
      }
      expect(game.snapshot().wave).toBe(wave + 1)
      expect(units.length).toBeLessThanOrEqual(101)
    }
    game.advanceTo(300_000)
    expect(game.snapshot().wave).toBe(10)
    expect(game.snapshot().units.every(unit => !unit.id.includes('-10-'))).toBe(true)
    expect(game.raw().deaths.every(death => death.at_ms < 300_000)).toBe(true)
  })

  it('has no physical event at the excluded endpoint and never lets snapshot mutation corrupt simulation', () => {
    const game = new LaneSimulation('ranged', 11)
    const exposed = game.snapshot()
    ;(exposed.units[0].position as { x: number }).x = NaN
    expect(game.snapshot().units[0].position.x).toBe(400)
    game.advanceTo(299_980)
    const before = game.snapshot(), raw = game.raw()
    game.advanceTo(299_999)
    expect(game.snapshot()).toEqual(before)
    game.advanceTo(300_000)
    game.advanceTo(Number.MAX_VALUE)
    expect(game.at_ms).toBe(300_000)
    expect(game.snapshot()).toEqual(before)
    expect(game.raw()).toEqual(raw)
  })

  it('moves and trades real damage without player input, retaining visible hit and death states', () => {
    const game = new LaneSimulation('melee', 1)
    game.advanceTo(1000)
    expect(game.snapshot().units.find(unit => unit.id === 'ally-0-0')!.position.x).toBeCloseTo(185)
    let sawDamage = false, sawDeath = false
    for (let now = 1020; now < 29_000; now += 20) {
      game.advanceTo(now)
      sawDamage ||= game.snapshot().units.some(unit => unit.kind !== 'hero' && unit.health < unit.max_health && unit.hit_at_ms !== null)
      sawDeath ||= game.snapshot().units.some(unit => unit.state === 'dying')
    }
    expect(sawDamage).toBe(true)
    expect(sawDeath).toBe(true)
    expect(game.raw().deaths.length).toBeGreaterThan(0)
    expect(game.raw().deaths.every(death => !death.by_player)).toBe(true)
  })

  it('uses fixed creep movement speed and health for both sides', () => {
    const game = new LaneSimulation('melee', 1)
    const before = game.snapshot()
    game.advanceTo(20)
    for (const unit of game.snapshot().units.filter(unit => unit.kind !== 'hero')) {
      const old = before.units.find(previous => previous.id === unit.id)!
      expect(Math.hypot(unit.position.x - old.position.x, unit.position.y - old.position.y)).toBeCloseTo(unit.kind === 'melee' ? 1.7 : 1.5)
      expect(unit.health).toBe(unit.kind === 'melee' ? 400 : 240)
    }
  })

  it.each((['melee', 'ranged'] as const).flatMap(mode => Array.from({ length: 10 }, (_, seed) => ({ mode, seed }))))('$mode seed $seed replays five minutes regardless of frame partition', ({ mode, seed }) => {
      const bulk = new LaneSimulation(mode, seed), split = new LaneSimulation(mode, seed)
      const actions = [
        { kind: 'point', at_ms: 0, x: 900, y: 247.25, button: 0 },
        { kind: 'point', at_ms: 6123, x: 430, y: 400, button: 2 },
        { kind: 'point', at_ms: 31_001, x: 820, y: 282, button: 0 },
      ] as const
      for (const action of actions) {
        bulk.input(action)
        for (let time = split.at_ms + 137; time < action.at_ms; time += 137) split.advanceTo(time)
        split.input(action)
      }
      bulk.advanceTo(300_000)
      for (let time = split.at_ms + 977; time < 300_000; time += 977) split.advanceTo(time)
      split.advanceTo(300_000)
      expect(split.snapshot()).toEqual(bulk.snapshot())
      expect(split.raw()).toEqual(bulk.raw())
      expect(validateRaw(fixtureMobaV4Config(mode), bulk.raw())).toEqual(bulk.raw())
      const deaths = bulk.raw().deaths
      expect(new Set(deaths.map(death => death.unit_id)).size).toBe(deaths.length)
      expect(deaths).toEqual([...deaths].sort((a, b) => a.at_ms - b.at_ms || a.unit_id - b.unit_id))
      expect(deaths.every(death => death.unit_id >= 0 && death.unit_id < 50 && death.at_ms >= Math.floor(death.unit_id / 5) * 30_000 && death.at_ms < 300_000)).toBe(true)
      expect(bulk.snapshot().units.length).toBeLessThanOrEqual(101)
  })

  it('selects only enemy creeps, chases, attacks repeatedly and stops when that target dies', () => {
    const game = new LaneSimulation('melee', 1)
    game.input({ kind: 'point', at_ms: 0, x: 100, y: 247.25, button: 0 })
    expect(game.snapshot().selected_target_id).toBeNull()
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, button: 2 })
    expect(game.snapshot().selected_target_id).toBe('enemy-0-0')
    game.advanceTo(500)
    expect(game.snapshot().units[0].position.x).toBeGreaterThan(400)
    game.advanceTo(20_000)
    expect(game.raw().attacks).toBeGreaterThan(1)
    expect(game.snapshot().selected_target_id).toBeNull()
    const attacks = game.raw().attacks
    game.advanceTo(29_999)
    expect(game.raw().attacks).toBe(attacks)
  })

  it('selects the nearest live enemy when two 26-unit pick circles overlap', () => {
    const game = new LaneSimulation('melee', 1)
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 270, button: 0 })
    expect(game.snapshot().selected_target_id).toBe('enemy-0-1')
  })

  it('cancels an unreleased melee windup but never damages the former target or counts a release', () => {
    const game = new LaneSimulation('melee', 1)
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, button: 0 })
    let start = 0
    for (let now = 20; now < 4000; now += 20) {
      game.advanceTo(now)
      if (game.snapshot().units[0].state === 'windup') { start = now; break }
    }
    expect(start).toBeGreaterThan(0)
    game.input({ kind: 'point', at_ms: start + 280, x: 400, y: 500, button: 0 })
    game.advanceTo(start + 320)
    expect(game.raw().attacks).toBe(0)
    expect(game.raw().cancelled_attacks).toBe(1)
    expect(game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.health).toBe(400)
  })

  it('deals exactly 70 on a melee release after the 300ms windup, not on button press', () => {
    const game = new LaneSimulation('melee', 1)
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, button: 0 })
    let start = 0
    for (let now = 20; now < 4000; now += 20) {
      game.advanceTo(now)
      if (game.snapshot().units[0].state === 'windup') { start = now; break }
    }
    expect(start).toBeGreaterThan(0)
    game.advanceTo(start + 299)
    expect(game.raw().attacks).toBe(0)
    expect(game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.health).toBe(400)
    game.advanceTo(start + 300)
    expect(game.raw().attacks).toBe(1)
    expect(game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.health).toBe(330)
    expect(game.snapshot().units[0].state).toBe('attacking')
    game.advanceTo(start + 1399)
    expect(game.raw().attacks).toBe(1)
    game.advanceTo(start + 1400)
    expect(game.raw().attacks).toBe(2)
    // The second release occurs on cooldown, but the retreating creep moved
    // beyond melee range during its windup: a release is not a guaranteed hit.
    expect(game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.health).toBe(330)
  })

  it('retains a released ranged missile after movement, delays its 54 damage and never cancels that release', () => {
    const game = new LaneSimulation('ranged', 1)
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, button: 0 })
    let start = 0
    for (let now = 20; now < 4000; now += 20) {
      game.advanceTo(now)
      if (game.snapshot().units[0].state === 'windup') { start = now; break }
    }
    game.advanceTo(start + 449)
    expect(game.raw().attacks).toBe(0)
    // 450ms rounds up to the next fixed 20ms simulation boundary, 460ms.
    game.advanceTo(start + 460)
    expect(game.raw().attacks).toBe(1)
    expect(game.snapshot().projectiles).toHaveLength(1)
    expect(game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.health).toBe(400)
    game.input({ kind: 'point', at_ms: start + 460, x: 400, y: 500, button: 2 })
    game.advanceTo(start + 480)
    expect(game.snapshot().projectiles).toHaveLength(1)
    game.advanceTo(start + 1000)
    expect(game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.health).toBe(346)
    expect(game.raw().cancelled_attacks).toBe(0)
    expect(game.raw().attacks).toBe(1)
    expect(game.snapshot().units[0].position.y).toBeGreaterThan(400)
  })

  it('keeps living creep silhouettes separated during a complete no-input lane', () => {
    const game = new LaneSimulation('melee', 1)
    for (let now = 0; now < 300_000; now += 200) {
      game.advanceTo(now)
      const units = game.snapshot().units.filter(unit => unit.kind !== 'hero' && unit.health > 0)
      expect(game.snapshot().projectiles.length).toBeLessThanOrEqual(100)
      expect(game.snapshot().units[0].health).toBe(1)
      for (let i = 0; i < units.length; i++) for (let j = i + 1; j < units.length; j++) {
        expect(Math.hypot(units[i].position.x - units[j].position.x, units[i].position.y - units[j].position.y)).toBeGreaterThan(23)
      }
    }
  })

  it('cannot create extra windups with many commands at an identical timestamp', () => {
    const game = new LaneSimulation('ranged', 1)
    game.input({ kind: 'point', at_ms: 0, x: 900, y: 247.25, button: 0 })
    let start = 0
    for (let now = 20; now < 4000; now += 20) {
      game.advanceTo(now)
      if (game.snapshot().units[0].state === 'windup') { start = now; break }
    }
    const target = game.snapshot().units.find(unit => unit.id === 'enemy-0-0')!.position
    for (let i = 0; i < 20_000; i++) {
      game.input({ kind: 'point', at_ms: start, x: 400, y: 500, button: 0 })
      game.input({ kind: 'point', at_ms: start, ...target, button: 0 })
    }
    expect(game.raw().cancelled_attacks).toBe(1)
    expect(game.raw().attacks).toBe(0)
    game.advanceTo(start + 20)
    expect(game.snapshot().units[0].state).toBe('windup')
  })

  it('ignores non-point skills, stale or invalid timestamps and non-finite coordinates; clamps finite extreme points', () => {
    const game = new LaneSimulation('ranged', 1)
    game.advanceTo(123)
    const before = game.snapshot()
    for (const at_ms of [-1, 122, NaN, Infinity, 300_000]) game.input({ kind: 'point', at_ms, x: 900, y: 247.25, button: 0 })
    game.input({ kind: 'point', at_ms: 123, x: NaN, y: 10, button: 0 })
    game.input({ kind: 'point', at_ms: 123, x: 5, y: Infinity, button: 0 })
    game.input({ kind: 'skill', at_ms: 123, x: 900, y: 247.25, release: true })
    game.advanceTo(NaN); game.advanceTo(Infinity); game.advanceTo(-20)
    expect(game.snapshot()).toEqual(before)
    game.input({ kind: 'point', at_ms: 123, x: Number.MAX_VALUE, y: -Number.MAX_VALUE, button: 0 })
    expect(game.snapshot().move_destination).toEqual({ x: 988, y: 12 })
    game.advanceTo(4000)
    expect(game.snapshot().units[0].position).toEqual({ x: 988, y: 12 })
  })
})
