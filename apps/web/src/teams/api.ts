import { apiFetch } from "../api/client"
import { z } from "zod"

export type TeamAction = "read" | "manage" | "transfer" | "assign_managers"
export type TeamPage<T> = { items: T[]; page: number; page_size: number; total: number }
export type TeamCandidatePage = { items: TeamCandidate[]; next_cursor: string | null }
export type ManagerCandidate = Omit<TeamCandidate, "account_id"> & { account_id: string }
export type ManagerCandidatePage = { items: ManagerCandidate[]; next_cursor: string | null }
export type TeamContext = {
  can_open_management: boolean
  can_read_self: boolean
  can_manage_catalog: boolean
  can_assign_managers: boolean
  can_transfer: boolean
}
export type TeamSummary = {
  id: string; project_id: string; project_name: string; name: string
  is_active: boolean; member_count: number; allowed_actions: TeamAction[]
}
export type TeamMemberView = {
  id: string; person_id: string; full_name: string; nickname: string | null
  age: number | null; is_minor: boolean | null; role: "team_member"; project_id: string
  project_name: string; team_id: string; team_name: string
  status: "current" | "left"; joined_on: string; left_on: string | null
  end_kind: "left" | "transferred" | "account_deleted" | null
  account_status: "active" | "disabled" | "deleted" | "not_activated"; transfer_reason: string | null
}
export type TeamAssignmentView = {
  id: string; account_id: string; full_name: string; nickname: string | null
  account_status: "active" | "disabled" | "deleted"
  started_on: string; ended_on: string | null
}
export type TeamStaffRole = "manager" | "coach" | "assistant_coach"
export type TeamStaffAssignmentView = TeamAssignmentView & { staff_role: TeamStaffRole }
export type AssignStaffInput = AssignManagerInput & { staff_role: TeamStaffRole }
export type StaffCandidateOptions = CandidateOptions & { staffRole: TeamStaffRole }
export type StaffAssignmentOptions = PageOptions & { status?: "current" | "ended" }
const staffRoleSchema = z.enum(["manager", "coach", "assistant_coach"])
const uuid = z.uuid()
const personStaffRowSchema = z.strictObject({
  row_key: z.string(), source_kind: z.enum(["person", "account"]),
  person_position_id: uuid.nullable(), account_assignment_id: uuid.nullable(), person_id: uuid, account_id: uuid.nullable(),
  full_name: z.string(), nickname: z.string().nullable(), staff_role: staffRoleSchema,
  account_status: z.enum(["not_activated", "active", "disabled", "deleted"]),
  authorization_status: z.enum(["valid", "reconfirmation_required", "pending_role_approval"]),
  started_on: z.iso.date(), ended_on: z.iso.date().nullable(), version: z.number().int().min(1).nullable(),
  allowed_actions: z.array(z.enum(["end", "reconfirm"])),
}).refine(row => {
  if (new Set(row.allowed_actions).size !== row.allowed_actions.length) return false
  if (row.ended_on && row.allowed_actions.length) return false
  if (row.source_kind === "person") return row.person_position_id !== null && row.version !== null && row.row_key === `person:${row.person_position_id}`
  return row.person_position_id === null && row.version === null && row.account_assignment_id !== null && row.account_id !== null &&
    row.row_key === `account:${row.account_assignment_id}` && !row.allowed_actions.includes("reconfirm")
}, "invalid_person_staff_source")
export type PersonStaffRow = z.infer<typeof personStaffRowSchema>
const personStaffCandidateSchema = z.strictObject({ person_id: uuid, account_id: z.null(), full_name: z.string(), nickname: z.string().nullable() })
export type PersonStaffCandidate = z.infer<typeof personStaffCandidateSchema>
const personStaffAssignSchema = z.strictObject({ person_id: uuid, staff_role: staffRoleSchema, idempotency_key: uuid })
export type PersonStaffAssignInput = z.infer<typeof personStaffAssignSchema>
const personStaffMaintenanceSchema = z.strictObject({ expected_version: z.number().int().min(1), reason: z.string().trim().min(1).max(200), idempotency_key: uuid })
export type PersonStaffMaintenanceInput = z.infer<typeof personStaffMaintenanceSchema>
const staffMutationResultSchema = z.strictObject({ operation_id: uuid, membership_id: z.null(), closed_membership_id: z.null(), assignment_id: uuid })
export type TeamCandidate = {
  account_id: string | null; person_id: string; full_name: string
  nickname: string | null; age: number | null; is_minor: boolean | null
}
export type TeamAffiliation = {
  membership_id: string; project_id: string; project_name: string
  team_id: string; team_name: string; joined_on: string
}
export type PersonTeamProjection = { visibility: "allowed" | "denied"; current: TeamAffiliation | null }
export type MyTeamResponse = { current: TeamAffiliation | null; history: TeamPage<TeamMemberView> }
export type TeamMutationResult = {
  operation_id: string; membership_id: string | null
  closed_membership_id: string | null; assignment_id: string | null
}
export type JoinTeamInput = { person_id: string; idempotency_key: string }
export type LeaveTeamInput = { idempotency_key: string }
export type TransferTeamInput = { target_team_id: string; reason: string; idempotency_key: string }
export type AssignManagerInput = { account_id: string; idempotency_key: string }
export type UnassignManagerInput = { idempotency_key: string }
export type TeamListOptions = { projectId?: string; page?: number; pageSize?: number }
export type MembershipOptions = { status?: "current" | "left" | "all"; page?: number; pageSize?: number }
export type CandidateOptions = { query: string; cursor?: string | null; pageSize?: number }
export type PageOptions = { page?: number; pageSize?: number }

