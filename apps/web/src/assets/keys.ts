export const assetKeys = {
  root: ["assets"] as const,
  list: (accountId: string | null, filters: unknown) => ["assets", "list", accountId, filters] as const,
  mine: () => ["assets", "mine"] as const,
  person: (personId: string) => ["assets", "person", personId] as const,
  scan: (assetCode: string) => ["assets", "scan", assetCode] as const,
  categories: (accountId: string | null) => ["assets", "categories", accountId] as const,
}

