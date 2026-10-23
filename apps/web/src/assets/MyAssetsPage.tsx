import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"

import { listMyAssets } from "./api"
import { formatDateOnly } from "../shared/date"
import { assetKeys } from "./keys"
import { statusLabels } from "./status"
import { AssetPhotoLink } from "./AssetPhotoLink"
import { AssetWarrantyDetails } from "./AssetWarrantyDetails"
import "./assets.css"

function displayTime(value: string | null): string {
  return formatDateOnly(value)
}

export function MyAssetsPage() {
  const assets = useQuery({ queryKey: assetKeys.mine(), queryFn: listMyAssets })
  return (
    <div className="myAssetsPage">
      <header className="myAssetsHeader">
        <div><p className="pageEyebrow">MY ASSETS</p><h1>我的资产</h1><p>查看当前登记在本人名下的资产和预计归还时间。</p></div>
        <Link className="secondaryButton" to="/">返回工作台</Link>
      </header>
      {assets.isPending ? <div className="assetState">正在读取我的资产…</div> : null}
      {assets.isError ? <div className="assetState" role="alert">暂时无法读取资产信息，请稍后重试。</div> : null}
      {assets.data && assets.data.length === 0 ? <div className="assetState">当前没有登记在本人名下的资产。</div> : null}
      {assets.data && assets.data.length > 0 ? (
        <section className="myAssetGrid" aria-label="本人使用资产">
          {assets.data.map((asset) => (
            <article className="myAssetCard" key={asset.id}>
              <header><div><h2>{asset.name}</h2><span className="assetCode">{asset.asset_code}</span></div><span className={`assetStatus ${asset.status}`}>{statusLabels[asset.status]}</span></header>
              <dl>
                <div><dt>分类</dt><dd>{asset.category ?? "未分类"}</dd></div>
                <div><dt>品牌型号</dt><dd>{[asset.brand, asset.model].filter(Boolean).join(" · ") || "未填写"}</dd></div>
                <div><dt>存放位置</dt><dd>{asset.location ?? "未填写"}</dd></div>
                <div><dt>采购日期</dt><dd>{asset.purchase_date ?? "未填写"}</dd></div>
                <div><dt>保修到期日</dt><dd>{asset.warranty_expires_on ?? "未填写"}</dd></div>
                <div><dt>领用日期</dt><dd>{displayTime(asset.assigned_at)}</dd></div>
                <div><dt>预计归还日期</dt><dd>{displayTime(asset.expected_return_at)}</dd></div>
              </dl>
              <AssetPhotoLink assetId={asset.id} photo={asset.photo} />
              <AssetWarrantyDetails asset={asset} />
              {asset.note ? <small>{asset.note}</small> : null}
            </article>
          ))}
        </section>
      ) : null}
    </div>
  )
}
