import { isLaneTaskConfig, taskConfigSchema, type ControlSettings, type LaneLastHitRaw, type LaneTowerLastHitRaw, type TaskConfig } from '../contracts'
import type { GameEngine, MobaSceneSnapshot } from '../engineRuntime/types'
import { LANE_SIZE, PICK_RADIUS } from './constants'
import { LaneSimulation } from './simulation'

export function createLaneEngine(config: TaskConfig, seed: number, settings: ControlSettings): GameEngine {
  const valid = taskConfigSchema.parse(config)
  if (!isLaneTaskConfig(valid)) throw new Error('Exact pc-moba-v4/v5 lane configuration required')
  // Mode and fixed stats come exclusively from the signed server configuration.
  void settings
  const simulation = new LaneSimulation(valid.params.attack_mode, seed, valid.params.kind === 'lane_last_hit_towers' ? valid.params : undefined)
  let finished: LaneLastHitRaw | LaneTowerLastHitRaw | null = null
  return {
    input(action) { if (!finished) simulation.input(action) },
    advanceTo(at) { if (!finished) simulation.advanceTo(at) },
    scene(): MobaSceneSnapshot {
      const lane = simulation.snapshot(), hero = lane.units[0]
      return {
        kind: 'moba', at_ms: simulation.at_ms, logical_size: { ...LANE_SIZE },
        player: { id: hero.id, position: { ...hero.position }, radius: 12, facing_rad: hero.facing_rad },
        targets: lane.units.filter(unit => unit.team === 'enemy').map(unit => ({
          id: unit.id, position: { ...unit.position }, radius: PICK_RADIUS, type: 'minion',
          state: unit.health > 0 ? 'active' : 'resolved', health_ratio: unit.health / unit.max_health,
        })),
        projectiles: [], minimap: { visible: false, bounds: { x: 0, y: 0, width: 0, height: 0 }, event: null }, lane,
      }
    },
    finish() {
      finished ??= simulation.raw()
      return structuredClone(finished)
    },
  }
}
