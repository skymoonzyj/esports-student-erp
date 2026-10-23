import { apiFetch } from "../api/client"

export type EnrollmentProgramCategory =
  | "weekend"
  | "experience"
  | "family"
  | "summer"

export type EnrollmentPaymentPreference = "offline"
export type EnrollmentApplicationStatus =
  | "pending_match"
  | "ready"
  | "enrolled"
  | "closed"
export type EnrollmentPaymentStatus =
  | "unpaid"
  | "partial"
  | "paid"
  | "overpaid"
  | "refunded"

export type EnrollmentApplicantStudent = {
  student_person_id: string
  student_name: string
  student_number: string
  age_status: "adult" | "minor"
  access_source: "self" | "family"
}

export type EnrollmentProject = {
  id: string
  name: string
  coaches: Array<{
    person_id: string
    full_name: string
  }>
}

export type EnrollmentProgramScheduleItem = {
  time: string
  item: string
  note: string
}

export type EnrollmentProgram = {
  id: string
  code: string
  name: string
  category: EnrollmentProgramCategory
  duration_label: string
  price_cents: number
  price_note: string
  inclusion_note: string
  summary: string
  target_audience: string
  highlights: string[]
  schedule: EnrollmentProgramScheduleItem[]
  eligibility_note: string | null
}

export type EnrollmentApplication = {
  id: string
  order_number: string
  student_person_id: string
  student_name: string
  status: EnrollmentApplicationStatus
  project_id: string
  project_name: string
  program_id: string
  program_name: string
  coach_person_id?: string | null
  coach_name?: string | null
  price_cents: number
  payment_preference: EnrollmentPaymentPreference
  payment_status: EnrollmentPaymentStatus
  created_at: string
  updated_at: string
}

export type EnrollmentCatalog = {
  students: EnrollmentApplicantStudent[]
  projects: EnrollmentProject[]
  programs: EnrollmentProgram[]
  applications: EnrollmentApplication[]
}

export type EnrollmentApplicationInput = {
  student_person_id: string
  project_id: string
  program_id: string
  coach_person_id?: string
  payment_preference: EnrollmentPaymentPreference
}

export const enrollmentKeys = {
  catalog: (accountId: string) => ["enrollment-catalog", accountId] as const,
}

export function getEnrollmentCatalog(signal?: AbortSignal): Promise<EnrollmentCatalog> {
  return apiFetch<EnrollmentCatalog>("/enrollment-catalog", { signal })
}

export function createEnrollmentApplication(
  input: EnrollmentApplicationInput,
): Promise<EnrollmentApplication> {
  return apiFetch<EnrollmentApplication>("/enrollment-applications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}
