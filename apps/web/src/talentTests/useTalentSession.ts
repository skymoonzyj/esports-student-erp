import { useCallback, useEffect, useRef, useState } from "react"

import { ProblemDetailsError } from "../api/client"
import {
  abandonTalentSession,
  getTalentSession,
  submitTalentResult,
} from "./api"
import {
  ENGINE_VERSIONS,
  TEST_IDS,
  type CompletedGame,
  type SessionView,
  type Submission,
} from "./contracts"
import {
  clearTalentSessionPending,
  clearTalentAbandonRequest,
  getOrCreateTalentAbandonRequest,
  putPending,
  readPending,
  removePending,
  sanitizeSubmission,
} from "./pending"

export type TalentSaveState = "idle" | "saving" | "pending" | "saved" | "rejected"

const MAX_RETRY_AFTER_SECONDS = 300

function randomUuid(): string {
  return crypto.randomUUID()
}

function submissionFor(result: CompletedGame, submission_id: string): Submission {
  const candidate: unknown = {
    ...result,
    submission_id,
    engine_version: ENGINE_VERSIONS[result.test_id],
    locale: "zh-CN",
  }
  const submission = sanitizeSubmission(candidate)
  if (!submission) throw new Error("游戏结果不符合提交协议。")
  return submission
}

function pendingFromStorage(accountId: string, sessionId: string): Submission | null {
  for (const testId of TEST_IDS) {
    const pending = readPending(accountId, sessionId, testId, Date.now())
    if (pending) return pending
  }
  return null
}

function isAuthorizationStop(error: unknown): boolean {
  return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)
}

