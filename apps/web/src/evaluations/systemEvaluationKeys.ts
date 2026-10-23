export type SystemEvaluationProjection = "account" | "admin"

export type SystemEvaluationTaskStatus =
  | "uploaded"
  | "identifying"
  | "pending_match"
  | "queued"
  | "processing"
  | "failed"
  | "manual_input"
  | "needs_review"
  | "rejected"
  | "correction_pending"
  | "archived"
  | "published"
  | "revision_draft"

export type SystemEvaluationAdminQueue =
  | "processing"
  | "pending_match"
  | "manual_input"
  | "needs_review"
  | "rejected"
  | "published"
  | "failed"
  | "archived"

export type SystemEvaluationListFilters = {
  status?: SystemEvaluationTaskStatus | string
  queue?: SystemEvaluationAdminQueue | string
  cohortId?: string
  projectId?: string
  classId?: string
  studentId?: string
  publishedOnly?: boolean
  page?: number
  pageSize?: number
}

const taskStatuses = new Set<SystemEvaluationTaskStatus>([
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
])

const adminQueues = new Set<SystemEvaluationAdminQueue>([
  "processing",
  "pending_match",
  "manual_input",
  "needs_review",
  "rejected",
  "published",
  "failed",
  "archived",
])

function trimmed(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined
  const result = value.trim()
  return result.length > 0 ? result : undefined
}

function normalizedPage(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback
  return Math.max(1, Math.floor(value))
}

function normalizedPageSize(value: unknown, fallback: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback
  return Math.min(100, Math.max(1, Math.floor(value)))
}

/**
 * Returns only the public, actor-relevant list values.  The explicit object
 * construction is intentional: free-text additions to a filter object (for
 * example a reason, advice, or idempotency key) can never become cache keys.
 */
export function normalizeSystemEvaluationListFilters(
  projection: SystemEvaluationProjection,
  filters: SystemEvaluationListFilters = {},
): Record<string, string | boolean | number> {
  const normalized: Record<string, string | boolean | number> = {}
  if (projection === "account") {
    const status = trimmed(filters.status)
    if (status && taskStatuses.has(status as SystemEvaluationTaskStatus)) {
      normalized.status = status
    }
  }
  const queue = trimmed(filters.queue)
  if (queue && adminQueues.has(queue as SystemEvaluationAdminQueue)) {
    normalized.queue = queue
  }

  const cohortId = trimmed(filters.cohortId)
  if (cohortId) normalized.cohortId = cohortId
  const projectId = trimmed(filters.projectId)
  if (projectId) normalized.projectId = projectId
  const classId = trimmed(filters.classId)
  if (classId) normalized.classId = classId
  const studentId = trimmed(filters.studentId)
  if (studentId) normalized.studentId = studentId
  if (projection === "account" && filters.publishedOnly === true) {
    normalized.publishedOnly = true
  }
  normalized.page = normalizedPage(filters.page, 1)
  normalized.pageSize = normalizedPageSize(filters.pageSize, 20)
  return normalized
}

const systemEvaluationRoot = (accountId: string) =>
  ["evaluations", accountId, "system", "v2"] as const

export const systemEvaluationKeys = {
  root: (accountId: string) => systemEvaluationRoot(accountId),
  dashboard: (accountId: string) =>
    [...systemEvaluationRoot(accountId), "dashboard"] as const,
  projection: (accountId: string, projection: SystemEvaluationProjection) =>
    [...systemEvaluationRoot(accountId), projection] as const,
  filterOptions: (
    accountId: string,
    projection: SystemEvaluationProjection,
  ) =>
    [
      ...systemEvaluationKeys.projection(accountId, projection),
      "filter-options",
    ] as const,
  list: (
    accountId: string,
    projection: SystemEvaluationProjection,
    filters: SystemEvaluationListFilters = {},
  ) =>
    [
      ...systemEvaluationKeys.projection(accountId, projection),
      "lists",
      normalizeSystemEvaluationListFilters(projection, filters),
    ] as const,
  detail: (
    accountId: string,
    projection: SystemEvaluationProjection,
    taskId: string,
  ) =>
    [...systemEvaluationKeys.projection(accountId, projection), "details", taskId] as const,
  workspace: (
    accountId: string,
    projection: SystemEvaluationProjection,
    taskId: string,
  ) =>
    [
      ...systemEvaluationKeys.projection(accountId, projection),
      "workspaces",
      taskId,
    ] as const,
  published: (accountId: string, studentId: string, page: number) =>
    [...systemEvaluationRoot(accountId), "published", studentId, page] as const,
  certifications: (accountId: string) =>
    [...systemEvaluationRoot(accountId), "certifications"] as const,
}
