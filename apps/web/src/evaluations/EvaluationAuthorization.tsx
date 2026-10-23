import { createContext, useContext, useMemo } from "react"
import type { PropsWithChildren } from "react"

type EvaluationAuthorizationValue = {
  pending: boolean
  revision: number
}

const EvaluationAuthorizationContext = createContext<EvaluationAuthorizationValue>({
  pending: false,
  revision: 0,
})

export function EvaluationAuthorizationProvider({
  pending,
  revision,
  children,
}: PropsWithChildren<EvaluationAuthorizationValue>) {
  const value = useMemo(() => ({ pending, revision }), [pending, revision])
  return (
    <EvaluationAuthorizationContext.Provider value={value}>
      {children}
    </EvaluationAuthorizationContext.Provider>
  )
}

export function useEvaluationAuthorization(): EvaluationAuthorizationValue {
  return useContext(EvaluationAuthorizationContext)
}
