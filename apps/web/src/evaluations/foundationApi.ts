import { apiFetch } from "../api/client"

export type EvaluationFoundationContext = {
  accessible: true
  system_evaluation_available: true
  embedded_start_url: string
  standalone_start_url: string
  upload_available: boolean
  video_available: false
  reports_available: boolean
  surveys_available: boolean
  survey_sections: {
    respondent: boolean
    staff_feedback: boolean
    administration: boolean
  }
  game_accounts_available: boolean
  admin_available: boolean
  admin_sections: {
    cohort_staff: boolean
    student_matching: boolean
    analysis_profiles: boolean
    system_tasks: boolean
    adapter_certifications: boolean
  }
  integration_api_version: "v1"
}

export type GameAccountTarget = {
  student_person_id: string
  student_name: string
  student_number: string
  access_source: "self" | "family"
  active_count: number
  active_limit: number
}

export type EvaluationProjectDirectoryItem = {
  id: string
  name: string
  is_active: boolean
}

export type EvaluationCohortDirectoryItem = {
  id: string
  name: string
}

export type EvaluationStaffScopeDirectoryItem = {
  id: string
  name: string
  project_id: string
  project_name: string
  is_active: boolean
}

export type StudentGameAccount = {
  id: string
  project_id: string
  project_name: string
  game_id: string
  status: "active" | "unbound"
  valid_from: string
  unbound_at: string | null
  unbind_reason: string | null
}

export type AuxiliaryPhone = {
  id: string
  masked_phone: string
  status: "active" | "inactive"
  created_at: string
  disabled_at: string | null
}

const auxiliaryPhoneWriteBrand = Symbol("auxiliary-phone-write-only")

export type AuxiliaryPhoneWriteOnly = {
  readonly [auxiliaryPhoneWriteBrand]: true
}

const auxiliaryPhoneWritePayloads = new WeakMap<
  AuxiliaryPhoneWriteOnly,
  { phone: string; reason: string }
>()

export function captureAuxiliaryPhoneWrite(form: HTMLFormElement): AuxiliaryPhoneWriteOnly {
  const formData = new FormData(form)
  const write = Object.freeze({
    [auxiliaryPhoneWriteBrand]: true as const,
  })
  auxiliaryPhoneWritePayloads.set(write, {
    phone: String(formData.get("phone") ?? ""),
    reason: String(formData.get("reason") ?? "").trim(),
  })
  return write
}

function consumeAuxiliaryPhoneWrite(write: AuxiliaryPhoneWriteOnly): {
  phone: string
  reason: string
} {
  const payload = auxiliaryPhoneWritePayloads.get(write)
  if (!payload) throw new TypeError("Invalid auxiliary phone write")
  auxiliaryPhoneWritePayloads.delete(write)
  return payload
}

export type StaffServiceItem = {
  id: string
  source: "coach_assignment" | "cohort_staff_assignment"
  staff_person_id: string
  staff_name: string
  role_name: string
  role_category: "teaching" | "life_service" | "operations"
  scope_kind: "project" | "class"
  project_id: string
  project_name: string
  class_id: string | null
  class_name: string | null
  permission_keys: string[]
  valid_from: string
  valid_until: string | null
  ended_at: string | null
  end_reason: string | null
  manage_allowed: boolean
}

export type AnalysisProfile = {
  id: string
  project_id: string
  project_name: string
  version: number
  mode: "manual_only" | "automatic"
  adapter_id: string | null
  adapter_version: string | null
  scoring_rule_version: string | null
  required_fields: string[]
  confidence_thresholds: Record<string, number>
  auto_publish_warning_levels: string[]
  publish_mode: "auto_publish" | "review_required"
  effective_from: string
  configured_by_name: string | null
  change_reason: string
  is_current: boolean
}

export type StudentDirectoryItem = {
  student_person_id: string
  student_name: string
  student_number: string
}

export type StaffDirectoryItem = {
  staff_person_id: string
  staff_name: string
  staff_type: "coach" | "life_teacher" | "customer_service" | "administration" | "other" | "team_manager" | "assistant_coach"
}

export type StaffRole = {
  id: string
  key: string
  name: string
  category: "teaching" | "life_service" | "operations"
  is_builtin: boolean
  is_active: boolean
}

