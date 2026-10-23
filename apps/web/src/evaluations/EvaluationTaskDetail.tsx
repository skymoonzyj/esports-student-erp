import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useId, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { evaluationKeys } from "./api"
import { EvaluationReviewPanel } from "./EvaluationReviewPanel"
import { EvaluationTaskActions } from "./EvaluationTaskActions"
import type {
  SystemEvaluationResult,
  SystemEvaluationTask,
  SystemEvaluationWorkspaceMedia,
} from "./systemEvaluationApi"
import { systemEvaluationPreviewUrl } from "./systemEvaluationApi"
import type { SystemEvaluationProjection } from "./systemEvaluationKeys"
import { systemEvaluationKeys } from "./systemEvaluationKeys"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import { useEvaluationAccessError } from "./useEvaluationAccessError"
import {
  useSystemEvaluationTaskDetail,
  useSystemEvaluationTaskWorkspace,
} from "./useSystemEvaluationContext"

import "./system-evaluation.css"

const statusLabels: Record<SystemEvaluationTask["status"], string> = {
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

const sourceLabels: Record<SystemEvaluationResult["source"], string> = {
  automatic: "系统自动评价",
  manual: "人工评价",
  correction: "整改版本",
  revision: "发布后修订",
}

const decisionLabels: Record<SystemEvaluationResult["review_decision"], string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已驳回",
}

function errorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError
    ? error.problem.detail
    : "系统评价详情暂时无法加载，请重试。"
}

function scalarText(value: string | number | boolean | null): string {
  if (value === null) return "空"
  if (typeof value === "boolean") return value ? "是" : "否"
  return String(value)
}

function confidenceText(value: number | undefined): string {
  return value === undefined ? "—" : `${Math.round(value * 100)}%`
}

