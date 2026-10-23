import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { AssessmentRankingPanel } from '../assessmentRankings/AssessmentRankingPanel'
import { assessmentApi } from './api'
import { gradeLabels, type AssessmentReportView, type ReportComparison, type ReportResult } from './contracts'
import { assessmentKeys } from './keys'
import { chinaDate, FrozenSetup, hasReportPermission, isUuid, Metrics, QualityNotice, reasonLabels, ReportError, reportTaskLabel, suiteLabels, TrackingNote } from './reportPresentation'
import './reports.css'

function PriorResult({ account, current, testId, id }: { account: string; current: AssessmentReportView; testId: ReportResult['test_id']; id: string }) {
  const previous = useQuery({
    queryKey: assessmentKeys.report(account, id), queryFn: () => assessmentApi.report(id),
    retry: false, gcTime: 0, refetchOnMount: 'always',
  })
  if (previous.isError) return <p role="alert">历史报告不可用或无权查看</p>
  if (previous.isPending || previous.isFetching) return <p role="status">正在读取历史报告…</p>
  const match = previous.data.results.find(result => result.test_id === testId)
  if (!match || previous.data.suite_id !== current.suite_id || previous.data.person.person_id !== current.person.person_id) return <p role="alert">历史报告不可用或无权查看</p>
  return (
    <div>
      <h4>既往记录 · {chinaDate(previous.data.completed_at)}</h4>
      <Metrics metrics={match.scored.metrics} emptyLabel={match.config.report_version === `${match.config.suite_id}-v2` ? '—' : undefined} />
      <QualityNotice flags={match.scored.quality_flags} />
      <FrozenSetup result={match} />
    </div>
  )
}
function Comparison({ account, report, result, comparison }: { account: string; report: AssessmentReportView; result: ReportResult; comparison: ReportComparison }) {
  return (
    <section className="assessmentReportsComparison" aria-label={`${reportTaskLabel(result.config)}历史比较`}>
      <h3>历史比较</h3>
      {comparison.non_comparable_reasons.map((reason, index) => <p key={`${reason}:${index}`}>{reasonLabels[reason] ?? '该历史记录不满足自动比较条件'}</p>)}
      {comparison.score_delta !== null && !comparison.non_comparable_reasons.length && !result.scored.quality_flags.length ? (
        <p>较最近可比记录：{comparison.score_delta > 0 ? '+' : ''}{comparison.score_delta} 分</p>
      ) : null}
      {comparison.previous_report_id ? (
        <div className="assessmentReportsColumns">
          <div><h4>本次记录</h4><Metrics metrics={result.scored.metrics} emptyLabel={result.config.report_version === `${result.config.suite_id}-v2` ? '—' : undefined} /><FrozenSetup result={result} /></div>
          <PriorResult account={account} current={report} testId={result.test_id} id={comparison.previous_report_id} />
        </div>
      ) : null}
    </section>
  )
}
export function AssessmentReportPage() {
  const { user } = useAuth()
  const { reportId = '' } = useParams<{ reportId: string }>()
  const allowed = hasReportPermission(user)
  const report = useQuery({
    queryKey: assessmentKeys.report(user?.account_id ?? 'anonymous', reportId),
    queryFn: () => assessmentApi.report(reportId), enabled: allowed && isUuid(reportId),
    retry: false, gcTime: 0, refetchOnMount: 'always',
  })
  if (!user) return <p role="status">正在确认读取权限…</p>
  if (!allowed) return <p>暂无天赋评测读取权限</p>
  if (!isUuid(reportId)) return <p role="alert">报告不存在或无权查看</p>
  if (report.isError) return <ReportError error={report.error} />
  if (report.isPending) return <p role="status">正在读取报告…</p>
  const saved = report.data
  return (
    <div className="assessmentReports">
      <Link to="/esports-assessments/history?view=reports">返回已完成报告</Link>
      <header>
        <h1>{suiteLabels[saved.suite_id]}专项报告</h1>
        <p>{saved.person.display_name}</p>
        <time dateTime={saved.completed_at}>{chinaDate(saved.completed_at)}</time>
        <p>仅描述本次场景及操作条件下的表现。</p>
      </header>
      <AssessmentRankingPanel
        key={`${user.account_id}:${reportId}`}
        reportId={reportId}
        queryKey={assessmentKeys.report(user.account_id, reportId)}
        fetchRanking={assessmentApi.ranking}
        items={saved.results.map(result => ({ id: result.test_id, name: reportTaskLabel(result.config) }))}
        reportPath="/esports-assessments/reports"
        hasQualityWarnings={saved.results.some(result => result.scored.quality_flags.length > 0)}
      />
      {saved.results.map(result => {
        const comparison = saved.comparisons.find(item => item.test_id === result.test_id)
        const label = reportTaskLabel(result.config)
        return (
          <section className="assessmentReportsCard" aria-label={label} key={result.result_id}>
            <h2>{label}</h2>
            <div className="assessmentReportsScore">
              <strong>{result.scored.score} 分</strong><span>{gradeLabels[result.scored.grade]}</span>
              {result.scored.quality_flags.length ? <span>试算分／需复测</span> : null}
            </div>
            <QualityNotice flags={result.scored.quality_flags} />
            <Metrics metrics={result.scored.metrics} emptyLabel={result.config.report_version === `${result.config.suite_id}-v2` ? '—' : undefined} />
            <TrackingNote testId={result.test_id} />
            <FrozenSetup result={result} />
            {comparison ? <Comparison account={user.account_id} report={saved} result={result} comparison={comparison} /> : null}
          </section>
        )
      })}
      <section className="assessmentReportsCard"><h2>本次训练建议</h2><ul>{saved.recommendations.map((text, index) => <li key={index}>{text}</li>)}</ul></section>
    </div>
  )
}
export default AssessmentReportPage
