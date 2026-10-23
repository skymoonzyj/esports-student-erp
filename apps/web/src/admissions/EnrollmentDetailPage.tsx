import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRightLeft,
  Ban,
  CalendarX2,
  CalendarClock,
  CheckCircle2,
  X,
} from "lucide-react"
import { useEffect, useId, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { createPortal } from "react-dom"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { TeachingSectionNav } from "../teaching/TeachingSectionNav"
import {
  cancelEnrollment,
  confirmWithdrawal,
  getEnrollment,
  getEnrollmentActionContext,
  listEnrollmentTransferOptions,
  previewWithdrawal,
  transferEnrollment,
  updateEnrollmentSchedule,
} from "./api"
import type {
  Enrollment,
  EnrollmentScheduleMode,
  RefundPolicy,
  TransferEnrollmentInput,
  WithdrawalPreview,
} from "./api"
import { formatCny } from "./money"
import { OrderNumber } from "./OrderNumber"
import {
  dateInputToShanghaiEndExclusiveIso,
  dateInputToShanghaiIso,
  exclusiveEndToDateInput,
  formatExclusiveEndDate,
  formatShanghaiDate,
  isoToShanghaiDate,
  todayDateInput,
} from "../teaching/datetime"
import "../teaching/teaching.css"
import "./admissions.css"

type EnrollmentOperation = "transfer" | "cancel" | "withdrawal" | "schedule" | null

const ENROLLMENT_STATUS_LABELS = {
  active: "在营",
  cancelled: "已取消",
  completed: "已结营",
} as const

const PAYMENT_STATUS_LABELS = {
  unpaid: "未付款",
  partial: "部分付款",
  paid: "已付清",
  overpaid: "有多付款",
  refunded: "已退款",
} as const

function formatDateTime(value: string): string {
  return formatShanghaiDate(value)
}

function problemMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ProblemDetailsError)) return fallback
  if (error.problem.code === "class_capacity_reached") {
    return "目标班级名额刚刚发生变化，请重新确认。"
  }
  if (error.problem.code === "capacity_override_reason_required") {
    return "目标班级已满，请填写超额转班原因后重新确认。"
  }
  if (error.problem.code === "enrollment_schedule_invalid") {
    return "参加日期设置无效，请检查开始和结束日期。"
  }
  return error.problem.detail
}

async function invalidateEnrollmentData(
  queryClient: ReturnType<typeof useQueryClient>,
  enrollmentId: string,
  cohortId: string,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["enrollment", enrollmentId] }),
    queryClient.invalidateQueries({
      queryKey: ["enrollment-action-context", enrollmentId],
    }),
    queryClient.invalidateQueries({
      queryKey: ["enrollment-transfer-options", enrollmentId],
    }),
    queryClient.invalidateQueries({ queryKey: ["enrollments"] }),
    queryClient.invalidateQueries({ queryKey: ["admission-cases"] }),
    queryClient.invalidateQueries({ queryKey: ["cohort-students", cohortId] }),
  ])
}

