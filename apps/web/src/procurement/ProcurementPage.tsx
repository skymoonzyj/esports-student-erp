import { useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CalendarDays,
  CheckCircle2,
  CircleDollarSign,
  Download,
  ExternalLink,
  FileCheck2,
  FileText,
  Landmark,
  Pencil,
  Plus,
  ReceiptText,
  RotateCcw,
  Search,
  Trash2,
  WalletCards,
} from "lucide-react"
import { Suspense, useMemo, useState } from "react"
import type { FormEvent } from "react"

import { formatCny, todayInChina } from "../revenueShare/format"
import {
  addSettlementCenterReserveFund,
  createPurchase,
  createSettlementCenter,
  downloadProcurementInvoice,
  exportPurchases,
  listPurchases,
  listSettlementCenters,
  removeProcurementInvoice,
  updatePurchase,
  updatePurchaseReimbursement,
  updatePurchaseSettlement,
  updateSettlementCenter,
  uploadProcurementInvoice,
} from "./api"
import type {
  InvoiceUploadInput,
  ProcurementExportFormat,
  ProcurementExportScope,
  ProcurementInvoiceStatus,
  ProcurementReimbursementStatus,
  ProcurementSettlementStatus,
  PurchaseCreateInput,
  PurchaseFilters,
  PurchaseInput,
  PurchaseRecord,
  SettlementCenter,
} from "./api"
import { createLazyModule } from "../app/loading/lazyModule"
import { ModuleBoundary } from "../app/loading/ModuleBoundary"
import { loadRouteStyles } from "../app/routeStyles"
import { ProcurementDialog, errorMessage, yuanToCents } from "./ProcurementDialogBase"
import "./procurement.css"

const PurchaseFormDialog = createLazyModule("procurement-PurchaseFormDialog", async () => {
  await loadRouteStyles("ProcurementDialogs")
  return { default: (await import("./ProcurementDialogs")).PurchaseFormDialog }
})

const InvoiceDialog = createLazyModule("procurement-InvoiceDialog", async () => {
  await loadRouteStyles("ProcurementDialogs")
  return { default: (await import("./ProcurementDialogs")).InvoiceDialog }
})

const SettlementDialog = createLazyModule("procurement-SettlementDialog", async () => {
  await loadRouteStyles("ProcurementDialogs")
  return { default: (await import("./ProcurementDialogs")).SettlementDialog }
})

const ReimbursementDialog = createLazyModule("procurement-ReimbursementDialog", async () => {
  await loadRouteStyles("ProcurementDialogs")
  return { default: (await import("./ProcurementDialogs")).ReimbursementDialog }
})

type PeriodMode = "all" | "day" | "month"

const procurementRootKey = ["procurement"] as const

function monthRange(value: string): { startDate?: string; endDate?: string } {
  const match = /^(\d{4})-(\d{2})$/.exec(value)
  if (!match) return {}
  const year = Number(match[1])
  const month = Number(match[2])
  if (year < 1000 || month < 1 || month > 12) return {}
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate()
  return {
    startDate: `${value}-01`,
    endDate: `${value}-${String(lastDay).padStart(2, "0")}`,
  }
}

function periodFilters(mode: PeriodMode, day: string, month: string): Pick<
  PurchaseFilters,
  "startDate" | "endDate"
> {
  if (mode === "day" && day) return { startDate: day, endDate: day }
  if (mode === "month") return monthRange(month)
  return {}
}

function InvoiceRemovalDialog({
  record,
  pending,
  onClose,
  onConfirm,
}: {
  record: PurchaseRecord
  pending: boolean
  onClose: () => void
  onConfirm: () => Promise<void>
}) {
  const [error, setError] = useState<string | null>(null)
  async function confirm(): Promise<void> {
    try {
      await onConfirm()
    } catch (caught) {
      setError(errorMessage(caught, "发票暂时无法移除"))
    }
  }
  return (
    <ProcurementDialog
      title="移除发票"
      description={`${record.item_name} · ${record.invoice?.original_filename ?? "发票文件"}`}
      onClose={onClose}
    >
      <p className="procurementDialogNotice">
        移除后，这张发票关联的全部采购记录都会恢复为“未开票”，发票文件也会删除。
      </p>
      {error ? <p className="formError" role="alert">{error}</p> : null}
      <div className="procurementDialogActions">
        <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button>
        <button className="primaryButton dangerButton" type="button" onClick={() => void confirm()} disabled={pending}>
          <Trash2 aria-hidden="true" />{pending ? "正在移除…" : "确认移除"}
        </button>
      </div>
    </ProcurementDialog>
  )
}

