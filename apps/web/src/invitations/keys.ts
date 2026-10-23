import type { InvitedHousingFilters } from "./api"
export const invitationKeys = {
  root: (accountId: string) => ["portal", accountId, "invitations"] as const,
  sources: (accountId: string) => [...invitationKeys.root(accountId), "sources"] as const,
  housingRoot: (accountId: string) => [...invitationKeys.root(accountId), "housing"] as const,
  housing: (accountId: string, filters: InvitedHousingFilters, epoch: number) => [...invitationKeys.housingRoot(accountId), filters.sourceKind,
    filters.organizationId ?? null, filters.status ?? null, filters.page, filters.pageSize, epoch] as const,
}