function EnrollmentOperationDialog({
  title,
  pending,
  onClose,
  children,
}: {
  title: string
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const titleId = useId()
  const backdropRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const closeRef = useRef(onClose)
  const pendingRef = useRef(pending)
  closeRef.current = onClose
  pendingRef.current = pending

  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const backdrop = backdropRef.current
    const dialog = dialogRef.current
    if (!backdrop || !dialog) return

    const backgroundStates = Array.from(document.body.children)
      .filter(
        (element): element is HTMLElement =>
          element instanceof HTMLElement && element !== backdrop,
      )
      .map((element) => ({ element, hadInert: element.hasAttribute("inert") }))
    for (const { element } of backgroundStates) element.setAttribute("inert", "")

    const focusableSelector = [
      "button:not([disabled])",
      "a[href]",
      "input:not([disabled])",
      "select:not([disabled])",
      "textarea:not([disabled])",
      '[tabindex]:not([tabindex="-1"])',
    ].join(",")
    const focusableElements = (): HTMLElement[] =>
      Array.from(dialog.querySelectorAll<HTMLElement>(focusableSelector)).filter(
        (element) => !element.hasAttribute("hidden"),
      )
    focusableElements()[0]?.focus()

    const handleKeys = (event: KeyboardEvent): void => {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        closeRef.current()
        return
      }
      if (event.key !== "Tab") return
      const focusable = focusableElements()
      if (!focusable.length) {
        event.preventDefault()
        dialog.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const active = document.activeElement
      if (event.shiftKey && (active === first || !dialog.contains(active))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && active === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener("keydown", handleKeys)
    return () => {
      document.removeEventListener("keydown", handleKeys)
      for (const { element, hadInert } of backgroundStates) {
        if (!hadInert) element.removeAttribute("inert")
      }
      previousFocus?.focus()
    }
  }, [])

  return createPortal(
    <div
      ref={backdropRef}
      className="admissionsDialogBackdrop"
      onMouseDown={pending ? undefined : onClose}
    >
      <section
        ref={dialogRef}
        className="admissionsDialog enrollmentOperationDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            className="admissionsIconButton"
            aria-label={`关闭${title}`}
            title="关闭"
            disabled={pending}
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>,
    document.body,
  )
}

function EnrollmentScheduleDialog({
  enrollment,
  onClose,
  onComplete,
}: {
  enrollment: Enrollment
  onClose: () => void
  onComplete: () => void
}) {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<EnrollmentScheduleMode>(
    enrollment.schedule_mode ?? "cohort",
  )
  const [startsAt, setStartsAt] = useState(() =>
    isoToShanghaiDate(
      enrollment.custom_starts_at ??
        enrollment.effective_starts_at ??
        enrollment.confirmed_at,
    ),
  )
  const [endsAt, setEndsAt] = useState(() =>
    exclusiveEndToDateInput(
      enrollment.custom_ends_at ??
        enrollment.effective_ends_at ??
        enrollment.updated_at,
    ),
  )
  const [validationError, setValidationError] = useState<string | null>(null)
  const mutation = useMutation({
    mutationFn: () =>
      updateEnrollmentSchedule(enrollment.id, {
        schedule_mode: mode,
        custom_starts_at: mode === "custom" ? dateInputToShanghaiIso(startsAt) : null,
        custom_ends_at: mode === "custom" ? dateInputToShanghaiEndExclusiveIso(endsAt) : null,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["enrollment", enrollment.id] }),
        queryClient.invalidateQueries({ queryKey: ["enrollments"] }),
        queryClient.invalidateQueries({ queryKey: ["cohort-students", enrollment.cohort_id] }),
      ])
      onComplete()
    },
  })

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (mutation.isPending) return
    if (mode === "custom" && (!startsAt || !endsAt || endsAt <= startsAt)) {
      setValidationError("结束日期必须晚于开始日期")
      return
    }
    try {
      setValidationError(null)
      mutation.mutate()
    } catch (error) {
      setValidationError(error instanceof Error ? error.message : "日期格式无效")
    }
  }

  return (
    <EnrollmentOperationDialog
      title="调整学员参加时间"
      pending={mutation.isPending}
      onClose={onClose}
    >
      <form className="admissionsDialogForm" onSubmit={submit}>
        <fieldset disabled={mutation.isPending} className="enrollmentScheduleFields">
          <legend>日期模式</legend>
          <label className="admissionCheckboxField">
            <input
              type="radio"
              name="schedule-edit-mode"
              checked={mode === "cohort"}
              onChange={() => setMode("cohort")}
            />
            跟随营期
          </label>
          <label className="admissionCheckboxField">
            <input
              type="radio"
              name="schedule-edit-mode"
              checked={mode === "custom"}
              onChange={() => setMode("custom")}
            />
            单独设置日期
          </label>
          {mode === "custom" ? (
            <div className="tuitionOverrideFields">
              <label>
                <span>实际开始日期</span>
                <input
                  required
                  type="date"
                  value={startsAt}
                  onChange={(event) => setStartsAt(event.target.value)}
                />
              </label>
              <label>
                <span>实际结束日期</span>
                <input
                  required
                  type="date"
                  value={endsAt}
                  onChange={(event) => setEndsAt(event.target.value)}
                />
              </label>
            </div>
          ) : null}
        </fieldset>
        {validationError ? (
          <div className="admissionsInlineError" role="alert">{validationError}</div>
        ) : null}
            {mutation.isError ? (
          <div className="admissionsInlineError" role="alert">
            {problemMessage(mutation.error, "参加日期保存失败，请稍后重试。")}
          </div>
        ) : null}
        <footer>
          <button type="button" className="teachingSecondaryButton" disabled={mutation.isPending} onClick={onClose}>
            取消
          </button>
          <button type="submit" className="teachingPrimaryButton" disabled={mutation.isPending}>
            <CheckCircle2 aria-hidden="true" />
            {mutation.isPending ? "正在保存…" : "保存参加日期"}
          </button>
        </footer>
      </form>
    </EnrollmentOperationDialog>
  )
}

