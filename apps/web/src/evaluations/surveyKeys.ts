export type SurveyListFilters = { cohortId?: string | null; projectId?: string | null; staffPersonId?: string | null; roleId?: string | null; roleKey?: string | null; identity?: "hidden" | "real_name"; page?: number; pageSize?: number }
export function normalizeSurveyFilters(input: SurveyListFilters = {}) {
  return { cohortId: input.cohortId ?? null, projectId: input.projectId ?? null, staffPersonId: input.staffPersonId ?? null, roleId: input.roleId ?? null, roleKey: input.roleKey ?? null, identity: input.identity ?? "hidden", page: input.page ?? 1, pageSize: input.pageSize ?? 50 }
}
export const surveyKeys = {
  root: (accountId: string) => ["camp-surveys", accountId] as const,
  food: (accountId: string, cycleId: string, studentPersonId: string) => [...surveyKeys.root(accountId), "food", cycleId, studentPersonId] as const,
  contextRoot: (accountId: string) => [...surveyKeys.root(accountId), "context"] as const,
  context: (accountId: string, page = 1, pageSize = 20) => [...surveyKeys.contextRoot(accountId), { page, pageSize }] as const,
  feedback: (accountId: string, page = 1, pageSize = 50, filters: SurveyListFilters = {}) => [...surveyKeys.root(accountId), "feedback", { ...normalizeSurveyFilters({ ...filters, page, pageSize }) }] as const,
  admin: (accountId: string, filters: SurveyListFilters = {}) => [...surveyKeys.root(accountId), "admin", normalizeSurveyFilters(filters)] as const,
  print: (accountId: string, filters: SurveyListFilters = {}) => [...surveyKeys.root(accountId), "print", normalizeSurveyFilters(filters)] as const,
}
