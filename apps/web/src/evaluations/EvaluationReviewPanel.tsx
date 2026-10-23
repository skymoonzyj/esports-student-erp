import { useMutation } from "@tanstack/react-query"
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react"

import { ProblemDetailsError } from "../api/client"
import { EvaluationDialogShell } from "./EvaluationDialogShell"
import {
  captureSystemEvaluationWrite,
  reviewSystemEvaluationTask,
  type SystemEvaluationReviewed,
  type SystemEvaluationTask,
  type SystemEvaluationWrite,
} from "./systemEvaluationApi"

type ReviewDecision = "" | "approved" | "rejected"

const COMPACT_QUERY = "(max-width: 768px)"

function useCompactReviewLayout(): boolean {
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

function defaultIdempotencyKey(): string {
  return globalThis.crypto.randomUUID()
}

function reviewErrorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (error.problem.code === "evaluation_review_conflict") {
      return "不能审核自己创建的评价版本。"
    }
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
  return "审核暂时无法提交，请重试。"
}

function requiresRefresh(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.status === 409 &&
    error.problem.code !== "evaluation_review_conflict"
  )
}

export function EvaluationReviewPanel({
  task,
  onRefresh,
  onReviewed,
  idempotencyKeyFactory = defaultIdempotencyKey,
  readOnlyResult,
}: {
  task: SystemEvaluationTask
  onRefresh: () => void | Promise<void>
  onReviewed?: (result: SystemEvaluationReviewed) => void | Promise<void>
  idempotencyKeyFactory?: () => string
  readOnlyResult?: ReactNode
}) {
  const compact = useCompactReviewLayout()
  const [decision, setDecision] = useState<ReviewDecision>("")
  const [reason, setReason] = useState("")
  const [message, setMessage] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)
  const ownerRef = useRef<HTMLElement>(null)
  const mobileTriggerRef = useRef<HTMLButtonElement>(null)
  const retryIdempotencyKeyRef = useRef<string | null>(null)
  const mutation = useMutation<
    SystemEvaluationReviewed,
    unknown,
    { write: SystemEvaluationWrite; idempotencyKey: string }
  >({
    mutationFn: ({ write, idempotencyKey }) =>
      reviewSystemEvaluationTask(task.id, write, idempotencyKey),
    gcTime: 0,
  })

  useEffect(() => {
    setDecision("")
    setReason("")
    setMessage(null)
    setSuccess(null)
    setMobileOpen(false)
    retryIdempotencyKeyRef.current = null
  }, [task.allowed_actions.review, task.id])

  useEffect(() => {
    if (compact) return
    setMobileOpen(false)
  }, [compact])

  if (!task.allowed_actions.review) return null

  const result = task.current_result
  const canSubmit =
    !mutation.isPending &&
    decision !== "" &&
    (decision !== "rejected" || reason.trim().length > 0)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!canSubmit) return
    setMessage(null)
    setSuccess(null)
    const write = captureSystemEvaluationWrite({
      kind: "review",
      expected_lock_version: task.lock_version,
      decision,
      rejection_reason: decision === "rejected" ? reason.trim() : null,
    })
    const idempotencyKey =
      retryIdempotencyKeyRef.current ?? idempotencyKeyFactory()
    retryIdempotencyKeyRef.current = idempotencyKey
    try {
      const reviewed = await mutation.mutateAsync({
        write,
        idempotencyKey,
      })
      retryIdempotencyKeyRef.current = null
      setReason("")
      setDecision("")
      setSuccess("审核已提交。")
      setMobileOpen(false)
      await onReviewed?.(reviewed)
    } catch (error) {
      const definiteResponse = error instanceof ProblemDetailsError
      if (definiteResponse) retryIdempotencyKeyRef.current = null
      if (
        definiteResponse &&
        (error.problem.status === 403 || error.problem.status === 404)
      ) {
        setDecision("")
        setReason("")
        setMessage(null)
        setSuccess(null)
        setMobileOpen(false)
        try {
          await onRefresh()
        } catch {
          // Access-loss forms must remain cleared even if the refresh fails.
        }
        return
      }
      setMessage(reviewErrorMessage(error))
      if (requiresRefresh(error)) {
        try {
          await onRefresh()
        } catch {
          // The stale-state prompt remains useful even when refresh is unavailable.
        }
      }
    } finally {
      mutation.reset()
    }
  }

  function closeMobileReview(): void {
    if (mutation.isPending) return
    setMobileOpen(false)
    setDecision("")
    setReason("")
    setMessage(null)
    setSuccess(null)
    retryIdempotencyKeyRef.current = null
  }

  const summary = result ? (
    <div className="systemEvaluationReviewSummary">
      <strong>{result.score} 分 · {result.grade}</strong>
      <p>{result.advice}</p>
    </div>
  ) : (
    <p>当前没有可审核结果。</p>
  )
  const form = (
    <form
      className="systemEvaluationReviewForm"
      onSubmit={(event) => void submit(event)}
    >
      <fieldset disabled={mutation.isPending || !result}>
        <legend>审核决定</legend>
        <label>
          <input
            type="radio"
            name="review-decision"
            value="approved"
            checked={decision === "approved"}
            onChange={() => {
              retryIdempotencyKeyRef.current = null
              setDecision("approved")
            }}
          />
          通过
        </label>
        <label>
          <input
            type="radio"
            name="review-decision"
            value="rejected"
            checked={decision === "rejected"}
            onChange={() => {
              retryIdempotencyKeyRef.current = null
              setDecision("rejected")
            }}
          />
          驳回
        </label>
        {decision === "rejected" ? (
          <label>
            驳回原因
            <textarea
              required
              maxLength={1000}
              value={reason}
              onChange={(event) => {
                retryIdempotencyKeyRef.current = null
                setReason(event.target.value)
              }}
            />
          </label>
        ) : null}
      </fieldset>
      {message ? <p role="alert">{message}</p> : null}
      {success ? <p role="status">{success}</p> : null}
      <div className="systemEvaluationReviewActions">
        {compact ? (
          <button
            type="button"
            disabled={mutation.isPending}
            onClick={closeMobileReview}
          >
            取消
          </button>
        ) : null}
        <button type="submit" disabled={!canSubmit}>
          {mutation.isPending ? "正在提交..." : "提交审核"}
        </button>
      </div>
    </form>
  )

  if (compact) {
    return (
      <section
        ref={ownerRef}
        className="systemEvaluationReviewPanel systemEvaluationMobileReviewLauncher"
        role="region"
        aria-label="审核评价"
        tabIndex={-1}
        data-evaluation-dialog-owner
      >
        <header>
          <div>
            <h2>审核评价</h2>
            <p>手机端使用全屏只读审核页。</p>
          </div>
          <button
            ref={mobileTriggerRef}
            type="button"
            onClick={() => {
              setMessage(null)
              setSuccess(null)
              setMobileOpen(true)
            }}
          >
            打开全屏审核
          </button>
        </header>
        {success ? <p role="status">{success}</p> : null}
        {mobileOpen ? (
          <EvaluationDialogShell
            title="审核评价"
            closeLabel="关闭审核评价"
            pending={mutation.isPending}
            onClose={closeMobileReview}
            focusFallbackRef={mobileTriggerRef}
          >
            {readOnlyResult ?? summary}
            {form}
          </EvaluationDialogShell>
        ) : null}
      </section>
    )
  }

  return (
    <section className="systemEvaluationReviewPanel" role="region" aria-label="审核评价">
      <header>
        <h2>审核评价</h2>
        <p>审核页为只读决策页，不能修改评价结果。</p>
      </header>
      {summary}
      {form}
    </section>
  )
}
