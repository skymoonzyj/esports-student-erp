import type { RefObject } from "react"

import { HousingDialogShell } from "./HousingDialogShell"
import { HOUSING_BULK_LABELS } from "./selection"
import type { HousingBulkAction } from "./selection"

export function HousingBulkActions({ action, count, pending, problems, onActionChange, onClear, onConfirm }: {
  action: HousingBulkAction
  count: number
  pending: boolean
  problems: readonly string[]
  onActionChange: (action: HousingBulkAction) => void
  onClear: () => void
  onConfirm: () => void
}) {
  return <><section className="housingCatalog__batch housingBulkActions" aria-label="房间批量操作">
    <label>批量操作<select value={action} disabled={pending} onChange={(event) => onActionChange(event.currentTarget.value as HousingBulkAction)}>
      {Object.entries(HOUSING_BULK_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    {count > 0 ? <><span aria-label={`跨页已选总数 ${count} 间`}><span>已选择 {count} 间房间</span><small>跨页总数 · 最多 500 间</small></span><div className="housingCatalog__batchActions">
      <button type="button" disabled={pending} onClick={onClear}>清空选择</button>
      <button type="button" disabled={pending || problems.length > 0} onClick={onConfirm}>批量{action === "verify" ? "核对" : HOUSING_BULK_LABELS[action]} {count} 间房</button>
    </div></> : <span>可先勾选房间，再选择批量操作 · 跨页最多 500 间</span>}
  </section>{problems.length ? <div className="housingCatalog__notice" role="alert"><p>所选房间不符合当前操作，请更换操作或取消对应房间。</p><ul>{problems.map((problem, index) => <li key={index}>{problem}</li>)}</ul></div> : null}</>
}

export function HousingBulkConfirmation({ action, count, pending, error, details, blocked, focusFallbackRef, onClose, onConfirm }: {
  action: HousingBulkAction
  count: number
  pending: boolean
  blocked: boolean
  error: string | null
  details: readonly string[]
  focusFallbackRef: RefObject<HTMLElement | null>
  onClose: () => void
  onConfirm: () => void
}) {
  return <HousingDialogShell title="确认批量操作" pending={pending} error={error} focusFallbackRef={focusFallbackRef} onClose={onClose}>
    <p>将对跨页选择的 {count} 间房执行“{HOUSING_BULK_LABELS[action]}”。</p>
    <p>任一房间不符合条件时整批不修改。</p>
    <p>{action === "verify" ? "本次仅完成核对，不改变服务状态、保洁、入住或预约。" : "设置服务状态将同步完成核对，不改变保洁、入住或预约。"}退房待保洁房间仍需完成保洁后才能安排入住。</p>
    {details.length ? <ul aria-label="房间冲突原因">{details.map((detail, index) => <li key={index}>{detail}</li>)}</ul> : null}
    <footer className="housingDialog__actions"><button type="button" disabled={pending} onClick={onClose}>取消</button><button type="button" disabled={pending || count === 0 || blocked} onClick={onConfirm}>确认执行</button></footer>
  </HousingDialogShell>
}
