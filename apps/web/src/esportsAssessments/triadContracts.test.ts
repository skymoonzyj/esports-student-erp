import { expect, it } from 'vitest'
import { assessmentReportViewSchema, pendingSubmissionSchema, previewTaskConfig, rawResultSchema, sessionViewSchema, suiteConfigSchema, taskConfigSchema, validateRaw } from './contracts'
import { recoveryItemSchema } from './apiContracts'
import { fixtureConfig, fixtureCurrentConfig, fixtureEnvironment, fixturePending, fixtureSettings, fixtureV3Config } from './testSupport/fixtures'

const task = (test_id = 'gridshot') => ({
  suite_id: 'pc-fps', test_id, catalog_version: 'pc-fps-v4', engine_version: 'pc-fps-v4', scoring_version: 'pc-fps-v4', report_version: 'pc-fps-v4', duration_ms: 60000, difficulty: 3,
  params: test_id === 'spheretrack90'
    ? { kind: 'spheretrack90', horizontal_fov_deg: 90, pitch_limit_deg: 70, fire_interval_ms: 200, target_diameter_deg: 3, speed_deg_s: 30, horizontal_bound_deg: 40, vertical_bound_deg: 15, direction_interval_ms: 2000, turn_ms: 200 }
    : { kind: 'multi_target', horizontal_fov_deg: 90, pitch_limit_deg: 70, fire_interval_ms: 50, target_count: test_id === 'gridshot' ? 3 : 6, target_diameter_deg: test_id === 'gridshot' ? 6 : 1.5, grid_columns: 5, grid_rows: test_id === 'gridshot' ? 3 : 5, grid_spacing_deg: test_id === 'gridshot' ? 10 : 6, reference_hits: test_id === 'gridshot' ? 300 : 150 },
})
const ids = ['gridshot', 'sixshot', 'spheretrack90']
const suite = () => ({ suite_id: 'pc-fps', catalog_version: 'pc-fps-v4', test_ids: ids, tasks: Object.fromEntries(ids.map(id => [id, task(id)])) })

it('accepts the three-item v4 catalog while keeping v1-v3 exact historical configurations', () => {
  expect(suiteConfigSchema.safeParse(suite()).success).toBe(true)
  for (const make of [fixtureConfig, fixtureCurrentConfig, fixtureV3Config]) expect(taskConfigSchema.safeParse(make('pc-fps', 'precision')).success).toBe(true)
  expect(suiteConfigSchema.safeParse({ ...suite(), test_ids: [...ids, 'precision'] }).success).toBe(false)
  expect(taskConfigSchema.safeParse({ ...task(), test_id: 'precision' }).success).toBe(false)
  expect(taskConfigSchema.safeParse({ ...task(), suite_id: 'mobile-fps' }).success).toBe(false)
})

it('uses exact registered practice size/speed transformations without changing reference hits', () => {
  const grid = previewTaskConfig(taskConfigSchema.parse(task()), 5)
  expect(grid.params).toMatchObject({ reference_hits: 300, fire_interval_ms: 50 })
  expect('target_diameter_deg' in grid.params && grid.params.target_diameter_deg).toBeCloseTo(4.2)
  expect(previewTaskConfig(taskConfigSchema.parse(task('spheretrack90')), 1).params).toMatchObject({ target_diameter_deg: 4.5, speed_deg_s: 18, horizontal_bound_deg: 40 })
  expect(taskConfigSchema.safeParse({ ...task(), params: { ...task().params, reference_hits: 999 } }).success).toBe(false)
})

it('validates point-shot raw timestamps and no-fire tracking independently of old raw kinds', () => {
  const config = taskConfigSchema.parse(task())
  expect(validateRaw(config, { kind: 'multi_target', shots: [{ at_ms: 0, hit: true }, { at_ms: 50, hit: false }, { at_ms: 59999, hit: true }] }).kind).toBe('multi_target')
  expect(validateRaw(config, { kind: 'multi_target', shots: [] }).kind).toBe('multi_target')
  for (const shots of [[{ at_ms: 60000, hit: true }], [{ at_ms: -1, hit: false }], [{ at_ms: 0, hit: true }, { at_ms: 49, hit: true }], Array.from({ length: 1201 }, (_, index) => ({ at_ms: index * 50, hit: true }))]) {
    expect(rawResultSchema.safeParse({ kind: 'multi_target', shots }).success).toBe(false)
  }
  expect(validateRaw(taskConfigSchema.parse(task('spheretrack90')), { kind: 'spheretrack90', coverage_ms: 60000, loss_count: 0, reacquisition_ms: [] }).kind).toBe('spheretrack90')
  expect(rawResultSchema.safeParse({ kind: 'spheretrack90', coverage_ms: 59999, loss_count: 1, reacquisition_ms: [2] }).success).toBe(false)
})

it('accepts completed three-item sessions and reports but rejects missing/duplicate results', () => {
  const scored = { metrics: {}, score: 50, grade: 'medium', quality_flags: [] }
  const session = { id: '33333333-3333-4333-8333-333333333333', suite_id: 'pc-fps', suite_config: suite(), status: 'completed', members: ids, results: ids.map((test_id, index) => ({ test_id, result_id: `55555555-5555-4555-8555-55555555555${index}`, scored })), report_id: '66666666-6666-4666-8666-666666666666' }
  expect(sessionViewSchema.safeParse(session).success).toBe(true)
  expect(sessionViewSchema.safeParse({ ...session, results: session.results.slice(0, 2) }).success).toBe(false)
  const report = { id: session.report_id, session_id: session.id, suite_id: 'pc-fps', suite: suite(), person: { person_id: session.id, display_name: '合成测试' }, completed_at: '2026-11-08T00:00:00Z', results: session.results.map(result => ({ ...result, config: task(result.test_id), settings: fixtureSettings('pc-fps'), environment: fixtureEnvironment() })), recommendations: [], comparisons: [] }
  expect(assessmentReportViewSchema.safeParse(report).success).toBe(true)
  expect(assessmentReportViewSchema.safeParse({ ...report, results: [report.results[0], report.results[0], report.results[2]] }).success).toBe(false)
})

it('preserves versionless pending/recovery for historical and v4 IDs without accepting cross-suite tests', () => {
  const pending = fixturePending()
  for (const test_id of ['precision', 'tracking', 'switching', 'recognition', ...ids]) {
    expect(pendingSubmissionSchema.safeParse({ ...pending, test_id }).success).toBe(true)
    expect(recoveryItemSchema.safeParse({ mode: pending.mode, attempt_id: pending.attempt_id, session_id: pending.session_id, suite_id: 'pc-fps', test_id, state: 'running', issued_at: '2026-11-08T00:00:00Z', run_until: '2026-11-08T00:01:00Z', submit_until: '2026-11-08T00:30:00Z' }).success).toBe(true)
  }
  expect(pendingSubmissionSchema.safeParse({ ...pending, suite_id: 'mobile-fps', test_id: 'gridshot' }).success).toBe(false)
})
