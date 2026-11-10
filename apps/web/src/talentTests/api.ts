import { useQuery } from "@tanstack/react-query"
import { useEffect } from "react"
import { apiFetch, ProblemDetailsError } from "../api/client"
import { parseAssessmentRanking, type AssessmentRanking } from "../assessmentRankings/contracts"
import { parseItemRankingView, type ItemRankingView } from "../assessmentRankings/itemContracts"
import type {
  ContextView,
  Page,
  PersonDisplay,
  ReportSummary,
  ReportView,
  ResultSaveView,
  SessionSummary,
  SessionView,
  Submission,
} from "./contracts"

const ROOT = "/talent-tests"

function jsonBody(value: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(value),
  }
}

export function getTalentContext(): Promise<ContextView> {
  return apiFetch<ContextView>(`${ROOT}/context`)
}

export function createTalentSession(
  request_id: string,
  catalog_version: string,
): Promise<SessionView> {
  return apiFetch<SessionView>(
    `${ROOT}/sessions`,
    jsonBody({ request_id, catalog_version }),
  )
}

export function getTalentSession(sessionId: string, signal?: AbortSignal): Promise<SessionView> {
  return apiFetch<SessionView>(`${ROOT}/sessions/${sessionId}`, { signal, cache: "no-store" })
}

function pageQuery(page: number, pageSize: number): URLSearchParams {
  return new URLSearchParams({ page: String(page), page_size: String(pageSize) })
}

export function listTalentSessions(
  page = 1,
  pageSize = 20,
): Promise<Page<SessionSummary>> {
  return apiFetch<Page<SessionSummary>>(`${ROOT}/sessions?${pageQuery(page, pageSize)}`)
}

export function listTalentSubjects(
  page = 1,
  pageSize = 20,
  name?: string,
): Promise<Page<PersonDisplay>> {
  const query = pageQuery(page, pageSize)
  if (name?.trim()) query.set("name", name.trim())
  return apiFetch<Page<PersonDisplay>>(`${ROOT}/subjects?${query}`)
}

export function listTalentReports(
  personId?: string,
  page = 1,
  pageSize = 20,
): Promise<Page<ReportSummary>> {
  const query = pageQuery(page, pageSize)
  if (personId) query.set("person_id", personId)
  return apiFetch<Page<ReportSummary>>(`${ROOT}/reports?${query}`)
}

export function getTalentReport(reportId: string, signal?: AbortSignal): Promise<ReportView> {
  return apiFetch<ReportView>(`${ROOT}/reports/${reportId}`, { signal })
}

export async function getTalentItemRanking(resultId: string, signal?: AbortSignal): Promise<ItemRankingView> {
  const raw = await apiFetch<unknown>(`${ROOT}/results/${encodeURIComponent(resultId)}/ranking`, { signal, cache: "no-store" })
  return parseItemRankingView(raw)
}

export function useTalentItemRanking(accountId: string, personId: string, resultId: string | undefined) {
  const ranking = useQuery({
    queryKey: ["talent-tests", accountId, "item-ranking", personId, resultId],
    queryFn: ({ signal }) => getTalentItemRanking(resultId!, signal),
    enabled: query => Boolean(resultId) && !(query.state.error instanceof ProblemDetailsError && [401, 403, 404].includes(query.state.error.problem.status)),
    retry: false, gcTime: 0, refetchOnMount: "always",
    refetchOnWindowFocus: false, refetchOnReconnect: false,
  })
  const { refetch, error } = ranking
  useEffect(() => {
    if (!resultId || (error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status))) return
    const refresh = () => { if (document.visibilityState === "visible") void refetch() }
    window.addEventListener("focus", refresh)
    document.addEventListener("visibilitychange", refresh)
    return () => {
      window.removeEventListener("focus", refresh)
      document.removeEventListener("visibilitychange", refresh)
    }
  }, [error, refetch, resultId])
  return ranking
}

export async function getTalentReportRanking(reportId: string, signal: AbortSignal): Promise<AssessmentRanking> {
  const raw = await apiFetch<unknown>(`${ROOT}/reports/${reportId}/ranking`, { signal, cache: "no-store" })
  return parseAssessmentRanking(raw, reportId)
}

export function submitTalentResult(
  sessionId: string,
  value: Submission,
): Promise<ResultSaveView> {
  return apiFetch<ResultSaveView>(
    `${ROOT}/sessions/${sessionId}/results`,
    jsonBody(value),
  )
}

export function abandonTalentSession(
  sessionId: string,
  request_id: string,
): Promise<SessionView> {
  return apiFetch<SessionView>(
    `${ROOT}/sessions/${sessionId}/abandon`,
    jsonBody({ request_id }),
  )
}
