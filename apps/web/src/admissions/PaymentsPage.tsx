import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Ban,
  Copy,
  Plus,
  RotateCw,
  SlidersHorizontal,
  Undo2,
  X,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { FormEvent, ReactNode } from "react"

import { ProblemDetailsError } from "../api/client"
import { TeachingSectionNav } from "../teaching/TeachingSectionNav"
import {
  createManualPayment,
  createRefund,
  createServiceAccount,
  createServiceCredential,
  disableServiceAccount,
  disableServiceCredential,
  listPaymentConflicts,
  listPayments,
  listServiceAccounts,
  resolvePaymentConflict,
} from "./api"
import type {
  CreatedServiceCredential,
  ManualPaymentInput,
  Payment,
  PaymentConflict,
  PaymentConflictStatus,
  PaymentFilters,
  RefundInput,
  ServiceAccount,
} from "./api"
import { formatCny } from "./money"
import "../teaching/teaching.css"
import "./admissions.css"

type PaymentsTab = "payments" | "conflicts" | "credentials"

const PAGE_SIZE = 20
const CONFLICT_TYPE_LABELS = {
  external_order_payload_mismatch: "订单内容冲突",
  idempotency_payload_mismatch: "幂等内容冲突",
  ambiguous_payer: "付款人不唯一",
} as const
const CONFLICT_STATUS_LABELS: Record<PaymentConflictStatus, string> = {
  open: "待处理",
  resolved: "已解决",
  dismissed: "已忽略",
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function formatDateTime(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value))
}

function currentLocalDateTime(): string {
  const date = new Date()
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
  return local.toISOString().slice(0, 16)
}

function localDateTimeToIso(value: string): string {
  return new Date(value).toISOString()
}

function parsePositiveCents(value: string): bigint | null {
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(value.trim())
  if (!match) return null
  const whole = BigInt(match[1] ?? "0")
  const fraction = (match[2] ?? "").padEnd(2, "0")
  const cents = whole * 100n + BigInt(fraction || "0")
  return cents > 0n ? cents : null
}

function OperationDialog({
  title,
  closeLabel,
  pending,
  onClose,
  children,
}: {
  title: string
  closeLabel: string
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const titleId = `admissions-dialog-${title}`
  useEffect(() => {
    if (pending) return
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [onClose, pending])

  return (
    <div className="admissionsDialogBackdrop" onMouseDown={pending ? undefined : onClose}>
      <section
        className="admissionsDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
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
    </div>
  )
}

function ManualPaymentDialog({
  pending,
  error,
  onClose,
  onSubmit,
}: {
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: ManualPaymentInput) => void
}) {
  const [targetKind, setTargetKind] = useState<"student" | "case">("student")
  const [targetId, setTargetId] = useState("")
  const [amount, setAmount] = useState("")
  const [paidAt, setPaidAt] = useState(currentLocalDateTime)
  const [note, setNote] = useState("")
  const [validationError, setValidationError] = useState<string | null>(null)

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!targetId.trim()) {
      setValidationError("请选择付款对应的学员或待分配单")
      return
    }
    if (parsePositiveCents(amount) === null) {
      setValidationError("请输入最多两位小数的正数金额")
      return
    }
    if (!paidAt || !note.trim()) {
      setValidationError("付款时间和备注均为必填项")
      return
    }
    setValidationError(null)
    onSubmit({
      student_person_id: targetKind === "student" ? targetId.trim() : null,
      admission_case_id: targetKind === "case" ? targetId.trim() : null,
      amount: amount.trim(),
      paid_at: localDateTimeToIso(paidAt),
      note: note.trim(),
    })
  }

  return (
    <OperationDialog title="登记付款" closeLabel="关闭付款登记" pending={pending} onClose={onClose}>
      <form className="admissionsDialogForm" onSubmit={submit}>
        <fieldset className="admissionsSegmentedField">
          <legend>付款归属</legend>
          <label>
            <input
              type="radio"
              name="payment-target"
              checked={targetKind === "student"}
              onChange={() => {
                setTargetKind("student")
                setTargetId("")
              }}
            />
            学员
          </label>
          <label>
            <input
              type="radio"
              name="payment-target"
              checked={targetKind === "case"}
              onChange={() => {
                setTargetKind("case")
                setTargetId("")
              }}
            />
            现有待分配单
          </label>
        </fieldset>
        <label>
          <span>{targetKind === "student" ? "学员 ID" : "待分配单 ID"}</span>
          <input value={targetId} onChange={(event) => setTargetId(event.target.value)} />
        </label>
        <label>
          <span>付款金额（元）</span>
          <input
            inputMode="decimal"
            placeholder="例如 36800.05"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
          />
        </label>
        <label>
          <span>付款时间</span>
          <input
            type="datetime-local"
            value={paidAt}
            onChange={(event) => setPaidAt(event.target.value)}
          />
        </label>
        <label>
          <span>备注</span>
          <textarea value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        {validationError || error ? (
          <div className="admissionsInlineError" role="alert">
            {validationError ?? error}
          </div>
        ) : null}
        <footer>
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending}>
            {pending ? "正在登记" : "确认登记"}
          </button>
        </footer>
      </form>
    </OperationDialog>
  )
}

