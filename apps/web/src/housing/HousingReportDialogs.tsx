import { useMemo, useState } from "react"
import type { FormEvent, RefObject } from "react"
import { Link } from "react-router-dom"

import type { ProblemDetails } from "../api/client"
import { fromShanghaiDateTimeInput } from "./housingDateTime"
import { HousingDialogShell } from "./HousingDialogShell"
import type {
  GenerateHousingReportInput,
  HousingReportBatchDetail,
  HousingReportFilterOptions,
  HousingReportPersonType,
  MarkHousingReportSubmittedInput,
  VoidHousingReportInput,
} from "./reportingApi"

const PERSON_TYPES: Array<[HousingReportPersonType, string]> = [
  ["student", "学员"],
  ["coach", "教练"],
  ["life_teacher", "生活老师"],
  ["customer_service", "客服"],
  ["administration", "行政"],
  ["other", "其他工作人员"],
  ["visitor", "临时访客"],
]

function shanghaiTodayRange(): { start: string; end: string } {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date())
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  const startDate = `${values.year}-${values.month}-${values.day}`
  const next = new Date(`${startDate}T00:00:00+08:00`)
  next.setUTCDate(next.getUTCDate() + 1)
  const nextParts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(next)
  const nextValues = Object.fromEntries(nextParts.map((part) => [part.type, part.value]))
  return {
    start: `${startDate}T00:00`,
    end: `${nextValues.year}-${nextValues.month}-${nextValues.day}T00:00`,
  }
}

export type GenerateHousingReportDialogProps = {
  options: HousingReportFilterOptions
  initial?: Partial<GenerateHousingReportInput>
  pending: boolean
  error: string | null
  problemErrors?: ProblemDetails["errors"]
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: GenerateHousingReportInput) => Promise<void>
}

