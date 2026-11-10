import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import {
  createTalentSession,
  getTalentContext,
  listTalentReports,
  listTalentSubjects,
} from "./api"
import type { PersonDisplay, TestId } from "./contracts"
import {
  confirmTalentSessionCreated,
  discardTalentSessionRequest,
  getOrCreateTalentSessionRequest,
} from "./pending"
import { useTalentSession } from "./useTalentSession"
import "./talentTests.css"

const TEST_NAMES: Record<TestId, string> = {
  reactiontime: "反应速度",
  aim: "瞄准速度",
  sequence: "顺序记忆",
  "number-memory": "数字记忆",
  "verbal-memory": "中文词语记忆",
  chimp: "黑猩猩测试",
  memory: "视觉记忆",
  "dynamic-vision": "动态视力",
}

const TEST_SUBTITLES: Partial<Record<TestId, string>> = {
  "dynamic-vision": "瞬时数字识别",
}

function isForbidden(error: unknown): boolean {
  return error instanceof ProblemDetailsError && error.problem.status === 403
}

function testCountText(count: number): string {
  if (count === 8) return "八项"
  if (count === 7) return "七项"
  return `${count} 项`
}

export function TalentTestsPage({
  accountId,
  personId,
}: {
  accountId: string
  personId: string
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [actionError, setActionError] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [catalogConflict, setCatalogConflict] = useState(false)
  const [subjectPage, setSubjectPage] = useState(1)
  const [reportPage, setReportPage] = useState(1)
  const [subjectSearchInput, setSubjectSearchInput] = useState("")
  const [subjectSearch, setSubjectSearch] = useState("")
  const [selectedPerson, setSelectedPerson] = useState<PersonDisplay | null>(null)
  const [activeRules, setActiveRules] = useState<{
    identityKey: string
    sessionId: string
    testCount: number
  } | null>(null)
  const createRequest = useRef<ReturnType<typeof getOrCreateTalentSessionRequest> | null>(null)
  const rootKey = useMemo(() => ["talent-tests", accountId] as const, [accountId])
  const identityKey = `${accountId}:${personId}`
  const identityRef = useRef(identityKey)
  identityRef.current = identityKey
  const handleActiveRulesLoaded = useCallback((sessionId: string, testCount: number | null) => {
    setActiveRules(testCount === null ? null : { identityKey, sessionId, testCount })
  }, [identityKey])

  useEffect(() => {
    createRequest.current = null
    setCreating(false)
    setActionError(null)
    setCatalogConflict(false)
    setSubjectPage(1)
    setReportPage(1)
    setSubjectSearchInput("")
    setSubjectSearch("")
    setSelectedPerson(null)
  }, [identityKey])

  const context = useQuery({
    queryKey: [...rootKey, "context"],
    queryFn: getTalentContext,
  })
  const activeSessionId = context.data?.active_session_id ?? null
  const subjects = useQuery({
    queryKey: [...rootKey, "subjects", subjectPage, subjectSearch],
    queryFn: () => listTalentSubjects(subjectPage, 20, subjectSearch),
    enabled: context.data?.can_read_reports === true,
  })
  const reports = useQuery({
    queryKey: [...rootKey, "reports", selectedPerson?.id ?? "all", reportPage],
    queryFn: () => listTalentReports(selectedPerson?.id, reportPage, 20),
    enabled: context.data?.can_read_reports === true,
  })

  useEffect(() => {
    if (![context.error, subjects.error, reports.error].some(isForbidden)) {
      return
    }
    void queryClient.cancelQueries({ queryKey: rootKey }).finally(() => {
      queryClient.removeQueries({ queryKey: rootKey })
    })
  }, [context.error, queryClient, reports.error, rootKey, subjects.error])

  if (context.isPending) return <p>正在读取天赋测试权限…</p>
  if (context.isError || !context.data) {
    return <p role="alert">无法读取天赋测试信息，请稍后重试。</p>
  }

  const catalog = context.data.catalog
  const activeRuleCount = context.data.can_take && activeSessionId
    ? activeRules?.identityKey === identityKey && activeRules.sessionId === activeSessionId
      ? activeRules.testCount
      : null
    : catalog.test_ids.length
  const introduction = activeRuleCount === null
    ? "本轮训练项目会保存在同一轮中；结果只用于训练参考。"
    : `${testCountText(activeRuleCount)}训练会保存在同一轮中；结果只用于训练参考。`
  async function startSession(preferredTest?: TestId): Promise<void> {
    if (creating) return
    setCreating(true)
    setActionError(null)
    setCatalogConflict(false)
    const request = createRequest.current ?? getOrCreateTalentSessionRequest(
      accountId,
      catalog.catalog_version,
    )
    createRequest.current = request
    const requestIdentity = identityKey
    try {
      const session = await createTalentSession(
        request.request_id,
        request.catalog_version,
      )
      const responseConfirmed = session.person_id === personId &&
        confirmTalentSessionCreated(accountId, request, {
          id: session.id,
          catalog_version: session.rules.catalog_version,
        })
      if (identityRef.current !== requestIdentity) return
      if (!responseConfirmed) {
        setActionError("服务器响应无法确认新轮次，请保留本次请求编号后重试。")
        return
      }
      createRequest.current = null
      queryClient.setQueryData([...rootKey, "session", session.id], session)
      await queryClient.invalidateQueries({ queryKey: [...rootKey, "context"] })
      if (identityRef.current !== requestIdentity) return
      const remaining = session.rules.test_ids.filter(
        (testId) => !session.results.some((result) => result.test_id === testId),
      )
      const first = preferredTest && remaining.includes(preferredTest) ? preferredTest : remaining[0]
      if (first) navigate(`/talent-tests/sessions/${session.id}/${first}`)
    } catch (error) {
      if (identityRef.current !== requestIdentity) return
      if (error instanceof ProblemDetailsError && error.problem.status === 409) {
        setActionError(error.problem.detail)
        setCatalogConflict(true)
      } else {
        setActionError("新一轮创建失败，可保留本次请求编号后重试。")
      }
    } finally {
      if (identityRef.current === requestIdentity) setCreating(false)
    }
  }

  async function rereadCatalog(): Promise<void> {
    const request = createRequest.current
    if (request) discardTalentSessionRequest(accountId, request)
    createRequest.current = null
    setCatalogConflict(false)
    setActionError(null)
    const refreshed = await context.refetch()
    if (refreshed.isError) setActionError("目录读取失败，请稍后重试。")
  }

  const readableReports = reports.data?.items ?? []
  const readablePeople = subjects.data?.items ?? []
  const subjectPages = Math.max(1, Math.ceil((subjects.data?.total ?? 0) / 20))
  const reportPages = Math.max(1, Math.ceil((reports.data?.total ?? 0) / 20))

  return (
    <div className="talent-page">
      <header className="talent-page-heading">
        <div>
          <p className="talent-game-kicker">TRAINING REFERENCE</p>
          <h1>天赋测试</h1>
          <p>{introduction}</p>
        </div>
        {context.data.can_read_self ? <Link to="/talent-tests/history">历史记录</Link> : null}
      </header>

      <section
        className="talent-session-panel"
        aria-label={context.data.can_take ? "本人测试轮次" : "天赋测试项目目录"}
      >
        {context.data.can_take ? (
          activeSessionId ? (
            <ActiveTalentSession
              key={`${accountId}:${activeSessionId}`}
              accountId={accountId}
              personId={personId}
              sessionId={activeSessionId}
              onRulesLoaded={handleActiveRulesLoaded}
              onAbandoned={() => queryClient.invalidateQueries({ queryKey: rootKey })}
            />
          ) : (
            <>
              <TalentCatalog
                testIds={catalog.test_ids}
                saved={new Set()}
                showProgress
                onStart={(testId) => void startSession(testId)}
                creating={creating}
              />
              <button type="button" className="primaryButton" disabled={creating} onClick={() => void startSession()}>
                {creating ? "正在创建…" : "开始新一轮"}
              </button>
            </>
          )
        ) : (
          <TalentCatalog testIds={catalog.test_ids} saved={new Set()} />
        )}
        {actionError ? <p role="alert">{actionError}</p> : null}
        {catalogConflict ? (
          <button type="button" onClick={() => void rereadCatalog()}>
            重新读取目录
          </button>
        ) : null}
      </section>

      <section className="talent-session-panel" aria-label="实体灯独立专项">
        <h2>实体灯专项</h2>
        <p>ReactionX 连接器与实体设备训练，独立准备及保存；不计入原八项测试轮次。正式模式尚未开放，自由训练不参与排名。</p>
        <Link to="/talent-tests/reactionx">进入实体灯专项</Link>
      </section>

      {context.data.can_read_reports ? (
        <section className="talent-readable-reports" aria-label="授权人员和报告">
          <form
            role="search"
            aria-label="授权人员搜索"
            onSubmit={(event) => {
              event.preventDefault()
              setSubjectPage(1)
              setSubjectSearch(subjectSearchInput.trim())
            }}
          >
            <label>
              搜索授权人员
              <input
                type="search"
                maxLength={80}
                value={subjectSearchInput}
                onChange={(event) => setSubjectSearchInput(event.target.value)}
              />
            </label>
            <button type="submit">搜索人员</button>
          </form>

          {subjects.isError ? (
            <div role="alert" aria-label="授权人员读取失败">
              <p>无法读取授权人员，请重试。</p>
              <button type="button" onClick={() => void subjects.refetch()}>
                重试人员列表
              </button>
            </div>
          ) : subjects.isPending ? (
            <p>正在读取授权人员…</p>
          ) : (
            <>
              <ul aria-label="授权人员">
                {readablePeople.map((person) => (
                  <li key={person.id}>
                    <button
                      type="button"
                      aria-pressed={selectedPerson?.id === person.id}
                      aria-label={`筛选${person.name}的报告`}
                      onClick={() => {
                        setSelectedPerson(person)
                        setReportPage(1)
                      }}
                    >
                      {person.name}{person.nickname ? `（${person.nickname}）` : ""}
                    </button>
                  </li>
                ))}
              </ul>
              <nav className="talent-pagination" aria-label="授权人员分页">
                <button
                  type="button"
                  disabled={subjectPage <= 1}
                  onClick={() => setSubjectPage((value) => Math.max(1, value - 1))}
                >
                  上一页授权人员
                </button>
                <span>人员第 {subjectPage} 页</span>
                <button
                  type="button"
                  disabled={subjectPage >= subjectPages}
                  onClick={() => setSubjectPage((value) => value + 1)}
                >
                  下一页授权人员
                </button>
              </nav>
            </>
          )}

          {selectedPerson ? (
            <p>
              当前报告筛选：{selectedPerson.name}
              <button
                type="button"
                onClick={() => {
                  setSelectedPerson(null)
                  setReportPage(1)
                }}
              >
                查看全部授权报告
              </button>
            </p>
          ) : null}
          {reports.isError ? (
            <div role="alert" aria-label="授权报告读取失败">
              <p>无法读取授权报告，请重试。</p>
              <button type="button" onClick={() => void reports.refetch()}>
                重试报告列表
              </button>
            </div>
          ) : reports.isPending ? (
            <p>正在读取授权报告…</p>
          ) : (
            <>
              {readableReports.length === 0 ? (
                <p>暂无可查看的天赋测试报告</p>
              ) : (
                <ul aria-label="可查看报告">
                  {readableReports.map((report) => (
                    <li key={report.id}>
                      <Link
                        to={`/talent-tests/reports/${report.id}`}
                        aria-label={`查看${report.person.name}的报告`}
                      >
                        {report.person.name}{report.person.nickname ? `（${report.person.nickname}）` : ""}
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
              <nav className="talent-pagination" aria-label="报告分页">
                <button
                  type="button"
                  disabled={reportPage <= 1}
                  onClick={() => setReportPage((value) => Math.max(1, value - 1))}
                >
                  上一页报告
                </button>
                <span>报告第 {reportPage} 页</span>
                <button
                  type="button"
                  disabled={reportPage >= reportPages}
                  onClick={() => setReportPage((value) => value + 1)}
                >
                  下一页报告
                </button>
              </nav>
            </>
          )}
        </section>
      ) : null}

    </div>
  )
}

function ActiveTalentSession({
  accountId,
  personId,
  sessionId,
  onRulesLoaded,
  onAbandoned,
}: {
  accountId: string
  personId: string
  sessionId: string
  onRulesLoaded: (sessionId: string, testCount: number | null) => void
  onAbandoned: () => void | Promise<unknown>
}) {
  const { session, statusText, abandon } = useTalentSession(accountId, personId, sessionId)
  const [confirmAbandon, setConfirmAbandon] = useState(false)
  const [abandoning, setAbandoning] = useState(false)
  const [abandonError, setAbandonError] = useState<string | null>(null)
  useEffect(() => {
    onRulesLoaded(sessionId, session?.rules.test_ids.length ?? null)
  }, [onRulesLoaded, session, sessionId])
  if (!session) return <p>{statusText ?? "正在读取本轮进度…"}</p>
  const saved = new Set(session.results.map((result) => result.test_id))
  const nextTest = session.rules.test_ids.find((testId) => !saved.has(testId)) ?? null

  async function confirm(): Promise<void> {
    if (abandoning) return
    setAbandoning(true)
    setAbandonError(null)
    try {
      await abandon()
      setConfirmAbandon(false)
      await onAbandoned()
    } catch {
      setAbandonError("放弃请求未确认，请重试。")
    } finally {
      setAbandoning(false)
    }
  }

  return (
    <>
      <TalentCatalog
        testIds={session.rules.test_ids}
        saved={saved}
        sessionId={sessionId}
        canSelect={session.status === "in_progress"}
        showProgress
      />
      {session.status === "in_progress" && nextTest ? (
        <div className="talent-actions">
          <Link
            className="primaryButton"
            to={`/talent-tests/sessions/${sessionId}/${nextTest}`}
            aria-label={`继续${TEST_NAMES[nextTest]}`}
          >
            继续{TEST_NAMES[nextTest]}
          </Link>
          <button type="button" className="secondaryButton" onClick={() => setConfirmAbandon(true)}>
            放弃本轮
          </button>
        </div>
      ) : null}
      {session.status === "completed" && session.report_id ? (
        <Link to={`/talent-tests/reports/${session.report_id}`}>查看本轮报告</Link>
      ) : null}
      {statusText ? <p>{statusText}</p> : null}
      {abandonError ? <p role="alert">{abandonError}</p> : null}
      {confirmAbandon ? (
        <div className="talent-dialog-backdrop">
          <section role="dialog" aria-modal="true" aria-label="确认放弃本轮" className="talent-dialog">
            <h2>确认放弃本轮</h2>
            <p>已保存的结果会保留，但本轮将不能继续，也不会生成完整报告。</p>
            <div className="talent-actions">
              <button type="button" disabled={abandoning} onClick={() => setConfirmAbandon(false)}>返回</button>
              <button type="button" disabled={abandoning} onClick={() => void confirm()}>
                {abandoning ? "正在放弃…" : "确认放弃"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </>
  )
}

function TalentCatalog({
  testIds,
  saved,
  sessionId,
  canSelect = false,
  showProgress = false,
  onStart,
  creating = false,
}: {
  testIds: TestId[]
  saved: ReadonlySet<TestId>
  sessionId?: string
  canSelect?: boolean
  showProgress?: boolean
  onStart?: (testId: TestId) => void
  creating?: boolean
}) {
  return (
    <>
      {showProgress ? (
        <div className="talent-progress-row">
          <strong>{saved.size}/{testIds.length}</strong>
          <span>进度只统计服务器已保存项目</span>
        </div>
      ) : null}
      <div className="talent-catalog-grid">
        {testIds.map((testId) => (
          <article key={testId} className="talent-test-card">
            <h2>{TEST_NAMES[testId]}</h2>
            {TEST_SUBTITLES[testId] ? <p>{TEST_SUBTITLES[testId]}</p> : null}
            <p>{saved.has(testId) ? "已保存" : sessionId ? "待完成" : "新一轮项目"}</p>
            {sessionId && canSelect && !saved.has(testId) ? (
              <Link
                to={`/talent-tests/sessions/${sessionId}/${testId}`}
                aria-label={`选择${TEST_NAMES[testId]}`}
              >
                进入项目
              </Link>
            ) : null}
            {!sessionId && onStart ? (
              <button
                type="button"
                className="secondaryButton"
                aria-label={`选择${TEST_NAMES[testId]}`}
                disabled={creating}
                onClick={() => onStart(testId)}
              >
                开始此项目
              </button>
            ) : null}
          </article>
        ))}
      </div>
    </>
  )
}

export { TEST_NAMES as TALENT_TEST_NAMES }
