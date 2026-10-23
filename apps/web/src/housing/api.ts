import { apiFetch, apiFetchResponse } from "../api/client"

export type RoomType = "single" | "double" | "triple" | "custom" | "functional"
export type VerificationStatus = "pending_verification" | "verified"
export type ServiceStatus = "available" | "problem" | "maintenance" | "disabled"
export type CurrentGender = "male" | "female" | null
export type HousingSex = "male" | "female"
export type HousingStayPurpose = "permanent" | "camp" | "temporary"
export type HousingStayDurationMode = "long_term" | "fixed_term"
export type HousingStayStatus = "planned" | "checked_in" | "checkout_pending" | "checked_out" | "cancelled"
export type HousingResidentPersonType =
  | "student"
  | "coach"
  | "life_teacher"
  | "customer_service"
  | "administration"
  | "other"
  | "visitor"
export type HousingCohortStatus = "draft" | "open" | "upcoming" | "active" | "completed" | "archived"

export type HousingRoomAllowedActions = {
  edit: boolean
  delete: boolean
  verify: boolean
  create_stay: boolean
  report_issue: boolean
  view_detail: boolean
}

export type HousingStayAllowedActions = {
  edit_reservation: boolean
  confirm_check_in: boolean
  cancel_reservation: boolean
  transfer: boolean
  request_checkout: boolean
  confirm_checkout: boolean
}

export type HousingStay = {
  id: string
  person_id: string
  room_id: string
  stay_purpose: HousingStayPurpose
  duration_mode: HousingStayDurationMode
  cohort_id: string | null
  visit_reason: string | null
  planned_check_in_at: string
  planned_check_out_at: string | null
  actual_check_in_at: string | null
  actual_check_out_at: string | null
  status: HousingStayStatus
  room_name_snapshot: string
  floor_name_snapshot: string
  building_name_snapshot: string
  notes: string | null
  cancellation_reason: string | null
  checkout_requested_actual_at: string | null
  checkout_requested_at: string | null
  person_name: string
  person_type: HousingResidentPersonType
  sex: HousingSex
  version: number
  allowed_actions: HousingStayAllowedActions
  created_at: string
  updated_at: string
}

export type HousingRoomIssue = {
  id: string
  room_id: string
  description: string
  previous_service_status: ServiceStatus
  reported_at: string
  status: "open" | "resolved"
  resolved_at: string | null
  resolution_note: string | null
  allowed_actions: { resolve: boolean }
  created_at: string
  updated_at: string
}

export type HousingTransfer = {
  id: string
  person_id: string
  from_stay_id: string
  to_stay_id: string
  transferred_at: string
  reason: string
  created_at: string
  updated_at: string
}

export type HousingBuilding = {
  id: string
  code: string
  name: string
  display_order: number
  is_active: boolean
  notes: string | null
  allowed_actions: Array<"edit" | "delete" | "create_floor">
  created_at: string
  updated_at: string
}

export type HousingFloor = {
  id: string
  building_id: string
  building_name: string
  number: number
  name: string
  display_order: number
  is_active: boolean
  notes: string | null
  allowed_actions: Array<"edit" | "delete" | "create_room">
  created_at: string
  updated_at: string
}

export type HousingCatalog = {
  access_revision: string
  buildings: HousingBuilding[]
  floors: HousingFloor[]
  can_create_building: boolean
}

export type HousingRoom = {
  id: string
  building_id: string
  floor_id: string
  building_code: string
  building_name: string
  building_is_active: boolean
  floor_number: number
  floor_name: string
  floor_is_active: boolean
  room_number: string
  room_type: RoomType
  capacity: number
  area_square_meters: string | null
  verification_status: VerificationStatus
  service_status: ServiceStatus
  notes: string | null
  version: number
  current_occupants: number
  remaining_capacity: number
  current_gender: CurrentGender
  has_future_reservation: boolean
  occupancy_status: "occupied" | "unoccupied"
  is_assignable: boolean
  status_text: string
  allowed_actions: HousingRoomAllowedActions
  occupants: HousingStay[]
  future_reservation_count: number
  open_issue_summary: HousingRoomIssue | null
  pending_transfer_count: number
  created_at: string
  updated_at: string
}

