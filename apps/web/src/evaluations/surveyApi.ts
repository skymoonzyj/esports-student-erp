import { z } from "zod"
import { apiFetch } from "../api/client"
import { normalizeSurveyFilters, type SurveyListFilters } from "./surveyKeys"

const UUID = z.uuid()
const DT = z.iso.datetime({ offset: true })
const staffRole = z
  .object({ role_key: z.string(), role_name: z.string(), role_category: z.string() })
  .strict()
const staff = z
  .object({
    id: UUID,
    staff_name: z.string(),
    role_key: z.string(),
    role_name: z.string(),
    role_category: z.string(),
    roles: z.array(staffRole).default([]),
    service_intervals: z.array(z.record(z.string(), z.unknown())),
  })
  .strict()
const staffRating = z
  .object({
    staff_snapshot_id: UUID,
    score: z.number().int(),
    reason: z.string().nullable().default(null),
    suggestion: z.string().nullable().default(null),
  })
  .strict()
const latestSubmission = z
  .object({
    version: z.number().int(),
    course_training_score: z.number().int(),
    course_training_reason: z.string().nullable().default(null),
    life_service_score: z.number().int(),
    life_service_reason: z.string().nullable().default(null),
    service_management_score: z.number().int(),
    service_management_reason: z.string().nullable().default(null),
    overall_suggestion: z.string().nullable().default(null),
    favorite_cuisines: z.array(
      z.object({
        cuisine_option_id: UUID,
        name: z.string(),
        custom_name: z.string().nullable().default(null),
      }).strict(),
    ),
    favorite_dishes: z.array(z.string()),
    staff_ratings: z.array(staffRating),
  })
  .strict()
const context = z
  .object({
    cycle_id: UUID,
    student_person_id: UUID,
    student_name: z.string().min(1),
    opens_at: DT,
    due_at: DT,
    snapshot_version: z.number().int(),
    respondent_kind: z.string(),
    can_submit: z.boolean(),
    can_revise: z.boolean(),
    can_supplement: z.boolean().default(false),
    submission_id: UUID.nullable(),
    latest_version: z.number().int().nullable(),
    staff: z.array(staff),
    latest_submission: latestSubmission.nullable().default(null),
    supplement_staff: z.array(staff).default([]),
  })
  .strict()
const submission = z
  .object({
    submission_id: UUID,
    version_id: UUID,
    version: z.number().int(),
    published_at: DT,
    idempotent: z.boolean(),
  })
  .strict()
const supplement = z.object({ supplement_id: UUID, idempotent: z.boolean() }).strict()
const adminFeedbackItem = z
  .object({
    cycle_id: UUID.nullable(),
    enrollment_id: UUID.nullable(),
    cohort_id: UUID,
    version_id: UUID.nullable(),
    version: z.number().int(),
    ordinal: z.number().int(),
    staff_snapshot_id: UUID.nullable(),
    staff_person_id: UUID.nullable(),
    staff_name: z.string().nullable(),
    role_key: z.string().nullable(),
    role_name: z.string().nullable(),
    role_category: z.string().nullable(),
    score: z.number().int(),
    reason: z.string().nullable(),
    suggestion: z.string().nullable(),
    respondent_label: z.string(),
    respondent_person_id: UUID.nullable(),
    respondent_name: z.string().nullable(),
    respondent_kind: z.string().nullable(),
    course_training_score: z.number().int().nullable(),
    course_training_reason: z.string().nullable(),
    life_service_score: z.number().int().nullable(),
    life_service_reason: z.string().nullable(),
    service_management_score: z.number().int().nullable(),
    service_management_reason: z.string().nullable(),
    overall_suggestion: z.string().nullable(),
    favorite_cuisines: z.array(z.record(z.string(), z.unknown())).nullable(),
    favorite_dishes: z.array(z.string()).nullable(),
  })
  .strict()
const adminFeedbackPage = z
  .object({
    items: z.array(adminFeedbackItem),
    total: z.number().int(),
    page: z.number().int(),
    page_size: z.number().int(),
  })
  .strict()
