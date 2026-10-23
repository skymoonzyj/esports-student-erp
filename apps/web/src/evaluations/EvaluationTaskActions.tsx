import { useMutation } from "@tanstack/react-query"
import { useState, type FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { EvaluationDialogShell } from "./EvaluationDialogShell"
import {
  archiveSystemEvaluationTask,
  captureSystemEvaluationWrite,
  listSystemEvaluationMatchTargets,
  matchSystemEvaluationTask,
  reopenSystemEvaluationTask,
  saveSystemEvaluationManualDraft,
  startSystemEvaluationCorrection,
  startSystemEvaluationRevision,
  submitSystemEvaluationManualDraft,
} from "./systemEvaluationApi"
import type {
  SystemEvaluationDraftSaved,
  SystemEvaluationMatched,
  SystemEvaluationMatchTarget,
  SystemEvaluationProjection,
  SystemEvaluationRevisionStarted,
  SystemEvaluationSubmitted,
  SystemEvaluationTask,
  SystemEvaluationTaskStateChanged,
  SystemEvaluationTaskWorkspace,
  SystemEvaluationWrite,
} from "./systemEvaluationApi"

function defaultIdempotencyKey(): string {
  return globalThis.crypto.randomUUID()
}

function safeActionError(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (
      error.problem.status === 409 &&
      [
        "evaluation_stale_version",
        "evaluation_invalid_transition",
        "evaluation_idempotency_conflict",
      ].includes(error.problem.code)
    ) {
      return "任务状态已变化，请刷新详情并比较后重试。"
    }
    return error.problem.detail
  }
  return "任务操作暂时无法完成，请重试。"
}

function editedScalar(value: string, original: unknown): string | number | boolean {
  if (typeof original === "number") {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : value
  }
  if (typeof original === "boolean") {
    if (value === "true") return true
    if (value === "false") return false
  }
  return value
}

async function closeAfterAccessLoss(
  error: unknown,
  onRefresh: () => void | Promise<void>,
  onClose: () => void,
): Promise<boolean> {
  if (
    !(error instanceof ProblemDetailsError) ||
    ![403, 404].includes(error.problem.status)
  ) {
    return false
  }
  try {
    await onRefresh()
  } catch {
    // Private forms must still close when the authoritative refresh also fails.
  }
  onClose()
  return true
}

