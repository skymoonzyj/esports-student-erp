import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ArrowLeft, CalendarPlus, RefreshCw, Wrench } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import { getHousingCatalog, getHousingRoomDetail, uploadRoomPhoto, deleteRoomPhoto, roomCleaningFrequencyLabel, updateHousingRoom } from "./api"
import type { HousingRoomIssue, HousingStay, HousingTransfer } from "./api"
import { housingKeys } from "./housingKeys"
import { STAY_ACTION_LABELS, useHousingRoomLifecycle } from "./useHousingRoomLifecycle"
import type { StayActionKind } from "./useHousingRoomLifecycle"
import { HousingRoomPhotos } from "./HousingRoomPhotos"
import { HousingRoomCardsPanel } from "./HousingRoomCardsPanel"
import { HousingPropertyChangesPanel } from "./HousingPropertyChangesPanel"
import { isHousingRoomDestinationAllowed, RoomDialog } from "./HousingCatalogDialogs"
import { personHousingKeys } from "../people/housingApi"
import type { HousingRoomDetail, HousingRoomUpdate } from "./api"

import "./housing.css"

const PURPOSE_LABELS = { permanent: "常住", camp: "随营", temporary: "临时住宿" } as const
const PERSON_TYPE_LABELS = {
  student: "学员",
  player: "队员",
  student_player: "学员兼队员",
  team_manager: "战队经理",
  coach: "教练",
  assistant_coach: "助教",
  life_teacher: "生活老师",
  customer_service: "客服",
  administration: "行政人员",
  other: "其他工作人员",
  visitor: "临时访客",
} as const
const STATUS_LABELS = {
  planned: "已预约",
  checked_in: "已入住",
  checkout_pending: "待确认退房",
  checked_out: "已退房",
  cancelled: "已取消",
} as const

function isPrivateFailure(error: unknown): boolean {
  return error instanceof ProblemDetailsError && (error.problem.status === 403 || error.problem.status === 404)
}

function errorMessage(error: unknown): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : "房间详情暂时无法读取"
}

