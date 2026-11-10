import { z } from 'zod'
import { buildDodgeChoicePlan } from './ruleSchedule'
import { isPcFpsTriadCatalog, isPcMobaLaneCatalog, registeredRule } from './versionRules'

const finite = z.number().finite()
const nonnegative = finite.nonnegative()
const positive = finite.positive()
const count = z.number().int().nonnegative()
const time = nonnegative.max(60000)
const uuid = z.uuid()
const date = z.iso.datetime({ offset: true })
export const suiteIdSchema = z.enum(['pc-fps', 'mobile-fps', 'pc-moba', 'mobile-moba'])
export const testIdSchema = z.enum(['precision', 'tracking', 'switching', 'recognition', 'move-fire', 'last-hit', 'orb-walk', 'dodge', 'map-awareness', 'skill-aim', 'move-attack', 'gridshot', 'sixshot', 'spheretrack90'])
export const modeSchema = z.enum(['formal', 'practice'])
export const runStateSchema = z.enum(['running', 'awaiting_submission', 'interrupted', 'expired', 'submitted'])
export const inputKindSchema = z.enum(['mouse', 'touch', 'mixed', 'unknown'])
export type SuiteId = z.infer<typeof suiteIdSchema>
export type TestId = z.infer<typeof testIdSchema>
export type Mode = z.infer<typeof modeSchema>
export type RunState = z.infer<typeof runStateSchema>
export type InputKind = z.infer<typeof inputKindSchema>
export const attackModeSchema = z.enum(['melee', 'ranged'])
export type AttackMode = z.infer<typeof attackModeSchema>
export const suiteMembers: Readonly<Record<SuiteId, readonly TestId[]>> = {
  'pc-fps': ['precision', 'tracking', 'switching', 'recognition'],
  'mobile-fps': ['precision', 'tracking', 'switching', 'move-fire'],
  'pc-moba': ['last-hit', 'orb-walk', 'dodge', 'map-awareness'],
  'mobile-moba': ['dodge', 'skill-aim', 'move-attack', 'map-awareness'],
}
const registeredCatalogVersions: Readonly<Record<SuiteId, readonly string[]>> = {
  'pc-fps': ['pc-fps-v1', 'pc-fps-v2', 'pc-fps-v3', 'pc-fps-v4', 'pc-fps-v5'],
  'mobile-fps': ['mobile-fps-v1', 'mobile-fps-v2', 'mobile-fps-v3'],
  'pc-moba': ['pc-moba-v1', 'pc-moba-v2', 'pc-moba-v3', 'pc-moba-v4', 'pc-moba-v5'],
  'mobile-moba': ['mobile-moba-v1', 'mobile-moba-v2', 'mobile-moba-v3'],
}
/** Exact frozen members. Versionless identities use the supported historical union instead. */
export function suiteMembersForCatalog(suiteId: SuiteId, catalogVersion: string): readonly TestId[] {
  if (!registeredCatalogVersions[suiteId].includes(catalogVersion)) throw new Error('unknown catalog version')
  if (isPcMobaLaneCatalog(suiteId, catalogVersion)) return ['last-hit']
  return isPcFpsTriadCatalog(suiteId, catalogVersion) ? ['gridshot', 'sixshot', 'spheretrack90'] : suiteMembers[suiteId]
}
export function isSupportedSuiteTest(suiteId: SuiteId, testId: TestId): boolean {
  return suiteMembers[suiteId].includes(testId) || (suiteId === 'pc-fps' && ['gridshot', 'sixshot', 'spheretrack90'].includes(testId))
}
const fps = { horizontal_fov_deg: positive, pitch_limit_deg: positive, fire_interval_ms: positive }
const moba = { logical_width: positive, logical_height: positive, player_radius: positive, player_speed: positive, attack_range: positive }
const lane = { ...moba, attack_mode: attackModeSchema, wave_interval_ms: positive, wave_count: count, melee_per_wave: count, ranged_per_wave: count, attack_windup_ms: positive, attack_interval_ms: positive, attack_damage: positive, projectile_speed: nonnegative }
export const paramsSchema = z.discriminatedUnion('kind', [
  z.strictObject({ ...lane, kind: z.literal('lane_last_hit_towers'), hero_health: positive, respawn_ms: positive, tower_damage: positive, tower_interval_ms: positive, tower_windup_ms: positive, tower_range: positive, tower_projectile_speed: positive, tower_positions: z.array(z.strictObject({ id: z.enum(['ally-rear', 'ally-front', 'enemy-front', 'enemy-rear']), team: z.enum(['ally', 'enemy']), x: nonnegative, y: nonnegative })).length(4) }),
  z.strictObject({ ...moba, kind: z.literal('lane_last_hit'), attack_mode: attackModeSchema, wave_interval_ms: positive, wave_count: count, melee_per_wave: count, ranged_per_wave: count, attack_windup_ms: positive, attack_interval_ms: positive, attack_damage: positive, projectile_speed: nonnegative }),
  z.strictObject({ ...fps, kind: z.literal('multi_target'), target_count: count.positive(), target_diameter_deg: positive, grid_columns: count.positive(), grid_rows: count.positive(), grid_spacing_deg: positive, reference_hits: count.positive() }),
  z.strictObject({ ...fps, kind: z.literal('spheretrack90'), target_diameter_deg: positive, speed_deg_s: positive, horizontal_bound_deg: positive, vertical_bound_deg: positive, direction_interval_ms: positive, turn_ms: positive }),
  z.strictObject({ ...fps, kind: z.literal('aim'), opportunity_count: count.min(1).max(40), window_ms: positive, target_diameter_deg: positive, angle_distances_deg: z.array(positive).max(3), distance_repetitions: count, target_separation_deg: nonnegative, group_count: count, group_ms: nonnegative, fast_ms: positive, slow_ms: positive }),
  z.strictObject({ ...fps, kind: z.literal('tracking'), target_diameter_deg: positive, speed_deg_s: positive, horizontal_bound_deg: positive, vertical_bound_deg: positive, direction_interval_ms: positive, turn_ms: positive }),
  z.strictObject({ ...fps, kind: z.literal('recognition'), opportunity_count: count, enemy_count: count, friend_count: count, round_ms: positive, window_ms: positive, target_diameter_deg: positive, angle_distances_deg: z.array(positive).max(3), distance_repetitions: count, fast_ms: positive, slow_ms: positive }),
  z.strictObject({ ...fps, kind: z.literal('move_fire'), opportunity_count: count, window_ms: positive, target_diameter_deg: positive, checkpoint_distance_m: positive, checkpoint_tolerance_m: positive, player_speed_m_s: positive }),
  z.strictObject({ ...fps, kind: z.literal('move_fire_v3'), opportunity_count: count, window_ms: positive, target_diameter_deg: positive, checkpoint_distance_m: positive, checkpoint_tolerance_m: positive, player_speed_m_s: positive, target_offset_min_deg: positive, target_offset_max_deg: positive, min_moving_ms: positive, min_moving_speed_m_s: positive }),
  z.strictObject({ ...moba, kind: z.literal('last_hit'), opportunity_count: count, window_ms: positive, attack_windup_ms: positive, attack_interval_ms: positive, attack_damage: positive, last_hit_window_ms: positive, window_starts_ms: z.array(positive).length(5) }),
  z.strictObject({ ...moba, kind: z.literal('rhythm'), opportunity_count: count, window_ms: positive, attack_windup_ms: positive, attack_interval_ms: positive, checkpoint_distance: positive, checkpoint_tolerance: positive }),
  z.strictObject({ ...moba, kind: z.literal('dodge'), opportunity_count: count, window_ms: positive, warning_ms: positive, projectile_radius: positive, projectile_speed: positive, spawn_distance: positive }),
  z.strictObject({ ...moba, kind: z.literal('dodge_choice'), opportunity_count: count, window_ms: positive, warning_ms: positive, safe_radius: positive, candidate_offset: positive, candidate_distance: positive, warning_start_min_ms: nonnegative, warning_start_max_ms: positive, burst_ms: positive }),
  z.strictObject({ ...moba, kind: z.literal('skill_aim'), opportunity_count: count, window_ms: positive, target_radius: positive, target_speed: positive, projectile_speed: positive, projectile_radius: positive, max_distance: positive }),
  z.strictObject({ ...moba, kind: z.literal('map'), opportunity_count: count, window_ms: positive, target_radius: positive, event_count: count, event_start_ms: positive, event_interval_ms: positive, event_window_ms: positive, minimap_x: nonnegative, minimap_y: nonnegative, minimap_width: positive, minimap_height: positive }),
])
/** Exact registered parameters, including practice transformations; not client overrides. */
function registeredParams(suite: SuiteId, test: TestId, difficulty: number, catalogVersion: string, attackMode: AttackMode = 'melee'): z.infer<typeof paramsSchema> {
  const [size, speed, window, distance] = [[1.5, .6, 1.4, .7], [1.25, .8, 1.2, .85], [1, 1, 1, 1], [.85, 1.15, .85, 1.1], [.7, 1.3, .7, 1.2]][difficulty - 1]
  const mobile = suite.startsWith('mobile')
  const f = { horizontal_fov_deg: 90, pitch_limit_deg: 70, fire_interval_ms: 200 }
  const m = { logical_width: 1000, logical_height: 562.5, player_radius: 12, player_speed: 180, attack_range: 180 }
  if (isPcMobaLaneCatalog(suite, catalogVersion)) {
    const shared = { ...m, attack_mode: attackMode, player_speed: 250, attack_range: attackMode === 'melee' ? 55 : 210, melee_per_wave: 3, ranged_per_wave: 2, attack_windup_ms: attackMode === 'melee' ? 300 : 450, attack_interval_ms: attackMode === 'melee' ? 1100 : 1250, attack_damage: attackMode === 'melee' ? 70 : 54, projectile_speed: attackMode === 'melee' ? 0 : 500 }
    if (catalogVersion === 'pc-moba-v4') return { ...shared, kind: 'lane_last_hit', wave_interval_ms: 30000, wave_count: 10 }
    return { ...shared, kind: 'lane_last_hit_towers', wave_interval_ms: 20000, wave_count: 15, hero_health: 1000, respawn_ms: 5000, tower_damage: 180, tower_interval_ms: 1200, tower_windup_ms: 300, tower_range: 240, tower_projectile_speed: 650, tower_positions: [{ id: 'ally-rear', team: 'ally', x: 70, y: 162.5 }, { id: 'ally-front', team: 'ally', x: 280, y: 400 }, { id: 'enemy-front', team: 'enemy', x: 720, y: 162.5 }, { id: 'enemy-rear', team: 'enemy', x: 930, y: 400 }] }
  }
  switch (test) {
    case 'gridshot': case 'sixshot': return { ...f, pitch_limit_deg: catalogVersion === 'pc-fps-v5' ? (test === 'gridshot' ? 9 : 12) : 70, fire_interval_ms: 50, kind: 'multi_target', target_count: test === 'gridshot' ? 3 : 6, target_diameter_deg: (test === 'gridshot' ? 6 : 1.5) * size, grid_columns: 5, grid_rows: test === 'gridshot' ? 3 : 5, grid_spacing_deg: test === 'gridshot' ? (catalogVersion === 'pc-fps-v5' ? 9 : 10) : 6, reference_hits: test === 'gridshot' ? 300 : 150 }
    case 'spheretrack90': return { ...f, kind: 'spheretrack90', target_diameter_deg: 3 * size, speed_deg_s: 30 * speed, horizontal_bound_deg: 40, vertical_bound_deg: 15, direction_interval_ms: 2000, turn_ms: 200 }
    case 'precision': return { ...f, kind: 'aim', opportunity_count: 30, window_ms: 2000, target_diameter_deg: (mobile ? 5 : 3) * size, angle_distances_deg: (mobile ? [8, 16, 24] : [10, 20, 30]).map(x => x * distance), distance_repetitions: 10, target_separation_deg: 0, group_count: 0, group_ms: 0, fast_ms: mobile ? 550 : 350, slow_ms: 2000 }
    case 'switching': return { ...f, kind: 'aim', opportunity_count: 40, window_ms: 1500, target_diameter_deg: (mobile ? 6 : 4) * size, angle_distances_deg: [], distance_repetitions: 0, target_separation_deg: (mobile ? 20 : 25) * distance, group_count: 20, group_ms: 3000, fast_ms: mobile ? 450 : 300, slow_ms: 1500 }
    case 'tracking': return { ...f, kind: 'tracking', target_diameter_deg: (mobile ? 6 : 4) * size, speed_deg_s: (mobile ? 20 : 30) * speed, horizontal_bound_deg: 30, vertical_bound_deg: 15, direction_interval_ms: 2000, turn_ms: 200 }
    case 'recognition': return { ...f, kind: 'recognition', opportunity_count: 30, enemy_count: 15, friend_count: 15, round_ms: 2000, window_ms: 1200, target_diameter_deg: 4 * size, angle_distances_deg: [10, 20, 30].map(x => x * distance), distance_repetitions: 10, fast_ms: 250, slow_ms: 1200 }
    case 'move-fire': return catalogVersion === 'mobile-fps-v3'
      ? { ...f, kind: 'move_fire_v3', opportunity_count: 20, window_ms: 3000, target_diameter_deg: 6 * size, checkpoint_distance_m: 1.2 * distance, checkpoint_tolerance_m: .35, player_speed_m_s: 2, target_offset_min_deg: 12 * distance, target_offset_max_deg: 20 * distance, min_moving_ms: 100, min_moving_speed_m_s: 1 }
      : { ...f, kind: 'move_fire', opportunity_count: 20, window_ms: 3000, target_diameter_deg: 6 * size, checkpoint_distance_m: 1.2 * distance, checkpoint_tolerance_m: .35, player_speed_m_s: 2 }
    case 'last-hit': return { ...m, kind: 'last_hit', opportunity_count: 15, window_ms: 4000, attack_windup_ms: 250, attack_interval_ms: 1000, attack_damage: 10, last_hit_window_ms: 500 * window, window_starts_ms: [2000, 2250, 2500, 2750, 3000] }
    case 'orb-walk': case 'move-attack': return { ...m, kind: 'rhythm', opportunity_count: 30, window_ms: 2000, attack_windup_ms: mobile ? 300 : 250, attack_interval_ms: 1000, checkpoint_distance: (mobile ? 90 : 100) * distance, checkpoint_tolerance: mobile ? 30 : 25 }
    case 'dodge': return catalogVersion === `${suite}-v3`
      ? { ...m, kind: 'dodge_choice', opportunity_count: 30, window_ms: 2000, warning_ms: (mobile ? [1250, 1200, 1100, 1050, 1000] : [1200, 1100, 1000, 950, 900])[difficulty - 1], safe_radius: 32, candidate_offset: 20, candidate_distance: 100, warning_start_min_ms: 100, warning_start_max_ms: 600, burst_ms: 150 }
      : { ...m, kind: 'dodge', opportunity_count: 30, window_ms: 2000, warning_ms: (mobile ? 550 : 400) * window, projectile_radius: 10, projectile_speed: (mobile ? 350 : 400) * speed, spawn_distance: 240 }
    case 'skill-aim': return { ...m, kind: 'skill_aim', opportunity_count: 30, window_ms: 2000, target_radius: 22 * size, target_speed: 80 * speed, projectile_speed: 600, projectile_radius: 10, max_distance: 500 }
    case 'map-awareness': return { ...m, kind: 'map', opportunity_count: 30, window_ms: 2000, target_radius: 24, event_count: 12, event_start_ms: 3000, event_interval_ms: 5000, event_window_ms: 1000 * window, minimap_x: 820, minimap_y: 0, minimap_width: 180, minimap_height: 110 }
  }
}
export const taskConfigSchema = z.strictObject({
  suite_id: suiteIdSchema, test_id: testIdSchema, catalog_version: z.string(), engine_version: z.string(), scoring_version: z.string(), report_version: z.string(), duration_ms: z.union([z.literal(60000), z.literal(300000)]), difficulty: count.min(1).max(5), params: paramsSchema,
}).superRefine((value, ctx) => {
  const fail = (message: string) => ctx.addIssue({ code: 'custom', message })
  let identity
  try { identity = registeredRule(value.suite_id, value.test_id, value.catalog_version) } catch { fail('unknown rule version'); return }
  if (!suiteMembersForCatalog(value.suite_id, value.catalog_version).includes(value.test_id) || identity.raw_kind !== value.params.kind) fail('task does not belong to suite or engine')
  if (!registeredCatalogVersions[value.suite_id].includes(value.catalog_version)
    || value.engine_version !== value.catalog_version
    || value.scoring_version !== identity.scoring_version
    || value.report_version !== identity.report_version) fail('unknown rule version')
  const p = value.params
  if (value.duration_ms !== (isPcMobaLaneCatalog(value.suite_id, value.catalog_version) ? 300000 : 60000)) fail('duration differs from registered rule version')
  const expected = registeredParams(value.suite_id, value.test_id, value.difficulty, value.catalog_version, p.kind === 'lane_last_hit' || p.kind === 'lane_last_hit_towers' ? p.attack_mode : undefined)
  if (Object.entries(expected).some(([key, expectedValue]) => JSON.stringify(Reflect.get(p, key)) !== JSON.stringify(expectedValue))) fail('parameters differ from registered rule version')
  if (p.kind === 'dodge' && p.warning_ms + p.spawn_distance / p.projectile_speed * 1000 > p.window_ms) fail('threat cannot arrive within round')
  if (p.kind === 'rhythm' && p.attack_windup_ms + p.checkpoint_distance / p.player_speed * 1000 > p.window_ms) fail('checkpoint unreachable')
  if (p.kind === 'last_hit' && Math.max(...p.window_starts_ms) + p.last_hit_window_ms > p.window_ms) fail('last-hit window exceeds wave')
  if (p.kind === 'move_fire_v3' && (p.target_offset_min_deg <= p.target_diameter_deg / 2 || p.target_offset_max_deg + p.target_diameter_deg / 2 > p.horizontal_fov_deg / 2)) fail('moving target is not fully visible away from the reticle')
  if (p.kind === 'dodge_choice' && (p.warning_start_min_ms > p.warning_start_max_ms || p.warning_start_max_ms + p.warning_ms + p.burst_ms > p.window_ms || p.candidate_offset + p.candidate_distance + p.safe_radius > Math.min(p.logical_width / 2, p.logical_height / 2))) fail('danger choice timing or geometry is infeasible')
  if (p.kind === 'aim' || p.kind === 'recognition') {
    const farthest = p.angle_distances_deg.length ? Math.max(...p.angle_distances_deg) : p.kind === 'aim' ? p.target_separation_deg : 0
    if (farthest + p.target_diameter_deg / 2 > p.horizontal_fov_deg / 2) fail('target is not fully visible')
  }
})
export type TaskConfig = z.infer<typeof taskConfigSchema>
export type LaneLastHitParams = Extract<TaskConfig['params'], { kind: 'lane_last_hit' }>
export type LaneTowerLastHitParams = Extract<TaskConfig['params'], { kind: 'lane_last_hit_towers' }>
export function isLaneTaskConfig(config: TaskConfig): config is TaskConfig & { params: LaneLastHitParams | LaneTowerLastHitParams } {
  return config.suite_id === 'pc-moba' && config.test_id === 'last-hit'
    && ((config.catalog_version === 'pc-moba-v4' && config.params.kind === 'lane_last_hit')
      || (config.catalog_version === 'pc-moba-v5' && config.params.kind === 'lane_last_hit_towers'))
    && taskConfigSchema.safeParse(config).success
}
/** Local trial preview only; retain frozen v1/v2 identity. Actual attempts use server configuration. */
export function previewTaskConfig(base: TaskConfig, difficulty: number, attackMode?: AttackMode): TaskConfig {
  const valid = taskConfigSchema.parse(base)
  const level = z.number().int().min(1).max(5).parse(difficulty)
  if (attackMode && !isPcMobaLaneCatalog(valid.suite_id, valid.catalog_version)) throw new Error('attack mode is only supported for lane last hit')
  return taskConfigSchema.parse({ ...valid, difficulty: level, params: registeredParams(valid.suite_id, valid.test_id, level, valid.catalog_version, attackMode ?? (isLaneTaskConfig(valid) ? valid.params.attack_mode : undefined)) })
}
export const suiteConfigSchema = z.strictObject({ suite_id: suiteIdSchema, catalog_version: z.string(), test_ids: z.array(testIdSchema).min(1).max(4), tasks: z.partialRecord(testIdSchema, taskConfigSchema) }).superRefine((v, ctx) => {
  if (!registeredCatalogVersions[v.suite_id].includes(v.catalog_version) || JSON.stringify(v.test_ids) !== JSON.stringify(suiteMembersForCatalog(v.suite_id, v.catalog_version)) || Object.keys(v.tasks).length !== v.test_ids.length || v.test_ids.some(id => v.tasks[id]?.suite_id !== v.suite_id || v.tasks[id]?.test_id !== id || v.tasks[id]?.difficulty !== 3 || v.tasks[id]?.catalog_version !== v.catalog_version)) ctx.addIssue({ code: 'custom', message: 'incompatible suite members' })
})
export type SuiteConfig = z.infer<typeof suiteConfigSchema>

