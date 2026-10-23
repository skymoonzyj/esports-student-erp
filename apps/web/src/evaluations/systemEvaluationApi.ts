import { z } from "zod"

import { apiFetch } from "../api/client"
import {
  normalizeSystemEvaluationListFilters,
  type SystemEvaluationAdminQueue,
  type SystemEvaluationListFilters,
  type SystemEvaluationProjection,
  type SystemEvaluationTaskStatus,
} from "./systemEvaluationKeys"

const INVALID_RESPONSE_MESSAGE = "系统评价响应格式无效，请刷新重试。"
const WRITE_EXPIRED_MESSAGE = "系统评价写入已失效，请重新填写。"

function invalidResponse(): Error {
  return new Error(INVALID_RESPONSE_MESSAGE)
}

const UUID = z.uuid()
const DATE_TIME = z.iso.datetime({ offset: true })
const FINITE_NUMBER = z.number().finite()
const NON_NEGATIVE_INTEGER = FINITE_NUMBER.int().min(0)
const POSITIVE_INTEGER = FINITE_NUMBER.int().min(1)
const SCORE = FINITE_NUMBER.min(0).max(100)
const CONFIDENCE = FINITE_NUMBER.min(0).max(1)

const PRIVATE_ANALYSIS_KEY_PARTS = [
  "account",
  "credential",
  "game_id",
  "identity",
  "password",
  "phone",
  "secret",
  "ticket",
  "token",
] as const
const PRIVATE_RESULT_KEYS = new Set([
  "id_card",
  "id_number",
  "mobile",
  "nickname",
  "player_id",
  "player_name",
])
const PUBLIC_KEY_PATTERN = /^[a-z][a-z0-9_]{0,79}$/

function isPublicAnalysisKey(value: string): boolean {
  return (
    PUBLIC_KEY_PATTERN.test(value) &&
    !PRIVATE_ANALYSIS_KEY_PARTS.some((part) => value.includes(part))
  )
}

function isPublicResultKey(value: string): boolean {
  return isPublicAnalysisKey(value) && !PRIVATE_RESULT_KEYS.has(value.toLowerCase())
}

const publicAnalysisKey = z.string().refine(isPublicAnalysisKey)
const publicResultKey = z.string().refine(isPublicResultKey)
const jsonScalar = z.union([z.string(), z.boolean(), FINITE_NUMBER, z.null()])
const publicFields = z.record(publicResultKey, jsonScalar)
const publicConfidences = z.record(publicAnalysisKey, CONFIDENCE)

const warningSchema = z.object({
  key: publicAnalysisKey,
  severity: z.enum(["info", "warning", "blocking"]),
  summary: z.string().min(1).max(500),
})

const componentSchema = z.object({
  key: publicAnalysisKey,
  score: SCORE,
  weight: CONFIDENCE,
  summary: z.string().min(1).max(500),
})

const resultSchema = z.object({
  id: UUID,
  version: POSITIVE_INTEGER,
  source: z.enum(["automatic", "manual", "correction", "revision"]),
  fields: publicFields,
  confidences: publicConfidences,
  warnings: z.array(warningSchema).max(100),
  components: z.array(componentSchema).max(50),
  missing_fields: z.array(publicResultKey).max(200),
  score: SCORE,
  grade: z.string().min(1).max(24),
  advice: z.string().min(1).max(4000),
  adapter_id: z.string().min(1).max(80).nullable(),
  adapter_version: z.string().min(1).max(40).nullable(),
  scoring_rule_version: z.string().min(1).max(40).nullable(),
  result_schema_version: z.string().min(1).max(40),
  review_decision: z.enum(["pending", "approved", "rejected"]),
  reviewed_at: DATE_TIME.nullable(),
  rejection_reason: z.string().max(1000).nullable(),
  published_at: DATE_TIME.nullable(),
  previous_version_id: UUID.nullable(),
})

