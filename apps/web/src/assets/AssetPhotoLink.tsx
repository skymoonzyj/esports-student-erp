import { ExternalLink } from "lucide-react"

import { assetPhotoUrl } from "./api"
import type { AssetPhoto } from "./api"

export function AssetPhotoLink({
  assetId,
  photo,
}: {
  assetId: string
  photo: AssetPhoto | null
}) {
  if (!photo) return null
  return (
    <a
      className="assetPhotoLink"
      href={assetPhotoUrl(assetId)}
      target="_blank"
      rel="noreferrer"
      title="查看资产辨识照片"
    >
      <ExternalLink aria-hidden="true" />
      查看照片
    </a>
  )
}
