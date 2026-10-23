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
  Sparkles,
  Trash2,
  Upload,
  WalletCards,
  X,
} from "lucide-react"
import { useMemo, useState } from "react"
import type { FormEvent, ReactNode } from "react"

import { ProblemDetailsError } from "../api/client"
import { formatCny, todayInChina } from "../revenueShare/format"
import {
  addSettlementCenterReserveFund,
  createPurchase,
  createSettlementCenter,
  downloadProcurementInvoice,
  exportPurchases,
  findInvoiceMatches,
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
  InvoiceMatchSuggestion,
  InvoiceUploadInput,
  ProcurementExportFormat,
  ProcurementExportScope,
  ProcurementInvoiceStatus,
  ProcurementPurchaseChannel,
  ProcurementReimbursementStatus,
  ProcurementSettlementStatus,
  PurchaseCreateInput,
  PurchaseFilters,
  PurchaseInput,
  PurchaseRecord,
  SettlementCenter,
} from "./api"
import "./procurement.css"

type PeriodMode = "all" | "day" | "month"

const procurementRootKey = ["procurement"] as const

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function initialInvoiceMatch(record: PurchaseRecord | null): InvoiceMatchSuggestion | null {
  if (!record) return null
  return {
    amount_cents: record.total_price_cents,
    matched_total_cents: record.total_price_cents,
    suggested_purchase_ids: [record.id],
    candidates: [{
      id: record.id,
      purchase_date: record.purchase_date,
      item_name: record.item_name,
      total_price_cents: record.total_price_cents,
      settlement_center_id: record.settlement_center_id,
      settlement_center_name: record.settlement_center_name,
    }],
  }
}

function yuanToCents(value: string): number | null {
  const match = /^(\d{1,11})(?:\.(\d{1,2}))?$/.exec(value.trim())
  if (!match) return null
  const cents = Number(match[1]) * 100 + Number((match[2] ?? "").padEnd(2, "0") || "0")
  return Number.isSafeInteger(cents) && cents > 0 ? cents : null
}

function validQuantity(value: string): boolean {
  return /^\d{1,9}(?:\.\d{1,3})?$/.test(value.trim()) && Number(value) > 0
}

function validHttpUrl(value: string): boolean {
  try {
    const parsed = new URL(value)
    return ["http:", "https:"].includes(parsed.protocol) && Boolean(parsed.hostname)
  } catch {
    return false
  }
}

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