const allowedActionsSchema = z.object({
  match: z.boolean(),
  save_manual_draft: z.boolean(),
  submit_review: z.boolean(),
  create_correction: z.boolean(),
  create_revision: z.boolean(),
  review: z.boolean(),
  archive: z.boolean(),
  reopen: z.boolean(),
  view_preview: z.boolean(),
})

const taskSchema = z.object({
  id: UUID,
  public_number: z.string().min(1),
  status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  lock_version: POSITIVE_INTEGER,
  created_at: DATE_TIME,
  student_person_id: UUID.nullable(),
  enrollment_id: UUID.nullable(),
  cohort_id: UUID.nullable(),
  project_id: UUID,
  class_id: UUID.nullable(),
  identity_summary: z.string().nullable(),
  cohort_name: z.string().nullable(),
  project_name: z.string(),
  class_name: z.string().nullable(),
  media_count: NON_NEGATIVE_INTEGER,
  current_result: resultSchema.nullable(),
  published_result: resultSchema.nullable(),
  result_versions: z.array(resultSchema),
  allowed_actions: allowedActionsSchema,
  allowed_reopen_modes: z.array(z.enum(["automatic", "correction"])).max(2),
})

const taskPageSchema = z.object({
  api_version: z.literal("v2"),
  items: z.array(taskSchema),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: FINITE_NUMBER.int().min(1).max(100),
})

const taskFilterOptionSchema = z.object({
  id: UUID,
  label: z.string().min(1).max(200),
})

const taskFilterOptionsSchema = z.object({
  api_version: z.literal("v2"),
  cohorts: z.array(taskFilterOptionSchema),
  projects: z.array(taskFilterOptionSchema),
  classes: z.array(taskFilterOptionSchema),
  students: z.array(taskFilterOptionSchema),
})

const taskDetailSchema = z.object({
  api_version: z.literal("v2"),
  task: taskSchema,
})

const dashboardSchema = z.object({
  api_version: z.literal("v2"),
  upload_available: z.boolean(),
  processing: NON_NEGATIVE_INTEGER,
  pending_match: NON_NEGATIVE_INTEGER,
  manual_input: NON_NEGATIVE_INTEGER,
  needs_review: NON_NEGATIVE_INTEGER,
  rejected: NON_NEGATIVE_INTEGER,
  published: NON_NEGATIVE_INTEGER,
})

const workspaceMediaSlotSchema = z.object({
  key: publicAnalysisKey,
  label: z.string().min(1),
  position: FINITE_NUMBER.int().min(1).max(3),
  required: z.boolean(),
})

const workspaceProfileSchema = z.object({
  mode: z.enum(["manual_only", "automatic"]),
  adapter_id: z.string().min(1).max(80).nullable(),
  adapter_version: z.string().min(1).max(40).nullable(),
  scoring_rule_version: z.string().min(1).max(40).nullable(),
  result_schema_version: z.string().min(1).max(40),
  required_fields: z.array(publicResultKey).max(50),
  media_slots: z.array(workspaceMediaSlotSchema).max(3),
})

const workspaceMediaSchema = z.object({
  id: UUID,
  slot_key: publicAnalysisKey,
  position: FINITE_NUMBER.int().min(1).max(3),
  mime_type: z.enum(["image/jpeg", "image/png", "image/webp"]),
  size_bytes: POSITIVE_INTEGER,
  width_pixels: POSITIVE_INTEGER,
  height_pixels: POSITIVE_INTEGER,
})

const workspaceDraftSchema = z.object({
  draft_revision: POSITIVE_INTEGER,
  fields: publicFields,
  confidences: publicConfidences,
  warnings: z.array(warningSchema).max(100),
  components: z.array(componentSchema).max(50),
  missing_fields: z.array(publicResultKey).max(200),
  score: SCORE.nullable(),
  grade: z.string().min(1).max(24).nullable(),
  advice: z.string().min(1).max(4000).nullable(),
  updated_at: DATE_TIME,
})

const workspaceSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  task_version: POSITIVE_INTEGER,
  profile: workspaceProfileSchema,
  media: z.array(workspaceMediaSchema),
  draft: workspaceDraftSchema.nullable(),
})

const draftSavedSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  task_status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  task_version: POSITIVE_INTEGER,
  draft_revision: POSITIVE_INTEGER,
  is_complete: z.boolean(),
  missing_required_fields: z.array(publicResultKey).max(200),
  updated_at: DATE_TIME,
})

const reviewedSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  result_id: UUID,
  result_version: POSITIVE_INTEGER,
  task_status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  task_version: POSITIVE_INTEGER,
  review_decision: z.enum(["approved", "rejected"]),
  reviewed_at: DATE_TIME,
  published_at: DATE_TIME.nullable(),
})

const matchTargetSchema = z.object({
  student_person_id: UUID,
  student_name: z.string(),
  student_number: z.string(),
  enrollment_id: UUID,
  cohort_id: UUID,
  cohort_name: z.string(),
  class_id: UUID,
  class_name: z.string(),
})

const matchTargetPageSchema = z.object({
  api_version: z.literal("v2"),
  items: z.array(matchTargetSchema),
  total: NON_NEGATIVE_INTEGER,
  page: POSITIVE_INTEGER,
  page_size: FINITE_NUMBER.int().min(1).max(100),
})

const matchedSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  student_person_id: UUID,
  enrollment_id: UUID,
  cohort_id: UUID,
  project_id: UUID,
  class_id: UUID,
  task_status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  task_version: POSITIVE_INTEGER,
})

const submittedSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  result_id: UUID,
  result_version: POSITIVE_INTEGER,
  task_status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  task_version: POSITIVE_INTEGER,
})

const revisionStartedSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  task_status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  task_version: POSITIVE_INTEGER,
  draft_revision: POSITIVE_INTEGER.nullable(),
})

const taskStateChangedSchema = z.object({
  api_version: z.literal("v2"),
  task_id: UUID,
  task_status: z.enum([
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "archived",
    "published",
    "revision_draft",
  ]),
  task_version: POSITIVE_INTEGER,
})

const adapterCertificationSchema = z.object({
  id: UUID,
  project_id: UUID,
  adapter_id: publicAnalysisKey,
  adapter_version: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/),
  scoring_rule_version: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/),
  result_schema_version: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$/),
  manifest_digest: z.string().regex(/^[0-9a-f]{64}$/),
  certified_by_user_id: UUID,
  certified_at: DATE_TIME,
})

const adapterCertificationListSchema = z.object({
  api_version: z.literal("v2"),
  items: z.array(adapterCertificationSchema),
})

const adapterCertificationResponseSchema = z.object({
  api_version: z.literal("v2"),
  certification: adapterCertificationSchema,
})

export type SystemEvaluationResult = z.infer<typeof resultSchema>
export type SystemEvaluationAllowedActions = z.infer<typeof allowedActionsSchema>
export type SystemEvaluationTask = z.infer<typeof taskSchema>
export type SystemEvaluationTaskPage = z.infer<typeof taskPageSchema>
export type SystemEvaluationTaskFilterOption = z.infer<
  typeof taskFilterOptionSchema
>
export type SystemEvaluationTaskFilterOptions = z.infer<
  typeof taskFilterOptionsSchema
>
export type SystemEvaluationTaskDetail = z.infer<typeof taskDetailSchema>
export type SystemEvaluationDashboard = z.infer<typeof dashboardSchema>
export type SystemEvaluationWorkspaceMediaSlot = z.infer<typeof workspaceMediaSlotSchema>
export type SystemEvaluationWorkspaceProfile = z.infer<typeof workspaceProfileSchema>
export type SystemEvaluationWorkspaceMedia = z.infer<typeof workspaceMediaSchema>
export type SystemEvaluationWorkspaceDraft = z.infer<typeof workspaceDraftSchema>
export type SystemEvaluationTaskWorkspace = z.infer<typeof workspaceSchema>
export type SystemEvaluationDraftSaved = z.infer<typeof draftSavedSchema>
export type SystemEvaluationReviewed = z.infer<typeof reviewedSchema>
export type SystemEvaluationMatchTarget = z.infer<typeof matchTargetSchema>
export type SystemEvaluationMatchTargetPage = z.infer<typeof matchTargetPageSchema>
export type SystemEvaluationMatched = z.infer<typeof matchedSchema>
export type SystemEvaluationSubmitted = z.infer<typeof submittedSchema>
export type SystemEvaluationRevisionStarted = z.infer<typeof revisionStartedSchema>
export type SystemEvaluationTaskStateChanged = z.infer<typeof taskStateChangedSchema>
export type SystemEvaluationAdapterCertification = z.infer<
  typeof adapterCertificationSchema