function SettlementCentersDialog({
  centers,
  canManageReserveFund,
  pending,
  onClose,
  onCreate,
  onUpdate,
  onAddReserveFund,
}: {
  centers: SettlementCenter[]
  canManageReserveFund: boolean
  pending: boolean
  onClose: () => void
  onCreate: (name: string) => Promise<void>
  onUpdate: (centerId: string, input: { name?: string; is_active?: boolean }) => Promise<void>
  onAddReserveFund: (centerId: string, amountCents: number, note: string | null, operationKey: string) => Promise<void>
}) {
  const [name, setName] = useState("")
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState("")
  const [fundingId, setFundingId] = useState<string | null>(null)
  const [fundingAmount, setFundingAmount] = useState("")
  const [fundingNote, setFundingNote] = useState("")
  const [fundingOperationKey, setFundingOperationKey] = useState(() => crypto.randomUUID())
  const [error, setError] = useState<string | null>(null)
  const fundingAmountCents = yuanToCents(fundingAmount)

  async function create(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!name.trim()) return
    setError(null)
    try {
      await onCreate(name.trim())
      setName("")
    } catch (caught) {
      setError(errorMessage(caught, "结算中心暂时无法新增"))
    }
  }

  async function saveName(center: SettlementCenter): Promise<void> {
    if (!editingName.trim()) return
    setError(null)
    try {
      await onUpdate(center.id, { name: editingName.trim() })
      setEditingId(null)
    } catch (caught) {
      setError(errorMessage(caught, "结算中心暂时无法更新"))
    }
  }

  async function toggle(center: SettlementCenter): Promise<void> {
    setError(null)
    try {
      await onUpdate(center.id, { is_active: !center.is_active })
    } catch (caught) {
      setError(errorMessage(caught, "结算中心状态暂时无法更新"))
    }
  }

  async function addReserveFund(
    event: FormEvent<HTMLFormElement>,
    center: SettlementCenter,
  ): Promise<void> {
    event.preventDefault()
    if (fundingAmountCents === null || fundingAmountCents <= 0) {
      setError("备用金金额必须大于 0，最多保留 2 位小数")
      return
    }
    setError(null)
    try {
      await onAddReserveFund(
        center.id,
        fundingAmountCents,
        fundingNote.trim() || null,
        fundingOperationKey,
      )
      setFundingId(null)
      setFundingAmount("")
      setFundingNote("")
    } catch (caught) {
      setError(errorMessage(caught, "备用金暂时无法增加"))
    }
  }

  return (
    <ProcurementDialog
      title="结算中心"
      description="备用金按入账累计并扣减实际使用；停用不会影响历史采购记录。"
      onClose={onClose}
    >
      <form className="procurementCenterCreate" onSubmit={(event) => void create(event)}>
        <label>
          <span>新增结算中心</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={120}
            placeholder="例如：青训营运营中心"
            required
          />
        </label>
        <button className="primaryButton" type="submit" disabled={pending || !name.trim()}>
          <Plus aria-hidden="true" />新增
        </button>
      </form>
      <div className="procurementCenterList" aria-label="结算中心列表">
        {centers.map((center) => (
          <div className="procurementCenterItem" key={center.id}>
            <div className="procurementCenterRow">
              {editingId === center.id ? (
                <input
                  aria-label={`编辑${center.name}`}
                  value={editingName}
                  onChange={(event) => setEditingName(event.target.value)}
                  maxLength={120}
                />
              ) : (
                <div className="procurementCenterInfo">
                  <div>
                    <strong>{center.name}</strong>
                    <span className={`procurementStatus ${center.is_active ? "active" : "inactive"}`}>
                      {center.is_active ? "启用中" : "已停用"}
                    </span>
                  </div>
                  <div className="procurementCenterFundMetrics">
                    <span>累计 {formatCny(center.reserve_fund_total_cents)}</span>
                    <span>已使用 {formatCny(center.reserve_fund_used_cents)}</span>
                    <strong>剩余 {formatCny(center.reserve_fund_remaining_cents)}</strong>
                  </div>
                </div>
              )}
              <div className="procurementCenterActions">
                {editingId === center.id ? (
                  <>
                    <button
                      className="procurementTextButton"
                      type="button"
                      onClick={() => void saveName(center)}
                      disabled={pending || !editingName.trim()}
                    >
                      保存
                    </button>
                    <button
                      className="procurementTextButton"
                      type="button"
                      onClick={() => setEditingId(null)}
                      disabled={pending}
                    >
                      取消
                    </button>
                  </>
                ) : (
                  <>
                    {canManageReserveFund ? (
                      <button
                        className="procurementTextButton"
                        type="button"
                        aria-label={`给${center.name}增加备用金`}
                        onClick={() => {
                          setFundingId(center.id)
                          setFundingOperationKey(crypto.randomUUID())
                          setFundingAmount("")
                          setFundingNote("")
                        }}
                        disabled={pending || !center.is_active}
                      >
                        <WalletCards aria-hidden="true" />增加备用金
                      </button>
                    ) : null}
                    <button
                      className="procurementIconButton"
                      type="button"
                      title="编辑结算中心"
                      aria-label={`编辑${center.name}`}
                      onClick={() => {
                        setEditingId(center.id)
                        setEditingName(center.name)
                      }}
                    >
                      <Pencil aria-hidden="true" />
                    </button>
                    <button
                      className="procurementTextButton"
                      type="button"
                      onClick={() => void toggle(center)}
                      disabled={pending}
                    >
                      {center.is_active ? "停用" : "启用"}
                    </button>
                  </>
                )}
              </div>
            </div>
            {fundingId === center.id ? (
              <form
                className="procurementReserveFundForm"
                aria-label={`给${center.name}增加备用金`}
                onSubmit={(event) => void addReserveFund(event, center)}
              >
                <label>
                  <span>增加金额（元）</span>
                  <input
                    aria-label={`${center.name}备用金金额`}
                    inputMode="decimal"
                    value={fundingAmount}
                    onChange={(event) => setFundingAmount(event.target.value)}
                    placeholder="0.00"
                    required
                  />
                </label>
                <label>
                  <span>备注（选填）</span>
                  <input
                    aria-label={`${center.name}备用金备注`}
                    value={fundingNote}
                    onChange={(event) => setFundingNote(event.target.value)}
                    maxLength={500}
                    placeholder="例如：9 月行政备用金"
                  />
                </label>
                <div className="procurementReserveFundActions">
                  <button
                    className="secondaryButton"
                    type="button"
                    onClick={() => setFundingId(null)}
                    disabled={pending}
                  >
                    取消
                  </button>
                  <button
                    className="primaryButton"
                    type="submit"
                    disabled={pending || fundingAmountCents === null || fundingAmountCents <= 0}
                  >
                    确认增加
                  </button>
                </div>
              </form>
            ) : null}
          </div>
        ))}
        {centers.length === 0 ? <p className="procurementEmptyLine">尚未设置结算中心。</p> : null}
      </div>
      {error ? <p className="formError" role="alert">{error}</p> : null}
    </ProcurementDialog>
  )
}

