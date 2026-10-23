import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckCircle2, RefreshCw, ShieldCheck } from "lucide-react"
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
} from "react"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import { EvaluationDialogShell } from "./EvaluationDialogShell"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import { ReportVersionTimeline } from "./ReportVersionTimeline"
import {
  captureTeachingReportWrite,
  listTeachingReportReviewQueue,
  reviewTeachingReport,
  type TeachingReportReviewQueueItem,
  type TeachingReportReviewQueuePage,
  type TeachingReportReviewResponse,
  type TeachingReportWrite,
} from "./reportApi"
import { reportKeys, type ReviewQueueFilters } from "./reportKeys"
import "./teaching-reports.css"

type ReviewDecision = "" | "approved" | "rejected"

type ReviewRetryAttempt = {
  reportId: string
  idempotencyKey: string
  expectedLockVersion: number
}

function reportKindLabel(kind: TeachingReportReviewQueueItem["report_kind"]): string {
  return kind === "daily" ? "日报" : "周报"
}

function reviewErrorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError) {
    if (error.problem.code === "evaluation_review_conflict") {
      return "不能审核自己提交的评语。"
    }
    if (error.problem.status === 409) {
      return "评语状态已经变化，请刷新队列后重试。"
    }
    return "审核暂时无法提交，请重试。"
  }
  return "审核暂时无法提交，请重试。"
}

function uniqueOptions(
  items: readonly TeachingReportReviewQueueItem[],
  id: "cohort_id" | "project_id" | "class_id",
  name: "cohort_name" | "project_name" | "class_name",
): Array<{ id: string; name: string }> {
  return Array.from(
    new Map(items.map((item) => [item[id], item[name]])).entries(),
    ([optionId, optionName]) => ({ id: optionId, name: optionName }),
  )
}

function defaultIdempotencyKey(): string {
  return globalThis.crypto.randomUUID()
}