>
export type SystemEvaluationAdapterCertificationList = z.infer<
  typeof adapterCertificationListSchema
>
export type SystemEvaluationAdapterCertificationResponse = z.infer<
  typeof adapterCertificationResponseSchema
>

export type SystemEvaluationTaskListInput = SystemEvaluationListFilters
export type {
  SystemEvaluationAdminQueue,
  SystemEvaluationListFilters,
  SystemEvaluationProjection,
  SystemEvaluationTaskStatus,
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

function listPath(projection: SystemEvaluationProjection): string {
  return projection === "admin"
    ? "/evaluations/admin/system-tasks"
    : "/evaluations/system/tasks"
}

function queryForList(
  projection: SystemEvaluationProjection,
  input: SystemEvaluationListFilters,
): string {
  const filters = normalizeSystemEvaluationListFilters(projection, input)
  const query = new URLSearchParams()
  if (projection === "account" && typeof filters.status === "string") {
    query.set("status", filters.status)
  }
  if (typeof filters.queue === "string") {
    query.set("queue", filters.queue)
  }
  for (const [key, queryName] of [
    ["cohortId", "cohort_id"],
    ["projectId", "project_id"],
    ["classId", "class_id"],
    ["studentId", "student_id"],
  ] as const) {
    const value = filters[key]
    if (typeof value === "string") query.set(queryName, value)
  }
  if (projection === "account" && filters.publishedOnly === true) {
    query.set("published_only", "true")
  }
  query.set("page", String(filters.page))
  query.set("page_size", String(filters.pageSize))
  return query.toString()
}

export function listSystemEvaluationTasks(
  projection: SystemEvaluationProjection,
  input: SystemEvaluationTaskListInput = {},
): Promise<SystemEvaluationTaskPage> {
  const query = queryForList(projection, input)
  return fetchDecoded(`${listPath(projection)}?${query}`, taskPageSchema)
}

export function getSystemEvaluationTaskFilterOptions(
  projection: SystemEvaluationProjection,
): Promise<SystemEvaluationTaskFilterOptions> {
  const path =
    projection === "admin"
      ? "/evaluations/admin/system-task-filter-options"
      : "/evaluations/system/task-filter-options"
  return fetchDecoded(path, taskFilterOptionsSchema)
}

export function getSystemEvaluationTask(
  projection: SystemEvaluationProjection,
  taskId: string,
): Promise<SystemEvaluationTaskDetail> {
  const path =
    projection === "admin"
      ? `/evaluations/admin/system-tasks/${taskId}`
      : `/evaluations/system/tasks/${taskId}`
  return fetchDecoded(path, taskDetailSchema)
}

export function getSystemEvaluationDashboard(): Promise<SystemEvaluationDashboard> {
  return fetchDecoded("/evaluations/system/dashboard", dashboardSchema)
}

export function getSystemEvaluationTaskWorkspace(
  projection: SystemEvaluationProjection,
  taskId: string,
): Promise<SystemEvaluationTaskWorkspace> {
  const path =
    projection === "admin"
      ? `/evaluations/admin/system-tasks/${taskId}/workspace`
      : `/evaluations/system/tasks/${taskId}/workspace`
  return fetchDecoded(path, workspaceSchema)
}

export type SystemEvaluationMatchTargetListInput = {
  query?: string
  page?: number
  pageSize?: number
}

export function listSystemEvaluationMatchTargets(
  taskId: string,
  input: SystemEvaluationMatchTargetListInput = {},
): Promise<SystemEvaluationMatchTargetPage> {
  const query = new URLSearchParams()
  query.set("query", input.query?.trim() ?? "")
  query.set("page", String(input.page ?? 1))
  query.set("page_size", String(input.pageSize ?? 20))
  return fetchDecoded(
    `/evaluations/admin/system-tasks/${taskId}/match-targets?${query.toString()}`,
    matchTargetPageSchema,
  )
}

export function systemEvaluationPreviewUrl(taskId: string, mediaId: string): string {
  return `/api/v1/evaluations/system/tasks/${taskId}/media/${mediaId}/preview`
}

export type SystemEvaluationManualDraftWriteInput = {
  kind: "manual_draft"
  expected_lock_version: number
  draft_revision: number | null
  fields: Array<{ key: string; value: string | boolean | number | null }>
  confidences: Array<{ key: string; confidence: number }>
  missing_fields: string[]
  warnings: Array<{ key: string; severity: "info" | "warning" | "blocking"; summary: string }>
  components: Array<{ key: string; score: number; weight: number; summary: string }>
  score: number | null
  grade: string | null
  advice: string | null
}

export type SystemEvaluationReviewWriteInput = {
  kind: "review"
  expected_lock_version: number
  decision: "approved" | "rejected"
  rejection_reason?: string | null
}

export type SystemEvaluationMatchWriteInput = {
  kind: "match"
  expected_lock_version: number
  student_person_id: string
  enrollment_id: string
  cohort_id: string
  class_id: string
}

export type SystemEvaluationSubmitWriteInput = {
  kind: "submit_review"
  expected_lock_version: number
  draft_revision: number
}

export type SystemEvaluationCorrectionWriteInput = {
  kind: "correction"
  expected_lock_version: number
  mode: "manual" | "automatic"
}

export type SystemEvaluationRevisionWriteInput = {
  kind: "revision"
  expected_lock_version: number
  mode: "manual" | "automatic"
}

export type SystemEvaluationArchiveWriteInput = {
  kind: "archive"
  expected_lock_version: number
  reason: string
}

export type SystemEvaluationReopenWriteInput = {
  kind: "reopen"
  expected_lock_version: number
  resume: "automatic" | "correction"
  reason: string
}

export type SystemEvaluationAdapterCertificationWriteInput = {
  kind: "adapter_certification"
  project_id: string
  adapter_id: string
  adapter_version: string
  scoring_rule_version: string
  result_schema_version: string
  manifest_digest: string
}

export type SystemEvaluationWriteInput =
  | SystemEvaluationManualDraftWriteInput
  | SystemEvaluationReviewWriteInput
  | SystemEvaluationMatchWriteInput
  | SystemEvaluationSubmitWriteInput
  | SystemEvaluationCorrectionWriteInput
  | SystemEvaluationRevisionWriteInput
  | SystemEvaluationArchiveWriteInput
  | SystemEvaluationReopenWriteInput
  | SystemEvaluationAdapterCertificationWriteInput

const systemEvaluationWriteBrand = Symbol("system-evaluation-write-only")
export type SystemEvaluationWrite = {
  readonly [systemEvaluationWriteBrand]: true
}

type StoredSystemEvaluationWrite =
  | SystemEvaluationManualDraftWriteInput
  | SystemEvaluationReviewWriteInput
  | SystemEvaluationMatchWriteInput
  | SystemEvaluationSubmitWriteInput
  | SystemEvaluationCorrectionWriteInput
  | SystemEvaluationRevisionWriteInput
  | SystemEvaluationArchiveWriteInput
  | SystemEvaluationReopenWriteInput
  | SystemEvaluationAdapterCertificationWriteInput

const systemEvaluationWritePayloads = new WeakMap<
  SystemEvaluationWrite,
  StoredSystemEvaluationWrite
>()

function isWriteInput(value: unknown): value is SystemEvaluationWriteInput {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false
  const kind = (value as { kind?: unknown }).kind
  return (
    kind === "manual_draft" ||
    kind === "review" ||
    kind === "match" ||
    kind === "submit_review" ||
    kind === "correction" ||
    kind === "revision" ||
    kind === "archive" ||
    kind === "reopen" ||
    kind === "adapter_certification"
  )
}

function snapshotSystemEvaluationWrite(
  input: SystemEvaluationWriteInput,
): StoredSystemEvaluationWrite {
  if (input.kind === "adapter_certification") {
    return {
      kind: input.kind,
      project_id: input.project_id,
      adapter_id: input.adapter_id,
      adapter_version: input.adapter_version,
      scoring_rule_version: input.scoring_rule_version,
      result_schema_version: input.result_schema_version,
      manifest_digest: input.manifest_digest,
    }
  }
  if (input.kind === "review") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      decision: input.decision,
      rejection_reason: input.rejection_reason,
    }
  }
  if (input.kind === "match") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      student_person_id: input.student_person_id,
      enrollment_id: input.enrollment_id,
      cohort_id: input.cohort_id,
      class_id: input.class_id,
    }
  }
  if (input.kind === "submit_review") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      draft_revision: input.draft_revision,
    }
  }
  if (input.kind === "correction" || input.kind === "revision") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      mode: input.mode,
    }
  }
  if (input.kind === "archive") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      reason: input.reason,
    }
  }
  if (input.kind === "reopen") {
    return {
      kind: input.kind,
      expected_lock_version: input.expected_lock_version,
      resume: input.resume,
      reason: input.reason,
    }
  }
  return {
    kind: input.kind,
    expected_lock_version: input.expected_lock_version,
    draft_revision: input.draft_revision,
    fields: input.fields.map(({ key, value }) => ({ key, value })),
    confidences: input.confidences.map(({ key, confidence }) => ({ key, confidence })),
    missing_fields: [...input.missing_fields],
    warnings: input.warnings.map(({ key, severity, summary }) => ({
      key,
      severity,
      summary,
    })),
    components: input.components.map(({ key, score, weight, summary }) => ({
      key,
      score,
      weight,
      summary,
    })),
    score: input.score,
    grade: input.grade,
    advice: input.advice,
  }
}

