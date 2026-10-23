import { useQuery, useQueryClient } from "@tanstack/react-query"
import { Download, Pencil, Plus, QrCode, RotateCcw, UserRoundPlus } from "lucide-react"
import { Suspense, useMemo, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { dateInputToShanghaiIso, formatDateOnly, todayDateInput } from "../shared/date"
import { listPeople } from "../people/api"
import type { PersonSummary } from "../people/api"
import {
  assignAsset,
  createAsset,
  deleteAssetPhoto,
  exportAssets,
  listAssets,
  returnAsset,
  uploadAssetPhoto,
  updateAsset,
} from "./api"
import type {
  Asset,
  AssetExportInput,
  AssetInput,
  AssetStatus,
  AssetUpdateInput,
} from "./api"
import { assetKeys } from "./keys"
import { createLazyModule } from "../app/loading/lazyModule"
import { ModuleBoundary } from "../app/loading/ModuleBoundary"
import { AssetPhotoLink } from "./AssetPhotoLink"
import { AssetWarrantyDetails } from "./AssetWarrantyDetails"
import { statusLabels } from "./status"
import "./assets.css"

const AssetQrDialog = createLazyModule("asset-qr", async () => ({ default: (await import("./AssetQrDialog")).AssetQrDialog }))

function toIso(value: string): string {
  return dateInputToShanghaiIso(value)
}

function displayTime(value: string | null): string {
  return formatDateOnly(value)
}

function emptyInput(): AssetInput {
  return {
    name: "",
    category: "",
    brand: "",
    model: "",
    serial_number: "",
    location: "",
    purchase_date: null,
    warranty_expires_on: null,
    note: "",
  }
}

type AssetFormSubmission = {
  input: AssetInput | AssetUpdateInput
  photoFile: File | null
  removePhoto: boolean
}

function AssetFormDialog({
  asset,
  pending,
  onClose,
  onSubmit,
}: {
  asset: Asset | null
  pending: boolean
  onClose: () => void
  onSubmit: (submission: AssetFormSubmission) => Promise<void>
}) {
  const [input, setInput] = useState<AssetInput>(() =>
    asset
      ? {
          name: asset.name,
          category: asset.category ?? "",
          brand: asset.brand ?? "",
          model: asset.model ?? "",
          serial_number: asset.serial_number ?? "",
          location: asset.location ?? "",
          purchase_date: asset.purchase_date,
          warranty_expires_on: asset.warranty_expires_on,
          warranty_channel: asset.warranty_channel ?? null,
          warranty_service_location: asset.warranty_service_location ?? null,
          warranty_contact_name: asset.warranty_contact_name ?? null,
          warranty_contact_phone: asset.warranty_contact_phone ?? null,
          warranty_product_url: asset.warranty_product_url ?? null,
          warranty_order_number: asset.warranty_order_number ?? null,
          note: asset.note ?? "",
        }
      : emptyInput(),
  )
  const [status, setStatus] = useState<AssetStatus>(asset?.status ?? "available")
  const [photoFile, setPhotoFile] = useState<File | null>(null)
  const [removePhoto, setRemovePhoto] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function update(field: keyof AssetInput, value: string | null): void {
    setInput((current) => ({ ...current, [field]: value }))
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!input.name.trim()) {
      setError("请填写资产名称")
      return
    }
    if (
      input.purchase_date
      && input.warranty_expires_on
      && input.warranty_expires_on < input.purchase_date
    ) {
      setError("保修到期日不能早于采购日期")
      return
    }
    setError(null)
    try {
      await onSubmit({
        input: asset ? { ...input, status } : input,
        photoFile,
        removePhoto,
      })
    } catch (caught) {
      setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "资产暂时无法保存")
    }
  }

  return (
    <div className="assetDialogBackdrop">
      <section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="asset-form-title">
        <p className="pageEyebrow">ASSET RECORD</p>
        <h2 id="asset-form-title">{asset ? "编辑资产" : "新增资产"}</h2>
        <p>{asset ? `资产代码 ${asset.asset_code} 不会改变。` : "保存后系统会自动生成唯一资产代码，可用于二维码标签。"}</p>
        <form className="assetDialogForm" onSubmit={(event) => void submit(event)}>
          <div className="assetField">
            <label htmlFor="asset-name">资产名称</label>
            <input id="asset-name" value={input.name} onChange={(event) => update("name", event.target.value)} maxLength={120} required />
          </div>
          <div className="assetField">
            <label htmlFor="asset-category">资产分类</label>
            <input id="asset-category" value={input.category ?? ""} onChange={(event) => update("category", event.target.value)} placeholder="例如：训练设备、办公设备" maxLength={80} />
          </div>
          <div className="assetFilters">
            <div className="assetField">
              <label htmlFor="asset-brand">品牌</label>
              <input id="asset-brand" value={input.brand ?? ""} onChange={(event) => update("brand", event.target.value)} maxLength={80} />
            </div>
            <div className="assetField">
              <label htmlFor="asset-model">型号</label>
              <input id="asset-model" value={input.model ?? ""} onChange={(event) => update("model", event.target.value)} maxLength={120} />
            </div>
            <div className="assetField">
              <label htmlFor="asset-serial">序列号</label>
              <input id="asset-serial" value={input.serial_number ?? ""} onChange={(event) => update("serial_number", event.target.value)} maxLength={120} />
            </div>
            {asset ? (
              <div className="assetField">
                <label htmlFor="asset-status">状态</label>
                <select id="asset-status" value={status} onChange={(event) => setStatus(event.target.value as AssetStatus)}>
                  {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                </select>
              </div>
            ) : null}
          </div>
          <div className="assetField">
            <label htmlFor="asset-location">存放位置</label>
            <input id="asset-location" value={input.location ?? ""} onChange={(event) => update("location", event.target.value)} maxLength={160} placeholder="例如：一号楼 201 室" />
          </div>
          <div className="assetFilters">
            <div className="assetField">
              <label htmlFor="asset-purchase-date">采购日期（选填）</label>
              <input
                id="asset-purchase-date"
                type="date"
                min="1000-01-01"
                max="9999-12-31"
                value={input.purchase_date ?? ""}
                onChange={(event) => update("purchase_date", event.target.value || null)}
              />
            </div>
            <div className="assetField">
              <label htmlFor="asset-warranty-date">保修到期日（选填）</label>
              <input
                id="asset-warranty-date"
                type="date"
                min={input.purchase_date || "1000-01-01"}
                max="9999-12-31"
                value={input.warranty_expires_on ?? ""}
                onChange={(event) => update("warranty_expires_on", event.target.value || null)}
              />
            </div>
          </div>
          <fieldset className="assetWarrantyFields">
            <legend>保修与售后（选填）</legend>
            <div className="assetField">
              <label htmlFor="asset-warranty-channel">保修方式</label>
              <select id="asset-warranty-channel" value={input.warranty_channel ?? ""} onChange={(event) => {
                setInput((current) => ({
                  ...current,
                  warranty_channel: event.target.value as "online" | "offline" || null,
                  warranty_service_location: null,
                  warranty_contact_name: null,
                  warranty_contact_phone: null,
                  warranty_product_url: null,
                  warranty_order_number: null,
                }))
              }}>
                <option value="">暂未填写</option>
                <option value="offline">线下售后</option>
                <option value="online">线上售后</option>
              </select>
            </div>
            {input.warranty_channel === "offline" ? <>
              <div className="assetField">
                <label htmlFor="asset-warranty-location">售后地点</label>
                <input id="asset-warranty-location" value={input.warranty_service_location ?? ""} maxLength={200}
                  placeholder="门店、维修点名称或地址" onChange={(event) => update("warranty_service_location", event.target.value)} />
              </div>
              <div className="assetField">
                <label htmlFor="asset-warranty-contact">售后联系人</label>
                <input id="asset-warranty-contact" value={input.warranty_contact_name ?? ""} maxLength={80}
                  onChange={(event) => update("warranty_contact_name", event.target.value)} />
              </div>
              <div className="assetField">
                <label htmlFor="asset-warranty-phone">售后电话</label>
                <input id="asset-warranty-phone" type="tel" value={input.warranty_contact_phone ?? ""} maxLength={40}
                  placeholder="手机号、固定电话或客服电话" onChange={(event) => update("warranty_contact_phone", event.target.value)} />
              </div>
            </> : null}
            {input.warranty_channel === "online" ? <>
              <div className="assetField">
                <label htmlFor="asset-warranty-url">产品链接</label>
                <input id="asset-warranty-url" type="url" pattern="https?://.+" value={input.warranty_product_url ?? ""} maxLength={2000}
                  placeholder="https://" onChange={(event) => update("warranty_product_url", event.target.value)} />
              </div>
              <div className="assetField">
                <label htmlFor="asset-warranty-order">订单号</label>
                <input id="asset-warranty-order" value={input.warranty_order_number ?? ""} maxLength={120}
                  onChange={(event) => update("warranty_order_number", event.target.value)} />
              </div>
            </> : null}
            <small>过保后仍保留售后信息，便于联系维修。</small>
          </fieldset>
          <div className="assetField">
            <label htmlFor="asset-note">备注</label>
            <textarea id="asset-note" value={input.note ?? ""} onChange={(event) => update("note", event.target.value)} maxLength={2000} />
          </div>
          <div className="assetField">
            <label htmlFor="asset-photo">辨识照片（可选）</label>
            <input
              id="asset-photo"
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(event) => {
                setPhotoFile(event.target.files?.[0] ?? null)
                setRemovePhoto(false)
              }}
            />
            <small>支持 JPG、PNG、WebP，单张不超过 10 MB。</small>
            {asset?.photo ? (
              <div className="assetPhotoControls">
                <AssetPhotoLink assetId={asset.id} photo={asset.photo} />
                <label className="assetCheckboxLabel">
                  <input
                    type="checkbox"
                    checked={removePhoto}
                    disabled={photoFile !== null}
                    onChange={(event) => setRemovePhoto(event.target.checked)}
                  />
                  移除当前照片
                </label>
              </div>
            ) : null}
          </div>
          {error ? <p className="formError" role="alert">{error}</p> : null}
          <div className="assetDialogActions">
            <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button>
            <button className="primaryButton" type="submit" disabled={pending}>{pending ? "正在保存…" : "保存资产"}</button>
          </div>
        </form>
      </section>
    </div>
  )
}

