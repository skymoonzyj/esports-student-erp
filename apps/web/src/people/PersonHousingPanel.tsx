import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { createHousingStay, transferHousingStay, type HousingStay, type HousingStayCreate, type HousingTransferStay } from "../housing/api"
import { HousingDialogShell } from "../housing/HousingDialogShell"
import { TransferStayDialog } from "../housing/HousingStayActionDialogs"
import { HousingStayDialog } from "../housing/HousingStayDialog"
import { housingKeys } from "../housing/housingKeys"
import { useHousingMutationCoordinator } from "../housing/useHousingMutationCoordinator"
import "../housing/housing.css"
import { formatDateOnly } from "../shared/date"
import { getPersonHousing, getPersonHousingCheckInOptions, personHousingKeys, type PersonHousingStay } from "./housingApi"

type Props = { personId: string; accountId: string; onAccessRevoked?: () => void }
type Dialog = { type: "create"; revision: string } | { type: "transfer"; revision: string; stay: HousingStay }

function isPrivateFailure(error: unknown): boolean {
  return error instanceof ProblemDetailsError && (error.problem.status === 403 || error.problem.status === 404)
}

function StaySummary({ value }: { value: PersonHousingStay }) {
  const statuses = { planned: "预约入住", checked_in: "已入住", checkout_pending: "待确认退房", checked_out: "已退房", cancelled: "已取消" }
  return <>
    <p>{value.building_name} · {value.floor_name} · {value.room_name}</p>
    <p>{statuses[value.status]}</p>
    <p>入住日期：{formatDateOnly(value.actual_check_in_at ?? value.planned_check_in_at)}{value.planned_check_out_at ? ` · 计划离开：${formatDateOnly(value.planned_check_out_at)}` : " · 长期居住"}</p>
  </>
}

export function PersonHousingPanel(props: Props) {
  return <ScopedPersonHousingPanel key={`${props.accountId}:${props.personId}`} {...props} />
}

