import type { QueryClient } from "@tanstack/react-query"
import { ProblemDetailsError } from "../api/client"
import { teamKeys } from "./keys"

export function isDeniedResource(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [403, 404].includes(error.problem.status)
}

export function clearDeniedTeamResource(client: QueryClient, accountId: string, teamId: string): void {
  const keys = teamKeys(accountId)
  for (const query of client.getQueryCache().findAll({ queryKey: keys.root })) {
    if (query.queryKey[3] !== teamId) continue
    void client.cancelQueries({ queryKey: query.queryKey, exact: true })
    client.removeQueries({ queryKey: query.queryKey, exact: true })
  }
  void client.invalidateQueries({ queryKey: keys.context() })
  void client.invalidateQueries({ queryKey: [...keys.root, "all-authorized"] })
  void client.invalidateQueries({ queryKey: [...keys.root, "transfer-targets"] })
}