export type HousingRoomDetail = {
  access_revision: string
  as_of: string
  room: HousingRoom
  current_stays: HousingStay[]
  future_reservations: HousingStay[]
  history: HousingStay[]
  transfers: HousingTransfer[]
  issues: HousingRoomIssue[]
}

export type HousingResidentOption = {
  person_id: string
  full_name: string
  person_type: HousingResidentPersonType
  sex: HousingSex
  masked_phone: string | null
  account_ready: boolean
  profile_ready: boolean
  guardian_ready: boolean
}

export type HousingResidentOptions = {
  access_revision: string
  items: HousingResidentOption[]
}

export type HousingCohortOption = {
  id: string
  name: string
  status: HousingCohortStatus
  default_check_in_at: string
  default_check_out_at: string
}

export type HousingCohortOptions = {
  access_revision: string
  items: HousingCohortOption[]
}

export type HousingTransferRoomOption = {
  room_id: string
  building_label: string
  floor_label: string
  room_label: string
  capacity: number
  current_occupants: number
  current_gender: CurrentGender
  advisory_compatible: boolean
  status_text: string
}

export type HousingTransferOptions = {
  access_revision: string
  items: HousingTransferRoomOption[]
}

export type HousingSummary = {
  total_rooms: number
  occupied_rooms: number
  unoccupied_rooms: number
  assignable_rooms: number
  unassignable_rooms: number
  problem_rooms: number
  pending_verification_rooms: number
  total_capacity: number
  current_occupants: number
  remaining_capacity: number
  occupancy_rate: string
  single_rooms: number
  double_rooms: number
  triple_rooms: number
  custom_rooms: number
  functional_rooms: number
}

export type HousingOverview = {
  access_revision: string
  as_of: string
  summary: HousingSummary
  rooms: HousingRoom[]
  page: number
  page_size: number
  total: number
}

export type HousingFiltersValue = {
  buildingId?: string
  floorId?: string
  query?: string
  roomTypes?: RoomType[]
  verificationStatuses?: VerificationStatus[]
  serviceStatuses?: ServiceStatus[]
  currentGender?: "male" | "female" | "unspecified"
  occupancy?: "occupied" | "unoccupied"
  assignability?: "assignable" | "unassignable"
  page?: number
  pageSize?: number
}

export type HousingOccupancyExportInput = {
  format: "xlsx" | "csv"
  purpose: string
  building_id?: string
  floor_id?: string
  query?: string
  room_types?: RoomType[]
  verification_statuses?: VerificationStatus[]
  service_statuses?: ServiceStatus[]
  current_gender?: "male" | "female" | "unspecified"
  occupancy?: "occupied" | "unoccupied"
  assignability?: "assignable" | "unassignable"
}

export type HousingOccupancyExportDownload = { blob: Blob; filename: string }

export type HousingBuildingCreate = {
  code: string
  name: string
  display_order: number
  notes: string | null
}

export type HousingBuildingUpdate = Partial<HousingBuildingCreate> & { is_active?: boolean }

export type HousingFloorCreate = {
  building_id: string
  number: number
  name: string
  display_order: number
  notes: string | null
}

export type HousingFloorUpdate = Partial<Omit<HousingFloorCreate, "building_id">> & { is_active?: boolean }

export type HousingRoomCreate = {
  floor_id: string
  room_number: string
  room_type: RoomType
  capacity: number
  area_square_meters: string | null
  notes: string | null
}

export type HousingRoomUpdate = Partial<Omit<HousingRoomCreate, "floor_id">> & {
  version: number
  floor_id?: string
  service_status?: Exclude<ServiceStatus, "problem">
}

export type HousingRoomVersion = { room_id: string; version: number }

export type HousingStayCreate = {
  person_id: string
  stay_purpose: HousingStayPurpose
  duration_mode: HousingStayDurationMode
  cohort_id: string | null
  visit_reason: string | null
  planned_check_in_at: string
  planned_check_out_at: string | null
  notes: string | null
}

export type HousingStayUpdate = {
  version: number
  planned_check_in_at: string
  planned_check_out_at: string | null
  notes: string | null
}

export type HousingConfirmCheckIn = { version: number; actual_check_in_at: string }
export type HousingCancelReservation = { version: number; reason: string }
export type HousingTransferStay = {
  version: number
  target_room_id: string
  transferred_at: string
  reason: string
}
export type HousingCheckoutRequest = { version: number; actual_check_out_at: string }
export type HousingCheckoutConfirmation = { version: number }
export type HousingRoomIssueCreate = { room_version: number; description: string }
export type HousingRoomIssueResolve = { room_version: number; resolution_note: string }

