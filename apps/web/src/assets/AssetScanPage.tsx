import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { getAssetScan } from "./api"
import { assetKeys } from "./keys"
import { statusLabels } from "./status"
import { OwnershipDetails } from "./OwnershipFields"
import { AssetHardwareDetails } from "./AssetHardwareDetails"
import "./assets.css"

export function AssetScanPage() {
  const { assetCode = "" } = useParams()
  const asset = useQuery({
    queryKey: assetKeys.scan(assetCode),
    queryFn: () => getAssetScan(assetCode),
    enabled: assetCode.length > 0,
  })
  return (
    <main className="assetScanPage">
      <header className="assetScanHeader">
        <div><p className="pageEyebrow">ASSET CHECK</p><h1>资产信息</h1><p>扫码查看公开的资产基础信息。</p></div>
        <Link className="secondaryButton" to="/login">登录系统</Link>
      </header>
      {asset.isPending ? <div className="assetState">正在读取资产信息…</div> : null}
      {asset.isError ? (
        <div className="assetScanCard" role="alert">
          <h2>{asset.error instanceof ProblemDetailsError && asset.error.problem.status === 404 ? "未找到该资产" : "暂时无法读取资产信息"}</h2>
          <p>资产代码可能已经停用或二维码已失效，请联系行政核对。</p>
        </div>
      ) : null}
      {asset.data ? (
        <section className="assetScanCard" aria-labelledby="asset-scan-title">
          <div><span className="assetCode">{asset.data.asset_code}</span><h2 id="asset-scan-title">{asset.data.name}</h2></div>
          <dl>
            <div><dt>状态</dt><dd><span className={`assetStatus ${asset.data.status}`}>{statusLabels[asset.data.status]}</span></dd></div>
            <div><dt>分类</dt><dd>{asset.data.category ?? "未填写"}</dd></div>
            <div><dt>品牌型号</dt><dd>{[asset.data.brand, asset.data.model].filter(Boolean).join(" · ") || "未填写"}</dd></div>
            <div><dt>存放位置</dt><dd>{asset.data.location ?? "未填写"}</dd></div>
            <div><dt>采购日期</dt><dd>{asset.data.purchase_date ?? "未填写"}</dd></div>
            <div><dt>保修到期日</dt><dd>{asset.data.warranty_expires_on ?? "未填写"}</dd></div>
          </dl>
          <OwnershipDetails asset={asset.data} />
          <AssetHardwareDetails asset={asset.data} />
          <p>如发现资产状态或位置不符，请联系行政人员更新台账。</p>
        </section>
      ) : null}
    </main>
  )
}
