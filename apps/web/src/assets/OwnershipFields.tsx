import type { Asset, AssetInput, AssetOwnership, AssetUpdateInput } from "./api"

const ownershipFields = ["ownership_type", "acquisition_method", "acquisition_channel", "acquisition_note", "rental_started_on", "rental_expires_on"] as const

export function ownershipPatch(input: AssetInput, original: Asset): AssetUpdateInput {
  const patch: AssetUpdateInput = {}
  for (const field of ownershipFields) {
    if ((input[field] ?? null) !== (original[field] ?? null)) Object.assign(patch, { [field]: input[field] ?? null })
  }
  if (Object.keys(patch).length) {
    patch.expected_rental_revision = original.rental_revision ?? 0
  }
  return patch
}

export function ownershipLabel(asset: AssetOwnership): string {
  return asset.ownership_type === "owned" ? "自有" : asset.ownership_type === "rented" ? "租用" : "待确认"
}

export function OwnershipDetails({ asset }: { asset: AssetOwnership }) {
  return <div className="assetOwnershipDetails"><span>归属：{ownershipLabel(asset)}</span>{asset.ownership_type === "rented" ? <>
    <span>租期：{asset.rental_started_on ?? "待确认"} 至 {asset.rental_expires_on ?? "待确认"}（含当天）</span>
    {asset.rental_returned_on ? <span>已退租：{asset.rental_returned_on}</span> : null}
  </> : null}</div>
}

export function OwnershipFields({ input, onChange, existing }: { input: AssetInput; onChange: (input: AssetInput) => void; existing: boolean }) {
  function set(field: keyof AssetInput, value: string | null) { onChange({ ...input, [field]: value }) }
  return <fieldset className="assetWarrantyFields"><legend>资产归属与取得方式</legend>
    <div className="assetField"><label htmlFor="asset-ownership">资产归属</label><select id="asset-ownership" required={!existing} value={input.ownership_type ?? ""} onChange={(event) => onChange({ ...input,
      ownership_type: event.target.value as AssetInput["ownership_type"] || null,
      acquisition_method: null, acquisition_channel: null, acquisition_note: null,
      rental_started_on: null, rental_expires_on: null,
    })}><option value="">{existing ? "待确认" : "请选择归属"}</option><option value="owned">自有</option><option value="rented">租用</option></select></div>
    {input.ownership_type === "owned" ? <>
      <div className="assetField"><label htmlFor="asset-acquisition-method">取得方式</label><select id="asset-acquisition-method" required value={input.acquisition_method ?? ""} onChange={(event) => onChange({ ...input, acquisition_method: event.target.value as AssetInput["acquisition_method"] || null, acquisition_channel: null, acquisition_note: null })}><option value="">请选择</option><option value="purchase">采购</option><option value="other">其他</option></select></div>
      {input.acquisition_method === "purchase" ? <div className="assetField"><label htmlFor="asset-acquisition-channel">采购渠道</label><select id="asset-acquisition-channel" required value={input.acquisition_channel ?? ""} onChange={(event) => set("acquisition_channel", event.target.value)}><option value="">请选择</option><option value="online">线上</option><option value="offline">线下</option></select></div> : null}
      {input.acquisition_method === "other" ? <div className="assetField"><label htmlFor="asset-acquisition-note">获得方式说明</label><textarea id="asset-acquisition-note" value={input.acquisition_note ?? ""} onChange={(event) => set("acquisition_note", event.target.value)} maxLength={500} required /></div> : null}
    </> : null}
    {input.ownership_type === "rented" ? <>
      <div className="assetField"><label htmlFor="asset-rental-start">租赁开始日期</label><input id="asset-rental-start" type="date" min="0001-01-01" max="9999-12-31" required value={input.rental_started_on ?? ""} onChange={(event) => set("rental_started_on", event.target.value || null)} /></div>
      <div className="assetField"><label htmlFor="asset-rental-expiry">租赁到期日期（含当天）</label><input id="asset-rental-expiry" type="date" min={input.rental_started_on || "0001-01-01"} max="9999-12-31" required value={input.rental_expires_on ?? ""} onChange={(event) => set("rental_expires_on", event.target.value || null)} /></div>
      <small>租期独立于保修、使用人的预计归还日期。续期请使用“续期”操作；续期后的日期不能通过普通编辑更改。</small>
    </> : null}
  </fieldset>
}