export function captureSystemEvaluationWrite(
  input: SystemEvaluationWriteInput,
): SystemEvaluationWrite {
  if (!isWriteInput(input)) {
    throw new TypeError("无效的系统评价写入")
  }
  const write = Object.freeze({
    [systemEvaluationWriteBrand]: true as const,
  })
  systemEvaluationWritePayloads.set(write, snapshotSystemEvaluationWrite(input))
  return write
}

function consumeSystemEvaluationWrite(
  write: SystemEvaluationWrite,
): StoredSystemEvaluationWrite {
  const payload = systemEvaluationWritePayloads.get(write)
  if (!payload) throw new Error(WRITE_EXPIRED_MESSAGE)
  systemEvaluationWritePayloads.delete(write)
  return payload
}

function requestBodyWithoutKind(
  payload: StoredSystemEvaluationWrite,
): Record<string, unknown> {
  const body: Record<string, unknown> = { ...payload }
  delete body.kind
  return body
}

function jsonMutationInit(method: "PUT" | "POST", body: unknown, extra?: HeadersInit): RequestInit {
  return {
    method,
    headers: {
      "Content-Type": "application/json",
      ...extra,
    },
    body: JSON.stringify(body),
  }
}

export async function saveSystemEvaluationManualDraft(
  projection: SystemEvaluationProjection,
  taskId: string,
  write: SystemEvaluationWrite,
): Promise<SystemEvaluationDraftSaved> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== "manual_draft") {
    throw new Error(WRITE_EXPIRED_MESSAGE)
  }
  const path =
    projection === "admin"
      ? `/evaluations/admin/system-tasks/${taskId}/manual-draft`
      : `/evaluations/system/tasks/${taskId}/manual-draft`
  return fetchDecoded(
    path,
    draftSavedSchema,
    jsonMutationInit("PUT", requestBodyWithoutKind(payload)),
  )
}