function MatchDialog({
  projection,
  task,
  onClose,
  onRefresh,
  idempotencyKeyFactory,
}: {
  projection: SystemEvaluationProjection
  task: SystemEvaluationTask
  onClose: () => void
  onRefresh: () => void | Promise<void>
  idempotencyKeyFactory: () => string
}) {
  const [query, setQuery] = useState("")
  const [candidates, setCandidates] = useState<SystemEvaluationMatchTarget[]>([])
  const [selectedEnrollmentId, setSelectedEnrollmentId] = useState("")
  const [searching, setSearching] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const mutation = useMutation<
    SystemEvaluationMatched,
    unknown,
    { write: SystemEvaluationWrite; idempotencyKey: string }
  >({
    mutationFn: ({ write, idempotencyKey }) =>
      matchSystemEvaluationTask(projection, task.id, write, idempotencyKey),
    gcTime: 0,
  })

  const selected = candidates.find(
    (candidate) => candidate.enrollment_id === selectedEnrollmentId,
  )

  async function search(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setSearching(true)
    setMessage(null)
    setSelectedEnrollmentId("")
    try {
      const result = await listSystemEvaluationMatchTargets(task.id, {
        query,
        page: 1,
        pageSize: 20,
      })
      setCandidates(result.items)
    } catch (error) {
      setCandidates([])
      setMessage(safeActionError(error))
      await closeAfterAccessLoss(error, onRefresh, onClose)
    } finally {
      setSearching(false)
    }
  }

  async function confirm(): Promise<void> {
    if (!selected || mutation.isPending) return
    setMessage(null)
    const write = captureSystemEvaluationWrite({
      kind: "match",
      expected_lock_version: task.lock_version,
      student_person_id: selected.student_person_id,
      enrollment_id: selected.enrollment_id,
      cohort_id: selected.cohort_id,
      class_id: selected.class_id,
    })
    try {
      await mutation.mutateAsync({
        write,
        idempotencyKey: idempotencyKeyFactory(),
      })
      await onRefresh()
      onClose()
    } catch (error) {
      setMessage(safeActionError(error))
      if (await closeAfterAccessLoss(error, onRefresh, onClose)) {
        // The dialog and its private local state have been discarded.
      } else if (
        error instanceof ProblemDetailsError &&
        error.problem.status === 409
      ) {
        try {
          await onRefresh()
        } catch {
          // Keep the selected candidate visible for comparison.
        }
      }
    } finally {
      mutation.reset()
    }
  }

  return (
    <EvaluationDialogShell
      title="匹配学员"
      closeLabel="关闭匹配学员"
      pending={searching || mutation.isPending}
      onClose={onClose}
    >
      <form onSubmit={(event) => void search(event)}>
        <label>
          查询学员
          <input
            data-autofocus
            value={query}
            maxLength={100}
            autoComplete="off"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="submit" disabled={searching || mutation.isPending}>
          {searching ? "正在查询..." : "查询候选学员"}
        </button>
      </form>

      {candidates.length ? (
        <fieldset disabled={mutation.isPending}>
          <legend>候选学员</legend>
          {candidates.map((candidate) => {
            const label = [
              candidate.student_name,
              candidate.student_number,
              candidate.cohort_name,
              candidate.class_name,
            ].join(" · ")
            return (
              <label key={`${candidate.student_person_id}:${candidate.enrollment_id}`}>
                <input
                  type="radio"
                  name="system-evaluation-match-target"
                  value={candidate.enrollment_id}
                  checked={selectedEnrollmentId === candidate.enrollment_id}
                  onChange={() => setSelectedEnrollmentId(candidate.enrollment_id)}
                />
                {label}
              </label>
            )
          })}
        </fieldset>
      ) : null}
      {message ? <p role="alert">{message}</p> : null}
      <div>
        <button type="button" disabled={searching || mutation.isPending} onClick={onClose}>
          取消
        </button>
        <button
          type="button"
          disabled={!selected || searching || mutation.isPending}
          onClick={() => void confirm()}
        >
          {mutation.isPending ? "正在匹配..." : "确认匹配"}
        </button>
      </div>
    </EvaluationDialogShell>
  )
}

