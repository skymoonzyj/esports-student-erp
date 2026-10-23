import { useQuery } from "@tanstack/react-query"

import { useAuth } from "../auth/AuthProvider"
import { getTeachingReportContext } from "./reportApi"
import { reportKeys } from "./reportKeys"

export function useTeachingReportContext() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  return useQuery({
    queryKey: reportKeys.context(accountId),
    queryFn: ({ signal }) => getTeachingReportContext(signal),
    enabled: Boolean(user?.account_id),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
    retry: false,
  })
}