const aimRawSchema = z.strictObject({ kind: z.literal('aim'), trials: z.array(z.strictObject({ hit: z.boolean(), latency_ms: time })).min(30).max(40), shots: count.max(300), wrong_target_shots: count.max(300) })
const trackingRawSchema = z.strictObject({ kind: z.literal('tracking'), coverage_ms: time, loss_count: count.max(7200), reacquisition_ms: z.array(time).max(7200) }).refine(v => v.reacquisition_ms.length <= v.loss_count && v.reacquisition_ms.reduce((a, b) => a + b, 0) + v.coverage_ms <= 60000, 'inconsistent reacquisition')
const spheretrackRawSchema = z.strictObject({ kind: z.literal('spheretrack90'), coverage_ms: time, loss_count: count.max(7200), reacquisition_ms: z.array(time).max(7200) }).refine(v => v.reacquisition_ms.length <= v.loss_count && v.reacquisition_ms.reduce((a, b) => a + b, 0) + v.coverage_ms <= 60000, 'inconsistent reacquisition')
const multiTargetRawSchema = z.strictObject({ kind: z.literal('multi_target'), shots: z.array(z.strictObject({ at_ms: nonnegative.lt(60000), hit: z.boolean() })).max(1200) }).refine(v => v.shots.every((shot, index) => index === 0 || shot.at_ms - v.shots[index - 1].at_ms >= 50), 'invalid shot interval')
const recognitionRawSchema = z.strictObject({ kind: z.literal('recognition'), trials: z.array(z.strictObject({ target_kind: z.enum(['enemy', 'friend']), shot: z.boolean(), hit: z.boolean(), latency_ms: time }).refine(t => !t.hit || t.shot, 'hit requires shot')).length(30) })
const moveFireRawSchema = z.strictObject({ kind: z.literal('move_fire'), rounds: z.array(z.strictObject({ hit: z.boolean(), reached: z.boolean() })).length(20), shots: count.max(300) })
const moveFireV3RoundSchema = z.strictObject({
  hit: z.boolean(), reached: z.boolean(), hit_latency_ms: time.nullable(),
  hit_speed_m_s: nonnegative.max(2).nullable(), pre_hit_moving_ms: time.nullable(),
}).refine(round => {
  const details = [round.hit_latency_ms, round.hit_speed_m_s, round.pre_hit_moving_ms]
  if (round.hit !== details.every(value => value !== null)) return false
  if (!round.hit) return details.every(value => value === null)
  return round.pre_hit_moving_ms! >= 100
    && round.pre_hit_moving_ms! <= round.hit_latency_ms!
    && round.hit_latency_ms! < 3000
    && round.hit_speed_m_s! >= 1
}, 'invalid moving hit evidence')
const moveFireV3RawSchema = z.strictObject({ kind: z.literal('move_fire_v3'), rounds: z.array(moveFireV3RoundSchema).length(20), shots: count.max(300) })
const lastHitRawSchema = z.strictObject({ kind: z.literal('last_hit'), waves: z.array(z.strictObject({ success: z.boolean(), early: count.max(4), late: count.max(4), offset_ms: time.nullable() }).refine(t => t.early + t.late + Number(t.success) <= 4 && t.success === (t.offset_ms !== null), 'inconsistent wave')).length(15) })
const rhythmRawSchema = z.strictObject({ kind: z.literal('rhythm'), rounds: z.array(z.strictObject({ attacked: z.boolean(), reached: z.boolean(), cancelled_count: count.max(2) })).length(30) })
const dodgeRawSchema = z.strictObject({ kind: z.literal('dodge'), threats: z.array(z.strictObject({ hit: z.boolean(), response_ms: time.nullable() })).length(30) })
const dodgeChoiceRoundSchema = z.strictObject({
  hit: z.boolean(), safe_option_id: count.max(3), warning_at_ms: count.max(60000),
  activation_at_ms: time, resolved_at_ms: time, safe_entry_latency_ms: time.nullable(),
}).refine(round => {
  if (!(round.warning_at_ms < round.activation_at_ms && round.activation_at_ms < round.resolved_at_ms)) return false
  const deadline = round.resolved_at_ms - round.warning_at_ms
  if (round.safe_entry_latency_ms !== null && round.safe_entry_latency_ms >= deadline) return false
  return round.hit || (round.safe_entry_latency_ms !== null && round.safe_entry_latency_ms <= round.activation_at_ms - round.warning_at_ms)
}, 'invalid danger choice timing')
const dodgeChoiceRawSchema = z.strictObject({ kind: z.literal('dodge_choice'), rounds: z.array(dodgeChoiceRoundSchema).length(30) })
const skillAimRawSchema = z.strictObject({ kind: z.literal('skill_aim'), trials: z.array(z.strictObject({ released: z.boolean(), hit: z.boolean(), angle_error_deg: nonnegative.max(180).nullable(), release_latency_ms: time.nullable(), hit_latency_ms: time.nullable() }).refine(t => t.released === (t.release_latency_ms !== null) && t.released === (t.angle_error_deg !== null) && t.hit === (t.hit_latency_ms !== null) && (!t.hit || (t.released && t.hit_latency_ms! >= t.release_latency_ms!)), 'inconsistent release/hit')).length(30) })
const mapRawSchema = z.strictObject({ kind: z.literal('map'), main_hits: z.array(z.boolean()).length(30), events: z.array(z.strictObject({ response: z.enum(['correct', 'wrong', 'missed']), latency_ms: time })).length(12), unprompted_count: count.max(60000) })
export const laneLastHitRawSchema = z.strictObject({ kind: z.literal('lane_last_hit'), deaths: z.array(z.strictObject({ unit_id: count.max(49), at_ms: count.lt(300000), by_player: z.boolean() })).max(50), attacks: count, cancelled_attacks: count.max(15000) }).refine(v => new Set(v.deaths.map(d => d.unit_id)).size === v.deaths.length && v.deaths.every((d, i) => d.at_ms >= Math.floor(d.unit_id / 5) * 30000 && (i === 0 || d.at_ms > v.deaths[i - 1].at_ms || (d.at_ms === v.deaths[i - 1].at_ms && d.unit_id > v.deaths[i - 1].unit_id))), 'invalid lane death sequence')
export type LaneLastHitRaw = z.infer<typeof laneLastHitRawSchema>
export const laneTowerLastHitRawSchema = z.strictObject({ kind: z.literal('lane_last_hit_towers'), deaths: z.array(z.strictObject({ unit_id: count.max(74), at_ms: count.lt(300000), by_player: z.boolean() })).max(75), attacks: count, cancelled_attacks: count.max(15000), hero_death_times: z.array(count.lt(300000)).max(60), hero_damage_taken: count.max(90000) }).refine(v => new Set(v.deaths.map(d => d.unit_id)).size === v.deaths.length && v.deaths.every((d, i) => d.at_ms >= Math.floor(d.unit_id / 5) * 20000 && (i === 0 || d.at_ms > v.deaths[i - 1].at_ms || (d.at_ms === v.deaths[i - 1].at_ms && d.unit_id > v.deaths[i - 1].unit_id))) && v.hero_death_times.every((t, i) => i === 0 || t - v.hero_death_times[i - 1] >= 5000) && v.hero_damage_taken >= v.hero_death_times.length * 1000 && v.hero_damage_taken < (v.hero_death_times.length + 1) * 1000, 'invalid tower lane life or death sequence')
export type LaneTowerLastHitRaw = z.infer<typeof laneTowerLastHitRawSchema>
export const rawResultSchema = z.discriminatedUnion('kind', [laneLastHitRawSchema, laneTowerLastHitRawSchema, aimRawSchema, trackingRawSchema, spheretrackRawSchema, multiTargetRawSchema, recognitionRawSchema, moveFireRawSchema, moveFireV3RawSchema, lastHitRawSchema, rhythmRawSchema, dodgeRawSchema, dodgeChoiceRawSchema, skillAimRawSchema, mapRawSchema])
export type RawResult = z.infer<typeof rawResultSchema>

