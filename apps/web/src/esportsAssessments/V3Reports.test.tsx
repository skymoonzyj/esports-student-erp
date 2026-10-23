import { render, screen, within } from '@testing-library/react'
import { Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AssessmentReportPage } from './AssessmentReportPage'
import { Metrics } from './reportPresentation'
import { assessmentReportViewSchema } from './contracts'
import { fixtureEnvironment, fixtureSettings, fixtureV3Config } from './testSupport/fixtures'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('shows no fabricated zero for an empty v3 switching sample', () => {
  render(<Metrics metrics={{ switching_success_average_ms: null }} emptyLabel="—" />)
  expect(screen.getByText('—')).toBeVisible()
  expect(screen.queryByText('0 ms')).not.toBeInTheDocument()
})

it('keeps the legacy empty-sample wording by default', () => {
  render(<Metrics metrics={{ successful_average_latency_ms: null }} />)
  expect(screen.getByText('暂无有效样本')).toBeVisible()
  expect(screen.queryByText('—')).not.toBeInTheDocument()
})

it('labels the distinct v3 metrics without collapsing them into generic reaction time', () => {
  render(<Metrics metrics={{
    switching_group_successes: 12,
    acquisition_success_average_ms: 250,
    switching_success_average_ms: 480,
    switching_scoring_average_ms: 990,
    moving_hits: 14,
    safe_entries: 20,
    safe_entry_average_ms: null,
  }} emptyLabel="—" />)
  for (const label of ['完成两靶组数', '首靶成功获取平均用时', '第二靶成功切换平均用时', '第二靶计分平均用时', '有效移动命中次数', '安全区进入次数', '安全区进入用时']) {
    expect(screen.getByText(label)).toBeVisible()
  }
  expect(screen.queryByText('每轮首次移动平均用时')).not.toBeInTheDocument()
})

it('uses the v3 dodge report label and dash only for the changed report result', async () => {
  const tests = ['last-hit', 'orb-walk', 'dodge', 'map-awareness'] as const
  const configs = Object.fromEntries(tests.map(test => [test, fixtureV3Config('pc-moba', test)]))
  const report = assessmentReportViewSchema.parse({
    id: '99999999-9999-4999-8999-999999999999', session_id: '33333333-3333-4333-8333-333333333333', suite_id: 'pc-moba',
    suite: { suite_id: 'pc-moba', catalog_version: 'pc-moba-v3', test_ids: tests, tasks: configs },
    person: { person_id: '77777777-7777-4777-8777-777777777777', display_name: '新版报告学员' }, completed_at: '2026-09-21T18:30:00Z',
    results: tests.map((test, index) => ({
      result_id: `00000000-0000-4000-8000-${String(index + 700).padStart(12, '0')}`, test_id: test,
      config: configs[test], settings: fixtureSettings('pc-moba'), environment: fixtureEnvironment(),
      scored: { metrics: test === 'dodge' ? { avoided: 20, hits_taken: 10, avoid_rate: 2 / 3, safe_entries: 0, safe_entry_average_ms: null } : { opportunities: 30 }, score: 50, grade: 'medium', quality_flags: [] },
    })),
    recommendations: ['服务端新版建议'], comparisons: [],
  })
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => new Response(JSON.stringify(report), { headers: { 'Content-Type': 'application/json' } })))
  renderAssessmentPage(<Routes><Route path="/esports-assessments/reports/:reportId" element={<AssessmentReportPage />} /></Routes>, {
    initialEntries: [`/esports-assessments/reports/${report.id}`], account: studentFixture,
  })
  const card = await screen.findByRole('region', { name: '危险区域识别与躲避' })
  expect(within(card).getByRole('heading', { name: '危险区域识别与躲避' })).toBeVisible()
  expect(within(card).getByText('安全区进入用时')).toBeVisible()
  expect(within(card).getByText('—')).toBeVisible()
  expect(within(card).queryByText('每轮首次移动平均用时')).not.toBeInTheDocument()
})