export type FoundationPage<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

const DIRECTORY_PAGE_SIZE = 100
const MAX_DIRECTORY_PAGES = 10

export class EvaluationDirectoryError extends Error {
  constructor(message: string) {
    super(message)
    this.name = "EvaluationDirectoryError"
  }
}

async function collectDirectoryPages<T>(
  loadPage: (page: number, pageSize: number) => Promise<FoundationPage<T>>,
): Promise<T[]> {
  const items: T[] = []
  const seenPages = new Set<number>()
  for (let requestedPage = 1; requestedPage <= MAX_DIRECTORY_PAGES; requestedPage += 1) {
    const response = await loadPage(requestedPage, DIRECTORY_PAGE_SIZE)
    if (response.page !== requestedPage || seenPages.has(response.page)) {
      throw new EvaluationDirectoryError("目录分页响应无效，请重试。")
    }
    seenPages.add(response.page)
    items.push(...response.items)
    if (
      items.length >= response.total ||
      response.items.length === 0 ||
      response.page * Math.max(1, response.page_size) >= response.total
    ) {
      return items
    }
  }
  throw new EvaluationDirectoryError("目录结果过多，请使用搜索缩小范围。")
}

export type GameAccountLimit = {
  student_person_id: string
  active_count: number
  active_limit: number
}

export type StudentMatchingProfile = GameAccountLimit & {
  student_name: string
  student_number: string
  game_accounts: StudentGameAccount[]
  game_accounts_total: number
  game_accounts_truncated: boolean
  auxiliary_phones: AuxiliaryPhone[]
  auxiliary_phones_total: number
  auxiliary_phones_truncated: boolean
}

export type AnalysisProfileInput = {
  expected_current_version: number
  mode: "manual_only" | "automatic"
  adapter_id: string | null
  adapter_version: string | null
  scoring_rule_version: string | null
  result_schema_version?: string
  required_fields: string[]
  media_slots?: Array<{
    key: string
    label: string
    required: boolean
  }>
  confidence_thresholds: Record<string, number>
  auto_publish_warning_levels: string[]
  publish_mode: "auto_publish" | "review_required"
  effective_from: string
  change_reason: string
}

export const evaluationFoundationKeys = {
  root: (accountId: string) => ["evaluations", accountId, "foundation"] as const,
  targets: (accountId: string) =>
    ["evaluations", accountId, "foundation", "game-account-targets"] as const,
  gameAccounts: (accountId: string, studentId: string) =>
    ["evaluations", accountId, "foundation", "game-accounts", studentId] as const,
  gameAccountHistory: (accountId: string, studentId: string, pageSize: number) =>
    [
      ...evaluationFoundationKeys.gameAccounts(accountId, studentId),
      "include-unbound",
      true,
      "page-size",
      pageSize,
    ] as const,
  gameAccountProjects: (accountId: string, studentId: string) =>
    ["evaluations", accountId, "foundation", "game-account-projects", studentId] as const,
  admin: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin"] as const,
  staffCohorts: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin", "cohorts"] as const,
  staffScopes: (accountId: string, cohortId: string, scopeKind: "project" | "class") =>
    [
      "evaluations",
      accountId,
      "foundation",
      "admin",
      "cohorts",
      cohortId,
      "staff-scopes",
      scopeKind,
    ] as const,
  analysisProjects: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin", "analysis-projects"] as const,
  certificationProjects: (accountId: string) =>
    ["evaluations", accountId, "foundation", "admin", "certification-projects"] as const,
}

function jsonRequest<T>(path: string, method: "POST" | "PUT", input?: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method,
    ...(input === undefined
      ? {}
      : {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(input),
        }),
  })
}

export function listGameAccountTargets(): Promise<GameAccountTarget[]> {
  return apiFetch("/evaluations/game-account-targets")
}

export function listGameAccountProjects(
  studentId: string,
  input: { page?: number; pageSize?: number } = {},
): Promise<FoundationPage<EvaluationProjectDirectoryItem>> {
  const query = new URLSearchParams({
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 100),
  })
  return apiFetch(`/evaluations/students/${studentId}/game-account-projects?${query}`)
}

