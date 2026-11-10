import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import type { FormEvent, RefObject } from "react"

import { ProblemDetailsError } from "../api/client"
import { isDateInput } from "../shared/date"
import { getHousingTransferOptions } from "./api"
import type {
  HousingCancelReservation,
  HousingCheckoutConfirmation,
  HousingCheckoutRequest,
  HousingConfirmCheckIn,
  HousingStay,
  HousingStayUpdate,
  HousingPlannedDepartureUpdate,
  HousingStayDurationMode,
  HousingTransferStay,
} from "./api"
import { HousingDialogShell } from "./HousingDialogShell"
import { formatShanghaiDate } from "../teaching/datetime"
import { fromShanghaiDateInput, toShanghaiDateInput } from "./housingDateTime"
import { housingKeys } from "./housingKeys"

type BaseDialogProps<T> = {
  stay: HousingStay
  pending: boolean
  error: string | null
  focusFallbackRef?: RefObject<HTMLElement | null>
  onClose: () => void
  onSubmit: (input: T) => void
}

function clean(value: string): string | null {
  return value.trim() || null
}

export function EditReservationDialog({ stay, pending, error, focusFallbackRef, onClose, onSubmit }: BaseDialogProps<HousingStayUpdate>) {
  const [checkIn, setCheckIn] = useState(toShanghaiDateInput(stay.planned_check_in_at))
  const [checkOut, setCheckOut] = useState(stay.planned_check_out_at ? toShanghaiDateInput(stay.planned_check_out_at) : "")
  const [notes, setNotes] = useState(stay.notes ?? "")
  const fixedTerm = stay.duration_mode === "fixed_term"
  const allowed = stay.allowed_actions.edit_reservation
  const valid = Boolean(checkIn && (!fixedTerm || (checkOut && checkOut > checkIn)))
  function submit(event: FormEvent): void {
    event.preventDefault()
    if (!valid || !allowed) return
    onSubmit({ version: stay.version, planned_check_in_at: fromShanghaiDateInput(checkIn), planned_check_out_at: fixedTerm ? fromShanghaiDateInput(checkOut) : null, notes: clean(notes) })
  }
  return <HousingDialogShell title={`编辑预约 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || !allowed}><label>计划入住日期<input data-autofocus required type="date" value={checkIn} onChange={(event) => setCheckIn(event.currentTarget.value)} /></label>{fixedTerm ? <label>计划离开日期<input required type="date" value={checkOut} onChange={(event) => setCheckOut(event.currentTarget.value)} /></label> : null}<label>备注<textarea maxLength={500} value={notes} onChange={(event) => setNotes(event.currentTarget.value)} /></label><footer><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={!valid || !allowed}>保存预约</button></footer></fieldset></form></HousingDialogShell>
}

export function EditPlannedDepartureDialog({ stay, pending, error, focusFallbackRef, onClose, onSubmit }: BaseDialogProps<HousingPlannedDepartureUpdate>) {
  const [durationMode, setDurationMode] = useState<HousingStayDurationMode | "">("fixed_term")
  const [checkOut, setCheckOut] = useState(stay.planned_check_out_at ? toShanghaiDateInput(stay.planned_check_out_at) : "")
  const fixedTerm = durationMode !== "long_term"
  const allowed = stay.allowed_actions.edit_planned_departure
  const canLongTerm = stay.stay_purpose === "permanent"
  const validEnd = Boolean(isDateInput(checkOut) && new Date(fromShanghaiDateInput(checkOut)) > new Date(stay.planned_check_in_at) && stay.actual_check_in_at && new Date(fromShanghaiDateInput(checkOut)) > new Date(stay.actual_check_in_at))
  const valid = Boolean(durationMode && (durationMode === "long_term" ? canLongTerm : validEnd))
  function submit(event: FormEvent): void {
    event.preventDefault()
    if (!valid || !allowed || pending || !durationMode) return
    onSubmit({ version: stay.version, duration_mode: durationMode, planned_check_out_at: fixedTerm ? fromShanghaiDateInput(checkOut) : null })
  }
  return <HousingDialogShell title={`修改计划离开 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || !allowed}><p>当前期限：{stay.duration_mode === "long_term" ? "长期住宿" : "固定期限"}。修改计划不会办理退房或释放床位。</p><label>住宿期限<select data-autofocus required value={durationMode} onChange={(event) => setDurationMode(event.currentTarget.value as HousingStayDurationMode | "")}><option value="">请选择住宿期限</option><option value="fixed_term">固定期限</option>{canLongTerm ? <option value="long_term">长期住宿（不设计划离开日期）</option> : null}</select></label>{fixedTerm ? <label>计划离开日期<input required type="date" value={checkOut} onChange={(event) => setCheckOut(event.currentTarget.value)} /></label> : null}<footer><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={!valid || !allowed}>保存计划离开</button></footer></fieldset></form></HousingDialogShell>
}

