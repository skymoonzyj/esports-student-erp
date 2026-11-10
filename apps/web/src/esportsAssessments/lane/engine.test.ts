import { describe, expect, it } from 'vitest'
import { validateRaw } from '../contracts'
import { fixtureConfig, fixtureSettings } from '../testSupport/fixtures'
import { fixtureMobaV4Config } from '../testSupport/fixturesMobaV4'
import { fixtureMobaV5Config } from '../testSupport/fixturesMobaV5'
import { createLaneEngine } from './engine'

const settings = fixtureSettings('pc-moba')
describe('lane engine adapter', () => {
  it.each(['melee', 'ranged'] as const)('runs signed v5 %s rules with four towers and immutable finished life stats', mode => {
    const config = fixtureMobaV5Config(mode)
    const game = createLaneEngine(config, 7, settings)
    const scene = game.scene()
    expect(scene).toMatchObject({ lane: { attack_mode: mode, wave_count: 15, hero_status: { health: 1000, deaths: 0 } } })
    if (scene.kind !== 'moba') throw new Error('Expected moba')
    expect(scene.lane?.towers).toHaveLength(4)
    game.input({ kind: 'point', at_ms: 0, x: 930, y: 520, button: 0, target_id: null })
    game.advanceTo(300000)
    const raw = game.finish()
    expect(raw.kind).toBe('lane_last_hit_towers')
    expect(validateRaw(config, raw)).toEqual(raw)
    if (raw.kind !== 'lane_last_hit_towers') throw new Error('Expected towers')
    expect(raw.hero_death_times).toHaveLength(1)
    raw.hero_death_times.push(299999)
    expect(game.finish()).not.toEqual(raw)
    expect(() => createLaneEngine({ ...config, engine_version: 'pc-moba-v4' }, 7, settings)).toThrow()
  })
  it.each(['melee', 'ranged'] as const)('projects logical %s lane state and returns a valid isolated frozen result', mode => {
    const config = fixtureMobaV4Config(mode)
    const game = createLaneEngine(config, 7, settings)
    expect(game.scene()).toMatchObject({ kind: 'moba', logical_size: { width: 1000, height: 562.5 }, minimap: { visible: false }, lane: { attack_mode: mode, wave: 1, wave_count: 10 } })
    expect(game.scene()).not.toHaveProperty('feedback')
    game.advanceTo(300_000)
    const raw = game.finish()
    expect(raw.kind).toBe('lane_last_hit')
    expect(validateRaw(config, raw)).toEqual(raw)
    expect(raw.kind === 'lane_last_hit' && raw.deaths.length).toBeGreaterThan(0)
    const scene = game.scene()
    expect(scene.at_ms).toBe(300_000)
    game.input({ kind: 'point', at_ms: 300_000, x: 100, y: 100, button: 0 })
    game.advanceTo(500_000)
    expect(game.finish()).toEqual(raw)
    expect(game.scene()).toEqual(scene)
    if (raw.kind === 'lane_last_hit') raw.deaths.length = 0
    expect(game.finish()).not.toEqual(raw)
  })

  it('rejects nonexact versions, legacy parameters and mutated fixed values', () => {
    const config = fixtureMobaV4Config()
    expect(() => createLaneEngine(fixtureConfig('pc-moba', 'last-hit'), 1, settings)).toThrow()
    for (const invalid of [
      { ...config, engine_version: 'pc-moba-v3' },
      { ...config, catalog_version: 'pc-moba-v3' },
      { ...config, duration_ms: 60000 },
      { ...config, params: { ...config.params, attack_damage: 999 } },
    ]) expect(() => createLaneEngine(invalid as typeof config, 1, settings)).toThrow()
  })
})
