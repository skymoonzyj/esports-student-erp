import { apiFetch } from "../api/client"

export type PersonType = "student" | "guardian" | "staff" | "visitor"
export type StaffType =
  | "coach"
  | "life_teacher"
  | "customer_service"
  | "administration"
  | "other"

export type AgeStatus = "adult" | "minor"
export type GuardianStatus = "ready" | "pending" | "not_applicable"

export type PersonSummary = {
  id: string
  full_name: string
  person_types: PersonType[]
  identity_number: string
  phone: string | null
  email: string | null
  sex: "male" | "female"
  date_of_birth: string
  created_at: string
  age_status: AgeStatus
  guardian_status: GuardianStatus
}

export type PersonDetail = PersonSummary & {
  student_number: string | null
  student_status: "draft" | "active" | "inactive" | null
  staff_type: StaffType | null
  visit_reason: string | null
  linked_student_id: string | null
  account_management_allowed?: boolean
}

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

export function listPeople(filters: PeopleFilters = {}): Promise<Page<PersonSummary>> {
  const query = new URLSearchParams()
  if (filters.phone) query.set("phone", filters.phone)
  if (filters.name) query.set("name", filters.name)
  if (filters.type) query.set("type", filters.type)
  query.set("page", String(filters.page ?? 1))
  query.set("page_size", String(filters.pageSize ?? 20))
  return apiFetch<Page<PersonSummary>>(`/people?${query.toString()}`)
}

export function getPerson(personId: string): Promise<PersonDetail> {
  return apiFetch<PersonDetail>(`/people/${personId}`)
}

export function createPerson(type: PersonType, input: PersonInput): Promise<PersonSummary> {
  const endpoint = type === "staff" ? "staff" : `${type}s`
  return apiFetch<PersonSummary>(`/people/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function updatePerson(personId: string, input: Partial<PersonInput>): Promise<PersonDetail> {
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