function ExportDialog({
  filters,
  centers,
  pending,
  onClose,
  onSubmit,
}: {
  filters: PurchaseFilters
  centers: SettlementCenter[]
  pending: boolean
  onClose: () => void
  onSubmit: (
    scope: ProcurementExportScope,
    format: ProcurementExportFormat,
    purpose: string,
    settlementCenterId: string,
  ) => Promise<void>
}) {
  const [scope, setScope] = useState<ProcurementExportScope>("current")
  const [format, setFormat] = useState<ProcurementExportFormat>("xlsx")
  const [purpose, setPurpose] = useState("")
  const [settlementCenterId, setSettlementCenterId] = useState(
    filters.settlementCenterId ?? "",
  )
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (purpose.trim().length < 4) {
      setError("请填写至少 4 个字的导出用途")
      return
    }
    try {
      await onSubmit(scope, format, purpose.trim(), settlementCenterId)
    } catch (caught) {
      setError(errorMessage(caught, "采购台账暂时无法导出"))
    }
  }

  const filtered = Boolean(
    filters.query || filters.settlementCenterId || filters.reimbursementStatus
    || filters.settlementStatus || filters.invoiceStatus || filters.startDate || filters.endDate,
  )
  const description = scope === "current"
    ? (filtered ? "将导出当前筛选结果。" : "当前未设置筛选，将导出全部采购记录。")
    : scope === "all"
      ? "将忽略当前筛选，导出全部采购记录。"
      : scope === "unreimbursed"
        ? "将按当前范围导出待报销汇总表。"
        : scope === "reimbursed"
          ? "将按当前范围导出已报销记录。"
          : scope === "unsettled"
            ? "将按当前范围导出待结算记录。"
            : "将按当前范围导出已结算记录。"
  const selectedCenter = centers.find((center) => center.id === settlementCenterId)
  return (
    <ProcurementDialog
      title="导出采购台账"
      description={description}
      onClose={onClose}
    >
      <form className="procurementForm" onSubmit={(event) => void submit(event)}>
        <label>
          <span>导出范围</span>
          <select
            aria-label="导出范围"
            value={scope}
            onChange={(event) => {
              const nextScope = event.target.value as ProcurementExportScope
              setScope(nextScope)
              if (nextScope === "unreimbursed") setFormat("xlsx")
            }}
          >
            <option value="current">当前筛选结果</option>
            <option value="all">全部采购记录</option>
            <option value="unreimbursed">待报销</option>
            <option value="reimbursed">已报销</option>
            <option value="unsettled">待结算</option>
            <option value="settled">已结算</option>
          </select>
        </label>
        <label>
          <span>导出用途</span>
          <input
            value={purpose}
            onChange={(event) => setPurpose(event.target.value)}
            minLength={4}
            maxLength={200}
            placeholder="例如：月度采购对账"
            required
          />
        </label>
        <label>
          <span>结算中心</span>
          <select
            aria-label="导出结算中心"
            value={settlementCenterId}
            onChange={(event) => setSettlementCenterId(event.target.value)}
          >
            <option value="">全部结算中心</option>
            {centers.map((center) => (
              <option key={center.id} value={center.id}>
                {center.name}{center.is_active ? "" : "（已停用）"}
              </option>
            ))}
          </select>
          <small>
            {selectedCenter
              ? `仅导出归属“${selectedCenter.name}”的记录。`
              : "导出所有结算中心的记录。"}
          </small>
        </label>
        <label>
          <span>文件格式</span>
          <select
            value={format}
            onChange={(event) => setFormat(event.target.value as ProcurementExportFormat)}
            disabled={scope === "unreimbursed"}
          >
            <option value="xlsx">Excel（XLSX）</option>
            <option value="csv">CSV</option>
          </select>
        </label>
        {error ? <p className="formError" role="alert">{error}</p> : null}
        <div className="procurementDialogActions">
          <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>
            取消
          </button>
          <button className="primaryButton" type="submit" disabled={pending}>
            <Download aria-hidden="true" />{pending ? "正在导出…" : "开始导出"}
          </button>
        </div>
      </form>
    </ProcurementDialog>
  )
}

