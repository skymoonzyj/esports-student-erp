import { z } from "zod"
import { apiFetch, ProblemDetailsError } from "../api/client"
import type { IntakeInput, DirectCreateInput, DirectCreatePendingInput, ManagedIdentityInput, StaffTeamSelection, ActivationRoleUpdateInput } from "./types"
import { statusSchema as housingStatusSchema } from "../invitations/api"
import { identityDocumentTypes, personDocumentTypes } from "./documentFacts"
import { identityKeys } from "./types"
import { emptyIdentityDraft, normalizeIdentityInput, selectedIdentities, staffRoleIdentity } from "./intakeIdentity"

const uuid = z.uuid()
const kind = z.enum(["student", "player", "both"])
const pendingKind = z.enum(["student", "player", "both", "staff"])
const status = z.enum(["pending", "returned", "approved", "withdrawn"])
// Public detail contains decrypted strings, never SecretStr's masked serialization.
const secret = z.string().min(1).refine((value) => !/^\*+$/.test(value))
const guardian = z.strictObject({
  full_name: z.string(), identity_number: secret, phone: secret, email: secret.nullable(),
  document_type: z.enum(identityDocumentTypes).optional(), document_issuing_country: z.string().nullable().optional(),
  relationship: z.string(), sex: z.enum(["male", "female"]), date_of_birth: z.iso.date(), is_primary: z.boolean(),
})
export const intakePayloadSchema = z.strictObject({
  kind, full_name: z.string(), identity_number: secret, phone: secret, email: secret.nullable(),
  document_type: z.enum(identityDocumentTypes).optional(), document_issuing_country: z.string().nullable().optional(),
  sex: z.enum(["male", "female"]), date_of_birth: z.iso.date(), guardians: z.array(guardian),
  coach_person_ids: z.array(uuid), team_id: uuid.nullable(),
})
const ordinarySubmitSchema = intakePayloadSchema.extend({ guardians: z.array(guardian.partial({ email: true, is_primary: true })) })
const strictManagedPayloadSchema = intakePayloadSchema.extend({ kind: pendingKind })
export const pendingIntakePayloadSchema = z.strictObject({
  material_version: z.literal(2), kind: pendingKind, full_name: z.string(), identity_number: secret,
  document_type: z.enum(personDocumentTypes), document_issuing_country: z.string().nullable(),
  phone: secret.nullable(), email: secret.nullable(), sex: z.enum(["male", "female"]).nullable(), date_of_birth: z.iso.date().nullable(),
  guardians: z.array(guardian), coach_person_ids: z.array(uuid), team_id: uuid.nullable(),
  team_allocation_status: z.enum(["pending", "assigned", "not_required"]),
})
export type PendingIntakePayload = z.infer<typeof pendingIntakePayloadSchema>
const itemFields = {
  id: uuid, version: z.number().int().positive(), status, kind: pendingKind, allowed_actions: z.array(z.string()),
  approved_person_id: uuid.nullable().optional(),
  submitted_by: z.strictObject({ id: uuid, label: z.string() }),
}
export const intakeListSchema = z.strictObject({
  items: z.array(z.strictObject(itemFields)), total: z.number().int().nonnegative(),
  page: z.number().int().positive(), page_size: z.number().int().min(1).max(100),
})
export const intakeDetailSchema = z.strictObject({ ...itemFields,
  scoped_payload: z.union([strictManagedPayloadSchema, pendingIntakePayloadSchema]).nullable(), returned_reason: z.string().nullable(),
  profile_version: z.number().int().positive().nullable().optional(),
})
const option = z.strictObject({ id: uuid, label: z.string() })
export const assistedContextSchema = z.strictObject({
  can_submit: z.boolean(), can_review: z.boolean(), can_direct_create: z.boolean(), can_read_managed: z.boolean(),
  can_direct_create_pending: z.boolean().default(false),
  allowed_team_ids: z.array(uuid), eligible_coaches: z.array(option), eligible_teams: z.array(option),
})
const approvalSchema = z.strictObject({ request_id: uuid.nullable(), person_id: uuid, approval_id: uuid })
const activationRoleKey = z.string().min(1).refine(key => !["admin", "super_admin", "root"].includes(key))
const activationRoleKeys = z.array(activationRoleKey).refine(keys => new Set(keys).size === keys.length)
export const activationRoleOptionsSchema = z.strictObject({ items: z.array(z.strictObject({
  key: activationRoleKey, name: z.string().min(1), assignable: z.boolean(), reason: z.string().nullable(),
  requestable: z.boolean().optional(),
})).refine(items => new Set(items.map(item => item.key)).size === items.length) })
export const activationRolePlanSchema = z.strictObject({
  person_id: uuid, version: z.number().int().nonnegative(), role_keys: activationRoleKeys,
  role_names: z.array(z.string()), can_edit: z.boolean(), account_activated: z.boolean(),
}).refine(value => value.role_keys.length === value.role_names.length)
export type ActivationRoleOptions = z.infer<typeof activationRoleOptionsSchema>
export const managementIdentityContextSchema = z.strictObject({
  role_options: activationRoleOptionsSchema.shape.items,
  can_assign_staff_teams: z.boolean(), staff_team_reason: z.string().nullable(),
  staff_team_options: z.array(z.strictObject({ id: uuid, project_id: uuid, project_name: z.string(), name: z.string() })).refine(options => new Set(options.map(option => option.id)).size === options.length),
})
export type ManagementIdentityContext = z.infer<typeof managementIdentityContextSchema>
export const managedIdentityInputSchema = z.strictObject({
  primary_identity: z.enum(identityKeys), concurrent_identities: z.array(z.enum(identityKeys)), primary_staff_identity: z.enum(identityKeys).nullable(),
}).refine(value => new Set([value.primary_identity, ...value.concurrent_identities]).size === value.concurrent_identities.length + 1)
const managedIdentityFields = { identity_selection: managedIdentityInputSchema, staff_teams: z.array(z.strictObject({ staff_role: z.enum(["manager", "coach", "assistant_coach"]), team_ids: z.array(uuid).refine(ids => new Set(ids).size === ids.length) })) }
function refineManagedIdentity(value: { identity_selection: ManagedIdentityInput; staff_teams: StaffTeamSelection[]; team_id: string | null }, context: z.RefinementCtx) {
  try {
    if (new Set(value.staff_teams.map(group => group.staff_role)).size !== value.staff_teams.length) throw new Error("工作人员岗位分组不能重复")
    const draft = { ...emptyIdentityDraft(), ...value.identity_selection, player_team_id: value.team_id }
    for (const group of value.staff_teams) {
      if (!selectedIdentities(draft).includes(staffRoleIdentity[group.staff_role])) throw new Error("队伍岗位必须属于所选身份")
      draft.staff_team_ids[group.staff_role] = group.team_ids
    }
    normalizeIdentityInput(draft)
  } catch (error) { context.addIssue({ code: "custom", message: error instanceof Error ? error.message : "身份或队伍输入无效" }) }
}
const managedStrictInput = z.union([
  ordinarySubmitSchema.extend({ kind: pendingKind, activation_role_keys: activationRoleKeys.optional() }),
  ordinarySubmitSchema.omit({ kind: true }).extend({ kind: pendingKind.optional(), ...managedIdentityFields }).superRefine(refineManagedIdentity),
])
const pendingCreateFacts = pendingIntakePayloadSchema.omit({ material_version: true, kind: true }).extend({ guardians: z.array(guardian.partial({ email: true, is_primary: true })), team_allocation_status: z.enum(["pending", "not_required"]) })
const managedPendingInput = z.union([
  pendingCreateFacts.extend({ kind: pendingKind, activation_role_keys: activationRoleKeys.optional() }),
  pendingCreateFacts.extend({ kind: pendingKind.optional(), ...managedIdentityFields }).superRefine(refineManagedIdentity),
])
export type ActivationRolePlan = z.infer<typeof activationRolePlanSchema>
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
  kind?: "student" | "player" | "both" | "staff"
  submitter_query?: string
}
const base = "/personnel-intake"
export const roleIntentSchema = z.strictObject({
  id: uuid, person_id: uuid, version: z.number().int().positive(),
  role_keys: z.array(z.enum(identityKeys).refine(key => key !== "finance")).min(1),
  role_names: z.array(z.string()), status: z.enum(["pending", "approved"]),
  account_activated: z.boolean(), can_approve: z.boolean(),
}).refine(value => value.role_keys.length === value.role_names.length && new Set(value.role_keys).size === value.role_keys.length)
const roleIntentPageSchema = z.strictObject({
  items: z.array(roleIntentSchema.safeExtend({ full_name: z.string() })),
  page: z.number().int().positive(), page_size: z.number().int().positive(), total: z.number().int().nonnegative(),
})
async function get<T>(path: string, schema: z.ZodType<T>, signal: AbortSignal): Promise<T> {
  return schema.parse(await apiFetch<unknown>(`${base}${path}`, { signal, cache: "no-store" }))
}
async function post<T>(path: string, body: unknown, schema: z.ZodType<T>, signal: AbortSignal): Promise<T> {
  return schema.parse(await apiFetch<unknown>(`${base}${path}`, {
    method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  }))
}
export const assistedPeopleApi = {
  roleIntent: async (personId: string, signal: AbortSignal) => {
    const result = await get(`/people/${encodeURIComponent(personId)}/role-intent`, roleIntentSchema.nullable(), signal)
    signal.throwIfAborted()
    if (result && result.person_id !== personId) throw new Error("role_intent_person_mismatch")
    return result
  },
  roleIntents: (page: number, signal: AbortSignal) => get(`/role-intents?page=${page}&page_size=20`, roleIntentPageSchema, signal),
  approveRoleIntent: async (personId: string, body: { expected_version: number; idempotency_key: string }, signal: AbortSignal) => {
    const result = await post(`/people/${encodeURIComponent(personId)}/role-intent/approve`, body, roleIntentSchema, signal)
    signal.throwIfAborted()
    if (result.person_id !== personId || result.status !== "approved") throw new Error("role_intent_approval_mismatch")
    return result
  },
  managementIdentityContext: (signal: AbortSignal) => get("/management-identity-context", managementIdentityContextSchema, signal),
  activationRoleOptions: (signal: AbortSignal) => get("/activation-role-options", activationRoleOptionsSchema, signal),
  activationRoles: async (personId: string, signal: AbortSignal) => {
    const result = await get(`/people/${encodeURIComponent(personId)}/activation-roles`, activationRolePlanSchema, signal)
    signal.throwIfAborted()
    if (result.person_id !== personId) throw new Error("activation_role_person_mismatch")
    return result
  },
  saveActivationRoles: async (personId: string, input: ActivationRoleUpdateInput, signal: AbortSignal) => {
    const body = z.strictObject({ role_keys: activationRoleKeys, expected_version: z.number().int().nonnegative(), idempotency_key: uuid, confirm_end_staff_positions: z.boolean().optional() }).parse(input)
    const result = activationRolePlanSchema.parse(await apiFetch<unknown>(`${base}/people/${encodeURIComponent(personId)}/activation-roles`, {
      method: "PUT", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    }))
    signal.throwIfAborted()
    if (result.person_id !== personId) throw new Error("activation_role_person_mismatch")
    return result
  },
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
  submit: async (input: IntakeInput, signal: AbortSignal) => post("/requests", ordinarySubmitSchema.parse(input), intakeDetailSchema, signal),
  resubmit: async (id: string, version: number, input: IntakeInput, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/resubmit`, { ...ordinarySubmitSchema.parse(input), expected_version: version }, intakeDetailSchema, signal),
  withdraw: (id: string, version: number, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/withdraw`, { expected_version: version }, intakeDetailSchema, signal),
  approve: (id: string, version: number, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/approve`, { expected_version: version }, approvalSchema, signal),
  returnRequest: (id: string, version: number, reason: string, signal: AbortSignal) => post(`/requests/${encodeURIComponent(id)}/return`, { expected_version: version, reason: reason.trim() }, intakeDetailSchema, signal),
  directCreate: async (input: DirectCreateInput, key: string, signal: AbortSignal) => post("/direct-create", { ...managedStrictInput.parse(input), idempotency_key: uuid.parse(key) }, approvalSchema, signal),
  directCreatePending: async (input: DirectCreatePendingInput, key: string, signal: AbortSignal) => post("/direct-create-pending", { ...managedPendingInput.parse(input), idempotency_key: uuid.parse(key) }, approvalSchema, signal),
}

export function isPrivateDenial(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
}
export function assistedErrorMessage(error: unknown): string {
  if (error instanceof ProblemDetailsError && error.problem.status === 409) return "资料版本或状态已变化，当前草稿已保留。请重新查看最新详情后再操作；也可能存在重复档案，请勿重复建档。"
  if (isPrivateDenial(error)) return "当前无权访问此建档资料，请刷新权限或联系管理员。"
  return "操作未成功，请检查资料或稍后重试。"
}
