import { z } from "zod"

import { apiFetch } from "../api/client"
import {
  normalizeAuthorReportFilters,
  normalizePublishedReportFilters,
  normalizeReportTargetFilters,
  normalizeReportVersionFilters,
  normalizeReviewQueueFilters,
  type AuthorReportFilters,
  type PublishedReportFilters,
  type ReportTargetFilters,
  type ReportVersionFilters,
  type ReviewQueueFilters,
} from "./reportKeys"

const INVALID_RESPONSE_MESSAGE = "教学评语响应格式无效，请刷新重试。"
const WRITE_EXPIRED_MESSAGE = "教学评语写入已失效，请重新填写。"

const UUID = z.uuid()
const DATE = z.string().regex(/^\d{4}-\d{2}-\d{2}$/)
const DATE_TIME = z.iso.datetime({ offset: true })
const POSITIVE_INTEGER = z.number().int().min(1)
const NON_NEGATIVE_INTEGER = z.number().int().min(0)
const EMPTY_DIMENSIONS = z.object({}).strict()

const reportKindSchema = z.enum(["daily", "weekly"])
const reportStatusSchema = z.enum([
  "draft",
  "pending_review",
  "rejected",
  "correction_draft",
  "published",
  "revision_pending_review",
])
const reportSourceSchema = z.enum(["initial", "correction", "revision"])
const reportDecisionSchema = z.enum(["approved", "rejected"])
const reportActionSchema = z.enum([
  "save_draft",
  "submit",
  "start_correction",
  "start_revision",
])

const contextSchema = z.object({
  author_available: z.boolean(),
  reviewer_available: z.boolean(),
  family_available: z.boolean(),
})

const targetItemSchema = z.object({
  enrollment_id: UUID,
  student_person_id: UUID,
  student_name: z.string().min(1),
  student_number: z.string().min(1),
  cohort_id: UUID,
  cohort_name: z.string().min(1),
  cohort_starts_at: DATE_TIME,
  cohort_ends_at: DATE_TIME,
  project_id: UUID,
  project_name: z.string().min(1),
  class_id: UUID,
  class_name: z.string().min(1),
})

const targetCohortFacetSchema = z.object({
  id: UUID,
  name: z.string().min(1),
  starts_at: DATE_TIME,
  ends_at: DATE_TIME,
})

const targetProjectFacetSchema = z.object({
  id: UUID,
  name: z.string().min(1),
  cohort_ids: z.array(UUID),
})

const targetClassFacetSchema = z.object({
  id: UUID,
  name: z.string().min(1),
  cohort_id: UUID,
  project_id: UUID,
})

const targetPageSchema = z.object({
  items: z.array(targetItemSchema),
  facets: z.object({
    cohorts: z.array(targetCohortFacetSchema),
    projects: z.array(targetProjectFacetSchema),
    classes: z.array(targetClassFacetSchema),
  }),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: POSITIVE_INTEGER.max(100),
})

const versionReviewSchema = z.discriminatedUnion("decision", [
  z.object({
    decision: z.literal("approved"),
    reason: z.null(),
    reviewed_at: DATE_TIME,
  }),
  z.object({
    decision: z.literal("rejected"),
    reason: z.string().max(2000).refine((reason) => reason.trim().length > 0),
    reviewed_at: DATE_TIME,
  }),
])

const versionItemSchema = z.object({
  report_id: UUID,
  version_id: UUID,
  version: POSITIVE_INTEGER,
  source: reportSourceSchema,
  body: z.string().min(1).max(5000),
  dimension_values: EMPTY_DIMENSIONS,
  submitted_at: DATE_TIME,
  is_current: z.boolean(),
  is_published: z.boolean(),
  review: versionReviewSchema.nullable(),
})

const versionPageSchema = z.object({
  items: z.array(versionItemSchema),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: POSITIVE_INTEGER.max(100),
})

