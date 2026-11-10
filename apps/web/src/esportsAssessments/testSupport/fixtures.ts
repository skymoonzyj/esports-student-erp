import catalog from './catalog-v1.json' with { type: 'json' }
import currentCatalog from './catalog-v2.json' with { type: 'json' }
import catalogV3 from './catalog-v3.json' with { type: 'json' }
import {
  attemptViewSchema, controlSettingsSchema, environmentSnapshotSchema, pendingSubmissionSchema,
  qualityInputSchema, rawResultSchema, recognitionSchedule, suiteConfigSchema, suiteIdSchema,
  testIdSchema, type AttemptView, type ControlSettings, type EnvironmentSnapshot, type InputKind,
  type PendingSubmission, type QualityInput, type RawResult, type TaskConfig,
} from '../contracts'
import { buildDodgeChoicePlan } from '../ruleSchedule'

export function fixtureConfig(suite_id: string, test_id: string): TaskConfig {
  const suite = suiteIdSchema.parse(suite_id)
  const test = testIdSchema.parse(test_id)
  const config = suiteConfigSchema.parse(catalog[suite]).tasks[test]
  if (!config) throw new Error('test is not a suite member')
  return config
}

/** Latest new-run configuration; fixtureConfig intentionally remains frozen at v1. */
export function fixtureCurrentConfig(suite_id: string, test_id: string): TaskConfig {
  const suite = suiteIdSchema.parse(suite_id)
  const test = testIdSchema.parse(test_id)
  const config = suiteConfigSchema.parse(currentCatalog[suite]).tasks[test]
  if (!config) throw new Error('test is not a suite member')
  return config
}

/** Explicit future-rule fixture; v1/current aliases above remain frozen. */
export function fixtureV3Config(suite_id: string, test_id: string): TaskConfig {
  const suite = suiteIdSchema.parse(suite_id)
  const test = testIdSchema.parse(test_id)
  const config = suiteConfigSchema.parse(catalogV3[suite]).tasks[test]
  if (!config) throw new Error('test is not a suite member')
  return config
}

export function fixtureSuiteConfig(suite_id: string, version: 'v1' | 'current' = 'v1') {
  const suite = suiteIdSchema.parse(suite_id)
  return suiteConfigSchema.parse((version === 'v1' ? catalog : currentCatalog)[suite])
}

export function fixtureSettings(suite_id: string): ControlSettings {
  const suite = suiteIdSchema.parse(suite_id)
  const mobile = suite.startsWith('mobile')
  return controlSettingsSchema.parse({
    sensitivity: 1, input_kind: mobile ? 'touch' : 'mouse', mouse_mode: suite === 'pc-fps' ? 'relative' : null,
    controls: !mobile ? [] : suite === 'mobile-fps' ? [
      { id: 'move', kind: 'joystick', x: .15, y: .75, width: 120, height: 120 },
      { id: 'fire', kind: 'button', x: .85, y: .75, width: 64, height: 64 },
    ] : [
      { id: 'move', kind: 'joystick', x: .15, y: .75, width: 120, height: 120 },
      { id: 'attack', kind: 'button', x: .85, y: .75, width: 64, height: 64 },
      { id: 'skill', kind: 'button', x: .7, y: .65, width: 64, height: 64 },
    ],
  })
}

export function fixtureEnvironment(suite_id: string = 'pc-fps'): EnvironmentSnapshot {
  const mobile = suiteIdSchema.parse(suite_id).startsWith('mobile')
  return environmentSnapshotSchema.parse({ input_kind: mobile ? 'touch' : 'mouse', is_phone: mobile, touch_capable: mobile, width_css_px: 1000, height_css_px: 562.5, device_pixel_ratio: 1, pointer_lock_supported: !mobile, pointer_lock_active: !mobile, raw_mouse_requested: false, raw_mouse_confirmed: false, webgl2_supported: true })
}

export function fixtureQuality(input_kind: InputKind = 'mouse'): QualityInput {
  return qualityInputSchema.parse({ duration_ms: 60000, max_frame_gap_ms: 16, long_frame_gap_total_ms: 0, input_kind, hidden: false, interrupted: false })
}