function RefundDialog({
  payment,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  payment: Payment
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: RefundInput) => void
}) {
  const [amount, setAmount] = useState("")
  const [refundedAt, setRefundedAt] = useState(currentLocalDateTime)
  const [reason, setReason] = useState("")
  const [validationError, setValidationError] = useState<string | null>(null)

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const cents = parsePositiveCents(amount)
    const available = payment.available_refund_cents
    if (cents === null || available === null || cents > BigInt(available)) {
      setValidationError("退款金额必须为正数且不能超过剩余可退金额")
      return
    }
    if (!refundedAt || !reason.trim()) {
      setValidationError("退款时间和退款原因均为必填项")
      return
    }
    setValidationError(null)
    onSubmit({
      amount: amount.trim(),
      refunded_at: localDateTimeToIso(refundedAt),
      reason: reason.trim(),
    })
  }

  return (
    <OperationDialog title="登记退款" closeLabel="关闭退款登记" pending={pending} onClose={onClose}>
      <dl className="refundSummary">
        <div><dt>原付款</dt><dd>{formatCny(payment.amount_cents)}</dd></div>
        <div><dt>已退款</dt><dd>{formatCny(payment.refunded_cents)}</dd></div>
        <div><dt>剩余可退</dt><dd>{formatCny(payment.available_refund_cents)}</dd></div>
      </dl>
      <form className="admissionsDialogForm" onSubmit={submit}>
        <label>
          <span>退款金额（元）</span>
          <input inputMode="decimal" value={amount} onChange={(event) => setAmount(event.target.value)} />
        </label>
        <label>
          <span>退款时间</span>
          <input type="datetime-local" value={refundedAt} onChange={(event) => setRefundedAt(event.target.value)} />
        </label>
        <label>
          <span>退款原因</span>
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} />
        </label>
        {validationError || error ? <div className="admissionsInlineError" role="alert">{validationError ?? error}</div> : null}
        <footer>
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending}>
            {pending ? "正在退款" : "确认退款"}
          </button>
        </footer>
      </form>
    </OperationDialog>
  )
}

