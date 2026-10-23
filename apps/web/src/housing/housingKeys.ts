import type { HousingFiltersValue } from "./api"

export const housingKeys = {
  root: (accountId: string) => ["housing", accountId] as const,
  catalog: (accountId: string) => [...housingKeys.root(accountId), "catalog"] as const,
  scope: (accountId: string, accessRevision: string) =>
    [...housingKeys.root(accountId), "scope", accessRevision] as const,
  overviews: (accountId: string, accessRevision: string) =>
    [...housingKeys.scope(accountId, accessRevision), "overviews"] as const,
  overview: (accountId: string, accessRevision: string, filters: HousingFiltersValue) =>
    [...housingKeys.overviews(accountId, accessRevision), filters] as const,
  details: (accountId: string, accessRevision: string) =>
    [...housingKeys.scope(accountId, accessRevision), "details"] as const,
  detail: (accountId: string, accessRevision: string, roomId: string, includeVoided = false) =>
    [...housingKeys.details(accountId, accessRevision), roomId, includeVoided] as const,
  cohortOptions: (accountId: string, accessRevision: string, roomId: string) =>
    [...housingKeys.scope(accountId, accessRevision), "cohort-options", roomId] as const,
  transferOptions: (accountId: string, accessRevision: string, stayId: string) =>
    [...housingKeys.scope(accountId, accessRevision), "transfer-options", stayId] as const,
}