function TransferDialog({
  enrollment,
  onClose,
  onComplete,
}: {
  enrollment: Enrollment
  onClose: () => void
  onComplete: () => void
}) {
  const queryClient = useQueryClient()
  const [selectedClassId, setSelectedClassId] = useState("")
  const [reason, setReason] = useState("")
  const [capacityReason, setCapacityReason] = useState("")
  const submitGate = useRef(false)
  const options = useQuery({
    queryKey: ["enrollment-transfer-options", enrollment.id],
    queryFn: () =>
      listEnrollmentTransferOptions(enrollment.id, { page: 1, pageSize: 100 }),
  })
  const selectedOption = options.data?.items.find(
    (option) => option.class_id === selectedClassId,
  )
  const mutation = useMutation({
    mutationFn: (input: TransferEnrollmentInput) =>
      transferEnrollment(enrollment.id, input),
    onSuccess: async (updated) => {
      await invalidateEnrollmentData(
        queryClient,
        enrollment.id,
        updated.cohort_id,
      )
      onComplete()
    },
    onError: async (error) => {
      if (
        error instanceof ProblemDetailsError &&
        ["class_capacity_reached", "capacity_override_reason_required"].includes(
          error.problem.code,
        )
      ) {
        await queryClient.invalidateQueries({
          queryKey: ["enrollment-transfer-options", enrollment.id],
        })
      }
    },
  })
  const needsCapacityReason =
    selectedOption?.is_full && selectedOption.capacity_override_allowed
  const submitDisabled =
    mutation.isPending ||
    !selectedOption ||
    !reason.trim() ||
    (needsCapacityReason && !capacityReason.trim())

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (submitDisabled || submitGate.current || !selectedOption) return
    submitGate.current = true
    mutation.mutate(
      {
        target_class_id: selectedOption.class_id,
        reason: reason.trim(),
        capacity_override_reason: needsCapacityReason
          ? capacityReason.trim()
          : null,
      },
      { onSettled: () => (submitGate.current = false) },
    )
  }

  return (
    <EnrollmentOperationDialog
      title="转班"
      pending={mutation.isPending}
      onClose={onClose}
    >
      <form className="admissionsDialogForm" onSubmit={submit}>
        <p className="admissionsDialogHint">
          仅显示同一营期内、当前账号有权管理的启用班级。
        </p>
        {options.isLoading ? (
          <div className="enrollmentDialogState">正在读取可转班班级…</div>
        ) : options.isError ? (
          <div className="admissionsInlineError" role="alert">
            可转班班级暂时无法读取。
          </div>
        ) : options.data?.items.length ? (
          <fieldset className="enrollmentTransferOptions" disabled={mutation.isPending}>
            <legend>目标班级</legend>
            {options.data.items.map((option) => {
              const unavailable = option.is_full && !option.capacity_override_allowed
              return (
                <label key={option.class_id}>
                  <input
                    type="radio"
                    name="target-class"
                    value={option.class_id}
                    checked={selectedClassId === option.class_id}
                    disabled={unavailable}
                    onChange={() => {
                      setSelectedClassId(option.class_id)
                      setCapacityReason("")
                    }}
                  />
                  <span>
                    <strong>{option.class_name}</strong>
                    <small>
                      名额 {option.active_enrollment_count} / {option.capacity}
                    </small>
                  </span>
                  {option.is_full ? (
                    <em>{unavailable ? "已满，无超额权限" : "已满，可申请超额"}</em>
                  ) : (
                    <em>可转入</em>
                  )}
                </label>
              )
            })}
          </fieldset>
        ) : (
          <div className="enrollmentDialogState">当前没有可转入的班级。</div>
        )}
        <label>
          <span>转班原因</span>
          <textarea
            value={reason}
            maxLength={500}
            disabled={mutation.isPending}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        {needsCapacityReason ? (
          <label>
            <span>超额转班原因</span>
            <textarea
              value={capacityReason}
              maxLength={500}
              disabled={mutation.isPending}
              onChange={(event) => setCapacityReason(event.target.value)}
            />
          </label>
        ) : null}
            {mutation.isError ? (
          <div className="admissionsInlineError" role="alert">
            {problemMessage(mutation.error, "转班失败，请稍后重试。")}
          </div>
        ) : null}
        <footer>
          <button
            type="button"
            className="teachingSecondaryButton"
            disabled={mutation.isPending}
            onClick={onClose}
          >
            暂不转班
          </button>
          <button
            type="submit"
            className="teachingPrimaryButton"
            disabled={submitDisabled}
          >
            <ArrowRightLeft aria-hidden="true" />
            {mutation.isPending ? "正在转班…" : "确认转班"}
          </button>
        </footer>
      </form>
    </EnrollmentOperationDialog>
  )
}