export async function reviewSystemEvaluationTask(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationReviewed> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== "review") {
    throw new Error(WRITE_EXPIRED_MESSAGE)
  }
  return fetchDecoded(
    `/evaluations/admin/system-tasks/${taskId}/reviews`,
    reviewedSchema,
    jsonMutationInit("POST", requestBodyWithoutKind(payload), {
      "Idempotency-Key": idempotencyKey,
    }),
  )
}

export async function matchSystemEvaluationTask(
  projection: SystemEvaluationProjection,
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationMatched> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== "match") throw new Error(WRITE_EXPIRED_MESSAGE)
  const path = projection === "admin"
    ? `/evaluations/admin/system-tasks/${taskId}/match`
    : `/evaluations/system/tasks/${taskId}/match`
  return fetchDecoded(
    path,
    matchedSchema,
    jsonMutationInit("POST", requestBodyWithoutKind(payload), {
      "Idempotency-Key": idempotencyKey,
    }),
  )
}

export async function submitSystemEvaluationManualDraft(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationSubmitted> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== "submit_review") throw new Error(WRITE_EXPIRED_MESSAGE)
  return fetchDecoded(
    `/evaluations/system/tasks/${taskId}/submit-review`,
    submittedSchema,
    jsonMutationInit("POST", requestBodyWithoutKind(payload), {
      "Idempotency-Key": idempotencyKey,
    }),
  )
}