function StayTimeline({ title, stays, pending, onAction }: {
  title: string
  stays: HousingStay[]
  pending: boolean
  onAction: (action: StayActionKind, stay: HousingStay) => void
}) {
  return (
    <section className="housingRoomDetail__section" aria-label={title}>
      <header><h2>{title}</h2><span>{stays.length} 项</span></header>
      {stays.length === 0 ? <p className="housingRoomDetail__empty">暂无记录</p> : (
        <div className="housingRoomDetail__cards">
          {stays.map((stay) => (
            <article className="housingStayRecord" key={stay.id}>
              <header><div><strong>{stay.person_name}</strong><span>{PERSON_TYPE_LABELS[stay.person_type]} · {stay.sex === "male" ? "男" : stay.sex === "female" ? "女" : "性别待补"}</span></div><span>{STATUS_LABELS[stay.status]}</span></header>
              <dl>
                <div><dt>住宿类型</dt><dd>{PURPOSE_LABELS[stay.stay_purpose]}</dd></div>
                <div><dt>计划入住</dt><dd>{formatShanghaiDate(stay.planned_check_in_at)}</dd></div>
                {stay.planned_check_out_at ? <div><dt>计划离开</dt><dd>{formatShanghaiDate(stay.planned_check_out_at)}</dd></div> : null}
                {stay.actual_check_in_at ? <div><dt>实际入住</dt><dd>{formatShanghaiDate(stay.actual_check_in_at)}</dd></div> : null}
                {stay.actual_check_out_at ? <div><dt>实际离开</dt><dd>{formatShanghaiDate(stay.actual_check_out_at)}</dd></div> : null}
              </dl>
              {stay.notes ? <p>{stay.notes}</p> : null}
              {stay.void_info ? <p>已作废 · {formatShanghaiDate(stay.void_info.voided_at)} · {stay.void_info.reason}</p> : null}
              <footer className="housingStayRecord__actions">
                {(Object.keys(STAY_ACTION_LABELS) as StayActionKind[]).map((action) => (
                  stay.allowed_actions[action]
                    ? <button key={action} type="button" disabled={pending} onClick={() => onAction(action, stay)}>{STAY_ACTION_LABELS[action]} {stay.person_name}</button>
                    : null
                ))}
              </footer>
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

function TransferTimeline({ transfers }: { transfers: HousingTransfer[] }) {
  return (
    <section className="housingRoomDetail__section" aria-label="调房记录">
      <header><h2>调房记录</h2><span>{transfers.length} 项</span></header>
      {transfers.length === 0 ? <p className="housingRoomDetail__empty">暂无记录</p> : <div className="housingRoomDetail__cards">{transfers.map((transfer) => <article className="housingSimpleRecord" key={transfer.id}><strong>{transfer.reason}</strong><span>{formatShanghaiDate(transfer.transferred_at)}</span>{transfer.from_stay_voided ? <span>来源历史已作废</span> : null}{transfer.to_stay_voided ? <span>去向历史已作废</span> : null}</article>)}</div>}
    </section>
  )
}

function IssueTimeline({ issues, pending, onResolve }: {
  issues: HousingRoomIssue[]
  pending: boolean
  onResolve: (issue: HousingRoomIssue) => void
}) {
  return (
    <section className="housingRoomDetail__section" aria-label="问题记录">
      <header><h2>问题记录</h2><span>{issues.length} 项</span></header>
      {issues.length === 0 ? <p className="housingRoomDetail__empty">暂无记录</p> : <div className="housingRoomDetail__cards">{issues.map((issue) => <article className="housingSimpleRecord" key={issue.id}><header><strong>{issue.description}</strong><span>{issue.status === "open" ? "待处理" : "已解决"}</span></header><span>{formatShanghaiDate(issue.reported_at)}</span>{issue.resolution_note ? <p>{issue.resolution_note}</p> : null}{issue.allowed_actions.resolve ? <button type="button" disabled={pending} onClick={() => onResolve(issue)}>解决问题 {issue.description}</button> : null}</article>)}</div>}
    </section>
  )
}

export function HousingRoomDetailPage() {
  const { roomId } = useParams<{ roomId: string }>()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const pageFocusRef = useRef<HTMLElement>(null)
  const clientStateEpochRef = useRef(0)
  const queryCleanupGenerationRef = useRef(0)
  const previousScopeRef = useRef<{ accountId: string; accessRevision: string } | null>(null)
  const accountId = user?.account_id ?? "anonymous"
  const authSignature = JSON.stringify([accountId, user?.is_super_admin, [...(user?.permissions ?? [])].sort(), [...(user?.scoped_permissions ?? [])].sort()])
  const initialAuthRef = useRef({ signature: authSignature, accountId })
  const authChanged = initialAuthRef.current.signature !== authSignature
  const [accessState, setAccessState] = useState<"active" | "private" | "mismatch">("active")
  const housingRoot = useMemo(() => housingKeys.root(accountId), [accountId])
  const catalog = useQuery({
    queryKey: housingKeys.catalog(accountId),
    queryFn: ({ signal }) => getHousingCatalog(signal),
    enabled: Boolean(user?.account_id && roomId && accessState === "active" && !authChanged),
  })
  const accessRevision = catalog.data?.access_revision ?? "pending"
  const requestScope = `${accountId}:${accessRevision}:${roomId}`
  const [voidedViewScope, setVoidedViewScope] = useState<string | null>(null)
  const voidedAuthorityScopeRef = useRef<string | null>(null)
  const includeVoided = voidedViewScope === requestScope
  const detailKey = housingKeys.detail(accountId, accessRevision, roomId ?? "missing", includeVoided)
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: ({ signal }) => getHousingRoomDetail(roomId ?? "", signal, includeVoided),
    enabled: Boolean(user?.account_id && roomId && catalog.data && accessState === "active" && !authChanged),
  })
  const privateFailure = [catalog.error, detail.error].find(isPrivateFailure) ?? null
  const revisionMismatch = Boolean(detail.data && catalog.data && detail.data.access_revision !== catalog.data.access_revision)
  const acceptedDetail = !authChanged && !revisionMismatch && detail.data?.room.allowed_actions.view_detail ? detail.data : undefined

  const purgePrivateHousing = useCallback((nextState: "private" | "mismatch") => {
    clientStateEpochRef.current += 1
    setAccessState(nextState)
    setVoidedViewScope(null)
    const cancellation = queryClient.cancelQueries({ queryKey: housingRoot })
    queryClient.removeQueries({ queryKey: housingRoot })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: housingRoot }))
  }, [housingRoot, queryClient])

  useEffect(() => {
    if (!authChanged) return
    purgePrivateHousing("mismatch")
    const oldRoot = housingKeys.root(initialAuthRef.current.accountId)
    const cancellation = queryClient.cancelQueries({ queryKey: oldRoot })
    queryClient.removeQueries({ queryKey: oldRoot })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: oldRoot }))
  }, [authChanged, purgePrivateHousing, queryClient])

  useEffect(() => {
    if (!acceptedDetail) return
    if (acceptedDetail.can_view_voided_history) {
      voidedAuthorityScopeRef.current = requestScope
      return
    }
    if (voidedAuthorityScopeRef.current === requestScope) {
      voidedAuthorityScopeRef.current = null
      purgePrivateHousing("mismatch")
      return
    }
    setVoidedViewScope(null)
    const filters = { queryKey: housingKeys.details(accountId, accessRevision), predicate: (query: { queryKey: readonly unknown[] }) => query.queryKey.at(-1) === true }
    if (!queryClient.getQueryCache().findAll(filters).length) return
    const cancellation = queryClient.cancelQueries(filters)
    queryClient.removeQueries(filters)
    void cancellation.finally(() => queryClient.removeQueries(filters))
  }, [acceptedDetail, accessRevision, accountId, purgePrivateHousing, queryClient, requestScope])

  useEffect(() => {
    if (!catalog.data || accessState !== "active") return
    const current = { accountId, accessRevision: catalog.data.access_revision }
    const previous = previousScopeRef.current
    previousScopeRef.current = current
    if (!previous || previous.accountId !== accountId || previous.accessRevision === current.accessRevision) return
    clientStateEpochRef.current += 1
    const previousScope = housingKeys.scope(accountId, previous.accessRevision)
    const cancellation = queryClient.cancelQueries({ queryKey: previousScope })
    queryClient.removeQueries({ queryKey: previousScope })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: previousScope }))
  }, [accessState, accountId, catalog.data, queryClient])

  useEffect(() => {
    if (!privateFailure || accessState !== "active") return
    purgePrivateHousing("private")
  }, [accessState, privateFailure, purgePrivateHousing])

  useEffect(() => {
    if (!revisionMismatch || accessState !== "active") return
    purgePrivateHousing("mismatch")
  }, [accessState, purgePrivateHousing, revisionMismatch])

  useEffect(() => {
    return () => { clientStateEpochRef.current += 1 }
  }, [])

  const handleLifecyclePrivateFailure = useCallback((error: unknown) => {
    if (isPrivateFailure(error)) purgePrivateHousing("private")
  }, [purgePrivateHousing])
  const handleLifecycleRevisionMismatch = useCallback(() => {
    purgePrivateHousing("mismatch")
  }, [purgePrivateHousing])

  const lifecycle = useHousingRoomLifecycle({
    accountId,
    accessRevision,
    detail: acceptedDetail,
    accessActive: accessState === "active" && !authChanged,
    clientStateEpochRef,
    focusFallbackRef: pageFocusRef,
    onPrivateFailure: handleLifecyclePrivateFailure,
    onAccessRevisionMismatch: handleLifecycleRevisionMismatch,
  })

  const editScope = JSON.stringify([requestScope, authSignature, accessState])
  const editScopeRef = useRef(editScope)
  editScopeRef.current = editScope
  const [editState, setEditState] = useState<{ scope: string; baseVersion: number } | null>(null)
  const [editError, setEditError] = useState<string | null>(null)
  const [editNotice, setEditNotice] = useState<string | null>(null)
  const editReady = accessState === "active" && !authChanged && !catalog.isFetching && !catalog.isError && !detail.isFetching && !detail.isError && acceptedDetail?.room.allowed_actions.edit === true
  const editVisible = editState?.scope === editScope && acceptedDetail?.room.allowed_actions.edit === true
  useEffect(() => {
    if (editState && (editState.scope !== editScope || (!detail.isFetching && !acceptedDetail?.room.allowed_actions.edit))) {
      setEditState(null)
      setEditError(null)
    }
  }, [acceptedDetail?.room.allowed_actions.edit, detail.isFetching, editScope, editState])

  async function submitRoomEdit(input: HousingRoomUpdate): Promise<void> {
    if (!editReady || !editVisible || !editState || !acceptedDetail || !catalog.data) return
    const currentDetail = queryClient.getQueryState<HousingRoomDetail>(detailKey)
    if (currentDetail?.status !== "success" || currentDetail.fetchStatus !== "idle" || !currentDetail.data?.room.allowed_actions.edit) return
    if (input.floor_id && !isHousingRoomDestinationAllowed(catalog.data, input.floor_id)) return
    const token = lifecycle.coordinator.acquire()
    if (!token) return
    const epoch = clientStateEpochRef.current
    const scope = editScope
    const current = () => epoch === clientStateEpochRef.current && scope === editScopeRef.current
    setEditError(null)
    setEditNotice(null)
    try {
      await updateHousingRoom(acceptedDetail.room.id, { ...input, version: editState.baseVersion })
      if (!current()) return
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: housingKeys.root(accountId) }),
        queryClient.invalidateQueries({ queryKey: personHousingKeys.root(accountId) }),
      ])
      if (!current()) return
      setEditState(null)
      setEditNotice("房间已保存，变更已生效。")
    } catch (caught) {
      if (!current()) return
      if (isPrivateFailure(caught)) { purgePrivateHousing("private"); return }
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        setEditError("数据已更新，请关闭并重新编辑，不会覆盖当前房间。")
        await queryClient.invalidateQueries({ queryKey: housingKeys.root(accountId) })
      } else setEditError(caught instanceof ProblemDetailsError ? caught.problem.detail : "操作暂时失败，请稍后重试")
    } finally { lifecycle.coordinator.release(token) }
  }

  const photoScope = JSON.stringify([requestScope, authSignature, acceptedDetail?.room.allowed_actions.manage_photos, accessState])
  const photoScopeRef = useRef(photoScope)
  photoScopeRef.current = photoScope
  async function mutatePhoto(operation: () => Promise<HousingRoomDetail>): Promise<void> {
    if (!acceptedDetail?.room.allowed_actions.manage_photos) return
    const token = lifecycle.coordinator.acquire()
    if (!token) throw new Error("请等待当前操作完成")
    const epoch = clientStateEpochRef.current
    const scope = photoScopeRef.current
    const current = () => epoch === clientStateEpochRef.current && scope === photoScopeRef.current
    try {
      const result = await operation()
      if (!current()) return
      if (result.access_revision !== accessRevision) { purgePrivateHousing("mismatch"); return }
      queryClient.setQueryData(detailKey, result)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: housingKeys.overviews(accountId, accessRevision) }),
        queryClient.invalidateQueries({ queryKey: [...housingKeys.details(accountId, accessRevision), acceptedDetail.room.id] }),
      ])
    } catch (caught) {
      if (!current()) return
      if (isPrivateFailure(caught)) purgePrivateHousing("private")
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        await queryClient.invalidateQueries({ queryKey: [...housingKeys.details(accountId, accessRevision), acceptedDetail.room.id] })
      }
      throw caught
    } finally { lifecycle.coordinator.release(token) }
  }

  useEffect(() => {
    const scope = housingKeys.scope(accountId, accessRevision)
    const detailRoot = housingKeys.details(accountId, accessRevision)
    const cohortRoot = [...scope, "cohort-options"] as const
    const transferRoot = [...scope, "transfer-options"] as const
    const cleanupGeneration = ++queryCleanupGenerationRef.current
    return () => {
      queueMicrotask(() => {
        if (queryCleanupGenerationRef.current !== cleanupGeneration) return
        for (const queryKey of [detailRoot, cohortRoot, transferRoot]) {
          const cancellation = queryClient.cancelQueries({ queryKey })
          queryClient.removeQueries({ queryKey })
          void cancellation.finally(() => queryClient.removeQueries({ queryKey }))
        }
      })
    }
  }, [accessRevision, accountId, queryClient])

  if (!roomId || !user?.account_id) return <section className="housingState housingState--error" role="alert">房间详情不可访问</section>
  if (accessState === "private") return <section className="housingState housingState--error" role="alert"><strong>房间详情不可访问</strong><p>权限或房间范围已变化，请返回房态重新选择。</p><Link to="/housing">返回住宿房态</Link></section>
  if (accessState === "mismatch") return <section className="housingState housingState--error" role="alert"><strong>房态权限范围已更新</strong><p>旧房间数据已清除，请返回房态重新进入。</p><Link to="/housing">返回住宿房态</Link></section>

  const failure = catalog.error ?? detail.error
  if (failure && !acceptedDetail) return <section className="housingState housingState--error" role="alert"><strong>房间详情读取失败</strong><p>{errorMessage(failure)}</p><button type="button" onClick={() => void Promise.all([catalog.refetch(), detail.refetch()])}><RefreshCw aria-hidden="true" />重试</button></section>
  if (!acceptedDetail) return <section className="housingState" aria-live="polite"><span className="housingState__pulse" /><p>正在读取房间详情</p></section>

  const room = acceptedDetail.room
  return (
    <section ref={pageFocusRef} className="housingRoomDetail" data-housing-dialog-owner="true" tabIndex={-1}>
      <Link className="housingRoomDetail__back" to="/housing"><ArrowLeft aria-hidden="true" />返回住宿房态</Link>
      <header className="housingRoomDetail__header">
        <div><p>{room.building_name} · {room.floor_name}</p><h1>房间 {room.room_number}</h1><span>{room.status_text}</span></div>
        <div className="housingRoomDetail__actions">
          {room.allowed_actions.edit ? <button type="button" disabled={lifecycle.pending || !editReady} onClick={() => {
            if (!editReady || lifecycle.coordinator.isPending()) return
            setEditError(null); setEditNotice(null); setEditState({ scope: editScope, baseVersion: room.version })
          }}>编辑房间</button> : null}
          {room.allowed_actions.complete_cleaning ? <button type="button" disabled={lifecycle.pending} onClick={lifecycle.openCompleteCleaning}>完成保洁</button> : null}
          {room.allowed_actions.create_stay ? <button type="button" disabled={lifecycle.pending} onClick={lifecycle.openCreateStay}><CalendarPlus aria-hidden="true" />安排入住</button> : null}
          {room.allowed_actions.report_issue ? <button type="button" disabled={lifecycle.pending} onClick={lifecycle.openReportIssue}><Wrench aria-hidden="true" />上报问题</button> : null}
        </div>
      </header>
      {editNotice && <p role="status">{editNotice}</p>}
      {room.service_status === "problem" ? <p className="housingRoomDetail__warning"><AlertTriangle aria-hidden="true" />该房间当前为问题房，新增入住已暂停。</p> : null}
      <dl className="housingRoomDetail__facts">
        <div><dt>保洁状态</dt><dd>{room.cleaning_status === "pending" ? "退房待保洁" : "无退房待保洁"}</dd></div>
        <div><dt>保洁频率</dt><dd>{roomCleaningFrequencyLabel(room)}</dd></div>
        <div><dt>房间照片</dt><dd>{room.photo_count ?? 0} 张</dd></div>
        <div><dt>房型</dt><dd>{room.room_type === "single" ? "单人间" : room.room_type === "double" ? "双人间" : room.room_type === "triple" ? "三人间" : room.room_type === "functional" ? "功能间" : "自定义房型"}</dd></div>
        <div><dt>当前入住</dt><dd>{room.current_occupants} / {room.capacity}</dd></div>
        <div><dt>剩余容量</dt><dd>{room.remaining_capacity}</dd></div>
        <div><dt>入住性别</dt><dd>{room.current_gender === "male" ? "男住" : room.current_gender === "female" ? "女住" : "未指定"}</dd></div>
        <div><dt>未来预约</dt><dd>{room.future_reservation_count}</dd></div>
        <div><dt>数据截至</dt><dd>{formatShanghaiDate(acceptedDetail.as_of)}</dd></div>
      </dl>
      <HousingRoomCardsPanel key={`cards:${requestScope}`} roomId={room.id} />
      <HousingPropertyChangesPanel mode="submit" key={`property:${requestScope}`} room={acceptedDetail} onChanged={() => {
        void queryClient.invalidateQueries({ queryKey: housingKeys.scope(accountId, accessRevision) })
        void queryClient.invalidateQueries({ queryKey: housingKeys.catalog(accountId), exact: true })
      }} />
      <HousingRoomPhotos key={photoScope} scope={photoScope} detail={acceptedDetail} pending={lifecycle.pending} onUpload={(slot, file, version) => mutatePhoto(() => uploadRoomPhoto(room.id, slot, file, version))} onDelete={(photoId, version) => mutatePhoto(() => deleteRoomPhoto(room.id, photoId, version))} onPrivateFailure={handleLifecyclePrivateFailure} />
      <div className="housingRoomDetail__grid">
        <StayTimeline title="当前入住" stays={acceptedDetail.current_stays} pending={lifecycle.pending} onAction={lifecycle.openStayAction} />
        <StayTimeline title="未来预约" stays={acceptedDetail.future_reservations} pending={lifecycle.pending} onAction={lifecycle.openStayAction} />
        <StayTimeline title="住宿历史" stays={acceptedDetail.history} pending={lifecycle.pending} onAction={lifecycle.openStayAction} />
        {acceptedDetail.can_view_voided_history ? <label><input type="checkbox" checked={includeVoided} disabled={lifecycle.pending} onChange={(event) => setVoidedViewScope(event.target.checked ? requestScope : null)} />查看已作废历史</label> : null}
        {includeVoided && acceptedDetail.can_view_voided_history ? <StayTimeline title="已作废住宿历史" stays={acceptedDetail.voided_history ?? []} pending={lifecycle.pending} onAction={lifecycle.openStayAction} /> : null}
        <TransferTimeline transfers={acceptedDetail.transfers} />
        <IssueTimeline issues={acceptedDetail.issues} pending={lifecycle.pending} onResolve={lifecycle.openResolveIssue} />
      </div>
      {lifecycle.dialogs}
      {editVisible && catalog.data ? <div hidden={!editReady} inert={!editReady}><RoomDialog catalog={catalog.data} room={room} pending={lifecycle.pending || !editReady} error={editError} focusFallbackRef={pageFocusRef} onClose={() => {
        if (!lifecycle.coordinator.isPending()) { setEditState(null); setEditError(null) }
      }} onSubmit={(input) => { void submitRoomEdit(input as HousingRoomUpdate) }} /></div> : null}
    </section>
  )
}