function AssetAssignDialog({
  asset,
  pending,
  onClose,
  onSubmit,
}: {
  asset: Asset
  pending: boolean
  onClose: () => void
  onSubmit: (input: { person_id: string; assigned_at: string; expected_return_at: string | null; condition_on_assign: string; note: string }) => Promise<void>
}) {
  const [search, setSearch] = useState("")
  const [searched, setSearched] = useState("")
  const [selected, setSelected] = useState<PersonSummary | null>(null)
  const [assignedAt, setAssignedAt] = useState(todayDateInput)
  const [expectedReturnAt, setExpectedReturnAt] = useState("")
  const [condition, setCondition] = useState("")
  const [note, setNote] = useState("")
  const [error, setError] = useState<string | null>(null)
  const candidates = useQuery({
    queryKey: ["asset-person-candidates", searched],
    queryFn: () => listPeople({ name: searched, page: 1, pageSize: 20 }),
    enabled: searched.length >= 1,
  })

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!selected) {
      setError("请先搜索并选择领用人员")
      return
    }
    if (!assignedAt) {
      setError("请选择领用时间")
      return
    }
    setError(null)
    try {
      await onSubmit({
        person_id: selected.id,
        assigned_at: toIso(assignedAt),
        expected_return_at: expectedReturnAt ? toIso(expectedReturnAt) : null,
        condition_on_assign: condition,
        note,
      })
    } catch (caught) {
      setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "资产暂时无法分配")
    }
  }

  return (
    <div className="assetDialogBackdrop">
      <section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="asset-assign-title">
        <p className="pageEyebrow">ASSET ASSIGNMENT</p>
        <h2 id="asset-assign-title">安排资产领用</h2>
        <p>{asset.name} · {asset.asset_code}</p>
        <form className="assetDialogForm" onSubmit={submit}>
          <div className="assetField">
            <label htmlFor="asset-person-search">搜索人员</label>
            <div className="assetFiltersActions">
              <input id="asset-person-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="输入姓名" minLength={1} />
              <button className="secondaryButton" type="button" onClick={() => { setSearched(search.trim()); setSelected(null) }}>查找</button>
            </div>
          </div>
          {candidates.data ? (
            <div className="assetPeopleResults" aria-label="人员搜索结果">
              {candidates.data.items.map((candidate) => (
                <label key={candidate.id}>
                  <input type="radio" name="asset-person" checked={selected?.id === candidate.id} onChange={() => setSelected(candidate)} />
                  <span><strong>{candidate.full_name}</strong><small>{candidate.person_types.join("、")} · {candidate.id}</small></span>
                </label>
              ))}
              {candidates.data.items.length === 0 ? <small>没有匹配人员。</small> : null}
            </div>
          ) : null}
          {selected ? <p className="subtleStatus">已选择：{selected.full_name}</p> : null}
          <div className="assetFilters">
            <div className="assetField"><label htmlFor="asset-assigned-at">领用日期</label><input id="asset-assigned-at" type="date" value={assignedAt} onChange={(event) => setAssignedAt(event.target.value)} required /></div>
            <div className="assetField"><label htmlFor="asset-return-at">预计归还日期</label><input id="asset-return-at" type="date" value={expectedReturnAt} onChange={(event) => setExpectedReturnAt(event.target.value)} /></div>
          </div>
          <div className="assetField"><label htmlFor="asset-condition">领用时状态</label><input id="asset-condition" value={condition} onChange={(event) => setCondition(event.target.value)} maxLength={500} placeholder="例如：外观完好" /></div>
          <div className="assetField"><label htmlFor="asset-assignment-note">备注</label><textarea id="asset-assignment-note" value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} /></div>
          {error ? <p className="formError" role="alert">{error}</p> : null}
          <div className="assetDialogActions">
            <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button>
            <button className="primaryButton" type="submit" disabled={pending}>{pending ? "正在分配…" : "确认分配"}</button>
          </div>
        </form>
      </section>
    </div>
  )
}

