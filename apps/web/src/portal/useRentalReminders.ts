import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { portalKeys } from "./api"
import { getRentalReminders, rentalReminderKeys } from "./rentalRemindersApi"
import { guardedPortalQueryOptions } from "./usePortalContext"

export function useRentalReminders({ accountId, contextReady, contextEpoch }: { accountId: string | null; contextReady: boolean; contextEpoch: number }) {
  const client = useQueryClient()
  const scope = `${accountId ?? "anonymous"}:${contextEpoch}`
  const [pagination, setPagination] = useState({ scope, page: 1 })
  const [blockedScope, setBlockedScope] = useState<string | null>(null)
  const page = pagination.scope === scope ? pagination.page : 1
  const enabled = Boolean(accountId && contextReady && blockedScope !== scope)
  const requestStarted = useRef<number | null>(null)
  const query = useQuery({
    queryKey: rentalReminderKeys.page(accountId ?? "anonymous", contextEpoch, page),
    queryFn: async ({ signal }) => {
      const started = performance.now()
      const data = await getRentalReminders(page, signal)
      if (signal.aborted) throw new DOMException("请求已取消", "AbortError")
      requestStarted.current = started
      return data
    },
    ...guardedPortalQueryOptions,
    enabled,
    retry: (failureCount, error) => failureCount < 1 && !(error instanceof ProblemDetailsError && error.problem.status < 500),
    retryDelay: 1000,
  })

  useEffect(() => {
    if (!accountId) return
    const predicate = (cached: { queryKey: readonly unknown[] }) => !enabled || cached.queryKey[3] !== contextEpoch
    void client.cancelQueries({ queryKey: rentalReminderKeys.root(accountId), predicate })
    client.removeQueries({ queryKey: rentalReminderKeys.root(accountId), predicate })
    // Identity changes must purge even an in-flight previous account's response.
    return () => {
      void client.cancelQueries({ queryKey: rentalReminderKeys.root(accountId) })
      client.removeQueries({ queryKey: rentalReminderKeys.root(accountId) })
    }
  }, [accountId, client, contextEpoch, enabled])

  const reauthorize = useCallback(() => {
    if (!accountId) return
    setBlockedScope(scope)
    void client.cancelQueries({ queryKey: rentalReminderKeys.root(accountId) })
    client.removeQueries({ queryKey: rentalReminderKeys.root(accountId) })
    void client.invalidateQueries({ queryKey: portalKeys.context(accountId), exact: true }, { cancelRefetch: false })
  }, [accountId, client, scope])

  useEffect(() => {
    const resume = () => { if (document.visibilityState === "visible") reauthorize() }
    document.addEventListener("visibilitychange", resume)
    return () => document.removeEventListener("visibilitychange", resume)
  }, [reauthorize])

  useEffect(() => {
    if (!enabled || !query.data) return
    const data = query.data
    const serverDelay = Date.parse(data.next_refresh_at) - Date.parse(data.observed_at)
    const elapsed = requestStarted.current === null ? 0 : performance.now() - requestStarted.current
    // RTT includes time before observed_at, so this conservative bound can fire
    // early. Every fresh response must re-arm even the same server deadline;
    // only the server advancing its business date confirms midnight crossed.
    // Keep the 1s floor so a near-deadline response cannot cause a retry spin.
    const timer = setTimeout(() => {
      if (document.visibilityState !== "hidden") reauthorize()
    }, Math.min(2_147_483_647, Math.max(1000, serverDelay - elapsed)))
    return () => clearTimeout(timer)
  }, [accountId, enabled, query.data, reauthorize])

  useEffect(() => {
    const data = query.data
    if (enabled && data && page > 1 && !data.items.length && (page - 1) * data.page_size >= data.total) setPagination({ scope, page: 1 })
  }, [enabled, page, query.data, scope])

  function setPage(value: number) { setPagination({ scope, page: Math.max(1, Math.trunc(value) || 1) }) }
  return { query: { ...query, data: enabled ? query.data : undefined }, page, setPage, retry: reauthorize }
}
