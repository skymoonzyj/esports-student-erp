import { z } from "zod"

import { apiFetch, ProblemDetailsError } from "../api/client"

export type PortalLearningMode = "self" | "family" | "self_and_family"
export type PortalAccessSource = "self" | "family"
export type AgeStatus = "adult" | "minor"
export type ClassType = "standard" | "elite"
export type AssignmentType = "primary" | "assistant"
export type EnrollmentStatus = "active" | "cancelled" | "completed"

export type PortalContext = {
  coach_available: boolean
  learning_available: boolean
  payments_available: boolean
  housing_management_available: boolean
  housing_available: boolean
  learning_mode: PortalLearningMode | null
}

export type PortalCapability = keyof Pick<
  PortalContext,
  | "coach_available"
  | "learning_available"
  | "payments_available"
  | "housing_management_available"
  | "housing_available"
>

export type CoachStartOption = {
  anchor_cohort_id: string
  starts_at: string
  cohort_names: string[]
  class_count: number
}

export type CoachClassSummary = {
  cohort_id: string
  cohort_name: string
  starts_at: string
  ends_at: string
  class_id: string
  class_name: string
  project_name: string
  class_type: ClassType
  professional_team_name: string | null
  assignment_type: AssignmentType
  student_count: number
}

export type CoachRosterStudent = {
  enrollment_id: string
  student_person_id: string
  student_name: string
  student_number: string
  age_status: AgeStatus
  cohort_id: string
  cohort_name: string
  project_name: string
  class_id: string
  class_name: string
  class_type: ClassType
  professional_team_name: string | null
  assignment_type: AssignmentType
  enrollment_status: "active"
}

export type CoachNextCohorts = {
  observed_at: string
  selected_starts_at: string | null
  options: CoachStartOption[]
  classes: CoachClassSummary[]
  items: CoachRosterStudent[]
  page: number
  page_size: number
  total: number
}

export type LearningEnrollment = {
  enrollment_id: string
  cohort_id: string
  cohort_name: string
  starts_at: string
  ends_at: string
  project_name: string
  class_id: string
  class_name: string
  class_type: ClassType
  professional_team_name: string | null
  status: EnrollmentStatus
  confirmed_at: string
  cancelled_at: string | null
  completed_at: string | null
}

export type LearningStudent = {
  student_person_id: string
  student_name: string
  student_number: string
  age_status: AgeStatus
  access_source: PortalAccessSource
  current_enrollment: LearningEnrollment | null
}

export type LearningStudents = {
  items: LearningStudent[]
  total: number
}

export type LearningTeaching = {
  student: LearningStudent
  items: LearningEnrollment[]
  page: number
  page_size: number
  total: number
}

export type PortalRefund = {
  refund_id: string
  amount_cents: number
  refunded_at: string
  refund_type: "manual" | "withdrawal_prorated" | "withdrawal_full"
}

export type PortalPayment = {
  payment_id: string
  source_kind: "api" | "manual"
  amount_cents: number
  currency: "CNY"
  paid_at: string
  refunded_cents: number
  net_cents: number
  status: "paid" | "partially_refunded" | "refunded"
  related_student_name: string | null
  refunds: PortalRefund[]
}

export type SelfPayments = {
  items: PortalPayment[]
  page: number
  page_size: number
  total: number
}

const housingPortalStaySchema = z.object({
  building_name: z.string(),
  floor_name: z.string(),
  room_name: z.string(),
  stay_purpose: z.enum(["permanent", "camp", "temporary"]),
  planned_check_in_at: z.string(),
  planned_check_out_at: z.string().nullable(),
  actual_check_in_at: z.string().nullable(),
  actual_check_out_at: z.string().nullable(),
  status: z.enum(["planned", "checked_in", "checkout_pending"]),
  roommates: z.array(z.string()).nullable(),
})

const housingPortalSubjectSchema = z.object({
  subject_person_id: z.uuid(),
  subject_name: z.string(),
  current_stay: housingPortalStaySchema.nullable(),
  planned_stays: z.array(housingPortalStaySchema),
})

const myHousingSchema = z.object({
  as_of: z.string(),
  subjects: z.array(housingPortalSubjectSchema),
})

export type HousingPortalStay = z.infer<typeof housingPortalStaySchema>
export type HousingPortalSubject = z.infer<typeof housingPortalSubjectSchema>
export type MyHousing = z.infer<typeof myHousingSchema>

export const portalKeys = {
  root: (accountId: string) => ["portal", accountId] as const,
  context: (accountId: string) => ["portal", accountId, "context"] as const,
  coach: (
    accountId: string,
    anchorCohortId: string | undefined,
    page: number,
  ) => ["portal", accountId, "coach", anchorCohortId ?? null, page] as const,
  students: (accountId: string) => ["portal", accountId, "students"] as const,
  teaching: (accountId: string, studentId: string, page: number) =>
    ["portal", accountId, "teaching", studentId, page] as const,
  payments: (accountId: string, page: number) =>
    ["portal", accountId, "payments", page] as const,
  housing: (accountId: string) => ["portal", accountId, "housing"] as const,
}

function pageQuery(page = 1, pageSize = 20): URLSearchParams {
  return new URLSearchParams({ page: String(page), page_size: String(pageSize) })
}

export function getPortalContext(): Promise<PortalContext> {
  return apiFetch<PortalContext>("/portal/context")
}

export function getCoachNextCohorts(
  input: {
    anchorCohortId?: string
    page?: number
    pageSize?: number
  } = {},
): Promise<CoachNextCohorts> {
  const query = new URLSearchParams()
  if (input.anchorCohortId) {
    query.set("anchor_cohort_id", input.anchorCohortId)
  }
  query.set("page", String(input.page ?? 1))
  query.set("page_size", String(input.pageSize ?? 20))
  return apiFetch<CoachNextCohorts>(`/coach/next-cohorts?${query}`)
}

export function getLearningStudents(): Promise<LearningStudents> {
  return apiFetch<LearningStudents>("/family/students")
}

export function getLearningTeaching(
  studentId: string,
  input: { page?: number; pageSize?: number } = {},
): Promise<LearningTeaching> {
  const query = pageQuery(input.page, input.pageSize)
  return apiFetch<LearningTeaching>(
    `/family/students/${studentId}/teaching?${query}`,
  )
}

export function getSelfPayments(
  input: { page?: number; pageSize?: number } = {},
): Promise<SelfPayments> {
  const query = pageQuery(input.page, input.pageSize)
  return apiFetch<SelfPayments>(`/me/payments?${query}`)
}

export async function getMyHousing(signal?: AbortSignal): Promise<MyHousing> {
  let raw: unknown
  try {
    raw = await apiFetch<unknown>("/me/housing", { signal })
  } catch (error) {
    if (error instanceof ProblemDetailsError) {
      throw new ProblemDetailsError({
        type: "about:blank",
        title: "Request failed",
        status: error.problem.status,
        detail: "服务暂时不可用，请重试。",
        code: "housing_request_failed",
        request_id: "",
      })
    }
    if (error instanceof SyntaxError) {
      // Do not retain the parse error as `cause`: browser JSON errors can echo
      // sensitive response-body fragments into TanStack Query's error cache.
      // eslint-disable-next-line preserve-caught-error
      throw new Error("个人住宿响应格式无效")
    }
    throw error
  }
  const parsed = myHousingSchema.safeParse(raw)
  if (!parsed.success) throw new Error("个人住宿响应格式无效")
  return parsed.data
}
