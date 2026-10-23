import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ProblemDetailsError } from '../api/client'
import { useAuth } from '../auth/AuthProvider'
import { assessmentApi } from './api'
import type { AttemptDetailView } from './apiContracts'
import { pendingSubmissionSchema, type AttemptView, type ControlSettings, type EnvironmentSnapshot, type Mode, type PendingSubmission, type QualityInput, type RawResult, type ResultSaveView, type SuiteId, type TestId } from './contracts'
import { acceptResponse, acceptsSave, matchesAttempt, remainingLeaseMs } from './hostState'
import { assessmentKeys } from './keys'
import { loadPending, removePending, savePending } from './pending'

export interface SessionIdentity { mode: Mode; suite_id: SuiteId; session_id?: string; practice_id?: string; test_id?: TestId }
export interface StartSettings { test_id: TestId; settings: ControlSettings; environment: EnvironmentSnapshot; difficulty: number }
interface SessionState {
  serverView: AttemptView | null; saved: ResultSaveView | null; pending: PendingSubmission | null
  ready: boolean; busy: boolean; error: string | null; revoked: boolean; volatile: boolean; leaseMs: number
}
const empty = (): SessionState => ({ serverView: null, saved: null, pending: null, ready: false, busy: false, error: null, revoked: false, volatile: false, leaseMs: 0 })

