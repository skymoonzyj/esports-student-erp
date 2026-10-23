import { describe, expect, it } from 'vitest'
import golden from './testSupport/task-1-golden.json'
import { previewTaskConfig, taskConfigSchema, validateRaw, validateSchedule } from './contracts'
import { buildDodgeChoicePlan } from './ruleSchedule'
import { registeredRule } from './versionRules'
import { fixtureConfig, fixtureQuality, fixtureRaw, fixtureV3Config, fixtureV3Raw } from './testSupport/fixtures'

const legacyCases = [
  ['pc-fps', 'switching', 'mouse'],
  ['mobile-fps', 'move-fire', 'touch'],
  ['pc-moba', 'dodge', 'mouse'],
] as const

describe('v3 frontend contracts', () => {
  it('parses every frozen v3 task and difficulty', () => {
    for (const levels of Object.values(golden.v3_tasks_by_suite_and_difficulty)) {
      for (const tasks of Object.values(levels)) {
        for (const task of Object.values(tasks)) expect(taskConfigSchema.parse(task)).toEqual(task)
      }
    }
    expect(fixtureV3Config('mobile-fps', 'move-fire').params.kind).toBe('move_fire_v3')
  })

  it('previews the exact frozen v3 parameters without rebinding legacy fixtures', () => {
    for (const [suite, levels] of Object.entries(golden.v3_tasks_by_suite_and_difficulty)) {
      for (const [difficulty, tasks] of Object.entries(levels)) {
        for (const [test, expected] of Object.entries(tasks)) {
          expect(previewTaskConfig(fixtureV3Config(suite, test), Number(difficulty))).toEqual(expected)
          expect(fixtureConfig(suite, test).catalog_version).toBe(`${suite}-v1`)
        }
      }
    }
  })

  it('exposes exact registered rule identities and rejects fall-forward versions', () => {
    expect(registeredRule('pc-fps', 'switching', 'pc-fps-v3')).toEqual({ raw_kind: 'aim', scoring_version: 'pc-fps-v2', report_version: 'pc-fps-v2' })
    expect(registeredRule('mobile-fps', 'move-fire', 'mobile-fps-v3')).toEqual({ raw_kind: 'move_fire_v3', scoring_version: 'mobile-fps-v2', report_version: 'mobile-fps-v2' })
    expect(registeredRule('pc-moba', 'dodge', 'pc-moba-v3')).toEqual({ raw_kind: 'dodge_choice', scoring_version: 'pc-moba-v2', report_version: 'pc-moba-v2' })
    expect(registeredRule('pc-fps', 'precision', 'pc-fps-v3')).toEqual({ raw_kind: 'aim', scoring_version: 'pc-fps-v1', report_version: 'pc-fps-v1' })
    for (const version of ['', 'pc-fps-v4', 'mobile-fps-v3']) expect(() => registeredRule('pc-fps', 'precision', version)).toThrow()
  })

  it.each(legacyCases)('keeps the %s/%s legacy submission JSON frozen', (suite, test, input) => {
    const key = `${suite}/${test}` as keyof typeof golden.legacy_submission_goldens
    const payload = {
      operation_key: '11111111-1111-4111-8111-111111111111',
      raw: fixtureRaw(suite, test),
      quality: fixtureQuality(input),
    }
    expect(JSON.stringify(payload)).toBe(JSON.stringify(JSON.parse(golden.legacy_submission_goldens[key].json)))
    expect(fixtureConfig(suite, test).catalog_version).toBe(`${suite}-v1`)
  })

  it('does not accept legacy moving statistics for v3', () => {
    const config = fixtureV3Config('mobile-fps', 'move-fire')
    expect(config.params.kind).toBe('move_fire_v3')
    expect(() => validateRaw(config, fixtureRaw('mobile-fps', 'move-fire'))).toThrow()
    expect(() => validateRaw(fixtureConfig('mobile-fps', 'move-fire'), fixtureV3Raw('mobile-fps', 'move-fire'))).toThrow()
  })

  it('builds valid v3 fixtures for every suite member without changing legacy fixture aliases', () => {
    for (const [suite, levels] of Object.entries(golden.v3_tasks_by_suite_and_difficulty)) {
      for (const test of Object.keys(levels['3'])) {
        for (const perfect of [true, false]) {
          const config = fixtureV3Config(suite, test)
          const raw = fixtureV3Raw(suite, test, perfect, 123)
          expect(() => validateSchedule(config, raw, 123)).not.toThrow()
        }
      }
    }
  })

  it('requires complete bounded movement evidence for every v3 hit', () => {
    const config = fixtureV3Config('mobile-fps', 'move-fire')
    const raw = fixtureV3Raw('mobile-fps', 'move-fire')
    expect(() => validateRaw(config, raw)).not.toThrow()
    if (raw.kind !== 'move_fire_v3') throw new Error('move-fire-v3 fixture kind')
    for (const patch of [
      { pre_hit_moving_ms: undefined },
      { pre_hit_moving_ms: 99 },
      { hit_speed_m_s: .999 },
      { hit_latency_ms: 3000 },
      { hit: false },
    ]) {
      const rounds = [{ ...raw.rounds[0], ...patch }, ...raw.rounds.slice(1)]
      expect(() => validateRaw(config, { ...raw, rounds })).toThrow()
    }
  })

  it('rejects impossible v3 switching pairs without changing the aim raw kind', () => {
    const config = fixtureV3Config('pc-fps', 'switching')
    const raw = fixtureV3Raw('pc-fps', 'switching')
    expect(raw.kind).toBe('aim')
    expect(() => validateRaw(config, raw)).not.toThrow()
    if (raw.kind !== 'aim') throw new Error('aim fixture kind')
    const missedFirst = raw.trials.map((trial, index) => index === 0 ? { hit: false, latency_ms: 1500 } : trial)
    expect(() => validateRaw(config, { ...raw, trials: missedFirst })).toThrow()
    const earlySecond = raw.trials.map((trial, index) => index === 1 ? { hit: true, latency_ms: 199 } : trial)
    expect(() => validateRaw(config, { ...raw, trials: earlySecond })).toThrow()
  })

  it.each([0, 123, 0xffffffff])('matches every backend dodge-choice event field for seed %s', seed => {
    const config = fixtureV3Config('pc-moba', 'dodge')
    if (config.params.kind !== 'dodge_choice') throw new Error('dodge-choice config kind')
    expect(buildDodgeChoicePlan(seed, config.params)).toEqual(
      golden.dodge_choice_schedule_warning_ms_1000[String(seed) as '0' | '123' | '4294967295'],
    )
  })

  it('requires complete seeded dodge-choice evidence and rejects legacy threats', () => {
    const config = fixtureV3Config('pc-moba', 'dodge')
    const raw = fixtureV3Raw('pc-moba', 'dodge', true, 123)
    expect(() => validateSchedule(config, raw, 123)).not.toThrow()
    expect(() => validateRaw(config, fixtureRaw('pc-moba', 'dodge'))).toThrow()
    expect(() => validateRaw(fixtureConfig('pc-moba', 'dodge'), raw)).toThrow()
    if (raw.kind !== 'dodge_choice') throw new Error('dodge-choice fixture kind')
    const withoutPlanField = raw.rounds.map((round, index) => index === 0
      ? { hit: round.hit, safe_option_id: round.safe_option_id, warning_at_ms: round.warning_at_ms, activation_at_ms: round.activation_at_ms, safe_entry_latency_ms: round.safe_entry_latency_ms }
      : round)
    expect(() => validateRaw(config, { ...raw, rounds: withoutPlanField })).toThrow()
    const changedPlan = raw.rounds.map((round, index) => index === 0 ? { ...round, safe_option_id: 3 } : round)
    expect(() => validateSchedule(config, { ...raw, rounds: changedPlan }, 123)).toThrow()
  })

  it('enforces dodge-choice entry and event ordering boundaries', () => {
    const config = fixtureV3Config('mobile-moba', 'dodge')
    const raw = fixtureV3Raw('mobile-moba', 'dodge', true)
    if (raw.kind !== 'dodge_choice') throw new Error('dodge-choice fixture kind')
    const first = raw.rounds[0]
    const invalidRounds = [
      { ...first, warning_at_ms: first.activation_at_ms },
      { ...first, activation_at_ms: first.resolved_at_ms },
      { ...first, safe_entry_latency_ms: first.resolved_at_ms - first.warning_at_ms },
      { ...first, hit: false, safe_entry_latency_ms: first.activation_at_ms - first.warning_at_ms + 1 },
      { ...first, safe_option_id: 4 },
    ]
    for (const invalid of invalidRounds) {
      expect(() => validateRaw(config, { ...raw, rounds: [invalid, ...raw.rounds.slice(1)] })).toThrow()
    }
  })
})
