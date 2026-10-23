import { apiFetch } from "../api/client"
import type { PersonTeamProjection } from "../teams/api"

export type PersonType = "student" | "guardian" | "staff" | "visitor"
export type StaffType =
  | "coach"
  | "life_teacher"
  | "customer_service"
  | "administration"
  | "other"

export type AgeStatus = "adult" | "minor" | "unknown"
export type TeamAllocationStatus = "untracked" | "pending" | "assigned" | "not_required"
export type TeamAllocationProjection = {
  visibility: "allowed" | "denied"
  status: TeamAllocationStatus | null
  version: number | null
  can_update: boolean
}
export type TeamAllocationUpdate = {
  status: "pending" | "not_required"
  expected_version: number
  idempotency_key: string
  reason: string
}
export type GuardianStatus = "ready" | "pending" | "not_applicable"
export type AssistedKind = "student" | "player" | "both" | "staff"
export type PersonAccountStatus = "active" | "disabled" | "deleted" | "not_activated"

export type PersonSummary = {
  id: string
  full_name: string
  nickname: string | null
  person_types: PersonType[]
  identity_number: string
  phone: string | null
  email: string | null
  sex: "male" | "female" | null
  date_of_birth: string | null
  document_type?: "cn_id" | "passport"
  document_issuing_country?: string | null
  intake_mode?: "strict" | "pending"
  staff_type: StaffType | null
  created_at: string
  age_status: AgeStatus
  guardian_status: GuardianStatus
  roles?: Array<{ key: string; name: string }>
  assisted_kind: AssistedKind | null
  account_status: PersonAccountStatus
  team?: PersonTeamProjection
  team_allocation?: TeamAllocationProjection
}

export type SelfProfile = {
  person_id: string
  full_name: string
  nickname: string | null
  person_types: PersonType[]
  staff_type: StaffType | null
  sex: "male" | "female"
  date_of_birth: string
  age_status: AgeStatus
  masked_identity: string
  masked_phone: string | null
  masked_email: string | null
}

export type PersonCounts = {
  total: number
  student: number
  guardian: number
  staff: number
  coach: number
  life_teacher: number
  customer_service: number
  administration: number
  other_staff: number
  visitor: number
  team_member?: number
  team_manager?: number
  in_team?: number
  pending_team_allocation?: number | null
  assigned_team_allocation?: number | null
}

export type PersonDetail = PersonSummary & {
  student_number: string | null
  student_status: "draft" | "active" | "inactive" | null
  staff_type: StaffType | null
  visit_reason: string | null
  linked_student_id: string | null
  account_management_allowed?: boolean
}

export type StaffPersonProfile = {
  person_id: string
  nickname: string | null
  profile_notes: string | null
}

export type StaffPersonProfileUpdate = Partial<Pick<StaffPersonProfile, "nickname" | "profile_notes">>