const ROOM_TYPE_ORDER: readonly RoomType[] = ["single", "double", "triple", "custom", "functional"]
const VERIFICATION_STATUS_ORDER: readonly VerificationStatus[] = ["pending_verification", "verified"]
const SERVICE_STATUS_ORDER: readonly ServiceStatus[] = ["available", "problem", "maintenance", "disabled"]

function orderedUnique<T extends string>(values: T[] | undefined, order: readonly T[]): T[] | undefined {
  if (!values?.length) return undefined
  const selected = new Set(values)
  return order.filter((value) => selected.has(value))
}

export function normalizeHousingMultiSelects(value: HousingFiltersValue): HousingFiltersValue {
  return {
    ...value,
    roomTypes: orderedUnique(value.roomTypes, ROOM_TYPE_ORDER),
    verificationStatuses: orderedUnique(value.verificationStatuses, VERIFICATION_STATUS_ORDER),
    serviceStatuses: orderedUnique(value.serviceStatuses, SERVICE_STATUS_ORDER),
  }
}

function appendValue(params: URLSearchParams, key: string, value: unknown): void {
  if (value === undefined || value === null || value === "") return
  params.append(key, String(value))
}

export function getHousingCatalog(): Promise<HousingCatalog> {
  return apiFetch<HousingCatalog>("/housing/catalog")
}

export function listHousingOverview(filters: HousingFiltersValue = {}): Promise<HousingOverview> {
  const normalized = normalizeHousingMultiSelects(filters)
  const params = new URLSearchParams()
  appendValue(params, "building_id", normalized.buildingId)
  appendValue(params, "floor_id", normalized.floorId)
  appendValue(params, "query", normalized.query?.trim())
  for (const value of normalized.roomTypes ?? []) params.append("room_type", value)
  for (const value of normalized.verificationStatuses ?? []) {
    params.append("verification_status", value)
  }
  for (const value of normalized.serviceStatuses ?? []) params.append("service_status", value)
  appendValue(params, "current_gender", normalized.currentGender)
  appendValue(params, "occupancy", normalized.occupancy)
  appendValue(params, "assignability", normalized.assignability)
  appendValue(params, "page", normalized.page)
  appendValue(params, "page_size", normalized.pageSize)
  const query = params.toString()
  return apiFetch<HousingOverview>(`/housing/overview${query ? `?${query}` : ""}`)
}

function housingExportFilename(response: Response, format: "xlsx" | "csv"): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const match = /filename="(housing-occupancy-[0-9]{8}\.(?:xlsx|csv))"/.exec(disposition)
  const expectedExtension = format === "xlsx" ? ".xlsx" : ".csv"
  if (!match || !match[1].endsWith(expectedExtension)) throw new Error("导出文件无效")
  return match[1]
}

export async function exportHousingOccupancy(
  input: HousingOccupancyExportInput,
): Promise<HousingOccupancyExportDownload> {
  const response = await apiFetchResponse("/housing/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...input,
      query: input.query?.trim() || undefined,
      room_types: input.room_types?.length ? input.room_types : undefined,
      verification_statuses: input.verification_statuses?.length
        ? input.verification_statuses
        : undefined,
      service_statuses: input.service_statuses?.length ? input.service_statuses : undefined,
    }),
  })
  const filename = housingExportFilename(response, input.format)
  return { blob: await response.blob(), filename }
}

export function getHousingRoomDetail(roomId: string, signal?: AbortSignal): Promise<HousingRoomDetail> {
  return apiFetch<HousingRoomDetail>(`/housing/rooms/${roomId}/detail`, { signal })
}

export function listHousingResidentOptions(roomId: string, query: string, signal?: AbortSignal): Promise<HousingResidentOptions> {
  const params = new URLSearchParams({ query: query.trim() })
  return apiFetch<HousingResidentOptions>(`/housing/rooms/${roomId}/resident-options?${params.toString()}`, { signal })
}

export function getHousingCohortOptions(roomId: string, signal?: AbortSignal): Promise<HousingCohortOptions> {
  return apiFetch<HousingCohortOptions>(`/housing/rooms/${roomId}/cohort-options`, { signal })
}

