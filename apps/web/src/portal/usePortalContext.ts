import { useQuery } from "@tanstack/react-query"

import { useAuth } from "../auth/AuthProvider"
import { getPortalContext, portalKeys } from "./api"

export const privatePortalQueryOptions = {
  staleTime: 0,
  gcTime: 0,
  refetchOnMount: "always" as const,
  refetchOnWindowFocus: true,
}

export const guardedPortalQueryOptions = {
  ...privatePortalQueryOptions,
  // The capability context owns focus reauthorization, then remounts or
  // re-enables the private projection exactly once.
  refetchOnWindowFocus: false,
}

export function usePortalContext() {
  const { user } = useAuth()
  return useQuery({
    queryKey: user
      ? portalKeys.context(user.account_id)
      : (["portal", "anonymous"] as const),
    queryFn: getPortalContext,
    enabled: Boolean(user?.account_id),
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
  })
}
