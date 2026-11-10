import type { AssetHardware } from "./api"
import { hardwareFields, isComputer } from "./AssetHardwareFields"

export function AssetHardwareDetails({ asset }: { asset: AssetHardware & { category?: string | null } }) {
  if (!isComputer(asset.category) && !hardwareFields.some(({ key }) => asset[key]?.trim())) return null
  return <div className="assetHardwareDetails" aria-label="电脑配置">
    {hardwareFields.filter(({ key }) => isComputer(asset.category) || asset[key]?.trim()).map(({ key, label }) => <small key={key}>{label}：{asset[key]?.trim() || "待补充"}</small>)}
  </div>
}