export function getHousingTransferOptions(stayId: string, signal?: AbortSignal): Promise<HousingTransferOptions> {
  return apiFetch<HousingTransferOptions>(`/housing/stays/${stayId}/transfer-options`, { signal })
}

function jsonRequest(method: "POST" | "PATCH", input: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }
}

export function createHousingBuilding(input: HousingBuildingCreate): Promise<HousingBuilding> {
  return apiFetch<HousingBuilding>("/housing/buildings", jsonRequest("POST", input))
}

export function updateHousingBuilding(id: string, input: HousingBuildingUpdate): Promise<HousingBuilding> {
  return apiFetch<HousingBuilding>(`/housing/buildings/${id}`, jsonRequest("PATCH", input))
}

export function deleteHousingBuilding(id: string): Promise<void> {
  return apiFetch<void>(`/housing/buildings/${id}`, { method: "DELETE" })
}

export function createHousingFloor(input: HousingFloorCreate): Promise<HousingFloor> {
  return apiFetch<HousingFloor>("/housing/floors", jsonRequest("POST", input))
}

export function updateHousingFloor(id: string, input: HousingFloorUpdate): Promise<HousingFloor> {
  return apiFetch<HousingFloor>(`/housing/floors/${id}`, jsonRequest("PATCH", input))
}

export function deleteHousingFloor(id: string): Promise<void> {
  return apiFetch<void>(`/housing/floors/${id}`, { method: "DELETE" })
}

export function createHousingRoom(input: HousingRoomCreate): Promise<HousingRoom> {
  return apiFetch<HousingRoom>("/housing/rooms", jsonRequest("POST", input))
}

export function updateHousingRoom(id: string, input: HousingRoomUpdate): Promise<HousingRoom> {
  return apiFetch<HousingRoom>(`/housing/rooms/${id}`, jsonRequest("PATCH", input))
}

export function deleteHousingRoom(id: string): Promise<void> {
  return apiFetch<void>(`/housing/rooms/${id}`, { method: "DELETE" })
}

export function verifyHousingRoom(id: string, version: number): Promise<HousingRoom> {
  return apiFetch<HousingRoom>(`/housing/rooms/${id}/verify`, jsonRequest("POST", { version }))
}

export function verifyHousingRooms(rooms: HousingRoomVersion[]): Promise<HousingRoom[]> {
  return apiFetch<HousingRoom[]>("/housing/rooms/batch-verify", jsonRequest("POST", { rooms }))
}

export function createHousingStay(roomId: string, input: HousingStayCreate): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/rooms/${roomId}/stays`, jsonRequest("POST", input))
}

export function backfillHousingStay(roomId: string, input: HousingStayCreate): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/rooms/${roomId}/stays/backfill`, jsonRequest("POST", input))
}

export function updateHousingStay(stayId: string, input: HousingStayUpdate): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/stays/${stayId}`, jsonRequest("PATCH", input))
}

export function confirmHousingCheckIn(stayId: string, input: HousingConfirmCheckIn): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/stays/${stayId}/confirm-check-in`, jsonRequest("POST", input))
}

export function cancelHousingReservation(stayId: string, input: HousingCancelReservation): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/stays/${stayId}/cancel`, jsonRequest("POST", input))
}

export function transferHousingStay(stayId: string, input: HousingTransferStay): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/stays/${stayId}/transfer`, jsonRequest("POST", input))
}

export function requestHousingCheckout(stayId: string, input: HousingCheckoutRequest): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/stays/${stayId}/request-checkout`, jsonRequest("POST", input))
}

export function confirmHousingCheckout(stayId: string, input: HousingCheckoutConfirmation): Promise<HousingStay> {
  return apiFetch<HousingStay>(`/housing/stays/${stayId}/confirm-checkout`, jsonRequest("POST", input))
}

export function reportHousingIssue(roomId: string, input: HousingRoomIssueCreate): Promise<HousingRoomIssue> {
  return apiFetch<HousingRoomIssue>(`/housing/rooms/${roomId}/issues`, jsonRequest("POST", input))
}

export function resolveHousingIssue(issueId: string, input: HousingRoomIssueResolve): Promise<HousingRoomIssue> {
  return apiFetch<HousingRoomIssue>(`/housing/issues/${issueId}/resolve`, jsonRequest("POST", input))
}
