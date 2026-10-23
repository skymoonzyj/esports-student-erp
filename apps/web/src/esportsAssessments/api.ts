import { z } from 'zod'

import { apiFetch } from '../api/client'
import {
  assessmentReportViewSchema,
  attemptCreateSchema,
  attemptViewSchema,
  modeSchema,
  operationRequestSchema,
  practiceCreateSchema,
  resultSaveViewSchema,
  resultSubmitSchema,
  sessionCreateSchema,
  sessionViewSchema,
  suiteIdSchema,
  type AttemptCreate,
  type Mode,
  type PracticeCreate,
  type ResultSaveView,
  type ResultSubmit,
  type SessionCreate,
  type SuiteId,
} from './contracts'
import {
  assessmentContextViewSchema,
  assessmentReportPageSchema,
  attemptDetailViewSchema,
  historyPageSchema,
  recoveryPageSchema,
  suiteCatalogViewSchema,
  type AssessmentContextView,
  type AssessmentReportPage,
  type AttemptDetailView,
  type HistoryPage,
  type RecoveryPage,
  type SuiteCatalogView,
} from './apiContracts'

const ROOT = '/esports-assessments'
const uuid = z.uuid()
const calendarDate = z.iso.date()
const pageLimit = z.number().int().min(1).max(100)
const opaqueCursor = z.string().min(1).max(2048)
type OperationRequest = z.infer<typeof operationRequestSchema>

type CursorQuery = {
  cursor?: string
  limit?: number
}

export type HistoryQuery = CursorQuery & { mode: Mode }
export type ReportsQuery = CursorQuery & {
  person_id?: string
  suite_id?: SuiteId
  date_from?: string
  date_to?: string
}

function jsonBody(value: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value),
  }
}

async function parsed<T extends z.ZodType>(
  path: string,
  schema: T,
  init?: RequestInit,
): Promise<z.infer<T>> {
  const raw = await apiFetch<unknown>(path, init)
  const result = schema.safeParse(raw)
  if (!result.success) throw new Error('服务返回的数据格式无效，请稍后重试。')
  return result.data
}

function sameIdentity(valid: boolean): void {
  if (!valid) throw new Error('服务响应与请求资源不一致，请重新对账。')
}

function encodedPage(query: CursorQuery): URLSearchParams {
  const params = new URLSearchParams()
  if (query.cursor !== undefined) params.set('cursor', opaqueCursor.parse(query.cursor))
  params.set('limit', String(pageLimit.parse(query.limit ?? 20)))
  return params
}

async function context(): Promise<AssessmentContextView> {
  return parsed(`${ROOT}/context`, assessmentContextViewSchema)
}

async function suites(): Promise<SuiteCatalogView> {
  return parsed(`${ROOT}/suites`, suiteCatalogViewSchema)
}

async function createSession(value: SessionCreate) {
  const request = sessionCreateSchema.parse(value)
  const result = await parsed(`${ROOT}/sessions`, sessionViewSchema, jsonBody(request))
  sameIdentity(result.suite_id === request.suite_id)
  return result
}

async function readSession(sessionId: string) {
  const id = uuid.parse(sessionId)
  const result = await parsed(`${ROOT}/sessions/${id}`, sessionViewSchema)
  sameIdentity(result.id === id)
  return result
}

async function startAttempt(sessionId: string, value: AttemptCreate) {
  const id = uuid.parse(sessionId)
  const request = attemptCreateSchema.parse(value)
  const result = await parsed(`${ROOT}/sessions/${id}/attempts`, attemptViewSchema, jsonBody(request))
  sameIdentity(
    result.mode === 'formal'
    && result.session_id === id
    && result.test_id === request.test_id,
  )
  return result
}

async function readAttempt(attemptId: string): Promise<AttemptDetailView> {
  const id = uuid.parse(attemptId)
  const result = await parsed(`${ROOT}/attempts/${id}`, attemptDetailViewSchema)
  sameIdentity(result.attempt.id === id && result.attempt.mode === 'formal')
  return result
}

async function submitResult(attemptId: string, value: ResultSubmit): Promise<ResultSaveView> {
  const id = uuid.parse(attemptId)
  const request = resultSubmitSchema.parse(value)
  const result = await parsed(`${ROOT}/attempts/${id}/result`, resultSaveViewSchema, jsonBody(request))
  // ResultSaveView intentionally has no attempt_id. The host binds this response to
  // its captured account/attempt/operation generation and reconciles ambiguous writes.
  sameIdentity(result.mode === 'formal')
  return result
}

async function interruptAttempt(attemptId: string, value: OperationRequest) {
  const id = uuid.parse(attemptId)
  const request = operationRequestSchema.parse(value)
  const result = await parsed(`${ROOT}/attempts/${id}/interrupt`, attemptViewSchema, jsonBody(request))
  sameIdentity(result.id === id && result.mode === 'formal' && result.state === 'interrupted')
  return result
}

async function abandonSession(sessionId: string, value: OperationRequest) {
  const id = uuid.parse(sessionId)
  const request = operationRequestSchema.parse(value)
  const result = await parsed(`${ROOT}/sessions/${id}/abandon`, sessionViewSchema, jsonBody(request))
  sameIdentity(result.id === id && result.status === 'abandoned')
  return result
}

