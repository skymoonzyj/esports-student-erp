import { useState } from "react"
import type { RefObject, ReactNode } from "react"
import { ProblemDetailsError } from "../api/client"
import type { HousingCatalog, HousingFiltersValue, HousingRoomStatusExportInput } from "./api"
import { HousingDialogShell } from "./HousingDialogShell"

export type SnapshotDialogProps = {
  catalog: HousingCatalog
  filters: HousingFiltersValue
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: HousingRoomStatusExportInput) => Promise<void>
  embedded?: boolean
}

export function HousingRoomStatusExportDialog({ catalog, filters, pending, error, focusFallbackRef, onClose, onSubmit, embedded }: SnapshotDialogProps) {
  const [draft, setDraft] = useState<HousingRoomStatusExportInput>(() => ({
    format: "xlsx", purpose: "", building_ids: filters.buildingId ? [filters.buildingId] : [], floor_ids: filters.floorId ? [filters.floorId] : [],
    query: filters.query, room_types: [...(filters.roomTypes ?? [])], verification_statuses: [...(filters.verificationStatuses ?? [])], service_statuses: [...(filters.serviceStatuses ?? [])],
    current_gender: filters.currentGender, occupancy: filters.occupancy, assignability: filters.assignability, cleaning_statuses: [...(filters.cleaningStatuses ?? [])], daily_cleaning_enabled: filters.dailyCleaningEnabled,
  }))
  const [formError, setFormError] = useState<string | null>(null)
  const floors = catalog.floors.filter((floor) => !draft.building_ids.length || draft.building_ids.includes(floor.building_id))
  function multi(key: "room_types" | "verification_statuses" | "service_statuses" | "cleaning_statuses", label: string, options: [string, string][]): ReactNode {
    return <label>{label}<select aria-label={label} multiple value={draft[key]} disabled={pending} onChange={(event) => setDraft({ ...draft, [key]: Array.from(event.target.selectedOptions, (option) => option.value) })}>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
  }
  function single(key: "occupancy" | "assignability" | "current_gender", label: string, options: [string, string][]): ReactNode {
    return <label>{label}<select aria-label={label} value={draft[key] ?? ""} disabled={pending} onChange={(event) => setDraft({ ...draft, [key]: event.target.value || undefined })}><option value="">全部</option>{options.map(([value, text]) => <option key={value} value={value}>{text}</option>)}</select></label>
  }
  const form = <form className="housingReportDialogForm" noValidate onSubmit={(event) => {
    event.preventDefault()
    const purpose = draft.purpose.trim()
    if (purpose.length < 2 || purpose.length > 200) { setFormError("请填写 2–200 个字的导出用途"); return }
    setFormError(null)
    void onSubmit({ ...draft, purpose }).catch((caught: unknown) => setFormError(caught instanceof ProblemDetailsError ? caught.problem.detail : "房态导出暂时失败"))
  }}>
    <p className="housingDialog__hint">当前房态快照：包含空房，每名当前住客一行，不含历史退房及未来预约。导出全部匹配房间，不仅当前页；人员证件和手机号等共 21 列，不含照片。多选不选表示全部，筛选不会改变页面。</p>
    <label>导出楼栋<select multiple aria-label="导出楼栋" value={draft.building_ids} disabled={pending} onChange={(event) => {
      const building_ids = Array.from(event.target.selectedOptions, (option) => option.value)
      setDraft({ ...draft, building_ids, floor_ids: draft.floor_ids.filter((id) => catalog.floors.some((floor) => floor.id === id && (!building_ids.length || building_ids.includes(floor.building_id)))) })
    }}>{catalog.buildings.map((building) => <option key={building.id} value={building.id}>{building.name}</option>)}</select></label>
    <label>导出楼层<select multiple aria-label="导出楼层" value={draft.floor_ids} disabled={pending} onChange={(event) => setDraft({ ...draft, floor_ids: Array.from(event.target.selectedOptions, (option) => option.value) })}>{floors.map((floor) => <option key={floor.id} value={floor.id}>{floor.building_name} · {floor.name}</option>)}</select></label>
    <label>导出房号搜索<input aria-label="导出房号搜索" value={draft.query ?? ""} disabled={pending} onChange={(event) => setDraft({ ...draft, query: event.target.value })} /></label>
    {single("occupancy", "导出入住状态", [["occupied", "已入住"], ["unoccupied", "未入住"]])}
    {single("assignability", "导出可入住性", [["assignable", "可入住"], ["unassignable", "不可入住"]])}
    {single("current_gender", "导出入住性别", [["male", "男住"], ["female", "女住"], ["unspecified", "未指定"]])}
    {multi("verification_statuses", "导出核对状态", [["verified", "已核对"], ["pending_verification", "待核对"]])}
    {multi("cleaning_statuses", "导出保洁状态", [["pending", "待保洁"], ["ready", "无待保洁"]])}
    <label>导出每日保洁<select aria-label="导出每日保洁" value={draft.daily_cleaning_enabled === undefined ? "" : String(draft.daily_cleaning_enabled)} disabled={pending} onChange={(event) => setDraft({ ...draft, daily_cleaning_enabled: event.target.value === "" ? undefined : event.target.value === "true" })}><option value="">全部</option><option value="true">是</option><option value="false">否</option></select></label>
    {multi("room_types", "导出房型", [["single", "单人间"], ["double", "双人间"], ["triple", "三人间"], ["custom", "自定义"], ["functional", "功能间"]])}
    {multi("service_statuses", "导出服务状态", [["available", "正常"], ["problem", "问题房"], ["maintenance", "维修中"], ["disabled", "停用"]])}
    <label>导出用途（必填）<input aria-label="导出用途（必填）" minLength={2} maxLength={200} value={draft.purpose} disabled={pending} onChange={(event) => setDraft({ ...draft, purpose: event.target.value })} /></label>
    <label>文件格式<select aria-label="文件格式" value={draft.format} disabled={pending} onChange={(event) => setDraft({ ...draft, format: event.target.value as "xlsx" | "csv" })}><option value="xlsx">Excel（XLSX）</option><option value="csv">CSV</option></select></label>
    {error || formError ? <p role="alert">{error ?? formError}</p> : null}
    <footer><button type="button" disabled={pending} onClick={onClose}>取消</button><button type="submit" disabled={pending}>{pending ? "正在导出…" : "开始导出"}</button></footer>
  </form>
  return embedded ? form : <HousingDialogShell title="导出当前房态" pending={pending} error={null} focusFallbackRef={focusFallbackRef} onClose={onClose}>{form}</HousingDialogShell>
}
