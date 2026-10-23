import type { RevenueShareRole } from "./api"

export const revenueShareRoleLabels: Record<RevenueShareRole, string> = {
  inviter: "邀请人 / 邀请机构",
  subject_coach: "对应学科教练",
  platform_developer: "平台开发者",
  assistant: "助教",
  additional: "额外分成人员",
}

export function formatCny(cents: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 2,
  }).format(cents / 100)
}

export function todayInChina(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date())
}

export function displayDate(value: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value)
  return match ? `${match[1]}-${match[2]}-${match[3]}` : value
}