const slotSchema = z.object({
  enrollment_id: UUID,
  student_person_id: UUID,
  cohort_id: UUID,
  cohort_name: z.string().min(1),
  project_id: UUID,
  project_name: z.string().min(1),
  class_id: UUID,
  class_name: z.string().min(1),
  report_kind: reportKindSchema,
  period_sequence: POSITIVE_INTEGER,
  period_start_date: DATE,
  period_end_date: DATE,
  submission_opens_at: DATE_TIME,
  initial_submission_deadline: DATE_TIME,
  report_id: UUID.nullable(),
  status: reportStatusSchema.nullable(),
  lock_version: POSITIVE_INTEGER.nullable(),
  draft_body: z.string().max(5000).nullable(),
  draft_dimension_values: EMPTY_DIMENSIONS.nullable(),
  draft_revision: POSITIVE_INTEGER.nullable(),
  allowed_actions: z.array(reportActionSchema),
})

const slotListSchema = z.object({ items: z.array(slotSchema) })
const reportPageSchema = z.object({
  items: z.array(slotSchema),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: POSITIVE_INTEGER.max(100),
})

const reviewQueueItemSchema = z.object({
  report_id: UUID,
  enrollment_id: UUID,
  cohort_id: UUID,
  cohort_name: z.string().min(1),
  project_id: UUID,
  project_name: z.string().min(1),
  class_id: UUID,
  class_name: z.string().min(1),
  report_kind: reportKindSchema,
  period_sequence: POSITIVE_INTEGER,
  period_start_date: DATE,
  period_end_date: DATE,
  status: reportStatusSchema,
  lock_version: POSITIVE_INTEGER,
  author_name: z.string().min(1),
  role_name: z.string().min(1),
  version_id: UUID,
  version: POSITIVE_INTEGER,
  source: reportSourceSchema,
  body: z.string().min(1).max(5000),
  dimension_values: EMPTY_DIMENSIONS,
  submitted_at: DATE_TIME,
})

const reviewQueuePageSchema = z.object({
  items: z.array(reviewQueueItemSchema),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: POSITIVE_INTEGER.max(100),
})

const publishedItemSchema = z.object({
  report_id: UUID,
  enrollment_id: UUID,
  cohort_id: UUID,
  cohort_name: z.string().min(1),
  project_id: UUID,
  project_name: z.string().min(1),
  class_id: UUID,
  class_name: z.string().min(1),
  report_kind: reportKindSchema,
  period_sequence: POSITIVE_INTEGER,
  period_start_date: DATE,
  period_end_date: DATE,
  author_name: z.string().min(1),
  role_name: z.string().min(1),
  version_id: UUID,
  version: POSITIVE_INTEGER,
  body: z.string().min(1).max(5000),
  dimension_values: EMPTY_DIMENSIONS,
  published_at: DATE_TIME,
})

const publishedPageSchema = z.object({
  items: z.array(publishedItemSchema),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: POSITIVE_INTEGER.max(100),
})

const draftResponseSchema = z.object({
  report_id: UUID,
  status: reportStatusSchema,
  lock_version: POSITIVE_INTEGER,
  draft_revision: POSITIVE_INTEGER,
  body: z.string().min(1).max(5000),
  dimension_values: EMPTY_DIMENSIONS,
  idempotent: z.boolean(),
  allowed_actions: z.array(reportActionSchema),
})

const draftStartResponseSchema = z.object({
  report_id: UUID,
  status: reportStatusSchema,
  lock_version: POSITIVE_INTEGER,
  draft_revision: POSITIVE_INTEGER,
  body: z.string().min(1).max(5000),
  dimension_values: EMPTY_DIMENSIONS,
  idempotent: z.boolean(),
})

const submitResponseSchema = z.object({
  report_id: UUID,
  status: reportStatusSchema,
  lock_version: POSITIVE_INTEGER,
  version_id: UUID,
  version: POSITIVE_INTEGER,
  source: reportSourceSchema,
  published_version_id: UUID.nullable(),
  idempotent: z.boolean(),
})

const reviewResponseSchema = z.object({
  report_id: UUID,
  status: reportStatusSchema,
  lock_version: POSITIVE_INTEGER,
  published_version_id: UUID.nullable(),
  review_id: UUID,
  decision: reportDecisionSchema,
  idempotent: z.boolean(),
})