const staffFeedbackItem = z
  .object({
    cycle_id: UUID,
    cohort_id: UUID,
    version: z.number().int(),
    ordinal: z.number().int(),
    role_category: z.string(),
    score: z.number().int(),
    reason: z.string().nullable(),
    suggestion: z.string().nullable(),
    course_training_score: z.number().int().nullable(),
    course_training_reason: z.string().nullable(),
    life_service_score: z.number().int().nullable(),
    life_service_reason: z.string().nullable(),
    service_management_score: z.number().int().nullable(),
    service_management_reason: z.string().nullable(),
    team_suggestion: z.string().nullable(),
    project_id: UUID.nullable(),
    role_key: z.string().nullable(),
    role_name: z.string().nullable(),
  })
  .strict()
const staffFeedbackPage = z
  .object({
    items: z.array(staffFeedbackItem),
    total: z.number().int(),
    page: z.number().int(),
    page_size: z.number().int(),
  })
  .strict()

export type SurveyContext = z.infer<typeof context>
export type SurveyStaff = z.infer<typeof staff>
export type SurveySubmissionInput = {
  idempotency_key: string
  expected_snapshot_version: number
  expected_food_preference_version: number
  favorite_cuisines: Array<{
    cuisine_option_id: string
    name: string
    custom_name?: string | null
  }>
  favorite_dishes: string[]
  course_training_score: number
  course_training_reason?: string | null
  life_service_score: number
  life_service_reason?: string | null
  service_management_score: number
  service_management_reason?: string | null
  overall_suggestion?: string | null
  staff_ratings: Array<{
    staff_snapshot_id: string
    score: number
    reason?: string | null
    suggestion?: string | null
  }>
}
export type SurveyFeedbackPage = z.infer<typeof adminFeedbackPage>
export type StaffSurveyFeedbackPage = z.infer<typeof staffFeedbackPage>

function parse<T>(schema: z.ZodType<T>, value: unknown): T {
  const result = schema.safeParse(value)
  if (!result.success) throw new Error("营期评价响应格式无效，请刷新重试。")
  return result.data
}

export async function getSurveyContext(
  page = 1,
  pageSize = 20,
  signal?: AbortSignal,
): Promise<SurveyContext[]> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  return parse(
    z.array(context),
    await apiFetch(`/evaluations/surveys/context?${qs.toString()}`, { signal }),
  )
}

export async function submitSurvey(
  cycleId: string,
  input: SurveySubmissionInput,
  signal?: AbortSignal,
) {
  return parse(
    submission,
    await apiFetch(`/evaluations/surveys/${encodeURIComponent(cycleId)}/submissions`, {
      method: "POST",
      signal,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    }),
  )
}

