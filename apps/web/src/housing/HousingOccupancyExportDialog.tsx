import { useState } from "react"
import type { FormEvent, RefObject } from "react"

import { ProblemDetailsError } from "../api/client"
import type {
  HousingFiltersValue,
  HousingCatalog,
  HousingOccupancyExportInput,
} from "./api"
import { CLEANING_FREQUENCY_OPTIONS } from "./api"
import { HousingDialogShell } from "./HousingDialogShell"
import { HousingQuickFilterSelect } from "./HousingQuickFilterSelect"

type HousingOccupancyExportDialogProps = {
  filters: HousingFiltersValue
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: HousingOccupancyExportInput) => Promise<void>
  embedded?: boolean
  catalog?: HousingCatalog
}

function exportInput(filters: HousingFiltersValue, purpose: string, format: "xlsx" | "csv"):
  HousingOccupancyExportInput {
  return {
    format,
    purpose,
    building_id: filters.buildingId,
    floor_id: filters.floorId,
    query: filters.query,
    room_types: filters.roomTypes,
    verification_statuses: filters.verificationStatuses,
    service_statuses: filters.serviceStatuses,
    current_gender: filters.currentGender,
    occupancy: filters.occupancy,
    assignability: filters.assignability,
    quick_filter: filters.quickFilter,
    cleaning_statuses: filters.cleaningStatuses,
    daily_cleaning_enabled: filters.dailyCleaningEnabled,
    cleaning_frequencies: filters.cleaningFrequencies,
  }
}