function ProcurementDialog({
  title,
  description,
  children,
  onClose,
}: {
  title: string
  description?: string
  children: ReactNode
  onClose: () => void
}) {
  return (
    <div className="procurementDialogBackdrop" onMouseDown={onClose}>
      <section
        className="procurementDialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="procurementDialogHeader">
          <div>
            <h2>{title}</h2>
            {description ? <p>{description}</p> : null}
          </div>
          <button
            className="procurementIconButton"
            type="button"
            title="关闭"
            aria-label="关闭"
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

function PurchaseFormDialog({
  record,
  centers,
  canSettle,
  canCreateAssets,
  pending,
  onClose,
  onSubmit,
}: {
  record: PurchaseRecord | null
  centers: SettlementCenter[]
  canSettle: boolean
  canCreateAssets: boolean
  pending: boolean
  onClose: () => void
  onSubmit: (input: PurchaseCreateInput | PurchaseInput) => Promise<void>
}) {
  const [purchaseDate, setPurchaseDate] = useState(record?.purchase_date ?? todayInChina())
  const [operationKey] = useState(() => crypto.randomUUID())
  const [itemName, setItemName] = useState(record?.item_name ?? "")
  const [purchaseChannel, setPurchaseChannel] = useState<ProcurementPurchaseChannel>(
    record?.purchase_channel ?? "offline",
  )
  const [productUrl, setProductUrl] = useState(record?.product_url ?? "")
  const [quantity, setQuantity] = useState(record?.quantity ?? "1")
  const [unitPrice, setUnitPrice] = useState(
    record ? (record.unit_price_cents / 100).toFixed(2) : "",
  )
  const [centerId, setCenterId] = useState(record?.settlement_center_id ?? "")
  const [usesReserveFund, setUsesReserveFund] = useState(record?.uses_reserve_fund ?? false)
  const [note, setNote] = useState(record?.note ?? "")
  const [isReimbursed, setIsReimbursed] = useState(false)
  const [isSettled, setIsSettled] = useState(false)
  const [settledOn, setSettledOn] = useState(todayInChina())
  const [addToAssets, setAddToAssets] = useState(false)
  const [assetCategory, setAssetCategory] = useState("")
  const [assetWarrantyExpiresOn, setAssetWarrantyExpiresOn] = useState("")
  const [error, setError] = useState<string | null>(null)
  const selectableCenters = centers.filter(
    (center) => center.is_active || center.id === record?.settlement_center_id,
  )
  const cents = yuanToCents(unitPrice)
  const calculatedTotal = validQuantity(quantity) && cents !== null
    ? Math.round(Number(quantity) * cents)
    : null
  const selectedCenter = centers.find((center) => center.id === centerId)
  const reserveFundAvailableForPurchase = selectedCenter
    ? selectedCenter.reserve_fund_remaining_cents
      + (record?.uses_reserve_fund && record.settlement_center_id === selectedCenter.id
        ? record.total_price_cents
        : 0)
    : 0
  const reserveFundInsufficient = usesReserveFund
    && calculatedTotal !== null
    && calculatedTotal > reserveFundAvailableForPurchase

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!purchaseDate || !/^\d{4}-\d{2}-\d{2}$/.test(purchaseDate)) {
      setError("请选择有效的采购日期")
      return
    }
    if (!itemName.trim()) {
      setError("请填写物品名称")
      return
    }
    if (!validQuantity(quantity)) {
      setError("数量必须是大于 0 的数字，最多保留 3 位小数")
      return
    }
    if (cents === null) {
      setError("单价必须是大于 0 的金额，最多保留 2 位小数")
      return
    }
    if (!centerId) {
      setError("请选择结算中心")
      return
    }
    if (reserveFundInsufficient) {
      setError("该结算中心备用金余额不足，请先增加备用金或改用其他资金来源")
      return
    }
    if (purchaseChannel === "online" && productUrl.trim() && !validHttpUrl(productUrl.trim())) {
      setError("商品链接必须是以 http:// 或 https:// 开头的有效网址")
      return
    }
    if (!record && isSettled && !settledOn) {
      setError("请选择结算日期")
      return
    }
    if (!record && addToAssets) {
      if (!/^\d+$/.test(quantity.trim()) || Number(quantity) > 100) {
        setError("同步到资产管理时，数量必须是 1 至 100 的整数")
        return
      }
      if (assetWarrantyExpiresOn && assetWarrantyExpiresOn < purchaseDate) {
        setError("保修到期日不能早于采购日期")
        return
      }
    }
    setError(null)
    const base: PurchaseInput = {
      purchase_date: purchaseDate,
      item_name: itemName.trim(),
      purchase_channel: purchaseChannel,
      product_url: purchaseChannel === "online" ? productUrl.trim() || null : null,
      quantity: quantity.trim(),
      unit_price_cents: cents,
      settlement_center_id: centerId,
      uses_reserve_fund: usesReserveFund,
      note: note.trim() || null,
    }
    try {
      await onSubmit(
        record
          ? base
          : {
              ...base,
              operation_key: operationKey,
              is_reimbursed: isReimbursed,
              is_settled: canSettle && isSettled,
              settled_on: canSettle && isSettled ? settledOn : null,
              add_to_assets: canCreateAssets && addToAssets,
              asset_category: canCreateAssets && addToAssets
                ? assetCategory.trim() || null
                : null,
              asset_warranty_expires_on: canCreateAssets && addToAssets
                ? assetWarrantyExpiresOn || null
                : null,
            },
      )
    } catch (caught) {
      setError(errorMessage(caught, "采购记录暂时无法保存"))
    }
  }

  return (
    <ProcurementDialog
      title={record ? "编辑采购记录" : "录入日常采购"}
      description={record ? "已报销、已结算或已开票记录需先撤销对应状态后才能编辑。" : "总价由数量和单价自动计算。"}
      onClose={onClose}
    >
      <form className="procurementForm" onSubmit={(event) => void submit(event)}>
        <div className="procurementFormGrid">
          <label>
            <span>采购日期</span>
            <input
              type="date"
              min="1000-01-01"
              max="9999-12-31"
              value={purchaseDate}
              onChange={(event) => setPurchaseDate(event.target.value)}
              required
            />
          </label>
          <label>
            <span>归属结算中心</span>
            <select
              value={centerId}
              onChange={(event) => {
                setCenterId(event.target.value)
                setUsesReserveFund(false)
              }}
              required
            >
              <option value="">请选择</option>
              {selectableCenters.map((center) => (
                <option key={center.id} value={center.id}>
                  {center.name}{center.is_active ? "" : "（已停用）"}
                </option>
              ))}
            </select>
          </label>
        </div>
        <label>
          <span>物品名称</span>
          <input
            value={itemName}
            onChange={(event) => setItemName(event.target.value)}
            maxLength={120}
            placeholder="例如：饮用水、清洁用品"
            required
          />
        </label>
        <fieldset className="procurementChannelField">
          <legend>采购渠道</legend>
          <div className="procurementSegmented procurementChannelSegmented">
            {(["offline", "online"] as const).map((channel) => (
              <button
                key={channel}
                className={purchaseChannel === channel ? "active" : ""}
                type="button"
                aria-pressed={purchaseChannel === channel}
                onClick={() => {
                  setPurchaseChannel(channel)
                  if (channel === "offline") setProductUrl("")
                }}
              >
                {channel === "online" ? "线上" : "线下"}
              </button>
            ))}
          </div>
        </fieldset>
        {purchaseChannel === "online" ? (
          <label>
            <span>商品链接（选填）</span>
            <input
              type="url"
              inputMode="url"
              value={productUrl}
              onChange={(event) => setProductUrl(event.target.value)}
              maxLength={2048}
              placeholder="https://"
            />
          </label>
        ) : null}
        <div className="procurementFormGrid procurementMoneyGrid">
          <label>
            <span>数量</span>
            <input
              inputMode="decimal"
              value={quantity}
              onChange={(event) => setQuantity(event.target.value)}
              placeholder="1"
              required
            />
          </label>
          <label>
            <span>单价（元）</span>
            <input
              inputMode="decimal"
              value={unitPrice}
              onChange={(event) => setUnitPrice(event.target.value)}
              placeholder="0.00"
              required
            />
          </label>
          <div className="procurementCalculatedTotal">
            <span>自动计算总价</span>
            <output>{calculatedTotal === null ? "待计算" : formatCny(calculatedTotal)}</output>
          </div>
        </div>
        {selectedCenter ? (
          <section className="procurementReserveFundChoice" aria-label="备用金资金来源">
            <label className="procurementCheckLabel">
              <input
                type="checkbox"
                checked={usesReserveFund}
                onChange={(event) => setUsesReserveFund(event.target.checked)}
                disabled={reserveFundAvailableForPurchase <= 0}
              />
              <span>使用该结算中心备用金支付</span>
            </label>
            <div className="procurementReserveFundMetrics">
              <span>累计备用金 <strong>{formatCny(selectedCenter.reserve_fund_total_cents)}</strong></span>
              <span>已使用 <strong>{formatCny(selectedCenter.reserve_fund_used_cents)}</strong></span>
              <span>本单可用 <strong>{formatCny(reserveFundAvailableForPurchase)}</strong></span>
            </div>
            {reserveFundInsufficient ? (
              <p className="formError" role="alert">本次采购金额超过当前可用备用金。</p>
            ) : null}
          </section>
        ) : null}
        {!record && canCreateAssets ? (
          <section className="procurementAssetChoice" aria-label="同步到资产管理">
            <label className="procurementCheckLabel">
              <input
                type="checkbox"
                checked={addToAssets}
                onChange={(event) => setAddToAssets(event.target.checked)}
              />
              <span>同步加入资产管理</span>
            </label>
            <small>
              勾选后按采购数量生成独立资产档案、资产代码和二维码；仅支持 1 至 100 的整数数量。
            </small>
            {addToAssets ? (
              <div className="procurementFormGrid">
                <label>
                  <span>资产分类（选填）</span>
                  <input
                    value={assetCategory}
                    onChange={(event) => setAssetCategory(event.target.value)}
                    maxLength={80}
                    placeholder="例如：办公设备、训练器材"
                  />
                </label>
                <label>
                  <span>保修到期日（选填）</span>
                  <input
                    type="date"
                    min={purchaseDate || "1000-01-01"}
                    max="9999-12-31"
                    value={assetWarrantyExpiresOn}
                    onChange={(event) => setAssetWarrantyExpiresOn(event.target.value)}
                  />
                </label>
              </div>
            ) : null}
          </section>
        ) : null}
        <label>
          <span>备注（选填）</span>
          <textarea
            value={note}
            onChange={(event) => setNote(event.target.value)}
            maxLength={2000}
            placeholder="可记录品牌、用途或其他说明"
          />
        </label>
        {!record ? (
          <div className="procurementSettlementInput">
            <label className="procurementCheckLabel">
              <input
                type="checkbox"
                checked={isReimbursed}
                onChange={(event) => setIsReimbursed(event.target.checked)}
              />
              <span>录入时标记为已报销</span>
            </label>
            {canSettle ? (
              <label className="procurementCheckLabel">
                <input
                  type="checkbox"
                  checked={isSettled}
                  onChange={(event) => setIsSettled(event.target.checked)}
                />
                <span>录入时标记为已结算</span>
              </label>
            ) : null}
            {canSettle && isSettled ? (
              <label className="procurementSettlementDate">
                <span>结算日期</span>
                <input
                  type="date"
                  min="1000-01-01"
                  max="9999-12-31"
                  value={settledOn}
                  onChange={(event) => setSettledOn(event.target.value)}
                  required
                />
              </label>
            ) : null}
            <small className="procurementFinanceHint">
              备用金是本次采购的资金来源；报销与结算仍为独立状态，互不联动。
            </small>
          </div>
        ) : null}
        {selectableCenters.length === 0 ? (
          <p className="formError" role="alert">请先新增并启用一个结算中心。</p>
        ) : null}
        {error ? <p className="formError" role="alert">{error}</p> : null}
        <div className="procurementDialogActions">
          <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>
            取消
          </button>
          <button
            className="primaryButton"
            type="submit"
            disabled={pending || selectableCenters.length === 0 || reserveFundInsufficient}
          >
            {pending ? "正在保存…" : "保存采购记录"}
          </button>
        </div>
      </form>
    </ProcurementDialog>
  )
}

