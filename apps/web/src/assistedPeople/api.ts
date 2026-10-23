import { z } from "zod"
import { apiFetch, ProblemDetailsError } from "../api/client"
import type { IntakeInput } from "./types"
import { statusSchema as housingStatusSchema } from "../invitations/api"

const uuid = z.uuid()
const kind = z.enum(["student", "player", "both"])
const status = z.enum(["pending", "returned", "approved", "withdrawn"])
// Public detail contains decrypted strings, never SecretStr's masked serialization.
const secret = z.string().min(1).refine((value) => !/^\*+$/.test(value))
const guardian = z.strictObject({
  full_name: z.string(), identity_number: secret, phone: secret, email: secret.nullable(),
  relationship: z.string(), sex: z.enum(["male", "female"]), date_of_birth: z.iso.date(), is_primary: z.boolean(),
})
export const intakePayloadSchema = z.strictObject({
  kind, full_name: z.string(), identity_number: secret, phone: secret, email: secret,
  sex: z.enum(["male", "female"]), date_of_birth: z.iso.date(), guardians: z.array(guardian),
  coach_person_ids: z.array(uuid), team_id: uuid.nullable(),
})
const itemFields = {
  id: uuid, version: z.number().int().positive(), status, kind, allowed_actions: z.array(z.string()),
  approved_person_id: uuid.nullable().optional(),
  submitted_by: z.strictObject({ id: uuid, label: z.string() }),
}
export const intakeListSchema = z.strictObject({
  items: z.array(z.strictObject(itemFields)), total: z.number().int().nonnegative(),
  page: z.number().int().positive(), page_size: z.number().int().min(1).max(100),
})
export const intakeDetailSchema = z.strictObject({ ...itemFields,
  scoped_payload: intakePayloadSchema.nullable(), returned_reason: z.string().nullable(),
  profile_version: z.number().int().positive().nullable().optional(),
})
const option = z.strictObject({ id: uuid, label: z.string() })
export const assistedContextSchema = z.strictObject({
  can_submit: z.boolean(), can_review: z.boolean(), can_direct_create: z.boolean(), can_read_managed: z.boolean(),
  allowed_team_ids: z.array(uuid), eligible_coaches: z.array(option), eligible_teams: z.array(option),
})
const approvalSchema = z.strictObject({ request_id: uuid.nullable(), person_id: uuid, approval_id: uuid })
const housingDate = z.iso.datetime({ offset: true })
const managedStaySchema = z.strictObject({
  building_name: z.string(), floor_name: z.string(), room_name: z.string(),
  duration_mode: z.enum(["long_term", "fixed_term"]), planned_check_in_at: housingDate,
  planned_check_out_at: housingDate.nullable(), actual_check_in_at: housingDate.nullable(), actual_check_out_at: housingDate.nullable(),
})
export const managedPeopleSchema = z.strictObject({
  items: z.array(z.strictObject({
    person_id: uuid, full_name: z.string(), nickname: z.string().nullable(),
    kinds: z.array(z.enum(["student", "player"])).min(1).max(2),
    authorized_affiliations: z.array(z.strictObject({ kind: z.enum(["coach", "team"]), id: uuid, label: z.string() })),
    housing: z.strictObject({ status: housingStatusSchema, stay: managedStaySchema.nullable(), next_planned_stay: managedStaySchema.nullable() }),
  })), next_cursor: z.string().max(1024).nullable(), total: z.number().int().nonnegative(),
})
const managedFiltersSchema = z.strictObject({
  query: z.string().trim().max(80).optional(), kind: kind.optional(), team_id: uuid.optional(),
  housing_status: housingStatusSchema.optional(), cursor: z.string().max(1024).optional(),
  page_size: z.number().int().min(1).max(100).optional(),
})
export type ManagedPeopleFilters = z.infer<typeof managedFiltersSchema>
export type ManagedPeople = z.infer<typeof managedPeopleSchema>
export type AssistedContext = z.infer<typeof assistedContextSchema>
export type IntakeDetail = z.infer<typeof intakeDetailSchema>
export type IntakeList = z.infer<typeof intakeListSchema>
export interface AssistedRequestFilters {
  view: "mine" | "review"
  status?: "pending" | "returned" | "approved" | "withdrawn"
  kind?: "student" | "player" | "both"
  submitter_query?: string
}
const base = "/personnel-intake"
async function get<T>(path: string, schema: z.ZodType<T>, signal: AbortSignal): Promise<T> {
  return schema.parse(await apiFetch<unknown>(`${base}${path}`, { signal, cache: "no-store" }))
}
async function post<T>(path: string, body: unknown, schema: z.ZodType<T>, signal: AbortSignal): Promise<T> {
  return schema.parse(await apiFetch<unknown>(`${base}${path}`, {
    method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }))
}
export const assistedPeopleApi = {
  context: (signal: AbortSignal) => get("/context", assistedContextSchema, signal),
  managedPeople: async (filters: ManagedPeopleFilters, signal: AbortSignal) => {
    const input = managedFiltersSchema.parse(filters)
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries({ ...input, page_size: input.page_size ?? 20 })) {
      if (value !== undefined && value !== "") query.set(key, String(value))
    }
    const result = await get(`/managed-people?${query}`, managedPeopleSchema, signal)
    signal.throwIfAborted()
    return result
  },
  list: (page: number, signal: AbortSignal, filters: AssistedRequestFilters) => {
    const query = new URLSearchParams({ page: String(page), page_size: "20", view: filters.view })
    if (filters.status) query.set("status", filters.status)
    if (filters.kind) query.set("kind", filters.kind)
    if (filters.view === "review" && filters.submitter_query?.trim()) query.set("submitter_query", filters.submitter_query.trim())
    return get(`/requests?${query}`, intakeListSchema, signal)
  },
  detail: (id: string, signal: AbortSignal) => get(`/requests/${encodeURIComponent(id)}`, intakeDetailSchema, signal),
  submit: (input: IntakeInput, signal: AbortSignal) => post("/requests", input, intakeDetailSchema, signal),
  resubmit: (id: string, version: number, input: IntakeInput, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/resubmit`, { ...input, expected_version: version }, intakeDetailSchema, signal),
  withdraw: (id: string, version: number, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/withdraw`, { expected_version: version }, intakeDetailSchema, signal),
  approve: (id: string, version: number, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/approve`, { expected_version: version }, approvalSchema, signal),
  returnRequest: (id: string, version: number, reason: string, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/return`, { expected_version: version, reason: reason.trim() }, intakeDetailSchema, signal),
  directCreate: (input: IntakeInput, key: string, signal: AbortSignal) => post("/direct-create", { ...input, idempotency_key: key }, approvalSchema, signal),
}

export function isPrivateDenial(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
}
export function assistedErrorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError && error.problem.status === 409) return "资料版本或状态已变化，当前草稿已保留。请重新查看最新详情后再操作；也可能存在重复档案，请勿重复建档。"
  if (isPrivateDenial(error)) return "当前无权访问此建档资料，请刷新权限或联系管理员。"
  return "操作未成功，请检查资料或稍后重试。"
}
