import { apiFetch } from "../api/client"

export type OrganizationManagerCandidate = {
  account_id: string
  full_name: string
  nickname: string | null
}
export type OrganizationManager = OrganizationManagerCandidate & {
  organization_id: string
  granted_at: string
}
export type OrganizationManagerCandidatePage = {
  items: OrganizationManagerCandidate[]
  total: number
  page: number
  page_size: number
}

function managersPath(organizationId: string): string {
  return `/revenue-share/organizations/${encodeURIComponent(organizationId)}/managers`
}

export function listOrganizationManagers(organizationId: string, signal?: AbortSignal): Promise<OrganizationManager[]> {
  return apiFetch(managersPath(organizationId), { cache: "no-store", signal })
}

export function listOrganizationManagerCandidates(input: { q: string; page: number; page_size: number }, signal?: AbortSignal): Promise<OrganizationManagerCandidatePage> {
  const parameters = new URLSearchParams({ q: input.q, page: String(input.page), page_size: String(input.page_size) })
  return apiFetch(`/revenue-share/organization-manager-candidates?${parameters}`, { cache: "no-store", signal })
}

export function grantOrganizationManager(organizationId: string, accountId: string, signal?: AbortSignal): Promise<OrganizationManager> {
  return apiFetch(`${managersPath(organizationId)}/${encodeURIComponent(accountId)}`, { method: "PUT", signal })
}

export function revokeOrganizationManager(organizationId: string, accountId: string, signal?: AbortSignal): Promise<void> {
  return apiFetch(`${managersPath(organizationId)}/${encodeURIComponent(accountId)}`, { method: "DELETE", signal })
}
