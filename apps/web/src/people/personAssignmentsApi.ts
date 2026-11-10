import { z } from "zod"
import { apiFetch } from "../api/client"

const projectSchema = z.object({ id: z.string(), name: z.string() })
const coachSchema = z.object({ id: z.string(), full_name: z.string(), project_ids: z.array(z.string()) })
const businessKindSchema = z.enum(["student", "player", "both"]).nullable()
export const personAssignmentsSchema = z.object({
  person_id: z.string(), version: z.string().min(1), business_kind: businessKindSchema,
  source_intake_kind: z.enum(["student", "player", "both", "staff"]).nullable(),
  student_project_ids: z.array(z.string()), coach_person_ids: z.array(z.string()),
  capabilities: z.object({ can_edit: z.boolean(), can_manage_account_roles: z.boolean(), can_remove_identity: z.boolean() }),
  eligible_projects: z.array(projectSchema), eligible_coaches: z.array(coachSchema),
  current_projects: z.array(projectSchema).optional(), current_coaches: z.array(coachSchema).optional(),
  account_id: z.string().nullable(), account_status: z.string(),
})
export type PersonAssignmentsView = z.infer<typeof personAssignmentsSchema>
export type PersonAssignmentsUpdate = {
  expected_version: string; idempotency_key: string; reason: string;
  business_kind: PersonAssignmentsView["business_kind"]; student_project_ids: string[]; coach_person_ids: string[]
}
export const personAssignmentsKey = (actorId: string, personId: string, scope: string) => ["person-assignments", actorId, personId, scope] as const

async function readResult(personId: string, response: Promise<unknown>, signal: AbortSignal) {
  const result = personAssignmentsSchema.parse(await response)
  signal.throwIfAborted()
  if (result.person_id !== personId) throw new Error("人员归属响应不匹配，请刷新后重试。")
  return result
}
export const personAssignmentsApi = {
  get: (personId: string, signal: AbortSignal): Promise<PersonAssignmentsView> => readResult(personId,
    apiFetch(`/people/${encodeURIComponent(personId)}/assignments`, { signal, cache: "no-store" }), signal),
  put: (personId: string, input: PersonAssignmentsUpdate, signal: AbortSignal): Promise<PersonAssignmentsView> => readResult(personId,
    apiFetch(`/people/${encodeURIComponent(personId)}/assignments`, { method: "PUT", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input) }), signal),
}