const reopenResponseSchema = z.object({
  report_id: UUID,
  status: reportStatusSchema,
  lock_version: POSITIVE_INTEGER,
  grant_id: UUID,
  grant_kind: z.enum(["correction", "revision"]),
  opens_at: DATE_TIME,
  expires_at: DATE_TIME,
  idempotent: z.boolean(),
})

export type TeachingReportContext = z.infer<typeof contextSchema>
export type TeachingReportTarget = z.infer<typeof targetItemSchema>
export type TeachingReportTargetPage = z.infer<typeof targetPageSchema>
export type TeachingReportVersion = z.infer<typeof versionItemSchema>
export type TeachingReportVersionPage = z.infer<typeof versionPageSchema>
export type TeachingReportSlot = z.infer<typeof slotSchema>
export type TeachingReportSlotList = z.infer<typeof slotListSchema>
export type TeachingReportPage = z.infer<typeof reportPageSchema>
export type TeachingReportReviewQueueItem = z.infer<
  typeof reviewQueueItemSchema
>
export type TeachingReportReviewQueuePage = z.infer<
  typeof reviewQueuePageSchema
>
export type PublishedTeachingReport = z.infer<typeof publishedItemSchema>
export type PublishedTeachingReportPage = z.infer<typeof publishedPageSchema>
export type TeachingReportDraftResponse = z.infer<typeof draftResponseSchema>
export type TeachingReportDraftStartResponse = z.infer<
  typeof draftStartResponseSchema
>
export type TeachingReportSubmitResponse = z.infer<typeof submitResponseSchema>
export type TeachingReportReviewResponse = z.infer<typeof reviewResponseSchema>
export type TeachingReportReopenResponse = z.infer<typeof reopenResponseSchema>

function invalidResponse(): Error {
  return new Error(INVALID_RESPONSE_MESSAGE)
}

async function fetchDecoded<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: RequestInit,
): Promise<T> {
  let raw: unknown
  try {
    raw = await apiFetch<unknown>(path, init)
  } catch (error) {
    if (error instanceof SyntaxError) throw invalidResponse()
    throw error
  }
  const parsed = schema.safeParse(raw)
  if (!parsed.success) throw invalidResponse()
  return parsed.data
}

export function getTeachingReportContext(
  signal?: AbortSignal,
): Promise<TeachingReportContext> {
  return fetchDecoded("/evaluations/reports/context", contextSchema, { signal })
}

export function listTeachingReportTargets(
  input: ReportTargetFilters = {},
  signal?: AbortSignal,
): Promise<TeachingReportTargetPage> {
  const filters = normalizeReportTargetFilters(input)
  const query = new URLSearchParams()
  if (filters.cohortId) query.set("cohort_id", filters.cohortId)
  if (filters.projectId) query.set("project_id", filters.projectId)
  if (filters.classId) query.set("class_id", filters.classId)
  if (filters.studentId) query.set("student_id", filters.studentId)
  if (filters.query) query.set("query", filters.query)
  query.set("page", String(filters.page))
  query.set("page_size", String(filters.pageSize))
  return fetchDecoded(
    `/evaluations/reports/targets?${query.toString()}`,
    targetPageSchema,
    { signal },
  )
}

export function listTeachingReportSlots(
  enrollmentId: string,
  signal?: AbortSignal,
): Promise<TeachingReportSlotList> {
  const query = new URLSearchParams({ enrollment_id: enrollmentId })
  return fetchDecoded(
    `/evaluations/reports/slots?${query.toString()}`,
    slotListSchema,
    { signal },
  )
}

export function listTeachingReports(
  input: AuthorReportFilters,
  signal?: AbortSignal,
): Promise<TeachingReportPage> {
  const filters = normalizeAuthorReportFilters(input)
  const query = new URLSearchParams({ enrollment_id: filters.enrollmentId })
  if (filters.kind) query.set("kind", filters.kind)
  if (filters.status) query.set("status", filters.status)
  query.set("page", String(filters.page))
  query.set("page_size", String(filters.pageSize))
  return fetchDecoded(
    `/evaluations/reports?${query.toString()}`,
    reportPageSchema,
    { signal },
  )
}

