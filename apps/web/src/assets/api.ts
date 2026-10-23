import { apiFetch, apiFetchResponse } from "../api/client"

export type AssetStatus = "available" | "assigned" | "maintenance" | "retired"

export type AssetAssignment = {
  id: string
  asset_id: string
  person_id: string
  person_name: string
  assigned_at: string
  expected_return_at: string | null
  returned_at: string | null
  condition_on_assign: string | null
  condition_on_return: string | null
  note: string | null
}

export type AssetPhoto = {
  id: string
  mime_type: "image/jpeg" | "image/png" | "image/webp"
  extension: "jpg" | "png" | "webp"
  size_bytes: number
  updated_at: string
  url: string
}

export type AssetWarrantyDetails = {
  warranty_channel?: "online" | "offline" | null
  warranty_service_location?: string | null
  warranty_contact_name?: string | null
  warranty_contact_phone?: string | null
  warranty_product_url?: string | null
  warranty_order_number?: string | null
}

export type Asset = AssetWarrantyDetails & {
  id: string
  asset_code: string
  name: string
  category: string | null
  brand: string | null
  model: string | null
  serial_number: string | null
  status: AssetStatus
  location: string | null
  purchase_date: string | null
  warranty_expires_on: string | null
  source_purchase_id: string | null
  note: string | null
  created_at: string
  updated_at: string
  current_assignment: AssetAssignment | null
  photo: AssetPhoto | null
}

export type MyAsset = AssetWarrantyDetails & {
  id: string
  asset_code: string
  name: string
  category: string | null
  brand: string | null
  model: string | null
  status: AssetStatus
  location: string | null
  purchase_date: string | null
  warranty_expires_on: string | null
  assigned_at: string
  expected_return_at: string | null
  note: string | null
  photo: AssetPhoto | null
}

export type AssetPage = {
  items: Asset[]
  page: number
  page_size: number
  total: number
}

export type AssetScan = {
  asset_code: string
  name: string
  category: string | null
  brand: string | null
  model: string | null
  status: AssetStatus
  location: string | null
  purchase_date: string | null
  warranty_expires_on: string | null
  scanned_at: string
}

export type AssetInput = AssetWarrantyDetails & {
  name: string
  category?: string | null
  brand?: string | null
  model?: string | null
  serial_number?: string | null
  location?: string | null
  purchase_date?: string | null
  warranty_expires_on?: string | null
  note?: string | null
}

export type AssetUpdateInput = Partial<AssetInput> & { status?: AssetStatus }

export type AssetExportInput = {
  format: "xlsx" | "csv"
  purpose: string
  query?: string
  status?: AssetStatus | ""
  category?: string
}

export type AssetExportDownload = { blob: Blob; filename: string }

export function listAssets(filters: {
  query?: string
  status?: AssetStatus | ""
  category?: string
  page?: number
  pageSize?: number
} = {}): Promise<AssetPage> {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 50),
  })
  if (filters.query) params.set("query", filters.query)
  if (filters.status) params.set("status", filters.status)
  if (filters.category) params.set("category", filters.category)
  return apiFetch<AssetPage>(`/assets?${params.toString()}`)
}

export function listMyAssets(): Promise<MyAsset[]> {
  return apiFetch<MyAsset[]>("/assets/me")
}

export function listPersonAssets(personId: string): Promise<Asset[]> {
  return apiFetch<Asset[]>(`/assets/person/${personId}`)
}

export function getAsset(assetId: string): Promise<Asset> {
  return apiFetch<Asset>(`/assets/${assetId}`)
}

export function createAsset(input: AssetInput): Promise<Asset> {
  return apiFetch<Asset>("/assets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function updateAsset(assetId: string, input: AssetUpdateInput): Promise<Asset> {
  return apiFetch<Asset>(`/assets/${assetId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function uploadAssetPhoto(assetId: string, file: File): Promise<Asset> {
  const body = new FormData()
  body.append("file", file)
  return apiFetch<Asset>(`/assets/${assetId}/photo`, { method: "PUT", body })
}

export function deleteAssetPhoto(assetId: string): Promise<Asset> {
  return apiFetch<Asset>(`/assets/${assetId}/photo`, { method: "DELETE" })
}

export function assetPhotoUrl(assetId: string): string {
  return `/api/v1/assets/${assetId}/photo`
}

function attachmentFilename(response: Response, format: "xlsx" | "csv"): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const match = /filename="([A-Za-z0-9._-]+)"/.exec(disposition)
  const extension = format === "xlsx" ? ".xlsx" : ".csv"
  if (!match || !match[1].endsWith(extension)) throw new Error("导出文件无效")
  return match[1]
}

export async function exportAssets(input: AssetExportInput): Promise<AssetExportDownload> {
  const response = await apiFetchResponse("/assets/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...input,
      query: input.query || undefined,
      status: input.status || undefined,
      category: input.category || undefined,
    }),
  })
  const filename = attachmentFilename(response, input.format)
  return { blob: await response.blob(), filename }
}

export function assignAsset(
  assetId: string,
  input: {
    person_id: string
    assigned_at: string
    expected_return_at?: string | null
    condition_on_assign?: string | null
    note?: string | null
  },
): Promise<Asset> {
  return apiFetch<Asset>(`/assets/${assetId}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function returnAsset(
  assetId: string,
  input: { returned_at: string; condition_on_return?: string | null; note?: string | null },
): Promise<Asset> {
  return apiFetch<Asset>(`/assets/${assetId}/return`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function getAssetScan(assetCode: string): Promise<AssetScan> {
  return apiFetch<AssetScan>(`/assets/scan/${encodeURIComponent(assetCode)}`)
}