function InvoiceDialog({
  initialRecord,
  settlementCenterId,
  pending,
  onClose,
  onSubmit,
}: {
  initialRecord: PurchaseRecord | null
  settlementCenterId?: string
  pending: boolean
  onClose: () => void
  onSubmit: (input: InvoiceUploadInput) => Promise<void>
}) {
  const initialMatch = initialInvoiceMatch(initialRecord)
  const [file, setFile] = useState<File | null>(null)
  const [amount, setAmount] = useState(
    initialRecord ? (initialRecord.total_price_cents / 100).toFixed(2) : "",
  )
  const [invoiceDate, setInvoiceDate] = useState(todayInChina())
  const [invoiceNumber, setInvoiceNumber] = useState("")
  const [note, setNote] = useState("")
  const [matches, setMatches] = useState<InvoiceMatchSuggestion | null>(initialMatch)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    new Set(initialMatch?.suggested_purchase_ids ?? []),
  )
  const [matching, setMatching] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const amountCents = yuanToCents(amount)
  const selectedTotal = useMemo(
    () => (matches?.candidates ?? []).reduce(
      (total, candidate) => selectedIds.has(candidate.id)
        ? total + candidate.total_price_cents
        : total,
      0,
    ),
    [matches, selectedIds],
  )

  function changeAmount(value: string): void {
    setAmount(value)
    setMatches(null)
    setSelectedIds(new Set())
  }

  async function matchPurchases(): Promise<void> {
    if (amountCents === null) {
      setError("请先填写有效的发票金额")
      return
    }
    setMatching(true)
    setError(null)
    try {
      const result = await findInvoiceMatches(amountCents, settlementCenterId)
      setMatches(result)
      setSelectedIds(new Set(result.suggested_purchase_ids))
      if (result.suggested_purchase_ids.length === 0) {
        setError(
          result.candidates.length === 0
            ? "没有可匹配的未开票采购记录"
            : "未找到金额完全一致的组合，请手动勾选采购记录",
        )
      }
    } catch (caught) {
      setError(errorMessage(caught, "暂时无法自动匹配采购记录"))
    } finally {
      setMatching(false)
    }
  }

  function togglePurchase(purchaseId: string): void {
    setSelectedIds((current) => {
      const next = new Set(current)
      if (next.has(purchaseId)) next.delete(purchaseId)
      else next.add(purchaseId)
      return next
    })
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!file) {
      setError("请选择发票 PDF 或图片文件")
      return
    }
    if (file.size > 20 * 1024 * 1024) {
      setError("发票文件不能超过 20 MB")
      return
    }
    if (amountCents === null) {
      setError("请填写有效的发票金额")
      return
    }
    if (!invoiceDate) {
      setError("请选择发票日期")
      return
    }
    if (selectedIds.size === 0) {
      setError("请至少选择一条采购记录")
      return
    }
    if (selectedTotal !== amountCents) {
      setError("所选采购总额必须与发票金额完全一致")
      return
    }
    setError(null)
    try {
      await onSubmit({
        file,
        amountCents,
        invoiceDate,
        invoiceNumber: invoiceNumber.trim() || undefined,
        note: note.trim() || undefined,
        purchaseIds: [...selectedIds],
      })
    } catch (caught) {
      setError(errorMessage(caught, "发票暂时无法上传"))
    }
  }

  return (
    <ProcurementDialog
      title="上传并匹配发票"
      description="按发票金额寻找尚未开票的采购组合，确认无误后保存。"
      onClose={onClose}
    >
      <form className="procurementForm" onSubmit={(event) => void submit(event)}>
        <div className="procurementFormGrid">
          <label>
            <span>发票文件</span>
            <input
              type="file"
              aria-label="发票文件"
              accept="application/pdf,image/jpeg,image/png,image/webp"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <small>支持 PDF、JPG、PNG、WebP，最大 20 MB</small>
          </label>
          <label>
            <span>发票日期</span>
            <input
              type="date"
              min="1000-01-01"
              max="9999-12-31"
              value={invoiceDate}
              onChange={(event) => setInvoiceDate(event.target.value)}
              required
            />
          </label>
        </div>
        <div className="procurementInvoiceAmountRow">
          <label>
            <span>发票金额（元）</span>
            <input
              inputMode="decimal"
              value={amount}
              onChange={(event) => changeAmount(event.target.value)}
              placeholder="0.00"
              required
            />
          </label>
          <button
            className="secondaryButton"
            type="button"
            disabled={matching || pending}
            onClick={() => void matchPurchases()}
          >
            <Sparkles aria-hidden="true" />{matching ? "正在匹配…" : "自动匹配"}
          </button>
        </div>
        <div className="procurementFormGrid">
          <label>
            <span>发票号码（选填）</span>
            <input
              value={invoiceNumber}
              onChange={(event) => setInvoiceNumber(event.target.value)}
              maxLength={120}
            />
          </label>
          <label>
            <span>备注（选填）</span>
            <input value={note} onChange={(event) => setNote(event.target.value)} maxLength={2000} />
          </label>
        </div>
        {matches ? (
          <section className="procurementInvoiceMatches" aria-label="发票匹配采购记录">
            <header>
              <div>
                <strong>匹配采购</strong>
                <span>已选 {selectedIds.size} 条</span>
              </div>
              <div className={selectedTotal === amountCents ? "matched" : "mismatch"}>
                {formatCny(selectedTotal)} / {amountCents === null ? "待填写" : formatCny(amountCents)}
              </div>
            </header>
            <div className="procurementInvoiceCandidateList">
              {matches.candidates.map((candidate) => (
                <label key={candidate.id}>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(candidate.id)}
                    onChange={() => togglePurchase(candidate.id)}
                  />
                  <span>
                    <strong>{candidate.item_name}</strong>
                    <small>{candidate.purchase_date} · {candidate.settlement_center_name}</small>
                  </span>
                  <b>{formatCny(candidate.total_price_cents)}</b>
                </label>
              ))}
              {matches.candidates.length === 0 ? (
                <p className="procurementEmptyLine">没有符合条件的未开票采购记录。</p>
              ) : null}
            </div>
          </section>
        ) : (
          <p className="procurementDialogNotice">填写金额后点击“自动匹配”选择采购记录。</p>
        )}
        {error ? <p className="formError" role="alert">{error}</p> : null}
        <div className="procurementDialogActions">
          <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>
            取消
          </button>
          <button className="primaryButton" type="submit" disabled={pending || matching}>
            <Upload aria-hidden="true" />{pending ? "正在上传…" : "确认上传发票"}
          </button>
        </div>
      </form>
    </ProcurementDialog>
  )
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

