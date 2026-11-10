import { useEffect, useRef, useState } from "react"
import type { FormEvent } from "react"
import { ProblemDetailsError } from "../api/client"
import { AssetCategorySelect } from "./AssetCategorySelect"
import { AssetHardwareFields, hardwareComplete, isComputer, normalizedHardware } from "./AssetHardwareFields"
import { createAssetBatch } from "./api"
import type { AssetBatchInput, AssetInput } from "./api"
import { validRentalDate } from "./rentalDates"

export function AssetBatchCreateDialog({ accountId, categoryAuthorized, onClose, onCreated }: {
  accountId: string | null
  categoryAuthorized: boolean
  onClose: () => void
  onCreated: () => Promise<void>
}) {
  const [operationKey] = useState(() => crypto.randomUUID())
  const [quantity, setQuantity] = useState("1")
  const [input, setInput] = useState<AssetInput>({ name: "", ownership_type: "rented", category: "", rental_started_on: "", rental_expires_on: "" })
  const [request, setRequest] = useState<AssetBatchInput | null>(null)
  const [pending, setPending] = useState(false)
  const inFlight = useRef(false)
  const [total, setTotal] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [closeWarning, setCloseWarning] = useState(false)
  const unresolved = request !== null && total === null
  useEffect(() => {
    if (!unresolved) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = "" }
    window.addEventListener("beforeunload", warn)
    return () => window.removeEventListener("beforeunload", warn)
  }, [unresolved])

  function update(field: keyof AssetInput, value: string): void {
    setInput((current) => ({ ...current, [field]: value }))
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (inFlight.current || total !== null) return
    let payload = request
    if (!payload) {
      if (!/^\d+$/.test(quantity) || Number(quantity) < 1 || Number(quantity) > 100) { setError("数量必须是 1 至 100 的整数"); return }
      if (!input.name.trim()) { setError("请填写资产名称"); return }
      if (!validRentalDate(input.rental_started_on ?? "") || !validRentalDate(input.rental_expires_on ?? "") || input.rental_started_on! > input.rental_expires_on!) { setError("请填写有效租期，开始日期不能晚于到期日期"); return }
      if (isComputer(input.category) && !hardwareComplete(input)) { setError("请补齐电脑的 CPU、内存、GPU 和硬盘配置"); return }
      // Explicitly build the common asset: serial numbers and photos never enter this request.
      payload = { operation_key: operationKey, quantity: Number(quantity), asset: {
        name: input.name.trim(), category: input.category || null,
        brand: input.brand?.trim() || null, model: input.model?.trim() || null,
        location: input.location?.trim() || null, note: input.note?.trim() || null,
        ownership_type: "rented", rental_started_on: input.rental_started_on, rental_expires_on: input.rental_expires_on,
        ...(isComputer(input.category) ? normalizedHardware(input) : {}),
      } }
      setRequest(payload)
    }
    inFlight.current = true
    setPending(true); setError(null)
    try {
      const result = await createAssetBatch(payload)
      setTotal(result.total)
      try { await onCreated() } catch { setError("批次已创建，但列表刷新失败。请关闭后刷新页面核对，勿重复新增。") }
    } catch (caught) {
      const detail = caught instanceof ProblemDetailsError ? caught.problem.detail : "网络暂时不可用"
      setError(`结果尚未确认：${detail}。参数已锁定，请重试同一批次。若提示冲突，请先核对现有批次，勿重新新增。`)
    } finally { inFlight.current = false; setPending(false) }
  }

  return <div className="assetDialogBackdrop"><section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="asset-batch-title">
    <p className="pageEyebrow">RENTAL BATCH</p><h2 id="asset-batch-title">批量新增租用资产</h2>
    <p>每台生成独立资产代码和二维码，初始为可用、未分配。序列号和照片不批量复制，请创建后逐台编辑补充。</p>
    <form className="assetDialogForm" onSubmit={(event) => void submit(event)}>
      <fieldset className="assetBatchFields" disabled={request !== null || pending}>
        <div className="assetField"><label htmlFor="batch-quantity">批量数量</label><input id="batch-quantity" type="number" min={1} max={100} step={1} required value={quantity} onChange={(event) => setQuantity(event.target.value)} /></div>
        <div className="assetField"><label htmlFor="batch-name">资产名称</label><input id="batch-name" maxLength={120} required value={input.name} onChange={(event) => update("name", event.target.value)} /></div>
        <div className="assetField"><label htmlFor="batch-category">资产分类</label><AssetCategorySelect id="batch-category" mode="create" accountId={accountId} authorized={categoryAuthorized} value={input.category ?? ""} onChange={(value) => update("category", value)} /></div>
        <div className="assetField"><label htmlFor="batch-brand">品牌</label><input id="batch-brand" maxLength={80} value={input.brand ?? ""} onChange={(event) => update("brand", event.target.value)} /></div>
        <div className="assetField"><label htmlFor="batch-model">型号</label><input id="batch-model" maxLength={120} value={input.model ?? ""} onChange={(event) => update("model", event.target.value)} /></div>
        <div className="assetField"><label htmlFor="batch-location">存放位置</label><input id="batch-location" maxLength={160} value={input.location ?? ""} onChange={(event) => update("location", event.target.value)} /></div>
        <div className="assetField"><label htmlFor="batch-start">租用开始日期</label><input id="batch-start" type="date" min="0001-01-01" max="9999-12-31" required value={input.rental_started_on ?? ""} onChange={(event) => update("rental_started_on", event.target.value)} /></div>
        <div className="assetField"><label htmlFor="batch-expiry">租用到期日期</label><input id="batch-expiry" type="date" min={input.rental_started_on || "0001-01-01"} max="9999-12-31" required value={input.rental_expires_on ?? ""} onChange={(event) => update("rental_expires_on", event.target.value)} /></div>
        <AssetHardwareFields idPrefix="batch" value={input} category={input.category} onChange={(hardware) => setInput((current) => ({ ...current, ...hardware }))} />
        <div className="assetField"><label htmlFor="batch-note">备注</label><textarea id="batch-note" maxLength={2000} value={input.note ?? ""} onChange={(event) => update("note", event.target.value)} /></div>
      </fieldset>
      {request ? <small className="assetOperationKey">批次操作键：{operationKey}</small> : null}
      {error ? <p role="alert" className="formError">{error}</p> : null}
      {total !== null ? <p role="status">已创建 {total} 台独立租用资产，请在台账逐台补充序列号和照片。</p> : null}
      {closeWarning ? <div role="alert" className="rentalWarning"><p>本批次结果尚未确认。关闭后请先核对台账和批次操作键，确认未创建前不要发起新批次，以免重复资产。</p><button className="secondaryButton" type="button" onClick={() => setCloseWarning(false)}>保留并继续核对</button><button className="secondaryButton" type="button" onClick={onClose}>已了解，关闭批次</button></div> : null}
      <div className="assetDialogActions"><button className="secondaryButton" type="button" disabled={pending} onClick={() => unresolved ? setCloseWarning(true) : onClose()}>关闭</button>
        {total === null ? <button className="primaryButton" type="submit" disabled={pending}>{pending ? "正在创建…" : request ? "重试同一批次" : "创建租用资产"}</button> : null}
      </div>
    </form>
  </section></div>
}
