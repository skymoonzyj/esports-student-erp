import catalog from './catalog-moba-v4.json' with { type: 'json' }
import { previewTaskConfig, suiteConfigSchema, type AttackMode, type LaneLastHitRaw, type TaskConfig } from '../contracts'

export function fixtureMobaV4Config(mode: AttackMode = 'melee'): TaskConfig {
  return previewTaskConfig(suiteConfigSchema.parse(catalog).tasks['last-hit']!, 3, mode)
}

export function fixtureMobaV4Raw(perfect = true): LaneLastHitRaw {
  return { kind: 'lane_last_hit', deaths: Array.from({ length: 50 }, (_, unit_id) => ({ unit_id, at_ms: Math.floor(unit_id / 5) * 30000 + 1000 + unit_id % 5, by_player: perfect })), attacks: 50, cancelled_attacks: 0 }
}
