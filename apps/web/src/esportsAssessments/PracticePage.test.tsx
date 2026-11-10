import { fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PracticePage } from './PracticePage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import catalog from './testSupport/catalog-v1.json'
import { fixtureV4SuiteConfig } from './testSupport/fixturesV4'
import { fixtureAttempt, fixtureConfig, fixtureSettings } from './testSupport/fixtures'
import laneCatalog from './testSupport/catalog-moba-v4.json'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import towerCatalog from './testSupport/catalog-moba-v5.json'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'
import type { TaskConfig } from './contracts'
vi.mock('./availability', () => ({ ASSESSMENT_SUITE_AVAILABILITY: { 'pc-fps': true } }))
vi.mock('./AssessmentHost', () => ({ AssessmentHost: ({ config, mode }: { config: TaskConfig; mode: string }) => <div>宿主 {mode} 难度 {config.difficulty}<span>{config.test_id} · {config.catalog_version}</span><span>冻结人物 {'attack_mode' in config.params ? config.params.attack_mode : 'legacy'}</span></div> }))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
it.each([[false, false], [true, false], [false, true], [true, true]])('displays authoritative recovered mode/version despite a ranged URL (explicit id=%s, legacy=%s)', async (explicitId, legacy) => {
  const attempt = fixtureAttempt({ mode: 'practice', session_id: null, suite_id: 'pc-moba', test_id: 'last-hit', config: legacy ? fixtureConfig('pc-moba', 'last-hit') : fixtureMobaV4Config('melee'), settings: fixtureSettings('pc-moba') })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => new Response(JSON.stringify(String(input).endsWith('/context')
    ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-moba'], own_in_progress_sessions: [], active_run: null }
    : String(input).endsWith('/suites') ? { items: [laneCatalog] }
    : String(input).includes('/recovery?') ? { items: [{ mode: 'practice', attempt_id: attempt.id, session_id: null, suite_id: 'pc-moba', test_id: 'last-hit', state: 'running', issued_at: attempt.issued_at, run_until: attempt.run_until, submit_until: attempt.submit_until }], next_cursor: null, visible_total: 1 }
    : { attempt, saved: null }), { headers: { 'Content-Type': 'application/json' } })))
  renderAssessmentPage(<PracticePage />, { initialEntries: [`/esports-assessments/practice?suite=pc-moba&test=last-hit&attack_mode=ranged${explicitId ? `&practice_id=${attempt.id}` : ''}`], account: { ...studentFixture, is_super_admin: true } })
  expect(await screen.findByText(`冻结人物 ${legacy ? 'legacy' : 'melee'}`)).toBeVisible()
})
it.each([laneCatalog, towerCatalog])('offers only standard $catalog_version practice with a mode picker', async live => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => new Response(JSON.stringify(String(input).endsWith('/context')
    ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-moba'], own_in_progress_sessions: [], active_run: null }
    : String(input).endsWith('/suites') ? { items: [live] } : { items: [], next_cursor: null, visible_total: 0 }), { headers: { 'Content-Type': 'application/json' } })))
  renderAssessmentPage(<PracticePage />, { initialEntries: ['/esports-assessments/practice?suite=pc-moba&attack_mode=ranged'], account: { ...studentFixture, is_super_admin: true } })
  expect(await screen.findByLabelText('练习人物模式')).toHaveValue('ranged')
  expect(screen.queryByLabelText('练习难度')).toBeNull()
  expect(screen.getByText(/只提供标准配置/)).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '五分钟兵线补刀' }))
  expect(await screen.findByText('宿主 practice 难度 3')).toBeVisible()
})
it.each([fixtureMobaV4Config('melee'), fixtureMobaV5Config('melee')])('recovers frozen $catalog_version mode despite current towers and a ranged URL', async config => {
  const attempt = fixtureAttempt({ mode: 'practice', session_id: null, suite_id: 'pc-moba', test_id: 'last-hit', config, settings: fixtureSettings('pc-moba') })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => new Response(JSON.stringify(String(input).endsWith('/context')
    ? { server_now: attempt.server_now, can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-moba'], own_in_progress_sessions: [], active_run: null }
    : String(input).endsWith('/suites') ? { items: [towerCatalog] } : { attempt, saved: null }), { headers: { 'Content-Type': 'application/json' } })))
  renderAssessmentPage(<PracticePage />, { initialEntries: [`/esports-assessments/practice?suite=pc-moba&test=last-hit&attack_mode=ranged&practice_id=${attempt.id}`], account: { ...studentFixture, is_super_admin: true } })
  expect(await screen.findByText(`last-hit · ${config.catalog_version}`)).toBeVisible()
  expect(screen.getByText('冻结人物 melee')).toBeVisible()
})
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

it('recovers an old practice by its server frozen config even after the live menu replaces its test', async () => {
  const attempt = fixtureAttempt({ mode: 'practice', session_id: null })
  const live = fixtureV4SuiteConfig()
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => new Response(JSON.stringify(String(input).endsWith('/context')
    ? { server_now: '2026-09-21T00:00:00Z', can_take: true, can_read_self: true, can_read_reports: true, available_suites: ['pc-fps'], own_in_progress_sessions: [], active_run: null }
    : String(input).endsWith('/suites') ? { items: [live] } : { attempt, saved: null }), { headers: { 'Content-Type': 'application/json' } })))
  renderAssessmentPage(<PracticePage />, { initialEntries: [`/esports-assessments/practice?suite=pc-fps&test=precision&practice_id=${attempt.id}`], account: studentFixture })
  expect(await screen.findByText('precision · pc-fps-v1')).toBeVisible()
  expect(screen.queryByLabelText('练习难度')).toBeNull()
})
