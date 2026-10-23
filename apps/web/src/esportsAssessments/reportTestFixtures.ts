// Test-only complete wire fixtures. Production modules must not import this file.
import { assessmentReportViewSchema } from './contracts'
import { practiceHistoryItemSchema } from './apiContracts'
import { fixtureConfig, fixtureEnvironment, fixtureSettings } from './testSupport/fixtures'

export const PERSON_ID = '77777777-7777-4777-8777-777777777777'
export const REPORT_ID = '99999999-9999-4999-8999-999999999999'
export const PREVIOUS_ID = '99999999-9999-4999-8999-999999999998'
export const SESSION_ID = '33333333-3333-4333-8333-333333333333'
export const reportContext = (self = true) => ({ server_now: '2026-09-22T00:00:00Z', can_take: self, can_read_self: self, can_read_reports: true, available_suites: [], own_in_progress_sessions: [], active_run: null })
export const jsonResponse = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } })
export const privateResponse = () => jsonResponse({ detail: '不得泄露的姓名', code: 'esports_not_found' }, 404)
export function reportFixture(id = REPORT_ID) {
  const tests = ['precision', 'tracking', 'switching', 'recognition'] as const
  return assessmentReportViewSchema.parse({
    id, session_id: SESSION_ID, suite_id: 'pc-fps',
    suite: { suite_id: 'pc-fps', catalog_version: 'pc-fps-v1', test_ids: tests, tasks: Object.fromEntries(tests.map(test => [test, fixtureConfig('pc-fps', test)])) },
    person: { person_id: PERSON_ID, display_name: '报告合成学员' }, completed_at: '2026-09-21T18:30:00Z',
    results: tests.map((test, index) => ({ result_id: `00000000-0000-4000-8000-${String(index + 900).padStart(12, '0')}`, test_id: test, config: fixtureConfig('pc-fps', test), settings: fixtureSettings('pc-fps'), environment: fixtureEnvironment(), scored: { metrics: test === 'tracking' ? { coverage_ms: 30000, coverage_rate: .5, loss_count: 4, average_reacquisition_ms: null } : { opportunities: 30, hits: 15, hit_rate: .5, successful_average_latency_ms: 240 }, score: 50, grade: 'medium', quality_flags: [] } })),
    recommendations: ['服务端冻结的训练建议'], comparisons: [],
  })
}
export function practiceFixture(id = '22222222-2222-4222-8222-222222222222') {
  return practiceHistoryItemSchema.parse({ mode: 'practice', attempt_id: id, session_id: null, result_id: id, suite_id: 'pc-fps', test_id: 'precision', completed_at: '2026-09-21T18:30:00Z', config: fixtureConfig('pc-fps', 'precision'), settings: fixtureSettings('pc-fps'), environment: fixtureEnvironment(), scored: { metrics: { hits: 15, hit_rate: .5, average_latency_ms: 240, successful_average_latency_ms: null }, score: null, grade: null, quality_flags: [] }, report_id: null })
}
export function summaryFixture(id = REPORT_ID) {
  const { session_id, suite_id, person, completed_at } = reportFixture(id)
  return { id, session_id, suite_id, person, completed_at }
}
