import type { QueryClient } from "@tanstack/react-query"

type PermissionSource = {
  permissions?: readonly string[] | null
  scoped_permissions?: readonly string[] | null
}

function hasPermission(user: PermissionSource, permission: string): boolean {
  return Boolean(
    user.permissions?.includes(permission) ||
      user.scoped_permissions?.includes(permission),
  )
}

export function canReadSurveyIdentity(user: PermissionSource | null | undefined): boolean {
  if (!user) return false
  return hasPermission(user, "camp_surveys.identity.read")
}

export function canPrintSurvey(user: PermissionSource | null | undefined): boolean {
  if (!user) return false
  return hasPermission(user, "camp_surveys.print")
}

export function canPrintSurveyIdentity(user: PermissionSource | null | undefined): boolean {
  return canPrintSurvey(user) && canReadSurveyIdentity(user)
}

function isRealNameQueryForAccount(queryKey: readonly unknown[], accountId: string): boolean {
  const [root, queryAccountId, resource, filters] = queryKey
  if (
    root !== "camp-surveys" ||
    queryAccountId !== accountId ||
    (resource !== "admin" && resource !== "print") ||
    typeof filters !== "object" ||
    filters === null
  ) {
    return false
  }
  return (filters as { identity?: unknown }).identity === "real_name"
}

export function purgeSensitiveSurveyQueries(queryClient: QueryClient, accountId: string): void {
  const queryKey = ["camp-surveys", accountId] as const
  const predicate = (query: { queryKey: readonly unknown[] }): boolean =>
    isRealNameQueryForAccount(query.queryKey, accountId)
  const remove = () => queryClient.removeQueries({ queryKey, predicate })
  void queryClient.cancelQueries({ queryKey, predicate }).finally(remove)
  remove()
}
