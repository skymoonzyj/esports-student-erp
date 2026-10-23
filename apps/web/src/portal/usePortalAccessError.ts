import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import { useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { portalKeys } from "./api"

export function usePortalAccessError(error: unknown): {
  permissionRevoked: boolean
  message: string
} {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const handled = useRef<{ accountId: string | null; deniedQueries: Set<string> }>({
    accountId: null,
    deniedQueries: new Set(),
  })
  const permissionRevoked =
    error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)

  useEffect(() => {
    const accountId = user?.account_id ?? null
    if (handled.current.accountId !== accountId) {
      handled.current = { accountId, deniedQueries: new Set() }
    }
    if (!user) return
    const root = portalKeys.root(user.account_id)
    const queries = queryClient.getQueryCache().findAll({ queryKey: root })
    // A new successful response to the denied query is the only authorization
    // signal that resets its cleanup guard. Removing an observed query causes
    // an automatic refetch, whose pending/error states must not reset it.
    for (const query of queries) {
      if (query.state.status === "success") {
        handled.current.deniedQueries.delete(query.queryHash)
      }
    }
    if (!permissionRevoked) return
    const deniedQuery = queries.find((query) => query.state.error === error)
    const denialKey = deniedQuery?.queryHash ?? "direct"
    if (handled.current.deniedQueries.has(denialKey)) return
    handled.current.deniedQueries.add(denialKey)
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    void cancellation.finally(() => {
      queryClient.removeQueries({ queryKey: root })
    })
    navigate("/", { replace: true })
  }, [error, navigate, permissionRevoked, queryClient, user])

  return {
    permissionRevoked,
    message:
      error instanceof ProblemDetailsError
        ? error.problem.detail
        : "服务暂时不可用，请重试。",
  }
}