export function validateRaw(config: TaskConfig, value: unknown): RawResult {
  const p = taskConfigSchema.parse(config).params
  const raw = rawResultSchema.parse(value)
  if (raw.kind !== p.kind) throw new Error('raw kind does not match config')
  const require = (valid: boolean) => { if (!valid) throw new Error('inconsistent raw statistics') }
  if ((raw.kind === 'lane_last_hit' || raw.kind === 'lane_last_hit_towers') && (p.kind === 'lane_last_hit' || p.kind === 'lane_last_hit_towers')) require(raw.attacks <= Math.ceil((config.duration_ms - p.attack_windup_ms) / p.attack_interval_ms) && raw.deaths.filter(d => d.by_player).length <= raw.attacks)
  if (raw.kind === 'aim' && p.kind === 'aim') {
    const hits = raw.trials.filter(t => t.hit).length
    require(raw.trials.length === p.opportunity_count && raw.shots >= hits && raw.wrong_target_shots <= raw.shots - hits && (config.test_id !== 'precision' || raw.wrong_target_shots === 0))
    require(raw.trials.every(t => t.hit ? t.latency_ms < p.window_ms : t.latency_ms === p.window_ms))
    if (config.catalog_version === `${config.suite_id}-v3` && config.test_id === 'switching') {
      for (let index = 0; index < raw.trials.length; index += 2) {
        const first = raw.trials[index], second = raw.trials[index + 1]
        require(first.hit || !second.hit)
        require(!second.hit || second.latency_ms >= p.fire_interval_ms)
      }
    }
  } else if (raw.kind === 'recognition' && p.kind === 'recognition') {
    require(raw.trials.filter(t => t.target_kind === 'enemy').length === p.enemy_count)
    require(raw.trials.every(t => t.shot ? t.latency_ms < p.window_ms : t.latency_ms === p.window_ms))
  } else if (raw.kind === 'move_fire' || raw.kind === 'move_fire_v3') require(raw.shots >= raw.rounds.filter(t => t.hit).length)
  else if (raw.kind === 'last_hit' && p.kind === 'last_hit') require(raw.waves.every(t => t.offset_ms === null || t.offset_ms < p.last_hit_window_ms))
  else if (raw.kind === 'rhythm' && p.kind === 'rhythm') require(raw.rounds.every(t => t.cancelled_count + Number(t.attacked) <= p.window_ms / p.attack_interval_ms))
  else if (raw.kind === 'dodge' && p.kind === 'dodge') require(raw.threats.every(t => t.response_ms === null || t.response_ms < p.window_ms))
  else if (raw.kind === 'dodge_choice' && p.kind === 'dodge_choice') { /* Shape and timing are fully checked by the strict schema. */ }
  else if (raw.kind === 'skill_aim' && p.kind === 'skill_aim') require(raw.trials.every(t => (t.release_latency_ms === null || t.release_latency_ms < p.window_ms) && (t.hit_latency_ms === null || t.hit_latency_ms < p.window_ms)))
  else if (raw.kind === 'map' && p.kind === 'map') require(raw.events.every(t => t.response === 'missed' ? t.latency_ms === p.event_window_ms : t.latency_ms < p.event_window_ms))
  return raw
}

