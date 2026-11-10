import type { MembershipOptions, TeamListOptions, TeamStaffRole } from "./api"

export function teamKeys(accountId: string) {
  const root = ["teams", accountId] as const
  return {
    root,
    context: () => [...root, "context"] as const,
    teams: (options: TeamListOptions = {}) => [...root, "list", options] as const,
    members: (teamId: string, options: MembershipOptions = {}) => [...root, "members", teamId, options] as const,
    candidates: (teamId: string, query: string, cursor: string | null) => [...root, "candidates", teamId, query, cursor] as const,
    managerCandidates: (teamId: string, query: string, cursor: string | null) => [...root, "manager-candidates", teamId, query, cursor] as const,
    assignments: (teamId: string, page: number) => [...root, "assignments", teamId, page] as const,
    staffCandidates: (teamId: string, role: TeamStaffRole, query: string) => [...root, "staff-candidates", teamId, role, query] as const,
    staffAssignments: (teamId: string, status: "current" | "ended", page: number) => [...root, "staff-assignments", teamId, status, page] as const,
    staffPositions: (teamId: string, status: "current" | "ended", page: number, pageSize = 20) => [...root, "staff-positions", teamId, status, page, pageSize] as const,
    personStaffCandidates: (teamId: string, role: TeamStaffRole, query: string, pageSize = 20) => [...root, "person-staff-candidates", teamId, role, query, pageSize] as const,
    self: (page: number) => [...root, "self", page] as const,
  }
}
