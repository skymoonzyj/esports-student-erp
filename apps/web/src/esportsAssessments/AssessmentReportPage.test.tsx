import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AssessmentReportPage } from './AssessmentReportPage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { jsonResponse, privateResponse, reportFixture, PREVIOUS_ID, REPORT_ID } from './reportTestFixtures'
import { assessmentKeys } from './keys'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })
function setup(handler: (url: string) => Response | Promise<Response>, id = REPORT_ID) {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async input => handler(String(input))))
  return renderAssessmentPage(<Routes><Route path="/esports-assessments/reports/:reportId" element={<AssessmentReportPage />} /></Routes>, { initialEntries: [`/esports-assessments/reports/${id}`], account: studentFixture })
}
function rankingFixture() {
  return { report_id: REPORT_ID, calculated_at: '2026-10-05T00:00:00Z', status: 'ranked', reason: null,
    best_report_id: PREVIOUS_ID, best_completed_at: '2026-10-04T18:30:00Z', overall_score: 82.5,
    overall_rank: 1, participant_count: 3,
    items: ['precision', 'tracking', 'switching', 'recognition'].map((test_id, index) => ({ test_id, score: 80 + index, rank: index === 0 ? 3 : 1 })) }
}
it('shows selected best-round internal ranking separately from saved scores', async () => {
  setup(url => jsonResponse(url.endsWith('/ranking') ? rankingFixture() : reportFixture()))
  const ranking = await screen.findByRole('region', { name: '内部训练排名' })
  expect(await within(ranking).findByText('82.50 分')).toBeVisible()
  expect(within(ranking).getByText(/整体第 1 名.*3 人/)).toBeVisible()
  expect(within(ranking).getByRole('row', { name: /精准定位与微调 80 分 第 3 名.*3 人/ })).toBeVisible()
  expect(within(ranking).getByRole('link', { name: '查看最佳整轮报告' })).toHaveAttribute('href', `/esports-assessments/reports/${PREVIOUS_ID}`)
  expect(within(ranking).getByText(/当前报告为历史轮次/)).toBeVisible()
  expect(screen.getAllByText('50 分')).toHaveLength(4)
  expect(within(ranking).queryByText(/precision|不得泄露|百分位|全球/)).not.toBeInTheDocument()
})
it.each([403, 404])('isolates ranking denial %s with no retry loop or leaked details', async status => {
  let attempts = 0
  const { queryClient } = setup(url => {
    if (!url.endsWith('/ranking')) return jsonResponse(reportFixture())
    attempts += 1
    return jsonResponse({ status, detail: '不得泄露的姓名' }, status)
  })
  expect(await screen.findByText('排名不存在或无权查看。')).toBeVisible()
  await queryClient.invalidateQueries({ queryKey: assessmentKeys.report(studentFixture.account_id, REPORT_ID) })
  expect(screen.getByText('报告合成学员')).toBeVisible()
  expect(screen.queryByText('不得泄露的姓名')).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '重试排名' })).not.toBeInTheDocument()
  expect(attempts).toBe(1)
})
it('drops the old ranking when following the best report route', async () => {
  let resolveNext!: (response: Response) => void
  setup(url => {
    if (url.endsWith('/ranking')) return url.includes(PREVIOUS_ID)
      ? new Promise<Response>(resolve => { resolveNext = resolve })
      : jsonResponse(rankingFixture())
    return jsonResponse(reportFixture(url.endsWith(PREVIOUS_ID) ? PREVIOUS_ID : REPORT_ID))
  })
  await screen.findByText('82.50 分')
  await userEvent.click(screen.getByRole('link', { name: '查看最佳整轮报告' }))
  await waitFor(() => expect(resolveNext).toBeTypeOf('function'))
  expect(screen.queryByText('82.50 分')).not.toBeInTheDocument()
  expect(screen.getByText('正在读取内部训练排名…')).toBeVisible()
  resolveNext(jsonResponse({ ...rankingFixture(), report_id: PREVIOUS_ID, best_report_id: PREVIOUS_ID, overall_score: 91 }))
  expect(await screen.findByText('91.00 分')).toBeVisible()
  expect(screen.queryByRole('link', { name: '查看最佳整轮报告' })).not.toBeInTheDocument()
})
it('explains quality exclusion while preserving frozen report advice', async () => {
  const value = reportFixture(); value.results[0].scored.quality_flags = ['long_frames']
  setup(url => jsonResponse(url.endsWith('/ranking') ? { ...rankingFixture(), status: 'not_eligible', reason: 'quality_warning', best_report_id: null,
    best_completed_at: null, overall_score: null, overall_rank: null, participant_count: 0, items: [] } : value))
  expect(await screen.findByText(/本轮存在质量提示，不参与排名/)).toBeVisible()
  expect(screen.getByText('服务端冻结的训练建议')).toBeVisible()
  expect(screen.queryByRole('table', { name: '最佳整轮各项排名' })).not.toBeInTheDocument()
})
it('renders four independent scores, server grades, China date, units, warnings and frozen advice without a total', async () => {
  const report = reportFixture(); report.results[0].scored.grade = 'exceptional'; report.results[0].scored.quality_flags = ['long_frames']
  setup(() => jsonResponse(report))
  expect(await screen.findByText('报告合成学员')).toBeVisible()
  expect(screen.getByText('2026-09-22')).toBeVisible()
  expect(screen.getAllByText('50 分')).toHaveLength(4)
  expect(screen.getByText('超高表现')).toBeVisible()
  expect(screen.getByText('试算分／需复测')).toBeVisible()
  expect(screen.getByText('30000 ms')).toBeVisible()
  expect(screen.getByText(/120 Hz.*首个真实离靶/)).toBeVisible()
  expect(screen.getByText('服务端冻结的训练建议')).toBeVisible()
  expect(screen.queryByText(/总分|总天赋|百分位/)).not.toBeInTheDocument()
})
it('uses the immutable server delta rather than recomputing a value from the prior report', async () => {
  const report = reportFixture(), previous = reportFixture(PREVIOUS_ID)
  previous.results[0].scored.score = 10
  report.comparisons = [{ test_id: 'precision', previous_report_id: PREVIOUS_ID, score_delta: 7, non_comparable_reasons: [] }]
  setup(url => jsonResponse(url.endsWith(PREVIOUS_ID) ? previous : report))
  expect(await screen.findByText('较最近可比记录：+7 分')).toBeVisible()
  expect(await screen.findByRole('region', { name: '精准定位与微调历史比较' })).toBeVisible()
  expect(screen.queryByText('+40 分')).not.toBeInTheDocument()
})
it('shows actual incompatibility reasons and prior raw metrics without a formal delta', async () => {
  const report = reportFixture(), previous = reportFixture(PREVIOUS_ID)
  previous.results[0].environment.width_css_px = 1200
  report.comparisons = [{ test_id: 'precision', previous_report_id: PREVIOUS_ID, score_delta: null, non_comparable_reasons: ['environment_changed'] }]
  setup(url => jsonResponse(url.endsWith(PREVIOUS_ID) ? previous : report))
  const comparison = await screen.findByRole('region', { name: '精准定位与微调历史比较' })
  expect(within(comparison).getByText('设备或环境不同')).toBeVisible()
  const viewport = await within(comparison).findByText('1200 × 562.5 CSS px')
  await userEvent.click(viewport.closest('details')!.querySelector('summary')!)
  expect(viewport).toBeVisible()
  expect(within(comparison).queryByText(/较最近可比|进步/)).not.toBeInTheDocument()
})
it('isolates denied prior report from the current authorized report without exposing error details', async () => {
  const report = reportFixture()
  report.comparisons = [{ test_id: 'precision', previous_report_id: PREVIOUS_ID, score_delta: null, non_comparable_reasons: ['config_changed'] }]
  setup(url => url.endsWith(PREVIOUS_ID) ? privateResponse() : jsonResponse(report))
  expect(await screen.findByText('历史报告不可用或无权查看')).toBeVisible()
  expect(screen.getByText('报告合成学员')).toBeVisible()
  expect(screen.queryByText('不得泄露的姓名')).not.toBeInTheDocument()
})
it('hides previously cached person data when a fresh detail request is denied', async () => {
  let denied = false
  const { queryClient } = setup(() => denied ? privateResponse() : jsonResponse(reportFixture()))
  await screen.findByText('报告合成学员'); denied = true
  await queryClient.invalidateQueries({ queryKey: assessmentKeys.report(studentFixture.account_id, REPORT_ID) })
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('报告不存在或无权查看'))
  expect(screen.queryByText('报告合成学员')).not.toBeInTheDocument()
  expect(screen.queryByText('不得泄露的姓名')).not.toBeInTheDocument()
})
it('rejects a malformed report ID without requesting it', async () => {
  const request = vi.fn(() => privateResponse())
  setup(request, 'not-a-uuid')
  expect(await screen.findByText('报告不存在或无权查看')).toBeVisible()
  expect(request).not.toHaveBeenCalled()
})
