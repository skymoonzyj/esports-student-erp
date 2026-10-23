import type { TeamMemberView } from "./api"

export const accountStatusLabels = { active: "账号正常", disabled: "账号已停用", deleted: "账号已删除", not_activated: "未激活" }
export const membershipEndLabels: Record<NonNullable<TeamMemberView["end_kind"]>, string> = {
  left: "离队", transferred: "调队离开", account_deleted: "账号删除",
}