function AssetReturnDialog({ asset, pending, onClose, onSubmit }: { asset: Asset; pending: boolean; onClose: () => void; onSubmit: (input: { returned_at: string; condition_on_return: string; note: string }) => Promise<void> }) {
  const [returnedAt, setReturnedAt] = useState(todayDateInput)
  const [condition, setCondition] = useState("")
  const [note, setNote] = useState("")
  const [error, setError] = useState<string | null>(null)
  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    setError(null)
    try { await onSubmit({ returned_at: toIso(returnedAt), condition_on_return: condition, note }) }
    catch (caught) { setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "资产暂时无法归还") }
  }
  return (
    <div className="assetDialogBackdrop">
      <section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="asset-return-title">
        <p className="pageEyebrow">ASSET RETURN</p><h2 id="asset-return-title">确认归还资产</h2>
        <p>{asset.name} · 当前使用人：{asset.current_assignment?.person_name ?? "未知"}</p>
        <form className="assetDialogForm" onSubmit={(event) => void submit(event)}>
          <div className="assetField"><label htmlFor="asset-returned-at">归还日期</label><input id="asset-returned-at" type="date" value={returnedAt} onChange={(event) => setReturnedAt(event.target.value)} required /></div>
          <div className="assetField"><label htmlFor="asset-return-condition">归还时状态</label><input id="asset-return-condition" value={condition} onChange={(event) => setCondition(event.target.value)} maxLength={500} placeholder="例如：屏幕有轻微划痕" /></div>
          <div className="assetField"><label htmlFor="asset-return-note">备注</label><textarea id="asset-return-note" value={note} onChange={(event) => setNote(event.target.value)} maxLength={500} /></div>
          {error ? <p className="formError" role="alert">{error}</p> : null}
          <div className="assetDialogActions"><button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button><button className="primaryButton" type="submit" disabled={pending}>{pending ? "正在归还…" : "确认归还"}</button></div>
        </form>
      </section>
    </div>
  )
}

