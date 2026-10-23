import { RefreshCw } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { formatDateOnly } from "../shared/date"
import type { SystemEvaluationTask } from "./systemEvaluationApi"
import type {
  SystemEvaluationProjection,
  SystemEvaluationTaskStatus,
} from "./systemEvaluationKeys"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import {
  useSystemEvaluationTaskFilterOptions,
  useSystemEvaluationTaskList,
} from "./useSystemEvaluationContext"

import "./system-evaluation.css"

const COMPACT_QUERY = "(max-width: 768px)"

const statusLabels: Record<SystemEvaluationTaskStatus, string> = {
  uploaded: "已上传",
  identifying: "识别中",
  pending_match: "待匹配",
  queued: "排队中",
  processing: "处理中",
  failed: "处理失败",
  manual_input: "待人工录入",
  needs_review: "待审核",
  rejected: "被驳回",
  correction_pending: "待整改",
  archived: "已归档",
  published: "已发布",
  revision_draft: "修订中",
}

const accountStatuses = Object.entries(statusLabels) as Array<
  [SystemEvaluationTaskStatus, string]
>

const accountAggregateQueues = [
  ["processing", "处理中（全部）"],
  ["manual_input", "待人工录入（全部）"],
] as const

const adminQueues = [
  ["processing", "处理中"],
  ["pending_match", "待匹配"],
  ["manual_input", "待人工录入"],
  ["needs_review", "待审核"],
  ["rejected", "被驳回"],
  ["published", "已发布"],
  ["failed", "处理失败"],
  ["archived", "已归档"],
] as const

function useCompactTaskLayout(): boolean {
  const current = () =>
    typeof window.matchMedia === "function"
      ? window.matchMedia(COMPACT_QUERY).matches
      : window.innerWidth <= 768
  const [compact, setCompact] = useState(current)
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const media = window.matchMedia(COMPACT_QUERY)
    const update = () => setCompact(media.matches)
    update()
    media.addEventListener("change", update)
    return () => media.removeEventListener("change", update)
  }, [])
  return compact
}

function positivePage(value: string | null): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed >= 1 ? parsed : 1
}

function taskDetailHref(
  projection: SystemEvaluationProjection,
  taskId: string,
): string {
  const path = `/evaluations/system/tasks/${taskId}`
  return projection === "admin" ? `${path}?actor=admin` : path
}

function formattedCreatedAt(value: string): string {
  return formatDateOnly(value)
}

function taskErrorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError
    ? error.problem.detail
    : "系统评价任务暂时无法加载，请重试。"
}

function TaskSummary({ task }: { task: SystemEvaluationTask }) {
  return (
    <>
      <span>{task.identity_summary ?? "待匹配"}</span>
      <span>{task.cohort_name ?? "未匹配营期"}</span>
      <span>{task.project_name}</span>
      <span>{task.class_name ?? "未匹配班级"}</span>
    </>
  )
}