export function getTeachingReport(
  reportId: string,
  signal?: AbortSignal,
): Promise<TeachingReportSlot> {
  return fetchDecoded(`/evaluations/reports/${reportId}`, slotSchema, { signal })
}

export function listTeachingReportVersions(
  input: ReportVersionFilters,
  signal?: AbortSignal,
): Promise<TeachingReportVersionPage> {
  const filters = normalizeReportVersionFilters(input)
  const query = new URLSearchParams({
    page: String(filters.page),
    page_size: String(filters.pageSize),
  })
  return fetchDecoded(
    `/evaluations/reports/${filters.reportId}/versions?${query.toString()}`,
    versionPageSchema,
    { signal },
  )
}

export function listTeachingReportReviewQueue(
  input: ReviewQueueFilters = {},
  signal?: AbortSignal,
): Promise<TeachingReportReviewQueuePage> {
  const filters = normalizeReviewQueueFilters(input)
  const query = new URLSearchParams()
  if (filters.cohortId) query.set("cohort_id", filters.cohortId)
  if (filters.projectId) query.set("project_id", filters.projectId)
  if (filters.classId) query.set("class_id", filters.classId)
  query.set("page", String(filters.page))
  query.set("page_size", String(filters.pageSize))
  return fetchDecoded(
    `/evaluations/reports/review-queue?${query.toString()}`,
    reviewQueuePageSchema,
    { signal },
  )
}

export function listPublishedTeachingReports(
  input: PublishedReportFilters,
  signal?: AbortSignal,
): Promise<PublishedTeachingReportPage> {
  const filters = normalizePublishedReportFilters(input)
  const query = new URLSearchParams()
  if (filters.enrollmentId) query.set("enrollment_id", filters.enrollmentId)
  query.set("page", String(filters.page))
  query.set("page_size", String(filters.pageSize))
  return fetchDecoded(
    `/evaluations/students/${filters.studentId}/published-reports?${query.toString()}`,
    publishedPageSchema,
    { signal },
  )
}

export type TeachingReportDraftWriteInput = {
  kind: "draft"
  enrollment_id: string
  report_kind: "daily" | "weekly"
  period_start_date: string
  body: string
  dimension_values: Record<string, never>
  expected_draft_revision: number
}

export type TeachingReportSubmitWriteInput = {
  kind: "submit"
  expected_lock_version: number
  expected_draft_revision: number
}

export type TeachingReportDraftStartWriteInput = {
  kind: "correction" | "revision"
  expected_lock_version: number
}

export type TeachingReportReviewWriteInput = {
  kind: "review"
  expected_lock_version: number
  decision: "approved" | "rejected"
  reason: string | null
}

export type TeachingReportReopenWriteInput = {
  kind: "reopen"
  expected_lock_version: number
  grant_kind: "correction" | "revision"
  reason: string
}

export type TeachingReportWriteInput =
  | TeachingReportDraftWriteInput
  | TeachingReportSubmitWriteInput
  | TeachingReportDraftStartWriteInput
  | TeachingReportReviewWriteInput
  | TeachingReportReopenWriteInput

const teachingReportWriteBrand = Symbol("teaching-report-write-only")
export type TeachingReportWrite = {
  readonly [teachingReportWriteBrand]: true
}

const teachingReportWritePayloads = new WeakMap<
  TeachingReportWrite,
  TeachingReportWriteInput
>()

