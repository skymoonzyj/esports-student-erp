import { apiFetch } from "../api/client"
import { parseAssessmentRanking, type AssessmentRanking } from "../assessmentRankings/contracts"
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

export function getTalentSession(sessionId: string): Promise<SessionView> {
  return apiFetch<SessionView>(`${ROOT}/sessions/${sessionId}`)
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

export function getTalentReport(reportId: string): Promise<ReportView> {
  return apiFetch<ReportView>(`${ROOT}/reports/${reportId}`)
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
