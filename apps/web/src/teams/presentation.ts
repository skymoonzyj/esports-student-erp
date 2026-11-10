import type { TeamMemberView, TeamStaffRole } from "./api"

export const teamStaffRoleLabels: Record<TeamStaffRole, string> = { manager: "战队经理", coach: "教练", assistant_coach: "助教" }

export const accountStatusLabels = { active: "账号正常", disabled: "账号已停用", deleted: "账号已删除", not_activated: "未激活" }
export const staffAccountStatusLabels = { active: "已激活", disabled: "账号已停用", deleted: "账号已删除", not_activated: "未激活" }
export const staffAuthorizationStatusLabels = { valid: "授权已确认", reconfirmation_required: "授权待重新确认", pending_role_approval: "账号角色待超管批准" }
export const membershipEndLabels: Record<NonNullable<TeamMemberView["end_kind"]>, string> = {
  left: "离队", transferred: "调队离开", account_deleted: "账号删除",
}