function ConflictDialog({
  conflict,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  conflict: PaymentConflict
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (action: "resolved" | "dismissed", note: string) => void
}) {
  const [action, setAction] = useState<"resolved" | "dismissed">("resolved")
  const [note, setNote] = useState("")
  return (
    <OperationDialog title="处理导入冲突" closeLabel="关闭冲突处理" pending={pending} onClose={onClose}>
      <form
        className="admissionsDialogForm"
        onSubmit={(event) => {
          event.preventDefault()
          if (note.trim()) onSubmit(action, note.trim())
        }}
      >
        <p className="admissionsDialogHint">原导入事件：{conflict.original_import_event_id}</p>
        <fieldset className="admissionsSegmentedField">
          <legend>处理结果</legend>
          <label><input type="radio" checked={action === "resolved"} onChange={() => setAction("resolved")} />已解决</label>
          <label><input type="radio" checked={action === "dismissed"} onChange={() => setAction("dismissed")} />忽略</label>
        </fieldset>
        <label>
          <span>处理说明</span>
          <textarea value={note} onChange={(event) => setNote(event.target.value)} />
        </label>
        {error ? <div className="admissionsInlineError" role="alert">{error}</div> : null}
        <footer>
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending || !note.trim()}>确认处理</button>
        </footer>
      </form>
    </OperationDialog>
  )
}

function CredentialDialog({
  account,
  onClose,
  onRefresh,
}: {
  account: ServiceAccount
  onClose: () => void
  onRefresh: () => Promise<void>
}) {
  const [name, setName] = useState("")
  const [created, setCreated] = useState<CreatedServiceCredential | null>(null)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  function close(): void {
    setCreated(null)
    setName("")
    setCopied(false)
    setError(null)
    onClose()
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!name.trim()) return
    setPending(true)
    setError(null)
    try {
      const secret = await createServiceCredential(account.id, { name: name.trim() })
      setCreated(secret)
      await onRefresh()
    } catch (caught) {
      setError(errorMessage(caught, "凭证暂时无法创建"))
    } finally {
      setPending(false)
    }
  }

  return (
    <OperationDialog
      title={created ? "一次性密钥" : "新建凭证"}
      closeLabel={created ? "关闭密钥窗口" : "关闭凭证创建"}
      pending={pending}
      onClose={close}
    >
      {created ? (
        <div className="credentialSecretPanel">
          <p className="credentialWarning">关闭后无法再次查看此密钥</p>
          <code>{created.secret}</code>
          <button
            type="button"
            className="teachingPrimaryButton"
            title="复制一次性密钥"
            onClick={() => {
              void navigator.clipboard?.writeText(created.secret)
              setCopied(true)
            }}
          >
            <Copy aria-hidden="true" />{copied ? "已复制" : "复制密钥"}
          </button>
        </div>
      ) : (
        <form className="admissionsDialogForm" onSubmit={(event) => void submit(event)}>
          <label>
            <span>凭证名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          {error ? <div className="admissionsInlineError" role="alert">{error}</div> : null}
          <footer>
            <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
            <button type="submit" className="teachingPrimaryButton" disabled={pending || !name.trim()}>
              {pending ? "正在创建" : "确认创建"}
            </button>
          </footer>
        </form>
      )}
    </OperationDialog>
  )
}

function ServiceAccountDialog({
  pending,
  error,
  onClose,
  onSubmit,
}: {
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: { name: string; source_system: string }) => void
}) {
  const [name, setName] = useState("")
  const [sourceSystem, setSourceSystem] = useState("")
  return (
    <OperationDialog title="新建接口账号" closeLabel="关闭接口账号创建" pending={pending} onClose={onClose}>
      <form
        className="admissionsDialogForm"
        onSubmit={(event) => {
          event.preventDefault()
          if (name.trim() && sourceSystem.trim()) {
            onSubmit({ name: name.trim(), source_system: sourceSystem.trim() })
          }
        }}
      >
        <label><span>账号名称</span><input value={name} onChange={(event) => setName(event.target.value)} /></label>
        <label><span>来源系统标识</span><input value={sourceSystem} onChange={(event) => setSourceSystem(event.target.value)} /></label>
        {error ? <div className="admissionsInlineError" role="alert">{error}</div> : null}
        <footer>
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending || !name.trim() || !sourceSystem.trim()}>确认创建</button>
        </footer>
      </form>
    </OperationDialog>
  )
}

