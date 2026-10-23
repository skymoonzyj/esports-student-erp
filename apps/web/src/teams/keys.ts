import type { MembershipOptions, TeamListOptions } from "./api"

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
    self: (page: number) => [...root, "self", page] as const,
  }
}
