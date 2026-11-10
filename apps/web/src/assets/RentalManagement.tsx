import { useState } from "react"
import type { ReactNode } from "react"
import type { Asset } from "./api"
import { rentalEligible, rentalReturnEligible } from "./rentalDates"
import { RentalRenewDialog } from "./RentalRenewDialog"
import { RentalHistoryDialog, RentalReturnDialog } from "./RentalDetails"
import { todayDateInput } from "../shared/date"

export function RentalManagement({ items, canManage, refresh, children }: { items: Asset[]; canManage: boolean; refresh: () => Promise<void>; children: (actions: (asset: Asset) => ReactNode) => ReactNode }) {
  const signature = JSON.stringify(items.map((asset) => [asset.id, asset.rental_revision, asset.status, asset.rental_returned_on]))
  const [selection, setSelection] = useState({ signature, ids: [] as string[] })
  const selected = signature === selection.signature ? items.filter((asset) => selection.ids.includes(asset.id) && rentalEligible(asset)) : []
  const [renewing, setRenewing] = useState<Asset[] | null>(null)
  const [history, setHistory] = useState<Asset | null>(null)
  const [returning, setReturning] = useState<Asset | null>(null)
  function clearSelection() { setSelection({ signature, ids: [] }) }
  async function afterWrite() { clearSelection(); await refresh() }
  function actions(asset: Asset) {
    return <>
      {canManage && rentalEligible(asset) ? <>
        <label className="assetCheckboxLabel"><input type="checkbox" aria-label={`选择续期 ${asset.asset_code}`} checked={selected.some((item) => item.id === asset.id)} disabled={selected.length >= 100 && !selected.some((item) => item.id === asset.id)} onChange={(event) => setSelection({ signature, ids: event.target.checked ? [...selected.map((item) => item.id), asset.id] : selected.filter((item) => item.id !== asset.id).map((item) => item.id) })} />选择</label>
        <button className="assetActionButton" type="button" onClick={() => setRenewing([asset])}>续期</button>
      </> : null}
      {canManage && asset.ownership_type === "rented" && !asset.rental_returned_on ? <button className="assetActionButton" type="button" disabled={!rentalReturnEligible(asset)} title={asset.rental_started_on && asset.rental_started_on > todayDateInput() ? "租赁尚未开始，暂无有效退租日期" : asset.current_assignment ? "请先完成使用人归还，再退租" : "结束供应方租赁"} onClick={() => setReturning(asset)}>退租</button> : null}
      {asset.ownership_type === "rented" ? <button className="assetActionButton" type="button" onClick={() => setHistory(asset)}>续期历史</button> : null}
    </>
  }
  return <>
    {canManage ? <div className="rentalToolbar"><span>仅选择本页有效租用资产，最多100件</span><button className="secondaryButton" type="button" disabled={!selected.length} onClick={() => setRenewing(selected)}>批量续期（{selected.length}）</button>{selected.length ? <button className="secondaryButton" type="button" onClick={clearSelection}>取消选择</button> : null}</div> : null}
    {children(actions)}
    {canManage && renewing ? <RentalRenewDialog assets={renewing} onClose={() => setRenewing(null)} onSuccess={afterWrite} onConflict={afterWrite} /> : null}
    {history ? <RentalHistoryDialog asset={history} onClose={() => setHistory(null)} /> : null}
    {canManage && returning ? <RentalReturnDialog asset={returning} onClose={() => setReturning(null)} onRefresh={afterWrite} /> : null}
  </>
}