function ManualDraftDialog({
  projection,
  task,
  workspace,
  onClose,
  onRefresh,
}: {
  projection: SystemEvaluationProjection
  task: SystemEvaluationTask
  workspace: SystemEvaluationTaskWorkspace
  onClose: () => void
  onRefresh: () => void | Promise<void>
}) {
  const initialFieldKeys = Array.from(new Set([
    ...workspace.profile.required_fields,
    ...Object.keys(workspace.draft?.fields ?? {}),
  ]))
  const [fields, setFields] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      initialFieldKeys.map((key) => {
        const value = workspace.draft?.fields[key]
        return [key, value === null || value === undefined ? "" : String(value)]
      }),
    ),
  )
  const [score, setScore] = useState(
    workspace.draft?.score === null || workspace.draft?.score === undefined
      ? ""
      : String(workspace.draft.score),
  )
  const [grade, setGrade] = useState(workspace.draft?.grade ?? "")
  const [componentSummary, setComponentSummary] = useState(
    workspace.draft?.components[0]?.summary ?? "",
  )
  const [advice, setAdvice] = useState(workspace.draft?.advice ?? "")
  const [message, setMessage] = useState<string | null>(null)
  const mutation = useMutation<
    SystemEvaluationDraftSaved,
    unknown,
    SystemEvaluationWrite
  >({
    mutationFn: (write) => saveSystemEvaluationManualDraft(projection, task.id, write),
    gcTime: 0,
  })

  async function save(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (mutation.isPending) return
    setMessage(null)
    const normalizedFields = Object.entries(fields)
      .filter(([, value]) => value.trim().length > 0)
      .map(([key, value]) => {
        const normalized = value.trim()
        const original = workspace.draft?.fields[key]
        const typedValue = editedScalar(normalized, original)
        return { key, value: typedValue }
      })
    const missingFields = workspace.profile.required_fields.filter(
      (key) => !fields[key]?.trim(),
    )
    const numericScore = score.trim() === "" ? null : Number(score)
    const normalizedScore = Number.isFinite(numericScore) ? numericScore : null
    const existingComponents = workspace.draft?.components ?? []
    const normalizedComponentSummary = componentSummary.trim()
    const components = existingComponents.length > 0
      ? existingComponents.flatMap((component, index) => {
          const summary = index === 0 ? normalizedComponentSummary : component.summary.trim()
          return summary ? [{ ...component, summary }] : []
        })
      : normalizedScore !== null && normalizedComponentSummary
        ? [
            {
              key: "overall",
              score: normalizedScore,
              weight: 1,
              summary: normalizedComponentSummary,
            },
          ]
        : []
    const write = captureSystemEvaluationWrite({
      kind: "manual_draft",
      expected_lock_version: task.lock_version,
      draft_revision: workspace.draft?.draft_revision ?? null,
      fields: normalizedFields,
      confidences: normalizedFields.map(({ key }) => ({
        key,
        confidence: workspace.draft?.confidences[key] ?? 1,
      })),
      missing_fields: missingFields,
      warnings: workspace.draft?.warnings ?? [],
      components,
      score: normalizedScore,
      grade: grade.trim() || null,
      advice: advice.trim() || null,
    })
    try {
      await mutation.mutateAsync(write)
      await onRefresh()
      onClose()
    } catch (error) {
      setMessage(safeActionError(error))
      if (await closeAfterAccessLoss(error, onRefresh, onClose)) {
        // The dialog and its private local state have been discarded.
      } else if (
        error instanceof ProblemDetailsError &&
        error.problem.status === 409
      ) {
        try {
          await onRefresh()
        } catch {
          // Keep the operator's draft visible for comparison.
        }
      }
    } finally {
      mutation.reset()
    }
  }

  return (
    <EvaluationDialogShell
      title="人工录入系统评价"
      closeLabel="关闭人工录入"
      pending={mutation.isPending}
      onClose={onClose}
    >
      <form onSubmit={(event) => void save(event)}>
        <fieldset disabled={mutation.isPending}>
          <legend>项目评价字段</legend>
          {initialFieldKeys.map((key, index) => (
            <label key={key}>
              字段 {key}
              <input
                data-autofocus={index === 0 ? "" : undefined}
                value={fields[key] ?? ""}
                onChange={(event) =>
                  setFields((current) => ({ ...current, [key]: event.target.value }))
                }
              />
            </label>
          ))}
        </fieldset>
        <label>
          总分
          <input
            type="number"
            min="0"
            max="100"
            step="0.01"
            value={score}
            onChange={(event) => setScore(event.target.value)}
          />
        </label>
        <label>
          等级
          <input maxLength={24} value={grade} onChange={(event) => setGrade(event.target.value)} />
        </label>
        <label>
          分项说明
          <textarea
            maxLength={500}
            value={componentSummary}
            onChange={(event) => setComponentSummary(event.target.value)}
          />
        </label>
        <label>
          训练建议
          <textarea
            maxLength={4000}
            value={advice}
            onChange={(event) => setAdvice(event.target.value)}
          />
        </label>
        {message ? <p role="alert">{message}</p> : null}
        <div>
          <button type="button" disabled={mutation.isPending} onClick={onClose}>取消</button>
          <button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "正在保存..." : "保存草稿"}
          </button>
        </div>
      </form>
    </EvaluationDialogShell>
  )
}

