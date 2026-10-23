import type { AssetStatus } from "./api"

export const statusLabels: Record<AssetStatus, string> = {
  available: "可用",
  assigned: "使用中",
  maintenance: "维修中",
  retired: "已停用",
}

