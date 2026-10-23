import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef, useState } from "react"
import { assistedPeopleApi, assistedErrorMessage, isPrivateDenial } from "./api"
import { assistedPeopleKeys } from "./keys"

export const assistedQueryOptions = { staleTime: 0, gcTime: 0, retry: false } as const

/** Call inside an account-keyed subtree. No previous-account placeholder data. */
export function useAssistedPeopleContext(accountId: string, enabled = true) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: assistedPeopleKeys.context(accountId),
    queryFn: ({ signal }) => assistedPeopleApi.context(signal), enabled,
    refetchOnWindowFocus: "always", ...assistedQueryOptions,
  })
  const context = query.isError ? undefined : query.data
  // gcTime=0 and the consumed AbortSignal clean up each unobserved query.
  // Do not purge an entire account tree on unmount: a shared guard/page may
  // still be observing that account. Actual auth switches clear QueryClient.
  useEffect(() => {
    if (!query.isError && !context) return
    const filters = { predicate: (entry: { queryKey: readonly unknown[] }) =>
      entry.queryKey[0] === "assisted-people" && entry.queryKey[1] === accountId && entry.queryKey[2] !== "context" &&
      (query.isError || (entry.queryKey[2] === "managed" ? !context?.can_read_managed :
        !(context?.can_submit || context?.can_review || context?.can_direct_create))) }
    void client.cancelQueries(filters)
    client.removeQueries(filters)
    // Remove the previous authorized context on a failed revalidation as well.
    if (query.isError && query.data) client.setQueryData(assistedPeopleKeys.context(accountId), null)
  }, [accountId, client, context, query.isError, query.data])
  return { ...query, context }
}

/** Aborts on unmount/account switch and suppresses late mutation responses. */
export function useAssistedOperation(accountId: string) {
  const client = useQueryClient()
  const controllers = useRef(new Set<AbortController>())
  const active = useRef(false)
  const inFlight = useRef(false)
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState("")
  const [denied, setDenied] = useState(false)
  useEffect(() => {
    active.current = true
    const pendingControllers = controllers.current
    return () => {
      active.current = false
      pendingControllers.forEach((controller) => controller.abort())
      pendingControllers.clear()
    }
  }, [accountId])
  const run = useCallback(async (operation: (signal: AbortSignal) => Promise<unknown>): Promise<boolean> => {
    if (inFlight.current || denied) return false
    inFlight.current = true
    setPending(true); setMessage("")
    const controller = new AbortController()
    controllers.current.add(controller)
    try {
      await operation(controller.signal)
      if (!active.current || controller.signal.aborted) return false
      setMessage("操作成功。")
      await client.invalidateQueries({ queryKey: assistedPeopleKeys.account(accountId) })
      return active.current && !controller.signal.aborted
    } catch (error) {
      if (!active.current || controller.signal.aborted) return false
      setMessage(assistedErrorMessage(error))
      if (isPrivateDenial(error)) {
        setDenied(true)
        const root = assistedPeopleKeys.account(accountId)
        await client.cancelQueries({ queryKey: root })
        client.removeQueries({ queryKey: root })
      }
      return false
    } finally {
      controllers.current.delete(controller)
      inFlight.current = false
      if (active.current) setPending(false)
    }
  }, [accountId, client, denied])
  return { run, pending, message, denied, clearMessage: () => setMessage("") }
}
