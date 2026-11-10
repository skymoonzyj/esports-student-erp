import { apiFetch, apiFetchResponse } from "../api/client"
import type { RoomType, ServiceStatus } from "./api"
import { safeAttachmentFilename, saveDownloadedBlob } from "./reportingApi"

export type HousingPropertyValues = { service_status?: ServiceStatus; room_type?: RoomType; capacity?: number; notes?: string | null }
export type HousingPropertyStatus = "pending" | "approved" | "rejected" | "withdrawn"
export type HousingPropertyCapabilities = { can_submit: boolean; can_review: boolean; can_export: boolean; show_basic_export?: boolean; can_create_request?: boolean }
export type HousingPropertyView = "all" | "submitted" | "review"
export type HousingPropertyChange = {
  id: string; room_id: string; room_number: string; building_name: string
  base_version: number; changes: HousingPropertyValues; before: HousingPropertyValues
  reason: string; status: HousingPropertyStatus; submitted_by_name: string; submitted_at: string
  reviewed_at: string | null; review_note: string | null; can_review: boolean; can_withdraw: boolean
}
export type HousingPropertyChangePage = { items: HousingPropertyChange[]; total: number; page: number; page_size: number }
export type HousingPropertyExportInput = { kind: "occupants" | "problems"; format: "xlsx" | "csv"; purpose: string; building_ids: string[] }
export type HousingPropertyExportFile = { blob: Blob; filename: string }

function jsonPost(value: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value) }
}

export function getHousingPropertyCapabilities(signal?: AbortSignal, roomId?: string): Promise<HousingPropertyCapabilities> {
  return apiFetch(`/housing/property/capabilities${roomId ? `?room_id=${encodeURIComponent(roomId)}` : ""}`, { signal })
}
export function listHousingPropertyChanges(input: { status: HousingPropertyStatus; page: number; page_size: number; room_id?: string; view?: HousingPropertyView }, signal?: AbortSignal): Promise<HousingPropertyChangePage> {
  const query = new URLSearchParams({ status: input.status, page: String(input.page), page_size: String(input.page_size) })
  if (input.room_id) query.set("room_id", input.room_id)
  if (input.view) query.set("view", input.view)
  return apiFetch(`/housing/property/changes?${query}`, { signal })
}
export function submitHousingPropertyChange(roomId: string, input: { expected_version: number; changes: HousingPropertyValues; reason: string }): Promise<HousingPropertyChange> {
  return apiFetch(`/housing/rooms/${encodeURIComponent(roomId)}/change-requests`, jsonPost(input))
}
export function reviewHousingPropertyChange(id: string, input: { decision: "approve" | "reject"; note?: string }): Promise<HousingPropertyChange> {
  return apiFetch(`/housing/change-requests/${encodeURIComponent(id)}/review`, jsonPost(input))
}
export function withdrawHousingPropertyChange(id: string): Promise<HousingPropertyChange> {
  return apiFetch(`/housing/change-requests/${encodeURIComponent(id)}/withdraw`, jsonPost({}))
}
export async function exportHousingProperty(input: HousingPropertyExportInput): Promise<HousingPropertyExportFile> {
  const response = await apiFetchResponse("/housing/property/exports", jsonPost(input))
  const fallback = `housing-${input.kind}.${input.format}`
  const filename = safeAttachmentFilename(response.headers.get("Content-Disposition") ?? "", fallback)
  return { blob: await response.blob(), filename: filename.toLowerCase().endsWith(`.${input.format}`) ? filename : fallback }
}
export function saveHousingPropertyExport(file: HousingPropertyExportFile): void { saveDownloadedBlob(file.blob, file.filename) }