function SettlementDialog({
  record,
  pending,
  onClose,
  onSubmit,
}: {
  record: PurchaseRecord
  pending: boolean
  onClose: () => void
  onSubmit: (isSettled: boolean, settledOn: string | null) => Promise<void>
}) {
  const [settledOn, setSettledOn] = useState(todayInChina())
  const [error, setError] = useState<string | null>(null)
  const nextState = !record.is_settled

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    try {
      await onSubmit(nextState, nextState ? settledOn : null)
    } catch (caught) {
      setError(errorMessage(caught, "结算状态暂时无法更新"))
    }
  }

  return (
    <ProcurementDialog
      title={nextState ? "确认采购结算" : "撤销采购结算"}
      description={`${record.item_name} · ${formatCny(record.total_price_cents)}`}
      onClose={onClose}
    >
      <form className="procurementForm" onSubmit={(event) => void submit(event)}>
        {nextState ? (
          <label>
            <span>结算日期</span>
            <input
              type="date"
              min="1000-01-01"
              max="9999-12-31"
              value={settledOn}
              onChange={(event) => setSettledOn(event.target.value)}
              required
            />
          </label>
        ) : (
          <p className="procurementDialogNotice">
            撤销后该记录恢复为未结算；如已报销或已开票，还需撤销报销或移除发票后才能编辑。
          </p>
        )}
        {error ? <p className="formError" role="alert">{error}</p> : null}
        <div className="procurementDialogActions">
          <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>
            取消
          </button>
          <button className="primaryButton" type="submit" disabled={pending}>
            {pending ? "正在处理…" : nextState ? "确认已结算" : "确认撤销"}
          </button>
        </div>
      </form>
    </ProcurementDialog>
  )
}