/** The only result writer. Every continuation is bound to account + route + request generation. */
export function useAssessmentSession(options: SessionIdentity) {
  const { user } = useAuth()
  const account = user?.account_id ?? ''
  const identity = JSON.stringify([account, options.mode, options.suite_id, options.session_id, options.practice_id, options.test_id])
  const current = useRef({ identity, account, generation: 0, mounted: true })
  const live = useRef<SessionState>(empty())
  const createRequest = useRef<(StartSettings & { operation_key: string }) | null>(null)
  const interruptKeys = useRef(new Map<string, string>())
  const starting = useRef<{ identity: string; settled: Promise<void> } | null>(null)
  const [rendered, setRendered] = useState({ identity, value: live.current })
  if (current.current.identity !== identity) {
    current.current = { identity, account, generation: current.current.generation + 1, mounted: true }
    live.current = empty()
    createRequest.current = null
    interruptKeys.current.clear()
  }
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const capture = () => ({ ...current.current })
  type Ticket = ReturnType<typeof capture>
  const valid = (ticket: Ticket) => current.current.mounted && ticket.identity === current.current.identity
    && acceptResponse(ticket.generation, current.current.generation, ticket.account, current.current.account)
  const update = (ticket: Ticket, patch: Partial<SessionState>) => {
    if (!valid(ticket)) return
    live.current = { ...live.current, ...patch }
    setRendered({ identity: ticket.identity, value: live.current })
  }
  const belongs = (attempt: AttemptView) => attempt.mode === options.mode && attempt.suite_id === options.suite_id
    && (options.mode === 'formal' ? attempt.session_id === options.session_id : attempt.session_id === null)
    && (!options.practice_id || attempt.id === options.practice_id) && (!options.test_id || attempt.test_id === options.test_id)
  const failed = (ticket: Ticket, error: unknown) => {
    if (!valid(ticket)) return
    if (error instanceof ProblemDetailsError && [401, 403].includes(error.problem.status)) {
      if (live.current.pending) removePending(live.current.pending)
      update(ticket, { ...empty(), ready: true, revoked: true, error: '当前登录或评测权限已失效，请返回入口重新确认。' })
      current.current.generation += 1
      createRequest.current = null
      return
    }
    const status = error instanceof ProblemDetailsError ? error.problem.status : 0
    const message = error instanceof ProblemDetailsError && status === 409 && error.problem.code === 'esports_result_too_early'
      ? '测试尚未完成，请等待完整测试结束后再手动重试保存。'
      : status === 429 ? `请求过于频繁，请等待 ${error instanceof ProblemDetailsError ? error.retryAfterSeconds ?? 60 : 60} 秒后手动重试。`
      : status === 409 ? '已有运行或保存状态与当前操作冲突，请先重新对账。'
      : status === 410 ? '此尝试已过期，请重新对账后开始新尝试。'
      : '暂时无法完成请求。请保留本页并手动重试或重新对账，不会自动创建新尝试。'
    update(ticket, { error: message })
  }
  async function acceptSaved(ticket: Ticket, attempt: AttemptView, saved: ResultSaveView) {
    if (!valid(ticket)) return
    if (!belongs(attempt) || !acceptsSave(attempt, saved)) throw new Error('saved identity mismatch')
    const pending = live.current.pending
    if (pending && matchesAttempt(pending, attempt)) removePending(pending)
    update(ticket, { saved, serverView: { ...attempt, state: 'submitted' }, pending: null, error: null, volatile: false })
    await queryClient.invalidateQueries({ queryKey: assessmentKeys.root(ticket.account) })
  }
  async function read(ticket: Ticket, id: string): Promise<AttemptDetailView | null> {
    if (!valid(ticket)) return null
    const detail = await (options.mode === 'formal' ? assessmentApi.readAttempt(id) : assessmentApi.readPractice(id))
    if (!valid(ticket)) return null
    if (!belongs(detail.attempt)) throw new Error('attempt identity mismatch')
    update(ticket, { serverView: detail.attempt })
    if (detail.saved) await acceptSaved(ticket, detail.attempt, detail.saved)
    else if (['expired', 'interrupted'].includes(detail.attempt.state)) {
      const pending = live.current.pending
      if (pending && matchesAttempt(pending, detail.attempt)) removePending(pending)
      createRequest.current = null
      update(ticket, { pending: null, error: detail.attempt.state === 'expired' ? '此尝试已过期，可以重新开始；旧结果不能移到新尝试。' : '此尝试已中断，可以重新开始。' })
    }
    return valid(ticket) ? detail : null
  }
  async function discover(ticket: Ticket) {
    const cached = loadPending(ticket.account).find(item => item.mode === options.mode && item.suite_id === options.suite_id
      && (options.mode === 'formal' ? item.session_id === options.session_id : item.session_id === null)
      && (!options.practice_id || item.attempt_id === options.practice_id) && (!options.test_id || item.test_id === options.test_id))
    if (cached) update(ticket, { pending: cached, volatile: !savePending(cached).persisted })
    const id = live.current.serverView?.id ?? options.practice_id ?? cached?.attempt_id
    if (id) return read(ticket, id)
    let cursor: string | undefined
    const seen = new Set<string>()
    do {
      if (!valid(ticket)) return null
      const page = await assessmentApi.recovery(cursor ? { cursor } : {})
      if (!valid(ticket)) return null
      const item = page.items.find(item => item.mode === options.mode && item.suite_id === options.suite_id
        && (options.mode === 'formal' ? item.session_id === options.session_id : item.session_id === null)
        && (!options.test_id || item.test_id === options.test_id))
      if (item) return read(ticket, item.attempt_id)
      cursor = page.next_cursor ?? undefined
      if (cursor && seen.has(cursor)) throw new Error('repeated recovery cursor')
      if (cursor) seen.add(cursor)
    } while (cursor)
    return null
  }
  async function reconcile() {
    const ticket = capture()
    if (!ticket.account || live.current.busy || live.current.revoked) return null
    update(ticket, { busy: true, error: null })
    try { const detail = await discover(ticket); update(ticket, { ready: true }); return detail }
    catch (error) { failed(ticket, error); return null }
    finally { update(ticket, { busy: false }) }
  }
  async function start(settings: StartSettings): Promise<{ attempt: AttemptView; leaseMs: number } | null> {
    const ticket = capture()
    if (!ticket.account || options.practice_id || !live.current.ready || live.current.busy || live.current.revoked || live.current.pending
      || (live.current.serverView && !['interrupted', 'expired'].includes(live.current.serverView.state))) return null
    update(ticket, { busy: true, error: null, saved: null })
    let settle!: () => void
    const flight = { identity: ticket.identity, settled: new Promise<void>(resolve => { settle = resolve }) }
    starting.current = flight
    createRequest.current ??= { ...settings, operation_key: crypto.randomUUID() }
    const request = createRequest.current
    const requestAt = performance.now()
    try {
      const { difficulty, ...formalRequest } = request
      const attempt = options.mode === 'formal'
        ? await assessmentApi.startAttempt(options.session_id!, formalRequest)
        : await assessmentApi.startPractice({ ...formalRequest, suite_id: options.suite_id, difficulty })
      if (!valid(ticket)) return null
      if (!belongs(attempt)) throw new Error('created attempt identity mismatch')
      const leaseMs = remainingLeaseMs(attempt, performance.now() - requestAt)
      update(ticket, { serverView: attempt, leaseMs })
      return { attempt, leaseMs }
    } catch (error) { failed(ticket, error); return null }
    finally {
      update(ticket, { busy: false })
      if (starting.current === flight) starting.current = null
      settle()
    }
  }
  async function postPending(ticket: Ticket, pending: PendingSubmission, attempt: AttemptView) {
    if (!valid(ticket) || pending.account_id !== ticket.account || !matchesAttempt(pending, attempt)) return
    const body = { operation_key: pending.operation_key, raw: pending.raw, quality: pending.quality }
    const saved = await (pending.mode === 'formal' ? assessmentApi.submitResult(attempt.id, body) : assessmentApi.submitPractice(attempt.id, body))
    if (!valid(ticket) || live.current.pending?.operation_key !== pending.operation_key || live.current.serverView?.id !== attempt.id) return
    await acceptSaved(ticket, attempt, saved)
  }
  async function submit(raw: RawResult, quality: QualityInput) {
    const ticket = capture()
    const attempt = live.current.serverView
    if (!valid(ticket) || !attempt || !belongs(attempt) || live.current.busy || live.current.revoked || live.current.pending || !['running', 'awaiting_submission'].includes(attempt.state)) return
    try {
      const pending = pendingSubmissionSchema.parse({ account_id: ticket.account, mode: attempt.mode, suite_id: attempt.suite_id, session_id: attempt.session_id, test_id: attempt.test_id, attempt_id: attempt.id, operation_key: crypto.randomUUID(), created_at: new Date().toISOString(), raw, quality })
      const { persisted } = savePending(pending)
      update(ticket, { pending, busy: true, volatile: !persisted, error: null })
      await postPending(ticket, pending, attempt)
    } catch (error) { failed(ticket, error) }
    finally { update(ticket, { busy: false }) }
  }
  async function retry() {
    const ticket = capture()
    if (live.current.busy || live.current.revoked) return
    update(ticket, { busy: true, error: null })
    try {
      const detail = await discover(ticket)
      if (!valid(ticket) || !detail || detail.saved || !['running', 'awaiting_submission'].includes(detail.attempt.state)) return
      if (live.current.pending) await postPending(ticket, live.current.pending, detail.attempt)
    } catch (error) { failed(ticket, error) }
    finally { update(ticket, { busy: false, ready: true }) }
  }
  async function interrupt() {
    const ticket = capture()
    if (live.current.busy || live.current.revoked) return
    update(ticket, { busy: true, error: null })
    try {
      const detail = await discover(ticket)
      if (!valid(ticket) || !detail || detail.saved || !['running', 'awaiting_submission'].includes(detail.attempt.state)) return
      const id = detail.attempt.id
      const key = interruptKeys.current.get(id) ?? crypto.randomUUID()
      interruptKeys.current.set(id, key)
      const attempt = await (options.mode === 'formal' ? assessmentApi.interruptAttempt(id, { operation_key: key }) : assessmentApi.interruptPractice(id, { operation_key: key }))
      if (!valid(ticket)) return
      if (!belongs(attempt)) throw new Error('interrupt identity mismatch')
      if (live.current.pending) removePending(live.current.pending)
      createRequest.current = null
      update(ticket, { serverView: attempt, pending: null })
    } catch (error) {
      failed(ticket, error)
      if (valid(ticket) && error instanceof ProblemDetailsError && [409, 410].includes(error.problem.status)) {
        try { await discover(ticket) } catch (nextError) { failed(ticket, nextError) }
      }
    } finally { update(ticket, { busy: false, ready: true }) }
  }
  async function leave(): Promise<boolean> {
    const ticket = capture()
    const terminalRevocation = () => current.current.mounted && ticket.identity === current.current.identity
      && ticket.account === current.current.account && live.current.revoked && live.current.pending === null
    const flight = starting.current
    if (flight?.identity === ticket.identity) await flight.settled
    // Never reconcile or interrupt an old attempt with a new account's credentials.
    if (terminalRevocation()) return true
    if (!valid(ticket)) return false
    if (live.current.saved) return true
    // Leaving is not discarding: a completed result retains its original durable key.
    if (live.current.pending) return !live.current.volatile
    if (live.current.busy || live.current.revoked) return false
    await interrupt()
    // Revocation invalidates this generation but is terminal for this same owner;
    // it must not create a navigation trap or retry with another account.
    if (terminalRevocation()) return true
    if (!valid(ticket)) return false
    return live.current.saved !== null || (live.current.serverView
      ? ['interrupted', 'expired', 'submitted'].includes(live.current.serverView.state)
      : live.current.error === null)
  }
  useEffect(() => {
    current.current.mounted = true
    // Cleanup invalidates the old generation, including its finally handler.
    // A replayed setup owns a fresh reconciliation, not the obsolete busy flag.
    live.current = { ...live.current, busy: false }
    void reconcile()
    return () => { current.current.mounted = false; current.current.generation += 1 }
    // Identity deliberately owns the entire resource lifetime; requests never restart on state updates.
  }, [identity])
  const state = rendered.identity === identity ? rendered.value : empty()
  const reportId = state.saved?.mode === 'formal' ? state.saved.session.report_id : null
  useEffect(() => {
    // Navigation follows the saved-state commit so the Host's route blocker has
    // released its pending guard; a fast invalidation must not race that render.
    if (reportId && !state.busy) navigate(`/esports-assessments/reports/${reportId}`)
  }, [reportId, state.busy, navigate])
  return { ...state, accountId: account, canRestart: !options.practice_id, start, submit, interrupt, leave, reconcile, retry }
}

export type AssessmentSessionController = ReturnType<typeof useAssessmentSession>
