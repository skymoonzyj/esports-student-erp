import { describe, expect, it, vi } from "vitest"
import { QueryClient } from "@tanstack/react-query"

import {
  accountRoot,
  catalog,
  foodPreferenceKeys,
  person,
  purgePreviousFoodPreferenceRevisions,
  revisionRoot,
} from "./keys"

const accountId = "account-a"
const otherAccountId = "account-b"
const previousRevision = "revision-a"
const olderRevision = "revision-older"
const nextRevision = "revision-b"
const personId = "person-a"

describe("food preference query keys", () => {
  it("keeps the frozen account, catalog, revision, and person arrays", () => {
    expect(accountRoot(accountId)).toEqual(["food-preferences", accountId])
    expect(catalog(accountId)).toEqual([
      "food-preferences",
      accountId,
      "catalog",
    ])
    expect(revisionRoot(accountId, previousRevision)).toEqual([
      "food-preferences",
      accountId,
      "revision",
      previousRevision,
    ])
    expect(person(accountId, previousRevision, personId)).toEqual([
      "food-preferences",
      accountId,
      "revision",
      previousRevision,
      "person",
      personId,
    ])
    expect(foodPreferenceKeys.accountRoot(accountId)).toEqual(accountRoot(accountId))
    expect(foodPreferenceKeys.catalog(accountId)).toEqual(catalog(accountId))
    expect(foodPreferenceKeys.revisionRoot(accountId, previousRevision)).toEqual(
      revisionRoot(accountId, previousRevision),
    )
    expect(foodPreferenceKeys.person(accountId, previousRevision, personId)).toEqual(
      person(accountId, previousRevision, personId),
    )
  })

  it("isolates account, revision, and person changes", () => {
    expect(accountRoot(accountId)).not.toEqual(accountRoot(otherAccountId))
    expect(revisionRoot(accountId, previousRevision)).not.toEqual(
      revisionRoot(accountId, nextRevision),
    )
    expect(person(accountId, previousRevision, personId)).not.toEqual(
      person(accountId, previousRevision, "person-b"),
    )
    expect(foodPreferenceKeys.self(accountId, previousRevision).slice(0, 4)).toEqual(
      revisionRoot(accountId, previousRevision),
    )
    expect(
      foodPreferenceKeys.suggestions(accountId, previousRevision, "米", 10).slice(0, 4),
    ).toEqual(revisionRoot(accountId, previousRevision))
    expect(
      foodPreferenceKeys.statistics(accountId, previousRevision, {
        population: "students",
        include_inactive: false,
        page: 1,
        page_size: 20,
      }).slice(0, 4),
    ).toEqual(revisionRoot(accountId, previousRevision))
  })
})