export function EvaluationTaskList({
  projection = "account",
}: {
  projection?: SystemEvaluationProjection
}) {
  const compact = useCompactTaskLayout()
  const authorization = useEvaluationAuthorization()
  const [searchParams, setSearchParams] = useSearchParams()
  const page = positivePage(searchParams.get("page"))
  const status = searchParams.get("status") ?? undefined
  const queue = searchParams.get("queue") ?? undefined
  const publishedOnly = searchParams.get("published_only") === "true"
  const cohortId = searchParams.get("cohort_id") ?? undefined
  const projectId = searchParams.get("project_id") ?? undefined
  const classId = searchParams.get("class_id") ?? undefined
  const studentId = searchParams.get("student_id") ?? undefined
  const filters = {
    ...(projection === "account" && status ? { status } : {}),
    ...(queue ? { queue } : {}),
    ...(cohortId ? { cohortId } : {}),
    ...(projectId ? { projectId } : {}),
    ...(classId ? { classId } : {}),
    ...(studentId ? { studentId } : {}),
    ...(publishedOnly ? { publishedOnly: true } : {}),
    page,
    pageSize: 20,
  }
  const tasks = useSystemEvaluationTaskList(projection, filters)
  const filterOptions = useSystemEvaluationTaskFilterOptions(projection)

  function updateFilter(
    name:
      | "status"
      | "queue"
      | "cohort_id"
      | "project_id"
      | "class_id"
      | "student_id",
    value: string,
  ): void {
    const next = new URLSearchParams(searchParams)
    if (value) next.set(name, value)
    else next.delete(name)
    next.set("page", "1")
    setSearchParams(next)
  }

  function updateAccountFilter(value: string): void {
    const next = new URLSearchParams(searchParams)
    next.delete("status")
    next.delete("queue")
    if (value.startsWith("queue:")) next.set("queue", value.slice(6))
    else if (value) next.set("status", value)
    next.set("page", "1")
    setSearchParams(next)
  }

  function goToPage(nextPage: number): void {
    const next = new URLSearchParams(searchParams)
    next.set("page", String(nextPage))
    setSearchParams(next)
  }

  if (authorization.pending || tasks.isPending) {
    return <main className="systemEvaluationTasksPage">正在更新系统评价任务...</main>
  }
  if (tasks.isError) {
    return (
      <main className="systemEvaluationTasksPage">
        <section className="systemEvaluationState" role="alert">
          <p>{taskErrorMessage(tasks.error)}</p>
          <button type="button" onClick={() => void tasks.refetch()}>
            重试任务名单
          </button>
        </section>
      </main>
    )
  }

  const data = tasks.data
  const hasPrevious = data.page > 1
  const hasNext = data.page * data.page_size < data.total

  return (
    <main className="systemEvaluationTasksPage">
      <header className="systemEvaluationPageHeader">
        <div>
          <Link to="/evaluations">返回评价中心</Link>
          <p className="pageEyebrow">SYSTEM EVALUATION TASKS</p>
          <h1>系统评价任务</h1>
        </div>
        <button type="button" onClick={() => void tasks.refetch()}>
          <RefreshCw aria-hidden="true" />
          刷新
        </button>
      </header>

      <section className="systemEvaluationFilters" aria-label="任务筛选">
        {projection === "account" ? (
          <label>
            任务状态
            <select
              value={queue ? `queue:${queue}` : (status ?? "")}
              onChange={(event) => updateAccountFilter(event.target.value)}
            >
              <option value="">全部状态</option>
              {accountAggregateQueues.map(([value, label]) => (
                <option key={`queue:${value}`} value={`queue:${value}`}>{label}</option>
              ))}
              {accountStatuses.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
        ) : (
          <label>
            管理队列
            <select
              value={queue ?? ""}
              onChange={(event) => updateFilter("queue", event.target.value)}
            >
              <option value="">全部队列</option>
              {adminQueues.map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
        )}
        <label>
          营期
          <select
            value={cohortId ?? ""}
            disabled={filterOptions.isPending || filterOptions.isError}
            onChange={(event) => updateFilter("cohort_id", event.target.value)}
          >
            <option value="">全部营期</option>
            {filterOptions.data?.cohorts.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          项目
          <select
            value={projectId ?? ""}
            disabled={filterOptions.isPending || filterOptions.isError}
            onChange={(event) => updateFilter("project_id", event.target.value)}
          >
            <option value="">全部项目</option>
            {filterOptions.data?.projects.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          班级
          <select
            value={classId ?? ""}
            disabled={filterOptions.isPending || filterOptions.isError}
            onChange={(event) => updateFilter("class_id", event.target.value)}
          >
            <option value="">全部班级</option>
            {filterOptions.data?.classes.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
        <label>
          学员
          <select
            value={studentId ?? ""}
            disabled={filterOptions.isPending || filterOptions.isError}
            onChange={(event) => updateFilter("student_id", event.target.value)}
          >
            <option value="">全部学员</option>
            {filterOptions.data?.students.map((option) => (
              <option key={option.id} value={option.id}>{option.label}</option>
            ))}
          </select>
        </label>
      </section>

      {filterOptions.isError ? (
        <section className="systemEvaluationState" role="alert">
          <p>筛选选项暂时无法加载，请重试。</p>
          <button type="button" onClick={() => void filterOptions.refetch()}>
            重试筛选选项
          </button>
        </section>
      ) : null}

      {data.items.length === 0 ? (
        <section className="systemEvaluationState">没有符合条件的系统评价任务</section>
      ) : compact ? (
        <section className="systemEvaluationTaskCards" role="region" aria-label="系统评价任务卡片">
          {data.items.map((task) => (
            <article key={task.id} aria-label={task.public_number}>
              <header>
                <strong>{task.public_number}</strong>
                <span>{statusLabels[task.status]}</span>
              </header>
              <div className="systemEvaluationTaskCardSummary">
                <TaskSummary task={task} />
              </div>
              <p>截图 {task.media_count} 张 · {formattedCreatedAt(task.created_at)}</p>
              <Link to={taskDetailHref(projection, task.id)}>查看 {task.public_number}</Link>
            </article>
          ))}
        </section>
      ) : (
        <div className="systemEvaluationTableWrap">
          <table aria-label="系统评价任务">
            <thead>
              <tr>
                <th>任务编号</th>
                <th>学员</th>
                <th>营期 / 项目 / 班级</th>
                <th>状态</th>
                <th>创建日期</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((task) => (
                <tr key={task.id}>
                  <td>{task.public_number}</td>
                  <td>{task.identity_summary ?? "待匹配"}</td>
                  <td>
                    {task.cohort_name ?? "未匹配营期"} · {task.project_name} · {task.class_name ?? "未匹配班级"}
                  </td>
                  <td>{statusLabels[task.status]}</td>
                  <td>{formattedCreatedAt(task.created_at)}</td>
                  <td><Link to={taskDetailHref(projection, task.id)}>查看 {task.public_number}</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <nav className="systemEvaluationPager" aria-label="系统评价任务分页">
        <button type="button" disabled={!hasPrevious} onClick={() => goToPage(data.page - 1)}>
          上一页
        </button>
        <span>第 {data.page} 页 · 共 {data.total} 项</span>
        <button type="button" disabled={!hasNext} onClick={() => goToPage(data.page + 1)}>
          下一页
        </button>
      </nav>
    </main>
  )
}
