import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes, useLocation } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { useAuth, type AuthUser } from '../auth/AuthProvider'
import { AssessmentHistoryPage } from './AssessmentHistoryPage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { jsonResponse, practiceFixture, reportContext, summaryFixture, REPORT_ID, PREVIOUS_ID, PERSON_ID, SESSION_ID } from './reportTestFixtures'
import { fixtureSuiteConfig } from './testSupport/fixtures'

// Keep the real console output: warnings are observed, not suppressed.
beforeEach(() => { vi.spyOn(console, 'error') })
afterEach(() => {
  try {
    cleanup()
    expect(console.error).not.toHaveBeenCalled()
  } finally {
    vi.restoreAllMocks(); vi.unstubAllGlobals()
  }
})
function Location() { const location = useLocation(); return <output data-testid="location">{location.search}</output> }
function RefreshAccount() { const { refreshUser } = useAuth(); return <button onClick={() => void refreshUser()}>刷新账号</button> }
function setup(options: { url?: string; self?: boolean; read?: boolean; account?: AuthUser; page?: (url: URL) => unknown } = {}) {
  const requests: URL[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => {
    const url = new URL(String(input), 'https://erp.test'); requests.push(url)
    if (url.pathname.endsWith('/context')) return jsonResponse(reportContext(options.self ?? true))
    return jsonResponse(options.page?.(url) ?? { items: [], next_cursor: null, visible_total: 0 })
  }))
  const result = renderAssessmentPage(<><Location /><RefreshAccount /><Routes><Route path="/esports-assessments/history" element={<AssessmentHistoryPage />} /></Routes></>, {
    initialEntries: [options.url ?? '/esports-assessments/history'], account: options.account ?? (options.read === false ? { ...studentFixture, scoped_permissions: ['people.read'] } : studentFixture),
  })
  return { ...result, requests }
}

it('practice shows only raw metrics and explicit current-page comparison', async () => {
  const a = practiceFixture(), b = practiceFixture('22222222-2222-4222-8222-222222222223')
  b.scored.metrics.hits = 18
  setup({ page: url => ({ items: url.searchParams.get('mode') === 'practice' ? [a, b] : [], visible_total: url.searchParams.get('mode') === 'practice' ? 2 : 0, next_cursor: null }) })
  await userEvent.click(await screen.findByRole('tab', { name: '自由练习' }))
  expect(await screen.findAllByText('原始指标')).toHaveLength(2)
  expect(screen.queryByText(/中等表现|超高表现|正式分/)).not.toBeInTheDocument()
  expect(screen.getByText(/仅可选择当前页的两条/)).toBeVisible()
  const checks = screen.getAllByRole('checkbox')
  await userEvent.click(checks[0]); await userEvent.click(checks[1])
  const comparison = screen.getByRole('region', { name: '练习原始指标比较' })
  expect(within(comparison).getByText('+3 次')).toBeVisible()
  expect(within(comparison).queryByText(/进步|退步|正式分|超高表现/)).not.toBeInTheDocument()
})

it('reader-only redirects an unavailable own-practice view without a private-history request', async () => {
  const { requests } = setup({ self: false, url: '/esports-assessments/history?view=own&mode=practice' })
  expect(await screen.findByText(/仅可查看已授权的完成报告/)).toBeVisible()
  expect(await screen.findByText('暂无已完成报告')).toBeVisible()
  expect(screen.getByTestId('location')).toHaveTextContent('view=reports')
  expect(requests.some(url => url.pathname.endsWith('/history'))).toBe(false)
  expect(screen.queryByRole('tab', { name: '自由练习' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /开始/ })).not.toBeInTheDocument()
})

it('does not treat people.read as independent assessment permission', async () => {
  const { requests } = setup({ read: false })
  expect(await screen.findByText('暂无天赋评测读取权限')).toBeVisible()
  expect(requests).toEqual([])
})

it('resets report cursor when filters change and retains server ordering while de-duplicating repeated IDs', async () => {
  const { requests } = setup({ url: '/esports-assessments/history?view=reports', page: url => {
    if (url.searchParams.has('cursor')) return { items: [summaryFixture(PREVIOUS_ID)], visible_total: 3, next_cursor: null }
    return { items: [summaryFixture(), summaryFixture(), summaryFixture(PREVIOUS_ID)], visible_total: 3, next_cursor: 'opaque-next' }
  } })
  const firstLinks = await screen.findAllByRole('link', { name: /查看报告/ })
  expect(firstLinks).toHaveLength(2)
  expect(firstLinks[0]).toHaveAttribute('href', `/esports-assessments/reports/${REPORT_ID}`)
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(screen.getAllByRole('link', { name: /查看报告/ })).toHaveLength(1))
  await userEvent.type(screen.getByLabelText('开始日期'), '2026-09-21')
  await userEvent.click(screen.getByRole('button', { name: '应用筛选' }))
  await waitFor(() => expect(requests.at(-1)?.searchParams.get('date_from')).toBe('2026-09-21'))
  expect(requests.at(-1)?.searchParams.has('cursor')).toBe(false)
})