export function GenerateHousingReportDialog({
  options,
  initial,
  pending,
  error,
  problemErrors,
  focusFallbackRef,
  onClose,
  onSubmit,
}: GenerateHousingReportDialogProps) {
  const defaults = useMemo(shanghaiTodayRange, [])
  const [rangeStart, setRangeStart] = useState(
    initial?.range_start?.slice(0, 16) ?? defaults.start,
  )
  const [rangeEnd, setRangeEnd] = useState(
    initial?.range_end?.slice(0, 16) ?? defaults.end,
  )
  const [scopeMode, setScopeMode] = useState<GenerateHousingReportInput["scope_mode"]>(
    initial?.scope_mode ?? "authorized",
  )
  const [buildingIds, setBuildingIds] = useState<string[]>(initial?.building_ids ?? [])
  const [floorIds, setFloorIds] = useState<string[]>(initial?.floor_ids ?? [])
  const [cohortIds, setCohortIds] = useState<string[]>(initial?.cohort_ids ?? [])
  const [personTypes, setPersonTypes] = useState<HousingReportPersonType[]>(
    initial?.person_types ?? [],
  )
  const [purpose, setPurpose] = useState(initial?.purpose ?? "")
  const valid = Boolean(
    rangeStart
    && rangeEnd
    && rangeStart < rangeEnd
    && purpose.trim().length >= 2
    && (scopeMode !== "buildings" || buildingIds.length > 0)
    && (scopeMode !== "floors" || floorIds.length > 0),
  )

  function toggle(current: string[], value: string, checked: boolean): string[] {
    return checked
      ? [...new Set([...current, value])]
      : current.filter((item) => item !== value)
  }

  async function submit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!valid) return
    await onSubmit({
      range_start: fromShanghaiDateTimeInput(rangeStart),
      range_end: fromShanghaiDateTimeInput(rangeEnd),
      scope_mode: scopeMode,
      building_ids: scopeMode === "buildings" ? [...buildingIds].sort() : [],
      floor_ids: scopeMode === "floors" ? [...floorIds].sort() : [],
      cohort_ids: [...cohortIds].sort(),
      person_types: PERSON_TYPES.map(([value]) => value).filter((value) =>
        personTypes.includes(value)),
      purpose: purpose.trim(),
      corrects_batch_id: initial?.corrects_batch_id ?? null,
    })
  }

  return (
    <HousingDialogShell
      title={initial?.corrects_batch_id ? "创建更正实名名单" : "生成实名名单"}
      pending={pending}
      error={error}
      focusFallbackRef={focusFallbackRef}
      onClose={onClose}
    >
      <form className="housingReportDialogForm" onSubmit={(event) => void submit(event)}>
        <div className="housingReportDialogForm__grid">
          <label>开始时间<input data-autofocus type="datetime-local" value={rangeStart} disabled={pending} onChange={(event) => setRangeStart(event.target.value)} /></label>
          <label>结束时间<input type="datetime-local" value={rangeEnd} disabled={pending} onChange={(event) => setRangeEnd(event.target.value)} /></label>
        </div>
        <fieldset className="housingReportChoices">
          <legend>住宿范围</legend>
          <label><input type="radio" name="report-scope" checked={scopeMode === "authorized"} disabled={pending} onChange={() => setScopeMode("authorized")} />全部获权范围</label>
          <label><input type="radio" name="report-scope" checked={scopeMode === "buildings"} disabled={pending} onChange={() => setScopeMode("buildings")} />指定楼栋</label>
          <label><input type="radio" name="report-scope" checked={scopeMode === "floors"} disabled={pending} onChange={() => setScopeMode("floors")} />指定楼层</label>
        </fieldset>
        {scopeMode === "buildings" ? (
          <fieldset className="housingReportChoices"><legend>选择楼栋</legend>{options.buildings.map((building) => (
            <label key={building.id}><input type="checkbox" checked={buildingIds.includes(building.id)} disabled={pending} onChange={(event) => setBuildingIds(toggle(buildingIds, building.id, event.target.checked))} />{building.name}</label>
          ))}</fieldset>
        ) : null}
        {scopeMode === "floors" ? (
          <fieldset className="housingReportChoices"><legend>选择楼层</legend>{options.floors.map((floor) => {
            const building = options.buildings.find((item) => item.id === floor.building_id)
            return (
              <label key={floor.id}><input type="checkbox" checked={floorIds.includes(floor.id)} disabled={pending} onChange={(event) => setFloorIds(toggle(floorIds, floor.id, event.target.checked))} />{floor.name}（{building?.name ?? "未知楼栋"}）</label>
            )
          })}</fieldset>
        ) : null}
        <fieldset className="housingReportChoices"><legend>营期（可选）</legend>{options.cohorts.map((cohort) => (
          <label key={cohort.id}><input type="checkbox" checked={cohortIds.includes(cohort.id)} disabled={pending} onChange={(event) => setCohortIds(toggle(cohortIds, cohort.id, event.target.checked))} />{cohort.name}</label>
        ))}</fieldset>
        <fieldset className="housingReportChoices"><legend>人员类型（可选）</legend>{PERSON_TYPES.map(([value, label]) => (
          <label key={value}><input type="checkbox" checked={personTypes.includes(value)} disabled={pending} onChange={(event) => setPersonTypes(toggle(personTypes, value, event.target.checked) as HousingReportPersonType[])} />{label}</label>
        ))}</fieldset>
        <label>导出用途<textarea value={purpose} maxLength={200} disabled={pending} onChange={(event) => setPurpose(event.target.value)} /></label>
        {problemErrors?.length ? (
          <section className="housingReportIssues" aria-label="资料问题">
            <h3>需要修复的资料</h3>
            <ul>{problemErrors.map((item, index) => <li key={`${item.field}-${index}`}>{item.message}</li>)}</ul>
            <Link to="/people">前往人员中心修复</Link>
          </section>
        ) : null}
        <footer className="housingDialog__actions">
          <button type="button" disabled={pending} onClick={onClose}>取消</button>
          <button className="housingDialog__primary" type="submit" disabled={pending || !valid}>{pending ? "生成中…" : "确认生成"}</button>
        </footer>
      </form>
    </HousingDialogShell>
  )
}