export function EvaluationResultContent({ value }: { value: SystemEvaluationResult }) {
  const fieldsTitleId = useId()
  return (
    <section className="systemEvaluationResult" role="region" aria-label="评价结果">
      <header>
        <div>
          <p>{sourceLabels[value.source]}</p>
          <h2>评价结果 V{value.version}</h2>
        </div>
        <div className="systemEvaluationScoreSummary">
          <div role="group" aria-label="总分"><span>总分</span><strong>{value.score}</strong></div>
          <div role="group" aria-label="等级"><span>等级</span><strong>{value.grade}</strong></div>
        </div>
      </header>

      <section aria-labelledby={fieldsTitleId}>
        <h3 id={fieldsTitleId}>结构化字段与置信度</h3>
        <dl className="systemEvaluationFields">
          {Object.entries(value.fields).map(([key, fieldValue]) => (
            <div key={key}>
              <dt>{key}</dt>
              <dd>{scalarText(fieldValue)} · {confidenceText(value.confidences[key])}</dd>
            </div>
          ))}
        </dl>
      </section>

      {value.missing_fields.length ? (
        <section>
          <h3>缺失字段</h3>
          <p>{value.missing_fields.join("、")}</p>
        </section>
      ) : null}
      {value.warnings.length ? (
        <section>
          <h3>识别警告</h3>
          <ul>
            {value.warnings.map((warning) => (
              <li key={warning.key}>{warning.summary}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {value.components.length ? (
        <section>
          <h3>评分分项</h3>
          <div className="systemEvaluationComponents">
            {value.components.map((component) => (
              <article key={component.key}>
                <strong>{component.key}</strong>
                <span>{component.score} · 权重 {Math.round(component.weight * 100)}%</span>
                <p>{component.summary}</p>
              </article>
            ))}
          </div>
        </section>
      ) : null}
      <section>
        <h3>训练建议</h3>
        <p>{value.advice}</p>
      </section>
      <footer>
        {value.adapter_id ?? "人工"} · {value.adapter_version ?? "无适配器版本"} · {value.scoring_rule_version ?? "无评分规则版本"} · {value.result_schema_version}
      </footer>
    </section>
  )
}

function PreviewItem({
  taskId,
  media,
  label,
}: {
  taskId: string
  media: SystemEvaluationWorkspaceMedia
  label: string
}) {
  const [state, setState] = useState<"loading" | "loaded" | "error">("loading")
  return (
    <figure>
      <img
        src={systemEvaluationPreviewUrl(taskId, media.id)}
        alt={`${label}预览`}
        referrerPolicy="same-origin"
        onLoad={() => setState("loaded")}
        onError={() => setState("error")}
      />
      {state === "loading" ? <figcaption>正在加载{label}预览</figcaption> : null}
      {state === "loaded" ? <figcaption>{label}</figcaption> : null}
      {state === "error" ? <figcaption role="alert">{label}预览加载失败</figcaption> : null}
    </figure>
  )
}

function ResultHistory({ task }: { task: SystemEvaluationTask }) {
  const versionsById = new Map(task.result_versions.map((version) => [version.id, version]))
  return (
    <section className="systemEvaluationHistory" role="region" aria-label="结果版本历史">
      <h2>结果版本历史</h2>
      <div>
        {task.result_versions.map((version) => {
          const previous = version.previous_version_id
            ? versionsById.get(version.previous_version_id)
            : undefined
          return (
            <article key={version.id} aria-label={`结果版本 V${version.version}`}>
              <header>
                <strong>V{version.version}</strong>
                <span>{sourceLabels[version.source]} · {decisionLabels[version.review_decision]}</span>
              </header>
              {previous ? <p>基于 V{previous.version}</p> : null}
              {task.current_result?.id === version.id ? <p>当前工作版本 V{version.version}</p> : null}
              {task.published_result?.id === version.id ? <p>当前发布版本 V{version.version}</p> : null}
            </article>
          )
        })}
      </div>
    </section>
  )
}

export function EvaluationTaskDetail({
  projection: projectionOverride,
}: {
  projection?: SystemEvaluationProjection
} = {}) {
  const authorization = useEvaluationAuthorization()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { taskId = "" } = useParams()
  const [searchParams] = useSearchParams()
  const projection =
    projectionOverride ?? (searchParams.get("actor") === "admin" ? "admin" : "account")
  const detail = useSystemEvaluationTaskDetail(projection, taskId)
  const workspace = useSystemEvaluationTaskWorkspace(projection, taskId)
  const accessError = useEvaluationAccessError([detail.error, workspace.error])
  const privateTaskLost = [detail.error, workspace.error].some(
    (error) =>
      error instanceof ProblemDetailsError &&
      error.problem.status === 404 &&
      error.problem.code === "evaluation_resource_not_found",
  )
  const taskCapabilityLost = [detail.error, workspace.error].some(
    (error) =>
      error instanceof ProblemDetailsError &&
      error.problem.status === 403 &&
      error.problem.code === "evaluation_operation_denied",
  )
  const taskAccessLost = privateTaskLost || taskCapabilityLost

  useEffect(() => {
    if (!taskAccessLost || !user?.account_id || !taskId) return
    const detailKey = systemEvaluationKeys.detail(user.account_id, projection, taskId)
    const workspaceKey = systemEvaluationKeys.workspace(
      user.account_id,
      projection,
      taskId,
    )
    const purge = (queryKey: readonly unknown[]) => {
      const remove = () => queryClient.removeQueries({ queryKey })
      void queryClient.cancelQueries({ queryKey }).finally(remove)
      remove()
    }
    if (taskCapabilityLost) {
      purge(systemEvaluationKeys.projection(user.account_id, projection))
      void queryClient.invalidateQueries({
        queryKey: evaluationKeys.context(user.account_id),
      })
    } else {
      purge(detailKey)
      purge(workspaceKey)
    }
    void queryClient.invalidateQueries({
      queryKey: systemEvaluationKeys.dashboard(user.account_id),
    })
    navigate(
      taskCapabilityLost ? "/evaluations" : "/evaluations/system/tasks",
      { replace: true },
    )
  }, [
    navigate,
    privateTaskLost,
    projection,
    queryClient,
    taskAccessLost,
    taskCapabilityLost,
    taskId,
    user?.account_id,
  ])

  if (
    authorization.pending ||
    accessError.permissionRevoked ||
    taskAccessLost ||
    detail.isPending ||
    workspace.isPending
  ) {
    return <main className="systemEvaluationTaskDetailPage">正在更新系统评价详情...</main>
  }
  const loadError = detail.error ?? workspace.error
  if (loadError) {
    return (
      <main className="systemEvaluationTaskDetailPage">
        <section className="systemEvaluationState" role="alert">
          <p>{errorMessage(loadError)}</p>
          <button
            type="button"
            onClick={() => {
              void detail.refetch()
              void workspace.refetch()
            }}
          >
            重试评价详情
          </button>
        </section>
      </main>
    )
  }
  if (!detail.data || !workspace.data) {
    return <main className="systemEvaluationTaskDetailPage">正在更新系统评价详情...</main>
  }

  const task = detail.data.task
  const displayResult = task.current_result ?? task.published_result
  const mediaLabels = new Map(
    workspace.data.profile.media_slots.map((slot) => [slot.key, slot.label]),
  )

  async function refreshTaskData(): Promise<void> {
    const refreshes: Array<Promise<unknown>> = [detail.refetch(), workspace.refetch()]
    if (user?.account_id) {
      refreshes.push(
        queryClient.invalidateQueries({
          queryKey: systemEvaluationKeys.dashboard(user.account_id),
          exact: true,
        }),
        queryClient.invalidateQueries({
          queryKey: [
            ...systemEvaluationKeys.projection(user.account_id, projection),
            "lists",
          ],
        }),
      )
      if (task.student_person_id) {
        refreshes.push(
          queryClient.invalidateQueries({
            queryKey: [
              ...systemEvaluationKeys.root(user.account_id),
              "published",
              task.student_person_id,
            ],
          }),
        )
      }
    }
    await Promise.all(refreshes)
  }

  return (
    <main className="systemEvaluationTaskDetailPage">
      <header className="systemEvaluationPageHeader">
        <div>
          <Link to="/evaluations/system/tasks">返回任务名单</Link>
          <p className="pageEyebrow">SYSTEM EVALUATION DETAIL</p>
          <h1>{task.public_number}</h1>
          <p>{task.identity_summary ?? "待匹配"} · {task.project_name} · {statusLabels[task.status]}</p>
          <p>任务版本 {task.lock_version}</p>
        </div>
      </header>

      <div className="systemEvaluationDetailLayout">
        {task.allowed_actions.view_preview && workspace.data.media.length ? (
          <section className="systemEvaluationPreview" role="region" aria-label="截图预览">
            <h2>截图预览</h2>
            <div>
              {workspace.data.media.map((media) => (
                <PreviewItem
                  key={media.id}
                  taskId={task.id}
                  media={media}
                  label={mediaLabels.get(media.slot_key) ?? media.slot_key}
                />
              ))}
            </div>
          </section>
        ) : null}

        {displayResult ? (
          <EvaluationResultContent value={displayResult} />
        ) : (
          <section className="systemEvaluationState" role="region" aria-label="评价结果">
            暂无评价结果
          </section>
        )}
      </div>

      <EvaluationTaskActions
        projection={projection}
        task={task}
        workspace={workspace.data}
        onRefresh={refreshTaskData}
      />

      <EvaluationReviewPanel
        task={task}
        readOnlyResult={
          task.current_result ? (
            <EvaluationResultContent value={task.current_result} />
          ) : null
        }
        onRefresh={refreshTaskData}
        onReviewed={refreshTaskData}
      />

      <ResultHistory task={task} />
    </main>
  )
}