describe("purgePreviousFoodPreferenceRevisions", () => {
  it("cancels and removes only old current-account revisions twice", async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(catalog(accountId), { kind: "catalog" })
    queryClient.setQueryData(person(accountId, previousRevision, personId), {
      kind: "previous",
    })
    queryClient.setQueryData(
      foodPreferenceKeys.self(accountId, previousRevision),
      { kind: "previous-self" },
    )
    queryClient.setQueryData(person(accountId, olderRevision, personId), {
      kind: "older",
    })
    queryClient.setQueryData(
      person(accountId, nextRevision, personId),
      { kind: "next" },
    )
    queryClient.setQueryData(
      person(otherAccountId, previousRevision, personId),
      { kind: "other-account" },
    )

    const releases = new Map<string, () => void>()
    const cancelQueries = vi
      .spyOn(queryClient, "cancelQueries")
      .mockImplementation((filters) => {
        const revisionValue = String(filters?.queryKey?.[3])
        return new Promise<void>((resolve) => {
          releases.set(revisionValue, resolve)
        })
      })
    const removeQueries = vi.spyOn(queryClient, "removeQueries")

    const pending = purgePreviousFoodPreferenceRevisions(
      queryClient,
      accountId,
      nextRevision,
    )
    await Promise.resolve()

    expect(cancelQueries).toHaveBeenCalledTimes(2)
    expect(cancelQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, previousRevision),
    })
    expect(cancelQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, olderRevision),
    })
    expect(removeQueries).toHaveBeenCalledTimes(2)
    expect(removeQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, previousRevision),
    })
    expect(removeQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, olderRevision),
    })
    expect(releases.has(previousRevision)).toBe(true)
    expect(releases.has(olderRevision)).toBe(true)
    expect(
      queryClient.getQueryData(person(accountId, previousRevision, personId)),
    ).toBeUndefined()
    expect(
      queryClient.getQueryData(person(accountId, olderRevision, personId)),
    ).toBeUndefined()
    expect(queryClient.getQueryData(catalog(accountId))).toEqual({ kind: "catalog" })
    expect(
      queryClient.getQueryData(person(accountId, nextRevision, personId)),
    ).toEqual({ kind: "next" })
    expect(
      queryClient.getQueryData(person(otherAccountId, previousRevision, personId)),
    ).toEqual({ kind: "other-account" })

    releases.get(previousRevision)?.()
    releases.get(olderRevision)?.()
    await pending

    expect(removeQueries).toHaveBeenCalledTimes(4)
    expect(removeQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, previousRevision),
    })
    expect(removeQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, olderRevision),
    })
    expect(queryClient.getQueryData(person(accountId, previousRevision, personId))).toBeUndefined()
  })

  it("is idempotent and retains catalog and next revision data", async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(catalog(accountId), { kind: "catalog" })
    queryClient.setQueryData(person(accountId, nextRevision, personId), {
      kind: "next",
    })

    const cancelQueries = vi.spyOn(queryClient, "cancelQueries")
    const removeQueries = vi.spyOn(queryClient, "removeQueries")

    await purgePreviousFoodPreferenceRevisions(queryClient, accountId, nextRevision)
    await purgePreviousFoodPreferenceRevisions(queryClient, accountId, nextRevision)

    expect(cancelQueries).not.toHaveBeenCalled()
    expect(removeQueries).not.toHaveBeenCalled()
    expect(queryClient.getQueryData(catalog(accountId))).toEqual({ kind: "catalog" })
    expect(queryClient.getQueryData(person(accountId, nextRevision, personId))).toEqual({
      kind: "next",
    })
  })

  it("rescans and removes an old revision added while cancellation is pending", async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(person(accountId, previousRevision, personId), {
      kind: "previous",
    })
    queryClient.setQueryData(
      person(otherAccountId, previousRevision, personId),
      { kind: "other-account" },
    )

    let releasePrevious: (() => void) | undefined
    const cancelQueries = vi
      .spyOn(queryClient, "cancelQueries")
      .mockImplementation((filters) => {
        if (filters?.queryKey?.[3] !== previousRevision) {
          return Promise.resolve()
        }
        return new Promise<void>((resolve) => {
          releasePrevious = resolve
        })
      })

    const pending = purgePreviousFoodPreferenceRevisions(
      queryClient,
      accountId,
      nextRevision,
    )
    await Promise.resolve()
    queryClient.setQueryData(person(accountId, olderRevision, personId), {
      kind: "late-old",
    })

    releasePrevious?.()
    await pending

    expect(cancelQueries).toHaveBeenCalledWith({
      queryKey: revisionRoot(accountId, olderRevision),
    })
    expect(
      queryClient.getQueryData(person(accountId, olderRevision, personId)),
    ).toBeUndefined()
    expect(
      queryClient.getQueryData(
        person(otherAccountId, previousRevision, personId),
      ),
    ).toEqual({ kind: "other-account" })
  })

  it("does not let an older overlapping purge remove a revision that became current again", async () => {
    const queryClient = new QueryClient()
    queryClient.setQueryData(person(accountId, previousRevision, personId), {
      kind: "old-a",
    })

    const releases = new Map<string, () => void>()
    vi.spyOn(queryClient, "cancelQueries").mockImplementation((filters) => {
      const revisionValue = String(filters?.queryKey?.[3])
      return new Promise<void>((resolve) => {
        releases.set(revisionValue, resolve)
      })
    })

    const toNext = purgePreviousFoodPreferenceRevisions(
      queryClient,
      accountId,
      nextRevision,
    )
    await Promise.resolve()
    queryClient.setQueryData(person(accountId, nextRevision, personId), {
      kind: "current-b",
    })

    const backToPrevious = purgePreviousFoodPreferenceRevisions(
      queryClient,
      accountId,
      previousRevision,
    )
    await Promise.resolve()
    releases.get(nextRevision)?.()
    await backToPrevious

    queryClient.setQueryData(person(accountId, previousRevision, personId), {
      kind: "current-a",
    })
    releases.get(previousRevision)?.()
    await toNext

    expect(
      queryClient.getQueryData(person(accountId, previousRevision, personId)),
    ).toEqual({ kind: "current-a" })
    expect(
      queryClient.getQueryData(person(accountId, nextRevision, personId)),
    ).toBeUndefined()
  })
})
