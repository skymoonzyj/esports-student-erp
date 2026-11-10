import { describe, expect, it } from 'vitest'
import * as contracts from './contracts'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { fixtureMobaV5Config, fixtureMobaV5Raw } from './testSupport/fixturesMobaV5'
import { registeredRule } from './versionRules'

const config = () => {
  const old = fixtureMobaV4Config()
  return { ...old, catalog_version: 'pc-moba-v5', engine_version: 'pc-moba-v5', scoring_version: 'pc-moba-v5', report_version: 'pc-moba-v5', params: { ...old.params, kind: 'lane_last_hit_towers', wave_interval_ms: 20000, wave_count: 15, hero_health: 1000, respawn_ms: 5000, tower_damage: 180, tower_interval_ms: 1200, tower_windup_ms: 300, tower_range: 240, tower_projectile_speed: 650, tower_positions: [{ id: 'ally-rear', team: 'ally', x: 70, y: 162.5 }, { id: 'ally-front', team: 'ally', x: 280, y: 400 }, { id: 'enemy-front', team: 'enemy', x: 720, y: 162.5 }, { id: 'enemy-rear', team: 'enemy', x: 930, y: 400 }] } }
}
const raw = (updates = {}) => ({ kind: 'lane_last_hit_towers', deaths: [], attacks: 0, cancelled_attacks: 0, hero_death_times: [], hero_damage_taken: 0, ...updates })

describe('independent frozen tower lane contract', () => {
  it('accepts the signed seventy-five-unit catalog and exact tower positions', () => {
    expect(contracts.taskConfigSchema.safeParse(config()).success).toBe(true)
    expect(contracts.taskConfigSchema.safeParse({ ...config(), params: { ...config().params, tower_damage: 181 } }).success).toBe(false)
  })
  it('accepts last wave birth and rejects pre-birth/end and inconsistent hero lives', () => {
    const parsed = contracts.taskConfigSchema.parse(config())
    expect(contracts.validateRaw(parsed, raw({ deaths: [{ unit_id: 74, at_ms: 280000, by_player: false }] })).kind).toBe('lane_last_hit_towers')
    for (const update of [{ deaths: [{ unit_id: 74, at_ms: 279999, by_player: false }] }, { deaths: [{ unit_id: 0, at_ms: 300000, by_player: false }] }, { hero_death_times: [10000], hero_damage_taken: 999 }, { hero_death_times: [10000, 14999], hero_damage_taken: 2000 }, { hero_damage_taken: 1000 }, { attacks: 274 }]) expect(() => contracts.validateRaw(parsed, raw(update))).toThrow()
  })
  it('recognizes both exact lane versions but never a mismatched identity', () => {
    expect(contracts.isLaneTaskConfig(fixtureMobaV4Config())).toBe(true)
    expect(contracts.isLaneTaskConfig(fixtureMobaV5Config())).toBe(true)
    expect(contracts.isLaneTaskConfig({ ...fixtureMobaV5Config(), catalog_version: 'pc-moba-v4' })).toBe(false)
    expect(() => registeredRule('pc-moba', 'dodge', 'pc-moba-v5')).toThrow()
  })
  it('keeps complete ranged previews and tower fixtures aligned', () => {
    const ranged = fixtureMobaV5Config('ranged')
    expect(ranged.params).toMatchObject({ kind: 'lane_last_hit_towers', attack_mode: 'ranged', attack_range: 210, attack_damage: 54 })
    expect(contracts.previewTaskConfig(ranged, 1).params).toEqual(ranged.params)
    expect(contracts.validateRaw(ranged, fixtureMobaV5Raw()).kind).toBe('lane_last_hit_towers')
    expect(() => contracts.validateRaw(fixtureMobaV4Config(), fixtureMobaV5Raw())).toThrow()
    expect(() => contracts.validateRaw(fixtureMobaV4Config(), { kind: 'lane_last_hit', deaths: [{ unit_id: 50, at_ms: 280000, by_player: false }], attacks: 0, cancelled_attacks: 0 })).toThrow()
    expect(() => contracts.validateRaw(fixtureMobaV4Config(), { kind: 'lane_last_hit', deaths: [{ unit_id: 5, at_ms: 20000, by_player: false }], attacks: 0, cancelled_attacks: 0 })).toThrow()
  })
})
