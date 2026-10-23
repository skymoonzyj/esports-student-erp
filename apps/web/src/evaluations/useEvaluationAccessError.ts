import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import { useLocation, useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { evaluationKeys } from "./api"
import { EvaluationDirectoryError } from "./foundationApi"

export function useEvaluationAccessError(error: unknown | readonly unknown[]): {
  permissionRevoked: boolean
  message: string
} {
  const { user, refreshUser } = useAuth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const handledRevocation = useRef(false)
  const errors = Array.isArray(error) ? error : [error]
  const revokedError = errors.find(
    (item) =>
      item instanceof ProblemDetailsError &&
      item.problem.status === 403 &&
      item.problem.code === "evaluation_access_denied",
  )
  const displayedError = revokedError ?? errors.find((item) => item != null)
  const permissionRevoked = revokedError !== undefined

  useEffect(() => {
    if (!permissionRevoked) {
      handledRevocation.current = false
      return
    }
    if (!user || handledRevocation.current) return
    handledRevocation.current = true
    const evaluationRoot = evaluationKeys.root(user.account_id)
    void queryClient.cancelQueries({ queryKey: evaluationRoot })
    queryClient.removeQueries({ queryKey: evaluationRoot })
    if (location.pathname === "/evaluations" || location.pathname.startsWith("/evaluations/")) {
      navigate("/", { replace: true })
    }
    void refreshUser()
      .catch(() => undefined)
      .finally(async () => {
        await queryClient.cancelQueries({ queryKey: evaluationRoot })
        queryClient.removeQueries({ queryKey: evaluationRoot })
      })
  }, [location.pathname, navigate, permissionRevoked, queryClient, refreshUser, user])

  return {
    permissionRevoked,
    message:
      displayedError instanceof ProblemDetailsError
        ? displayedError.problem.detail
        : displayedError instanceof EvaluationDirectoryError
          ? displayedError.message
        : "评价中心暂时不可用，请重试。",
  }
}
