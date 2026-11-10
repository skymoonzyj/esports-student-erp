import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { ProblemDetailsError } from "../api/client"
import type { HousingRoomDetail, RoomType, ServiceStatus } from "./api"
import { housingKeys } from "./housingKeys"
import { useHousingPropertyAccess } from "./propertyAccess"
import { listHousingPropertyChanges, reviewHousingPropertyChange, submitHousingPropertyChange, withdrawHousingPropertyChange } from "./propertyApi"
import type { HousingPropertyChange, HousingPropertyStatus, HousingPropertyValues } from "./propertyApi"
import "./housing-property.css"

const STATUS = { pending: "待审核（未生效）", approved: "已通过（已生效）", rejected: "已驳回（未生效）", withdrawn: "已撤回（未生效）" }
const SERVICE: Record<ServiceStatus, string> = { available: "正常", problem: "问题房", maintenance: "维修", disabled: "停用" }
const ROOM: Record<RoomType, string> = { single: "单人间", double: "双人间", triple: "三人间", custom: "自定义", functional: "功能房" }
const FIELD = { service_status: "服务状态", room_type: "房型", capacity: "容量", notes: "备注 / 问题说明" }
const CAPACITY: Partial<Record<RoomType, number>> = { single: 1, double: 2, triple: 3, functional: 0 }

function valueText(key: keyof HousingPropertyValues, value: unknown): string {
  if (value == null || value === "") return "无"
  if (key === "service_status") return SERVICE[value as ServiceStatus] ?? String(value)
  if (key === "room_type") return ROOM[value as RoomType] ?? String(value)
  return String(value)
}
function Preview({ before, changes }: { before: HousingPropertyValues; changes: HousingPropertyValues }) {
  return <dl className="housingProperty__preview">{(Object.keys(changes) as (keyof HousingPropertyValues)[]).map((key) => <div key={key}><dt>{FIELD[key]}</dt><dd>{`${valueText(key, before[key])} → ${valueText(key, changes[key])}`}</dd></div>)}</dl>
}

type PanelProps = { mode: "submit" | "review"; room?: HousingRoomDetail; onChanged?: () => void; enabled?: boolean }

export function HousingPropertyChangesPanel(props: PanelProps) {
  const access = useHousingPropertyAccess(props.mode === "submit" ? props.room?.room.id : undefined, props.enabled ?? true)
  if (access.error) return <p className="housingProperty__error" role="alert">无法核验物业申请权限，请刷新重试。</p>
  if (!access.catalog || access.capabilities?.[props.mode === "submit" ? "can_submit" : "can_review"] !== true) return null
  if (props.mode === "submit" && !props.room) return null
  if (props.room && props.room.access_revision !== access.catalog?.access_revision) return null
  return <Changes key={`${access.signature}:${props.mode}:${props.room?.room.id ?? "all"}:${props.room?.room.version ?? ""}`} {...props} access={access} />
}

