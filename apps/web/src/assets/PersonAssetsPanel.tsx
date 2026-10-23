import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { listPersonAssets } from "./api"
import { assetKeys } from "./keys"
import { statusLabels } from "./status"
import { AssetPhotoLink } from "./AssetPhotoLink"
import { AssetWarrantyDetails } from "./AssetWarrantyDetails"
import "./assets.css"

export function PersonAssetsPanel({ personId }: { personId: string }) {
  const assets = useQuery({
    queryKey: assetKeys.person(personId),
    queryFn: () => listPersonAssets(personId),
  })
  return (
    <section className="detailCard personAssetsPanel" aria-labelledby="person-assets-title">
      <div className="detailCardHeader">
        <div><p className="detailCardEyebrow">ASSETS</p><h2 id="person-assets-title">正在使用的资产</h2></div>
        <Link className="peopleDetailLink" to="/assets">资产管理</Link>
      </div>
      {assets.isPending ? <p className="subtleStatus">正在读取资产…</p> : null}
      {assets.isError ? <p className="subtleStatus" role="alert">暂时无法读取资产信息。</p> : null}
      {assets.data && assets.data.length === 0 ? <p className="subtleStatus">当前没有登记在该人员名下的资产。</p> : null}
      {assets.data && assets.data.length > 0 ? (
        <div className="personAssetsList">
          {assets.data.map((asset) => (
            <div className="personAssetLine" key={asset.id}>
              <div>
                <strong>{asset.name}</strong>
                <span>{asset.asset_code} · {asset.category ?? "未分类"}</span>
                <span>采购 {asset.purchase_date ?? "未填写"} · 保修至 {asset.warranty_expires_on ?? "未填写"}</span>
                <AssetWarrantyDetails asset={asset} />
              </div>
              <div className="personAssetActions">
                <span className={`assetStatus ${asset.status}`}>{statusLabels[asset.status]}</span>
                <AssetPhotoLink assetId={asset.id} photo={asset.photo} />
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  )
}