export function ConfirmCheckInDialog({ stay, pending, error, defaultTime, focusFallbackRef, onClose, onSubmit }: BaseDialogProps<HousingConfirmCheckIn> & { defaultTime: string }) {
  const [actualAt, setActualAt] = useState(toShanghaiDateInput(defaultTime))
  const allowed = stay.allowed_actions.confirm_check_in
  function submit(event: FormEvent): void { event.preventDefault(); if (actualAt && allowed) onSubmit({ version: stay.version, actual_check_in_at: fromShanghaiDateInput(actualAt) }) }
  return <HousingDialogShell title={`确认入住 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || !allowed}><label>实际入住日期<input data-autofocus required type="date" value={actualAt} onChange={(event) => setActualAt(event.currentTarget.value)} /></label><footer><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={!actualAt || !allowed}>确认入住</button></footer></fieldset></form></HousingDialogShell>
}

export function CancelReservationDialog({ stay, pending, error, focusFallbackRef, onClose, onSubmit }: BaseDialogProps<HousingCancelReservation>) {
  const [reason, setReason] = useState("")
  const allowed = stay.allowed_actions.cancel_reservation
  function submit(event: FormEvent): void { event.preventDefault(); if (reason.trim() && allowed) onSubmit({ version: stay.version, reason: reason.trim() }) }
  return <HousingDialogShell title={`取消预约 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || !allowed}><label>取消原因<textarea data-autofocus required maxLength={500} value={reason} onChange={(event) => setReason(event.currentTarget.value)} /></label><footer><button type="button" onClick={onClose}>返回</button><button type="submit" disabled={!reason.trim() || !allowed}>确认取消预约</button></footer></fieldset></form></HousingDialogShell>
}

