import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { getInvitationSources } from "./api"
import { invitationKeys } from "./keys"

export function useInvitationAccess(accountId: string) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: invitationKeys.sources(accountId),
    queryFn: ({ signal }) => getInvitationSources(signal),
    staleTime: 0,
    gcTime: 0,
    retry: false,
    refetchInterval: 30000,
    refetchOnWindowFocus: true,
    refetchOnMount: "always",
  })
  // An offline refresh can remain success/paused with old data. Only idle,
  // successfully validated sources may open the private housing projection.
  const validating = query.isPending || query.fetchStatus !== "idle"
  const ready = query.isSuccess && !validating

  useEffect(() => {
    const emptySources = query.isSuccess && !query.data.sources.length
    if (!validating && !query.isError && !emptySources) return
    const queryKey = query.isError || emptySources
      ? invitationKeys.root(accountId)
      : invitationKeys.housingRoot(accountId)
    const remove = () => client.removeQueries({ queryKey })
    remove()
    void client.cancelQueries({ queryKey }).finally(remove)
  }, [accountId, client, validating, query.isError, query.isSuccess, query.data])

  // AuthProvider synchronously clears/cancels queries at every account/session
  // boundary. Ordinary route unmounts use gcTime:0 and consumed AbortSignals;
  // manually destroying an active sources query in effect cleanup would also
  // destroy it during StrictMode's effect replay, leaving a pending observer.
  return {
    sources: ready ? query.data.sources : [],
    ready,
    validating,
    error: query.error,
    revalidate: query.refetch,
    epoch: query.dataUpdatedAt,
  }
}