function CancelDialog({
  enrollment,
  onClose,
  onComplete,
}: {
  enrollment: Enrollment
  onClose: () => void
  onComplete: () => void
}) {
  const queryClient = useQueryClient()
  const [reason, setReason] = useState("")
  const submitGate = useRef(false)
  const mutation = useMutation({
    mutationFn: () => cancelEnrollment(enrollment.id, reason.trim()),
    onSuccess: async (updated) => {
      await invalidateEnrollmentData(
        queryClient,
        enrollment.id,
        updated.cohort_id,
      )
      onComplete()
    },
  })

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!reason.trim() || mutation.isPending || submitGate.current) return
    submitGate.current = true
    mutation.mutate(undefined, {
      onSettled: () => (submitGate.current = false),
    })
  }

  return (
    <EnrollmentOperationDialog
      title="取消报名"
      pending={mutation.isPending}
      onClose={onClose}
    >
      <form className="admissionsDialogForm" onSubmit={submit}>
        <div className="enrollmentOperationWarning">
          <AlertTriangle aria-hidden="true" />
          <p>取消报名不会自动退款，付款记录将继续保留。</p>
        </div>
        <label>
          <span>取消原因</span>
          <textarea
            value={reason}
            maxLength={500}
            disabled={mutation.isPending}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        {mutation.isError ? (
          <div className="admissionsInlineError" role="alert">
            {problemMessage(mutation.error, "取消报名失败，请稍后重试。")}
          </div>
        ) : null}
        <footer>
          <button
            type="button"
            className="teachingSecondaryButton"
            disabled={mutation.isPending}
            onClick={onClose}
          >
            保留报名
          </button>
          <button
            type="submit"
            className="teachingDangerButton"
            disabled={mutation.isPending || !reason.trim()}
          >
            <Ban aria-hidden="true" />
            {mutation.isPending ? "正在取消…" : "确认取消报名"}
          </button>
        </footer>
      </form>
    </EnrollmentOperationDialog>
  )
}

function WithdrawalPreviewPanel({ preview }: { preview: WithdrawalPreview }) {
  return (
    <section className="withdrawalPreview" aria-label="退款计算预览">
      <h3>退款计算预览</h3>
      <dl className="withdrawalPreviewSummary">
        <div><dt>营期天数</dt><dd>共 {preview.total_days} 天</dd></div>
        <div><dt>剩余天数</dt><dd>剩余 {preview.remaining_days} 天</dd></div>
        <div><dt>约定学费</dt><dd>{formatCny(preview.agreed_tuition_cents)}</dd></div>
        <div><dt>当前可退款</dt><dd>{formatCny(preview.available_paid_cents)}</dd></div>
        <div className="withdrawalRefundTotal"><dt>本次退款</dt><dd>{formatCny(preview.refund_cents)}</dd></div>
      </dl>
      <div className="withdrawalAllocations">
        <h4>原付款退款拆分</h4>
        {preview.allocations.map((allocation) => (
          <div key={allocation.payment_id}>
            <code>{allocation.payment_id}</code>
            <strong>{formatCny(allocation.amount_cents)}</strong>
          </div>
        ))}
      </div>
    </section>
  )
}