export function nextU32(value: number): number {
  if (!Number.isInteger(value) || value < 0 || value > 0xffffffff) throw new Error('invalid unsigned seed')
  value = value || 0x6d2b79f5
  value ^= value << 13
  value ^= value >>> 17
  value ^= value << 5
  return value >>> 0
}
export function shuffled<T>(values: readonly T[], seed: number): T[] {
  nextU32(seed)
  const result = [...values]
  for (let i = result.length - 1; i > 0; i--) {
    seed = nextU32(seed)
    const j = seed % (i + 1)
    ;[result[i], result[j]] = [result[j], result[i]]
  }
  return result
}
export function recognitionSchedule(seed: number): ('enemy' | 'friend')[] {
  return shuffled<('enemy' | 'friend')>([...Array< 'enemy' >(15).fill('enemy'), ...Array< 'friend' >(15).fill('friend')], seed)
}
export function validateSchedule(config: TaskConfig, raw: RawResult, seed: number): void {
  nextU32(seed)
  const parsed = validateRaw(config, raw)
  if (parsed.kind === 'recognition' && parsed.trials.some((t, i) => t.target_kind !== recognitionSchedule(seed)[i])) throw new Error('recognition schedule does not match persisted seed')
  if (parsed.kind === 'dodge_choice') {
    if (config.params.kind !== 'dodge_choice') throw new Error('dodge-choice config kind mismatch')
    const plan = buildDodgeChoicePlan(seed, config.params)
    const fields = ['safe_option_id', 'warning_at_ms', 'activation_at_ms', 'resolved_at_ms'] as const
    if (parsed.rounds.some((round, index) => fields.some(field => round[field] !== plan[index][field]))) {
      throw new Error('danger choice schedule does not match persisted seed')
    }
  }
}

