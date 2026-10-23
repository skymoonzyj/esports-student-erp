import { useQuery } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { useEffect, useId, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import {
  listTeachingReportVersions,
  type TeachingReportVersion,
} from "./reportApi"
import { reportKeys } from "./reportKeys"

export type ReportVersionTimelineProps = {
  reportId: string
  compareCurrent?: boolean
  onUnavailable?: (reportId: string) => Promise<void> | void
}

const sourceLabels: Record<TeachingReportVersion["source"], string> = {
  initial: "首次提交",
  correction: "整改提交",
  revision: "修订提交",
}

function VersionPanel({ version }: { version: TeachingReportVersion }) {
  return (
    <article
      className="teachingReportVersionCard"
      aria-label={`教学评语版本 ${version.version}`}
    >
      <header>
        <div>
          <h3>版本 {version.version}</h3>
          <p>{sourceLabels[version.source]}</p>
        </div>
        <div className="teachingReportVersionBadges">
          {version.is_current ? <span>当前版本</span> : null}
          {version.is_published ? <span>已发布版本</span> : null}
        </div>
      </header>
      <time dateTime={version.submitted_at}>
        {formatShanghaiDate(version.submitted_at)} 提交
      </time>
      <p className="teachingReportVersionBody">{version.body}</p>
      {version.review ? (
        <footer>
          <strong>
            {version.review.decision === "approved" ? "审核通过" : "审核驳回"}
          </strong>
          <time dateTime={version.review.reviewed_at}>
            {formatShanghaiDate(version.review.reviewed_at)}
          </time>
          {version.review.decision === "rejected" && version.review.reason ? (
            <p>{version.review.reason}</p>
          ) : null}
        </footer>
      ) : (
        <footer><span>等待审核</span></footer>
      )}
    </article>
  )
}

export function ReportVersionTimeline({
  reportId,
  compareCurrent = false,
  onUnavailable,
}: ReportVersionTimelineProps) {
  const { user } = useAuth()
  const { pending, revision } = useEvaluationAuthorization()
  const accountId = user?.account_id ?? "anonymous"
  const headingId = useId()
  const [pageState, setPageState] = useState({ reportId, page: 1 })
  const page = pageState.reportId === reportId ? pageState.page : 1
  const pageSize = 20
  const filters = { reportId, page, pageSize }
  const versions = useQuery({
    queryKey: [
      ...reportKeys.versions(accountId, filters),
      { authorizationRevision: revision },
    ],
    queryFn: ({ signal }) => listTeachingReportVersions(filters, signal),
    enabled: Boolean(user?.account_id && reportId && !pending),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })

  useEffect(() => {
    const error = versions.error
    if (
      !onUnavailable ||
      !(error instanceof ProblemDetailsError) ||
      error.problem.status !== 404 ||
      error.problem.code !== "evaluation_resource_not_found"
    ) {
      return
    }
    void (async () => {
      try {
        await onUnavailable(reportId)
      } catch {
        // The parent keeps this private response non-disclosing if refresh fails.
      }
    })()
  }, [onUnavailable, reportId, versions.error])

  if (pending) return null

  const totalPages = versions.data
    ? Math.max(1, Math.ceil(versions.data.total / versions.data.page_size))
    : 1
  const items = versions.data?.items ?? []
  const compared = compareCurrent && items.length >= 2 ? items.slice(0, 2) : []
  const remaining = compared.length > 0 ? items.slice(2) : items

  return (
    <section className="teachingReportVersionTimeline" aria-labelledby={headingId}>
      <header>
        <h2 id={headingId}>版本记录</h2>
        <p>提交版本不可覆盖，当前草稿不会显示在这里。</p>
      </header>

      {versions.isPending || versions.isFetching ? (
        <div className="teachingReportVersionState" aria-live="polite">
          <p>正在更新版本记录…</p>
        </div>
      ) : versions.isError ? (
        <div className="teachingReportVersionState error" role="alert">
          <p>版本记录暂时无法读取。</p>
          <button
            type="button"
            aria-label="重试版本记录"
            onClick={() => void versions.refetch()}
          >
            <RefreshCw aria-hidden="true" /> 重试
          </button>
        </div>
      ) : items.length === 0 ? (
        <div className="teachingReportVersionState" aria-label="暂无教学评语版本">
          <p>尚无已提交版本。</p>
        </div>
      ) : (
        <>
          {compared.length === 2 ? (
            <div
              className="teachingReportVersionComparison"
              role="region"
              aria-label={`版本 ${compared[0]?.version} 与版本 ${compared[1]?.version} 对比`}
            >
              {compared.map((version) => (
                <VersionPanel key={version.version_id} version={version} />
              ))}
            </div>
          ) : null}
          {remaining.length > 0 ? (
            <div className="teachingReportVersionList">
              {remaining.map((version) => (
                <VersionPanel key={version.version_id} version={version} />
              ))}
            </div>
          ) : null}
        </>
      )}

      {versions.data && totalPages > 1 ? (
        <nav className="teachingReportPagination" aria-label="版本记录分页">
          <button
            type="button"
            aria-label="上一页版本"
            disabled={page <= 1}
            onClick={() =>
              setPageState({ reportId, page: Math.max(1, page - 1) })
            }
          >
            上一页
          </button>
          <span>第 {page} / {totalPages} 页</span>
          <button
            type="button"
            aria-label="下一页版本"
            disabled={page >= totalPages}
            onClick={() => setPageState({ reportId, page: page + 1 })}
          >
            下一页
          </button>
        </nav>
      ) : null}
    </section>
  )
}
