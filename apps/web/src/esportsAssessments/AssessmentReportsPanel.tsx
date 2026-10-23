import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { useAuth } from '../auth/AuthProvider'
import { assessmentApi, type ReportsQuery } from './api'
import { assessmentKeys } from './keys'
import { hasReportPermission, isUuid, ReportError, ReportPager, ReportRows, useReportPager } from './reportPresentation'
import './reports.css'

export interface AssessmentReportsPanelProps { readonly personId: string }

/** Caller keys this subtree by account and filters to reset its opaque cursor. */
export function AuthorizedReportList({ account, filters }: { account: string; filters: ReportsQuery }) {
  const pager = useReportPager()
  const query = { ...filters, cursor: pager.cursor, limit: 20 }
  const reports = useQuery({ queryKey: [...assessmentKeys.reports(account), 'list', query], queryFn: () => assessmentApi.reports(query), retry: false, gcTime: 0, refetchOnMount: 'always' })
  if (reports.isError) return <ReportError error={reports.error} />
  if (reports.isPending || reports.isFetching) return <p role="status">正在读取已授权报告…</p>
  return <><ReportRows items={reports.data.items} /><ReportPager pager={pager} next={reports.data.next_cursor} total={reports.data.visible_total} /></>
}

export function AssessmentReportsPanel({ personId }: AssessmentReportsPanelProps) {
  const { user } = useAuth()
  return <section className="assessmentReports" aria-label="天赋评测 2 报告"><h2>天赋评测 2 报告</h2>
    {!user ? <p role="status">正在确认读取权限…</p> : !hasReportPermission(user) ? <p>暂无天赋评测读取权限</p> : !isUuid(personId) ? <p role="alert">报告不存在或无权查看</p> : <>
      <AuthorizedReportList key={`${user.account_id}:${personId}`} account={user.account_id} filters={{ person_id: personId }} />
      <Link to={`/esports-assessments/history?view=reports&person_id=${personId}`}>查看全部已授权报告</Link>
    </>}
  </section>
}
