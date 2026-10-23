import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Archive,
  Ban,
  CheckCircle2,
  RotateCcw,
  Search,
  UserCheck,
  X,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { createPortal } from "react-dom"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { TeachingSectionNav } from "../teaching/TeachingSectionNav"
import {
  closeAdmissionCase,
  confirmAdmissionEnrollment,
  listAdmissionCases,
  listAdmissionEnrollmentOptions,
  listAdmissionStudentCandidates,
  matchAdmissionStudent,
  reopenAdmissionCase,
} from "./api"
import type {
  AdmissionCase,
  AdmissionCaseStatus,
  AdmissionEnrollmentOption,
  ConfirmEnrollmentInput,
} from "./api"
import { formatCny } from "./money"
import "../teaching/teaching.css"
import "./admissions.css"

const PAGE_SIZE = 20

const CASE_STATUS_LABELS: Record<AdmissionCaseStatus, string> = {
  pending_match: "待匹配",
  ready: "待报名",
  enrolled: "已报名",
  closed: "已关闭",
}

const PAYMENT_STATUS_LABELS = {
  unpaid: "未付款",
  partial: "部分付款",
  paid: "已付清",
  overpaid: "有多付款",
  refunded: "已退款",
} as const

type AdmissionFlow =
  | { kind: "match"; admissionCase: AdmissionCase }
  | { kind: "enroll"; admissionCase: AdmissionCase }
  | null

function problemMessage(error: unknown, fallback: string): string {
  if (!(error instanceof ProblemDetailsError)) return fallback
  if (error.problem.code === "minor_guardian_required") {
    return "开营时监护人资料尚未就绪，请先完善监护关系"
  }
  if (error.problem.code === "class_capacity_reached") {
    return "班级名额刚刚发生变化，请刷新后重新确认"
  }
  if (error.problem.code === "capacity_override_reason_required") {
    return "班级名额刚刚发生变化，请填写超额报名原因后重新确认"
  }
  return error.problem.detail
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}

function centsToDecimal(cents: number): string {
  const value = BigInt(cents)
  return `${value / 100n}.${String(value % 100n).padStart(2, "0")}`
}

function parseNonNegativeCents(value: string): number | null {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim())
  if (!match) return null
  const whole = BigInt(match[1] ?? "0")
  const fraction = BigInt((match[2] ?? "").padEnd(2, "0") || "0")
  const cents = whole * 100n + fraction
  if (cents > BigInt(Number.MAX_SAFE_INTEGER)) return null
  return Number(cents)
}

