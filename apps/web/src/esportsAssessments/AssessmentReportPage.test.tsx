import { cleanup, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { AssessmentReportPage } from './AssessmentReportPage'
import { renderAssessmentPage, studentFixture } from './testSupport/renderAssessmentPage'
import { jsonResponse, privateResponse, reportFixture, PREVIOUS_ID, REPORT_ID } from './reportTestFixtures'
import { assessmentKeys } from './keys'
import { fixtureV4Config, fixtureV4SuiteConfig } from './testSupport/fixturesV4'
import { fixtureV5Config } from './testSupport/fixturesV5'
import catalogV5 from './testSupport/catalog-v5.json'
import { suiteConfigSchema } from './contracts'
import { fixtureMobaV4Config } from './testSupport/fixturesMobaV4'
import laneCatalog from './testSupport/catalog-moba-v4.json'
import towerCatalog from './testSupport/catalog-moba-v5.json'
import { fixtureMobaV5Config } from './testSupport/fixturesMobaV5'

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
it.each([4, 5])('renders saved v%s ranged lane, frozen denominator, hero metrics and its own top three', async version => {
  const old = reportFixture()
  const config = version === 4 ? fixtureMobaV4Config('ranged') : fixtureMobaV5Config('ranged')
  const suite = suiteConfigSchema.parse({ ...(version === 4 ? laneCatalog : towerCatalog), tasks: { 'last-hit': config } })
  const report = { ...old, suite_id: 'pc-moba', suite, comparisons: [], results: [{ ...old.results[0], test_id: 'last-hit', config,
    scored: { ...old.results[0].scored, metrics: { last_hits: 20, enemy_deaths: 40, missed: 20, unresolved: version === 4 ? 10 : 35, last_hit_rate: .5, opportunity_completion: version === 4 ? .4 : 20 / 75, last_hits_per_minute: 4, attacks: 80, cancelled_attacks: 3, minute_1_last_hits: 1, minute_2_last_hits: 2, minute_3_last_hits: 4, minute_4_last_hits: 5, minute_5_last_hits: 8, ...(version === 5 ? { hero_deaths: 2, hero_damage_taken: 2360 } : {}) } } }] }
  const ranking = { ...rankingFixture(), overall_score: 40, overall_rank: 2, top_scores: [{ rank: 1, score: 50, gap: 10 }, { rank: 2, score: 40, gap: 0 }], items: [{ test_id: 'last-hit', score: 40, rank: 2, top_scores: [{ rank: 1, score: 50, gap: 10 }, { rank: 2, score: 40, gap: 0 }] }] }
  setup(url => jsonResponse(url.endsWith('/ranking') ? ranking : report))
  expect(await screen.findByRole('heading', { name: '五分钟兵线补刀 · 远程' })).toBeVisible()
  expect(screen.getByText(version === 4 ? /补刀率.*已死亡敌兵.*总机会完成率.*50/ : /补刀率.*已死亡敌兵.*总机会完成率.*75/)).toBeVisible()
  expect(screen.getByText(/未结算.*不记为漏刀/)).toBeVisible()
  expect(screen.getByText('第 5 分钟补刀')).toBeVisible()
  expect(screen.getByText(version === 4 ? '40%' : '26.67%')).toBeVisible()
  if (version === 5) {
    expect(screen.getByText('人物死亡次数')).toBeVisible()
    expect(screen.getByText('承受防御塔伤害')).toBeVisible()
    expect(screen.getByText('2360 点')).toBeVisible()
    expect(screen.getByText(/防御塔.*不计.*补刀/)).toBeVisible()
  } else expect(screen.queryByText('人物死亡次数')).toBeNull()
  expect(await screen.findAllByRole('table', { name: /匿名前三/ })).toHaveLength(2)
  expect(screen.getAllByText('+10.00 分')).toHaveLength(2)
})
it.each(['v4', 'v5'] as const)('renders frozen %s three-item reports with anonymous real top scores and signed gaps', async version => {
  const old = reportFixture()
  const suite = version === 'v4' ? fixtureV4SuiteConfig() : suiteConfigSchema.parse(catalogV5['pc-fps'])
  const report = { ...old, suite, results: suite.test_ids.map((test_id, index) => ({ ...old.results[index], test_id, config: version === 'v4' ? fixtureV4Config('pc-fps', test_id) : fixtureV5Config(test_id as 'gridshot' | 'sixshot' | 'spheretrack90'), scored: { ...old.results[index].scored, metrics: test_id === 'spheretrack90' ? { coverage_ms: 30000 } : { hits_per_second: 2.5, successful_average_interval_ms: 400 } } })) }
  const ranking = { ...rankingFixture(), overall_score: 80, participant_count: 2, overall_rank: 2, top_scores: [{ rank: 1, score: 90, gap: 10 }, { rank: 2, score: 80, gap: 0 }], items: suite.test_ids.map(test_id => ({ test_id, score: 80, rank: 2, top_scores: [{ rank: 1, score: 90, gap: 10 }, { rank: 2, score: 80, gap: 0 }] })) }
  setup(url => jsonResponse(url.endsWith('/ranking') ? ranking : report))
  expect(await screen.findByRole('heading', { name: '网格射击' })).toBeVisible()
  expect(screen.getAllByText('50 分')).toHaveLength(3)
  expect(await screen.findByText(/同组真实参与者不足 3 人/)).toBeVisible()
  expect(screen.getAllByRole('table', { name: /匿名前三/ })).toHaveLength(4)
  expect(screen.getAllByText('+10.00 分')).toHaveLength(4)
  expect(screen.getAllByText('2.5 次/秒')).toHaveLength(2)
  expect(screen.getByText(/ERP 暂定场景规则/)).toBeVisible()
  expect(screen.getByText(/新旧版本记录并存/)).toBeVisible()
})
it('scopes current lane life metrics independently from duplicate current and prior comparison metrics', async () => {
  const old = reportFixture()
  const config = fixtureMobaV5Config('ranged')
  const suite = suiteConfigSchema.parse({ ...towerCatalog, tasks: { 'last-hit': config } })
  const current = { ...old, suite_id: 'pc-moba', suite, comparisons: [{ test_id: 'last-hit', previous_report_id: PREVIOUS_ID, score_delta: null, non_comparable_reasons: ['settings_changed'] }], results: [{ ...old.results[0], test_id: 'last-hit', config, scored: { ...old.results[0].scored, metrics: { hero_deaths: 2, hero_damage_taken: 2360 } } }] }
  const previous = { ...current, id: PREVIOUS_ID, comparisons: [], results: [{ ...current.results[0], scored: { ...current.results[0].scored, metrics: { hero_deaths: 4, hero_damage_taken: 4180 } } }] }
  setup(url => jsonResponse(url.endsWith('/ranking') ? { ...rankingFixture(), items: [{ test_id: 'last-hit', score: 50, rank: 1 }] } : url.endsWith(PREVIOUS_ID) ? previous : current))
  await waitFor(() => expect(screen.getAllByText('人物死亡次数')).toHaveLength(3))
  // Reproduce the native harness's ambiguous global query, then prove the
  // public current-result region/direct metric list is unambiguous.
  expect(() => screen.getByText('人物死亡次数', { exact: true })).toThrow(/multiple elements/)
  const result = screen.getByRole('region', { name: '五分钟兵线补刀 · 远程' })
  const metrics = result.querySelector<HTMLElement>(':scope > dl.assessmentReportsMetrics')!
  expect(within(metrics).getByText('人物死亡次数')).toBeVisible()
  expect(within(metrics).getByText('2 次')).toBeVisible()
  expect(within(metrics).getByText('2360 点')).toBeVisible()
  expect(within(metrics).queryByText('4 次')).toBeNull()
})
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