function snapshotWrite(input: TeachingReportWriteInput): TeachingReportWriteInput {
  if (input.kind === "draft") {
    return {
      kind: input.kind,
      enrollment_id: input.enrollment_id,
      report_kind: input.report_kind,
      period_start_date: input.period_start_date,
      body: input.body,
      dimension_values: {},
      expected_draft_revision: input.expected_draft_revision,
    }
  }
  if (input.kind === "submit") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      expected_draft_revision: input.expected_draft_revision,
    }
  }
  if (input.kind === "correction" || input.kind === "revision") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
    }
  }
  if (input.kind === "review") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      decision: input.decision,
      reason: input.reason,
    }
  }
  if (input.kind === "reopen") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      grant_kind: input.grant_kind,
      reason: input.reason,
    }
  }
  throw new TypeError("无效的教学评语写入")
}

export function captureTeachingReportWrite(
  input: TeachingReportWriteInput,
): TeachingReportWrite {
  const write = Object.freeze({
    [teachingReportWriteBrand]: true as const,
  })
  teachingReportWritePayloads.set(write, snapshotWrite(input))
  return write
}

function consumeWrite(write: TeachingReportWrite): TeachingReportWriteInput {
  const payload = teachingReportWritePayloads.get(write)
  if (!payload) throw new Error(WRITE_EXPIRED_MESSAGE)
  teachingReportWritePayloads.delete(write)
  return payload
}

function bodyWithoutKind(payload: TeachingReportWriteInput): Record<string, unknown> {
  const body: Record<string, unknown> = { ...payload }
  delete body.kind
  return body
}

function mutationInit(
  method: "PUT" | "POST",
  payload: TeachingReportWriteInput,
  idempotencyKey: string,
): RequestInit {
  return {
    method,
    headers: {
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    body: JSON.stringify(bodyWithoutKind(payload)),
  }
}

export async function saveTeachingReportDraft(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
): Promise<TeachingReportDraftResponse> {
  const payload = consumeWrite(write)
  if (payload.kind !== "draft") throw new Error(WRITE_EXPIRED_MESSAGE)
  return fetchDecoded(
    `/evaluations/reports/${reportId}/draft`,
    draftResponseSchema,
    mutationInit("PUT", payload, idempotencyKey),
  )
}

export async function submitTeachingReport(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
): Promise<TeachingReportSubmitResponse> {
  const payload = consumeWrite(write)
  if (payload.kind !== "submit") throw new Error(WRITE_EXPIRED_MESSAGE)
  return fetchDecoded(
    `/evaluations/reports/${reportId}/submit`,
    submitResponseSchema,
    mutationInit("POST", payload, idempotencyKey),
  )
}

async function startDraft(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
  kind: "correction" | "revision",
): Promise<TeachingReportDraftStartResponse> {
  const payload = consumeWrite(write)
  if (payload.kind !== kind) throw new Error(WRITE_EXPIRED_MESSAGE)
  const operation = kind === "correction" ? "correction-drafts" : "revisions"
  return fetchDecoded(
    `/evaluations/reports/${reportId}/${operation}`,
    draftStartResponseSchema,
    mutationInit("POST", payload, idempotencyKey),
  )
}

export async function startTeachingReportCorrection(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
): Promise<TeachingReportDraftStartResponse> {
  return await startDraft(reportId, write, idempotencyKey, "correction")
}

export async function startTeachingReportRevision(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
): Promise<TeachingReportDraftStartResponse> {
  return await startDraft(reportId, write, idempotencyKey, "revision")
}

export async function reviewTeachingReport(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
): Promise<TeachingReportReviewResponse> {
  const payload = consumeWrite(write)
  if (payload.kind !== "review") throw new Error(WRITE_EXPIRED_MESSAGE)
  return fetchDecoded(
    `/evaluations/reports/${reportId}/reviews`,
    reviewResponseSchema,
    mutationInit("POST", payload, idempotencyKey),
  )
}

export async function reopenTeachingReport(
  reportId: string,
  write: TeachingReportWrite,
  idempotencyKey: string,
): Promise<TeachingReportReopenResponse> {
  const payload = consumeWrite(write)
  if (payload.kind !== "reopen") throw new Error(WRITE_EXPIRED_MESSAGE)
  return fetchDecoded(
    `/evaluations/reports/${reportId}/reopen`,
    reopenResponseSchema,
    mutationInit("POST", payload, idempotencyKey),
  )
}
