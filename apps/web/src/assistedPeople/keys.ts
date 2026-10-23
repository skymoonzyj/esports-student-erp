export const assistedPeopleKeys = {
  account: (accountId: string) => ["assisted-people", accountId] as const,
  context: (accountId: string) => [...assistedPeopleKeys.account(accountId), "context"] as const,
  list: (accountId: string, page: number, filters: object) => [...assistedPeopleKeys.account(accountId), "list", page, filters] as const,
  detail: (accountId: string, id: string) => [...assistedPeopleKeys.account(accountId), "detail", id] as const,
  managed: (accountId: string, filters: object) => [...assistedPeopleKeys.account(accountId), "managed", filters] as const,
}
