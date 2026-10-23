import { describe, expect, it } from 'vitest'
import {
  taskConfigSchema, rawResultSchema, qualityInputSchema, resultSaveViewSchema,
  validateRaw, nextU32, recognitionSchedule, validateSchedule, gradeLabels,
  validateSettings, controlSettingsSchema,
  suiteConfigSchema,
} from '../contracts'
import { fixtureConfig, fixtureRaw, fixtureQuality, fixturePending, fixtureAttempt, fixtureSettings, fixtureEnvironment } from './fixtures'
import catalog from './catalog-v1.json'

const members = {
  'pc-fps': ['precision', 'tracking', 'switching', 'recognition'],
  'mobile-fps': ['precision', 'tracking', 'switching', 'move-fire'],
  'pc-moba': ['last-hit', 'orb-walk', 'dodge', 'map-awareness'],
  'mobile-moba': ['dodge', 'skill-aim', 'move-attack', 'map-awareness'],
} as const

describe('strict shared wire contracts', () => {
  it('roundtrips suites regardless of JSON object key order', () => {
    for (const source of Object.values(catalog)) {
      const reversed = { ...source, tasks: Object.fromEntries(Object.entries(source.tasks).reverse()) }
      const restored = suiteConfigSchema.parse(JSON.parse(JSON.stringify(reversed)))
      expect(restored).toEqual(suiteConfigSchema.parse(source))
      expect(restored.test_ids).toEqual(source.test_ids)
    }
  })
  it.each(['1' + '0'.repeat(400), '-1' + '0'.repeat(400)])('rejects oversized JSON numeric syntax %s', literal => {
    const value: unknown = JSON.parse(literal)
    expect(qualityInputSchema.safeParse({ ...fixtureQuality('mouse'), duration_ms: value }).success).toBe(false)
    expect(controlSettingsSchema.safeParse({ ...fixtureSettings('pc-fps'), sensitivity: value }).success).toBe(false)
  })
  it('parses actual configs and raw results for all sixteen scenes', () => {
    for (const [suite, tests] of Object.entries(members)) {
      for (const test of tests) {
        const config = fixtureConfig(suite, test)
        for (const perfect of [true, false]) {
          const raw = validateRaw(config, fixtureRaw(suite, test, perfect))
          validateSchedule(config, raw, 123)
          expect(raw.kind).toBe(config.params.kind)
        }
      }
    }
  })
  it.each([true, Infinity, NaN, '60000', -1])('rejects invalid time %s', value => {
    expect(qualityInputSchema.safeParse({ ...fixtureQuality('mouse'), duration_ms: value }).success).toBe(false)
  })
  it('rejects wrong versions, alien members and unknown nested fields', () => {
    const config = fixtureConfig('pc-fps', 'precision')
    for (const invalid of [
      { ...config, catalog_version: 'v999' },
      { ...config, test_id: 'last-hit' },
      { ...config, duration_ms: true },
      { ...config, params: { ...config.params, target_diameter_deg: Infinity } },
      { ...config, params: { ...config.params, cheats: true } },
    ]) expect(taskConfigSchema.safeParse(invalid).success).toBe(false)
  })
  it('retains all 42 map opportunities and rejects a missing secondary channel', () => {
    const config = fixtureConfig('pc-moba', 'map-awareness')
    const raw = fixtureRaw('pc-moba', 'map-awareness', true)
    if (raw.kind !== 'map') throw new Error('fixture kind')
    expect(raw.main_hits.length + raw.events.length).toBe(42)
    expect(() => validateRaw(config, { ...raw, events: raw.events.slice(1) })).toThrow()
  })
  it('checks live shot counts and timeout latencies, not only the raw shape', () => {
    const config = fixtureConfig('pc-fps', 'precision')
    const raw = fixtureRaw('pc-fps', 'precision', true)
    expect(() => validateRaw(config, { ...raw, shots: 29 })).toThrow()
    expect(rawResultSchema.safeParse({ ...raw, score: 100 }).success).toBe(false)
    if (raw.kind !== 'aim') throw new Error('fixture kind')
    expect(() => validateRaw(config, { ...raw, trials: [{ hit: false, latency_ms: 100 }, ...raw.trials.slice(1)] })).toThrow()
  })
  it('rejects changed rules under an existing version label', () => {
    const config = fixtureConfig('pc-fps', 'precision')
    expect(taskConfigSchema.safeParse({ ...config, params: { ...config.params, opportunity_count: 39 } }).success).toBe(false)
    expect(taskConfigSchema.safeParse({ ...config, params: { ...config.params, fire_interval_ms: 1 } }).success).toBe(false)
  })
  it('does not accept score or grade in practice save responses', () => {
    const result = { mode: 'practice', result_id: '11111111-1111-4111-8111-111111111111', scored: { metrics: {}, score: null, grade: null, quality_flags: [] }, session: null }
    expect(resultSaveViewSchema.safeParse(result).success).toBe(true)
    expect(resultSaveViewSchema.safeParse({ ...result, scored: { ...result.scored, score: 100, grade: 'exceptional' } }).success).toBe(false)
    expect(gradeLabels.exceptional).toBe('超高表现')
  })
  it('creates complete pending and attempt fixtures with independent values', () => {
    expect(fixturePending().raw).toEqual(fixtureRaw('pc-fps', 'precision', false))
    const attempt = fixtureAttempt()
    expect(Date.parse(attempt.run_until) - Date.parse(attempt.issued_at)).toBe(90000)
    expect(Date.parse(attempt.submit_until) - Date.parse(attempt.issued_at)).toBe(1800000)
    expect(attempt.server_now).toBe(attempt.issued_at)
    expect(fixtureSettings('mobile-fps').controls).toHaveLength(2)
  })
  it.each([
    [0, [1085196063, 2447379481, 2618286376, 1701901981, 265159372, 1030440423, 4012273292, 2080899351, 4184948546, 230363598]],
    [123, [31682556, 4018661298, 2101636938, 3842487452, 1628673942, 1875546790, 2388115785, 3114636470, 1834579151, 1003884751]],
  ] as const)('matches backend unsigned RNG seed %s', (seed, expected) => {
    let state: number = seed
    expect(Array.from({ length: 10 }, () => (state = nextU32(state)))).toEqual(expected)
  })
  it('validates recognition sequence against the persisted seed', () => {
    const config = fixtureConfig('pc-fps', 'recognition')
    const raw = fixtureRaw('pc-fps', 'recognition', true)
    if (raw.kind !== 'recognition') throw new Error('fixture kind')
    expect(raw.trials.map(t => t.target_kind)).toEqual(recognitionSchedule(123))
    expect(() => validateSchedule(config, raw, 124)).toThrow()
  })
  it('validates actual rectangle placement and suite-specific controls', () => {
    const config = fixtureConfig('mobile-fps', 'move-fire')
    const settings = fixtureSettings('mobile-fps')
    validateSettings(config, settings, fixtureEnvironment('mobile-fps'))
    for (const x of [0, .15]) {
      const invalid = structuredClone(settings)
      invalid.controls[1].x = x
      expect(() => validateSettings(config, invalid, fixtureEnvironment('mobile-fps'))).toThrow()
    }
    expect(() => validateSettings(fixtureConfig('mobile-moba', 'dodge'), settings)).toThrow()
    expect(() => validateSettings(fixtureConfig('pc-moba', 'dodge'), fixtureSettings('pc-fps'))).toThrow()
    expect(controlSettingsSchema.safeParse({ ...settings, sensitivity: 1.03 }).success).toBe(false)
  })
  it('keeps valid last-moment hits distinct from end-position reached flags', () => {
    const config = fixtureConfig('pc-fps', 'precision')
    const raw = fixtureRaw('pc-fps', 'precision', true)
    if (raw.kind !== 'aim') throw new Error('fixture kind')
    raw.trials[0].latency_ms = 1999.999
    validateRaw(config, raw)
    raw.trials[0].latency_ms = 2000
    expect(() => validateRaw(config, raw)).toThrow()
    const rhythm = fixtureRaw('pc-moba', 'orb-walk', false)
    if (rhythm.kind !== 'rhythm') throw new Error('fixture kind')
    rhythm.rounds[0].reached = true
    validateRaw(fixtureConfig('pc-moba', 'orb-walk'), rhythm)
  })
  it('rejects inconsistent releases, attack counts and map timeouts', () => {
    const skill = fixtureRaw('mobile-moba', 'skill-aim', true)
    if (skill.kind !== 'skill_aim') throw new Error('fixture kind')
    skill.trials[0].hit_latency_ms = 99
    expect(() => validateRaw(fixtureConfig('mobile-moba', 'skill-aim'), skill)).toThrow()
    const rhythm = fixtureRaw('pc-moba', 'orb-walk', true)
    if (rhythm.kind !== 'rhythm') throw new Error('fixture kind')
    rhythm.rounds[0].cancelled_count = 2
    expect(() => validateRaw(fixtureConfig('pc-moba', 'orb-walk'), rhythm)).toThrow()
    const map = fixtureRaw('pc-moba', 'map-awareness', false)
    if (map.kind !== 'map') throw new Error('fixture kind')
    map.events[0].latency_ms = 999
    expect(() => validateRaw(fixtureConfig('pc-moba', 'map-awareness'), map)).toThrow()
  })
})