function WithdrawalDialog({
  enrollment,
  onClose,
  onComplete,
}: {
  enrollment: Enrollment
  onClose: () => void
  onComplete: () => void
}) {
  const queryClient = useQueryClient()
  const [withdrawalDate, setWithdrawalDate] = useState(todayDateInput)
  const [policy, setPolicy] = useState<RefundPolicy>("prorated_remaining_days")
  const [preview, setPreview] = useState<WithdrawalPreview | null>(null)
  const [reason, setReason] = useState("")
  const [staleMessage, setStaleMessage] = useState(false)
  const previewGate = useRef(false)
  const confirmGate = useRef(false)

  const previewMutation = useMutation({
    mutationFn: () =>
      previewWithdrawal(enrollment.id, {
        withdrawal_date: withdrawalDate,
        policy,
      }),
    onSuccess: (result) => {
      setPreview(result)
      setReason("")
      setStaleMessage(false)
    },
  })
  const confirmMutation = useMutation({
    mutationFn: (snapshot: WithdrawalPreview) =>
      confirmWithdrawal(enrollment.id, {
        ...snapshot,
        reason: reason.trim(),
      }),
    onSuccess: async (updated) => {
      await invalidateEnrollmentData(
        queryClient,
        enrollment.id,
        updated.cohort_id,
      )
      onComplete()
    },
    onError: async (error) => {
      if (
        error instanceof ProblemDetailsError &&
        error.problem.code === "withdrawal_refund_changed"
      ) {
        setPreview(null)
        setReason("")
        setStaleMessage(true)
        await invalidateEnrollmentData(
          queryClient,
          enrollment.id,
          enrollment.cohort_id,
        )
      }
    },
  })
  const pending = previewMutation.isPending || confirmMutation.isPending

  function clearPreview(): void {
    setPreview(null)
    setReason("")
    setStaleMessage(false)
    previewMutation.reset()
    confirmMutation.reset()
  }

  function calculate(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!withdrawalDate || pending || previewGate.current) return
    previewGate.current = true
    previewMutation.mutate(undefined, {
      onSettled: () => (previewGate.current = false),
    })
  }

  function confirm(): void {
    if (!preview || !reason.trim() || pending || confirmGate.current) return
    confirmGate.current = true
    confirmMutation.mutate(preview, {
      onSettled: () => (confirmGate.current = false),
    })
  }

  return (
    <EnrollmentOperationDialog
      title="退营并退款"
      pending={pending}
      onClose={onClose}
    >
      <OrderNumber orderNumber={enrollment.order_number} />
      <form className="admissionsDialogForm" onSubmit={calculate}>
        <label>
          <span>退营日期</span>
          <input
            type="date"
            value={withdrawalDate}
            disabled={pending}
            onChange={(event) => {
              setWithdrawalDate(event.target.value)
              clearPreview()
            }}
          />
        </label>
        <fieldset className="admissionsSegmentedField" disabled={pending}>
          <legend>退款方式</legend>
          <label>
            <input
              type="radio"
              name="refund-policy"
              checked={policy === "prorated_remaining_days"}
              onChange={() => {
                setPolicy("prorated_remaining_days")
                clearPreview()
              }}
            />
            按剩余日期退款
          </label>
          <label>
            <input
              type="radio"
              name="refund-policy"
              checked={policy === "full_remaining_paid"}
              onChange={() => {
                setPolicy("full_remaining_paid")
                clearPreview()
              }}
            />
            退还剩余已付款
          </label>
        </fieldset>
        {staleMessage ? (
          <div className="admissionsInlineError" role="alert">
            付款或退款状态已变化，请重新计算退款。
          </div>
        ) : null}
        {previewMutation.isError ? (
          <div className="admissionsInlineError" role="alert">
            {problemMessage(previewMutation.error, "退款暂时无法计算，请稍后重试。")}
          </div>
        ) : null}
        {!preview ? (
          <footer>
            <button
              type="button"
              className="teachingSecondaryButton"
              disabled={pending}
              onClick={onClose}
            >
              暂不退营
            </button>
            <button
              type="submit"
              className="teachingPrimaryButton"
              disabled={!withdrawalDate || pending}
            >
              <CheckCircle2 aria-hidden="true" />
              {previewMutation.isPending ? "正在计算…" : "计算退款"}
            </button>
          </footer>
        ) : null}
      </form>
      {preview ? (
        <div className="withdrawalConfirmation">
          <WithdrawalPreviewPanel preview={preview} />
          <label>
            <span>退营原因</span>
            <textarea
              value={reason}
              maxLength={500}
              disabled={pending}
              onChange={(event) => setReason(event.target.value)}
            />
          </label>
          {confirmMutation.isError && !staleMessage ? (
            <div className="admissionsInlineError" role="alert">
              {problemMessage(confirmMutation.error, "退营退款失败，请稍后重试。")}
            </div>
          ) : null}
          <footer>
            <button
              type="button"
              className="teachingSecondaryButton"
              disabled={pending}
              onClick={clearPreview}
            >
              重新计算
            </button>
            <button
              type="button"
              className="teachingDangerButton"
              disabled={pending || !reason.trim()}
              onClick={confirm}
            >
              <CalendarX2 aria-hidden="true" />
              {confirmMutation.isPending
                ? "正在确认退营…"
                : "二次确认退营并退款"}
            </button>
          </footer>
        </div>
      ) : null}
    </EnrollmentOperationDialog>
  )
}

