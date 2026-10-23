import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PracticePage } from './PracticePage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import catalog from './testSupport/catalog-v1.json'
import { fixtureAttempt } from './testSupport/fixtures'
vi.mock('./availability', () => ({ ASSESSMENT_SUITE_AVAILABILITY: { 'pc-fps': true } }))
vi.mock('./AssessmentHost', () => ({ AssessmentHost: ({ config, mode }: { config: { difficulty: number }; mode: string }) => <div>宿主 {mode} 难度 {config.difficulty}</div> }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
it('offers five preview levels without allocating a practice attempt or formal progress', async () => {
  const fetcher = vi.fn<typeof fetch>(async input => new Response(JSON.stringify(String(input).endsWith('/context')
    ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-fps'], own_in_progress_sessions: [], active_run: null }
    : String(input).endsWith('/suites') ? { items: [catalog['pc-fps']] } : { items: [], next_cursor: null, visible_total: 0 }), { headers: { 'Content-Type': 'application/json' } }))
  vi.stubGlobal('fetch', fetcher)
  renderAssessmentPage(<PracticePage />, { initialEntries: ['/esports-assessments/practice?suite=pc-fps'], account: studentFixture })
  const difficulty = await screen.findByLabelText('练习难度')
  fireEvent.change(difficulty, { target: { value: '5' } })
  fireEvent.click(screen.getByRole('button', { name: '定位精度' }))
  expect(await screen.findByText('宿主 practice 难度 5')).toBeVisible()
  expect(fetcher.mock.calls.every(([, init]) => !init?.method || init.method === 'GET')).toBe(true)
})

it('keeps authoritative reconciliation available for an existing practice when the suite closes', async () => {
  const attempt = fixtureAttempt({ mode: 'practice', session_id: null })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => new Response(JSON.stringify(String(input).endsWith('/context')
    ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: [], own_in_progress_sessions: [], active_run: null }
    : String(input).endsWith('/suites') ? { items: [] } : { attempt, saved: null }), { headers: { 'Content-Type': 'application/json' } })))
  renderAssessmentPage(<PracticePage />, { initialEntries: [`/esports-assessments/practice?suite=pc-fps&test=precision&practice_id=${attempt.id}`], account: studentFixture })
  expect(await screen.findByRole('button', { name: '结束未完成尝试并重新开始' })).toBeVisible()
  expect(screen.queryByRole('button', { name: '开始练习' })).toBeNull()
})