function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <nav className="teachingPagination" aria-label="列表分页">
      <span>第 {page} / {pages} 页，共 {total} 条</span>
      <div>
        <button type="button" className="teachingSecondaryButton" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>上一页</button>
        <button type="button" className="teachingSecondaryButton" disabled={page >= pages} onClick={() => onPageChange(page + 1)}>下一页</button>
      </div>
    </nav>
  )
}

export function PaymentsPage({
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<PaymentsTab>("payments")
  const [sourceKind, setSourceKind] = useState<"" | "api" | "manual">("")
  const [sourceSystem, setSourceSystem] = useState("")
  const [payerPhone, setPayerPhone] = useState("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [paymentFilters, setPaymentFilters] = useState<PaymentFilters>({ page: 1, pageSize: PAGE_SIZE })
  const [conflictStatus, setConflictStatus] = useState<PaymentConflictStatus | "open">("open")
  const [conflictPage, setConflictPage] = useState(1)
  const [servicePage, setServicePage] = useState(1)
  const [manualOpen, setManualOpen] = useState(false)
  const [refundPayment, setRefundPayment] = useState<Payment | null>(null)
  const [selectedConflict, setSelectedConflict] = useState<PaymentConflict | null>(null)
  const [credentialAccount, setCredentialAccount] = useState<ServiceAccount | null>(null)
  const [serviceAccountOpen, setServiceAccountOpen] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)

  const canManagePayments = permissions.has("payments.manage")
  const canRefund = permissions.has("refunds.manage") && permissions.has("payments.read")
  const canManageImports = permissions.has("payment_imports.manage")

  const payments = useQuery({
    queryKey: ["payments", paymentFilters],
    queryFn: () => listPayments(paymentFilters),
    enabled: tab === "payments",
  })
  const conflicts = useQuery({
    queryKey: ["payment-conflicts", conflictStatus, conflictPage],
    queryFn: () => listPaymentConflicts({ status: conflictStatus, page: conflictPage, pageSize: PAGE_SIZE }),
    enabled: tab === "conflicts" && canManageImports,
  })
  const serviceAccounts = useQuery({
    queryKey: ["service-accounts", servicePage],
    queryFn: () => listServiceAccounts({ page: servicePage, pageSize: PAGE_SIZE }),
    enabled: tab === "credentials" && canManageImports,
  })

  const manualMutation = useMutation({
    mutationFn: (input: ManualPaymentInput) => createManualPayment(input),
    onSuccess: async () => {
      setManualOpen(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["payments"] }),
        queryClient.invalidateQueries({ queryKey: ["admission-cases"] }),
      ])
    },
  })
  const refundMutation = useMutation({
    mutationFn: ({ paymentId, input }: { paymentId: string; input: RefundInput }) => createRefund(paymentId, input),
    onSuccess: async () => {
      setRefundPayment(null)
      await queryClient.invalidateQueries({ queryKey: ["payments"] })
    },
  })
  const conflictMutation = useMutation({
    mutationFn: ({ id, action, note }: { id: string; action: "resolved" | "dismissed"; note: string }) => resolvePaymentConflict(id, { action, note }),
    onSuccess: async () => {
      setSelectedConflict(null)
      await queryClient.invalidateQueries({ queryKey: ["payment-conflicts"] })
    },
  })
  const accountCreateMutation = useMutation({
    mutationFn: createServiceAccount,
    onSuccess: async () => {
      setServiceAccountOpen(false)
      await queryClient.invalidateQueries({ queryKey: ["service-accounts"] })
    },
  })
  const accountDisableMutation = useMutation({
    mutationFn: disableServiceAccount,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["service-accounts"] }),
    onError: (error) => setPageError(errorMessage(error, "接口账号暂时无法停用")),
  })
  const credentialDisableMutation = useMutation({
    mutationFn: disableServiceCredential,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["service-accounts"] }),
    onError: (error) => setPageError(errorMessage(error, "凭证暂时无法停用")),
  })
  const operationLocked =
    manualMutation.isPending ||
    refundMutation.isPending ||
    conflictMutation.isPending ||
    accountCreateMutation.isPending ||
    accountDisableMutation.isPending ||
    credentialDisableMutation.isPending

  function applyPaymentFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setPaymentFilters({
      sourceKind: sourceKind || undefined,
      sourceSystem: sourceSystem.trim() || undefined,
      payerPhone: payerPhone.trim() || undefined,
      dateFrom: dateFrom ? new Date(`${dateFrom}T00:00:00`).toISOString() : undefined,
      dateTo: dateTo ? new Date(`${dateTo}T23:59:59.999`).toISOString() : undefined,
      page: 1,
      pageSize: PAGE_SIZE,
    })
  }

  return (
    <div className="paymentsPage">
      <TeachingSectionNav permissions={permissions} scopedPermissions={scopedPermissions} />
      <header className="teachingPageHeader admissionsPageHeader">
        <div><p className="teachingEyebrow">招生教学</p><h1>付款与退款</h1></div>
        {tab === "payments" && canManagePayments ? (
          <button type="button" className="teachingPrimaryButton" title="登记一笔付款" disabled={operationLocked} onClick={() => { manualMutation.reset(); setManualOpen(true) }}><Plus aria-hidden="true" />登记付款</button>
        ) : tab === "credentials" && canManageImports ? (
          <button type="button" className="teachingPrimaryButton" title="新建付款接口账号" disabled={operationLocked} onClick={() => { accountCreateMutation.reset(); setServiceAccountOpen(true) }}><Plus aria-hidden="true" />新建接口账号</button>
        ) : null}
      </header>

      <div className="admissionsTabs" role="tablist" aria-label="付款运营">
        <button type="button" role="tab" aria-selected={tab === "payments"} onClick={() => setTab("payments")}>付款与退款</button>
        {canManageImports ? <button type="button" role="tab" aria-selected={tab === "conflicts"} onClick={() => setTab("conflicts")}>导入冲突</button> : null}
        {canManageImports ? <button type="button" role="tab" aria-selected={tab === "credentials"} onClick={() => setTab("credentials")}>接口凭证</button> : null}
      </div>
      {pageError ? <div className="admissionsPageError" role="alert">{pageError}</div> : null}

      {tab === "payments" ? (
        <section aria-label="付款记录">
          <details className="admissionsFilterMenu" open>
            <summary title="展开或收起付款筛选"><SlidersHorizontal aria-hidden="true" />筛选付款</summary>
            <form className="admissionsFilters" onSubmit={applyPaymentFilters}>
              <label><span>付款来源</span><select value={sourceKind} onChange={(event) => setSourceKind(event.target.value as "" | "api" | "manual")}><option value="">全部</option><option value="api">接口导入</option><option value="manual">手工登记</option></select></label>
              <label><span>来源系统</span><input value={sourceSystem} onChange={(event) => setSourceSystem(event.target.value)} /></label>
              <label><span>付款手机号</span><input inputMode="tel" value={payerPhone} onChange={(event) => setPayerPhone(event.target.value)} /></label>
              <label><span>付款日期从</span><input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} /></label>
              <label><span>付款日期至</span><input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} /></label>
              <button type="submit" className="teachingSecondaryButton" title="应用付款筛选"><SlidersHorizontal aria-hidden="true" />应用筛选</button>
            </form>
          </details>
          {payments.isLoading ? <div className="admissionsEmptyState">正在读取付款记录…</div> : payments.isError ? <div className="admissionsEmptyState">付款记录暂时无法读取。</div> : payments.data?.items.length ? (
            <>
              <div className="admissionsDataTable paymentsTable" role="table" aria-label="付款记录">
                <div className="admissionsTableHeader" role="row"><span role="columnheader">付款时间</span><span role="columnheader">来源</span><span role="columnheader">付款金额</span><span role="columnheader">已退 / 可退</span><span role="columnheader">待分配单</span><span role="columnheader">操作</span></div>
                {payments.data.items.map((item) => (
                  <div className="admissionsTableRow" role="row" key={item.id}>
                    <span role="cell" data-label="付款时间">{formatDateTime(item.paid_at)}</span>
                    <span role="cell" data-label="来源">{item.source_kind === "manual" ? "手工登记" : item.source_system ?? "接口导入"}</span>
                    <span role="cell" data-label="付款金额" className="amountCell">{item.amount_cents === null ? "金额不可见" : formatCny(item.amount_cents)}</span>
                    <span role="cell" data-label="已退 / 可退">{item.refunded_cents === null ? "—" : `${formatCny(item.refunded_cents)} / ${formatCny(item.available_refund_cents)}`}</span>
                    <span role="cell" data-label="待分配单" className="monoCell">{item.admission_case_id}</span>
                    <span role="cell" data-label="操作" className="actionCell">{canRefund && item.available_refund_cents !== null && item.available_refund_cents > 0 ? <button type="button" className="admissionsRowButton" aria-label={`退款 ${item.id}`} title="从本笔付款退款" disabled={operationLocked} onClick={() => { refundMutation.reset(); setRefundPayment(item) }}><Undo2 aria-hidden="true" />退款</button> : <span>—</span>}</span>
                  </div>
                ))}
              </div>
              <Pagination page={payments.data.page} pageSize={payments.data.page_size} total={payments.data.total} onPageChange={(page) => setPaymentFilters((current) => ({ ...current, page }))} />
            </>
          ) : <div className="admissionsEmptyState">当前筛选条件下暂无付款记录。</div>}
        </section>
      ) : null}

      {tab === "conflicts" ? (
        <section aria-label="导入冲突">
          <div className="admissionsInlineFilters"><label><span>冲突状态</span><select value={conflictStatus} onChange={(event) => { setConflictStatus(event.target.value as PaymentConflictStatus); setConflictPage(1) }}><option value="open">待处理</option><option value="resolved">已解决</option><option value="dismissed">已忽略</option></select></label></div>
          {conflicts.isLoading ? <div className="admissionsEmptyState">正在读取导入冲突…</div> : conflicts.isError ? <div className="admissionsEmptyState" role="alert">导入冲突暂时无法读取。</div> : conflicts.data?.items.length ? <>
            <div className="admissionsDataTable conflictsTable" role="table" aria-label="导入冲突"><div className="admissionsTableHeader" role="row"><span role="columnheader">发生时间</span><span role="columnheader">冲突类型</span><span role="columnheader">状态</span><span role="columnheader">处理说明</span><span role="columnheader">操作</span></div>{conflicts.data.items.map((item) => <div className="admissionsTableRow" role="row" key={item.id}><span role="cell" data-label="发生时间">{formatDateTime(item.created_at)}</span><span role="cell" data-label="冲突类型">{CONFLICT_TYPE_LABELS[item.conflict_type]}</span><span role="cell" data-label="状态"><span className={`admissionsStatus status-${item.status}`}>{CONFLICT_STATUS_LABELS[item.status]}</span></span><span role="cell" data-label="处理说明">{item.resolution_note ?? "—"}</span><span role="cell" data-label="操作" className="actionCell">{item.status === "open" ? <button type="button" className="admissionsRowButton" disabled={operationLocked} onClick={() => { conflictMutation.reset(); setSelectedConflict(item) }}>处理冲突</button> : "—"}</span></div>)}</div>
            <Pagination page={conflicts.data.page} pageSize={conflicts.data.page_size} total={conflicts.data.total} onPageChange={setConflictPage} />
          </> : <div className="admissionsEmptyState">当前没有需要处理的导入冲突。</div>}
        </section>
      ) : null}

      {tab === "credentials" ? (
        <section className="serviceAccountList" aria-label="接口凭证">
          {serviceAccounts.isLoading ? <div className="admissionsEmptyState">正在读取接口账号…</div> : serviceAccounts.isError ? <div className="admissionsEmptyState" role="alert">接口账号暂时无法读取。</div> : serviceAccounts.data?.items.length ? <>{serviceAccounts.data.items.map((account) => <article className="serviceAccountCard" key={account.id}><header><div><h2>{account.name}</h2><p>{account.source_system}</p></div><span className={`admissionsStatus ${account.is_active ? "status-active" : "status-disabled"}`}>{account.is_active ? "启用中" : "已停用"}</span></header><div className="credentialList">{account.credentials.length ? account.credentials.map((credential) => <div className="credentialRow" key={credential.id}><div><strong>{credential.name}</strong><code>{credential.public_id}</code></div><span className={`admissionsStatus ${credential.is_active ? "status-active" : "status-disabled"}`}>{credential.is_active ? "启用中" : "已停用"}</span>{credential.is_active ? <button type="button" className="admissionsIconButton" title="停用凭证" aria-label={`停用凭证 ${credential.name}`} disabled={operationLocked} onClick={() => { if (window.confirm(`确认停用凭证“${credential.name}”？`)) credentialDisableMutation.mutate(credential.id) }}><Ban aria-hidden="true" /></button> : null}</div>) : <p className="admissionsMuted">尚未创建凭证</p>}</div><footer>{account.is_active ? <><button type="button" className="teachingSecondaryButton" title="为该接口账号新建凭证" disabled={operationLocked} onClick={() => setCredentialAccount(account)}><RotateCw aria-hidden="true" />新建凭证</button><button type="button" className="teachingSecondaryButton dangerText" title="停用接口账号" disabled={operationLocked} onClick={() => { if (window.confirm(`确认停用接口账号“${account.name}”？`)) accountDisableMutation.mutate(account.id) }}><Ban aria-hidden="true" />停用账号</button></> : null}</footer></article>)}<Pagination page={serviceAccounts.data.page} pageSize={serviceAccounts.data.page_size} total={serviceAccounts.data.total} onPageChange={setServicePage} /></> : <div className="admissionsEmptyState">尚未创建付款接口账号。</div>}
        </section>
      ) : null}

      {manualOpen ? <ManualPaymentDialog pending={manualMutation.isPending} error={manualMutation.isError ? errorMessage(manualMutation.error, "付款暂时无法登记") : null} onClose={() => { if (!manualMutation.isPending) setManualOpen(false) }} onSubmit={(input) => manualMutation.mutate(input)} /> : null}
      {refundPayment ? <RefundDialog payment={refundPayment} pending={refundMutation.isPending} error={refundMutation.isError ? errorMessage(refundMutation.error, "退款暂时无法登记") : null} onClose={() => { if (!refundMutation.isPending) setRefundPayment(null) }} onSubmit={(input) => refundMutation.mutate({ paymentId: refundPayment.id, input })} /> : null}
      {selectedConflict ? <ConflictDialog conflict={selectedConflict} pending={conflictMutation.isPending} error={conflictMutation.isError ? errorMessage(conflictMutation.error, "冲突暂时无法处理") : null} onClose={() => { if (!conflictMutation.isPending) setSelectedConflict(null) }} onSubmit={(action, note) => conflictMutation.mutate({ id: selectedConflict.id, action, note })} /> : null}
      {credentialAccount ? <CredentialDialog account={credentialAccount} onClose={() => setCredentialAccount(null)} onRefresh={async () => { await queryClient.invalidateQueries({ queryKey: ["service-accounts"] }) }} /> : null}
      {serviceAccountOpen ? <ServiceAccountDialog pending={accountCreateMutation.isPending} error={accountCreateMutation.isError ? errorMessage(accountCreateMutation.error, "接口账号暂时无法创建") : null} onClose={() => { if (!accountCreateMutation.isPending) setServiceAccountOpen(false) }} onSubmit={(input) => accountCreateMutation.mutate(input)} /> : null}
    </div>
  )
}
