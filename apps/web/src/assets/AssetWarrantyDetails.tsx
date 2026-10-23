import type { AssetWarrantyDetails as WarrantyInfo } from "./api"
import { todayDateInput } from "../shared/date"

export function AssetWarrantyDetails({ asset }: {
  asset: WarrantyInfo & { warranty_expires_on: string | null }
}) {
  if (!asset.warranty_channel && !asset.warranty_expires_on) return null
  const warrantyStatus = !asset.warranty_expires_on
    ? "保修到期日未填写"
    : asset.warranty_expires_on >= todayDateInput() ? "保修期内" : "已过保"
  return (
    <details className="assetWarrantyDetails">
      <summary>{warrantyStatus} · 查看售后信息</summary>
      {!asset.warranty_channel ? <p>尚未填写保修方式</p> : (
        <dl>
          <div><dt>保修方式</dt><dd>{asset.warranty_channel === "offline" ? "线下" : "线上"}</dd></div>
          {asset.warranty_channel === "offline" ? <>
            <div><dt>售后地点</dt><dd>{asset.warranty_service_location || "未填写"}</dd></div>
            <div><dt>售后联系人</dt><dd>{asset.warranty_contact_name || "未填写"}</dd></div>
            <div><dt>售后电话</dt><dd>{asset.warranty_contact_phone || "未填写"}</dd></div>
          </> : <>
            <div><dt>产品链接</dt><dd>{asset.warranty_product_url && /^https?:\/\//i.test(asset.warranty_product_url)
              ? <a href={asset.warranty_product_url} target="_blank" rel="noopener noreferrer">打开商品详情</a>
              : "未填写"}</dd></div>
            <div><dt>订单号</dt><dd>{asset.warranty_order_number || "未填写"}</dd></div>
          </>}
        </dl>
      )}
    </details>
  )
}
