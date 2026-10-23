import type { PersonType, StaffType } from "./api"
import type { PersonTeamProjection } from "../teams/api"

export const personTypeLabels: Record<PersonType, string> = {
  student: "学员",
  guardian: "监护人",
  staff: "工作人员",
  visitor: "访客",
}

export const staffTypeLabels: Record<StaffType, string> = {
  coach: "教练",
  life_teacher: "生活老师",
  customer_service: "客服",
  administration: "行政",
  other: "其他工作人员",
}

export function personTypeText(
  types: PersonType[],
  staffType: StaffType | null | undefined,
  roles: Array<{ key: string; name: string }> = [],
): string {
  const labels = types.map((type) => personTypeLabels[type])
  if (staffType && types.includes("staff")) {
    labels.push(staffTypeLabels[staffType])
  }
  for (const role of roles) {
    if (role.key === "team_member") labels.push("队员")
    if (role.key === "team_manager") labels.push("队伍管理")
  }
  return labels.length ? labels.join(" · ") : "普通人员"
}

export function teamProjectionText(team: PersonTeamProjection | undefined): string {
  if (!team || team.visibility === "denied") return "无查看权限"
  return team.current ? `${team.current.project_name} · ${team.current.team_name}` : "未分配队伍"
}

type DateParts = { year: number; month: number; day: number }

function chinaDateParts(value: Date): DateParts {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(value)
  const get = (type: "year" | "month" | "day") =>
    Number(parts.find((part) => part.type === type)?.value ?? 0)
  return { year: get("year"), month: get("month"), day: get("day") }
}

function birthDateParts(value: string): DateParts | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
  if (!match) return null
  return {
    year: Number(match[1]),
    month: Number(match[2]),
    day: Number(match[3]),
  }
}

function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0)
}

/** Calculate age using the same China-time business date as the backend. */
export function ageOn(dateOfBirth: string, referenceDate: Date): number {
  const birth = birthDateParts(dateOfBirth)
  if (!birth) return 0
  const today = chinaDateParts(referenceDate)
  let age = today.year - birth.year
  const birthdayDay =
    birth.month === 2 && birth.day === 29 && !isLeapYear(today.year) ? 28 : birth.day
  if (
    today.month < birth.month ||
    (today.month === birth.month && today.day < birthdayDay)
  ) {
    age -= 1
  }
  return Math.max(0, age)
}

export function currentAge(dateOfBirth: string): number {
  return ageOn(dateOfBirth, new Date())
}
