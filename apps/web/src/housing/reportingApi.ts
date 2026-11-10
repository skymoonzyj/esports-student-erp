import { apiFetch, ProblemDetailsError } from "../api/client"

export type HousingReportBatchStatus =
  | "generated"
  | "marked_submitted"
  | "voided"
  | "failed"

export type HousingReportFormat = "xlsx" | "csv"
export type HousingReportScopeMode = "authorized" | "buildings" | "floors"
export type HousingReportPersonType =
  | "player"
  | "student_player"
  | "student"
  | "team_manager"
  | "coach"
  | "assistant_coach"
  | "life_teacher"
  | "customer_service"
  | "administration"
  | "other"
  | "visitor"

export type HousingReportBatchScope = {
  building_id: string
  building_name: string
  floor_id: string
  floor_name: string
}

export type HousingReportBatchActions = {
  download: boolean
  mark_submitted: boolean
  void: boolean
  correct: boolean
}

export type HousingReportBatchSummary = {
  id: string
  batch_number: string
  status: HousingReportBatchStatus
  version: number
  range_start: string
  range_end: string
  cohort_filter: string[]
  person_type_filter: HousingReportPersonType[]
  purpose: string
  resident_count: number
  stay_row_count: number
  guardian_row_count: number
  scopes: HousingReportBatchScope[]
  created_at: string
  created_by_user_id: string
  created_by_name: string
  submitted_at: string | null
  submitted_by_user_id: string | null
  submitted_by_name: string | null
  voided_at: string | null
  voided_by_user_id: string | null
  voided_by_name: string | null
  corrects_batch_id: string | null
  failure_code: string | null
  allowed_actions: HousingReportBatchActions
}

export type HousingReportCorrectionLink = {
  id: string
  batch_number: string
  status: HousingReportBatchStatus
  created_at: string
}

export type HousingReportBatchDetail = HousingReportBatchSummary & {
  submission_note: string | null
  void_reason: string | null
  correction_chain: HousingReportCorrectionLink[]
  failure_summary: HousingReportFailureSummary | null
}

export type HousingReportFailureSummary = {
  issue_count: number
  field_counts: Record<string, number>
}

export type HousingReportIssue = {
  field: string
  message: string
  batch_id: string
  person_id: string
  person_name: string
  masked_phone: string
  stay_id: string
  missing_fields: string
}

export type HousingReportStatusCounts = Record<HousingReportBatchStatus, number>

export type HousingReportBuildingOption = {
  id: string
  name: string
}

export type HousingReportFloorOption = {
  id: string
  building_id: string
  name: string
}

export type HousingReportCohortOption = {
  id: string
  name: string
}

export type HousingReportFilterOptions = {
  buildings: HousingReportBuildingOption[]
  floors: HousingReportFloorOption[]
  cohorts: HousingReportCohortOption[]
}

export type HousingReportListResponse = {
  items: HousingReportBatchSummary[]
  page: number
  page_size: number
  total: number
  status_counts: HousingReportStatusCounts
  access_revision: string
  filter_options: HousingReportFilterOptions
}

export type HousingReportListFilters = {
  statuses?: HousingReportBatchStatus[]
  rangeStart?: string
  rangeEnd?: string
  buildingIds?: string[]
  floorIds?: string[]
  cohortIds?: string[]
  createdByUserId?: string
  page?: number
  pageSize?: number
}

export type GenerateHousingReportInput = {
  range_start: string
  range_end: string
  scope_mode: HousingReportScopeMode
  building_ids: string[]
  floor_ids: string[]
  cohort_ids: string[]
  person_types: HousingReportPersonType[]
  purpose: string
  corrects_batch_id: string | null
}

export type MarkHousingReportSubmittedInput = {
  submitted_at: string
  note: string | null
  version: number
}

export type VoidHousingReportInput = {
  reason: string
  version: number
}

export type DownloadedHousingReport = {
  blob: Blob
  filename: string
}

const STATUS_ORDER: readonly HousingReportBatchStatus[] = [
  "generated",
  "marked_submitted",
  "voided",
  "failed",
]

const XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
const ZIP_MIME = "application/zip"
const SAFE_ATTACHMENT_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/