async function startSystemEvaluationResultVersion(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
  kind: "correction" | "revision",
): Promise<SystemEvaluationRevisionStarted> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== kind) throw new Error(WRITE_EXPIRED_MESSAGE)
  const operation = kind === "correction" ? "corrections" : "revisions"
  return fetchDecoded(
    `/evaluations/system/tasks/${taskId}/${operation}`,
    revisionStartedSchema,
    jsonMutationInit("POST", requestBodyWithoutKind(payload), {
      "Idempotency-Key": idempotencyKey,
    }),
  )
}

export function startSystemEvaluationCorrection(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationRevisionStarted> {
  return startSystemEvaluationResultVersion(taskId, write, idempotencyKey, "correction")
}

export function startSystemEvaluationRevision(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationRevisionStarted> {
  return startSystemEvaluationResultVersion(taskId, write, idempotencyKey, "revision")
}

async function changeSystemEvaluationTaskState(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
  kind: "archive" | "reopen",
): Promise<SystemEvaluationTaskStateChanged> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== kind) throw new Error(WRITE_EXPIRED_MESSAGE)
  return fetchDecoded(
    `/evaluations/admin/system-tasks/${taskId}/${kind}`,
    taskStateChangedSchema,
    jsonMutationInit("POST", requestBodyWithoutKind(payload), {
      "Idempotency-Key": idempotencyKey,
    }),
  )
}

export function archiveSystemEvaluationTask(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationTaskStateChanged> {
  return changeSystemEvaluationTaskState(taskId, write, idempotencyKey, "archive")
}

export function reopenSystemEvaluationTask(
  taskId: string,
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationTaskStateChanged> {
  return changeSystemEvaluationTaskState(taskId, write, idempotencyKey, "reopen")
}

export function listSystemEvaluationAdapterCertifications(): Promise<
  SystemEvaluationAdapterCertificationList
> {
  return fetchDecoded(
    "/evaluations/admin/adapter-certifications",
    adapterCertificationListSchema,
  )
}

export async function createSystemEvaluationAdapterCertification(
  write: SystemEvaluationWrite,
  idempotencyKey: string,
): Promise<SystemEvaluationAdapterCertificationResponse> {
  const payload = consumeSystemEvaluationWrite(write)
  if (payload.kind !== "adapter_certification") {
    throw new Error(WRITE_EXPIRED_MESSAGE)
  }
  return fetchDecoded(
    "/evaluations/admin/adapter-certifications",
    adapterCertificationResponseSchema,
    jsonMutationInit("POST", requestBodyWithoutKind(payload), {
      "Idempotency-Key": idempotencyKey,
    }),
  )
}