export type Page<T> = {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type PeopleFilters = {
  phone?: string
  name?: string
  type?: PersonType | ""
  staffType?: StaffType | ""
  role?: "team_member" | "team_manager" | ""
  teamAllocationStatus?: "pending" | "assigned" | ""
  page?: number
  pageSize?: number
}

export type PersonInput = {
  full_name: string
  identity_number: string
  sex: "male" | "female"
  date_of_birth: string
  phone?: string | null
  email?: string | null
  student_number?: string
  staff_type?: StaffType
  visit_reason?: string
  linked_student_id?: string | null
}

export type GuardianRelationship = PersonSummary & {
  relationship_id: string
  relationship: string
  is_primary: boolean
  valid_from: string
  valid_until: string | null
  status: "scheduled" | "active" | "expired" | "ended"
  ended_at: string | null
  end_reason: string | null
}

export type GuardianIssueCode =
  | "guardian_underage"
  | "guardian_profile_incomplete"
  | "guardian_account_missing"
  | "guardian_account_disabled"
  | "guardian_role_missing"
  | "relationship_not_started"
  | "relationship_expired"
  | "relationship_ended"
  | "relationship_verification_required"

export type GuardianIssue = {
  relationship_id: string
  guardian_person_id: string
  account_id: string | null
  reason_codes: GuardianIssueCode[]
}

export type Readiness = {
  ready: boolean
  missing: string[]
  age_status: AgeStatus
  guardian_status: GuardianStatus
  guardian_issues?: GuardianIssue[]
}

export function listPeople(filters: PeopleFilters = {}, signal?: AbortSignal): Promise<Page<PersonSummary>> {
  const query = new URLSearchParams()
  if (filters.phone) query.set("phone", filters.phone)
  if (filters.name) query.set("name", filters.name)
  if (filters.type) query.set("type", filters.type)
  if (filters.staffType) query.set("staff_type", filters.staffType)
  if (filters.role) query.set("role", filters.role)
  if (filters.teamAllocationStatus) query.set("team_allocation_status", filters.teamAllocationStatus)
  query.set("page", String(filters.page ?? 1))
  query.set("page_size", String(filters.pageSize ?? 20))
  return apiFetch<Page<PersonSummary>>(`/people?${query.toString()}`, { signal, cache: "no-store" })
}

export function getPeopleCounts(signal?: AbortSignal): Promise<PersonCounts> {
  return apiFetch<PersonCounts>("/people/counts", { signal, cache: "no-store" })
}

export function updateTeamAllocation(personId: string, input: TeamAllocationUpdate): Promise<TeamAllocationProjection> {
  return apiFetch<TeamAllocationProjection>(`/people/${personId}/team-allocation`, {
    method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  })
}

export function getPerson(personId: string, signal?: AbortSignal): Promise<PersonDetail> {
  return apiFetch<PersonDetail>(`/people/${personId}`, { signal, cache: "no-store" })
}

export function getStaffPersonProfile(personId: string, signal?: AbortSignal): Promise<StaffPersonProfile> {
  return apiFetch<StaffPersonProfile>(`/people/${personId}/staff-profile`, { signal, cache: "no-store" })
}

export const selfProfileKeys = {
  detail: (personId: string) => ["self-profile", personId] as const,
}

export function getSelfProfile(): Promise<SelfProfile> {
  return apiFetch<SelfProfile>("/people/me/profile")
}

export function updateSelfProfile(nickname: string | null): Promise<SelfProfile> {
  return apiFetch<SelfProfile>("/people/me/profile", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nickname }),
  })
}

export function createPerson(type: PersonType, input: PersonInput): Promise<PersonSummary> {
  const endpoint = type === "staff" ? "staff" : `${type}s`
  return apiFetch<PersonSummary>(`/people/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function updatePerson(personId: string, input: Partial<PersonInput> & StaffPersonProfileUpdate): Promise<PersonDetail> {
  return apiFetch<PersonDetail>(`/people/${personId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function revealSensitive(
  personId: string,
  field: "phone" | "identity_number",
  purpose: string,
): Promise<{ value: string }> {
  return apiFetch<{ value: string }>(`/people/${personId}/sensitive-reveal`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ field, purpose }),
  })
}

export function listGuardians(
  studentId: string,
  includeInactive = false,
): Promise<GuardianRelationship[]> {
  const query = includeInactive ? "?include_inactive=true" : ""
  return apiFetch<GuardianRelationship[]>(`/people/${studentId}/guardians${query}`)
}

export function addGuardian(
  studentId: string,
  input: {
    guardian_id: string
    relationship: string
    is_primary: boolean
    valid_from: string
    valid_until: string | null
  },
): Promise<GuardianRelationship> {
  return apiFetch<GuardianRelationship>(`/people/${studentId}/guardians`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function endGuardianRelationship(
  studentId: string,
  relationshipId: string,
  reason: string,
): Promise<GuardianRelationship> {
  return apiFetch<GuardianRelationship>(
    `/people/${studentId}/guardians/${relationshipId}/end`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    },
  )
}

export function getReadiness(studentId: string, referenceDate: string): Promise<Readiness> {
  return apiFetch<Readiness>(
    `/people/${studentId}/readiness?reference_date=${encodeURIComponent(referenceDate)}`,
  )
}
