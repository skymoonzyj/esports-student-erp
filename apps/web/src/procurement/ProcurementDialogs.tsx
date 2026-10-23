import { useMemo, useState, type FormEvent } from "react"
import { Sparkles, Upload } from "lucide-react"
import { formatCny, todayInChina } from "../revenueShare/format"
import { findInvoiceMatches } from "./api"
import type { InvoiceMatchSuggestion, InvoiceUploadInput, ProcurementPurchaseChannel, PurchaseCreateInput, PurchaseInput, PurchaseRecord, SettlementCenter } from "./api"
import { ProcurementDialog, errorMessage, yuanToCents } from "./ProcurementDialogBase"
import "./procurement-dialogs.css"

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

export function PurchaseFormDialog({
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

export function InvoiceDialog({
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

export function SettlementDialog({
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

export function ReimbursementDialog({
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
