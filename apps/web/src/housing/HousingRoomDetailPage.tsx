import { useQuery, useQueryClient } from "@tanstack/react-query"
import { AlertTriangle, ArrowLeft, CalendarPlus, RefreshCw, Wrench } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDateTime } from "../teaching/datetime"
import { getHousingCatalog, getHousingRoomDetail } from "./api"
import type { HousingRoomIssue, HousingStay, HousingTransfer } from "./api"
import { housingKeys } from "./housingKeys"
import { STAY_ACTION_LABELS, useHousingRoomLifecycle } from "./useHousingRoomLifecycle"
import type { StayActionKind } from "./useHousingRoomLifecycle"

import "./housing.css"

const PURPOSE_LABELS = { permanent: "常住", camp: "随营", temporary: "临时住宿" } as const
const PERSON_TYPE_LABELS = {
  student: "学员",
  coach: "教练",
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
              <header><div><strong>{stay.person_name}</strong><span>{PERSON_TYPE_LABELS[stay.person_type]} · {stay.sex === "male" ? "男" : "女"}</span></div><span>{STATUS_LABELS[stay.status]}</span></header>
              <dl>
                <div><dt>住宿类型</dt><dd>{PURPOSE_LABELS[stay.stay_purpose]}</dd></div>
                <div><dt>计划入住</dt><dd>{formatShanghaiDateTime(stay.planned_check_in_at)}</dd></div>
                {stay.planned_check_out_at ? <div><dt>计划离开</dt><dd>{formatShanghaiDateTime(stay.planned_check_out_at)}</dd></div> : null}
                {stay.actual_check_in_at ? <div><dt>实际入住</dt><dd>{formatShanghaiDateTime(stay.actual_check_in_at)}</dd></div> : null}
                {stay.actual_check_out_at ? <div><dt>实际离开</dt><dd>{formatShanghaiDateTime(stay.actual_check_out_at)}</dd></div> : null}
              </dl>
              {stay.notes ? <p>{stay.notes}</p> : null}
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
      {transfers.length === 0 ? <p className="housingRoomDetail__empty">暂无记录</p> : <div className="housingRoomDetail__cards">{transfers.map((transfer) => <article className="housingSimpleRecord" key={transfer.id}><strong>{transfer.reason}</strong><span>{formatShanghaiDateTime(transfer.transferred_at)}</span></article>)}</div>}
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
      {issues.length === 0 ? <p className="housingRoomDetail__empty">暂无记录</p> : <div className="housingRoomDetail__cards">{issues.map((issue) => <article className="housingSimpleRecord" key={issue.id}><header><strong>{issue.description}</strong><span>{issue.status === "open" ? "待处理" : "已解决"}</span></header><span>{formatShanghaiDateTime(issue.reported_at)}</span>{issue.resolution_note ? <p>{issue.resolution_note}</p> : null}{issue.allowed_actions.resolve ? <button type="button" disabled={pending} onClick={() => onResolve(issue)}>解决问题 {issue.description}</button> : null}</article>)}</div>}
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
  const [accessState, setAccessState] = useState<"active" | "private" | "mismatch">("active")
  const housingRoot = useMemo(() => housingKeys.root(accountId), [accountId])
  const catalog = useQuery({
    queryKey: housingKeys.catalog(accountId),
    queryFn: getHousingCatalog,
    enabled: Boolean(user?.account_id && roomId && accessState === "active"),
  })
  const accessRevision = catalog.data?.access_revision ?? "pending"
  const detailKey = housingKeys.detail(accountId, accessRevision, roomId ?? "missing")
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: ({ signal }) => getHousingRoomDetail(roomId ?? "", signal),
    enabled: Boolean(user?.account_id && roomId && catalog.data && accessState === "active"),
  })
  const privateFailure = [catalog.error, detail.error].find(isPrivateFailure) ?? null
  const revisionMismatch = Boolean(detail.data && catalog.data && detail.data.access_revision !== catalog.data.access_revision)
  const acceptedDetail = !revisionMismatch && detail.data?.room.allowed_actions.view_detail ? detail.data : undefined

  const purgePrivateHousing = useCallback((nextState: "private" | "mismatch") => {
    clientStateEpochRef.current += 1
    setAccessState(nextState)
    const cancellation = queryClient.cancelQueries({ queryKey: housingRoot })
    queryClient.removeQueries({ queryKey: housingRoot })
    void cancellation.finally(() => queryClient.removeQueries({ queryKey: housingRoot }))
  }, [housingRoot, queryClient])

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
    accessActive: accessState === "active",
    clientStateEpochRef,
    focusFallbackRef: pageFocusRef,
    onPrivateFailure: handleLifecyclePrivateFailure,
    onAccessRevisionMismatch: handleLifecycleRevisionMismatch,
  })

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
  if (failure) return <section className="housingState housingState--error" role="alert"><strong>房间详情读取失败</strong><p>{errorMessage(failure)}</p><button type="button" onClick={() => void Promise.all([catalog.refetch(), detail.refetch()])}><RefreshCw aria-hidden="true" />重试</button></section>
  if (!acceptedDetail) return <section className="housingState" aria-live="polite"><span className="housingState__pulse" /><p>正在读取房间详情</p></section>

  const room = acceptedDetail.room
  return (
    <section ref={pageFocusRef} className="housingRoomDetail" data-housing-dialog-owner="true" tabIndex={-1}>
      <Link className="housingRoomDetail__back" to="/housing"><ArrowLeft aria-hidden="true" />返回住宿房态</Link>
      <header className="housingRoomDetail__header">
        <div><p>{room.building_name} · {room.floor_name}</p><h1>房间 {room.room_number}</h1><span>{room.status_text}</span></div>
        <div className="housingRoomDetail__actions">
          {room.allowed_actions.create_stay ? <button type="button" disabled={lifecycle.pending} onClick={lifecycle.openCreateStay}><CalendarPlus aria-hidden="true" />安排入住</button> : null}
          {room.allowed_actions.report_issue ? <button type="button" disabled={lifecycle.pending} onClick={lifecycle.openReportIssue}><Wrench aria-hidden="true" />上报问题</button> : null}
        </div>
      </header>
      {room.service_status === "problem" ? <p className="housingRoomDetail__warning"><AlertTriangle aria-hidden="true" />该房间当前为问题房，新增入住已暂停。</p> : null}
      <dl className="housingRoomDetail__facts">
        <div><dt>房型</dt><dd>{room.room_type === "single" ? "单人间" : room.room_type === "double" ? "双人间" : room.room_type === "triple" ? "三人间" : room.room_type === "functional" ? "功能间" : "自定义房型"}</dd></div>
        <div><dt>当前入住</dt><dd>{room.current_occupants} / {room.capacity}</dd></div>
        <div><dt>剩余容量</dt><dd>{room.remaining_capacity}</dd></div>
        <div><dt>入住性别</dt><dd>{room.current_gender === "male" ? "男住" : room.current_gender === "female" ? "女住" : "未指定"}</dd></div>
        <div><dt>未来预约</dt><dd>{room.future_reservation_count}</dd></div>
        <div><dt>数据截至</dt><dd>{formatShanghaiDateTime(acceptedDetail.as_of)}</dd></div>
      </dl>
      <div className="housingRoomDetail__grid">
        <StayTimeline title="当前入住" stays={acceptedDetail.current_stays} pending={lifecycle.pending} onAction={lifecycle.openStayAction} />
        <StayTimeline title="未来预约" stays={acceptedDetail.future_reservations} pending={lifecycle.pending} onAction={lifecycle.openStayAction} />
        <StayTimeline title="住宿历史" stays={acceptedDetail.history} pending={lifecycle.pending} onAction={lifecycle.openStayAction} />
        <TransferTimeline transfers={acceptedDetail.transfers} />
        <IssueTimeline issues={acceptedDetail.issues} pending={lifecycle.pending} onResolve={lifecycle.openResolveIssue} />
      </div>
      {lifecycle.dialogs}
    </section>
  )
}