export function TransferStayDialog({ accountId, accessRevision, stay, pending, error, defaultTime, focusFallbackRef, onClose, onSubmit, onAccessRevisionMismatch, onPrivateFailure }: BaseDialogProps<HousingTransferStay> & { accountId: string; accessRevision: string; defaultTime: string; onAccessRevisionMismatch: () => void; onPrivateFailure?: (error: unknown) => void }) {
  const [targetRoomId, setTargetRoomId] = useState("")
  const [transferredAt, setTransferredAt] = useState(toShanghaiDateInput(defaultTime))
  const [reason, setReason] = useState("")
  const [mismatch, setMismatch] = useState(false)
  const allowed = stay.allowed_actions.transfer
  const options = useQuery({ queryKey: housingKeys.transferOptions(accountId, accessRevision, stay.id), queryFn: ({ signal }) => getHousingTransferOptions(stay.id, signal), enabled: allowed, retry: false, staleTime: 0, gcTime: 0 })
  useEffect(() => {
    if (!options.data || options.data.access_revision === accessRevision || mismatch) return
    setMismatch(true)
    setTargetRoomId("")
    onAccessRevisionMismatch()
  }, [accessRevision, mismatch, onAccessRevisionMismatch, options.data])
  useEffect(() => {
    if (
      options.error instanceof ProblemDetailsError
      && (options.error.problem.status === 403 || options.error.problem.status === 404)
    ) onPrivateFailure?.(options.error)
  }, [onPrivateFailure, options.error])
  const items = options.data?.access_revision === accessRevision ? options.data.items : []
  const valid = Boolean(items.some((room) => room.room_id === targetRoomId && room.advisory_compatible) && transferredAt && reason.trim() && !mismatch && allowed && !options.isError && !pending)
  function submit(event: FormEvent): void { event.preventDefault(); if (valid) onSubmit({ version: stay.version, target_room_id: targetRoomId, transferred_at: fromShanghaiDateInput(transferredAt), reason: reason.trim() }) }
  return <HousingDialogShell title={`调房 · ${stay.person_name}`} pending={pending} error={error ?? (options.error instanceof Error ? options.error.message : null)} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || mismatch || !allowed}><fieldset className="housingResidentOptions"><legend>目标房间</legend>{items.map((room) => <label key={room.room_id} aria-disabled={!room.advisory_compatible}><input data-autofocus type="radio" name="target-room" aria-label={`${room.building_label} · ${room.floor_label} · ${room.room_label}`} checked={targetRoomId === room.room_id} disabled={!room.advisory_compatible} onChange={() => setTargetRoomId(room.room_id)} /><span><strong>{room.building_label} · {room.floor_label} · {room.room_label}</strong><small>{room.status_text} · {room.current_occupants}/{room.capacity}</small></span></label>)}</fieldset><label>调房日期<input required type="date" value={transferredAt} onChange={(event) => setTransferredAt(event.currentTarget.value)} /></label><label>调房原因<textarea required maxLength={500} value={reason} onChange={(event) => setReason(event.currentTarget.value)} /></label><footer><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={!valid}>确认调房</button></footer></fieldset></form></HousingDialogShell>
}

export function RequestCheckoutDialog({ stay, pending, error, defaultTime, focusFallbackRef, onClose, onSubmit }: BaseDialogProps<HousingCheckoutRequest> & { defaultTime: string }) {
  const [actualAt, setActualAt] = useState(toShanghaiDateInput(defaultTime))
  const allowed = stay.allowed_actions.request_checkout
  function submit(event: FormEvent): void { event.preventDefault(); if (actualAt && allowed) onSubmit({ version: stay.version, actual_check_out_at: fromShanghaiDateInput(actualAt) }) }
  return <HousingDialogShell title={`申请退房 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || !allowed}><p className="housingDialog__warning">提交后房间仍保持占用，必须由管理员再次确认退房才会释放容量。</p><label>实际离开日期<input data-autofocus required type="date" value={actualAt} onChange={(event) => setActualAt(event.currentTarget.value)} /></label><footer><button type="button" onClick={onClose}>取消</button><button type="submit" disabled={!actualAt || !allowed}>申请退房</button></footer></fieldset></form></HousingDialogShell>
}

export function ConfirmCheckoutDialog({ stay, pending, error, focusFallbackRef, onClose, onSubmit }: BaseDialogProps<HousingCheckoutConfirmation>) {
  const allowed = stay.allowed_actions.confirm_checkout
  function submit(event: FormEvent): void { event.preventDefault(); if (allowed) onSubmit({ version: stay.version }) }
  return <HousingDialogShell title={`二次确认退房 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}><form onSubmit={submit}><fieldset disabled={pending || !allowed}><p className="housingDialog__warning">确认后将立即释放房间容量，请核对以下信息。</p><dl className="housingCheckoutSummary"><div><dt>入住人员</dt><dd>{stay.person_name}</dd></div><div><dt>房间</dt><dd>{stay.building_name_snapshot} · {stay.floor_name_snapshot} · {stay.room_name_snapshot}</dd></div><div><dt>实际离开</dt><dd>{stay.checkout_requested_actual_at ? formatShanghaiDate(stay.checkout_requested_actual_at) : "未填写"}</dd></div></dl><footer><button type="button" onClick={onClose}>返回核对</button><button data-autofocus type="submit" disabled={!allowed}>二次确认退房</button></footer></fieldset></form></HousingDialogShell>
}