function orderedUnique<T extends string>(
  values: T[] | undefined,
  order?: readonly T[],
): T[] {
  if (!values?.length) return []
  const selected = new Set(values)
  if (order) return order.filter((value) => selected.has(value))
  return [...selected].sort((left, right) => left.localeCompare(right))
}

function appendMany(params: URLSearchParams, key: string, values: string[]): void {
  for (const value of values) params.append(key, value)
}

function appendOptional(params: URLSearchParams, key: string, value: unknown): void {
  if (value === undefined || value === null || value === "") return
  params.append(key, String(value))
}

function jsonPost(input: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  }
}

export function listHousingReports(
  filters: HousingReportListFilters = {},
): Promise<HousingReportListResponse> {
  const params = new URLSearchParams()
  appendMany(params, "status", orderedUnique(filters.statuses, STATUS_ORDER))
  appendOptional(params, "range_start", filters.rangeStart)
  appendOptional(params, "range_end", filters.rangeEnd)
  appendMany(params, "building_id", orderedUnique(filters.buildingIds))
  appendMany(params, "floor_id", orderedUnique(filters.floorIds))
  appendMany(params, "cohort_id", orderedUnique(filters.cohortIds))
  appendOptional(params, "created_by_user_id", filters.createdByUserId)
  appendOptional(params, "page", filters.page)
  appendOptional(params, "page_size", filters.pageSize)
  const query = params.toString()
  return apiFetch<HousingReportListResponse>(
    `/housing/report-batches${query ? `?${query}` : ""}`,
  )
}

export function getHousingReport(batchId: string): Promise<HousingReportBatchDetail> {
  return apiFetch<HousingReportBatchDetail>(
    `/housing/report-batches/${encodeURIComponent(batchId)}`,
  )
}

export function generateHousingReport(
  input: GenerateHousingReportInput,
): Promise<HousingReportBatchSummary> {
  return apiFetch<HousingReportBatchSummary>(
    "/housing/report-batches",
    jsonPost(input),
  )
}

export function markHousingReportSubmitted(
  batchId: string,
  input: MarkHousingReportSubmittedInput,
): Promise<HousingReportBatchSummary> {
  return apiFetch<HousingReportBatchSummary>(
    `/housing/report-batches/${encodeURIComponent(batchId)}/mark-submitted`,
    jsonPost(input),
  )
}

export function voidHousingReport(
  batchId: string,
  input: VoidHousingReportInput,
): Promise<HousingReportBatchSummary> {
  return apiFetch<HousingReportBatchSummary>(
    `/housing/report-batches/${encodeURIComponent(batchId)}/void`,
    jsonPost(input),
  )
}

function dispositionFilename(disposition: string): string | null {
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1]
  if (encoded) {
    try {
      return decodeURIComponent(encoded.trim())
    } catch {
      return null
    }
  }
  return disposition.match(/filename="([^"]+)"/i)?.[1]
    ?? disposition.match(/filename=([^;]+)/i)?.[1]?.trim()
    ?? null
}

export function safeAttachmentFilename(disposition: string, fallback: string): string {
  const candidate = dispositionFilename(disposition)
  if (
    candidate === null
    || candidate.includes("/")
    || candidate.includes("\\")
    || !SAFE_ATTACHMENT_NAME.test(candidate)
  ) {
    return fallback
  }
  return candidate
}

export async function downloadHousingReport(
  batchId: string,
  format: HousingReportFormat,
): Promise<DownloadedHousingReport> {
  const headers = new Headers({ Accept: format === "xlsx" ? XLSX_MIME : ZIP_MIME })
  const response = await fetch(
    `/api/v1/housing/report-batches/${encodeURIComponent(batchId)}/download?format=${format}`,
    { credentials: "include", headers },
  )
  if (!response.ok) throw await ProblemDetailsError.fromResponse(response)
  const extension = format === "xlsx" ? "xlsx" : "zip"
  return {
    blob: await response.blob(),
    filename: safeAttachmentFilename(
      response.headers.get("Content-Disposition") ?? "",
      `housing-report-${batchId}.${extension}`,
    ),
  }
}

export function saveDownloadedBlob(blob: Blob, filename: string): void {
  const objectUrl = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  try {
    anchor.href = objectUrl
    anchor.download = filename
    anchor.click()
  } finally {
    anchor.remove()
    URL.revokeObjectURL(objectUrl)
  }
}
