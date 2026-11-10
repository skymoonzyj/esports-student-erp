import { useState } from "react"
import type { FormEvent } from "react"
import { ProblemDetailsError } from "../api/client"
import { todayDateInput } from "../shared/date"
import type { Asset } from "./api"
import { renewAssetRentals } from "./rentalApi"
import type { RentalRenewInput } from "./rentalApi"
import { rentalEligible, rentalPeriodLabels, renewalDate } from "./rentalDates"
import type { RentalPeriod } from "./rentalDates"

export function RentalRenewDialog({ assets, onClose, onSuccess, onConflict }: { assets: Asset[]; onClose: () => void; onSuccess: () => Promise<void>; onConflict: () => Promise<void> }) {
  const [period, setPeriod] = useState<RentalPeriod>("week")
  const [confirmed, setConfirmed] = useState(false)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conflict, setConflict] = useState(false)
  const [submission, setSubmission] = useState<RentalRenewInput | null>(null)
  let previewError: string | null = null
  const previews = assets.map((asset) => {
    try { return { asset, date: renewalDate(asset.rental_expires_on ?? "", period) } }
    catch (caught) { previewError = caught instanceof Error ? caught.message : "无法计算续期日期"; return { asset, date: null } }
  })
  const valid = assets.length >= 1 && assets.length <= 100 && new Set(assets.map((asset) => asset.id)).size === assets.length && assets.every(rentalEligible) && !previewError
  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!valid || !confirmed || pending || conflict) return
    const payload = submission ?? { operation_key: crypto.randomUUID(), period, assets: assets.map((asset) => ({ asset_id: asset.id, expected_rental_revision: asset.rental_revision! })) }
    setSubmission(payload); setPending(true); setError(null)
    try { await renewAssetRentals(payload); await onSuccess(); onClose() }
    catch (caught) {
      if (caught instanceof ProblemDetailsError && caught.problem.status === 409) {
        setConflict(true); setError("租赁信息已变化，已刷新台账；请关闭并重新选择资产。")
        await onConflict()
      } else setError(caught instanceof ProblemDetailsError ? caught.problem.detail : "请求结果暂未确认，请重试本次操作；重试不会重复续期。")
    } finally { setPending(false) }
  }
  return <div className="assetDialogBackdrop"><section className="assetDialog" role="dialog" aria-modal="true" aria-labelledby="rental-renew-title">
    <h2 id="rental-renew-title">续期租赁资产</h2><p>仅续期本次明确选择的 {assets.length} 件资产，全部成功或全部不变。从各自原到期日顺延，不从今天起算。</p>
    <form className="assetDialogForm" onSubmit={(event) => void submit(event)}>
      <div className="assetField"><label htmlFor="rental-period">续期周期</label><select id="rental-period" value={period} disabled={pending || submission !== null} onChange={(event) => { setPeriod(event.target.value as RentalPeriod); setConfirmed(false) }}>{Object.entries(rentalPeriodLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
      <div className="rentalPreviews">{previews.map(({ asset, date }) => <div className="rentalPreview" key={asset.id}><strong>{asset.asset_code} · {asset.name}</strong><span>原到期日：{asset.rental_expires_on}</span><span>新到期日：<b>{date ?? "无法续期"}</b></span>{date && date < todayDateInput() ? <small className="rentalWarning">续期后仍逾期</small> : null}</div>)}</div>
      {previewError ? <p role="alert" className="formError">{previewError}</p> : null}
      <label className="assetCheckboxLabel"><input type="checkbox" checked={confirmed} disabled={pending || conflict} onChange={(event) => setConfirmed(event.target.checked)} />我已核对原到期日和新到期日，确认续期</label>
      {error ? <p role="alert" className="formError">{error}</p> : null}
      <div className="assetDialogActions"><button type="button" className="secondaryButton" disabled={pending} onClick={onClose}>关闭</button><button type="submit" className="primaryButton" disabled={pending || !confirmed || !valid || conflict}>{pending ? "正在续期…" : "确认续期"}</button></div>
    </form>
  </section></div>
}