export function listAllGameAccountProjects(
  studentId: string,
): Promise<EvaluationProjectDirectoryItem[]> {
  return collectDirectoryPages((page, pageSize) =>
    listGameAccountProjects(studentId, { page, pageSize }),
  )
}

export function listGameAccounts(
  studentId: string,
  input: { includeUnbound?: boolean; page?: number; pageSize?: number } = {},
): Promise<StudentGameAccount[]> {
  const query = new URLSearchParams({
    include_unbound: String(input.includeUnbound ?? true),
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 100),
  })
  return apiFetch(`/evaluations/students/${studentId}/game-accounts?${query}`)
}

export function bindGameAccount(
  studentId: string,
  input: { project_id: string; game_id: string },
): Promise<StudentGameAccount> {
  return jsonRequest(`/evaluations/students/${studentId}/game-accounts`, "POST", input)
}

export function unbindGameAccount(
  studentId: string,
  accountId: string,
  input: { reason: string },
): Promise<StudentGameAccount> {
  return jsonRequest(
    `/evaluations/students/${studentId}/game-accounts/${accountId}/unbind`,
    "POST",
    input,
  )
}

export function listStudents(
  input: { query?: string; page?: number; pageSize?: number } = {},
): Promise<FoundationPage<StudentDirectoryItem>> {
  const query = new URLSearchParams({
    query: input.query ?? "",
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 20),
  })
  return apiFetch(`/evaluations/admin/students?${query}`)
}

export function getStudentMatchingProfile(studentId: string): Promise<StudentMatchingProfile> {
  return apiFetch(`/evaluations/admin/students/${studentId}/matching-profile`)
}

export function getGameAccountLimit(studentId: string): Promise<GameAccountLimit> {
  return apiFetch(`/evaluations/admin/students/${studentId}/game-account-limit`)
}

export function updateGameAccountLimit(
  studentId: string,
  input: { max_active_accounts: number; reason: string },
): Promise<GameAccountLimit> {
  return jsonRequest(`/evaluations/admin/students/${studentId}/game-account-limit`, "PUT", input)
}

export function listAuxiliaryPhones(
  studentId: string,
  input: { includeInactive?: boolean; page?: number; pageSize?: number } = {},
): Promise<AuxiliaryPhone[]> {
  const query = new URLSearchParams({
    include_inactive: String(input.includeInactive ?? true),
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 100),
  })
  return apiFetch(`/evaluations/admin/students/${studentId}/auxiliary-phones?${query}`)
}

export function addAuxiliaryPhone(
  studentId: string,
  write: AuxiliaryPhoneWriteOnly,
): Promise<AuxiliaryPhone> {
  const { phone } = consumeAuxiliaryPhoneWrite(write)
  return jsonRequest(`/evaluations/admin/students/${studentId}/auxiliary-phones`, "POST", {
    phone,
  })
}

export function replaceAuxiliaryPhone(
  studentId: string,
  phoneId: string,
  write: AuxiliaryPhoneWriteOnly,
): Promise<AuxiliaryPhone> {
  const { phone, reason } = consumeAuxiliaryPhoneWrite(write)
  return jsonRequest(
    `/evaluations/admin/students/${studentId}/auxiliary-phones/${phoneId}/replace`,
    "POST",
    { phone, reason },
  )
}

export function deactivateAuxiliaryPhone(
  studentId: string,
  phoneId: string,
  input: { reason: string },
): Promise<AuxiliaryPhone> {
  return jsonRequest(
    `/evaluations/admin/students/${studentId}/auxiliary-phones/${phoneId}/deactivate`,
    "POST",
    input,
  )
}

export function listStaffDirectory(
  input: { cohortId?: string; query?: string; page?: number; pageSize?: number } = {},
): Promise<FoundationPage<StaffDirectoryItem>> {
  const query = new URLSearchParams()
  if (input.cohortId) query.set("cohort_id", input.cohortId)
  query.set("query", input.query ?? "")
  query.set("page", String(input.page ?? 1))
  query.set("page_size", String(input.pageSize ?? 20))
  return apiFetch(`/evaluations/admin/staff-directory?${query}`)
}