/** x/y: scene-relative center ratios; width/height: CSS-pixel diameters. */
export const controlRectSchema = z.strictObject({ id: z.enum(['fire', 'move', 'attack', 'skill']), kind: z.enum(['button', 'joystick']), x: nonnegative.max(1), y: nonnegative.max(1), width: positive, height: positive }).refine(c => c.width === c.height && (c.kind === 'joystick' ? c.width >= 96 && c.width <= 160 : c.width >= 48 && c.width <= 96) && (c.id === 'move') === (c.kind === 'joystick'), 'invalid control size or role')
export const controlSettingsSchema = z.strictObject({ sensitivity: finite.min(.5).max(2).refine(v => Math.abs(v * 20 - Math.round(v * 20)) < 1e-8), input_kind: z.enum(['mouse', 'touch']), mouse_mode: z.enum(['raw', 'relative']).nullable(), controls: z.array(controlRectSchema).max(3) }).refine(s => (s.input_kind !== 'mouse' || s.controls.length === 0) && (s.input_kind !== 'touch' || s.mouse_mode === null) && new Set(s.controls.map(c => c.id)).size === s.controls.length, 'incompatible input settings')
export type ControlRect = z.infer<typeof controlRectSchema>
export type ControlSettings = z.infer<typeof controlSettingsSchema>
export const environmentSnapshotSchema = z.strictObject({ input_kind: inputKindSchema, is_phone: z.boolean(), touch_capable: z.boolean(), width_css_px: positive, height_css_px: positive, device_pixel_ratio: positive.max(16), pointer_lock_supported: z.boolean(), pointer_lock_active: z.boolean(), raw_mouse_requested: z.boolean(), raw_mouse_confirmed: z.boolean(), webgl2_supported: z.boolean() })
export type EnvironmentSnapshot = z.infer<typeof environmentSnapshotSchema>
export function validateSettings(config: TaskConfig, settings: ControlSettings, environment?: EnvironmentSnapshot): void {
  settings = controlSettingsSchema.parse(settings)
  const mobile = config.suite_id.startsWith('mobile')
  if (settings.input_kind !== (mobile ? 'touch' : 'mouse') || (config.suite_id === 'pc-fps') !== (settings.mouse_mode !== null)) throw new Error('suite settings mismatch')
  const allowed = config.suite_id === 'mobile-fps' ? ['fire', 'move'] : ['move', 'attack', 'skill']
  if (mobile && settings.controls.some(c => !allowed.includes(c.id))) throw new Error('control does not belong to suite')
  if (environment) {
    environment = environmentSnapshotSchema.parse(environment)
    const e = environment
    if (e.input_kind !== settings.input_kind || e.width_css_px < (mobile ? 560 : 800) || e.height_css_px < (mobile ? 280 : 450) || (mobile && (!e.is_phone || !e.touch_capable || e.width_css_px <= e.height_css_px)) || (config.suite_id === 'pc-fps' && !e.pointer_lock_supported) || (config.suite_id.includes('fps') && !e.webgl2_supported)) throw new Error('unsupported start environment')
    const rectangles: number[][] = []
    for (const c of settings.controls) {
      const x = c.x * e.width_css_px, y = c.y * e.height_css_px
      const r = [x - c.width / 2, y - c.height / 2, x + c.width / 2, y + c.height / 2]
      if (r[0] < 0 || r[1] < 0 || r[2] > e.width_css_px || r[3] > e.height_css_px || rectangles.some(other => r[0] < other[2] && r[2] > other[0] && r[1] < other[3] && r[3] > other[1])) throw new Error('control outside scene or overlapping')
      rectangles.push(r)
    }
  }
}
export const qualityInputSchema = z.strictObject({ duration_ms: nonnegative.max(300000), max_frame_gap_ms: nonnegative.max(300000), long_frame_gap_total_ms: nonnegative.max(300000), input_kind: inputKindSchema, hidden: z.boolean(), interrupted: z.boolean() })
export type QualityInput = z.infer<typeof qualityInputSchema>
export const operationRequestSchema = z.strictObject({ operation_key: uuid })
export const sessionCreateSchema = operationRequestSchema.extend({ suite_id: suiteIdSchema, attack_mode: attackModeSchema.optional() })
export const attemptCreateSchema = operationRequestSchema.extend({ test_id: testIdSchema, settings: controlSettingsSchema, environment: environmentSnapshotSchema })
export const practiceCreateSchema = attemptCreateSchema.extend({ suite_id: suiteIdSchema, difficulty: count.min(1).max(5), attack_mode: attackModeSchema.optional() })
export const resultSubmitSchema = operationRequestSchema.extend({ raw: rawResultSchema, quality: qualityInputSchema })
export type SessionCreate = z.infer<typeof sessionCreateSchema>
export type AttemptCreate = z.infer<typeof attemptCreateSchema>
export type PracticeCreate = z.infer<typeof practiceCreateSchema>
export type ResultSubmit = z.infer<typeof resultSubmitSchema>
export const attemptViewSchema = z.strictObject({ id: uuid, mode: modeSchema, session_id: uuid.nullable(), suite_id: suiteIdSchema, test_id: testIdSchema, state: runStateSchema, server_now: date, issued_at: date, run_until: date, submit_until: date, seed: count.max(0xffffffff), config: taskConfigSchema, settings: controlSettingsSchema, environment: environmentSnapshotSchema }).superRefine((v, ctx) => {
  if ((v.mode === 'formal') !== (v.session_id !== null) || v.suite_id !== v.config.suite_id || v.test_id !== v.config.test_id || (v.mode === 'formal' && v.config.difficulty !== 3) || Date.parse(v.run_until) <= Date.parse(v.issued_at) || Date.parse(v.submit_until) <= Date.parse(v.run_until)) ctx.addIssue({ code: 'custom', message: 'invalid attempt identity or deadlines' })
  try { validateSettings(v.config, v.settings) } catch { ctx.addIssue({ code: 'custom', message: 'invalid attempt settings' }) }
})
export type AttemptView = z.infer<typeof attemptViewSchema>
export const gradeSchema = z.enum(['low', 'medium', 'high', 'exceptional'])
export type Grade = z.infer<typeof gradeSchema>
export const gradeLabels: Record<Grade, string> = { low: '较低表现', medium: '中等表现', high: '较高表现', exceptional: '超高表现' }
export const qualityFlagSchema = z.enum(['incomplete_duration', 'frame_gap', 'long_frames', 'mixed_input', 'unconfirmed_input', 'hidden', 'interrupted'])
export type QualityFlag = z.infer<typeof qualityFlagSchema>
const metricsSchema = z.record(z.string(), finite.nullable()).refine(m => Object.keys(m).length <= 32)
export const scoredResultSchema = z.strictObject({ metrics: metricsSchema, score: count.max(100), grade: gradeSchema, quality_flags: z.array(qualityFlagSchema) })
export const practiceResultSchema = z.strictObject({ metrics: metricsSchema, score: z.null(), grade: z.null(), quality_flags: z.array(qualityFlagSchema) })
export type ScoredResult = z.infer<typeof scoredResultSchema>
export type PracticeResult = z.infer<typeof practiceResultSchema>
export const sessionResultViewSchema = z.strictObject({ result_id: uuid, test_id: testIdSchema, scored: scoredResultSchema })
export const sessionViewSchema = z.strictObject({ id: uuid, suite_id: suiteIdSchema, suite_config: suiteConfigSchema, status: z.enum(['in_progress', 'completed', 'abandoned']), members: z.array(testIdSchema).min(1).max(4), results: z.array(sessionResultViewSchema).max(4), report_id: uuid.nullable() }).refine(v => v.suite_config.suite_id === v.suite_id && JSON.stringify(v.members) === JSON.stringify(v.suite_config.test_ids) && new Set(v.results.map(r => r.test_id)).size === v.results.length && v.results.every(r => v.members.includes(r.test_id)) && (v.status !== 'completed' || (v.results.length === v.members.length && v.report_id !== null)), 'inconsistent session members')
export type SessionView = z.infer<typeof sessionViewSchema>
export const resultSaveViewSchema = z.discriminatedUnion('mode', [
  z.strictObject({ mode: z.literal('formal'), result_id: uuid, scored: scoredResultSchema, session: sessionViewSchema }),
  z.strictObject({ mode: z.literal('practice'), result_id: uuid, scored: practiceResultSchema, session: z.null() }),
])
export type ResultSaveView = z.infer<typeof resultSaveViewSchema>
export const reportResultSchema = z.strictObject({ result_id: uuid, test_id: testIdSchema, config: taskConfigSchema, settings: controlSettingsSchema, environment: environmentSnapshotSchema, scored: scoredResultSchema })
export const reportComparisonSchema = z.strictObject({ test_id: testIdSchema, previous_report_id: uuid.nullable(), score_delta: z.number().int().min(-100).max(100).nullable(), non_comparable_reasons: z.array(z.string()) })
export const assessmentReportViewSchema = z.strictObject({ id: uuid, session_id: uuid, suite_id: suiteIdSchema, suite: suiteConfigSchema, person: z.strictObject({ person_id: uuid, display_name: z.string().min(1).max(200) }), completed_at: date, results: z.array(reportResultSchema).min(1).max(4), recommendations: z.array(z.string()).max(16), comparisons: z.array(reportComparisonSchema).max(4) }).refine(v => v.results.length === v.suite.test_ids.length && v.results.every((result, index) => result.test_id === v.suite.test_ids[index] && JSON.stringify(result.config) === JSON.stringify(v.suite.tasks[result.test_id])), 'inconsistent report members')
export type AssessmentReportView = z.infer<typeof assessmentReportViewSchema>
export type ReportResult = z.infer<typeof reportResultSchema>
export type ReportComparison = z.infer<typeof reportComparisonSchema>
export const pendingSubmissionSchema = resultSubmitSchema.extend({ account_id: uuid, mode: modeSchema, suite_id: suiteIdSchema, test_id: testIdSchema, attempt_id: uuid, session_id: uuid.nullable(), created_at: date }).refine(v => (v.mode === 'formal') === (v.session_id !== null) && isSupportedSuiteTest(v.suite_id, v.test_id), 'pending identity mismatch')
export type PendingSubmission = z.infer<typeof pendingSubmissionSchema>
