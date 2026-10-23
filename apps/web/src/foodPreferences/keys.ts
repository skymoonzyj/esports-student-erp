import type { QueryClient } from "@tanstack/react-query"

import type { FoodPreferenceStatisticsFilters } from "./api"

type PurgeLease = {
  generation: number
  nextRevision: string
}

const purgeLeases = new WeakMap<QueryClient, Map<string, PurgeLease>>()

export const accountRoot = (accountId: string) =>
  ["food-preferences", accountId] as const

export const catalog = (accountId: string) =>
  [...accountRoot(accountId), "catalog"] as const

export const revisionRoot = (accountId: string, revision: string) =>
  [...accountRoot(accountId), "revision", revision] as const

export const person = (
  accountId: string,
  revision: string,
  personId: string,
) => [...revisionRoot(accountId, revision), "person", personId] as const

export const foodPreferenceKeys = {
  accountRoot,
  catalog,
  revisionRoot,
  person,
  self: (accountId: string, revision: string) =>
    [...revisionRoot(accountId, revision), "self"] as const,
  suggestions: (
    accountId: string,
    revision: string,
    query: string,
    limit: number,
  ) =>
    [
      ...revisionRoot(accountId, revision),
      "suggestions",
      query,
      limit,
    ] as const,
  statistics: (
    accountId: string,
    revision: string,
    filters: FoodPreferenceStatisticsFilters,
  ) =>
    [
      ...revisionRoot(accountId, revision),
      "statistics",
      {
        population: filters.population,
        include_inactive: filters.include_inactive,
        page: filters.page,
        page_size: filters.page_size,
      },
    ] as const,
}

function previousRevisionRoots(
  queryClient: QueryClient,
  accountId: string,
  nextRevision: string,
): ReturnType<typeof revisionRoot>[] {
  const revisions = new Set<string>()
  for (const query of queryClient.getQueryCache().findAll({
    queryKey: accountRoot(accountId),
  })) {
    const key = query.queryKey
    if (
      key[0] === "food-preferences" &&
      key[1] === accountId &&
      key[2] === "revision" &&
      typeof key[3] === "string" &&
      key[3] !== nextRevision
    ) {
      revisions.add(key[3])
    }
  }
  return [...revisions]
    .sort((left, right) => left.localeCompare(right))
    .map((revision) => revisionRoot(accountId, revision))
}

function beginPurge(
  queryClient: QueryClient,
  accountId: string,
  nextRevision: string,
): number {
  let accountLeases = purgeLeases.get(queryClient)
  if (!accountLeases) {
    accountLeases = new Map()
    purgeLeases.set(queryClient, accountLeases)
  }
  const generation = (accountLeases.get(accountId)?.generation ?? 0) + 1
  accountLeases.set(accountId, { generation, nextRevision })
  return generation
}

function ownsPurgeLease(
  queryClient: QueryClient,
  accountId: string,
  nextRevision: string,
  generation: number,
): boolean {
  const lease = purgeLeases.get(queryClient)?.get(accountId)
  return (
    lease?.generation === generation && lease.nextRevision === nextRevision
  )
}

async function cancelAndRemoveRevisionRoot(
  queryClient: QueryClient,
  queryKey: ReturnType<typeof revisionRoot>,
  ownsLease: () => boolean,
): Promise<void> {
  const cancellation = queryClient.cancelQueries({ queryKey })
  queryClient.removeQueries({ queryKey })
  try {
    await cancellation
  } finally {
    if (ownsLease()) queryClient.removeQueries({ queryKey })
  }
}

export async function purgePreviousFoodPreferenceRevisions(
  queryClient: QueryClient,
  accountId: string,
  nextRevision: string,
): Promise<void> {
  const generation = beginPurge(queryClient, accountId, nextRevision)
  const ownsLease = () =>
    ownsPurgeLease(queryClient, accountId, nextRevision, generation)

  let roots = previousRevisionRoots(queryClient, accountId, nextRevision)
  while (roots.length > 0) {
    await Promise.all(
      roots.map((queryKey) =>
        cancelAndRemoveRevisionRoot(queryClient, queryKey, ownsLease),
      ),
    )
    if (!ownsLease()) return
    roots = previousRevisionRoots(queryClient, accountId, nextRevision)
  }
}