async function startPractice(value: PracticeCreate) {
  const request = practiceCreateSchema.parse(value)
  const result = await parsed(`${ROOT}/practice-runs`, attemptViewSchema, jsonBody(request))
  sameIdentity(
    result.mode === 'practice'
    && result.session_id === null
    && result.suite_id === request.suite_id
    && result.test_id === request.test_id,
  )
  return result
}

async function readPractice(attemptId: string): Promise<AttemptDetailView> {
  const id = uuid.parse(attemptId)
  const result = await parsed(`${ROOT}/practice-runs/${id}`, attemptDetailViewSchema)
  sameIdentity(result.attempt.id === id && result.attempt.mode === 'practice')
  return result
}

async function submitPractice(attemptId: string, value: ResultSubmit): Promise<ResultSaveView> {
  const id = uuid.parse(attemptId)
  const request = resultSubmitSchema.parse(value)
  const result = await parsed(`${ROOT}/practice-runs/${id}/result`, resultSaveViewSchema, jsonBody(request))
  sameIdentity(result.mode === 'practice')
  return result
}

async function interruptPractice(attemptId: string, value: OperationRequest) {
  const id = uuid.parse(attemptId)
  const request = operationRequestSchema.parse(value)
  const result = await parsed(`${ROOT}/practice-runs/${id}/interrupt`, attemptViewSchema, jsonBody(request))
  sameIdentity(result.id === id && result.mode === 'practice' && result.state === 'interrupted')
  return result
}

async function recovery(query: CursorQuery = {}): Promise<RecoveryPage> {
  return parsed(`${ROOT}/recovery?${encodedPage(query)}`, recoveryPageSchema)
}

async function history(query: HistoryQuery): Promise<HistoryPage> {
  const mode = modeSchema.parse(query.mode)
  const params = encodedPage(query)
  params.set('mode', mode)
  const ordered = new URLSearchParams([['mode', mode]])
  for (const [key, value] of params) if (key !== 'mode') ordered.set(key, value)
  const result = await parsed(`${ROOT}/history?${ordered}`, historyPageSchema)
  sameIdentity(result.items.every((item) => item.mode === mode))
  return result
}

async function reports(query: ReportsQuery = {}): Promise<AssessmentReportPage> {
  const params = new URLSearchParams()
  const personId = query.person_id === undefined ? undefined : uuid.parse(query.person_id)
  const suiteId = query.suite_id === undefined ? undefined : suiteIdSchema.parse(query.suite_id)
  const dateFrom = query.date_from === undefined ? undefined : calendarDate.parse(query.date_from)
  const dateTo = query.date_to === undefined ? undefined : calendarDate.parse(query.date_to)
  if (dateFrom !== undefined && dateTo !== undefined && dateFrom > dateTo) {
    throw new Error('开始日期不能晚于结束日期。')
  }
  if (personId) params.set('person_id', personId)
  if (suiteId) params.set('suite_id', suiteId)
  if (dateFrom) params.set('date_from', dateFrom)
  if (dateTo) params.set('date_to', dateTo)
  if (query.cursor !== undefined) params.set('cursor', opaqueCursor.parse(query.cursor))
  params.set('limit', String(pageLimit.parse(query.limit ?? 20)))
  const result = await parsed(`${ROOT}/reports?${params}`, assessmentReportPageSchema)
  sameIdentity(result.items.every((item) =>
    (personId === undefined || item.person.person_id === personId)
    && (suiteId === undefined || item.suite_id === suiteId)
    && (dateFrom === undefined || shanghaiCalendarDate(item.completed_at) >= dateFrom)
    && (dateTo === undefined || shanghaiCalendarDate(item.completed_at) <= dateTo),
  ))
  return result
}

function shanghaiCalendarDate(value: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date(value))
  const byType = new Map(parts.map((part) => [part.type, part.value]))
  return `${byType.get('year')}-${byType.get('month')}-${byType.get('day')}`
}

async function report(reportId: string) {
  const id = uuid.parse(reportId)
  const result = await parsed(`${ROOT}/reports/${id}`, assessmentReportViewSchema)
  const members = result.suite.test_ids
  const comparisonIds = result.comparisons.map((comparison) => comparison.test_id)
  sameIdentity(
    result.id === id
    && result.suite.suite_id === result.suite_id
    && result.results.every((item, index) =>
      item.test_id === members[index]
      && item.config.suite_id === result.suite_id
      && item.config.test_id === item.test_id
      && item.config.catalog_version === result.suite.catalog_version
      && item.config.difficulty === 3,
    )
    && new Set(comparisonIds).size === comparisonIds.length
    && comparisonIds.every((testId) => members.includes(testId)),
  )
  return result
}

export const assessmentApi = {
  context,
  suites,
  createSession,
  readSession,
  startAttempt,
  readAttempt,
  submitResult,
  interruptAttempt,
  abandonSession,
  startPractice,
  readPractice,
  submitPractice,
  interruptPractice,
  recovery,
  history,
  reports,
  report,
}