const base = "/team-management"

function pagedQuery(options: PageOptions): URLSearchParams {
  const query = new URLSearchParams()
  if (options.page !== undefined) query.set("page", String(options.page))
  if (options.pageSize !== undefined) query.set("page_size", String(options.pageSize))
  return query
}

function withQuery(path: string, query: URLSearchParams): string {
  const value = query.toString()
  return value ? `${path}?${value}` : path
}

function post(inputPath: string, body: unknown): Promise<TeamMutationResult> {
  return apiFetch<TeamMutationResult>(`${base}${inputPath}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  })
}

export function getTeamContext(): Promise<TeamContext> { return apiFetch(`${base}/context`) }
export function listTeams(options: TeamListOptions = {}): Promise<TeamPage<TeamSummary>> {
  const query = pagedQuery(options)
  if (options.projectId) query.set("project_id", options.projectId)
  return apiFetch(withQuery(`${base}/teams`, query))
}
export function listMemberships(teamId: string, options: MembershipOptions = {}): Promise<TeamPage<TeamMemberView>> {
  const query = pagedQuery(options)
  if (options.status) query.set("status", options.status)
  return apiFetch(withQuery(`${base}/teams/${teamId}/memberships`, query))
}
export function candidates(teamId: string, options: CandidateOptions): Promise<TeamCandidatePage> {
  const query = new URLSearchParams({ query: options.query })
  if (options.cursor) query.set("cursor", options.cursor)
  if (options.pageSize !== undefined) query.set("page_size", String(options.pageSize))
  return apiFetch(withQuery(`${base}/teams/${teamId}/candidates`, query))
}
export async function managerCandidates(teamId: string, options: CandidateOptions): Promise<ManagerCandidatePage> {
  const query = new URLSearchParams({ query: options.query })
  if (options.cursor) query.set("cursor", options.cursor)
  if (options.pageSize !== undefined) query.set("page_size", String(options.pageSize))
  const result = await apiFetch<TeamCandidatePage>(withQuery(`${base}/teams/${teamId}/manager-candidates`, query))
  // A member can be accountless; assigning managers must never accept that shape.
  const items = result.items.map((item) => ({ ...item, account_id: z.string().min(1).parse(item.account_id) }))
  return { ...result, items }
}
export function myTeam(options: PageOptions = {}): Promise<MyTeamResponse> {
  return apiFetch(withQuery(`${base}/me`, pagedQuery(options)))
}
export function listAssignments(teamId: string, options: PageOptions = {}): Promise<TeamPage<TeamAssignmentView>> {
  return apiFetch(withQuery(`${base}/teams/${teamId}/manager-assignments`, pagedQuery(options)))
}
export async function staffCandidates(teamId: string, options: StaffCandidateOptions): Promise<ManagerCandidatePage> {
  const query = new URLSearchParams({ staff_role: options.staffRole, query: options.query })
  if (options.cursor) query.set("cursor", options.cursor)
  query.set("page_size", String(Math.min(options.pageSize ?? 20, 20)))
  const result = await apiFetch<TeamCandidatePage>(withQuery(`${base}/teams/${teamId}/staff-candidates`, query))
  return { ...result, items: result.items.map((item) => ({ ...item, account_id: z.string().min(1).parse(item.account_id) })) }
}
export function listStaffAssignments(teamId: string, options: StaffAssignmentOptions = {}): Promise<TeamPage<TeamStaffAssignmentView>> {
  const query = pagedQuery(options)
  if (options.status) query.set("status", options.status)
  return apiFetch(withQuery(`${base}/teams/${teamId}/staff-assignments`, query))
}
export async function listStaffPositions(teamId: string, options: StaffAssignmentOptions = {}, signal?: AbortSignal): Promise<TeamPage<PersonStaffRow>> {
  const input = z.strictObject({ status: z.enum(["current", "ended"]).default("current"), page: z.number().int().min(1).default(1), pageSize: z.number().int().min(1).max(100).default(20) }).parse(options)
  const query = new URLSearchParams({ status: input.status, page: String(input.page), page_size: String(input.pageSize) })
  const result = z.strictObject({ items: z.array(personStaffRowSchema), page: z.number().int().min(1), page_size: z.number().int().min(1).max(100), total: z.number().int().nonnegative() }).parse(
    await apiFetch<unknown>(withQuery(`${base}/teams/${uuid.parse(teamId)}/staff-positions`, query), { signal, cache: "no-store" }),
  )
  signal?.throwIfAborted()
  return result
}
export async function personStaffCandidates(teamId: string, options: StaffCandidateOptions, signal?: AbortSignal): Promise<{ items: PersonStaffCandidate[]; next_cursor: string | null }> {
  const input = z.strictObject({ staffRole: staffRoleSchema, query: z.string().trim().min(1).max(120), cursor: z.string().nullable().optional(), pageSize: z.number().int().min(1).max(20).default(20) }).parse(options)
  const query = new URLSearchParams({ staff_role: input.staffRole, query: input.query, page_size: String(input.pageSize) })
  if (input.cursor) query.set("cursor", input.cursor)
  const result = z.strictObject({ items: z.array(personStaffCandidateSchema), next_cursor: z.string().nullable() }).parse(
    await apiFetch<unknown>(withQuery(`${base}/teams/${uuid.parse(teamId)}/person-staff-candidates`, query), { signal, cache: "no-store" }),
  )
  signal?.throwIfAborted()
  return result
}
async function personStaffCommand(path: string, input: PersonStaffAssignInput | PersonStaffMaintenanceInput, signal?: AbortSignal, positionId?: string): Promise<TeamMutationResult> {
  const result = staffMutationResultSchema.parse(await apiFetch<unknown>(`${base}${path}`, {
    method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  }))
  signal?.throwIfAborted()
  if (positionId && result.assignment_id !== positionId) throw new Error("person_staff_response_mismatch")
  return result
}
export async function assignPersonStaff(teamId: string, input: PersonStaffAssignInput, signal?: AbortSignal): Promise<TeamMutationResult> {
  return personStaffCommand(`/teams/${uuid.parse(teamId)}/person-staff-positions`, personStaffAssignSchema.parse(input), signal)
}
export async function endPersonStaff(positionId: string, input: PersonStaffMaintenanceInput, signal?: AbortSignal): Promise<TeamMutationResult> {
  return personStaffCommand(`/person-staff-positions/${uuid.parse(positionId)}/end`, personStaffMaintenanceSchema.parse(input), signal, positionId)
}
export async function reconfirmPersonStaff(positionId: string, input: PersonStaffMaintenanceInput, signal?: AbortSignal): Promise<TeamMutationResult> {
  return personStaffCommand(`/person-staff-positions/${uuid.parse(positionId)}/reconfirm`, personStaffMaintenanceSchema.parse(input), signal, positionId)
}
export function assignStaff(teamId: string, input: AssignStaffInput): Promise<TeamMutationResult> { return post(`/teams/${teamId}/staff-assignments`, input) }
export function endStaffAssignment(assignmentId: string, input: UnassignManagerInput): Promise<TeamMutationResult> { return post(`/staff-assignments/${assignmentId}/end`, input) }
export function join(teamId: string, input: JoinTeamInput): Promise<TeamMutationResult> { return post(`/teams/${teamId}/memberships`, input) }
export function leave(membershipId: string, input: LeaveTeamInput): Promise<TeamMutationResult> { return post(`/memberships/${membershipId}/leave`, input) }
export function transfer(membershipId: string, input: TransferTeamInput): Promise<TeamMutationResult> { return post(`/memberships/${membershipId}/transfer`, input) }
export function assign(teamId: string, input: AssignManagerInput): Promise<TeamMutationResult> { return post(`/teams/${teamId}/manager-assignments`, input) }
export function unassign(assignmentId: string, input: UnassignManagerInput): Promise<TeamMutationResult> { return post(`/manager-assignments/${assignmentId}/end`, input) }
