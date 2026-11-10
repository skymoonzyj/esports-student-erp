import { describe, expect, it } from 'vitest'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import { previewTaskConfig, taskConfigSchema, validateRaw } from './contracts'

describe('frozen five-minute lane contracts', () => {
  it('preserves ranged previews and can select ranged from catalog default', () => {
    const config = fixtureMobaV4Config('ranged')
    expect(config.duration_ms).toBe(300000)
    expect(config.params).toMatchObject({ kind: 'lane_last_hit', attack_mode: 'ranged', attack_damage: 54, attack_range: 210, projectile_speed: 500 })
    expect(previewTaskConfig(config, 1).params).toEqual(config.params)
    expect(previewTaskConfig(fixtureMobaV4Config(), 3, 'ranged')).toEqual(config)
    expect(() => taskConfigSchema.parse({ ...config, duration_ms: 60000 })).toThrow()
  })
  it('rejects duplicate, pre-spawn and too many ranged attacks', () => {
    const config = fixtureMobaV4Config('ranged')
    for (const value of [
      { deaths: [], attacks: 241 },
      { deaths: [{ unit_id: 5, at_ms: 29999, by_player: false }], attacks: 0 },
      { deaths: [{ unit_id: 0, at_ms: 1000, by_player: false }, { unit_id: 0, at_ms: 1001, by_player: false }], attacks: 0 },
    ]) expect(() => validateRaw(config, { kind: 'lane_last_hit', cancelled_attacks: 0, ...value })).toThrow()
  })
  it('allows responsive cancels only inside the fixed-step five-minute cap', () => {
    const config = fixtureMobaV4Config()
    expect(validateRaw(config, { kind: 'lane_last_hit', deaths: [], attacks: 0, cancelled_attacks: 15000 }).kind).toBe('lane_last_hit')
    expect(() => validateRaw(config, { kind: 'lane_last_hit', deaths: [], attacks: 0, cancelled_attacks: 15001 })).toThrow()
  })
})
