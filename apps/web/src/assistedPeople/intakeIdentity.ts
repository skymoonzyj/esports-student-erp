import { identityKeys } from "./types"
import type { IdentityKey, ManagedIdentityInput, PendingPersonKind, StaffTeamSelection, TeamStaffRole } from "./types"

export interface IntakeIdentityDraft {
  primary_identity: IdentityKey | null
  concurrent_identities: IdentityKey[]
  primary_staff_identity: IdentityKey | null
  player_team_id: string | null
  staff_team_ids: Record<TeamStaffRole, string[]>
}
export interface LegacySavedDraft {
  draft?: { kind: PendingPersonKind; team_id: string | null }
  roleKeys?: string[]
  attempt?: { fingerprint: string; key: string }
}
export interface RemovedAssignment { kind: "player_team" | "staff_team" | "coach"; id: string; staff_role: TeamStaffRole | null }
export const staffIdentities: readonly IdentityKey[] = ["team_manager", "coach", "assistant_coach", "administration", "customer_service", "life_teacher", "property", "finance", "staff"]
export const staffRoleIdentity: Record<TeamStaffRole, IdentityKey> = { manager: "team_manager", coach: "coach", assistant_coach: "assistant_coach" }
export const identityNames: Record<IdentityKey, string> = { student: "学员", team_member: "队员", team_manager: "队伍经理", coach: "教练", assistant_coach: "助教", administration: "行政", customer_service: "客服", life_teacher: "生活老师", property: "物业", finance: "财务", guardian: "监护人", staff: "其他员工", registered_user: "其他人员（基础账号）" }
export function emptyIdentityDraft(): IntakeIdentityDraft {
  return { primary_identity: null, concurrent_identities: [], primary_staff_identity: null, player_team_id: null, staff_team_ids: { manager: [], coach: [], assistant_coach: [] } }
}
export function selectedIdentities(value: Pick<IntakeIdentityDraft, "primary_identity" | "concurrent_identities">): IdentityKey[] {
  return value.primary_identity ? [value.primary_identity, ...value.concurrent_identities] : [...value.concurrent_identities]
}
/** Presentation-only kind; the server independently resolves and authorizes the intent. */
export function identityKind(value: Pick<IntakeIdentityDraft, "primary_identity" | "concurrent_identities">): PendingPersonKind {
  const selected = selectedIdentities(value)
  return selected.includes("student") ? selected.includes("team_member") ? "both" : "student" : selected.includes("team_member") ? "player" : "staff"
}
export function extraIdentityKeys(value: Pick<IntakeIdentityDraft, "primary_identity" | "concurrent_identities">): IdentityKey[] {
  const kind = identityKind(value)
  const baseline: IdentityKey[] = kind === "both" ? ["student", "team_member"] : kind === "student" ? ["student"] : kind === "player" ? ["team_member"] : ["registered_user"]
  return selectedIdentities(value).filter(key => !baseline.includes(key))
}
export function normalizeIdentityInput(value: IntakeIdentityDraft): { identity_selection: ManagedIdentityInput; staff_teams: StaffTeamSelection[]; team_id: string | null } {
  if (!value.primary_identity) throw new Error("请选择主要身份")
  const selected = selectedIdentities(value)
  if (selected.some(key => !identityKeys.includes(key)) || new Set(selected).size !== selected.length) throw new Error("身份不存在或重复")
  const staff = selected.filter(key => staffIdentities.includes(key))
  if (value.primary_staff_identity && !staff.includes(value.primary_staff_identity)) throw new Error("主要工作人员岗位不属于已选身份")
  if (staffIdentities.includes(value.primary_identity) && value.primary_staff_identity && value.primary_staff_identity !== value.primary_identity) throw new Error("主要工作人员分类必须与主要身份一致")
  if (!staffIdentities.includes(value.primary_identity) && staff.length > 1 && !value.primary_staff_identity) throw new Error("请选择主要工作人员岗位")
  if (value.player_team_id && !selected.includes("team_member")) throw new Error("请确认移除队员所属队伍")
  const groups: StaffTeamSelection[] = []
  for (const staff_role of (Object.keys(staffRoleIdentity) as TeamStaffRole[]).sort()) {
    const ids = value.staff_team_ids[staff_role]
    if (new Set(ids).size !== ids.length) throw new Error("同一岗位的队伍不能重复")
    if (ids.length && !selected.includes(staffRoleIdentity[staff_role])) throw new Error("请确认移除隐藏岗位的队伍归属")
    if (ids.length) groups.push({ staff_role, team_ids: [...ids].sort() })
  }
  return { identity_selection: { primary_identity: value.primary_identity, concurrent_identities: [...value.concurrent_identities].sort(), primary_staff_identity: value.primary_staff_identity }, staff_teams: groups, team_id: value.player_team_id }
}
export function migrateLegacyIdentityDraft(saved: LegacySavedDraft): { value: IntakeIdentityDraft; needsPrimaryConfirmation: boolean } {
  const kind = saved.draft?.kind
  const implicit: IdentityKey[] = kind === "both" ? ["student", "team_member"] : kind === "student" ? ["student"] : kind === "player" ? ["team_member"] : kind === "staff" ? ["registered_user"] : []
  const roles = saved.roleKeys ?? []
  const unknown = roles.filter(key => !identityKeys.includes(key as IdentityKey))
  if (unknown.length) throw new Error(`旧草稿含未识别身份：${unknown.join("、")}；请明确清除草稿后重新建档`)
  return { value: { ...emptyIdentityDraft(), concurrent_identities: [...new Set([...implicit, ...roles as IdentityKey[]])], player_team_id: saved.draft?.team_id ?? null }, needsPrimaryConfirmation: Boolean(saved.draft || roles.length) }
}
export function previewIdentityChange(current: IntakeIdentityDraft, nextSelection: Omit<ManagedIdentityInput, "primary_identity"> & { primary_identity: IdentityKey | null }, coachPersonIds: string[]): { next: IntakeIdentityDraft; nextCoachPersonIds: string[]; removedAssignments: RemovedAssignment[] } {
  const selected = selectedIdentities(nextSelection)
  const removedAssignments: RemovedAssignment[] = []
  const next = { ...current, ...nextSelection, concurrent_identities: [...nextSelection.concurrent_identities], staff_team_ids: { ...current.staff_team_ids } }
  if (!selected.includes("team_member") && current.player_team_id) { removedAssignments.push({ kind: "player_team", id: current.player_team_id, staff_role: null }); next.player_team_id = null }
  for (const role of Object.keys(staffRoleIdentity) as TeamStaffRole[]) {
    if (!selected.includes(staffRoleIdentity[role])) {
      removedAssignments.push(...current.staff_team_ids[role].map(id => ({ kind: "staff_team" as const, id, staff_role: role })))
      next.staff_team_ids[role] = []
    }
  }
  if (!selected.includes("student")) removedAssignments.push(...coachPersonIds.map(id => ({ kind: "coach" as const, id, staff_role: null })))
  return { next, nextCoachPersonIds: selected.includes("student") ? [...coachPersonIds] : [], removedAssignments }
}
export function identityFingerprint(value: IntakeIdentityDraft): string { return JSON.stringify(normalizeIdentityInput(value)) }
/** Checkbox order is not a material change to an uncertain retry. */
export function intakePayloadFingerprint(value: { coach_person_ids: string[]; [key: string]: unknown }): string {
  return canonical({ ...value, coach_person_ids: [...value.coach_person_ids].sort() })
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`
  if (value && typeof value === "object") return `{${Object.entries(value).filter(([, item]) => item !== undefined).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`
  return JSON.stringify(value)
}