function AssetExportDialog({
  filters,
  pending,
  onClose,
  onSubmit,
}: {
  filters: { query: string; status: AssetStatus | ""; category: string }
  pending: boolean
  onClose: () => void
  onSubmit: (input: AssetExportInput) => Promise<void>
}) {
  const [purpose, setPurpose] = useState("")
  const [format, setFormat] = useState<AssetExportInput["format"]>("xlsx")
  const [error, setError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const normalized = purpose.trim()
    if (normalized.length < 4) {
      setError("请填写至少 4 个字的导出用途")
      return
    }
    setError(null)
    try {
      await onSubmit({ ...filters, purpose: normalized, format })
    } catch (caught) {
      setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "导出暂时失败")
    }
  }

  return (
    <div className="assetDialogBackdrop">
      <section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="asset-export-title">
        <p className="pageEyebrow">ASSET EXPORT</p>
        <h2 id="asset-export-title">导出资产台账</h2>
        <p>当前筛选条件下共 {filters.status || filters.category || filters.query ? "匹配的" : "全部"} 资产；导出操作会记录到安全审计。</p>
        <form className="assetDialogForm" onSubmit={(event) => void submit(event)}>
          <div className="assetField">
            <label htmlFor="asset-export-purpose">导出用途</label>
            <input id="asset-export-purpose" value={purpose} onChange={(event) => setPurpose(event.target.value)} minLength={4} maxLength={200} placeholder="例如：年度资产盘点" required />
          </div>
          <div className="assetField">
            <label htmlFor="asset-export-format">文件格式</label>
            <select id="asset-export-format" value={format} onChange={(event) => setFormat(event.target.value as AssetExportInput["format"])}>
              <option value="xlsx">Excel（XLSX）</option>
              <option value="csv">CSV</option>
            </select>
          </div>
          {error ? <p className="formError" role="alert">{error}</p> : null}
          <div className="assetDialogActions">
            <button className="secondaryButton" type="button" onClick={onClose} disabled={pending}>取消</button>
            <button className="primaryButton" type="submit" disabled={pending}>{pending ? "正在导出…" : "开始导出"}</button>
          </div>
        </form>
      </section>
    </div>
  )
}