export type ProcurementPageProps = {
  canManage: boolean
  canSettle: boolean
  canExport: boolean
  canManageCenters: boolean
  canManageInvoices: boolean
  canCreateAssets?: boolean
}

export function ProcurementPage({
  canManage,
  canSettle,
  canExport,
  canManageCenters,
  canManageInvoices,
  canCreateAssets = false,
}: ProcurementPageProps) {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState("")
  const [centerId, setCenterId] = useState("")
  const [reimbursementStatus, setReimbursementStatus] = useState<
    ProcurementReimbursementStatus | ""
  >("")
  const [settlementStatus, setSettlementStatus] = useState<ProcurementSettlementStatus | "">("")
  const [invoiceStatus, setInvoiceStatus] = useState<ProcurementInvoiceStatus | "">("")
  const [periodMode, setPeriodMode] = useState<PeriodMode>("all")
  const [day, setDay] = useState(todayInChina())
  const [month, setMonth] = useState(todayInChina().slice(0, 7))
  const [filters, setFilters] = useState<PurchaseFilters>({ page: 1, pageSize: 50 })
  const [editing, setEditing] = useState<PurchaseRecord | null | undefined>(undefined)
  const [reimbursing, setReimbursing] = useState<PurchaseRecord | null>(null)
  const [settling, setSettling] = useState<PurchaseRecord | null>(null)
  const [invoicing, setInvoicing] = useState<PurchaseRecord | null | undefined>(undefined)
  const [removingInvoice, setRemovingInvoice] = useState<PurchaseRecord | null>(null)
  const [centersOpen, setCentersOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)

  const centers = useQuery({
    queryKey: [...procurementRootKey, "centers"],
    queryFn: () => listSettlementCenters(true),
  })
  const purchases = useQuery({
    queryKey: [...procurementRootKey, "list", filters],
    queryFn: () => listPurchases(filters),
  })
  const selectedFilterCenter = useMemo(
    () => centers.data?.find((center) => center.id === filters.settlementCenterId),
    [centers.data, filters.settlementCenterId],
  )
  const centerName = selectedFilterCenter?.name

  async function refresh(includeCenters = false): Promise<void> {
    await queryClient.invalidateQueries({ queryKey: procurementRootKey })
    if (includeCenters) {
      await queryClient.invalidateQueries({ queryKey: [...procurementRootKey, "centers"] })
    }
  }

  function applyFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setFilters({
      query: query.trim() || undefined,
      settlementCenterId: centerId || undefined,
      reimbursementStatus,
      settlementStatus,
      invoiceStatus,
      ...periodFilters(periodMode, day, month),
      page: 1,
      pageSize: 50,
    })
  }

  function resetFilters(): void {
    setQuery("")
    setCenterId("")
    setReimbursementStatus("")
    setSettlementStatus("")
    setInvoiceStatus("")
    setPeriodMode("all")
    setDay(todayInChina())
    setMonth(todayInChina().slice(0, 7))
    setFilters({ page: 1, pageSize: 50 })
  }

  async function savePurchase(input: PurchaseCreateInput | PurchaseInput): Promise<void> {
    setPending(true)
    setPageError(null)
    try {
      if (editing) await updatePurchase(editing.id, input as PurchaseInput)
      else await createPurchase(input as PurchaseCreateInput)
      setEditing(undefined)
      await refresh()
    } catch (caught) {
      setPageError(errorMessage(caught, "采购记录暂时无法保存"))
      throw caught
    } finally {
      setPending(false)
    }
  }

  async function saveSettlement(isSettled: boolean, settledOn: string | null): Promise<void> {
    if (!settling) return
    setPending(true)
    setPageError(null)
    try {
      await updatePurchaseSettlement(settling.id, {
        is_settled: isSettled,
        settled_on: settledOn,
      })
      setSettling(null)
      await refresh()
    } catch (caught) {
      setPageError(errorMessage(caught, "结算状态暂时无法更新"))
      throw caught
    } finally {
      setPending(false)
    }
  }

  async function saveReimbursement(isReimbursed: boolean): Promise<void> {
    if (!reimbursing) return
    setPending(true)
    setPageError(null)
    try {
      await updatePurchaseReimbursement(reimbursing.id, isReimbursed)
      setReimbursing(null)
      await refresh()
    } catch (caught) {
      setPageError(errorMessage(caught, "报销状态暂时无法更新"))
      throw caught
    } finally {
      setPending(false)
    }
  }

  async function saveInvoice(input: InvoiceUploadInput): Promise<void> {
    setPending(true)
    setPageError(null)
    try {
      await uploadProcurementInvoice(input)
      setInvoicing(undefined)
      await refresh()
    } catch (caught) {
      setPageError(errorMessage(caught, "发票暂时无法上传"))
      throw caught
    } finally {
      setPending(false)
    }
  }

  async function downloadInvoice(record: PurchaseRecord): Promise<void> {
    if (!record.invoice) return
    setPageError(null)
    try {
      const download = await downloadProcurementInvoice(record.invoice.id)
      const url = URL.createObjectURL(download.blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = download.filename
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch (caught) {
      setPageError(errorMessage(caught, "发票文件暂时无法下载"))
    }
  }

  async function confirmRemoveInvoice(): Promise<void> {
    if (!removingInvoice?.invoice) return
    setPending(true)
    setPageError(null)
    try {
      await removeProcurementInvoice(removingInvoice.invoice.id)
      setRemovingInvoice(null)
      await refresh()
    } catch (caught) {
      setPageError(errorMessage(caught, "发票暂时无法移除"))
      throw caught
    } finally {
      setPending(false)
    }
  }

  async function createCenter(name: string): Promise<void> {
    setPending(true)
    try {
      await createSettlementCenter(name)
      await refresh(true)
    } finally {
      setPending(false)
    }
  }

  async function updateCenter(
    id: string,
    input: { name?: string; is_active?: boolean },
  ): Promise<void> {
    setPending(true)
    try {
      await updateSettlementCenter(id, input)
      await refresh(true)
    } finally {
      setPending(false)
    }
  }

  async function addReserveFund(
    id: string,
    amountCents: number,
    note: string | null,
    operationKey: string,
  ): Promise<void> {
    setPending(true)
    try {
      await addSettlementCenterReserveFund(id, {
        operation_key: operationKey,
        amount_cents: amountCents,
        note,
      })
      await refresh(true)
    } finally {
      setPending(false)
    }
  }

  async function downloadExport(
    scope: ProcurementExportScope,
    format: ProcurementExportFormat,
    purpose: string,
    settlementCenterId: string,
  ): Promise<void> {
    setPending(true)
    setPageError(null)
    try {
      const download = await exportPurchases({
        ...filters,
        settlementCenterId: settlementCenterId || undefined,
        scope,
        format,
        purpose,
      })
      const url = URL.createObjectURL(download.blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = download.filename
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      setExportOpen(false)
    } catch (caught) {
      setPageError(errorMessage(caught, "采购台账暂时无法导出"))
      throw caught
    } finally {
      setPending(false)
    }
  }

  const pageCount = purchases.data
    ? Math.max(1, Math.ceil(purchases.data.total / purchases.data.page_size))
    : 1

  return (
    <section className="procurementPage" aria-labelledby="procurement-page-title">
      <header className="procurementPageHeader">
        <div>
          <p className="pageEyebrow">DAILY PROCUREMENT</p>
          <h1 id="procurement-page-title">日常采购</h1>
          <p>登记日杂采购，并分别跟踪备用金、行政报销、结算中心结算与发票状态。</p>
        </div>
        <div className="procurementHeaderActions">
          {canManageCenters ? (
            <button className="secondaryButton" type="button" onClick={() => setCentersOpen(true)}>
              <Landmark aria-hidden="true" />结算中心
            </button>
          ) : null}
          {canExport ? (
            <button className="secondaryButton" type="button" onClick={() => setExportOpen(true)}>
              <Download aria-hidden="true" />导出
            </button>
          ) : null}
          {canManageInvoices ? (
            <button className="secondaryButton" type="button" onClick={() => setInvoicing(null)}>
              <ReceiptText aria-hidden="true" />上传发票
            </button>
          ) : null}
          {canManage ? (
            <button className="primaryButton" type="button" onClick={() => setEditing(null)}>
              <Plus aria-hidden="true" />新增采购
            </button>
          ) : null}
        </div>
      </header>

      <section className="procurementSummary" aria-label="采购金额统计">
        <div><span>采购记录</span><strong>{purchases.data?.total ?? 0}</strong></div>
        <div><span>采购总额</span><strong>{formatCny(purchases.data?.total_amount_cents ?? 0)}</strong></div>
        <div><span>已报销</span><strong>{formatCny(purchases.data?.reimbursed_amount_cents ?? 0)}</strong></div>
        <div><span>未报销</span><strong>{formatCny(purchases.data?.unreimbursed_amount_cents ?? 0)}</strong></div>
        <div><span>已结算</span><strong>{formatCny(purchases.data?.settled_amount_cents ?? 0)}</strong></div>
        <div><span>未结算</span><strong>{formatCny(purchases.data?.unsettled_amount_cents ?? 0)}</strong></div>
      </section>

      {selectedFilterCenter ? (
        <section className="procurementReserveFundOverview" aria-label={`${selectedFilterCenter.name}备用金`}>
          <div>
            <span>结算中心备用金</span>
            <strong>{selectedFilterCenter.name}</strong>
          </div>
          <div><span>累计备用金</span><strong>{formatCny(selectedFilterCenter.reserve_fund_total_cents)}</strong></div>
          <div><span>已使用</span><strong>{formatCny(selectedFilterCenter.reserve_fund_used_cents)}</strong></div>
          <div><span>剩余备用金</span><strong>{formatCny(selectedFilterCenter.reserve_fund_remaining_cents)}</strong></div>
        </section>
      ) : null}

      <form className="procurementFilters" onSubmit={applyFilters}>
        <div className="procurementFilterField procurementSearchField">
          <label htmlFor="procurement-query">搜索物品</label>
          <div>
            <Search aria-hidden="true" />
            <input
              id="procurement-query"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="物品名称或备注"
              maxLength={120}
            />
          </div>
        </div>
        <div className="procurementFilterField">
          <label htmlFor="procurement-center-filter">结算中心</label>
          <select
            id="procurement-center-filter"
            value={centerId}
            onChange={(event) => setCenterId(event.target.value)}
          >
            <option value="">全部中心</option>
            {(centers.data ?? []).map((center) => (
              <option key={center.id} value={center.id}>
                {center.name}{center.is_active ? "" : "（已停用）"}
              </option>
            ))}
          </select>
        </div>
        <div className="procurementFilterField">
          <label htmlFor="procurement-reimbursement-filter">报销状态</label>
          <select
            id="procurement-reimbursement-filter"
            value={reimbursementStatus}
            onChange={(event) => {
              setReimbursementStatus(event.target.value as ProcurementReimbursementStatus | "")
            }}
          >
            <option value="">全部状态</option>
            <option value="unreimbursed">未报销</option>
            <option value="reimbursed">已报销</option>
          </select>
        </div>
        <div className="procurementFilterField">
          <label htmlFor="procurement-invoice-filter">开票状态</label>
          <select
            id="procurement-invoice-filter"
            value={invoiceStatus}
            onChange={(event) => {
              setInvoiceStatus(event.target.value as ProcurementInvoiceStatus | "")
            }}
          >
            <option value="">全部状态</option>
            <option value="uninvoiced">未开票</option>
            <option value="invoiced">已开票</option>
          </select>
        </div>
        <div className="procurementFilterField">
          <label htmlFor="procurement-status-filter">结算状态</label>
          <select
            id="procurement-status-filter"
            value={settlementStatus}
            onChange={(event) => {
              setSettlementStatus(event.target.value as ProcurementSettlementStatus | "")
            }}
          >
            <option value="">全部状态</option>
            <option value="unsettled">未结算</option>
            <option value="settled">已结算</option>
          </select>
        </div>
        <div className="procurementPeriodField">
          <span>采购周期</span>
          <div className="procurementSegmented" role="group" aria-label="采购周期">
            {(["all", "day", "month"] as const).map((mode) => (
              <button
                type="button"
                className={periodMode === mode ? "active" : ""}
                aria-pressed={periodMode === mode}
                key={mode}
                onClick={() => setPeriodMode(mode)}
              >
                {mode === "all" ? "全部" : mode === "day" ? "按日" : "按月"}
              </button>
            ))}
          </div>
          {periodMode === "day" ? (
            <input
              aria-label="采购日期筛选"
              type="date"
              min="1000-01-01"
              max="9999-12-31"
              value={day}
              onChange={(event) => setDay(event.target.value)}
            />
          ) : null}
          {periodMode === "month" ? (
            <input
              aria-label="采购月份筛选"
              type="month"
              min="1000-01"
              max="9999-12"
              value={month}
              onChange={(event) => setMonth(event.target.value)}
            />
          ) : null}
        </div>
        <div className="procurementFilterActions">
          <button className="primaryButton" type="submit">查询</button>
          <button className="secondaryButton" type="button" onClick={resetFilters}>清空</button>
        </div>
      </form>

      {centerName ? <p className="procurementAppliedFilter">当前结算中心：{centerName}</p> : null}
      {pageError ? <p className="formError" role="alert">{pageError}</p> : null}

      <section className="procurementLedger" aria-labelledby="procurement-ledger-title">
        <header>
          <div>
            <p className="pageEyebrow">PURCHASE LEDGER</p>
            <h2 id="procurement-ledger-title">采购台账</h2>
          </div>
          <span>{purchases.data ? `第 ${purchases.data.page} / ${pageCount} 页` : "正在读取"}</span>
        </header>
        {purchases.isPending ? <div className="procurementState">正在读取采购记录…</div> : null}
        {purchases.isError ? <div className="procurementState" role="alert">采购记录暂时无法读取。</div> : null}
        {purchases.data && purchases.data.items.length === 0 ? (
          <div className="procurementState">当前条件下没有采购记录。</div>
        ) : null}
        {purchases.data && purchases.data.items.length > 0 ? (
          <div className="procurementTableWrap">
            <table className="procurementTable">
              <thead>
                <tr>
                  <th>采购日期</th>
                  <th>物品</th>
                  <th>渠道</th>
                  <th>数量</th>
                  <th>单价</th>
                  <th>总价</th>
                  <th>结算中心</th>
                  <th>资金来源</th>
                  <th>报销</th>
                  <th>结算</th>
                  <th>发票</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {purchases.data.items.map((record) => (
                  <tr key={record.id}>
                    <td><CalendarDays aria-hidden="true" />{record.purchase_date}</td>
                    <td><strong>{record.item_name}</strong><small>{record.note || "无备注"}</small></td>
                    <td className="procurementChannelCell">
                      <span className={`procurementStatus ${record.purchase_channel}`}>
                        {record.purchase_channel === "online" ? "线上" : "线下"}
                      </span>
                      {record.product_url ? (
                        <a
                          className="procurementProductLink"
                          href={record.product_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="打开商品链接"
                          aria-label={`打开${record.item_name}商品链接`}
                        >
                          <ExternalLink aria-hidden="true" />
                        </a>
                      ) : null}
                    </td>
                    <td>{record.quantity}</td>
                    <td>{formatCny(record.unit_price_cents)}</td>
                    <td><strong>{formatCny(record.total_price_cents)}</strong></td>
                    <td>{record.settlement_center_name}</td>
                    <td>
                      <span className={`procurementStatus ${record.uses_reserve_fund ? "reserveFund" : "otherFunds"}`}>
                        {record.uses_reserve_fund ? "备用金" : "其他资金"}
                      </span>
                    </td>
                    <td>
                      <span className={`procurementStatus ${record.is_reimbursed ? "reimbursed" : "unreimbursed"}`}>
                        {record.is_reimbursed ? "已报销" : "未报销"}
                      </span>
                    </td>
                    <td>
                      <span className={`procurementStatus ${record.is_settled ? "settled" : "unsettled"}`}>
                        {record.is_settled ? "已结算" : "未结算"}
                      </span>
                      {record.settled_on ? <small>{record.settled_on}</small> : null}
                    </td>
                    <td>
                      <span className={`procurementStatus ${record.has_invoice ? "invoiced" : "uninvoiced"}`}>
                        {record.has_invoice ? "已开票" : "未开票"}
                      </span>
                      {record.invoice ? (
                        <>
                          <small>{record.invoice.invoice_date} · {formatCny(record.invoice.amount_cents)}</small>
                          <button
                            className="procurementInvoiceFileButton"
                            type="button"
                            title={record.invoice.original_filename}
                            onClick={() => void downloadInvoice(record)}
                          >
                            <FileText aria-hidden="true" />查看发票
                          </button>
                        </>
                      ) : null}
                    </td>
                    <td>
                      <div className="procurementRowActions">
                        {canManage && !record.is_reimbursed && !record.is_settled && !record.has_invoice ? (
                          <button
                            className="procurementIconButton"
                            type="button"
                            title="编辑采购记录"
                            aria-label={`编辑${record.item_name}`}
                            onClick={() => setEditing(record)}
                          >
                            <Pencil aria-hidden="true" />
                          </button>
                        ) : null}
                        {canManage && record.is_reimbursed ? (
                          <small>修改采购信息前请先撤销报销</small>
                        ) : null}
                        {canManage ? (
                          <button
                            className="procurementTextButton"
                            type="button"
                            onClick={() => setReimbursing(record)}
                          >
                            <CircleDollarSign aria-hidden="true" />
                            {record.is_reimbursed ? "撤销报销" : "标记已报销"}
                          </button>
                        ) : null}
                        {canManageInvoices && !record.has_invoice ? (
                          <button
                            className="procurementTextButton"
                            type="button"
                            onClick={() => setInvoicing(record)}
                          >
                            <FileCheck2 aria-hidden="true" />关联发票
                          </button>
                        ) : null}
                        {canManageInvoices && record.has_invoice ? (
                          <button
                            className="procurementIconButton"
                            type="button"
                            title="移除发票"
                            aria-label={`移除${record.item_name}的发票`}
                            onClick={() => setRemovingInvoice(record)}
                          >
                            <Trash2 aria-hidden="true" />
                          </button>
                        ) : null}
                        {canSettle ? (
                          <button
                            className="procurementTextButton"
                            type="button"
                            onClick={() => setSettling(record)}
                          >
                            {record.is_settled ? (
                              <><RotateCcw aria-hidden="true" />撤销结算</>
                            ) : (
                              <><CheckCircle2 aria-hidden="true" />确认结算</>
                            )}
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {purchases.data && pageCount > 1 ? (
          <footer className="procurementPagination">
            <button
              className="secondaryButton"
              type="button"
              disabled={purchases.data.page <= 1}
              onClick={() => setFilters((current) => ({ ...current, page: (current.page ?? 1) - 1 }))}
            >
              上一页
            </button>
            <span>共 {purchases.data.total} 条</span>
            <button
              className="secondaryButton"
              type="button"
              disabled={purchases.data.page >= pageCount}
              onClick={() => setFilters((current) => ({ ...current, page: (current.page ?? 1) + 1 }))}
            >
              下一页
            </button>
          </footer>
        ) : null}
      </section>

      {editing !== undefined && canManage ? (
        <ModuleBoundary moduleId="procurement-PurchaseFormDialog" retry={PurchaseFormDialog.retry}><Suspense fallback={<p role="status">正在加载弹窗…</p>}>
        <PurchaseFormDialog.Component
          key={editing?.id ?? "new-purchase"}
          record={editing}
          centers={centers.data ?? []}
          canSettle={canSettle}
          canCreateAssets={canCreateAssets}
          pending={pending}
          onClose={() => setEditing(undefined)}
          onSubmit={savePurchase}
        />
        </Suspense></ModuleBoundary>
      ) : null}
      {settling && canSettle ? (
        <ModuleBoundary moduleId="procurement-SettlementDialog" retry={SettlementDialog.retry}><Suspense fallback={<p role="status">正在加载弹窗…</p>}>
        <SettlementDialog.Component
          record={settling}
          pending={pending}
          onClose={() => setSettling(null)}
          onSubmit={saveSettlement}
        />
        </Suspense></ModuleBoundary>
      ) : null}
      {reimbursing && canManage ? (
        <ModuleBoundary moduleId="procurement-ReimbursementDialog" retry={ReimbursementDialog.retry}><Suspense fallback={<p role="status">正在加载弹窗…</p>}>
        <ReimbursementDialog.Component
          record={reimbursing}
          pending={pending}
          onClose={() => setReimbursing(null)}
          onSubmit={saveReimbursement}
        />
        </Suspense></ModuleBoundary>
      ) : null}
      {invoicing !== undefined && canManageInvoices ? (
        <ModuleBoundary moduleId="procurement-InvoiceDialog" retry={InvoiceDialog.retry}><Suspense fallback={<p role="status">正在加载弹窗…</p>}>
        <InvoiceDialog.Component
          key={invoicing?.id ?? "new-invoice"}
          initialRecord={invoicing}
          settlementCenterId={filters.settlementCenterId}
          pending={pending}
          onClose={() => setInvoicing(undefined)}
          onSubmit={saveInvoice}
        />
        </Suspense></ModuleBoundary>
      ) : null}
      {removingInvoice && canManageInvoices ? (
        <InvoiceRemovalDialog
          record={removingInvoice}
          pending={pending}
          onClose={() => setRemovingInvoice(null)}
          onConfirm={confirmRemoveInvoice}
        />
      ) : null}
      {centersOpen && canManageCenters ? (
        <SettlementCentersDialog
          centers={centers.data ?? []}
          canManageReserveFund={canManage}
          pending={pending}
          onClose={() => setCentersOpen(false)}
          onCreate={createCenter}
          onUpdate={updateCenter}
          onAddReserveFund={addReserveFund}
        />
      ) : null}
      {exportOpen && canExport ? (
        <ExportDialog
          filters={filters}
          centers={centers.data ?? []}
          pending={pending}
          onClose={() => setExportOpen(false)}
          onSubmit={downloadExport}
        />
      ) : null}
    </section>
  )
}
