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
export function join(teamId: string, input: JoinTeamInput): Promise<TeamMutationResult> { return post(`/teams/${teamId}/memberships`, input) }
export function leave(membershipId: string, input: LeaveTeamInput): Promise<TeamMutationResult> { return post(`/memberships/${membershipId}/leave`, input) }
export function transfer(membershipId: string, input: TransferTeamInput): Promise<TeamMutationResult> { return post(`/memberships/${membershipId}/transfer`, input) }
export function assign(teamId: string, input: AssignManagerInput): Promise<TeamMutationResult> { return post(`/teams/${teamId}/manager-assignments`, input) }
export function unassign(assignmentId: string, input: UnassignManagerInput): Promise<TeamMutationResult> { return post(`/manager-assignments/${assignmentId}/end`, input) }
