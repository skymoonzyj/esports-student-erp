import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useRef } from "react"
import { useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { surveyKeys } from "./surveyKeys"

type SurveyAccessRevocationOptions = {
  accountId?: string
  errors?: readonly unknown[]
  onRevoke?: () => void
}

export function isSurveyAccessRevoked(error: unknown): error is ProblemDetailsError {
  return error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)
}

export function useSurveyAccessRevocation({
  accountId,
  errors = [],
  onRevoke,
}: SurveyAccessRevocationOptions): (error: unknown) => boolean {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const revokedAccountRef = useRef<string | null>(null)
  const onRevokeRef = useRef(onRevoke)
  onRevokeRef.current = onRevoke

  const revokeAccess = useCallback((error: unknown): boolean => {
    if (!accountId || !isSurveyAccessRevoked(error)) return false
    if (revokedAccountRef.current === accountId) return true
    revokedAccountRef.current = accountId

    onRevokeRef.current?.()
    const queryKey = surveyKeys.root(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey })
    const remove = () => queryClient.removeQueries({ queryKey })
    remove()
    void cancellation.finally(remove)
    navigate("/evaluations", { replace: true })
    return true
  }, [accountId, navigate, queryClient])

  const revokedError = errors.find(isSurveyAccessRevoked)
  useEffect(() => {
    if (revokedError) revokeAccess(revokedError)
  }, [revokeAccess, revokedError])

  useEffect(() => () => {
    if (accountId) {
      queryClient.removeQueries({ queryKey: surveyKeys.root(accountId) })
    }
  }, [accountId, queryClient])

  return revokeAccess
}
