import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import { portalKeys } from "./api"
import { departureKeys, getDepartureReminders, type DepartureWindow } from "./departureApi"
import { guardedPortalQueryOptions } from "./usePortalContext"

export function useDepartureReminders(input: { accountId: string | null; contextReady: boolean; contextEpoch: number }) {
  const { accountId, contextReady, contextEpoch } = input
  const client = useQueryClient()
  const scope = `${accountId ?? "anonymous"}:${contextEpoch}`
  const [pagination, setPagination] = useState({ scope, page: 1 })
  const [blockedScope, setBlockedScope] = useState<string | null>(null)
  const page = pagination.scope === scope ? pagination.page : 1
  const enabled = Boolean(accountId && contextReady && blockedScope !== scope)
  // Non-sensitive preference survives route unmount, but remains under logout cleanup.
  const preferenceKey = ["portal", accountId ?? "anonymous", "departure-window"] as const
  const preference = useQuery<DepartureWindow>({ queryKey: preferenceKey, queryFn: () => "three_days", initialData: "three_days", enabled: false, staleTime: Infinity, gcTime: Infinity })
  const window = preference.data ?? "three_days"
  const requestStarted = useRef<number | null>(null)
  const refreshedDeadline = useRef<string | null>(null)
  const query = useQuery({
    queryKey: departureKeys.page(accountId ?? "anonymous", contextEpoch, window, page, 20),
    queryFn: async ({ signal }) => {
      const started = performance.now()
      const data = await getDepartureReminders({ window, page, pageSize: 20 }, signal)
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
    void client.cancelQueries({ queryKey: departureKeys.root(accountId), predicate })
    client.removeQueries({ queryKey: departureKeys.root(accountId), predicate })
  }, [accountId, client, contextEpoch, enabled])

  const reauthorize = useCallback(() => {
    if (!accountId) return
    setBlockedScope(scope)
    void client.cancelQueries({ queryKey: departureKeys.root(accountId) })
    client.removeQueries({ queryKey: departureKeys.root(accountId) })
    // Visibility and the existing context focus observer can fire together.
    // Join an in-flight recheck instead of cancelling and starting another.
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
    const deadline = `${accountId}:${data.next_refresh_at}`
    if (refreshedDeadline.current === deadline) return
    const serverDelay = Date.parse(data.next_refresh_at) - Date.parse(data.observed_at)
    const elapsed = requestStarted.current === null ? 0 : performance.now() - requestStarted.current
    const delay = Math.min(2_147_483_647, Math.max(1000, serverDelay - elapsed))
    const timer = setTimeout(() => {
      refreshedDeadline.current = deadline
      if (document.visibilityState !== "hidden") reauthorize()
    }, delay)
    return () => clearTimeout(timer)
  }, [accountId, enabled, query.data, reauthorize])

  useEffect(() => {
    const data = query.data
    if (enabled && data && page > 1 && !data.items.length && (page - 1) * data.page_size >= data.total) {
      setPagination({ scope, page: 1 })
    }
  }, [enabled, page, query.data, scope])

  function setWindow(value: DepartureWindow) {
    client.setQueryData(preferenceKey, value)
    setPagination({ scope, page: 1 })
  }
  function setPage(value: number) { setPagination({ scope, page: Math.max(1, Math.trunc(value) || 1) }) }

  return {
    query: { ...query, data: enabled ? query.data : undefined },
    window, setWindow, page, setPage,
    retry: reauthorize,
  }
}