export function AssetsPage({
  canManage = true,
  canAssign = true,
  canExport = true,
}: {
  canManage?: boolean
  canAssign?: boolean
  canExport?: boolean
}) {
  const queryClient = useQueryClient()
  const [query, setQuery] = useState("")
  const [status, setStatus] = useState<AssetStatus | "">("")
  const [category, setCategory] = useState("")
  const [filters, setFilters] = useState({ query: "", status: "" as AssetStatus | "", category: "" })
  const [editing, setEditing] = useState<Asset | null | undefined>(undefined)
  const [assigning, setAssigning] = useState<Asset | null>(null)
  const [returning, setReturning] = useState<Asset | null>(null)
  const [qrAsset, setQrAsset] = useState<Asset | null>(null)
  const [exportOpen, setExportOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)
  const assets = useQuery({ queryKey: assetKeys.list(filters), queryFn: () => listAssets(filters) })
  const counts = useMemo(() => {
    const items = assets.data?.items ?? []
    return {
      total: assets.data?.total ?? 0,
      available: items.filter((item) => item.status === "available").length,
      assigned: items.filter((item) => item.status === "assigned").length,
      attention: items.filter((item) => item.status === "maintenance" || item.status === "retired").length,
    }
  }, [assets.data])

  async function refresh(): Promise<void> { await queryClient.invalidateQueries({ queryKey: assetKeys.root }) }

  function search(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setFilters({ query: query.trim(), status, category: category.trim() })
  }

  async function save({ input, photoFile, removePhoto }: AssetFormSubmission): Promise<void> {
    setPending(true); setPageError(null)
    try {
      let saved = editing
        ? await updateAsset(editing.id, input as AssetUpdateInput)
        : await createAsset(input as AssetInput)
      if (photoFile) saved = await uploadAssetPhoto(saved.id, photoFile)
      else if (removePhoto && saved.photo) saved = await deleteAssetPhoto(saved.id)
      setEditing(undefined); await refresh()
    } catch (caught) {
      setPageError(caught instanceof ProblemDetailsError ? caught.problem.detail : "资产暂时无法保存")
      throw caught
    } finally { setPending(false) }
  }

  async function mutate(action: () => Promise<unknown>): Promise<void> {
    setPending(true); setPageError(null)
    try { await action(); await refresh(); setAssigning(null); setReturning(null) }
    catch (caught) { setPageError(caught instanceof ProblemDetailsError ? caught.problem.detail : "资产操作失败") }
    finally { setPending(false) }
  }

  async function downloadExport(input: AssetExportInput): Promise<void> {
    setPending(true); setPageError(null)
    try {
      const result = await exportAssets(input)
      const url = URL.createObjectURL(result.blob)
      const anchor = document.createElement("a")
      anchor.href = url
      anchor.download = result.filename
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
      setExportOpen(false)
    } catch (caught) {
      setPageError(caught instanceof ProblemDetailsError ? caught.problem.detail : "资产导出失败")
      throw caught
    } finally {
      setPending(false)
    }
  }

  return (
    <div className="assetsPage">
      <header className="assetsPageHeader">
        <div><p className="pageEyebrow">ASSET INVENTORY</p><h1>资产管理</h1><p>统一登记、分配和追踪训练设备、办公设备及其他固定资产。</p></div>
        <div className="assetHeaderActions">
          {canExport ? <button className="secondaryButton" type="button" onClick={() => { setPageError(null); setExportOpen(true) }}><Download aria-hidden="true" />导出</button> : null}
          {canManage ? <button className="primaryButton" type="button" onClick={() => { setPageError(null); setEditing(null) }}><Plus aria-hidden="true" />新增资产</button> : null}
        </div>
      </header>
      <section className="assetStats" aria-label="资产数量统计">
        <div className="assetStat"><span>资产总数</span><strong>{counts.total}</strong></div>
        <div className="assetStat"><span>当前可用</span><strong>{counts.available}</strong></div>
        <div className="assetStat"><span>使用中</span><strong>{counts.assigned}</strong></div>
        <div className="assetStat"><span>维修/停用</span><strong>{counts.attention}</strong></div>
      </section>
      <form className="assetFilters" onSubmit={search}>
        <div className="assetField"><label htmlFor="asset-query">搜索资产</label><input id="asset-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="名称、资产代码、序列号" /></div>
        <div className="assetField"><label htmlFor="asset-status-filter">状态</label><select id="asset-status-filter" value={status} onChange={(event) => setStatus(event.target.value as AssetStatus | "")}><option value="">全部状态</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
        <div className="assetField"><label htmlFor="asset-category-filter">分类</label><input id="asset-category-filter" value={category} onChange={(event) => setCategory(event.target.value)} placeholder="例如：训练设备" /></div>
        <div className="assetFiltersActions"><button className="primaryButton" type="submit">查询</button><button className="secondaryButton" type="button" onClick={() => { setQuery(""); setStatus(""); setCategory(""); setFilters({ query: "", status: "", category: "" }) }}>清空</button></div>
      </form>
      {pageError ? <p className="formError" role="alert">{pageError}</p> : null}
      <section className="assetDirectory" aria-labelledby="asset-directory-title">
        <header className="assetDirectoryHeader"><h2 id="asset-directory-title">资产台账</h2><span>{assets.data ? `共 ${assets.data.total} 项` : "正在统计"}</span></header>
        {assets.isPending ? <div className="assetState">正在读取资产台账…</div> : null}
        {assets.isError ? <div className="assetState" role="alert">暂时无法读取资产台账，请稍后重试。</div> : null}
        {assets.data && assets.data.items.length === 0 ? <div className="assetState">暂无资产记录，先新增一项资产。</div> : null}
        {assets.data && assets.data.items.length > 0 ? <>
          <div className="assetTableHeader"><span>资产</span><span>分类</span><span>使用人</span><span>状态</span><span>操作</span></div>
          {assets.data.items.map((asset) => <div className="assetTableRow" key={asset.id}>
            <div><strong>{asset.name}</strong><small className="assetCode">{asset.asset_code}</small>{asset.photo ? <AssetPhotoLink assetId={asset.id} photo={asset.photo} /> : null}</div>
            <div>
              <span>{asset.category ?? "未分类"}</span>
              <small>{asset.brand || asset.model ? [asset.brand, asset.model].filter(Boolean).join(" · ") : "未填写品牌型号"}</small>
              <small>采购 {asset.purchase_date ?? "未填写"} · 保修至 {asset.warranty_expires_on ?? "未填写"}</small>
              <AssetWarrantyDetails asset={asset} />
            </div>
            <div><span>{asset.current_assignment?.person_name ?? "未分配"}</span><small>{asset.current_assignment ? displayTime(asset.current_assignment.assigned_at) : asset.location ?? ""}</small></div>
            <div><span className={`assetStatus ${asset.status}`}>{statusLabels[asset.status]}</span></div>
            <div className="assetRowActions">
              {canAssign && asset.status === "available" ? <button className="assetActionButton" type="button" onClick={() => setAssigning(asset)} title="分配资产"><UserRoundPlus aria-hidden="true" />分配</button> : null}
              {canAssign && asset.status === "assigned" ? <button className="assetActionButton" type="button" onClick={() => setReturning(asset)} title="归还资产"><RotateCcw aria-hidden="true" />归还</button> : null}
              {canManage ? <button className="assetActionButton" type="button" onClick={() => setEditing(asset)} title="编辑资产"><Pencil aria-hidden="true" />编辑</button> : null}
              <button className="assetActionButton" type="button" onClick={() => setQrAsset(asset)} title="查看并打印二维码"><QrCode aria-hidden="true" />二维码</button>
            </div>
          </div>)}</> : null}
      </section>
      {editing !== undefined ? <AssetFormDialog key={editing?.id ?? "new-asset"} asset={editing} pending={pending} onClose={() => setEditing(undefined)} onSubmit={save} /> : null}
      {assigning ? <AssetAssignDialog asset={assigning} pending={pending} onClose={() => setAssigning(null)} onSubmit={(input) => mutate(() => assignAsset(assigning.id, input))} /> : null}
      {returning ? <AssetReturnDialog asset={returning} pending={pending} onClose={() => setReturning(null)} onSubmit={(input) => mutate(() => returnAsset(returning.id, input))} /> : null}
      {qrAsset ? <ModuleBoundary moduleId="asset-qr" retry={AssetQrDialog.retry}><Suspense fallback={<p role="status">正在加载二维码…</p>}><AssetQrDialog.Component asset={qrAsset} onClose={() => setQrAsset(null)} /></Suspense></ModuleBoundary> : null}
      {exportOpen && canExport ? <AssetExportDialog filters={filters} pending={pending} onClose={() => setExportOpen(false)} onSubmit={downloadExport} /> : null}
    </div>
  )
}