export async function reviseSurvey(
  cycleId: string,
  submissionId: string,
  input: SurveySubmissionInput,
  signal?: AbortSignal,
) {
  return parse(
    submission,
    await apiFetch(
      `/evaluations/surveys/${encodeURIComponent(cycleId)}/submissions/${encodeURIComponent(submissionId)}/revisions`,
      {
        method: "POST",
        signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  )
}

export type SurveySupplementInput = {
  idempotency_key: string
  expected_snapshot_version: number
  change_reason?: string
  staff_ratings: Array<{
    staff_snapshot_id: string
    score: number
    reason?: string | null
    suggestion?: string | null
  }>
}

export async function submitSurveySupplement(
  cycleId: string,
  submissionId: string,
  input: SurveySupplementInput,
  signal?: AbortSignal,
) {
  return parse(
    supplement,
    await apiFetch(
      `/evaluations/surveys/${encodeURIComponent(cycleId)}/submissions/${encodeURIComponent(submissionId)}/supplement`,
      {
        method: "POST",
        signal,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
  )
}

export async function getMySurveyFeedback(
  page = 1,
  pageSize = 50,
  signal?: AbortSignal,
  filters: Pick<SurveyListFilters, "cohortId" | "roleId" | "roleKey"> = {},
): Promise<StaffSurveyFeedbackPage> {
  const qs = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (filters.cohortId) qs.set("cohort_id", filters.cohortId)
  if (filters.roleId) qs.set("role_id", filters.roleId)
  if (filters.roleKey) qs.set("role_key", filters.roleKey)
  return parse(
    staffFeedbackPage,
    await apiFetch(`/evaluations/surveys/my-feedback?${qs.toString()}`, { signal }),
  )
}

export async function getAdminSurveyFeedback(
  filters: SurveyListFilters = {},
  signal?: AbortSignal,
): Promise<SurveyFeedbackPage> {
  const f = normalizeSurveyFilters(filters)
  const qs = new URLSearchParams({
    page: String(f.page),
    page_size: String(f.pageSize),
    identity: f.identity,
  })
  if (f.cohortId) qs.set("cohort_id", f.cohortId)
  if (f.projectId) qs.set("project_id", f.projectId)
  if (f.staffPersonId) qs.set("staff_id", f.staffPersonId)
  if (f.roleKey) qs.set("role_key", f.roleKey)
  return parse(
    adminFeedbackPage,
    await apiFetch(`/evaluations/admin/surveys?${qs.toString()}`, { signal }),
  )
}

export type SurveyPrintItem = {
  cohort_id: string
  project_id: string | null
  staff_person_id: string | null
  staff_name: string | null
  role_key: string
  role_name: string
  role_category: string
  service_intervals: Array<Record<string, unknown>>
  average_score: number
  item_count: number
  details: Array<{
    score: number
    reason: string | null
    suggestion: string | null
    ordinal: number
    respondent_person_id: string | null
    respondent_name: string | null
    respondent_kind: string | null
  }>
  team_suggestions: string[]
  roles: Array<{ role_key: string; role_name: string; role_category: string }>
}
const printItem = z
  .object({
    cohort_id: UUID,
    project_id: UUID.nullable().default(null),
    staff_person_id: UUID.nullable(),
    staff_name: z.string().nullable(),
    role_key: z.string(),
    role_name: z.string(),
    role_category: z.string(),
    service_intervals: z.array(z.record(z.string(), z.unknown())),
    average_score: z.number(),
    item_count: z.number().int(),
    details: z.array(
      z.object({
        score: z.number().int(),
        reason: z.string().nullable(),
        suggestion: z.string().nullable(),
        ordinal: z.number().int(),
        respondent_person_id: UUID.nullable(),
        respondent_name: z.string().nullable(),
        respondent_kind: z.string().nullable(),
      }),
    ),
    team_suggestions: z.array(z.string()).default([]),
    roles: z.array(staffRole).default([]),
  })
  .strict()
const printResponse = z
  .object({ identity: z.enum(["hidden", "real_name"]), items: z.array(printItem) })
  .strict()
  .superRefine((value, ctx) => {
    if (value.identity !== "hidden") return
    value.items.forEach((item, itemIndex) => {
      item.details.forEach((detail, detailIndex) => {
        if (
          detail.respondent_person_id !== null ||
          detail.respondent_name !== null ||
          detail.respondent_kind !== null
        ) {
          ctx.addIssue({
            code: "custom",
            path: ["items", itemIndex, "details", detailIndex],
            message: "hidden print projections must not contain respondent identity",
          })
        }
      })
    })
  })

export async function getSurveyPrint(
  filters: SurveyListFilters = {},
  signal?: AbortSignal,
) {
  const f = normalizeSurveyFilters(filters)
  const qs = new URLSearchParams({ identity: f.identity })
  if (f.cohortId) qs.set("cohort_id", f.cohortId)
  if (f.projectId) qs.set("project_id", f.projectId)
  if (f.staffPersonId) qs.set("staff_id", f.staffPersonId)
  if (f.roleKey) qs.set("role_key", f.roleKey)
  return parse(
    printResponse,
    await apiFetch(`/evaluations/admin/surveys/print-projection?${qs.toString()}`, { signal }),
  )
}
