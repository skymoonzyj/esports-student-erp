import { cleanup, fireEvent, screen, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { AssessmentHome } from './AssessmentHome'
import { clearAssessmentPending, savePending } from './pending'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { fixturePending } from './testSupport/fixtures'

vi.mock(import('./availability'), () => ({
  ASSESSMENT_SUITE_AVAILABILITY: {
    'pc-fps': true,
    'mobile-fps': false,
    'pc-moba': false,
    'mobile-moba': false,
  },
}))

const NOW = '2026-09-22T00:00:00.000Z'

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function context(overrides: Record<string, unknown> = {}) {
  return {
    server_now: NOW,
    can_take: true,
    can_read_self: true,
    can_read_reports: true,
    available_suites: ['pc-fps', 'mobile-fps'],
    own_in_progress_sessions: [],
    active_run: null,
    ...overrides,
  }
}

function renderHome(value = context(), superAdmin = false) {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith('/esports-assessments/context')) return json(value)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    throw new Error(`Unexpected request: ${url}`)
  }))
  return renderAssessmentPage(<AssessmentHome />, {
    initialEntries: ['/esports-assessments'],
    account: { ...studentFixture, is_super_admin: superAdmin },
  })
}

it('offers the protected listening preview only to an authorized superadmin', async () => {
  renderHome(context(), true)
  const link = await screen.findByRole('link', { name: '听声辨位（试听）' })
  expect(link).toHaveAttribute('href', '/api/v1/esports-assessments/sound-preview/index.html')
  expect(link).toHaveAttribute('target', '_blank')
  expect(link).toHaveAttribute('rel', 'noopener noreferrer')
  expect(screen.getByText(/试听结果仅在本次页面显示/)).toBeVisible()
})

it('keeps the listening preview closed to a regular account even when it can take assessments', async () => {
  renderHome()
  await screen.findByRole('heading', { name: '天赋评测 2' })
  expect(screen.queryByRole('link', { name: '听声辨位（试听）' })).not.toBeInTheDocument()
})

it.each([
  { can_take: false },
  { can_read_self: false },
  { available_suites: [] },
])('hides the listening preview when the server no longer allows superadmin participation (%j)', async overrides => {
  renderHome(context(overrides), true)
  await screen.findByRole('heading', { name: '天赋评测 2' })
  expect(screen.queryByRole('link', { name: '听声辨位（试听）' })).not.toBeInTheDocument()
})

afterEach(() => {
  cleanup()
  clearAssessmentPending()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('offers a suite only when both the local implementation and server context enable it', async () => {
  renderHome()

  expect(await screen.findByRole('heading', { name: '天赋评测 2' })).toBeVisible()
  const pc = screen.getByRole('region', { name: '端游 FPS' })
  const mobile = screen.getByRole('region', { name: '手游 FPS' })
  expect(within(pc).getByText('可进行')).toBeVisible()
  expect(within(pc).getByRole('button', { name: '端游 FPS 正式测试' })).toBeEnabled()
  expect(within(pc).getByRole('button', { name: '端游 FPS 自由练习' })).toBeEnabled()
  expect(within(mobile).getByText('开发中')).toBeVisible()
  expect(within(mobile).queryByRole('button', { name: '开始正式测试' })).not.toBeInTheDocument()
})

it('does not expose a locally implemented suite when the server keeps it unavailable', async () => {
  renderHome(context({ available_suites: [] }))

  const pc = await screen.findByRole('region', { name: '端游 FPS' })
  expect(within(pc).getByText('暂未开放')).toBeVisible()
  expect(within(pc).queryByRole('button', { name: '开始正式测试' })).not.toBeInTheDocument()
})

it('keeps report navigation for a reader without showing a formal start control', async () => {
  renderHome(context({ can_take: false, can_read_self: true, can_read_reports: true }))

  expect(await screen.findByText('当前账号仅可查看已授权记录，不能开始评测。')).toBeVisible()
  expect(screen.getByRole('link', { name: '查看评测记录' })).toHaveAttribute(
    'href',
    '/esports-assessments/history',
  )
  expect(screen.queryByRole('button', { name: '开始正式测试' })).not.toBeInTheDocument()
})

it('blocks a new start while another attempt owns the account run slot', async () => {
  renderHome(context({
    active_run: {
      mode: 'practice',
      attempt_id: '22222222-2222-4222-8222-222222222222',
      session_id: null,
      suite_id: 'pc-moba',
      test_id: 'dodge',
      run_until: '2026-09-22T00:01:30.000Z',
      submit_until: '2026-09-22T00:30:00.000Z',
    },
  }))

  expect(await screen.findByRole('alert')).toHaveTextContent('已有一项评测正在进行')
  expect(screen.getByRole('button', { name: '端游 FPS 正式测试' })).toBeDisabled()
})

it('uses one stable session creation key after an ambiguous server failure', async () => {
  const bodies: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/context')) return json(context())
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    bodies.push(String(init?.body))
    return new Response(JSON.stringify({ detail: 'failed' }), { status: 500 })
  }))
  renderAssessmentPage(<AssessmentHome />, { initialEntries: ['/esports-assessments'], account: studentFixture })
  fireEvent.click(await screen.findByRole('button', { name: '端游 FPS 正式测试' }))
  await screen.findByRole('alert')
  fireEvent.click(screen.getByRole('button', { name: '端游 FPS 正式测试' }))
  await screen.findByRole('alert')
  expect(bodies).toHaveLength(2)
  expect(bodies[0]).toBe(bodies[1])
})

it('offers server recovery even after the 90-second active slot ended and no local raw exists', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => json(String(input).endsWith('/context') ? context() : { items: [{ mode: 'practice', attempt_id: '22222222-2222-4222-8222-222222222222', session_id: null, suite_id: 'pc-fps', test_id: 'tracking', state: 'awaiting_submission', issued_at: NOW, run_until: '2026-09-22T00:01:30.000Z', submit_until: '2026-09-22T00:30:00.000Z' }], next_cursor: null, visible_total: 1 })))
  renderAssessmentPage(<AssessmentHome />, { initialEntries: ['/esports-assessments'], account: studentFixture })
  expect(await screen.findByRole('link', { name: /恢复练习/ })).toHaveAttribute('href', '/esports-assessments/practice?suite=pc-fps&test=tracking&practice_id=22222222-2222-4222-8222-222222222222')
})

it('warns when a pending result exists only in page memory', async () => {
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('quota', 'QuotaExceededError')
  })
  const pending = fixturePending({
    account_id: studentFixture.account_id,
    created_at: new Date().toISOString(),
  })
  expect(savePending(pending).persisted).toBe(false)

  renderHome()

  expect(await screen.findByText('结果暂存仅在当前页面有效，刷新或关闭页面后可能无法恢复。'))
    .toBeVisible()
})