export function TeachingReportReviewPage({
  idempotencyKeyFactory = defaultIdempotencyKey,
}: {
  idempotencyKeyFactory?: () => string
}) {
  const { user } = useAuth()
  const { pending, revision } = useEvaluationAuthorization()
  const queryClient = useQueryClient()
  const accountId = user?.account_id ?? "anonymous"
  const [filters, setFilters] = useState<ReviewQueueFilters>({
    cohortId: null,
    projectId: null,
    classId: null,
    page: 1,
    pageSize: 20,
  })
  const [selectedReportId, setSelectedReportId] = useState<string | null>(null)
  const [dialogItem, setDialogItem] =
    useState<TeachingReportReviewQueueItem | null>(null)
  const [decision, setDecision] = useState<ReviewDecision>("")
  const [reasonPresent, setReasonPresent] = useState(false)
  const [dialogError, setDialogError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [uncertainRetry, setUncertainRetry] = useState(false)
  const retryAttemptRef = useRef<ReviewRetryAttempt | null>(null)
  const queue = useQuery({
    queryKey: [
      ...reportKeys.reviewQueue(accountId, filters),
      { authorizationRevision: revision },
    ],
    queryFn: ({ signal }) => listTeachingReportReviewQueue(filters, signal),
    enabled: Boolean(user?.account_id && !pending),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const mutation = useMutation<
    TeachingReportReviewResponse,
    unknown,
    { reportId: string; write: TeachingReportWrite; idempotencyKey: string }
  >({
    mutationKey: [...reportKeys.root(accountId), "mutation", "review"],
    mutationFn: ({ reportId, write, idempotencyKey }) =>
      reviewTeachingReport(reportId, write, idempotencyKey),
    gcTime: 0,
  })

  useEffect(() => {
    if (!pending) return
    setDialogItem(null)
    setDecision("")
    setReasonPresent(false)
    setDialogError(null)
    setUncertainRetry(false)
    retryAttemptRef.current = null
  }, [pending])

  const items = queue.data?.items ?? []
  const dialogAuthoritativeAvailable = Boolean(
    dialogItem && items.some((item) => item.report_id === dialogItem.report_id),
  )
  const selected =
    items.find((item) => item.report_id === selectedReportId) ?? items[0] ?? null
  const cohorts = useMemo(
    () => uniqueOptions(items, "cohort_id", "cohort_name"),
    [items],
  )
  const projects = useMemo(
    () => uniqueOptions(items, "project_id", "project_name"),
    [items],
  )
  const classes = useMemo(
    () => uniqueOptions(items, "class_id", "class_name"),
    [items],
  )

  useEffect(() => {
    if (selected && selected.report_id !== selectedReportId) {
      setSelectedReportId(selected.report_id)
    }
  }, [selected, selectedReportId])

  useEffect(() => {
    if (!dialogItem) return
    const refreshed = items.find(
      (item) => item.report_id === dialogItem.report_id,
    )
    if (refreshed && refreshed !== dialogItem) setDialogItem(refreshed)
  }, [dialogItem, items])

  function updateFilter(
    key: "cohortId" | "projectId" | "classId",
    value: string,
  ): void {
    setFilters((current) => ({
      ...current,
      [key]: value || null,
      page: 1,
    }))
    setSelectedReportId(null)
  }

  function openReview(item: TeachingReportReviewQueueItem): void {
    setDialogItem(item)
    setDecision("")
    setReasonPresent(false)
    setDialogError(null)
    setUncertainRetry(false)
    retryAttemptRef.current = null
  }

  function closeReview(): void {
    if (mutation.isPending) return
    setDialogItem(null)
    setDecision("")
    setReasonPresent(false)
    setDialogError(null)
    setUncertainRetry(false)
    retryAttemptRef.current = null
  }

  const invalidateReviewWorkspace = useCallback(
    async (reportId: string): Promise<void> => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: [...reportKeys.root(accountId), "review-queue"],
        }),
        queryClient.invalidateQueries({
          queryKey: [
            ...reportKeys.root(accountId),
            "versions",
            { reportId },
          ],
        }),
        queryClient.invalidateQueries({
          queryKey: reportKeys.detail(accountId, reportId),
        }),
      ])
    },
    [accountId, queryClient],
  )

  const recoverPrivateReport = useCallback(
    async (reportId: string): Promise<void> => {
      const queueKey = [
        ...reportKeys.root(accountId),
        "review-queue",
      ] as const
      const versionsKey = [
        ...reportKeys.root(accountId),
        "versions",
        { reportId },
      ] as const
      const detailKey = reportKeys.detail(accountId, reportId)
      void queryClient.cancelQueries({ queryKey: queueKey })
      void queryClient.cancelQueries({ queryKey: versionsKey })
      void queryClient.cancelQueries({ queryKey: detailKey })
      queryClient.setQueriesData<TeachingReportReviewQueuePage>(
        { queryKey: queueKey },
        (page) => {
          if (!page) return page
          const items = page.items.filter((item) => item.report_id !== reportId)
          const removed = page.items.length - items.length
          return removed === 0
            ? page
            : { ...page, items, total: Math.max(0, page.total - removed) }
        },
      )
      queryClient.removeQueries({ queryKey: versionsKey })
      queryClient.removeQueries({ queryKey: detailKey })
      setDialogItem(null)
      setSelectedReportId(null)
      setDecision("")
      setReasonPresent(false)
      setDialogError(null)
      setUncertainRetry(false)
      await invalidateReviewWorkspace(reportId)
    },
    [accountId, invalidateReviewWorkspace, queryClient],
  )

  async function submitReview(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (queue.isFetching || queue.isError) return
    const authoritativeItem = items.find(
      (item) => item.report_id === dialogItem?.report_id,
    )
    if (!dialogItem || !authoritativeItem || decision === "") return
    const formData = new FormData(event.currentTarget)
    const reason = String(formData.get("reason") ?? "").trim()
    if (decision === "rejected" && !reason) return
    setDialogError(null)
    setSuccess(null)
    const currentAttempt = retryAttemptRef.current
    const attempt =
      uncertainRetry && currentAttempt?.reportId === dialogItem.report_id
        ? currentAttempt
        : {
            reportId: dialogItem.report_id,
            idempotencyKey: idempotencyKeyFactory(),
            expectedLockVersion: authoritativeItem.lock_version,
          }
    retryAttemptRef.current = attempt
    const write = captureTeachingReportWrite({
      kind: "review",
      expected_lock_version: attempt.expectedLockVersion,
      decision,
      reason: decision === "rejected" ? reason : null,
    })
    try {
      await mutation.mutateAsync({
        reportId: dialogItem.report_id,
        write,
        idempotencyKey: attempt.idempotencyKey,
      })
      retryAttemptRef.current = null
      setUncertainRetry(false)
      setSuccess("审核已提交。")
      setDialogItem(null)
      setSelectedReportId(null)
      await invalidateReviewWorkspace(dialogItem.report_id)
    } catch (error) {
      if (error instanceof ProblemDetailsError) {
        retryAttemptRef.current = null
        setUncertainRetry(false)
        if (
          error.problem.status === 404 &&
          error.problem.code === "evaluation_resource_not_found"
        ) {
          await recoverPrivateReport(dialogItem.report_id)
          return
        }
        if (
          error.problem.status === 409 &&
          error.problem.code !== "evaluation_review_conflict"
        ) {
          await invalidateReviewWorkspace(dialogItem.report_id)
        }
      } else {
        setUncertainRetry(true)
      }
      setDialogError(reviewErrorMessage(error))
    } finally {
      mutation.reset()
    }
  }

  if (pending || queue.isPending) {
    return (
      <main className="teachingReportPage">
        <header className="teachingReportHeader">
          <p className="pageEyebrow">TEACHING REPORTS</p>
          <h1>教学评语审核</h1>
        </header>
        <section className="teachingReportState" aria-live="polite">
          <p>正在更新审核队列…</p>
        </section>
      </main>
    )
  }

  if (queue.isError && !queue.data) {
    return (
      <main className="teachingReportPage">
        <header className="teachingReportHeader">
          <p className="pageEyebrow">TEACHING REPORTS</p>
          <h1>教学评语审核</h1>
        </header>
        <section className="teachingReportState error" role="alert">
          <p>审核队列暂时无法读取。</p>
          <button type="button" onClick={() => void queue.refetch()}>
            <RefreshCw aria-hidden="true" /> 重试
          </button>
        </section>
      </main>
    )
  }

  const totalPages = Math.max(
    1,
    Math.ceil((queue.data?.total ?? 0) / (queue.data?.page_size ?? 20)),
  )

  return (
    <main
      className="teachingReportPage teachingReportReviewPage"
      data-evaluation-dialog-owner
      tabIndex={-1}
    >
      <header className="teachingReportHeader">
        <div>
          <p className="pageEyebrow">TEACHING REPORTS</p>
          <h1>教学评语审核</h1>
          <p>审核教练提交的日报和周报，发布后家庭端即可查看。</p>
        </div>
        <ShieldCheck aria-hidden="true" />
      </header>

      {success ? (
        <p className="teachingReportNotice success" role="status">
          <CheckCircle2 aria-hidden="true" /> {success}
        </p>
      ) : null}

      <section className="teachingReportFilters" aria-label="审核筛选">
        <label>
          <span>营期</span>
          <select
            value={filters.cohortId ?? ""}
            onChange={(event) => updateFilter("cohortId", event.target.value)}
          >
            <option value="">全部营期</option>
            {cohorts.map((option) => (
              <option key={option.id} value={option.id}>{option.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>项目</span>
          <select
            value={filters.projectId ?? ""}
            onChange={(event) => updateFilter("projectId", event.target.value)}
          >
            <option value="">全部项目</option>
            {projects.map((option) => (
              <option key={option.id} value={option.id}>{option.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>班级</span>
          <select
            value={filters.classId ?? ""}
            onChange={(event) => updateFilter("classId", event.target.value)}
          >
            <option value="">全部班级</option>
            {classes.map((option) => (
              <option key={option.id} value={option.id}>{option.name}</option>
            ))}
          </select>
        </label>
      </section>

      {items.length === 0 ? (
        <section className="teachingReportState" aria-label="暂无待审核评语">
          <h2>暂无待审核评语</h2>
          <p>新的日报或周报提交后会出现在这里。</p>
        </section>
      ) : (
        <div className="teachingReportWorkspace">
          <section className="teachingReportQueue" aria-label="待审核评语">
            {items.map((item) => (
              <article
                key={item.report_id}
                className={
                  item.report_id === selected?.report_id
                    ? "teachingReportQueueCard selected"
                    : "teachingReportQueueCard"
                }
              >
                <button
                  type="button"
                  className="teachingReportQueueSelect"
                  onClick={() => setSelectedReportId(item.report_id)}
                >
                  <span>{item.project_name} · {item.class_name}</span>
                  <strong>{reportKindLabel(item.report_kind)} 第 {item.period_sequence} 期</strong>
                  <small>{item.author_name} · {item.role_name}</small>
                </button>
                <button
                  type="button"
                  className="teachingReportPrimaryAction"
                  aria-label={`审核${item.author_name}的${reportKindLabel(item.report_kind)}`}
                  onClick={() => openReview(item)}
                >
                  审核
                </button>
              </article>
            ))}
          </section>

          {selected ? (
            <article className="teachingReportDetail">
              <header>
                <div>
                  <p>{selected.cohort_name} · {selected.project_name} · {selected.class_name}</p>
                  <h2>{reportKindLabel(selected.report_kind)} 第 {selected.period_sequence} 期</h2>
                </div>
                <span>版本 {selected.version}</span>
              </header>
              <dl className="teachingReportFacts">
                <div><dt>作者</dt><dd>{selected.author_name} · {selected.role_name}</dd></div>
                <div><dt>周期</dt><dd>{selected.period_start_date} 至 {selected.period_end_date}</dd></div>
                <div><dt>提交日期</dt><dd>{formatShanghaiDate(selected.submitted_at)}</dd></div>
              </dl>
              <section className="teachingReportBody" aria-label="评语正文">
                <p>{selected.body}</p>
              </section>
              <ReportVersionTimeline
                reportId={selected.report_id}
                compareCurrent
                onUnavailable={recoverPrivateReport}
              />
            </article>
          ) : null}
        </div>
      )}

      {totalPages > 1 ? (
        <nav className="teachingReportPagination" aria-label="审核队列分页">
          <button
            type="button"
            disabled={(filters.page ?? 1) <= 1}
            onClick={() => setFilters((current) => ({
              ...current,
              page: Math.max(1, (current.page ?? 1) - 1),
            }))}
          >上一页</button>
          <span>第 {filters.page ?? 1} / {totalPages} 页</span>
          <button
            type="button"
            disabled={(filters.page ?? 1) >= totalPages}
            onClick={() => setFilters((current) => ({
              ...current,
              page: (current.page ?? 1) + 1,
            }))}
          >下一页</button>
        </nav>
      ) : null}

      {!pending && dialogItem ? (
        <EvaluationDialogShell
          title="审核教学评语"
          closeLabel="关闭教学评语审核"
          pending={mutation.isPending}
          onClose={closeReview}
        >
          <form className="teachingReportReviewForm" onSubmit={submitReview}>
            <p>{dialogItem.author_name} · {reportKindLabel(dialogItem.report_kind)} 第 {dialogItem.period_sequence} 期</p>
            <fieldset>
              <legend>审核决定</legend>
              <label>
                <input
                  type="radio"
                  name="decision"
                  value="approved"
                  checked={decision === "approved"}
                  disabled={
                    mutation.isPending ||
                    queue.isFetching ||
                    queue.isError ||
                    !dialogAuthoritativeAvailable ||
                    uncertainRetry
                  }
                  onChange={() => setDecision("approved")}
                />
                通过
              </label>
              <label>
                <input
                  type="radio"
                  name="decision"
                  value="rejected"
                  checked={decision === "rejected"}
                  disabled={
                    mutation.isPending ||
                    queue.isFetching ||
                    queue.isError ||
                    !dialogAuthoritativeAvailable ||
                    uncertainRetry
                  }
                  onChange={() => setDecision("rejected")}
                />
                驳回
              </label>
            </fieldset>
            {decision === "rejected" ? (
              <label>
                <span>驳回原因</span>
                <textarea
                  name="reason"
                  aria-label="驳回原因"
                  data-autofocus
                  maxLength={1000}
                  required
                  readOnly={
                    queue.isFetching ||
                    queue.isError ||
                    !dialogAuthoritativeAvailable ||
                    uncertainRetry
                  }
                  onInput={(event) =>
                    setReasonPresent(event.currentTarget.value.trim().length > 0)
                  }
                />
              </label>
            ) : null}
            {dialogError ? <p className="teachingReportFormError" role="alert">{dialogError}</p> : null}
            <div className="teachingReportDialogActions">
              <button type="button" disabled={mutation.isPending} onClick={closeReview}>取消</button>
              <button
                type="submit"
                disabled={
                  mutation.isPending ||
                  queue.isFetching ||
                  queue.isError ||
                  decision === "" ||
                  !dialogAuthoritativeAvailable ||
                  (decision === "rejected" && !reasonPresent)
                }
              >
                {mutation.isPending ? "正在提交…" : "确认审核"}
              </button>
            </div>
          </form>
        </EvaluationDialogShell>
      ) : null}
    </main>
  )
}
