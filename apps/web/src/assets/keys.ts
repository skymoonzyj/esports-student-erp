export const assetKeys = {
  root: ["assets"] as const,
  list: (filters: unknown) => ["assets", "list", filters] as const,
  mine: () => ["assets", "mine"] as const,
  person: (personId: string) => ["assets", "person", personId] as const,
  scan: (assetCode: string) => ["assets", "scan", assetCode] as const,
}