export function listEvaluationCohorts(
  input: { query?: string; page?: number; pageSize?: number } = {},
): Promise<FoundationPage<EvaluationCohortDirectoryItem>> {
  const query = new URLSearchParams({
    query: input.query ?? "",
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 20),
  })
  return apiFetch(`/evaluations/admin/cohorts?${query}`)
}

export function listAllEvaluationCohorts(): Promise<EvaluationCohortDirectoryItem[]> {
  return collectDirectoryPages((page, pageSize) =>
    listEvaluationCohorts({ page, pageSize }),
  )
}

export function listEvaluationStaffScopes(
  cohortId: string,
  input: { scopeKind: "project" | "class"; page?: number; pageSize?: number },
): Promise<FoundationPage<EvaluationStaffScopeDirectoryItem>> {
  const query = new URLSearchParams({
    scope_kind: input.scopeKind,
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 20),
  })
  return apiFetch(`/evaluations/admin/cohorts/${cohortId}/staff-scopes?${query}`)
}

export function listAllEvaluationStaffScopes(
  cohortId: string,
  scopeKind: "project" | "class",
): Promise<EvaluationStaffScopeDirectoryItem[]> {
  return collectDirectoryPages((page, pageSize) =>
    listEvaluationStaffScopes(cohortId, { scopeKind, page, pageSize }),
  )
}

export function listStaffRoles(): Promise<StaffRole[]> {
  return apiFetch("/evaluations/admin/staff-roles")
}

export function createStaffRole(input: {
  key: string
  name: string
  category: StaffRole["category"]
}): Promise<StaffRole> {
  return jsonRequest("/evaluations/admin/staff-roles", "POST", input)
}

export function deactivateStaffRole(roleId: string): Promise<StaffRole> {
  return jsonRequest(`/evaluations/admin/staff-roles/${roleId}/deactivate`, "POST")
}

export function listCohortStaffAssignments(cohortId: string): Promise<StaffServiceItem[]> {
  return apiFetch(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`)
}

export function createCohortStaffAssignment(
  cohortId: string,
  input: {
    role_id: string
    staff_person_id: string
    scope_kind: "project" | "class"
    scope_id: string
    valid_from: string
    valid_until: string | null
    permission_keys: string[]
  },
): Promise<StaffServiceItem> {
  return jsonRequest(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`, "POST", input)
}

export function endStaffAssignment(
  assignmentId: string,
  input: { reason: string },
): Promise<StaffServiceItem> {
  return jsonRequest(`/evaluations/admin/staff-assignments/${assignmentId}/end`, "POST", input)
}

export function listAnalysisProfiles(projectId: string): Promise<AnalysisProfile[]> {
  return apiFetch(`/evaluations/admin/projects/${projectId}/analysis-profiles`)
}

export function listEvaluationProjects(
  input: { query?: string; page?: number; pageSize?: number } = {},
): Promise<FoundationPage<EvaluationProjectDirectoryItem>> {
  const query = new URLSearchParams({
    query: input.query ?? "",
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 20),
  })
  return apiFetch(`/evaluations/admin/projects?${query}`)
}

export function listAllEvaluationProjects(): Promise<EvaluationProjectDirectoryItem[]> {
  return collectDirectoryPages((page, pageSize) =>
    listEvaluationProjects({ page, pageSize }),
  )
}

export function listCertificationProjects(
  input: { query?: string; page?: number; pageSize?: number } = {},
): Promise<FoundationPage<EvaluationProjectDirectoryItem>> {
  const query = new URLSearchParams({
    query: input.query ?? "",
    page: String(input.page ?? 1),
    page_size: String(input.pageSize ?? 20),
  })
  return apiFetch(`/evaluations/admin/adapter-certification-projects?${query}`)
}

export function listAllCertificationProjects(): Promise<EvaluationProjectDirectoryItem[]> {
  return collectDirectoryPages((page, pageSize) =>
    listCertificationProjects({ page, pageSize }),
  )
}

export function createAnalysisProfile(
  projectId: string,
  input: AnalysisProfileInput,
): Promise<AnalysisProfile> {
  return jsonRequest(`/evaluations/admin/projects/${projectId}/analysis-profiles`, "POST", input)
}