export function fixtureRaw(suite_id: string, test_id: string, perfect: boolean = true, seed: number = 123): RawResult {
  fixtureConfig(suite_id, test_id)
  const repeat = <T>(count: number, make: () => T): T[] => Array.from({ length: count }, make)
  let raw: unknown
  switch (test_id) {
    case 'precision': case 'switching': {
      const count = test_id === 'precision' ? 30 : 40
      raw = { kind: 'aim', trials: repeat(count, () => ({ hit: perfect, latency_ms: perfect ? 100 : test_id === 'precision' ? 2000 : 1500 })), shots: perfect ? count : 0, wrong_target_shots: 0 }
      break
    }
    case 'tracking': raw = { kind: 'tracking', coverage_ms: perfect ? 60000 : 0, loss_count: 0, reacquisition_ms: [] }; break
    case 'recognition': raw = { kind: 'recognition', trials: recognitionSchedule(seed).map(target_kind => ({ target_kind, shot: perfect && target_kind === 'enemy', hit: perfect && target_kind === 'enemy', latency_ms: perfect && target_kind === 'enemy' ? 100 : 1200 })) }; break
    case 'move-fire': raw = { kind: 'move_fire', rounds: repeat(20, () => ({ hit: perfect, reached: perfect })), shots: perfect ? 20 : 0 }; break
    case 'last-hit': raw = { kind: 'last_hit', waves: repeat(15, () => ({ success: perfect, early: 0, late: 0, offset_ms: perfect ? 0 : null })) }; break
    case 'orb-walk': case 'move-attack': raw = { kind: 'rhythm', rounds: repeat(30, () => ({ attacked: perfect, reached: perfect, cancelled_count: 0 })) }; break
    case 'dodge': raw = { kind: 'dodge', threats: repeat(30, () => ({ hit: !perfect, response_ms: perfect ? 100 : null })) }; break
    case 'skill-aim': raw = { kind: 'skill_aim', trials: repeat(30, () => ({ released: perfect, hit: perfect, angle_error_deg: perfect ? 0 : null, release_latency_ms: perfect ? 100 : null, hit_latency_ms: perfect ? 300 : null })) }; break
    case 'map-awareness': raw = { kind: 'map', main_hits: repeat(30, () => perfect), events: repeat(12, () => ({ response: perfect ? 'correct' : 'missed', latency_ms: perfect ? 100 : 1000 })), unprompted_count: 0 }; break
    default: throw new Error('unknown test')
  }
  return rawResultSchema.parse(raw)
}

export function fixtureV3Raw(suite_id: string, test_id: string, perfect: boolean = true, seed: number = 123): RawResult {
  const config = fixtureV3Config(suite_id, test_id)
  const repeat = <T>(count: number, make: () => T): T[] => Array.from({ length: count }, make)
  if (config.params.kind === 'move_fire_v3') {
    return rawResultSchema.parse({
      kind: 'move_fire_v3',
      rounds: repeat(20, () => ({
        hit: perfect,
        reached: perfect,
        hit_latency_ms: perfect ? 500 : null,
        hit_speed_m_s: perfect ? 2 : null,
        pre_hit_moving_ms: perfect ? 100 : null,
      })),
      shots: perfect ? 20 : 0,
    })
  }
  if (config.params.kind === 'dodge_choice') {
    return rawResultSchema.parse({
      kind: 'dodge_choice',
      rounds: buildDodgeChoicePlan(seed, config.params).map(event => ({
        ...event,
        hit: !perfect,
        safe_entry_latency_ms: perfect ? 100 : null,
      })),
    })
  }
  const raw = structuredClone(fixtureRaw(suite_id, test_id, perfect, seed))
  if (test_id === 'switching' && perfect && raw.kind === 'aim') {
    for (let index = 1; index < raw.trials.length; index += 2) raw.trials[index].latency_ms = 200
  }
  return rawResultSchema.parse(raw)
}

export function fixturePending(overrides: Partial<PendingSubmission> = {}): PendingSubmission {
  return pendingSubmissionSchema.parse({ account_id: '11111111-1111-4111-8111-111111111111', attempt_id: '22222222-2222-4222-8222-222222222222', session_id: '33333333-3333-4333-8333-333333333333', operation_key: '44444444-4444-4444-8444-444444444444', mode: 'formal', suite_id: 'pc-fps', test_id: 'precision', raw: fixtureRaw('pc-fps', 'precision', false), quality: fixtureQuality('mouse'), created_at: '2026-09-21T00:00:00.000Z', ...overrides })
}

export function fixtureAttempt(overrides: Partial<AttemptView> = {}): AttemptView {
  return attemptViewSchema.parse({ id: '22222222-2222-4222-8222-222222222222', mode: 'formal', session_id: '33333333-3333-4333-8333-333333333333', suite_id: 'pc-fps', test_id: 'precision', state: 'running', server_now: '2026-09-21T00:00:00.000Z', issued_at: '2026-09-21T00:00:00.000Z', run_until: '2026-09-21T00:01:30.000Z', submit_until: '2026-09-21T00:30:00.000Z', seed: 123, config: fixtureConfig('pc-fps', 'precision'), settings: fixtureSettings('pc-fps'), environment: fixtureEnvironment(), ...overrides })
}
