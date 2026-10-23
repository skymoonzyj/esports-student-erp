import { z } from "zod"
import { apiFetch } from "../api/client"

const sourceSchema = z.object({
  source_kind: z.enum(["person", "organization"]),
  organization_id: z.uuid().nullable(),
  name: z.string(),
})
const staySchema = z.object({
  building_name: z.string(),
  floor_name: z.string(),
  room_name: z.string(),
  duration_mode: z.enum(["long_term", "fixed_term"]),
  planned_check_in_at: z.string(),
  planned_check_out_at: z.string().nullable(),
  actual_check_in_at: z.string().nullable(),
  actual_check_out_at: z.string().nullable(),
})
export const statusSchema = z.enum([
  "unarranged",
  "reserved",
  "check_in_pending",
  "checked_in",
  "checkout_pending",
  "checked_out",
])
const summarySchema = z.object({
  status: statusSchema,
  stay: staySchema.nullable(),
  next_planned_stay: staySchema.nullable(),
})
const sourcesSchema = z.object({ sources: z.array(sourceSchema) })
const pageSchema = z.object({
  as_of: z.string(),
  items: z.array(z.object({
    person_id: z.uuid(),
    full_name: z.string(),
    nickname: z.string().nullable(),
    source: sourceSchema,
    housing: summarySchema,
  })),
  total: z.number().int().nonnegative(),
  page: z.number().int().positive(),
  page_size: z.number().int().min(1).max(100),
})
export type InvitationSource = z.infer<typeof sourceSchema>
export type InvitationSourcesResponse = z.infer<typeof sourcesSchema>
export type InvitedHousingPage = z.infer<typeof pageSchema>
export type HousingSummary = z.infer<typeof summarySchema>
export type InvitedHousingFilters = {
  sourceKind: "all" | "person" | "organization"
  organizationId?: string
  status?: z.infer<typeof statusSchema>
  page: number
  pageSize: number
}

async function readPrivate<T>(path: string, schema: z.ZodType<T>, signal?: AbortSignal): Promise<T> {
  let raw: unknown
  try {
    raw = await apiFetch<unknown>(path, { signal, cache: "no-store" })
  } catch (error) {
    if (error instanceof SyntaxError) {
      // eslint-disable-next-line preserve-caught-error
      throw new Error("邀请信息响应格式无效")
    }
    throw error
  }
  signal?.throwIfAborted()
  const parsed = schema.safeParse(raw)
  if (!parsed.success) throw new Error("邀请信息响应格式无效")
  return parsed.data
}
export function getInvitationSources(signal?: AbortSignal): Promise<InvitationSourcesResponse> {
  return readPrivate("/referrals/me/sources", sourcesSchema, signal)
}
export function getInvitedHousing(filters: InvitedHousingFilters, signal?: AbortSignal): Promise<InvitedHousingPage> {
  const query = new URLSearchParams({ source_kind: filters.sourceKind })
  if (filters.sourceKind === "organization" && filters.organizationId) {
    query.set("organization_id", filters.organizationId)
  }
  if (filters.status) query.set("status", filters.status)
  query.set("page", String(filters.page))
  query.set("page_size", String(filters.pageSize))
  return readPrivate(`/referrals/me/housing?${query}`, pageSchema, signal)
}