function SubmitReviewDialog({
  task,
  workspace,
  onClose,
  onRefresh,
  idempotencyKeyFactory,
}: {
  task: SystemEvaluationTask
  workspace: SystemEvaluationTaskWorkspace
  onClose: () => void
  onRefresh: () => void | Promise<void>
  idempotencyKeyFactory: () => string
}) {
  const [message, setMessage] = useState<string | null>(null)
  const mutation = useMutation<
    SystemEvaluationSubmitted,
    unknown,
    { write: SystemEvaluationWrite; idempotencyKey: string }
  >({
    mutationFn: ({ write, idempotencyKey }) =>
      submitSystemEvaluationManualDraft(task.id, write, idempotencyKey),
    gcTime: 0,
  })
  const draft = workspace.draft
  const explicitlyMissing = new Set(draft?.missing_fields ?? [])
  const requiredFieldsCovered = workspace.profile.required_fields.every((key) => {
    const value = draft?.fields[key]
    return (
      (value !== null && value !== undefined && String(value).trim().length > 0) ||
      explicitlyMissing.has(key)
    )
  })
  const fieldKeys = draft ? Object.keys(draft.fields) : []
  const confidencesComplete = Boolean(
    draft &&
    fieldKeys.length === Object.keys(draft.confidences).length &&
    fieldKeys.every((key) => key in draft.confidences),
  )
  const complete = Boolean(
    draft &&
    requiredFieldsCovered &&
    confidencesComplete &&
    draft.components.length > 0 &&
    draft.score !== null &&
    draft.grade?.trim() &&
    draft.advice?.trim(),
  )

  async function submit(): Promise<void> {
    if (!draft || !complete || mutation.isPending) return
    setMessage(null)
    const write = captureSystemEvaluationWrite({
      kind: "submit_review",
      expected_lock_version: task.lock_version,
      draft_revision: draft.draft_revision,
    })
    try {
      await mutation.mutateAsync({
        write,
        idempotencyKey: idempotencyKeyFactory(),
      })
      await onRefresh()
      onClose()
    } catch (error) {
      setMessage(safeActionError(error))
      if (await closeAfterAccessLoss(error, onRefresh, onClose)) {
        // The dialog and its private local state have been discarded.
      } else if (
        error instanceof ProblemDetailsError &&
        error.problem.status === 409
      ) {
        try {
          await onRefresh()
        } catch {
          // Keep the current draft summary visible for comparison.
        }
      }
    } finally {
      mutation.reset()
    }
  }

  return (
    <EvaluationDialogShell
      title="提交人工评价审核"
      closeLabel="关闭提交审核"
      pending={mutation.isPending}
      onClose={onClose}
    >
      {draft ? <p>草稿版本 {draft.draft_revision}</p> : <p role="alert">尚无可提交草稿。</p>}
      {!complete && draft ? <p role="alert">草稿仍有必填内容未完成。</p> : null}
      {message ? <p role="alert">{message}</p> : null}
      <div>
        <button type="button" disabled={mutation.isPending} onClick={onClose}>取消</button>
        <button
          type="button"
          disabled={!complete || mutation.isPending}
          onClick={() => void submit()}
        >
          {mutation.isPending ? "正在提交..." : "确认提交审核"}
        </button>
      </div>
    </EvaluationDialogShell>
  )
}

