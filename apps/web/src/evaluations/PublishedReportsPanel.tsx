import { useQuery, useQueryClient } from "@tanstack/react-query"
import { BookOpenText, RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import {
  listPublishedTeachingReports,
  type PublishedTeachingReport,
} from "./reportApi"
import { reportKeys } from "./reportKeys"
import { useEvaluationAccessError } from "./useEvaluationAccessError"
import "./teaching-reports.css"

function reportKindLabel(kind: PublishedTeachingReport["report_kind"]): string {
  return kind === "daily" ? "日报" : "周报"
}

function isPrivatePublishedReportError(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.status === 404 &&
    error.problem.code === "evaluation_resource_not_found"
  )
}

export function PublishedReportsPanel({
  studentId,
  enrollmentId = null,
}: {
  studentId: string
  enrollmentId?: string | null
}) {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const accountId = user?.account_id ?? "anonymous"
  const [page, setPage] = useState(1)
  const [privateUnavailable, setPrivateUnavailable] = useState(false)
  const pageSize = 10
  const filters = { studentId, enrollmentId, page, pageSize }
  const reports = useQuery({
    queryKey: reportKeys.published(accountId, filters),
    queryFn: ({ signal }) => listPublishedTeachingReports(filters, signal),
    enabled: Boolean(user?.account_id && studentId && !privateUnavailable),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const accessError = useEvaluationAccessError(reports.error, {
    redirectToHome: true,
  })

  useEffect(() => {
    setPage(1)
    setPrivateUnavailable(false)
  }, [enrollmentId, studentId])

  useEffect(() => {
    if (
      !user?.account_id ||
      !isPrivatePublishedReportError(reports.error)
    ) {
      return
    }
    setPrivateUnavailable(true)
    const queryKey = reportKeys.published(user.account_id, {
      studentId,
      enrollmentId,
      page,
      pageSize,
    })
    const remove = () => queryClient.removeQueries({ queryKey, exact: true })
    void queryClient.cancelQueries({ queryKey, exact: true }).finally(remove)
    remove()
  }, [
    enrollmentId,
    page,
    pageSize,
    queryClient,
    reports.error,
    studentId,
    user?.account_id,
  ])

  const totalPages = reports.data
    ? Math.max(1, Math.ceil(reports.data.total / reports.data.page_size))
    : 1

  if (accessError.permissionRevoked) return null

  return (
    <section className="publishedReportsPanel" aria-labelledby="published-reports-heading">
      <header className="publishedReportsHeader">
        <div>
          <p className="pageEyebrow">COACH FEEDBACK</p>
          <h2 id="published-reports-heading">教学评语</h2>
          <p>这里只展示已经审核发布的日报和周报。</p>
        </div>
        <BookOpenText aria-hidden="true" />
      </header>

      {privateUnavailable || reports.isError ? (
        <div className="publishedReportsState error" role="alert">
          <p>已发布评语暂时无法读取。</p>
          <button
            type="button"
            aria-label="重试教学评语"
            onClick={() => {
              if (privateUnavailable) {
                setPrivateUnavailable(false)
              } else {
                void reports.refetch()
              }
            }}
          >
            <RefreshCw aria-hidden="true" /> 重试
          </button>
        </div>
      ) : reports.isPending || reports.isFetching ? (
        <div className="publishedReportsState" aria-live="polite">
          <p>正在更新教学评语…</p>
        </div>
      ) : reports.data.items.length === 0 ? (
        <div className="publishedReportsState" aria-label="暂无已发布教学评语">
          <h3>暂无已发布评语</h3>
          <p>教练评语审核发布后会显示在这里。</p>
        </div>
      ) : (
        <div className="publishedReportsList">
          {reports.data.items.map((report) => (
            <article className="publishedReportCard" key={report.report_id}>
              <header>
                <div>
                  <p>{report.project_name} · {report.class_name}</p>
                  <h3>{reportKindLabel(report.report_kind)} · 第 {report.period_sequence} 期</h3>
                </div>
                <span>版本 {report.version}</span>
              </header>
              <p className="publishedReportPeriod">
                {report.period_start_date} 至 {report.period_end_date}
              </p>
              <p className="publishedReportBody">{report.body}</p>
              <footer>
                <span>{report.author_name} · {report.role_name}</span>
                <time dateTime={report.published_at}>
                  {formatShanghaiDate(report.published_at)} 发布
                </time>
              </footer>
            </article>
          ))}
        </div>
      )}

      {reports.data && totalPages > 1 ? (
        <nav className="teachingReportPagination" aria-label="教学评语分页">
          <button
            type="button"
            aria-label="上一页评语"
            disabled={page <= 1}
            onClick={() => setPage((current) => Math.max(1, current - 1))}
          >上一页</button>
          <span>第 {page} / {totalPages} 页</span>
          <button
            type="button"
            aria-label="下一页评语"
            disabled={page >= totalPages}
            onClick={() => setPage((current) => current + 1)}
          >下一页</button>
        </nav>
      ) : null}
    </section>
  )
}