it('validates URL report selectors without sending a malformed query', async () => {
  const { requests } = setup({ url: '/esports-assessments/history?view=reports&person_id=secret-name' })
  expect(await screen.findByRole('alert')).toHaveTextContent('筛选条件无效')
  expect(requests.some(url => url.pathname.endsWith('/reports'))).toBe(false)
})

it('shows own formal progress separately from completed reports without generating report links', async () => {
  setup({ page: () => ({ items: [{ mode: 'formal', session: { id: SESSION_ID, suite_id: 'pc-fps', suite_config: fixtureSuiteConfig('pc-fps'), status: 'in_progress', members: ['precision', 'tracking', 'switching', 'recognition'], results: [], report_id: null }, created_at: '2026-09-21T18:30:00Z', completed_at: null }], visible_total: 1, next_cursor: null }) })
  expect(await screen.findByText(/进行中 · 0\/4/)).toBeVisible()
  expect(screen.getByRole('link', { name: '查看轮次' })).toHaveAttribute('href', `/esports-assessments/sessions/${SESSION_ID}`)
  expect(screen.queryByRole('link', { name: /查看报告/ })).not.toBeInTheDocument()
})

it('clears practice selections on pagination instead of comparing across hidden pages', async () => {
  const first = practiceFixture(), second = practiceFixture('22222222-2222-4222-8222-222222222223')
  setup({ url: '/esports-assessments/history?view=own&mode=practice', page: url => ({ items: url.searchParams.has('cursor') ? [practiceFixture('22222222-2222-4222-8222-222222222224')] : [first, second], visible_total: 3, next_cursor: url.searchParams.has('cursor') ? null : 'next' }) })
  const checks = await screen.findAllByRole('checkbox'); await userEvent.click(checks[0]); await userEvent.click(checks[1])
  expect(screen.getByRole('region', { name: '练习原始指标比较' })).toBeVisible()
  await userEvent.click(screen.getByRole('button', { name: '下一页' }))
  await waitFor(() => expect(screen.getAllByRole('checkbox')).toHaveLength(1))
  expect(screen.queryByRole('region', { name: '练习原始指标比较' })).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox')).not.toBeChecked()
})

it('keeps an exact person selector on authorized completed-report requests', async () => {
  const { requests } = setup({ self: false, url: `/esports-assessments/history?view=reports&person_id=${PERSON_ID}&suite_id=pc-fps` })
  await screen.findByText('暂无已完成报告')
  expect(requests.at(-1)?.searchParams.get('person_id')).toBe(PERSON_ID)
  expect(requests.at(-1)?.searchParams.get('suite_id')).toBe('pc-fps')
})

it('explains incompatible practice choices without deriving any differences', async () => {
  const first = practiceFixture(), second = practiceFixture('22222222-2222-4222-8222-222222222223')
  second.environment.width_css_px = 1200; second.scored.metrics.hits = 29
  setup({ url: '/esports-assessments/history?view=own&mode=practice', page: () => ({ items: [first, second], visible_total: 2, next_cursor: null }) })
  const checks = await screen.findAllByRole('checkbox'); await userEvent.click(checks[0]); await userEvent.click(checks[1])
  const comparison = screen.getByRole('region', { name: '练习原始指标比较' })
  expect(within(comparison).getByText('设备或环境不同')).toBeVisible()
  expect(within(comparison).queryByText('+14 次')).not.toBeInTheDocument()
  expect(within(comparison).getByText('15 次')).toBeVisible()
  expect(within(comparison).getByText('29 次')).toBeVisible()
})

it('clears practice selection and old records on account identity refresh', async () => {
  const account = { ...studentFixture }
  const first = practiceFixture(), second = practiceFixture('22222222-2222-4222-8222-222222222223')
  const { queryClient } = setup({ account, url: '/esports-assessments/history?view=own&mode=practice', page: () => ({ items: account.account_id === studentFixture.account_id ? [first, second] : [], visible_total: account.account_id === studentFixture.account_id ? 2 : 0, next_cursor: null }) })
  const checks = await screen.findAllByRole('checkbox'); await userEvent.click(checks[0]); await userEvent.click(checks[1])
  account.account_id = '11111111-1111-4111-8111-111111111112'
  await userEvent.click(screen.getByRole('button', { name: '刷新账号' }))
  expect(await screen.findByText('暂无已完成练习')).toBeVisible()
  expect(screen.queryByRole('region', { name: '练习原始指标比较' })).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  expect(queryClient.getQueryCache().getAll().filter(query => query.queryKey[0] === 'esports-assessments').every(query => query.queryKey[1] === account.account_id)).toBe(true)
})