function ScopedPersonHousingPanel({ personId, accountId, onAccessRevoked }: Props) {
  const client = useQueryClient()
  const coordinator = useHousingMutationCoordinator()
  const active = useRef(false)
  const epoch = useRef(0)
  const focusFallbackRef = useRef<HTMLElement>(null)
  const [revoked, setRevoked] = useState(false)
  const [dialog, setDialog] = useState<Dialog | null>(null)
  const [roomId, setRoomId] = useState("")
  const [roomConfirmed, setRoomConfirmed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    active.current = true
    return () => { active.current = false; epoch.current += 1 }
  }, [])
  const summary = useQuery({
    queryKey: personHousingKeys.summary(accountId, personId),
    queryFn: ({ signal }) => getPersonHousing(personId, signal),
    retry: false, staleTime: 0, gcTime: 0, enabled: !revoked,
  })
  const revision = summary.data?.access_revision ?? ""
  const housingRevision = summary.data?.housing_access_revision ?? ""
  const checkInOptions = useQuery({
    queryKey: personHousingKeys.options(accountId, personId, dialog?.revision ?? ""),
    queryFn: ({ signal }) => getPersonHousingCheckInOptions(personId, signal),
    enabled: !revoked && dialog?.type === "create" && dialog.revision === revision && summary.data?.allowed_actions.create_stay === true,
    retry: false, staleTime: 0, gcTime: 0,
  })
  const revoke = useCallback(() => {
    if (!active.current) return
    epoch.current += 1
    setRevoked(true)
    setDialog(null)
    setRoomId("")
    setRoomConfirmed(false)
    setError(null)
    for (const key of [personHousingKeys.summary(accountId, personId), housingKeys.root(accountId)]) {
      void client.cancelQueries({ queryKey: key })
      client.removeQueries({ queryKey: key })
    }
    onAccessRevoked?.()
  }, [accountId, client, onAccessRevoked, personId])
  const mismatch = useCallback(() => {
    if (!active.current) return
    epoch.current += 1
    setDialog(null)
    setRoomId("")
    setRoomConfirmed(false)
    setError(null)
    void client.invalidateQueries({ queryKey: personHousingKeys.summary(accountId, personId), exact: true })
  }, [accountId, client, personId])
  const denied = isPrivateFailure(summary.error)
  useEffect(() => {
    if (denied || isPrivateFailure(checkInOptions.error)) revoke()
  }, [checkInOptions.error, denied, revoke])
  const currentStay = summary.data?.management_stay
  const authorized = Boolean(dialog && !revoked && !denied && dialog.revision === revision && (
    dialog.type === "create" ? summary.data?.allowed_actions.create_stay : currentStay?.id === dialog.stay.id && currentStay?.allowed_actions.transfer
  ))
  useEffect(() => {
    if (dialog && !authorized) {
      epoch.current += 1
      setDialog(null)
      setError(null)
    }
  }, [authorized, dialog])
  useEffect(() => {
    if (dialog?.type === "create" && checkInOptions.data && (checkInOptions.data.access_revision !== dialog.revision || checkInOptions.data.housing_access_revision !== housingRevision)) mismatch()
  }, [checkInOptions.data, dialog, housingRevision, mismatch])
  const options = checkInOptions.data?.access_revision === revision && checkInOptions.data.housing_access_revision === housingRevision && checkInOptions.data.resident.person_id === personId ? checkInOptions.data : undefined
  const room = options?.items.find((item) => item.id === roomId && item.allowed_actions.create_stay)

  function close(): void {
    if (!active.current || coordinator.isPending()) return
    setDialog(null)
    setError(null)
  }

  async function mutate(operation: () => Promise<unknown>): Promise<void> {
    if (!active.current || !authorized) return
    const token = coordinator.acquire()
    if (!token) return
    const openingEpoch = epoch.current
    const isCurrent = () => active.current && openingEpoch === epoch.current
    setError(null)
    const dialogOptionKeys = dialog?.type === "transfer"
      ? [housingKeys.transferOptions(accountId, housingRevision, dialog.stay.id)]
      : [personHousingKeys.options(accountId, personId, revision), ...(room ? [housingKeys.cohortOptions(accountId, housingRevision, room.id)] : [])]
    const isDialogOption = (key: readonly unknown[]) => dialogOptionKeys.some((candidate) => candidate.length === key.length && candidate.every((value, index) => value === key[index]))
    const refresh = (completed = false) => Promise.all([
      client.invalidateQueries({ queryKey: housingKeys.root(accountId), predicate: (query) => !completed || !isDialogOption(query.queryKey) }),
      client.invalidateQueries({ queryKey: personHousingKeys.root(accountId), predicate: (query) => !completed || !isDialogOption(query.queryKey) }),
      ...(completed ? dialogOptionKeys.map((key) => client.invalidateQueries({ queryKey: key, exact: true, refetchType: "none" })) : []),
    ])
    try {
      await operation()
      if (!isCurrent()) return
      // The completed stay/transfer makes its old options legitimately ineligible.
      // Close and stale those caches, but never refetch them as an open dialog.
      setDialog(null)
      await Promise.all(dialogOptionKeys.map((key) => client.cancelQueries({ queryKey: key, exact: true })))
      if (!isCurrent()) return
      await refresh(true)
      if (!isCurrent()) return
    } catch (caught) {
      if (!isCurrent()) return
      if (isPrivateFailure(caught)) { revoke(); return }
      setError(caught instanceof ProblemDetailsError ? caught.problem.status === 409 && caught.problem.code === "housing_stale_version" ? "数据已更新，请查看当前数据后再重试。" : caught.problem.detail : "操作暂时失败，请稍后重试")
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) await refresh()
    } finally { coordinator.release(token) }
  }

  function create(input: HousingStayCreate): void {
    if (dialog?.type !== "create" || !room || !options?.resident.housing_ready || input.person_id !== personId) return
    void mutate(() => createHousingStay(room.id, input))
  }

  function transfer(input: HousingTransferStay): void {
    if (dialog?.type !== "transfer") return
    void mutate(() => transferHousingStay(dialog.stay.id, input))
  }

  if (revoked || denied) return null
  return <section ref={focusFallbackRef} tabIndex={-1} data-housing-dialog-owner className="detailCard" aria-label="住宿信息">
    <div className="detailCardHeader"><div><p className="detailCardEyebrow">HOUSING</p><h2>住宿信息</h2></div><div>
      {summary.data?.allowed_actions.create_stay ? <button className="primaryButton" type="button" disabled={coordinator.pending} onClick={() => {
        if (!active.current || coordinator.isPending()) return
        setDialog({ type: "create", revision }); setRoomId(""); setRoomConfirmed(false); setError(null)
      }}>安排入住</button> : null}
      {currentStay?.allowed_actions.transfer ? <button className="secondaryButton" type="button" disabled={coordinator.pending} onClick={() => {
        if (!active.current || coordinator.isPending()) return
        setDialog({ type: "transfer", revision, stay: currentStay }); setError(null)
      }}>修改房间</button> : null}
    </div></div>
    {summary.isError && summary.data ? <p role="alert">住宿信息暂时无法刷新，现有草稿未清空。</p> : null}
    {!summary.data ? <p role={summary.isError ? "alert" : "status"}>{summary.isError ? "住宿信息暂时无法读取" : "正在读取住宿信息…"}</p> : <>
      <section aria-label="当前住宿"><h3>当前住宿</h3>{summary.data.current ? <StaySummary value={summary.data.current} /> : <p>当前未入住</p>}</section>
      {summary.data.planned ? <section aria-label="计划住宿"><h3>计划住宿（非当前入住）</h3><StaySummary value={summary.data.planned} /></section> : null}
    </>}
    {dialog?.type === "create" && authorized && (!roomConfirmed || !room || !options) ? <HousingDialogShell title="选择入住房间" pending={coordinator.pending} error={checkInOptions.isError ? "可入住房间暂时无法读取" : error} focusFallbackRef={focusFallbackRef} onClose={close}>
      {options ? <><label>选择房间<select data-autofocus value={roomId} onChange={(event) => { setRoomId(event.currentTarget.value); setRoomConfirmed(false) }}><option value="">请选择房间</option>{options.items.filter((item) => item.allowed_actions.create_stay).map((item) => <option key={item.id} value={item.id}>{item.building_name} · {item.floor_name} · {item.room_number}</option>)}</select></label>{options.items.length === 0 ? <p>暂无可安排入住的房间</p> : null}<footer><button type="button" onClick={close}>取消</button><button type="button" disabled={!room || !options.resident.housing_ready} onClick={() => { if (active.current && authorized && room) setRoomConfirmed(true) }}>下一步</button></footer></> : <p role="status">正在读取可入住房间…</p>}
    </HousingDialogShell> : null}
    {dialog?.type === "create" && authorized && roomConfirmed && room && options ? <HousingStayDialog key={room.id} accountId={accountId} accessRevision={housingRevision} room={room} preselectedResident={options.resident} pending={coordinator.pending} error={error} focusFallbackRef={focusFallbackRef} onClose={close} onSubmit={create} onAccessRevisionMismatch={mismatch} onPrivateFailure={revoke} /> : null}
    {dialog?.type === "transfer" && authorized ? <TransferStayDialog accountId={accountId} accessRevision={housingRevision} stay={dialog.stay} defaultTime={new Date().toISOString()} pending={coordinator.pending} error={error} focusFallbackRef={focusFallbackRef} onClose={close} onSubmit={transfer} onAccessRevisionMismatch={mismatch} onPrivateFailure={revoke} /> : null}
  </section>
}