function ResultVersionDialog({
  kind,
  task,
  workspace,
  onClose,
  onRefresh,
  idempotencyKeyFactory,
}: {
  kind: "correction" | "revision"
  task: SystemEvaluationTask
  workspace: SystemEvaluationTaskWorkspace
  onClose: () => void
  onRefresh: () => void | Promise<void>
  idempotencyKeyFactory: () => string
}) {
  const [mode, setMode] = useState<"manual" | "automatic" | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const mutation = useMutation<
    SystemEvaluationRevisionStarted,
    unknown,
    { write: SystemEvaluationWrite; idempotencyKey: string }
  >({
    mutationFn: ({ write, idempotencyKey }) =>
      kind === "correction"
        ? startSystemEvaluationCorrection(task.id, write, idempotencyKey)
        : startSystemEvaluationRevision(task.id, write, idempotencyKey),
    gcTime: 0,
  })

  async function submit(): Promise<void> {
    if (!mode || mutation.isPending) return
    setMessage(null)
    const write = captureSystemEvaluationWrite({
      kind,
      expected_lock_version: task.lock_version,
      mode,
    })
    try {
      await mutation.mutateAsync({
        write,
        idempotencyKey: idempotencyKeyFactory(),
      })
      await onRefresh()
      onClose()
    } catch (error) {
      setMessage(safeActionError(error))
      if (await closeAfterAccessLoss(error, onRefresh, onClose)) {
        // The dialog and its private local state have been discarded.
      } else if (
        error instanceof ProblemDetailsError &&
        error.problem.status === 409
      ) {
        try {
          await onRefresh()
        } catch {
          // Keep the selected mode visible for comparison.
        }
      }
    } finally {
      mutation.reset()
    }
  }

  const title = kind === "correction" ? "开始评价整改" : "创建发布后修订"
  const automaticAllowed =
    kind === "revision" && workspace.profile.mode === "automatic"
  return (
    <EvaluationDialogShell
      title={title}
      closeLabel={`关闭${title}`}
      pending={mutation.isPending}
      onClose={onClose}
    >
      <fieldset disabled={mutation.isPending}>
        <legend>处理方式</legend>
        <label>
          <input
            data-autofocus
            type="radio"
            name={`system-evaluation-${kind}-mode`}
            checked={mode === "manual"}
            onChange={() => setMode("manual")}
          />
          人工处理
        </label>
        {automaticAllowed ? (
          <label>
            <input
              type="radio"
              name={`system-evaluation-${kind}-mode`}
              checked={mode === "automatic"}
              onChange={() => setMode("automatic")}
            />
            自动处理
          </label>
        ) : null}
      </fieldset>
      {message ? <p role="alert">{message}</p> : null}
      <div>
        <button type="button" disabled={mutation.isPending} onClick={onClose}>取消</button>
        <button
          type="button"
          disabled={!mode || mutation.isPending}
          onClick={() => void submit()}
        >
          {mutation.isPending ? "正在开始..." : "确认开始"}
        </button>
      </div>
    </EvaluationDialogShell>
  )
}

function TaskStateDialog({
  kind,
  task,
  onClose,
  onRefresh,
  idempotencyKeyFactory,
}: {
  kind: "archive" | "reopen"
  task: SystemEvaluationTask
  onClose: () => void
  onRefresh: () => void | Promise<void>
  idempotencyKeyFactory: () => string
}) {
  const [reason, setReason] = useState("")
  const [resume, setResume] = useState<"automatic" | "correction" | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const mutation = useMutation<
    SystemEvaluationTaskStateChanged,
    unknown,
    { write: SystemEvaluationWrite; idempotencyKey: string }
  >({
    mutationFn: ({ write, idempotencyKey }) =>
      kind === "archive"
        ? archiveSystemEvaluationTask(task.id, write, idempotencyKey)
        : reopenSystemEvaluationTask(task.id, write, idempotencyKey),
    gcTime: 0,
  })
  const automaticResumeAllowed = task.allowed_reopen_modes.includes("automatic")
  const correctionResumeAllowed = task.allowed_reopen_modes.includes("correction")
  const ready = Boolean(reason.trim() && (kind === "archive" || resume))

  async function submit(): Promise<void> {
    if (!ready || mutation.isPending) return
    setMessage(null)
    const write = kind === "archive"
      ? captureSystemEvaluationWrite({
          kind: "archive",
          expected_lock_version: task.lock_version,
          reason: reason.trim(),
        })
      : captureSystemEvaluationWrite({
          kind: "reopen",
          expected_lock_version: task.lock_version,
          resume: resume ?? "correction",
          reason: reason.trim(),
        })
    try {
      await mutation.mutateAsync({
        write,
        idempotencyKey: idempotencyKeyFactory(),
      })
      await onRefresh()
      onClose()
    } catch (error) {
      setMessage(safeActionError(error))
      if (await closeAfterAccessLoss(error, onRefresh, onClose)) {
        // The dialog and its private local state have been discarded.
      } else if (
        error instanceof ProblemDetailsError &&
        error.problem.status === 409
      ) {
        try {
          await onRefresh()
        } catch {
          // Keep the reason and selected resume mode visible for comparison.
        }
      }
    } finally {
      mutation.reset()
    }
  }

  const title = kind === "archive" ? "归档任务" : "重开任务"
  return (
    <EvaluationDialogShell
      title={title}
      closeLabel={`关闭${title}`}
      pending={mutation.isPending}
      onClose={onClose}
    >
      {kind === "reopen" ? (
        <fieldset disabled={mutation.isPending}>
          <legend>重开方式</legend>
          {automaticResumeAllowed ? (
            <label>
              <input
                type="radio"
                name="system-evaluation-reopen-mode"
                checked={resume === "automatic"}
                onChange={() => setResume("automatic")}
              />
              继续自动处理
            </label>
          ) : null}
          {correctionResumeAllowed ? (
            <label>
              <input
                type="radio"
                name="system-evaluation-reopen-mode"
                checked={resume === "correction"}
                onChange={() => setResume("correction")}
              />
              转入整改
            </label>
          ) : null}
          {!automaticResumeAllowed && !correctionResumeAllowed ? (
            <p role="alert">当前任务没有可用的重开方式。</p>
          ) : null}
        </fieldset>
      ) : null}
      <label>
        操作原因
        <textarea
          data-autofocus
          required
          maxLength={1000}
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </label>
      {message ? <p role="alert">{message}</p> : null}
      <div>
        <button type="button" disabled={mutation.isPending} onClick={onClose}>取消</button>
        <button
          type="button"
          disabled={!ready || mutation.isPending}
          onClick={() => void submit()}
        >
          {mutation.isPending
            ? kind === "archive" ? "正在归档..." : "正在重开..."
            : kind === "archive" ? "确认归档" : "确认重开"}
        </button>
      </div>
    </EvaluationDialogShell>
  )
}