function belongsToCurrentPerson(
  session: SessionView,
  sessionId: string,
  personId: string,
): boolean {
  return session.id === sessionId && session.person_id === personId
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function validServerResult(result: SessionView["results"][number]): boolean {
  return Boolean(
    isUuid(result.id) &&
    sanitizeSubmission(result) &&
    Number.isInteger(result.score) &&
    result.score >= 0 &&
    result.score <= 100 &&
    Number.isFinite(result.raw_value) &&
    typeof result.unit === "string" &&
    typeof result.created_at === "string",
  )
}

function isPresentTimestamp(value: unknown): value is string {
  return typeof value === "string" && value.length > 0
}

function validSessionState(session: SessionView): boolean {
  switch (session.status) {
    case "in_progress":
      return session.completed_at === null &&
        session.abandoned_at === null &&
        session.report_id === null
    case "completed":
      return isPresentTimestamp(session.completed_at) &&
        session.abandoned_at === null &&
        typeof session.report_id === "string" &&
        isUuid(session.report_id)
    case "abandoned":
      return session.completed_at === null &&
        isPresentTimestamp(session.abandoned_at) &&
        session.report_id === null
    default:
      return false
  }
}

function validOwnedSession(
  session: SessionView,
  sessionId: string,
  personId: string,
): boolean {
  return belongsToCurrentPerson(session, sessionId, personId) &&
    validSessionState(session) &&
    Array.isArray(session.results) &&
    session.results.every(validServerResult)
}

function confirmsSave(
  saved: Awaited<ReturnType<typeof submitTalentResult>>,
  value: Submission,
  sessionId: string,
  personId: string,
): boolean {
  if (!validOwnedSession(saved.session, sessionId, personId) ||
      !validServerResult(saved.result)) return false
  if (
    saved.result.submission_id !== value.submission_id ||
    saved.result.test_id !== value.test_id ||
    saved.result.engine_version !== value.engine_version ||
    saved.result.locale !== value.locale
  ) return false
  return saved.session.results.some((result) =>
    result.id === saved.result.id &&
    result.submission_id === value.submission_id &&
    result.test_id === value.test_id &&
    result.engine_version === value.engine_version &&
    result.locale === value.locale,
  )
}

export function useTalentSession(
  accountId: string,
  personId: string,
  sessionId: string,
) {
  const identity = JSON.stringify([accountId, personId, sessionId])
  const [loadedIdentity, setLoadedIdentity] = useState<string | null>(null)
  const [session, setSession] = useState<SessionView | null>(null)
  const [readState, setReadState] = useState<"loading" | "ready" | "failed" | "blocked">("loading")
  const [readAttempt, setReadAttempt] = useState(0)
  const [saveState, setSaveState] = useState<TalentSaveState>("idle")
  const [statusText, setStatusText] = useState<string | null>(null)
  const [retryAfterSeconds, setRetryAfterSeconds] = useState(0)
  const [retryDeadlineMs, setRetryDeadlineMs] = useState<number | null>(null)
  const generationRef = useRef(0)
  const pendingRef = useRef<Submission | null>(null)

  useEffect(() => {
    const generation = generationRef.current + 1
    generationRef.current = generation
    setLoadedIdentity(null)
    setSession(null)
    setReadState("loading")
    setSaveState("idle")
    setStatusText(null)
    setRetryAfterSeconds(0)
    setRetryDeadlineMs(null)
    pendingRef.current = pendingFromStorage(accountId, sessionId)

    void getTalentSession(sessionId).then((loaded) => {
      if (generationRef.current !== generation) return
      if (!validOwnedSession(loaded, sessionId, personId)) {
        setReadState("blocked")
        setSaveState("rejected")
        setStatusText(
          pendingRef.current
            ? "无法验证本轮归属，已停止处理待提交结果；清空浏览器数据会丢失它。"
            : "无法验证本轮归属，请重新进入训练。",
        )
        return
      }
      setSession(loaded)
      setLoadedIdentity(identity)
      setReadState("ready")
      const pending = pendingRef.current
      if (!pending) return
      const saved = loaded.results.find((result) => result.test_id === pending.test_id)
      if (saved?.submission_id === pending.submission_id) {
        removePending(accountId, sessionId, pending.test_id)
        pendingRef.current = null
        setSaveState("saved")
        setStatusText("结果已由服务器确认保存。")
      } else if (saved) {
        removePending(accountId, sessionId, pending.test_id)
        pendingRef.current = null
        setSaveState("rejected")
        setStatusText("该项目已有其他结果，不能重复提交。")
      } else if (loaded.status === "in_progress") {
        setSaveState("pending")
        setStatusText("有一份未提交结果，可手动重试；清空浏览器数据会丢失它。")
      } else {
        setSaveState("rejected")
        setStatusText(
          "本轮已结束，不能处理待提交结果；它仍保存在本次浏览器会话中，清空浏览器数据会丢失它。",
        )
      }
    }).catch((error: unknown) => {
      if (generationRef.current !== generation) return
      if (isAuthorizationStop(error)) {
        setReadState("blocked")
        setStatusText(
          pendingRef.current
            ? "请重新登录并验证身份后再处理待提交结果；清空浏览器数据会丢失它。"
            : "请重新登录并验证身份后再进入训练。",
        )
      } else {
        setReadState("failed")
        setStatusText(
          pendingRef.current
            ? "暂时无法读取服务器状态；待提交结果仍保存在本次浏览器会话中，清空浏览器数据会丢失它。"
            : "暂时无法读取服务器状态，请重新读取本轮后继续。",
        )
      }
    })

    return () => {
      if (generationRef.current === generation) generationRef.current += 1
    }
  }, [accountId, personId, sessionId, identity, readAttempt])

  const reread = useCallback(() => {
    setReadState("loading")
    setReadAttempt((value) => value + 1)
  }, [])

  useEffect(() => {
    if (retryDeadlineMs === null) return
    const updateCountdown = () => {
      const remaining = Math.max(0, Math.ceil((retryDeadlineMs - Date.now()) / 1000))
      setRetryAfterSeconds(remaining)
      if (remaining === 0) setRetryDeadlineMs(null)
    }
    const timer = window.setInterval(updateCountdown, 250)
    return () => window.clearInterval(timer)
  }, [retryDeadlineMs])

  const save = useCallback(async (value: Submission): Promise<void> => {
    const generation = generationRef.current
    const submission = sanitizeSubmission(value)
    if (!submission) {
      setSaveState("rejected")
      setStatusText("结果无效，请重新完成本项。")
      return
    }
    pendingRef.current = submission
    putPending(accountId, sessionId, submission, Date.now())
    setRetryAfterSeconds(0)
    setRetryDeadlineMs(null)
    setSaveState("saving")
    setStatusText("正在保存结果…")
    try {
      const saved = await submitTalentResult(sessionId, submission)
      if (generationRef.current !== generation) return
      if (!confirmsSave(saved, submission, sessionId, personId)) {
        setSaveState("pending")
        setStatusText(
          "服务器响应无法确认本次结果，已保留原提交编号供手动重试；清空浏览器数据会丢失它。",
        )
        return
      }
      setSession(saved.session)
      removePending(accountId, sessionId, submission.test_id)
      pendingRef.current = null
      setSaveState("saved")
      setStatusText("结果已保存。")
    } catch (error) {
      if (generationRef.current !== generation) return
      if (error instanceof ProblemDetailsError && error.problem.status === 409) {
        try {
          const current = await getTalentSession(sessionId)
          if (generationRef.current !== generation) return
          if (!validOwnedSession(current, sessionId, personId)) {
            setSession(null)
            setSaveState("rejected")
            setStatusText(
              "无法验证本轮归属，已停止处理待提交结果；清空浏览器数据会丢失它。",
            )
            return
          }
          setSession(current)
          const existing = current.results.find((result) => result.test_id === submission.test_id)
          if (existing?.submission_id === submission.submission_id) {
            removePending(accountId, sessionId, submission.test_id)
            pendingRef.current = null
            setSaveState("saved")
            setStatusText("结果已由服务器确认保存。")
          } else if (existing) {
            removePending(accountId, sessionId, submission.test_id)
            pendingRef.current = null
            setSaveState("rejected")
            setStatusText("该项目已有其他结果，不能重复提交。")
          } else if (current.status !== "in_progress") {
            setSaveState("rejected")
            setStatusText("本轮已结束，不能再提交；清空浏览器数据会丢失待提交结果。")
          } else {
            setSaveState("rejected")
            setStatusText("提交编号发生冲突，请重新完成本项；清空浏览器数据会丢失待提交结果。")
          }
        } catch (refreshError) {
          if (generationRef.current !== generation) return
          if (isAuthorizationStop(refreshError)) setSession(null)
          setSaveState("pending")
          setStatusText(
            "无法核对冲突状态，已保留原提交编号供手动重试；清空浏览器数据会丢失它。",
          )
        }
        return
      }
      if (error instanceof ProblemDetailsError && error.problem.status === 422) {
        removePending(accountId, sessionId, submission.test_id)
        pendingRef.current = null
        setSaveState("rejected")
        setStatusText("结果无效，请重新完成本项。")
        return
      }
      if (isAuthorizationStop(error)) {
        setSession(null)
        setSaveState("rejected")
        setStatusText(
          "请重新登录并验证身份后再处理待提交结果；清空浏览器数据会丢失它。",
        )
        return
      }
      setSaveState("pending")
      if (error instanceof ProblemDetailsError && error.problem.status === 429) {
        const waitSeconds = Math.min(
          MAX_RETRY_AFTER_SECONDS,
          Math.max(0, error.retryAfterSeconds ?? 0),
        )
        setRetryAfterSeconds(waitSeconds)
        setRetryDeadlineMs(waitSeconds > 0 ? Date.now() + waitSeconds * 1000 : null)
        setStatusText("提交过于频繁，现在可以手动重试；清空浏览器数据会丢失待提交结果。")
      } else {
        setStatusText("保存失败，已保留原提交编号；可手动重试，清空浏览器数据会丢失结果。")
      }
    }
  }, [accountId, personId, sessionId])

  const submit = useCallback(async (value: CompletedGame): Promise<void> => {
    await save(submissionFor(value, randomUuid()))
  }, [save])

  const retry = useCallback(async (): Promise<void> => {
    if (loadedIdentity !== identity || retryAfterSeconds > 0 || !session || !validOwnedSession(session, sessionId, personId)) return
    const pending = pendingRef.current ?? pendingFromStorage(accountId, sessionId)
    if (!pending) return
    await save(pending)
  }, [accountId, retryAfterSeconds, save, sessionId, personId, session, loadedIdentity, identity])

  const abandon = useCallback(async (): Promise<void> => {
    const generation = generationRef.current
    const requestId = getOrCreateTalentAbandonRequest(accountId, sessionId)
    try {
      const abandoned = await abandonTalentSession(sessionId, requestId)
      if (generationRef.current !== generation) return
      if (!validOwnedSession(abandoned, sessionId, personId)) {
        setSession(null)
        setStatusText(
          pendingRef.current
            ? "无法验证放弃响应的轮次归属，请重试；待提交结果仍保存在本次浏览器会话中，清空浏览器数据会丢失它。"
            : "无法验证放弃响应的轮次归属，请重试。",
        )
        return
      }
      setSession(abandoned)
      clearTalentAbandonRequest(accountId, sessionId, requestId)
      clearTalentSessionPending(accountId, sessionId)
      pendingRef.current = null
      setSaveState("idle")
      setStatusText("本轮已放弃。")
    } catch (error) {
      if (generationRef.current !== generation) return
      if (isAuthorizationStop(error)) setSession(null)
      setStatusText(
        pendingRef.current
          ? "放弃请求未确认，已保留原请求编号供重试；待提交结果仍保存在本次浏览器会话中，清空浏览器数据会丢失它。"
          : "放弃请求未确认，已保留原请求编号供重试。",
      )
      throw error
    }
  }, [accountId, personId, sessionId])

  const visibleStatusText = retryAfterSeconds > 0
    ? `提交过于频繁，请在 ${retryAfterSeconds} 秒后手动重试；清空浏览器数据会丢失待提交结果。`
    : statusText

  // Effects reset state after rendering. Gate the exposed data synchronously so
  // changing accounts/people/rounds never renders the previous identity's result.
  const visibleSession = loadedIdentity === identity ? session : null
  const pendingResult = visibleSession &&
    (saveState === "saving" || saveState === "pending") &&
    pendingRef.current &&
    visibleSession.rules.test_ids.includes(pendingRef.current.test_id)
    ? pendingRef.current
    : null

  return {
    session: visibleSession,
    pendingResult,
    readState,
    reread,
    saveState,
    statusText: visibleStatusText,
    retryAfterSeconds,
    submit,
    retry,
    abandon,
  }
}