function AdmissionDialog({
  title,
  closeLabel,
  flow = false,
  pending,
  onClose,
  children,
}: {
  title: string
  closeLabel: string
  flow?: boolean
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const titleId = `admission-case-dialog-${title}`
  const backdropRef = useRef<HTMLDivElement>(null)
  const dialogRef = useRef<HTMLElement>(null)
  const onCloseRef = useRef(onClose)
  const pendingRef = useRef(pending)
  onCloseRef.current = onClose
  pendingRef.current = pending

  useEffect(() => {
    const previousFocus =
      document.activeElement instanceof HTMLElement ? document.activeElement : null
    const backdrop = backdropRef.current
    const dialog = dialogRef.current
    if (!backdrop || !dialog) return

    const backgroundStates = Array.from(document.body.children)
      .filter((element): element is HTMLElement =>
        element instanceof HTMLElement && element !== backdrop,
      )
      .map((element) => ({
        element,
        hadInert: element.hasAttribute("inert"),
      }))
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

    const handleDialogKeys = (event: KeyboardEvent): void => {
      if (event.key === "Escape" && !pendingRef.current) {
        event.preventDefault()
        onCloseRef.current()
        return
      }
      if (event.key !== "Tab") return
      const focusable = focusableElements()
      if (focusable.length === 0) {
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
    document.addEventListener("keydown", handleDialogKeys)
    return () => {
      document.removeEventListener("keydown", handleDialogKeys)
      for (const { element, hadInert } of backgroundStates) {
        if (!hadInert) element.removeAttribute("inert")
      }
      previousFocus?.focus()
    }
  }, [])

  return createPortal(
    <div
      ref={backdropRef}
      className={`admissionsDialogBackdrop${flow ? " admissionFlowBackdrop" : ""}`}
      onMouseDown={pending ? undefined : onClose}
    >
      <section
        ref={dialogRef}
        className={`admissionsDialog${flow ? " admissionFlowDialog" : ""}`}
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
            aria-label={closeLabel}
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

function ListPagination({
  page,
  pageSize,
  total,
  label,
  onPageChange,
}: {
  page: number
  pageSize: number
  total: number
  label: string
  onPageChange: (page: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  return (
    <nav className="teachingPagination" aria-label={label}>
      <span>第 {page} / {pageCount} 页，共 {total} 条</span>
      <div>
        <button
          type="button"
          className="teachingSecondaryButton"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </button>
        <button
          type="button"
          className="teachingSecondaryButton"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </nav>
  )
}

function MatchStudentDialog({
  admissionCase,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  admissionCase: AdmissionCase
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (studentId: string) => void
}) {
  const [queryInput, setQueryInput] = useState("")
  const [appliedQuery, setAppliedQuery] = useState("")
  const [page, setPage] = useState(1)
  const [selectedStudentId, setSelectedStudentId] = useState<string | null>(null)
  const candidates = useQuery({
    queryKey: ["admission-student-candidates", admissionCase.id, appliedQuery, page],
    queryFn: () =>
      listAdmissionStudentCandidates(admissionCase.id, {
        query: appliedQuery,
        page,
        pageSize: PAGE_SIZE,
      }),
    enabled: Boolean(appliedQuery),
  })

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const normalized = queryInput.trim()
    if (!normalized) return
    setAppliedQuery(normalized)
    setPage(1)
    setSelectedStudentId(null)
  }

  return (
    <AdmissionDialog
      title="匹配学员"
      closeLabel="关闭学员匹配"
      flow
      pending={pending}
      onClose={onClose}
    >
      <div className="admissionFlowStep">步骤 1 / 2</div>
      <form className="candidateSearch" onSubmit={search}>
        <label>
          <span>手机号或学员编号</span>
          <input
            value={queryInput}
            onChange={(event) => setQueryInput(event.target.value)}
            autoComplete="off"
          />
        </label>
        <button
          type="submit"
          className="teachingSecondaryButton"
          title="查询候选学员"
          disabled={!queryInput.trim() || pending}
        >
          <Search aria-hidden="true" />查询学员
        </button>
      </form>

      {candidates.isLoading ? (
        <div className="admissionsEmptyState compact">正在查找学员…</div>
      ) : candidates.isError ? (
        <div className="admissionsInlineError" role="alert">候选学员暂时无法读取</div>
      ) : appliedQuery && candidates.data?.items.length === 0 ? (
        <div className="admissionsEmptyState compact">没有找到对应学员。</div>
      ) : candidates.data?.items.length ? (
        <>
          <fieldset className="candidateList" disabled={pending}>
            <legend>选择一位学员</legend>
            {candidates.data.items.map((item) => (
              <label key={item.id}>
                <input
                  type="radio"
                  name="admission-student"
                  aria-label={item.full_name}
                  checked={selectedStudentId === item.id}
                  onChange={() => setSelectedStudentId(item.id)}
                />
                <span>
                  <strong>{item.full_name}</strong>
                  <small>
                    {item.student_number}
                    {item.masked_phone ? ` · ${item.masked_phone}` : ""}
                  </small>
                </span>
                <em>{item.student_status === "active" ? "已激活" : item.student_status === "draft" ? "待激活" : "已停用"}</em>
              </label>
            ))}
          </fieldset>
          <ListPagination
            page={candidates.data.page}
            pageSize={candidates.data.page_size}
            total={candidates.data.total}
            label="候选学员分页"
            onPageChange={(nextPage) => {
              setPage(nextPage)
              setSelectedStudentId(null)
            }}
          />
        </>
      ) : null}

      {error ? <div className="admissionsInlineError" role="alert">{error}</div> : null}
      <footer className="admissionDialogActions">
        <button
          type="button"
          className="teachingSecondaryButton"
          disabled={pending}
          onClick={onClose}
        >
          取消
        </button>
        <button
          type="button"
          className="teachingPrimaryButton"
          disabled={pending || selectedStudentId === null}
          onClick={() => {
            if (selectedStudentId) onSubmit(selectedStudentId)
          }}
        >
          <UserCheck aria-hidden="true" />
          {pending ? "正在匹配" : "确认匹配"}
        </button>
      </footer>
    </AdmissionDialog>
  )
}

function EnrollmentDialog({
  admissionCase,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  admissionCase: AdmissionCase
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: ConfirmEnrollmentInput) => void
}) {
  const [page, setPage] = useState(1)
  const [selectedClassId, setSelectedClassId] = useState<string | null>(null)
  const [adjustTuition, setAdjustTuition] = useState(false)
  const [tuition, setTuition] = useState("")
  const [tuitionReason, setTuitionReason] = useState("")
  const [capacityReason, setCapacityReason] = useState("")
  const options = useQuery({
    queryKey: ["admission-enrollment-options", admissionCase.id, page],
    queryFn: () =>
      listAdmissionEnrollmentOptions(admissionCase.id, {
        page,
        pageSize: PAGE_SIZE,
      }),
  })
  const selectedOption =
    options.data?.items.find((item) => item.class_id === selectedClassId) ?? null
  const adjustedCents = adjustTuition ? parseNonNegativeCents(tuition) : null
  const tuitionChanged =
    adjustTuition &&
    adjustedCents !== null &&
    selectedOption !== null &&
    adjustedCents !== selectedOption.default_tuition_cents
  const blockedByReadiness =
    selectedOption?.readiness_at_start === "minor_guardian_required"
  const blockedByCapacity =
    selectedOption?.is_full === true && !selectedOption.capacity_override_allowed
  const missingCapacityReason =
    selectedOption?.is_full === true &&
    selectedOption.capacity_override_allowed &&
    !capacityReason.trim()
  const tuitionAmountError =
    adjustTuition && adjustedCents === null
      ? "请输入最多两位小数且不超出系统范围的学费"
      : null
  const tuitionReasonError =
    tuitionChanged && !tuitionReason.trim() ? "调整后的学费需要填写原因" : null
  const invalidTuition = tuitionAmountError !== null || tuitionReasonError !== null
  const submitDisabled =
    pending ||
    selectedOption === null ||
    blockedByReadiness ||
    blockedByCapacity ||
    missingCapacityReason ||
    invalidTuition

  function selectOption(item: AdmissionEnrollmentOption): void {
    setSelectedClassId(item.class_id)
    setAdjustTuition(false)
    setTuition(centsToDecimal(item.default_tuition_cents))
    setTuitionReason("")
    setCapacityReason("")
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (submitDisabled || selectedOption === null) return
    onSubmit({
      class_id: selectedOption.class_id,
      agreed_tuition_cents: tuitionChanged ? adjustedCents : null,
      tuition_override_reason: tuitionChanged ? tuitionReason.trim() : null,
      capacity_override_reason: selectedOption.is_full
        ? capacityReason.trim()
        : null,
    })
  }

  return (
    <AdmissionDialog
      title="确认报名"
      closeLabel="关闭报名确认"
      flow
      pending={pending}
      onClose={onClose}
    >
      <div className="admissionFlowStep">步骤 2 / 2</div>
      <div className="admissionStudentSummary">
        <span>报名学员</span>
        <strong>{admissionCase.student_name ?? "已匹配学员"}</strong>
      </div>

      {options.isLoading ? (
        <div className="admissionsEmptyState compact">正在读取可报名班级…</div>
      ) : options.isError ? (
        <div className="admissionsInlineError" role="alert">可报名班级暂时无法读取</div>
      ) : options.data?.items.length ? (
        <>
          <fieldset className="enrollmentOptionList" disabled={pending}>
            <legend>选择营期和班级</legend>
            {options.data.items.map((item) => (
              <label key={item.class_id}>
                <input
                  type="radio"
                  name="enrollment-option"
                  aria-label={`${item.cohort_name} · ${item.class_name}`}
                  checked={selectedClassId === item.class_id}
                  onChange={() => selectOption(item)}
                />
                <span>
                  <strong>{item.cohort_name} · {item.class_name}</strong>
                  <small>
                    名额 {item.active_enrollment_count} / {item.capacity} · 默认学费 {formatCny(item.default_tuition_cents)}
                  </small>
                </span>
                {item.is_full ? <em>已满</em> : <em>可报名</em>}
              </label>
            ))}
          </fieldset>
          <ListPagination
            page={options.data.page}
            pageSize={options.data.page_size}
            total={options.data.total}
            label="报名班级分页"
            onPageChange={(nextPage) => {
              setPage(nextPage)
              setSelectedClassId(null)
            }}
          />
        </>
      ) : (
        <div className="admissionsEmptyState compact">当前没有可报名班级。</div>
      )}

      {selectedOption ? (
        <form className="enrollmentConfirmForm" onSubmit={submit}>
          <dl className="enrollmentOptionSummary">
            <div><dt>默认学费</dt><dd>{formatCny(selectedOption.default_tuition_cents)}</dd></div>
            <div><dt>班级名额</dt><dd>{selectedOption.active_enrollment_count} / {selectedOption.capacity}</dd></div>
          </dl>

          {blockedByReadiness ? (
            <div className="admissionsInlineError" role="alert">
              开营时监护人资料尚未就绪
              {admissionCase.student_person_id ? (
                <Link to={`/people/${admissionCase.student_person_id}`}>查看学员档案</Link>
              ) : null}
            </div>
          ) : null}
          {blockedByCapacity ? (
            <div className="admissionsInlineError" role="alert">
              班级已满，当前账号无超额报名权限
            </div>
          ) : null}
          {selectedOption.is_full && selectedOption.capacity_override_allowed ? (
            <label>
              <span>超额报名原因</span>
              <textarea
                value={capacityReason}
                onChange={(event) => setCapacityReason(event.target.value)}
              />
            </label>
          ) : null}

          <label className="admissionCheckboxField">
            <input
              type="checkbox"
              checked={adjustTuition}
              onChange={(event) => {
                setAdjustTuition(event.target.checked)
                setTuition(centsToDecimal(selectedOption.default_tuition_cents))
                setTuitionReason("")
              }}
            />
            调整学费
          </label>
          {adjustTuition ? (
            <div className="tuitionOverrideFields">
              <label>
                <span>最终学费（元）</span>
                <input
                  inputMode="decimal"
                  value={tuition}
                  onChange={(event) => setTuition(event.target.value)}
                  aria-invalid={tuitionAmountError !== null}
                  aria-describedby={
                    tuitionAmountError ? "tuition-amount-error" : undefined
                  }
                />
              </label>
              <label>
                <span>学费调整原因</span>
                <textarea
                  value={tuitionReason}
                  onChange={(event) => setTuitionReason(event.target.value)}
                  disabled={!tuitionChanged}
                  aria-invalid={tuitionReasonError !== null}
                  aria-describedby={
                    tuitionReasonError ? "tuition-reason-error" : undefined
                  }
                />
              </label>
            </div>
          ) : null}

          {tuitionAmountError ? (
            <div
              id="tuition-amount-error"
              className="admissionsInlineError"
              role="alert"
              aria-label={tuitionAmountError}
            >
              {tuitionAmountError}
            </div>
          ) : null}
          {tuitionReasonError ? (
            <div
              id="tuition-reason-error"
              className="admissionsInlineError"
              role="alert"
              aria-label={tuitionReasonError}
            >
              {tuitionReasonError}
            </div>
          ) : null}
          {error ? <div className="admissionsInlineError" role="alert">{error}</div> : null}
          <footer className="admissionDialogActions">
            <button
              type="button"
              className="teachingSecondaryButton"
              disabled={pending}
              onClick={onClose}
            >
              取消
            </button>
            <button
              type="submit"
              className="teachingPrimaryButton"
              disabled={submitDisabled}
            >
              <CheckCircle2 aria-hidden="true" />
              {pending ? "正在确认" : "最终确认报名"}
            </button>
          </footer>
        </form>
      ) : error ? (
        <div className="admissionsInlineError" role="alert">{error}</div>
      ) : null}
    </AdmissionDialog>
  )
}

function CloseCaseDialog({
  pending,
  error,
  onClose,
  onSubmit,
}: {
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (reason: string) => void
}) {
  const [reason, setReason] = useState("")
  return (
    <AdmissionDialog
      title="关闭待分配单"
      closeLabel="关闭待分配单表单"
      pending={pending}
      onClose={onClose}
    >
      <form
        className="admissionsDialogForm"
        onSubmit={(event) => {
          event.preventDefault()
          if (reason.trim()) onSubmit(reason.trim())
        }}
      >
        <label>
          <span>关闭原因</span>
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        {error ? <div className="admissionsInlineError" role="alert">{error}</div> : null}
        <footer>
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending || !reason.trim()}>确认关闭</button>
        </footer>
      </form>
    </AdmissionDialog>
  )
}

export function AdmissionCasesPage({
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const queryClient = useQueryClient()
  const [status, setStatus] = useState<AdmissionCaseStatus>("pending_match")
  const [page, setPage] = useState(1)
  const [flow, setFlow] = useState<AdmissionFlow>(null)
  const [closingCase, setClosingCase] = useState<AdmissionCase | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const mutationGate = useRef(false)
  const availablePermissions = new Set([...permissions, ...scopedPermissions])
  const canManageCases = availablePermissions.has("admission_cases.manage")
  const canReadEnrollments = availablePermissions.has("enrollments.read")
  const canManageEnrollments = availablePermissions.has("enrollments.manage")
  const cases = useQuery({
    queryKey: ["admission-cases", { status, page, pageSize: PAGE_SIZE }],
    queryFn: () => listAdmissionCases({ status, page, pageSize: PAGE_SIZE }),
  })

  const matchMutation = useMutation({
    mutationFn: ({ caseId, studentId }: { caseId: string; studentId: string }) =>
      matchAdmissionStudent(caseId, studentId),
    onSuccess: async (matched) => {
      await queryClient.invalidateQueries({ queryKey: ["admission-cases"] })
      setFlow({ kind: "enroll", admissionCase: matched })
    },
    onSettled: () => {
      mutationGate.current = false
    },
  })
  const confirmMutation = useMutation({
    mutationFn: ({ caseId, input }: { caseId: string; input: ConfirmEnrollmentInput }) =>
      confirmAdmissionEnrollment(caseId, input),
    onSuccess: async () => {
      setFlow(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admission-cases"] }),
        queryClient.invalidateQueries({ queryKey: ["enrollments"] }),
        queryClient.invalidateQueries({ queryKey: ["admission-enrollment-options"] }),
      ])
    },
    onError: async (error) => {
      if (
        error instanceof ProblemDetailsError &&
        [
          "class_capacity_reached",
          "capacity_override_reason_required",
          "minor_guardian_required",
        ].includes(error.problem.code)
      ) {
        await queryClient.invalidateQueries({
          queryKey: ["admission-enrollment-options"],
        })
      }
    },
    onSettled: () => {
      mutationGate.current = false
    },
  })
  const closeMutation = useMutation({
    mutationFn: ({ caseId, reason }: { caseId: string; reason: string }) =>
      closeAdmissionCase(caseId, reason),
    onSuccess: async () => {
      setClosingCase(null)
      await queryClient.invalidateQueries({ queryKey: ["admission-cases"] })
    },
    onSettled: () => {
      mutationGate.current = false
    },
  })
  const reopenMutation = useMutation({
    mutationFn: (caseId: string) => reopenAdmissionCase(caseId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["admission-cases"] })
    },
    onError: (error) => setPageError(problemMessage(error, "待分配单暂时无法重新打开")),
    onSettled: () => {
      mutationGate.current = false
    },
  })
  const operationLocked =
    matchMutation.isPending ||
    confirmMutation.isPending ||
    closeMutation.isPending ||
    reopenMutation.isPending

  function runMutation(mutate: () => void): void {
    if (mutationGate.current) return
    mutationGate.current = true
    mutate()
  }

  function actionFor(item: AdmissionCase): ReactNode {
    if (item.status === "enrolled") {
      return item.current_enrollment_id && canReadEnrollments ? (
        <Link className="admissionsRowButton" to={`/teaching/enrollments/${item.current_enrollment_id}`}>
          查看报名
        </Link>
      ) : (
        <span>—</span>
      )
    }
    if (
      !item.manage_allowed ||
      item.read_only_reason === "archived" ||
      !canManageCases
    ) {
      return <span>—</span>
    }
    if (item.status === "pending_match") {
      return (
        <button
          type="button"
          className="admissionsRowButton"
          title="为付款匹配学员"
          disabled={operationLocked}
          onClick={() => {
            matchMutation.reset()
            confirmMutation.reset()
            setFlow({ kind: "match", admissionCase: item })
          }}
        >
          <UserCheck aria-hidden="true" />匹配学员
        </button>
      )
    }
    if (item.status === "ready" && canManageEnrollments) {
      return (
        <button
          type="button"
          className="admissionsRowButton"
          title="选择班级并确认报名"
          disabled={operationLocked}
          onClick={() => {
            confirmMutation.reset()
            setFlow({ kind: "enroll", admissionCase: item })
          }}
        >
          <CheckCircle2 aria-hidden="true" />确认报名
        </button>
      )
    }
    if (item.status === "closed") {
      return (
        <button
          type="button"
          className="admissionsRowButton"
          title="重新打开待分配单"
          disabled={operationLocked}
          onClick={() => {
            if (window.confirm("确认重新打开这张待分配单？")) {
              setPageError(null)
              runMutation(() => reopenMutation.mutate(item.id))
            }
          }}
        >
          <RotateCcw aria-hidden="true" />重新打开
        </button>
      )
    }
    return <span>—</span>
  }

  return (
    <div className="paymentsPage admissionCasesPage">
      <TeachingSectionNav
        permissions={permissions}
        scopedPermissions={scopedPermissions}
      />
      <header className="teachingPageHeader admissionsPageHeader">
        <div>
          <p className="teachingEyebrow">招生教学</p>
          <h1>待分配</h1>
        </div>
      </header>

      <section className="admissionCaseFilters" aria-label="待分配筛选">
        <label>
          <span>待分配状态</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as AdmissionCaseStatus)
              setPage(1)
            }}
          >
            <option value="pending_match">待匹配</option>
            <option value="ready">待报名</option>
            <option value="enrolled">已报名</option>
            <option value="closed">已关闭</option>
          </select>
        </label>
      </section>
      {pageError ? <div className="admissionsPageError" role="alert">{pageError}</div> : null}

      {cases.isLoading ? (
        <div className="admissionsEmptyState">正在读取待分配名单…</div>
      ) : cases.isError ? (
        <div className="admissionsEmptyState" role="alert">待分配名单暂时无法读取。</div>
      ) : cases.data ? (
        <>
          {cases.data.items.length ? (
            <div className="admissionsDataTable admissionCasesTable" role="table" aria-label="待分配名单">
            <div className="admissionsTableHeader" role="row">
              <span role="columnheader">学员</span>
              <span role="columnheader">状态</span>
              <span role="columnheader">付款状态</span>
              <span role="columnheader">净付款</span>
              <span role="columnheader">更新时间</span>
              <span role="columnheader">操作</span>
            </div>
            {cases.data.items.map((item, index) => (
              <div className="admissionsTableRow" role="row" key={item.id}>
                <span role="cell" data-label="学员">
                  <strong>{item.student_name ?? "尚未匹配"}</strong>
                  {item.read_only_reason === "archived" ? (
                    <small className="readOnlyLabel"><Archive aria-hidden="true" />已归档，只读</small>
                  ) : null}
                </span>
                <span role="cell" data-label="状态">
                  <span className={`admissionsStatus status-${item.status}`}>
                    {CASE_STATUS_LABELS[item.status]}
                  </span>
                </span>
                <span role="cell" data-label="付款状态">
                  {PAYMENT_STATUS_LABELS[item.payment_status]}
                </span>
                <span role="cell" data-label="净付款" className="amountCell">
                  {item.net_paid_cents === null ? "金额不可见" : formatCny(item.net_paid_cents)}
                </span>
                <span role="cell" data-label="更新时间">{formatDateTime(item.updated_at)}</span>
                <span role="cell" data-label="操作" className="actionCell caseActions">
                  {actionFor(item)}
                  {item.manage_allowed &&
                  item.read_only_reason === null &&
                  canManageCases &&
                  ["pending_match", "ready"].includes(item.status) ? (
                    <button
                      type="button"
                      className="admissionsIconButton dangerText"
                      aria-label={`关闭第 ${index + 1} 条待分配单（${item.student_name ?? "尚未匹配"}）`}
                      title="关闭待分配单"
                      disabled={operationLocked}
                      onClick={() => {
                        closeMutation.reset()
                        setClosingCase(item)
                      }}
                    >
                      <Ban aria-hidden="true" />
                    </button>
                  ) : null}
                </span>
              </div>
            ))}
            </div>
          ) : (
            <div className="admissionsEmptyState">当前状态下没有待分配记录。</div>
          )}
          {cases.data.total > 0 ? (
            <ListPagination
              page={cases.data.page}
              pageSize={cases.data.page_size}
              total={cases.data.total}
              label="待分配名单分页"
              onPageChange={setPage}
            />
          ) : null}
        </>
      ) : (
        <div className="admissionsEmptyState">待分配名单暂时无法读取。</div>
      )}

      {flow?.kind === "match" ? (
        <MatchStudentDialog
          admissionCase={flow.admissionCase}
          pending={matchMutation.isPending}
          error={matchMutation.isError ? problemMessage(matchMutation.error, "学员暂时无法匹配") : null}
          onClose={() => {
            if (!matchMutation.isPending) setFlow(null)
          }}
          onSubmit={(studentId) =>
            runMutation(() =>
              matchMutation.mutate({
                caseId: flow.admissionCase.id,
                studentId,
              }),
            )
          }
        />
      ) : null}
      {flow?.kind === "enroll" ? (
        <EnrollmentDialog
          admissionCase={flow.admissionCase}
          pending={confirmMutation.isPending}
          error={confirmMutation.isError ? problemMessage(confirmMutation.error, "报名暂时无法确认") : null}
          onClose={() => {
            if (!confirmMutation.isPending) setFlow(null)
          }}
          onSubmit={(input) =>
            runMutation(() =>
              confirmMutation.mutate({ caseId: flow.admissionCase.id, input }),
            )
          }
        />
      ) : null}
      {closingCase ? (
        <CloseCaseDialog
          pending={closeMutation.isPending}
          error={closeMutation.isError ? problemMessage(closeMutation.error, "待分配单暂时无法关闭") : null}
          onClose={() => {
            if (!closeMutation.isPending) setClosingCase(null)
          }}
          onSubmit={(reason) =>
            runMutation(() =>
              closeMutation.mutate({ caseId: closingCase.id, reason }),
            )
          }
        />
      ) : null}
    </div>
  )
}
