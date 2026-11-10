import { useQuery } from "@tanstack/react-query"
import { useState } from "react"
import type { FormEvent } from "react"
import { ProblemDetailsError } from "../api/client"
import { todayDateInput } from "../shared/date"
import type { Asset } from "./api"
import { listRentalRenewals, returnAssetRental } from "./rentalApi"
import { rentalPeriodLabels, validRentalDate } from "./rentalDates"
import { assetKeys } from "./keys"

export function RentalHistoryDialog({ asset, onClose }: { asset: Asset; onClose: () => void }) {
  const [page, setPage] = useState(1)
  const history = useQuery({ queryKey: [...assetKeys.root, "rental-renewals", asset.id, page], queryFn: () => listRentalRenewals(asset.id, page) })
  return <div className="assetDialogBackdrop"><section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="rental-history-title">
    <h2 id="rental-history-title">续期历史</h2><p>{asset.asset_code} · {asset.name}</p>
    {history.isPending ? <p>正在读取续期历史…</p> : null}
    {history.isError ? <p role="alert">续期历史读取失败，请重试。</p> : null}
    {history.data?.items.length === 0 ? <p>暂无续期历史</p> : null}
    <div className="rentalPreviews">{history.data?.items.map((item) => <article className="rentalPreview" key={item.id}>
      <strong>{rentalPeriodLabels[item.period]}</strong><span>{item.previous_expires_on} → {item.rental_expires_on}</span>
      <span>{item.actor_name ?? "操作人待确认"} · {new Date(item.created_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false })}</span>
    </article>)}</div>
    <div className="assetDialogActions"><button className="secondaryButton" type="button" disabled={page <= 1 || history.isFetching} onClick={() => setPage(page - 1)}>上一页历史</button><span>第 {page} 页 · 共 {history.data?.total ?? 0} 条</span><button className="secondaryButton" type="button" disabled={!history.data || page * history.data.page_size >= history.data.total || history.isFetching} onClick={() => setPage(page + 1)}>下一页历史</button><button className="primaryButton" type="button" onClick={onClose}>关闭</button></div>
  </section></div>
}

export function RentalReturnDialog({ asset, onClose, onRefresh }: { asset: Asset; onClose: () => void; onRefresh: () => Promise<void> }) {
  const [date, setDate] = useState(todayDateInput())
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const valid = validRentalDate(date) && date >= (asset.rental_started_on ?? "9999-12-31") && date <= todayDateInput() && !asset.current_assignment && !asset.rental_returned_on
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid || !confirmed || pending || conflict) return
    setPending(true); setError(null)
    try {
      await returnAssetRental(asset.id, { returned_on: date, expected_rental_revision: asset.rental_revision! })
      await onRefresh(); onClose()
    } catch (caught) {
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        setConflict(true); setError("租赁或使用人信息已变化，请关闭并重新选择资产。")
        await onRefresh()
      } else setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "退租结果未确认，请刷新台账核对后再操作。")
    } finally { setPending(false) }
  }
  return <div className="assetDialogBackdrop"><section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="rental-return-title"><h2 id="rental-return-title">退租确认</h2>
    <p>{asset.asset_code} · {asset.name}。退租将结束供应方租赁，不会代替使用人归还。退租后不可再次分配、续期或撤销。</p>
    <form className="assetDialogForm" onSubmit={(event) => void submit(event)}><div className="assetField"><label htmlFor="rental-returned-on">退租日期</label><input id="rental-returned-on" type="date" min={asset.rental_started_on ?? "0001-01-01"} max={todayDateInput()} required value={date} onChange={(event) => setDate(event.target.value)} /></div>
      <label className="assetCheckboxLabel"><input type="checkbox" checked={confirmed} disabled={pending || conflict} onChange={(event) => setConfirmed(event.target.checked)} />已确认使用人归还，结束租赁</label>
      {error ? <p className="formError" role="alert">{error}</p> : null}
      <div className="assetDialogActions"><button className="secondaryButton" type="button" disabled={pending} onClick={onClose}>取消</button><button className="primaryButton" type="submit" disabled={!valid || !confirmed || pending || conflict}>{pending ? "正在退租…" : "确认退租"}</button></div>
    </form>
  </section></div>
}
