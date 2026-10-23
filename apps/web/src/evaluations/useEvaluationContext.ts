import { useQuery } from "@tanstack/react-query"

import { useAuth } from "../auth/AuthProvider"
import { evaluationKeys, getEvaluationContext } from "./api"

export const privateEvaluationQueryOptions = {
  staleTime: 0,
  gcTime: 0,
  refetchOnMount: "always" as const,
  refetchOnWindowFocus: true,
}

export const guardedEvaluationQueryOptions = {
  ...privateEvaluationQueryOptions,
  refetchOnWindowFocus: false,
}

export function useEvaluationContext({
  refetchOnMount = "always",
}: {
  refetchOnMount?: boolean | "always"
} = {}) {
  const { user } = useAuth()
  return useQuery({
    queryKey: user
      ? evaluationKeys.context(user.account_id)
      : (["evaluations", "anonymous", "context"] as const),
    queryFn: getEvaluationContext,
    enabled: Boolean(user?.account_id),
    ...privateEvaluationQueryOptions,
    refetchOnMount,
  })
}