export function EvaluationTaskActions({
  projection,
  task,
  workspace,
  onRefresh,
  idempotencyKeyFactory = defaultIdempotencyKey,
}: {
  projection: SystemEvaluationProjection
  task: SystemEvaluationTask
  workspace: SystemEvaluationTaskWorkspace
  onRefresh: () => void | Promise<void>
  idempotencyKeyFactory?: () => string
}) {
  const [dialog, setDialog] = useState<
    | "match"
    | "manual"
    | "submit"
    | "correction"
    | "revision"
    | "archive"
    | "reopen"
    | null
  >(null)
  const actions = task.allowed_actions
  const controls = [
    [actions.match, "匹配学员", () => setDialog("match")],
    [actions.save_manual_draft, "人工录入", () => setDialog("manual")],
    [actions.submit_review, "提交审核", () => setDialog("submit")],
    [actions.create_correction, "开始整改", () => setDialog("correction")],
    [actions.create_revision, "创建修订", () => setDialog("revision")],
    [actions.archive, "归档任务", () => setDialog("archive")],
    [actions.reopen, "重开任务", () => setDialog("reopen")],
  ] as const
  const visibleControls = controls.filter(([visible]) => visible)
  if (!visibleControls.length) return null

  return (
    <section role="region" aria-label="任务操作">
      <h2>任务操作</h2>
      <div>
        {visibleControls.map(([, label, onClick]) => (
          <button key={label} type="button" onClick={onClick}>{label}</button>
        ))}
      </div>
      {dialog === "match" ? (
        <MatchDialog
          projection={projection}
          task={task}
          onClose={() => setDialog(null)}
          onRefresh={onRefresh}
          idempotencyKeyFactory={idempotencyKeyFactory}
        />
      ) : null}
      {dialog === "manual" ? (
        <ManualDraftDialog
          projection={projection}
          task={task}
          workspace={workspace}
          onClose={() => setDialog(null)}
          onRefresh={onRefresh}
        />
      ) : null}
      {dialog === "submit" ? (
        <SubmitReviewDialog
          task={task}
          workspace={workspace}
          onClose={() => setDialog(null)}
          onRefresh={onRefresh}
          idempotencyKeyFactory={idempotencyKeyFactory}
        />
      ) : null}
      {dialog === "correction" || dialog === "revision" ? (
        <ResultVersionDialog
          kind={dialog}
          task={task}
          workspace={workspace}
          onClose={() => setDialog(null)}
          onRefresh={onRefresh}
          idempotencyKeyFactory={idempotencyKeyFactory}
        />
      ) : null}
      {dialog === "archive" || dialog === "reopen" ? (
        <TaskStateDialog
          kind={dialog}
          task={task}
          onClose={() => setDialog(null)}
          onRefresh={onRefresh}
          idempotencyKeyFactory={idempotencyKeyFactory}
        />
      ) : null}
    </section>
  )
}