function Changes({ mode, room, onChanged, access }: PanelProps & { access: ReturnType<typeof useHousingPropertyAccess> }) {
  const client = useQueryClient()
  const live = useRef(true)
  useEffect(() => { live.current = true; return () => { live.current = false } }, [])
  const [status, setStatus] = useState<HousingPropertyStatus>("pending")
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState({ service_status: false, room_type: false, capacity: false, notes: false })
  const [service, setService] = useState<ServiceStatus>(room?.room.service_status ?? "available")
  const [roomType, setRoomType] = useState<RoomType>(room?.room.room_type ?? "single")
  const [capacity, setCapacity] = useState(String(room?.room.capacity ?? 1))
  const [notes, setNotes] = useState(room?.room.notes ?? "")
  const [reason, setReason] = useState("")
  const [review, setReview] = useState<{ id: string; decision: "approve" | "reject" } | null>(null)
  const [reviewNote, setReviewNote] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [blocked, setBlocked] = useState(false)
  const list = useQuery({ queryKey: [...access.scope, "changes", mode, room?.room.id ?? "all", status, page], queryFn: ({ signal }) => listHousingPropertyChanges({ status, page, page_size: 20, view: mode === "submit" ? "submitted" : "review", ...(mode === "submit" && room ? { room_id: room.room.id } : {}) }, signal), enabled: access.ready, retry: false, gcTime: 0 })
  const changes: HousingPropertyValues = {}
  if (room) {
    if (selected.service_status && service !== room.room.service_status) changes.service_status = service
    if (selected.room_type && roomType !== room.room.room_type) changes.room_type = roomType
    const targetCapacity = selected.room_type && CAPACITY[roomType] !== undefined ? CAPACITY[roomType]! : Number(capacity)
    if ((selected.capacity || selected.room_type) && targetCapacity !== room.room.capacity) changes.capacity = targetCapacity
    if (selected.notes && notes.trim() !== (room.room.notes ?? "")) changes.notes = notes.trim() || null
  }
  const targetType = changes.room_type ?? room?.room.room_type
  const targetCapacity = changes.capacity ?? room?.room.capacity
  const validCapacity = targetType === "custom" ? Number.isInteger(targetCapacity) && (targetCapacity ?? 0) >= 1 && (targetCapacity ?? 101) <= 100 : targetType != null && targetCapacity === CAPACITY[targetType]
  const hasChanges = Object.keys(changes).length > 0

  async function act(operation: () => Promise<HousingPropertyChange>, kind: "submit" | "approve" | "reject" | "withdraw") {
    if ((mode === "review") !== (kind === "approve" || kind === "reject")) return
    if (busy || blocked || !access.ready || !access.isAuthorizedNow(kind === "approve" || kind === "reject" ? "can_review" : "can_submit")) return
    if (kind === "submit" && !access.isAuthorizedNow("can_create_request")) return
    setBusy(true); setError(""); setNotice("")
    try {
      const result = await operation()
      if (!live.current) return
      setReview(null); setReviewNote("")
      if (kind === "submit") {
        setSelected({ service_status: false, room_type: false, capacity: false, notes: false }); setReason("")
        setNotice("申请已提交，审核通过前当前房间不变。")
      } else {
        setNotice(STATUS[result.status])
        if (kind === "approve" && result.status === "approved") {
          onChanged?.()
          await client.invalidateQueries({ queryKey: housingKeys.root(access.accountId) })
        }
      }
      await client.invalidateQueries({ queryKey: [...access.scope, "changes"] })
    } catch (caught) {
      if (!live.current) return
      if (caught instanceof ProblemDetailsError && [401, 403].includes(caught.problem.status)) { setBlocked(true); void client.invalidateQueries({ queryKey: access.scope }) }
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        setError(`${caught.problem.detail}；请刷新后撤回重提，不会覆盖当前房间。`)
        setSelected({ service_status: false, room_type: false, capacity: false, notes: false }); setReason(""); setReview(null)
        await client.invalidateQueries({ queryKey: [...access.scope, "changes"] }); onChanged?.()
      } else setError(caught instanceof Error ? caught.message : "操作失败，请重试。")
    } finally { if (live.current) setBusy(false) }
  }
  const items = !blocked && !list.isError ? list.data?.items ?? [] : []
  const title = mode === "review" ? "物业变更审批" : "物业变更申请"
  return <><section className="housingProperty" aria-label={title} hidden={!access.ready} inert={!access.ready}>
    <header><h2>{title}</h2><p>待审核的建议不计入当前房态、统计或导出；通过后才生效。</p></header>
    {error && <p className="housingProperty__error" role="alert">{error}</p>}
    {notice && <p role="status">{notice}</p>}
    {mode === "submit" && room && access.capabilities?.can_create_request === true && !blocked && <form onSubmit={(event) => { event.preventDefault(); if (hasChanges && validCapacity && reason.trim()) void act(() => submitHousingPropertyChange(room.room.id, { expected_version: room.room.version, changes, reason: reason.trim() }), "submit") }}>
      <fieldset disabled={busy || !access.ready}><legend>为 {room.room.room_number} 提交申请（当前版本 {room.room.version}）</legend>
        <p>只发送勾选且实际变化的项目。备注也可作为问题说明，不直接修改房间。</p>
        <label className="housingProperty__check"><input type="checkbox" checked={selected.service_status} onChange={(e) => setSelected({ ...selected, service_status: e.target.checked })} />变更服务状态</label>
        {selected.service_status && <label>建议服务状态<select value={service} onChange={(e) => setService(e.target.value as ServiceStatus)}>{Object.entries(SERVICE).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
        <label className="housingProperty__check"><input type="checkbox" checked={selected.room_type} onChange={(e) => setSelected({ ...selected, room_type: e.target.checked })} />变更房型</label>
        {selected.room_type && <label>建议房型<select value={roomType} onChange={(e) => setRoomType(e.target.value as RoomType)}>{Object.entries(ROOM).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
        <label className="housingProperty__check"><input type="checkbox" checked={selected.capacity} onChange={(e) => setSelected({ ...selected, capacity: e.target.checked })} />变更容量</label>
        {(selected.capacity || (selected.room_type && roomType === "custom")) && <label>建议容量<input type="number" min={0} max={100} step={1} value={selected.room_type && CAPACITY[roomType] !== undefined ? CAPACITY[roomType] : capacity} disabled={selected.room_type && roomType !== "custom"} onChange={(e) => setCapacity(e.target.value)} /></label>}
        {selected.room_type && roomType !== "custom" && <p>房型容量将同时设为 {CAPACITY[roomType]}，并在下方预览。</p>}
        <label className="housingProperty__check"><input type="checkbox" checked={selected.notes} onChange={(e) => setSelected({ ...selected, notes: e.target.checked })} />变更备注 / 问题说明</label>
        {selected.notes && <label>建议备注 / 问题说明<textarea maxLength={500} value={notes} onChange={(e) => setNotes(e.target.value)} /></label>}
        <label>申请原因<textarea required maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></label>
        {hasChanges && <Preview before={room.room} changes={changes} />}
        {hasChanges && !validCapacity && <p role="alert">房型与容量不一致，请修正后提交。</p>}
        <button type="submit" disabled={!hasChanges || !validCapacity || !reason.trim()}>提交待审申请</button>
      </fieldset>
    </form>}
    {!blocked && <><div className="housingProperty__toolbar"><label>申请状态<select disabled={!access.ready} value={status} onChange={(e) => { setStatus(e.target.value as HousingPropertyStatus); setPage(1); setReview(null) }}>{Object.entries(STATUS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><button type="button" disabled={busy || !access.ready || list.isFetching} onClick={() => { setReview(null); void list.refetch() }}>刷新申请</button></div>
      {list.isError ? <p role="alert">申请加载失败，请刷新重试。</p> : list.isPending ? <p role="status">正在加载申请…</p> : items.length === 0 ? <p>暂无此状态申请。</p> : items.map((item) => <article className="housingProperty__request" key={item.id}>
        <h3>{item.building_name} · {item.room_number}</h3><p>{STATUS[item.status]}</p><p><span>{item.submitted_by_name}</span> · <time dateTime={item.submitted_at}>{new Date(item.submitted_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</time> · 原版本 {item.base_version}</p>
        <Preview before={item.before} changes={item.changes} /><p>申请原因：{item.reason}</p>
        {item.review_note && <p>{item.review_note}</p>}
        {item.reviewed_at && <p>处理时间：{new Date(item.reviewed_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</p>}
        {item.status === "pending" && <div className="housingProperty__actions">
          {mode === "submit" && item.can_withdraw && <button type="button" disabled={busy || !access.ready} onClick={() => void act(() => withdrawHousingPropertyChange(item.id), "withdraw")}>撤回申请</button>}
          {mode === "review" && access.capabilities?.can_review && item.can_review && <><button type="button" disabled={busy || !access.ready} onClick={() => { setReview({ id: item.id, decision: "approve" }); setReviewNote("") }}>通过申请</button><button type="button" disabled={busy || !access.ready} onClick={() => { setReview({ id: item.id, decision: "reject" }); setReviewNote("") }}>驳回申请</button></>}
        </div>}
        {mode === "review" && item.status === "pending" && review?.id === item.id && access.capabilities?.can_review && item.can_review && <div className="housingProperty__confirm" role="group" aria-label="确认审核">
          <p>{review.decision === "approve" ? "确认将以上建议应用到当前房间？系统会重新检查版本、权限与住宿冲突。" : "请填写驳回原因，房间不会变化。"}</p>
          <label>审核说明<textarea disabled={!access.ready} maxLength={500} value={reviewNote} onChange={(e) => setReviewNote(e.target.value)} /></label>
          <div className="housingProperty__actions"><button type="button" disabled={busy || !access.ready || (review.decision === "reject" && !reviewNote.trim())} onClick={() => { const decision = review.decision; void act(() => reviewHousingPropertyChange(item.id, { decision, ...(reviewNote.trim() ? { note: reviewNote.trim() } : {}) }), decision) }}>{review.decision === "approve" ? "确认通过并生效" : "确认驳回"}</button><button type="button" disabled={busy || !access.ready} onClick={() => setReview(null)}>取消</button></div>
        </div>}
      </article>)}
      <div className="housingProperty__actions"><button type="button" disabled={page <= 1 || busy || !access.ready || list.isFetching} onClick={() => { setPage(page - 1); setReview(null) }}>上一页</button><span>第 {page} 页 · 共 {list.data?.total ?? 0} 条</span><button type="button" disabled={busy || !access.ready || list.isFetching || page * 20 >= (list.data?.total ?? 0)} onClick={() => { setPage(page + 1); setReview(null) }}>下一页</button></div>
    </>}
  </section>{!access.ready && <p role="status">正在重新核验物业申请权限…</p>}</>
}
