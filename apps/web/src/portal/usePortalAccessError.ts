import { useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"
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
  const permissionRevoked =
    error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)

  useEffect(() => {
    if (!permissionRevoked || !user) return
    const root = portalKeys.root(user.account_id)
    const cancellation = queryClient.cancelQueries({ queryKey: root })
    queryClient.removeQueries({ queryKey: root })
    void cancellation.finally(() => {
      queryClient.removeQueries({ queryKey: root })
    })
    navigate("/", { replace: true })
  }, [navigate, permissionRevoked, queryClient, user])

  return {
    permissionRevoked,
    message:
      error instanceof ProblemDetailsError
        ? error.problem.detail
        : "服务暂时不可用，请重试。",
  }
}
