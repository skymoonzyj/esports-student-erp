import catalog from './catalog-moba-v5.json' with { type: 'json' }
import { previewTaskConfig, suiteConfigSchema, type AttackMode, type LaneTowerLastHitRaw, type TaskConfig } from '../contracts'

export function fixtureMobaV5Config(attack_mode: AttackMode = 'melee'): TaskConfig {
  return previewTaskConfig(suiteConfigSchema.parse(catalog).tasks['last-hit']!, 3, attack_mode)
}

export function fixtureMobaV5Raw(perfect = true): LaneTowerLastHitRaw {
  return { kind: 'lane_last_hit_towers', deaths: Array.from({ length: 75 }, (_, unit_id) => ({ unit_id, at_ms: Math.floor(unit_id / 5) * 20000 + 1000 + unit_id % 5, by_player: perfect })), attacks: 75, cancelled_attacks: 0, hero_death_times: [], hero_damage_taken: 0 }
}
