export type TeachingReportKind = "daily" | "weekly"

export type TeachingReportStatus =
  | "draft"
  | "pending_review"
  | "rejected"
  | "correction_draft"
  | "published"
  | "revision_pending_review"

export type AuthorReportFilters = {
  enrollmentId: string
  kind?: TeachingReportKind | null
  status?: TeachingReportStatus | null
  page?: number
  pageSize?: number
}

export type ReviewQueueFilters = {
  cohortId?: string | null
  projectId?: string | null
  classId?: string | null
  page?: number
  pageSize?: number
}

export type PublishedReportFilters = {
  studentId: string
  enrollmentId?: string | null
  page?: number
  pageSize?: number
}

export type ReportTargetFilters = {
  cohortId?: string | null
  projectId?: string | null
  classId?: string | null
  studentId?: string | null
  query?: string | null
  page?: number
  pageSize?: number
}

export type ReportVersionFilters = {
  reportId: string
  page?: number
  pageSize?: number
}

export type NormalizedAuthorReportFilters = {
  enrollmentId: string
  kind: TeachingReportKind | null
  status: TeachingReportStatus | null
  page: number
  pageSize: number
}

export type NormalizedReviewQueueFilters = {
  cohortId: string | null
  projectId: string | null
  classId: string | null
  page: number
  pageSize: number
}

export type NormalizedPublishedReportFilters = {
  studentId: string
  enrollmentId: string | null
  page: number
  pageSize: number
}

export type NormalizedReportTargetFilters = {
  cohortId: string | null
  projectId: string | null
  classId: string | null
  studentId: string | null
  query: string | null
  page: number
  pageSize: number
}

export type NormalizedReportVersionFilters = {
  reportId: string
  page: number
  pageSize: number
}

export function normalizeAuthorReportFilters(
  input: AuthorReportFilters,
): NormalizedAuthorReportFilters {
  return {
    enrollmentId: input.enrollmentId,
    kind: input.kind ?? null,
    status: input.status ?? null,
    page: input.page ?? 1,
    pageSize: input.pageSize ?? 20,
  }
}

export function normalizeReviewQueueFilters(
  input: ReviewQueueFilters = {},
): NormalizedReviewQueueFilters {
  return {
    cohortId: input.cohortId ?? null,
    projectId: input.projectId ?? null,
    classId: input.classId ?? null,
    page: input.page ?? 1,
    pageSize: input.pageSize ?? 20,
  }
}

export function normalizePublishedReportFilters(
  input: PublishedReportFilters,
): NormalizedPublishedReportFilters {
  return {
    studentId: input.studentId,
    enrollmentId: input.enrollmentId ?? null,
    page: input.page ?? 1,
    pageSize: input.pageSize ?? 20,
  }
}

export function normalizeReportTargetFilters(
  input: ReportTargetFilters = {},
): NormalizedReportTargetFilters {
  const query = input.query?.trim() ?? ""
  return {
    cohortId: input.cohortId ?? null,
    projectId: input.projectId ?? null,
    classId: input.classId ?? null,
    studentId: input.studentId ?? null,
    query: query || null,
    page: input.page ?? 1,
    pageSize: input.pageSize ?? 20,
  }
}

export function normalizeReportVersionFilters(
  input: ReportVersionFilters,
): NormalizedReportVersionFilters {
  return {
    reportId: input.reportId,
    page: input.page ?? 1,
    pageSize: input.pageSize ?? 20,
  }
}

export const reportKeys = {
  root: (accountId: string) => ["teaching-reports", accountId] as const,
  context: (accountId: string) =>
    [...reportKeys.root(accountId), "context"] as const,
  targets: (accountId: string, input: ReportTargetFilters = {}) =>
    [
      ...reportKeys.root(accountId),
      "targets",
      normalizeReportTargetFilters(input),
    ] as const,
  slots: (accountId: string, enrollmentId: string) =>
    [...reportKeys.root(accountId), "slots", enrollmentId] as const,
  list: (accountId: string, input: AuthorReportFilters) =>
    [
      ...reportKeys.root(accountId),
      "list",
      normalizeAuthorReportFilters(input),
    ] as const,
  detail: (accountId: string, reportId: string) =>
    [...reportKeys.root(accountId), "detail", reportId] as const,
  versions: (accountId: string, input: ReportVersionFilters) =>
    [
      ...reportKeys.root(accountId),
      "versions",
      normalizeReportVersionFilters(input),
    ] as const,
  reviewQueue: (accountId: string, input: ReviewQueueFilters = {}) =>
    [
      ...reportKeys.root(accountId),
      "review-queue",
      normalizeReviewQueueFilters(input),
    ] as const,
  published: (accountId: string, input: PublishedReportFilters) =>
    [
      ...reportKeys.root(accountId),
      "published",
      normalizePublishedReportFilters(input),
    ] as const,
}