type ActionDialogProps<Input> = {
  batchNumber: string
  version: number
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: Input) => Promise<void>
}

export function MarkHousingReportSubmittedDialog({
  batchNumber,
  version,
  pending,
  error,
  focusFallbackRef,
  onClose,
  onSubmit,
}: ActionDialogProps<MarkHousingReportSubmittedInput>) {
  const [submittedAt, setSubmittedAt] = useState("")
  const [note, setNote] = useState("")
  return (
    <HousingDialogShell title="标记人工报送" pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <form className="housingReportDialogForm" onSubmit={(event) => {
        event.preventDefault()
        if (!submittedAt) return
        void onSubmit({ submitted_at: fromShanghaiDateTimeInput(submittedAt), note: note.trim() || null, version })
      }}>
        <p>批次：{batchNumber}</p>
        <label>报送时间<input data-autofocus type="datetime-local" value={submittedAt} disabled={pending} onChange={(event) => setSubmittedAt(event.target.value)} /></label>
        <label>报送备注（选填）<textarea value={note} maxLength={500} disabled={pending} onChange={(event) => setNote(event.target.value)} /></label>
        <footer className="housingDialog__actions"><button type="button" disabled={pending} onClick={onClose}>取消</button><button className="housingDialog__primary" type="submit" disabled={pending || !submittedAt}>确认已报送</button></footer>
      </form>
    </HousingDialogShell>
  )
}

export function VoidHousingReportDialog({
  batchNumber,
  version,
  pending,
  error,
  focusFallbackRef,
  onClose,
  onSubmit,
}: ActionDialogProps<VoidHousingReportInput>) {
  const [reason, setReason] = useState("")
  return (
    <HousingDialogShell title="作废实名批次" pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <form className="housingReportDialogForm" onSubmit={(event) => {
        event.preventDefault()
        if (reason.trim().length < 2) return
        void onSubmit({ reason: reason.trim(), version })
      }}>
        <p>批次：{batchNumber}</p>
        <p className="housingDialog__warning">作废后原批次不能再次下载，只能创建关联更正批次。</p>
        <label>作废原因<textarea data-autofocus value={reason} maxLength={500} disabled={pending} onChange={(event) => setReason(event.target.value)} /></label>
        <footer className="housingDialog__actions"><button type="button" disabled={pending} onClick={onClose}>取消</button><button className="housingDialog__primary" type="submit" disabled={pending || reason.trim().length < 2}>确认作废</button></footer>
      </form>
    </HousingDialogShell>
  )
}

export function HousingReportDetailDialog({
  detail,
  pending,
  error,
  focusFallbackRef,
  onClose,
}: {
  detail: HousingReportBatchDetail | null
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
}) {
  return (
    <HousingDialogShell title="实名批次详情" pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      {detail ? (
        <dl className="housingReportDetail">
          <div><dt>批次编号</dt><dd>{detail.batch_number}</dd></div>
          <div><dt>导出用途</dt><dd>{detail.purpose}</dd></div>
          <div><dt>人员 / 住宿 / 监护人</dt><dd>{detail.resident_count} / {detail.stay_row_count} / {detail.guardian_row_count}</dd></div>
          <div><dt>报送备注</dt><dd>{detail.submission_note || "—"}</dd></div>
          <div><dt>作废原因</dt><dd>{detail.void_reason || "—"}</dd></div>
          <div><dt>更正链</dt><dd>{detail.correction_chain.length ? detail.correction_chain.map((item) => item.batch_number).join(" → ") : "无"}</dd></div>
        </dl>
      ) : <p>正在读取批次详情…</p>}
      <footer className="housingDialog__actions"><button type="button" data-autofocus disabled={pending} onClick={onClose}>关闭</button></footer>
    </HousingDialogShell>
  )
}
