import { useQuery } from "@tanstack/react-query"
import { z } from "zod"
import { apiFetch } from "../api/client"
import { useAuth } from "../auth/AuthProvider"

export const claimStatusSchema = z.enum(["pending", "rejected", "expired", "invalidated", "approved", "revoked", "completed"])
const itemFields = {
  id: z.uuid(), version: z.number().int().positive(), status: claimStatusSchema,
  masked_name: z.string(), masked_email: z.string(), verified_at: z.iso.datetime({ offset: true }),
  expires_at: z.iso.datetime({ offset: true }), allowed_actions: z.array(z.string()),
}
const itemSchema = z.strictObject(itemFields)
const guardianView = z.strictObject({
  full_name: z.string(), identity_number: z.string(), phone: z.string(), email: z.string().nullable(),
  relationship: z.string(), sex: z.enum(["male", "female"]), date_of_birth: z.iso.date(), is_primary: z.boolean(),
})
const completionView = z.strictObject({
  phone: z.string().nullable(), sex: z.enum(["male", "female"]).nullable(), date_of_birth: z.iso.date().nullable(),
  issuing_country: z.string().nullable(), guardians: z.array(guardianView),
})
export type ClaimCompletionView = z.infer<typeof completionView>
const detailSchema = z.strictObject({ ...itemFields,
  person_id: z.uuid().nullable(), applicant_masked_name: z.string(), applicant_masked_identity: z.string(), applicant_email: z.email(),
  profile_masked_name: z.string().nullable(), profile_masked_identity: z.string().nullable(), profile_masked_email: z.string().nullable(),
  private_match_reason: z.string().nullable(), same_target_pending_count: z.number().int().nonnegative(), can_approve: z.boolean(),
  applicant_document_type: z.enum(["cn_id", "passport"]).optional(), applicant_issuing_country: z.string().nullable().optional(),
  applicant_completion: completionView.nullable().optional(), profile_completion: completionView.nullable().optional(),
})
const pageSchema = z.strictObject({ items: z.array(itemSchema), total: z.number().int().nonnegative(), pending_count: z.number().int().nonnegative(), page: z.number().int().positive(), page_size: z.number().int().min(1).max(100) })
const contextSchema = z.strictObject({ can_enter: z.boolean() })
const filtersSchema = z.strictObject({ status: claimStatusSchema.optional(), page: z.number().int().positive(), page_size: z.number().int().min(1).max(100) })
const decisionSchema = z.strictObject({ expected_version: z.number().int().positive(), idempotency_key: z.uuid(), identity_confirmed: z.literal(true), note: z.string().trim().max(500).nullable() })
const reasonSchema = z.strictObject({ expected_version: z.number().int().positive(), idempotency_key: z.uuid(), reason: z.string().trim().min(1).max(500) })
export type ClaimDetail = z.infer<typeof detailSchema>
export type ClaimFilters = z.infer<typeof filtersSchema>
export type ClaimDecisionInput = z.infer<typeof decisionSchema>
export type ClaimReasonInput = z.infer<typeof reasonSchema>
export const claimAdminKeys = {
  root: (account: string) => ["profile-claims-admin", account] as const,
  context: (account: string) => ["profile-claims-admin", account, "context"] as const,
  privateRoot: (account: string) => ["profile-claims-admin", account, "private"] as const,
  list: (account: string, filters: ClaimFilters) => ["profile-claims-admin", account, "private", "list", filters] as const,
  detail: (account: string, id: string) => ["profile-claims-admin", account, "private", "detail", id] as const,
}
const base = "/personnel-intake/profile-claims"
async function request<T>(path: string, schema: z.ZodType<T>, signal: AbortSignal, input?: object): Promise<T> {
  signal.throwIfAborted()
  const value = await apiFetch<unknown>(`${base}${path}`, { signal, cache: "no-store",
    ...(input === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }),
  })
  signal.throwIfAborted()
  return schema.parse(value)
}
const target = (id: string) => `/${encodeURIComponent(z.uuid().parse(id))}`
export const claimAdminApi = {
  context: (signal: AbortSignal) => request("/context", contextSchema, signal),
  list: (filters: ClaimFilters, signal: AbortSignal) => {
    const parsed = filtersSchema.parse(filters)
    const query = new URLSearchParams({ page: String(parsed.page), page_size: String(parsed.page_size) })
    if (parsed.status) query.set("status", parsed.status)
    return request(`?${query}`, pageSchema, signal)
  },
  detail: async (id: string, signal: AbortSignal) => {
    const value = await request(target(id), detailSchema, signal)
    if (value.id !== id) throw new Error("Mismatched claim detail")
    return value
  },
  approve: (id: string, input: ClaimDecisionInput, signal: AbortSignal) => request(`${target(id)}/approve`, detailSchema, signal, decisionSchema.parse(input)),
  reject: (id: string, input: ClaimReasonInput, signal: AbortSignal) => request(`${target(id)}/reject`, detailSchema, signal, reasonSchema.parse(input)),
  revoke: (id: string, input: ClaimReasonInput, signal: AbortSignal) => request(`${target(id)}/revoke`, detailSchema, signal, reasonSchema.parse(input)),
}

export function useClaimAdminContext() {
  const { user, status } = useAuth()
  // A required global grant is a request optimization, never sufficient admission.
  const enabled = status === "authenticated" && Boolean(user?.account_id) && user?.permissions.includes("accounts.manage") === true
  const query = useQuery({ queryKey: claimAdminKeys.context(user?.account_id ?? "anonymous"),
    queryFn: ({ signal }) => claimAdminApi.context(signal), enabled,
    retry: false, staleTime: 0, gcTime: 0, refetchOnWindowFocus: "always",
  })
  return { ...query, enabled, allowed: enabled && query.data?.can_enter === true && !query.isError && !query.isFetching }
}
