import { useEffect, useRef, useState } from "react"
import type { ReactElement, RefObject } from "react"

import { formatShanghaiDate } from "../teaching/datetime"
import type { HousingStay, HousingTransfer, StayHistoryVoidRequest } from "./api"
import { HousingDialogShell } from "./HousingDialogShell"

function newOperationKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((byte) => byte.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

export function HousingHistoryVoidDialog({ stay, linkedTransfers, pending, error, focusFallbackRef, onSubmit, onClose }: {
  stay: HousingStay
  linkedTransfers: readonly HousingTransfer[]
  pending: boolean
  error: string | null
  focusFallbackRef: RefObject<HTMLElement | null>
  onSubmit: (payload: StayHistoryVoidRequest) => void
  onClose: () => void
}): ReactElement {
  const [reason, setReason] = useState("")
  const [reviewing, setReviewing] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [operationKey] = useState(newOperationKey)
  const [submitted, setSubmitted] = useState(false)
  const submittingRef = useRef(false)
  const confirmationRef = useRef<HTMLInputElement>(null)
  const trimmedReason = reason.trim()
  const validReason = trimmedReason.length >= 2 && trimmedReason.length <= 500 &&
    !/(?<![0-9])(?:1[3-9][0-9]{9}|[0-9]{17}[0-9Xx]|[0-9]{15})(?![0-9])/.test(trimmedReason)

  useEffect(() => {
    if (!pending && error) submittingRef.current = false
  }, [error, pending])

  useEffect(() => {
    if (reviewing) confirmationRef.current?.focus()
  }, [reviewing])

  function submit(): void {
    if (pending || submittingRef.current || !confirmed || !validReason) return
    submittingRef.current = true
    setSubmitted(true)
    onSubmit({ expected_version: stay.version, reason: trimmedReason, confirmed: true, idempotency_key: operationKey })
  }

  return (
    <HousingDialogShell title={`作废历史住宿 · ${stay.person_name}`} pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
      <fieldset disabled={pending}>
      {!reviewing ? <>
        <label>作废原因<textarea data-autofocus value={reason} maxLength={500} disabled={pending} onChange={(event) => setReason(event.target.value)} /></label>
        <p>请填写 2 至 500 字的核对原因，不要填写完整手机号或证件号码。</p>
        <footer className="housingDialog__actions">
          <button type="button" disabled={pending} onClick={onClose}>取消</button>
          <button type="button" disabled={pending || !validReason} onClick={() => setReviewing(true)}>下一步：核对历史住宿</button>
        </footer>
      </> : <>
        <dl className="housingCheckoutSummary">
          <div><dt>目标人员</dt><dd>{stay.person_name}</dd></div>
          <div><dt>历史房间</dt><dd>{stay.building_name_snapshot} · {stay.floor_name_snapshot} · {stay.room_name_snapshot}</dd></div>
          <div><dt>实际入住</dt><dd>{stay.actual_check_in_at ? formatShanghaiDate(stay.actual_check_in_at) : "无实际入住记录"}</dd></div>
          <div><dt>实际离开</dt><dd>{stay.actual_check_out_at ? formatShanghaiDate(stay.actual_check_out_at) : "无实际离开记录"}</dd></div>
          <div><dt>作废原因</dt><dd>{trimmedReason}</dd></div>
        </dl>
        <section aria-label="关联调房">
          <h3>关联调房</h3>
          {linkedTransfers.length ? linkedTransfers.map((transfer) => <p key={transfer.id}>
            {transfer.from_stay_id === stay.id ? "来源住宿" : "去向住宿"} · {formatShanghaiDate(transfer.transferred_at)} · {transfer.reason}
          </p>) : <p>当前房间详情没有关联调房记录</p>}
        </section>
        <p className="housingDialog__warning">只作废此条历史，不删除人员、账号和其他住宿</p>
        <p className="housingDialog__warning">已生成报送不会自动改变</p>
        <div className="housingResidentOptions"><label><input ref={confirmationRef} type="checkbox" checked={confirmed} disabled={pending} onChange={(event) => setConfirmed(event.target.checked)} />我已核对目标历史住宿并确认作废</label></div>
        {submitted ? <p>结果不确定时可重试同一操作；重新查询目标后才能修改原因。</p> : null}
        <footer className="housingDialog__actions">
          {!submitted ? <button type="button" disabled={pending} onClick={() => { setReviewing(false); setConfirmed(false) }}>返回修改原因</button> : null}
          <button type="button" disabled={pending} onClick={onClose}>{submitted ? "关闭并重新查询目标" : "取消"}</button>
          <button type="button" disabled={pending || !confirmed} onClick={submit}>确认作废历史住宿</button>
        </footer>
      </>}
      </fieldset>
    </HousingDialogShell>
  )
}
