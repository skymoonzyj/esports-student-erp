import type {
  HousingReportBatchStatus,
  HousingReportListFilters,
} from "./reportingApi"

const STATUS_ORDER: readonly HousingReportBatchStatus[] = [
  "generated",
  "marked_submitted",
  "voided",
  "failed",
]

function orderedUnique(values: string[] | undefined): string[] | undefined {
  if (!values?.length) return undefined
  return [...new Set(values)].sort((left, right) => left.localeCompare(right))
}

function orderedStatuses(
  values: HousingReportBatchStatus[] | undefined,
): HousingReportBatchStatus[] | undefined {
  if (!values?.length) return undefined
  const selected = new Set(values)
  return STATUS_ORDER.filter((value) => selected.has(value))
}

export function normalizeHousingReportFilters(
  filters: HousingReportListFilters,
): HousingReportListFilters {
  const normalized: HousingReportListFilters = { ...filters }
  const statuses = orderedStatuses(filters.statuses)
  const buildingIds = orderedUnique(filters.buildingIds)
  const floorIds = orderedUnique(filters.floorIds)
  const cohortIds = orderedUnique(filters.cohortIds)
  if (statuses) normalized.statuses = statuses
  else delete normalized.statuses
  if (buildingIds) normalized.buildingIds = buildingIds
  else delete normalized.buildingIds
  if (floorIds) normalized.floorIds = floorIds
  else delete normalized.floorIds
  if (cohortIds) normalized.cohortIds = cohortIds
  else delete normalized.cohortIds
  return normalized
}

export const reportingKeys = {
  root: (accountId: string) => ["housing", accountId, "reports"] as const,
  scope: (accountId: string, accessRevision: string) =>
    [...reportingKeys.root(accountId), accessRevision] as const,
  lists: (accountId: string, accessRevision: string) =>
    [...reportingKeys.scope(accountId, accessRevision), "lists"] as const,
  list: (
    accountId: string,
    accessRevision: string,
    filters: HousingReportListFilters,
  ) => [
    ...reportingKeys.lists(accountId, accessRevision),
    normalizeHousingReportFilters(filters),
  ] as const,
  details: (accountId: string, accessRevision: string) =>
    [...reportingKeys.scope(accountId, accessRevision), "details"] as const,
  detail: (accountId: string, accessRevision: string, batchId: string) =>
    [...reportingKeys.details(accountId, accessRevision), batchId] as const,
}

export function housingReportInvalidationKeys(
  accountId: string,
  listAccessRevision: string,
  detailAccessRevision: string,
  batchIds: readonly string[] = [],
): ReadonlyArray<readonly unknown[]> {
  const detailKeys = [...new Set(batchIds)]
    .filter(Boolean)
    .sort((left, right) => left.localeCompare(right))
    .map((batchId) => reportingKeys.detail(accountId, detailAccessRevision, batchId))
  return [
    reportingKeys.lists(accountId, listAccessRevision),
    ...detailKeys,
  ]
}