function ReimbursementDialog({
  record,
  pending,
  onClose,
  onSubmit,
}: {
  record: PurchaseRecord
  pending: boolean
  onClose: () => void
  onSubmit: (isReimbursed: boolean) => Promise<void>
}) {
  const [error, setError] = useState<string | null>(null)
  const nextState = !record.is_reimbursed

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    try {
      await onSubmit(nextState)
    } catch (caught) {
      setError(errorMessage(caught, "报销状态暂时无法更新"))
    }
  }

  return (
    <ProcurementDialog
      title={nextState ? "确认采购已报销" : "撤销采购报销"}
      description={`${record.item_name} · ${formatCny(record.total_price_cents)}`}
      onClose={onClose}
    >
      <form className="procurementForm" onSubmit={(event) => void submit(event)}>
        <p className="procurementDialogNotice">
          {nextState
            ? "确认后表示公司已将这笔行政垫付款返还，采购信息将锁定，不会改变结算中心的结算状态。"
            : "撤销后该记录恢复为未报销，不会改变结算中心的结算状态；未结算且未开票的记录可重新编辑。"}
        </p>
        {error ? <p className="formError" role="alert">{error}</p> : null}
        <div className="procurementDialogActions">
          <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>
            取消
          </button>
          <button className="primaryButton" type="submit" disabled={pending}>
            {pending ? "正在处理…" : nextState ? "确认已报销" : "确认撤销"}
          </button>
        </div>
      </form>
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
        <PurchaseFormDialog
          key={editing?.id ?? "new-purchase"}
          record={editing}
          centers={centers.data ?? []}
          canSettle={canSettle}
          canCreateAssets={canCreateAssets}
          pending={pending}
          onClose={() => setEditing(undefined)}
          onSubmit={savePurchase}
        />
      ) : null}
      {settling && canSettle ? (
        <SettlementDialog
          record={settling}
          pending={pending}
          onClose={() => setSettling(null)}
          onSubmit={saveSettlement}
        />
      ) : null}
      {reimbursing && canManage ? (
        <ReimbursementDialog
          record={reimbursing}
          pending={pending}
          onClose={() => setReimbursing(null)}
          onSubmit={saveReimbursement}
        />
      ) : null}
      {invoicing !== undefined && canManageInvoices ? (
        <InvoiceDialog
          key={invoicing?.id ?? "new-invoice"}
          initialRecord={invoicing}
          settlementCenterId={filters.settlementCenterId}
          pending={pending}
          onClose={() => setInvoicing(undefined)}
          onSubmit={saveInvoice}
        />
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