function EnrollmentAmounts({ enrollment }: { enrollment: Enrollment }) {
  if (
    enrollment.agreed_tuition_cents === null ||
    enrollment.paid_cents === null ||
    enrollment.refunded_cents === null ||
    enrollment.net_paid_cents === null ||
    enrollment.due_cents === null ||
    enrollment.overpaid_cents === null
  ) {
    return <div className="enrollmentAmountUnavailable">金额不可见</div>
  }

  return (
    <dl className="enrollmentFinancialGrid">
      <div><dt>约定学费</dt><dd>{formatCny(enrollment.agreed_tuition_cents)}</dd></div>
      <div><dt>累计付款</dt><dd>{formatCny(enrollment.paid_cents)}</dd></div>
      <div><dt>累计退款</dt><dd>{formatCny(enrollment.refunded_cents)}</dd></div>
      <div><dt>净付款</dt><dd>{formatCny(enrollment.net_paid_cents)}</dd></div>
      <div className={enrollment.due_cents > 0 ? "financialAttention" : undefined}>
        <dt>结算状态</dt>
        <dd>
          {enrollment.due_cents > 0
            ? `欠款 ${formatCny(enrollment.due_cents)}`
            : enrollment.overpaid_cents > 0
              ? `多付 ${formatCny(enrollment.overpaid_cents)}`
              : "已结清"}
        </dd>
      </div>
    </dl>
  )
}

