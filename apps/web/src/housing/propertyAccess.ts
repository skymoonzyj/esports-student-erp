import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRef } from "react"
import { useAuth } from "../auth/AuthProvider"
import { getHousingCatalog } from "./api"
import type { HousingCatalog } from "./api"
import { housingKeys } from "./housingKeys"
import { getHousingPropertyCapabilities } from "./propertyApi"
import type { HousingPropertyCapabilities } from "./propertyApi"

export function useHousingPropertyAccess(roomId?: string, enabled = true) {
  const { user } = useAuth()
  const client = useQueryClient()
  const signature = JSON.stringify([user?.account_id, user?.is_super_admin, [...(user?.permissions ?? [])].sort(), [...(user?.scoped_permissions ?? [])].sort()])
  const initialIdentity = useRef({ signature, unchanged: true })
  if (initialIdentity.current.signature !== signature) initialIdentity.current.unchanged = false
  const canonicalCatalog = useQuery({ queryKey: housingKeys.catalog(user?.account_id ?? "anonymous"), queryFn: ({ signal }) => getHousingCatalog(signal), enabled: enabled && !!user, retry: false, refetchOnMount: false })
  // A local account/permission change must refetch even if the shared catalog is still fresh.
  // Observe its revision as well so parent housing refreshes invalidate this boundary.
  const catalogKey = [...housingKeys.root(user?.account_id ?? "anonymous"), "property", signature, "catalog", canonicalCatalog.data?.access_revision ?? "pending"]
  const catalog = useQuery({ queryKey: catalogKey, queryFn: ({ signal }) => getHousingCatalog(signal), enabled: enabled && !!user && !!canonicalCatalog.data && !canonicalCatalog.isError, initialData: enabled && initialIdentity.current.unchanged ? canonicalCatalog.data : undefined, retry: false, gcTime: 0, staleTime: Infinity, refetchOnMount: false })
  const scope = [...housingKeys.root(user?.account_id ?? "anonymous"), "property", signature, catalog.data?.access_revision ?? "pending"]
  const capabilityKey = [...scope, "capabilities", roomId ?? "all"]
  const capabilities = useQuery({ queryKey: capabilityKey, queryFn: ({ signal }) => getHousingPropertyCapabilities(signal, roomId), enabled: enabled && !!user && !!catalog.data && !catalog.isError, retry: false, staleTime: 0, gcTime: 0 })
  function isAuthorizedNow(capability: keyof HousingPropertyCapabilities): boolean {
    const current = client.getQueryState<HousingPropertyCapabilities>(capabilityKey)
    const shared = client.getQueryState<HousingCatalog>(housingKeys.catalog(user?.account_id ?? "anonymous"))
    const scoped = client.getQueryState<HousingCatalog>(catalogKey)
    return enabled && !!user && current?.status === "success" && current.fetchStatus === "idle" && current.data?.[capability] === true && shared?.status === "success" && shared.fetchStatus === "idle" && scoped?.status === "success" && scoped.fetchStatus === "idle" && shared.data?.access_revision === canonicalCatalog.data?.access_revision && scoped.data?.access_revision === catalog.data?.access_revision
  }
  return { accountId: user?.account_id ?? "anonymous", scope, signature: JSON.stringify(scope), catalog: enabled ? catalog.data : undefined, capabilities: enabled ? capabilities.data : undefined, ready: enabled && !!user && !canonicalCatalog.isFetching && !canonicalCatalog.isError && !catalog.isFetching && !catalog.isError && !!catalog.data && !capabilities.isFetching && !capabilities.isError && !!capabilities.data, error: enabled ? canonicalCatalog.error ?? catalog.error ?? capabilities.error : null, isAuthorizedNow }
}