export function HousingOccupancyExportDialog({
  filters,
  pending,
  error,
  focusFallbackRef,
  onClose,
  onSubmit,
  embedded,
  catalog,
}: HousingOccupancyExportDialogProps) {
  const [draft, setDraft] = useState<HousingFiltersValue>(() => ({ ...filters, roomTypes: [...(filters.roomTypes ?? [])], verificationStatuses: [...(filters.verificationStatuses ?? [])], serviceStatuses: [...(filters.serviceStatuses ?? [])], cleaningStatuses: [...(filters.cleaningStatuses ?? [])], cleaningFrequencies: [...(filters.cleaningFrequencies ?? [])] }))
  const [purpose, setPurpose] = useState("")
  const [format, setFormat] = useState<"xlsx" | "csv">("xlsx")
  const [formError, setFormError] = useState<string | null>(null)

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    const normalized = purpose.trim()
    if (normalized.length < 2 || normalized.length > 200) {
      setFormError("请填写 2–200 个字的导出用途")
      return
    }
    setFormError(null)
    try {
      await onSubmit(exportInput(draft, normalized, format))
    } catch (caught) {
      setFormError(
        caught instanceof ProblemDetailsError ? caught.problem.detail : "房态导出暂时失败",
      )
    }
  }

  const form = (
      <form className="housingReportDialogForm" onSubmit={(event) => void submit(event)}>
        <p>入住记录（含历史）：含有效当前及历史入住，不含空房。筛选按当前房间状态，不代表历史时点状态。</p>
        {catalog ? <>
          <label>历史导出楼栋<select aria-label="历史导出楼栋" value={draft.buildingId ?? ""} disabled={pending} onChange={(event) => setDraft({ ...draft, buildingId: event.target.value || undefined, floorId: undefined })}><option value="">全部</option>{catalog.buildings.map((building) => <option key={building.id} value={building.id}>{building.name}</option>)}</select></label>
          <label>历史导出楼层<select aria-label="历史导出楼层" value={draft.floorId ?? ""} disabled={pending} onChange={(event) => setDraft({ ...draft, floorId: event.target.value || undefined })}><option value="">全部</option>{catalog.floors.filter((floor) => !draft.buildingId || floor.building_id === draft.buildingId).map((floor) => <option key={floor.id} value={floor.id}>{floor.building_name} · {floor.name}</option>)}</select></label>
        </> : null}
        <label>历史导出房号搜索<input aria-label="历史导出房号搜索" value={draft.query ?? ""} disabled={pending} onChange={(event) => setDraft({ ...draft, query: event.target.value })} /></label>
        <HousingQuickFilterSelect label="历史导出快捷筛选" value={draft.quickFilter} pending={pending} onChange={(quickFilter) => setDraft({ ...draft, quickFilter })} />
        {([
          ["occupancy", "历史导出入住状态", [["occupied", "已入住"], ["unoccupied", "未入住"]]],
          ["assignability", "历史导出可入住性", [["assignable", "可入住"], ["full", "满房"], ["unassignable", "不可安排（含核对、保洁等限制）"]]],
          ["currentGender", "历史导出入住性别", [["male", "男住"], ["female", "女住"], ["unspecified", "未指定"]]],
        ] as const).map(([key, label, options]) => <label key={key}>{label}<select aria-label={label} value={draft[key] ?? ""} disabled={pending} onChange={(event) => setDraft({ ...draft, [key]: event.target.value || undefined })}><option value="">全部</option>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>)}
        {([
          ["roomTypes", "历史导出房型", [["single", "单人间"], ["double", "双人间"], ["triple", "三人间"], ["custom", "自定义"], ["functional", "功能间"]]],
          ["verificationStatuses", "历史导出核对状态", [["verified", "已核对"], ["pending_verification", "待核对"]]],
          ["cleaningStatuses", "历史导出保洁状态", [["ready", "无退房待保洁"], ["pending", "退房待保洁"]]],
          ["serviceStatuses", "历史导出服务状态", [["available", "正常"], ["problem", "问题房"], ["maintenance", "维修中"], ["disabled", "停用"]]],
        ] as const).map(([key, label, options]) => <label key={key}>{label}<select multiple aria-label={label} value={draft[key]} disabled={pending} onChange={(event) => setDraft({ ...draft, [key]: Array.from(event.target.selectedOptions, (option) => option.value) })}>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>)}
        <label>历史导出保洁频率<select multiple aria-label="历史导出保洁频率" value={draft.cleaningFrequencies ?? []} disabled={pending} onChange={(event) => setDraft({ ...draft, cleaningFrequencies: Array.from(event.currentTarget.selectedOptions, (option) => option.value) as HousingFiltersValue["cleaningFrequencies"] })}>{CLEANING_FREQUENCY_OPTIONS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
        {draft.dailyCleaningEnabled !== undefined ? <p>兼容筛选：{draft.dailyCleaningEnabled ? "仅每日保洁" : "非每日保洁"}</p> : null}
        <button type="button" disabled={pending} onClick={() => setDraft({ ...draft, cleaningFrequencies: [], dailyCleaningEnabled: undefined, quickFilter: draft.quickFilter?.startsWith("cleaning_") ? undefined : draft.quickFilter })}>清除历史导出保洁频率筛选</button>
        <p className="housingDialog__hint">保洁状态多选，不选表示全部；保洁条件按当前房间状态筛选。</p>
        <button type="button" disabled={pending} onClick={() => setDraft({ ...draft, cleaningStatuses: [], cleaningFrequencies: [], dailyCleaningEnabled: undefined, quickFilter: draft.quickFilter?.startsWith("cleaning_") ? undefined : draft.quickFilter })}>清除历史导出保洁筛选</button>
        <p className="housingDialog__hint">
          导出字段：房号、入住人、性别、身份证号、手机号、入住时间、离开时间。手机号未绑定时留空；日期只保留年月日。
        </p>
        <label>
          导出用途（必填）
          <input
            aria-label="导出用途（必填）"
            value={purpose}
            minLength={2}
            maxLength={200}
            placeholder="例如：住宿台账核对"
            required
            onChange={(event) => setPurpose(event.target.value)}
          />
        </label>
        <label>
          文件格式
          <select
            aria-label="文件格式"
            value={format}
            onChange={(event) => setFormat(event.target.value as "xlsx" | "csv")}
          >
            <option value="xlsx">Excel（XLSX）</option>
            <option value="csv">CSV</option>
          </select>
        </label>
        {error || formError ? <p className="housingDialog__error" role="alert">{error ?? formError}</p> : null}
        <footer>
          <button type="button" onClick={onClose} disabled={pending}>取消</button>
          <button type="submit" disabled={pending}>{pending ? "正在导出…" : "开始导出"}</button>
        </footer>
      </form>
  )
  return embedded ? form : <HousingDialogShell title="导出房态入住名单" pending={pending} error={null} focusFallbackRef={focusFallbackRef} onClose={onClose}>{form}</HousingDialogShell>
}
