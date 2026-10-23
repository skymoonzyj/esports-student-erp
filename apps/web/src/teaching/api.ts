import { apiFetch } from "../api/client"

export type CohortStatus =
  | "draft"
  | "open"
  | "upcoming"
  | "active"
  | "completed"
  | "archived"
export type TransitionTrigger = "manual" | "scheduler" | "request_reconciliation"
export type AutomationIssue =
  | "auto_start_transition_blocked"
  | "auto_complete_transition_blocked"
export type ClassType = "standard" | "elite"
export type AssignmentType = "primary" | "assistant"
export type Page<T> = { items: T[]; page: number; page_size: number; total: number }

export type GameProject = {
  id: string
  name: string
  sort_order: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export type ProfessionalTeam = {
  id: string
  project_id: string
  name: string
  notes: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export type Cohort = {
  id: string
  name: string
  starts_at: string
  ends_at: string
  default_check_in_at: string
  default_check_out_at: string
  status: CohortStatus
  auto_start_enabled: boolean
  auto_complete_enabled: boolean
  automation_issues: AutomationIssue[]
  enabled_class_count: number
  missing_primary_class_count: number
  created_at: string
  updated_at: string
}

export type CohortStatusEvent = {
  id: string
  cohort_id: string
  old_status: CohortStatus
  new_status: CohortStatus
  trigger: TransitionTrigger
  actor_user_id: string | null
  request_id: string
  occurred_at: string
}

export type TeachingClass = {
  id: string
  cohort_id: string
  project_id: string
  name: string
  class_type: ClassType
  professional_team_id: string | null
  capacity: number
  default_tuition_cents: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export type CoachAssignment = {
  id: string
  class_id: string
  coach_person_id: string
  coach_full_name: string
  assignment_type: AssignmentType
  valid_from: string
  valid_until: string | null
  ended_at: string | null
  ended_by_user_id: string | null
  end_reason: string | null
  created_at: string
  updated_at: string
}

export type EligibleCoach = { id: string; full_name: string }

export type ProjectFilters = {
  name?: string
  includeInactive?: boolean
  page?: number
  pageSize?: number
}

export type TeamFilters = ProjectFilters & { projectId?: string }
export type CohortFilters = {
  name?: string
  status?: CohortStatus
  dateFrom?: string
  dateTo?: string
  page?: number
  pageSize?: number
}
export type ClassFilters = {
  cohortId?: string
  projectId?: string
  name?: string
  isActive?: boolean
  page?: number
  pageSize?: number
}

function pageQuery(page = 1, pageSize = 20): URLSearchParams {
  return new URLSearchParams({ page: String(page), page_size: String(pageSize) })
}

function jsonRequest<T>(path: string, method: "POST" | "PATCH", input: unknown): Promise<T> {
  return apiFetch<T>(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function listProjects(filters: ProjectFilters = {}): Promise<Page<GameProject>> {
  const query = pageQuery(filters.page, filters.pageSize)
  if (filters.name) query.set("name", filters.name)
  if (filters.includeInactive !== undefined) {
    query.set("include_inactive", String(filters.includeInactive))
  }
  return apiFetch<Page<GameProject>>(`/projects?${query.toString()}`)
}

export function createProject(input: {
  name: string
  sort_order: number
}): Promise<GameProject> {
  return jsonRequest("/projects", "POST", input)
}

export function updateProject(
  projectId: string,
  input: Partial<Pick<GameProject, "name" | "sort_order" | "is_active">>,
): Promise<GameProject> {
  return jsonRequest(`/projects/${projectId}`, "PATCH", input)
}

export function listProfessionalTeams(
  filters: TeamFilters = {},
): Promise<Page<ProfessionalTeam>> {
  const query = pageQuery(filters.page, filters.pageSize)
  if (filters.projectId) query.set("project_id", filters.projectId)
  if (filters.name) query.set("name", filters.name)
  if (filters.includeInactive !== undefined) {
    query.set("include_inactive", String(filters.includeInactive))
  }
  return apiFetch<Page<ProfessionalTeam>>(`/professional-teams?${query.toString()}`)
}

export function createProfessionalTeam(input: {
  project_id: string
  name: string
  notes?: string | null
}): Promise<ProfessionalTeam> {
  return jsonRequest("/professional-teams", "POST", input)
}

export function updateProfessionalTeam(
  teamId: string,
  input: Partial<Pick<ProfessionalTeam, "name" | "notes" | "is_active">>,
): Promise<ProfessionalTeam> {
  return jsonRequest(`/professional-teams/${teamId}`, "PATCH", input)
}

export function listCohorts(filters: CohortFilters = {}): Promise<Page<Cohort>> {
  const query = pageQuery(filters.page, filters.pageSize)
  if (filters.name) query.set("name", filters.name)
  if (filters.status) query.set("status", filters.status)
  if (filters.dateFrom) query.set("date_from", filters.dateFrom)
  if (filters.dateTo) query.set("date_to", filters.dateTo)
  return apiFetch<Page<Cohort>>(`/cohorts?${query.toString()}`)
}

export type CohortInput = {
  name: string
  starts_at: string
  ends_at: string
  default_check_in_at: string
  default_check_out_at: string
  auto_start_enabled: boolean
  auto_complete_enabled: boolean
}

export function createCohort(input: CohortInput): Promise<Cohort> {
  return jsonRequest("/cohorts", "POST", input)
}

export function getCohort(cohortId: string): Promise<Cohort> {
  return apiFetch<Cohort>(`/cohorts/${cohortId}`)
}

export function updateCohort(
  cohortId: string,
  input: Partial<CohortInput>,
): Promise<Cohort> {
  return jsonRequest(`/cohorts/${cohortId}`, "PATCH", input)
}

export function transitionCohort(cohortId: string, target: CohortStatus): Promise<Cohort> {
  return jsonRequest(`/cohorts/${cohortId}/transitions`, "POST", { target })
}

export function listCohortStatusEvents(
  cohortId: string,
  page = 1,
  pageSize = 20,
): Promise<Page<CohortStatusEvent>> {
  const query = pageQuery(page, pageSize)
  return apiFetch<Page<CohortStatusEvent>>(
    `/cohorts/${cohortId}/status-events?${query.toString()}`,
  )
}

export type TeachingClassInput = {
  cohort_id: string
  project_id: string
  name: string
  class_type: ClassType
  professional_team_id: string | null
  capacity: number
  default_tuition_cents: number
}

export function listClasses(filters: ClassFilters = {}): Promise<Page<TeachingClass>> {
  const query = pageQuery(filters.page, filters.pageSize)
  if (filters.cohortId) query.set("cohort_id", filters.cohortId)
  if (filters.projectId) query.set("project_id", filters.projectId)
  if (filters.name) query.set("name", filters.name)
  if (filters.isActive !== undefined) query.set("is_active", String(filters.isActive))
  return apiFetch<Page<TeachingClass>>(`/classes?${query.toString()}`)
}

export function createClass(input: TeachingClassInput): Promise<TeachingClass> {
  return jsonRequest("/classes", "POST", input)
}

export function getClass(classId: string): Promise<TeachingClass> {
  return apiFetch<TeachingClass>(`/classes/${classId}`)
}

export function updateClass(
  classId: string,
  input: Partial<Omit<TeachingClassInput, "cohort_id" | "project_id">> & {
    is_active?: boolean
  },
): Promise<TeachingClass> {
  return jsonRequest(`/classes/${classId}`, "PATCH", input)
}

export type CoachAssignmentInput = {
  coach_person_id: string
  assignment_type: AssignmentType
  valid_from: string
  valid_until?: string | null
}

export function listCoachAssignments(
  classId: string,
  includeEnded = false,
  page = 1,
  pageSize = 20,
): Promise<Page<CoachAssignment>> {
  const query = pageQuery(page, pageSize)
  query.set("include_ended", String(includeEnded))
  return apiFetch<Page<CoachAssignment>>(
    `/classes/${classId}/coach-assignments?${query.toString()}`,
  )
}

export function createCoachAssignment(
  classId: string,
  input: CoachAssignmentInput,
): Promise<CoachAssignment> {
  return jsonRequest(`/classes/${classId}/coach-assignments`, "POST", input)
}

export function endCoachAssignment(
  classId: string,
  assignmentId: string,
  reason: string,
): Promise<CoachAssignment> {
  return jsonRequest(
    `/classes/${classId}/coach-assignments/${assignmentId}/end`,
    "POST",
    { reason },
  )
}

export function listEligibleCoaches(
  classId: string,
  queryText = "",
  page = 1,
  pageSize = 20,
): Promise<Page<EligibleCoach>> {
  const query = pageQuery(page, pageSize)
  if (queryText) query.set("query", queryText)
  return apiFetch<Page<EligibleCoach>>(
    `/classes/${classId}/eligible-coaches?${query.toString()}`,
  )
}