export function EnrollmentDetailPage({
  enrollmentId,
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  enrollmentId: string
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const [operation, setOperation] = useState<EnrollmentOperation>(null)
  const enrollment = useQuery({
    queryKey: ["enrollment", enrollmentId],
    queryFn: () => getEnrollment(enrollmentId),
  })
  const actionContext = useQuery({
    queryKey: ["enrollment-action-context", enrollmentId],
    queryFn: () => getEnrollmentActionContext(enrollmentId),
  })

  return (
    <div className="paymentsPage enrollmentDetailPage">
      <TeachingSectionNav
        permissions={permissions}
        scopedPermissions={scopedPermissions}
      />
      <Link className="enrollmentDetailBack" to="/teaching/enrollments">
        <ArrowLeft aria-hidden="true" />
        返回正式报名
      </Link>

      {enrollment.isLoading ? (
        <div className="admissionsEmptyState">正在读取报名详情…</div>
      ) : enrollment.isError ? (
        <div className="admissionsEmptyState" role="alert">
          报名详情暂时无法读取。
        </div>
      ) : enrollment.data ? (
        <>
          <header className="enrollmentDetailHeader">
            <div>
              <p className="teachingEyebrow">报名详情</p>
              <h1>{enrollment.data.student_name}</h1>
              <p>
                {enrollment.data.cohort_name} · {enrollment.data.class_name}
              </p>
            </div>
            <span className={`admissionsStatus status-${enrollment.data.status}`}>
              {ENROLLMENT_STATUS_LABELS[enrollment.data.status]}
            </span>
          </header>

          {actionContext.data?.read_only_reason === "archived" ? (
            <div className="enrollmentReadOnlyNotice">
              <AlertTriangle aria-hidden="true" />
              营期已归档，报名记录仅可查看。
            </div>
          ) : null}
          {actionContext.isError ? (
            <div className="admissionsPageError" role="alert">
              操作权限暂时无法确认，本页已切换为只读。
            </div>
          ) : null}

          <section className="enrollmentDetailBand" aria-labelledby="enrollment-overview">
            <h2 id="enrollment-overview">报名信息</h2>
            <dl className="enrollmentDetailGrid">
              <div><dt>订单号</dt><dd><OrderNumber orderNumber={enrollment.data.order_number} showLabel={false} /></dd></div>
              <div><dt>营期</dt><dd>{enrollment.data.cohort_name}</dd></div>
              <div><dt>当前班级</dt><dd>{enrollment.data.class_name}</dd></div>
              <div>
                <dt>参加日期模式</dt>
                <dd>{enrollment.data.schedule_mode === "custom" ? "单独日期" : "跟随营期"}</dd>
              </div>
              <div>
                <dt>实际参加区间</dt>
                <dd>
                  {enrollment.data.effective_starts_at && enrollment.data.effective_ends_at
                    ? `${formatDateTime(enrollment.data.effective_starts_at)} 至 ${formatExclusiveEndDate(enrollment.data.effective_ends_at)}`
                    : "跟随营期日期"}
                </dd>
              </div>
              <div><dt>付款状态</dt><dd>{PAYMENT_STATUS_LABELS[enrollment.data.payment_status]}</dd></div>
              <div><dt>确认日期</dt><dd>{formatDateTime(enrollment.data.confirmed_at)}</dd></div>
            </dl>
          </section>

          <section className="enrollmentDetailBand" aria-labelledby="enrollment-finance">
            <h2 id="enrollment-finance">学费与付款</h2>
            <EnrollmentAmounts enrollment={enrollment.data} />
          </section>

          {actionContext.data?.manage_allowed ? (
            <section className="enrollmentActionBar" aria-labelledby="enrollment-actions">
              <div>
                <h2 id="enrollment-actions">报名操作</h2>
                <p>转班、取消报名和退营退款为相互独立的操作。</p>
              </div>
              <div>
                <button
                  type="button"
                  className="teachingSecondaryButton"
                  onClick={() => setOperation("transfer")}
                >
                  <ArrowRightLeft aria-hidden="true" />
                  转班
                </button>
                <button
                  type="button"
                  className="teachingSecondaryButton"
                  onClick={() => setOperation("schedule")}
                >
                  <CalendarClock aria-hidden="true" />
                  调整参加日期
                </button>
                <button
                  type="button"
                  className="teachingSecondaryButton"
                  onClick={() => setOperation("cancel")}
                >
                  <Ban aria-hidden="true" />
                  取消报名
                </button>
                {actionContext.data.refund_allowed ? (
                  <button
                    type="button"
                    className="teachingDangerButton"
                    onClick={() => setOperation("withdrawal")}
                  >
                    <CalendarX2 aria-hidden="true" />
                    退营并退款
                  </button>
                ) : null}
              </div>
            </section>
          ) : null}

          {operation === "transfer" ? (
            <TransferDialog
              enrollment={enrollment.data}
              onClose={() => setOperation(null)}
              onComplete={() => setOperation(null)}
            />
          ) : null}
          {operation === "schedule" ? (
            <EnrollmentScheduleDialog
              enrollment={enrollment.data}
              onClose={() => setOperation(null)}
              onComplete={() => setOperation(null)}
            />
          ) : null}
          {operation === "cancel" ? (
            <CancelDialog
              enrollment={enrollment.data}
              onClose={() => setOperation(null)}
              onComplete={() => setOperation(null)}
            />
          ) : null}
          {operation === "withdrawal" ? (
            <WithdrawalDialog
              enrollment={enrollment.data}
              onClose={() => setOperation(null)}
              onComplete={() => setOperation(null)}
            />
          ) : null}
        </>
      ) : null}
    </div>
  )
}
