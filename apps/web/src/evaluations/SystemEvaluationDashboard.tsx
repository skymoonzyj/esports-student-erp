import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { getSystemEvaluationDashboard } from "./systemEvaluationApi"
import type { SystemEvaluationDashboard as DashboardData } from "./systemEvaluationApi"
import { systemEvaluationKeys } from "./systemEvaluationKeys"

import "./system-evaluation.css"

type CountMetric = {
  key: keyof Pick<
    DashboardData,
    "processing" | "pending_match" | "manual_input" | "needs_review" | "rejected" | "published"
  >
  label: string
  href: string
}

const countMetrics: CountMetric[] = [
  { key: "processing", label: "处理中", href: "/evaluations/system/tasks?queue=processing" },
  { key: "pending_match", label: "待匹配", href: "/evaluations/system/tasks?status=pending_match" },
  { key: "manual_input", label: "待人工录入", href: "/evaluations/system/tasks?queue=manual_input" },
  { key: "needs_review", label: "待审核", href: "/evaluations/system/tasks?status=needs_review" },
  { key: "rejected", label: "被驳回", href: "/evaluations/system/tasks?status=rejected" },
  { key: "published", label: "已发布", href: "/evaluations/system/tasks?published_only=true" },
]

function dashboardErrorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError
    ? error.problem.detail
    : "系统评价概览暂时无法更新，请重试。"
}

export function SystemEvaluationDashboard() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const dashboard = useQuery({
    queryKey: systemEvaluationKeys.dashboard(accountId),
    queryFn: getSystemEvaluationDashboard,
    enabled: Boolean(user?.account_id),
    staleTime: 0,
    gcTime: 0,
    refetchOnWindowFocus: false,
  })

  if (dashboard.isPending) {
    return (
      <section className="systemEvaluationState" aria-live="polite">
        正在更新系统评价概览...
      </section>
    )
  }
  if (dashboard.isError) {
    return (
      <section className="systemEvaluationState" role="alert">
        <p>{dashboardErrorMessage(dashboard.error)}</p>
        <button type="button" onClick={() => void dashboard.refetch()}>
          重试概览
        </button>
      </section>
    )
  }

  return (
    <section className="systemEvaluationDashboard" aria-labelledby="system-evaluation-dashboard-title">
      <header>
        <h2 id="system-evaluation-dashboard-title">系统评价概览</h2>
        <p>任务数量由 ERP 实时授权范围计算。</p>
      </header>
      <div className="systemEvaluationMetricGrid">
        <article className="systemEvaluationMetric" aria-label="可上传">
          <span>可上传</span>
          <strong>{dashboard.data.upload_available ? "可用" : "未开放"}</strong>
          {dashboard.data.upload_available ? (
            <Link to="/evaluations/system">打开截图评价</Link>
          ) : null}
        </article>
        {countMetrics.map((metric) => (
          <article key={metric.key} className="systemEvaluationMetric" aria-label={metric.label}>
            <span>{metric.label}</span>
            <strong>{dashboard.data[metric.key]}</strong>
            <Link to={metric.href}>查看{metric.label}</Link>
          </article>
        ))}
      </div>
    </section>
  )
}
