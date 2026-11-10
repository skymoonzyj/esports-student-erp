import { act, fireEvent, render as renderBase, renderHook, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode, type ReactElement } from "react"
import { MemoryRouter, Route, Routes, useLocation, useParams } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { GameHost } from "./GameHost"
import type { CompletedGame, ResultView, RuleSnapshot, SessionView, Submission, TestRule } from "./contracts"
import { putPending, readPending } from "./pending"
import { useTalentSession } from "./useTalentSession"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

const itemRanking = {
  status: "ranked", reason: null, group_label: "反应速度鼠标组", calculated_at: "2026-10-31T02:03:04Z", participant_count: 1,
  metrics: [{ key: "score", label: "评分", unit: "分", better: "higher", decimals: 0 }],
  current: { rank: 1, values: { score: 80 } }, best: { rank: 1, values: { score: 80 } },
  top_three: [{ rank: 1, values: { score: 80 }, is_self: true, tie_count: 1, gaps: [{ key: "score", amount: 0, state: "equal" }] }],
}

it("loads rankings only after confirmed save and preserves score and next action on ranking failure", async () => {
  const pending = pendingSubmission()
  putPending(accountId, sessionId, pending, Date.now())
  const urls: string[] = []
  let rankings = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async input => {
    const url = String(input)
    urls.push(url)
    if (url.endsWith("/ranking")) {
      if (++rankings === 1) return new Response("{}", { status: 503 })
      return new Response(JSON.stringify(itemRanking))
    }
    const result = savedResult(pending)
    return new Response(JSON.stringify(url.endsWith("/results") ? { result, session: session({ results: [result] }) } : session()))
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  await screen.findByRole("button", { name: "手动重试" })
  expect(urls.some(url => url.endsWith("/ranking"))).toBe(false)
  expect(screen.queryByRole("heading", { name: "当前数据库排名" })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "手动重试" }))
  expect(await screen.findByText(/单项排名暂不可用/)).toBeVisible()
  expect(screen.getByText("服务器成绩：80 分")).toBeVisible()
  expect(screen.getByRole("link", { name: "继续下一项：瞄准速度" })).toBeVisible()
  expect(urls).toContain("/api/v1/talent-tests/results/f1000000-0000-4000-8000-000000000004/ranking")
  await userEvent.click(screen.getByRole("button", { name: "重试单项排名" }))
  expect(await screen.findByText(/本次成绩：第 1 名/)).toBeVisible()
})

it("aborts old identity ranking and ignores its late response when switching account", async () => {
  let resolveOld!: (response: Response) => void
  let oldSignal: AbortSignal | undefined
  let second = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/ranking")) {
      if (second) return new Response(JSON.stringify({ ...itemRanking, group_label: "新账号比较组" }))
      oldSignal = init?.signal as AbortSignal
      return new Promise<Response>(resolve => { resolveOld = resolve })
    }
    return new Response(JSON.stringify(session({ results: [savedResult(pendingSubmission())] })))
  }))
  const page = render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  await waitFor(() => expect(resolveOld).toBeTypeOf("function"))
  second = true
  page.rerender(<GameHost accountId="new-account" personId={personId} sessionId={sessionId} testId="reactiontime" />)
  expect(oldSignal?.aborted).toBe(true)
  expect(await screen.findByText("新账号比较组")).toBeVisible()
  await act(async () => resolveOld(new Response(JSON.stringify(itemRanking))))
  expect(screen.queryByText("反应速度鼠标组")).not.toBeInTheDocument()
})

it("refreshes a saved item on return to foreground without polling", async () => {
  let reads = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async input => {
    if (String(input).endsWith("/ranking")) return new Response(JSON.stringify({ ...itemRanking, group_label: `比较组刷新 ${++reads}` }))
    return new Response(JSON.stringify(session({ results: [savedResult(pendingSubmission())] })))
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  expect(await screen.findByText("比较组刷新 1")).toBeVisible()
  fireEvent(document, new Event("visibilitychange"))
  expect(await screen.findByText("比较组刷新 2")).toBeVisible()
})

const accountId = "f1000000-0000-4000-8000-000000000001"
const sessionId = "f1000000-0000-4000-8000-000000000002"
const personId = "f1000000-0000-4000-8000-000000000003"

function render(element: ReactElement) {
  const client = new QueryClient()
  return renderBase(element, { wrapper: ({ children }) => <MemoryRouter><QueryClientProvider client={client}>{children}</QueryClientProvider></MemoryRouter> })
}

const rules: RuleSnapshot = {
  catalog_version: "talent-training-v1",
  scoring_version: "talent-score-v1",
  report_version: "talent-report-v1",
  test_ids: ["reactiontime", "aim", "sequence", "number-memory", "verbal-memory", "chimp", "memory"],
  tests: {
    reactiontime: { engine_version: "reactiontime-clone-v1", anchors: [500, 275, 225, 175, 150, 100], source_label: "训练", unit: "ms" },
    aim: { engine_version: "aim-clone-v1", anchors: [1000, 550, 450, 350, 300, 250], source_label: "训练", unit: "ms" },
    sequence: { engine_version: "sequence-clone-v1", anchors: [0, 8, 10, 18, 25, 35], source_label: "训练", unit: "level" },
    "number-memory": { engine_version: "number-memory-clone-v1", anchors: [0, 7, 9, 13, 16, 20], source_label: "训练", unit: "digits" },
    "verbal-memory": { engine_version: "verbal-memory-clone-v1", anchors: [0, 20, 50, 100, 200, 300], source_label: "内部试行训练目标", unit: "count" },
    chimp: { engine_version: "chimp-clone-v1", anchors: [0, 9, 11, 15, 20, 25], source_label: "训练", unit: "count" },
    memory: { engine_version: "memory-clone-v1", anchors: [0, 6, 10, 18, 25, 35], source_label: "内部试行训练目标", unit: "level" },
  },
}

function session(overrides: Partial<SessionView> = {}): SessionView {
  return {
    id: sessionId,
    person_id: personId,
    status: "in_progress",
    created_at: "2026-09-20T00:00:00Z",
    completed_at: null,
    abandoned_at: null,
    rules,
    results: [],
    report_id: null,
    ...overrides,
  }
}

function pendingSubmission(): Submission {
  return {
    test_id: "reactiontime",
    raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
    device: { input_kind: "mouse", viewport_width: 1440, viewport_height: 900, geometry: null },
    duration_ms: 10_000,
    background_interrupted: false,
    submission_id: "f1000000-0000-4000-8000-000000000005",
    engine_version: "reactiontime-clone-v1",
    locale: "zh-CN",
  }
}

function savedResult(value: Submission, score = 80): ResultView {
  return {
    ...value,
    id: "f1000000-0000-4000-8000-000000000004",
    raw_value: 200,
    unit: "ms",
    score,
    grade: "high",
    quality_flags: [],
    scoring_version: "talent-score-v1",
    created_at: "2026-09-20T00:01:00Z",
  }
}

function problemResponse(status: number, code: string): Response {
  return new Response(JSON.stringify({
    type: "about:blank",
    title: "Request failed",
    status,
    detail: "请求失败",
    code,
    request_id: `request-${status}`,
  }), { status, headers: { "Content-Type": "application/json" } })
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

it.each([[false, "network"], [true, "network"], [false, "500"], [true, "500"]] as const)(
  "recovers a first GET failure with cache=%s and error=%s before enabling play or submission",
  async (cached, error) => {
    const pending = pendingSubmission()
    if (cached) putPending(accountId, sessionId, pending, Date.now())
    let reads = 0
    const requests: RequestInit[] = []
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
      requests.push(init ?? {})
      if (++reads === 1) {
        if (error === "network") throw new TypeError("offline")
        return problemResponse(500, "server_error")
      }
      return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
    }))
    render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
    const reread = await screen.findByRole("button", { name: "重新读取本轮" })
    expect(screen.queryByText("正在加载本轮训练…")).not.toBeInTheDocument()
    expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "手动重试" })).not.toBeInTheDocument()
    await userEvent.click(reread)
    if (cached) {
      expect(await screen.findByRole("button", { name: "手动重试" })).toBeVisible()
      expect(readPending(accountId, sessionId, "reactiontime", Date.now())?.submission_id).toBe(pending.submission_id)
    } else {
      expect(await screen.findByTestId("reaction-game")).toBeVisible()
    }
    expect(requests.every((request) => !request.method || request.method === "GET")).toBe(true)
  },
)

it("does not reopen a game or submit cached results when reread belongs to another person", async () => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  let reads = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => {
    if (++reads === 1) throw new TypeError("offline")
    return new Response(JSON.stringify(session({ person_id: "another-person" })), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  await userEvent.click(await screen.findByRole("button", { name: "重新读取本轮" }))
  expect(await screen.findByText(/无法验证本轮归属/)).toBeVisible()
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "手动重试" })).not.toBeInTheDocument()
  expect(readPending(accountId, sessionId, "reactiontime", Date.now())).not.toBeNull()
})

it("shows the server-confirmed five-trial average before the score without replaying the game", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    results: [{
      id: "f1000000-0000-4000-8000-000000000004",
      submission_id: "f1000000-0000-4000-8000-000000000005",
      test_id: "reactiontime",
      raw_value: 200,
      unit: "ms",
      score: 80,
      grade: "high",
      quality_flags: [],
      raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
      device: { input_kind: "mouse", viewport_width: 1440, viewport_height: 900, geometry: null },
      duration_ms: 10_000,
      background_interrupted: false,
      engine_version: "reactiontime-clone-v1",
      scoring_version: "talent-score-v1",
      locale: "zh-CN",
      created_at: "2026-09-20T00:01:00Z",
    }],
  })), { headers: { "Content-Type": "application/json" } })))

  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  expect(await screen.findByText("服务器成绩：80 分")).toBeVisible()
  const average = screen.getByText("5 次平均响应：200 毫秒")
  expect(average).toBeVisible()
  expect(average.compareDocumentPosition(screen.getByText("服务器成绩：80 分")) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
})

it("continues to the next unsaved frozen item through a visible link", async () => {
  // jsdom has no layout observer; this case exercises navigation, not aim geometry.
  vi.stubGlobal("ResizeObserver", class {
    observe = vi.fn()
    unobserve = vi.fn()
    disconnect = vi.fn()
  })
  const pending = pendingSubmission()
  putPending(accountId, sessionId, pending, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const result = savedResult(pending)
    const response = String(input).endsWith("/results")
      ? { session: session({ results: [result] }), result }
      : session()
    return new Response(JSON.stringify(response), { headers: { "Content-Type": "application/json" } })
  }))
  function CurrentGame() {
    const { testId } = useParams()
    return <GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId={testId === "aim" ? "aim" : "reactiontime"} />
  }
  renderBase(
    <MemoryRouter initialEntries={[`/talent-tests/sessions/${sessionId}/reactiontime`]}>
      <QueryClientProvider client={new QueryClient()}>
      <Routes>
        <Route path="/talent-tests/sessions/:sessionId/:testId" element={<CurrentGame />} />
        <Route path="/talent-tests" element={<h1>项目列表</h1>} />
      </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  await userEvent.click(await screen.findByRole("button", { name: "手动重试" }))
  const next = await screen.findByRole("link", { name: "继续下一项：瞄准速度" })
  expect(next).toHaveAttribute("href", `/talent-tests/sessions/${sessionId}/aim`)
  await userEvent.click(next)
  expect(await screen.findByRole("heading", { name: "瞄准速度" })).toBeVisible()
  expect(screen.queryByText("服务器成绩：80 分")).not.toBeInTheDocument()
  expect(screen.queryByText("结果已保存。")).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: /继续下一项/ })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("link", { name: "返回项目列表" }))
  expect(await screen.findByRole("heading", { name: "项目列表" })).toBeVisible()
})

it("chooses remaining membership order rather than the route index or current catalog", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    rules: { ...rules, test_ids: ["memory", "reactiontime", "aim"] },
    results: [savedResult(pendingSubmission())],
  })), { headers: { "Content-Type": "application/json" } })))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  expect(await screen.findByRole("link", { name: "继续下一项：视觉记忆" })).toHaveAttribute(
    "href", `/talent-tests/sessions/${sessionId}/memory`,
  )
})

it.each(["completed", "abandoned"] as const)("does not offer another test for a %s round", async (status) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    status,
    completed_at: status === "completed" ? "2026-09-20T00:02:00Z" : null,
    abandoned_at: status === "abandoned" ? "2026-09-20T00:02:00Z" : null,
    report_id: status === "completed" ? "f1000000-0000-4000-8000-000000000099" : null,
    results: [savedResult(pendingSubmission())],
  })), { headers: { "Content-Type": "application/json" } })))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  await screen.findByText("服务器成绩：80 分")
  expect(screen.queryByRole("link", { name: /继续下一项/ })).not.toBeInTheDocument()
  expect(screen.getByRole("link", { name: "返回项目列表" })).toHaveAttribute("href", "/talent-tests")
  if (status === "completed") {
    expect(screen.getByRole("link", { name: "查看本轮系统评价" })).toHaveAttribute(
      "href", "/talent-tests/reports/f1000000-0000-4000-8000-000000000099",
    )
  }
})

it("does not count an unconfirmed pending result as ready to continue", async () => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session()), {
    headers: { "Content-Type": "application/json" },
  })))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  await screen.findByRole("button", { name: "手动重试" })
  expect(screen.queryByRole("link", { name: /继续下一项/ })).not.toBeInTheDocument()
  expect(screen.queryByText(/5 次平均响应/)).not.toBeInTheDocument()
  expect(screen.getByRole("link", { name: "返回项目列表" })).toBeVisible()
})

it.each(["返回项目列表", "继续下一项：瞄准速度"])("ignores a completion gesture retargeted onto %s but permits a fresh click", async (name) => {
  const pending = pendingSubmission()
  putPending(accountId, sessionId, pending, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const result = savedResult(pending)
    return new Response(JSON.stringify(String(input).endsWith("/results")
      ? { session: session({ results: [result] }), result }
      : session()), { headers: { "Content-Type": "application/json" } })
  }))
  function Path() {
    return <output aria-label="当前位置">{useLocation().pathname}</output>
  }
  render(<><GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" /><Path /></>)
  const retry = await screen.findByRole("button", { name: "手动重试" })
  fireEvent.pointerDown(retry, { pointerType: "touch", isPrimary: true })
  fireEvent.click(retry)
  await screen.findByText("服务器成绩：80 分")
  const link = screen.getByRole("link", { name })
  // Browser touch-up can retarget its click after completed content collapses.
  fireEvent.click(link, { detail: 1 })
  expect(screen.getByLabelText("当前位置")).toHaveTextContent(/^\/$/)
  await userEvent.click(link)
  expect(screen.getByLabelText("当前位置")).toHaveTextContent(
    name === "返回项目列表" ? /^\/talent-tests$/ : new RegExp(`/sessions/${sessionId}/aim$`),
  )
})

it("allows keyboard activation of result navigation without a pointer gesture", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    results: [savedResult(pendingSubmission())],
  })), { headers: { "Content-Type": "application/json" } })))
  function Path() {
    return <output aria-label="当前位置">{useLocation().pathname}</output>
  }
  render(<><GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" /><Path /></>)
  const next = await screen.findByRole("link", { name: "继续下一项：瞄准速度" })
  next.focus()
  await userEvent.keyboard("{Enter}")
  expect(screen.getByLabelText("当前位置")).toHaveTextContent(`/sessions/${sessionId}/aim`)
})

it("remounts an unfinished game after a hidden interruption", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session()), {
    headers: { "Content-Type": "application/json" },
  })))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  const first = await screen.findByTestId("reaction-game")
  fireEvent.pointerDown(first, { button: 0, isPrimary: true, pointerType: "mouse" })

  Object.defineProperty(document, "hidden", { configurable: true, value: true })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" })
  await act(async () => document.dispatchEvent(new Event("visibilitychange")))

  expect(await screen.findByText("页面切换已中止本项，请重新开始")).toBeVisible()
  await waitFor(() => expect(screen.getByTestId("reaction-game")).not.toBe(first))
  Object.defineProperty(document, "hidden", { configurable: true, value: false })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" })

  fireEvent.pointerDown(screen.getByTestId("reaction-game"), {
    button: 0,
    isPrimary: true,
    pointerType: "mouse",
  })
  expect(screen.queryByText("页面切换已中止本项，请重新开始")).not.toBeInTheDocument()
})

it("does not silently substitute an unknown engine version", async () => {
  const unknown = session({
    rules: {
      ...rules,
      tests: {
        ...rules.tests,
        reactiontime: {
          ...(rules.tests.reactiontime as TestRule),
          engine_version: "reactiontime-v2",
        },
      },
    },
  })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(unknown), {
    headers: { "Content-Type": "application/json" },
  })))

  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  expect(await screen.findByText(/规则版本暂不受支持，请联系管理员/)).toBeVisible()
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
})

it("rejects a supported route that is not a member of this legacy round", async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session()), {
    headers: { "Content-Type": "application/json" },
  }))
  vi.stubGlobal("fetch", fetchMock)

  render(
    <GameHost
      accountId={accountId}
      personId={personId}
      sessionId={sessionId}
      testId="dynamic-vision"
    />,
  )

  expect(await screen.findByRole("alert")).toHaveTextContent("本轮不包含该测试项目")
  expect(screen.queryByRole("button", { name: "开始测试" })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.every(([, init]) => !init?.method || init.method === "GET")).toBe(true)
})

it("retries a pending result with the original submission id", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  let postCount = 0
  const bodies: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith(`/talent-tests/sessions/${sessionId}`) && !url.endsWith("/results")) {
      return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/results")) {
      postCount += 1
      bodies.push(JSON.parse(String(init?.body)))
      if (postCount === 1) {
        return new Response(JSON.stringify({
          type: "about:blank", title: "Unavailable", status: 503,
          detail: "暂时不可用", code: "unavailable", request_id: "request-1",
        }), { status: 503, headers: { "Content-Type": "application/json" } })
      }
      const result = savedResult(value)
      return new Response(JSON.stringify({ session: session({ results: [result] }), result }), {
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const user = userEvent.setup()
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await user.click(await screen.findByRole("button", { name: "手动重试" }))
  expect(await screen.findByText(/保存失败，已保留原提交编号/)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "手动重试" }))

  expect(await screen.findByText("服务器成绩：80 分")).toBeVisible()
  expect(bodies).toEqual([value, value])
})

it("treats a conflict as saved when refresh finds the same submission", async () => {
  const value = pendingSubmission()
  const result = savedResult(value)
  putPending(accountId, sessionId, value, Date.now())
  let getCount = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) {
      return new Response(JSON.stringify({
        type: "about:blank", title: "Conflict", status: 409,
        detail: "冲突", code: "talent_result_conflict", request_id: "request-2",
      }), { status: 409, headers: { "Content-Type": "application/json" } })
    }
    getCount += 1
    return new Response(JSON.stringify(getCount === 1 ? session() : session({ results: [result] })), {
      headers: { "Content-Type": "application/json" },
    })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText("服务器成绩：80 分")).toBeVisible()
  expect(sessionStorage.length).toBe(0)
})

it("rejects a conflict when the project already has a different result", async () => {
  const value = pendingSubmission()
  const otherValue: Submission = {
    ...value,
    submission_id: "f1000000-0000-4000-8000-000000000006",
  }
  putPending(accountId, sessionId, value, Date.now())
  let getCount = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) {
      return new Response(JSON.stringify({
        type: "about:blank", title: "Conflict", status: 409,
        detail: "冲突", code: "talent_result_conflict", request_id: "request-4",
      }), { status: 409, headers: { "Content-Type": "application/json" } })
    }
    getCount += 1
    return new Response(JSON.stringify(
      getCount === 1 ? session() : session({ results: [savedResult(otherValue)] }),
    ), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText("该项目已有其他结果，不能重复提交。")).toBeVisible()
  expect(screen.getByText("服务器成绩：80 分")).toBeVisible()
  expect(sessionStorage.length).toBe(0)
})

it("rejects a conflict when the server says the session already ended", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  let getCount = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) {
      return new Response(JSON.stringify({
        type: "about:blank", title: "Conflict", status: 409,
        detail: "冲突", code: "talent_session_closed", request_id: "request-5",
      }), { status: 409, headers: { "Content-Type": "application/json" } })
    }
    getCount += 1
    return new Response(JSON.stringify(
      getCount === 1 ? session() : session({ status: "abandoned", abandoned_at: "2026-09-20T00:02:00Z" }),
    ), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText(/本轮已结束，不能再提交/)).toHaveTextContent("清空浏览器数据")
  expect(screen.getByText("本轮训练已结束，不能继续作答。")).toBeVisible()
})

it("counts down Retry-After without polling, then retries the same submission id", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  let postCount = 0
  const bodies: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/results")) {
      postCount += 1
      bodies.push(JSON.parse(String(init?.body)))
      if (postCount === 1) {
        return new Response(JSON.stringify({
          type: "about:blank", title: "Too Many Requests", status: 429,
          detail: "请求过于频繁", code: "rate_limited", request_id: "request-3",
        }), {
          status: 429,
          headers: { "Content-Type": "application/json", "Retry-After": "12" },
        })
      }
      const result = savedResult(value)
      return new Response(JSON.stringify({ session: session({ results: [result] }), result }), {
        headers: { "Content-Type": "application/json" },
      })
    }
    return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  const initialRetry = await screen.findByRole("button", { name: "手动重试" })
  vi.useFakeTimers()
  await act(async () => {
    fireEvent.click(initialRetry)
    await Promise.resolve()
    await Promise.resolve()
  })
  expect(screen.getByText(/请在 12 秒后手动重试/)).toBeVisible()
  expect(screen.getByRole("button", { name: "12 秒后可重试" })).toBeDisabled()
  act(() => vi.advanceTimersByTime(11_000))
  expect(screen.getByText(/请在 1 秒后手动重试/)).toBeVisible()
  expect(postCount).toBe(1)
  act(() => vi.advanceTimersByTime(1_000))
  expect(screen.getByRole("button", { name: "手动重试" })).toBeEnabled()
  expect(postCount).toBe(1)
  fireEvent.click(screen.getByRole("button", { name: "手动重试" }))

  await vi.waitFor(() => expect(screen.getByText("服务器成绩：80 分")).toBeVisible())
  expect(bodies).toEqual([value, value])
  expect(sessionStorage.length).toBe(0)
})

it("ignores a late session response from the previous account generation", async () => {
  let resolveOld: ((response: Response) => void) | undefined
  const nextSessionId = "f1000000-0000-4000-8000-000000000009"
  const nextAccountId = "f1000000-0000-4000-8000-000000000008"
  const value = pendingSubmission()
  const current = session({ id: nextSessionId, results: [savedResult(value, 90)] })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith(`/sessions/${sessionId}`)) {
      return new Promise<Response>((resolve) => { resolveOld = resolve })
    }
    if (url.endsWith(`/sessions/${nextSessionId}`)) {
      return new Response(JSON.stringify(current), { headers: { "Content-Type": "application/json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const rendered = render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  rendered.rerender(<GameHost accountId={nextAccountId} personId={personId} sessionId={nextSessionId} testId="reactiontime" />)
  expect(await screen.findByText("服务器成绩：90 分")).toBeVisible()

  await act(async () => {
    resolveOld?.(new Response(JSON.stringify(session({ results: [savedResult(value, 10)] })), {
      headers: { "Content-Type": "application/json" },
    }))
  })

  expect(screen.getByText("服务器成绩：90 分")).toBeVisible()
  expect(screen.queryByText("服务器成绩：10 分")).not.toBeInTheDocument()
})

it("does not mount or replay pending data when GET returns another person", async () => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    person_id: "f1000000-0000-4000-8000-000000000099",
  })), { headers: { "Content-Type": "application/json" } })))

  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  expect(await screen.findByText(/无法验证本轮归属/)).toBeVisible()
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "手动重试" })).not.toBeInTheDocument()
  expect(sessionStorage.length).toBe(1)
})

it("does not mount or replay pending data when GET returns another session id", async () => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    id: "f1000000-0000-4000-8000-000000000098",
  })), { headers: { "Content-Type": "application/json" } })))

  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  expect(await screen.findByText(/无法验证本轮归属/)).toBeVisible()
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "手动重试" })).not.toBeInTheDocument()
})

it("drops a 422 pending result and allows a clean new game", async () => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) return problemResponse(422, "validation_error")
    return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText("结果无效，请重新完成本项。")).toBeVisible()
  expect(screen.getByTestId("reaction-game")).toBeVisible()
  expect(sessionStorage.length).toBe(0)
})

it.each([
  [401, "invalid_session"],
  [403, "permission_denied"],
  [404, "resource_not_found"],
])("stops pending replay after private HTTP %i", async (status, code) => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) return problemResponse(status, code)
    return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText(/请重新登录并验证身份/)).toHaveTextContent("清空浏览器数据")
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "手动重试" })).not.toBeInTheDocument()
  expect(sessionStorage.length).toBe(1)
})

it("recovers a dropped success response from server state after refresh", async () => {
  const value = pendingSubmission()
  const result = savedResult(value)
  putPending(accountId, sessionId, value, Date.now())
  let serverSaved = false
  let postCount = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) {
      postCount += 1
      serverSaved = true
      throw new TypeError("connection dropped after server commit")
    }
    return new Response(JSON.stringify(session({ results: serverSaved ? [result] : [] })), {
      headers: { "Content-Type": "application/json" },
    })
  }))
  const first = render(
    <GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />,
  )
  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))
  expect(await screen.findByText(/保存失败，已保留原提交编号/)).toHaveTextContent("清空浏览器数据")
  first.unmount()

  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  expect(await screen.findByText("服务器成绩：80 分")).toBeVisible()
  expect(postCount).toBe(1)
  expect(sessionStorage.length).toBe(0)
})

it("does not lose a pending result when the page becomes hidden", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session()), {
    headers: { "Content-Type": "application/json" },
  })))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)
  expect(await screen.findByRole("button", { name: "手动重试" })).toBeEnabled()

  Object.defineProperty(document, "hidden", { configurable: true, value: true })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" })
  document.dispatchEvent(new Event("visibilitychange"))

  expect(readPending(accountId, sessionId, "reactiontime", Date.now())).toEqual(value)
  expect(screen.getByRole("button", { name: "手动重试" })).toBeEnabled()
  expect(screen.queryByText(/页面切换已中止/)).not.toBeInTheDocument()
  Object.defineProperty(document, "hidden", { configurable: true, value: false })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" })
})

it("does not auto-submit pending data under StrictMode effect replay", async () => {
  putPending(accountId, sessionId, pendingSubmission(), Date.now())
  let postCount = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    if (String(input).endsWith("/results")) postCount += 1
    return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
  }))
  render(
    <StrictMode>
      <GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />
    </StrictMode>,
  )

  expect(await screen.findByRole("button", { name: "手动重试" })).toBeEnabled()
  expect(postCount).toBe(0)
})

it("ignores a late POST response after switching accounts", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  let finishPost: ((response: Response) => void) | undefined
  const nextAccountId = "f1000000-0000-4000-8000-000000000020"
  const nextPersonId = "f1000000-0000-4000-8000-000000000021"
  const nextSessionId = "f1000000-0000-4000-8000-000000000022"
  const nextSession = session({ id: nextSessionId, person_id: nextPersonId })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/results")) {
      return new Promise<Response>((resolve) => { finishPost = resolve })
    }
    return new Response(JSON.stringify(url.endsWith(nextSessionId) ? nextSession : session()), {
      headers: { "Content-Type": "application/json" },
    })
  }))
  const rendered = render(
    <GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />,
  )
  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))
  rendered.rerender(
    <GameHost accountId={nextAccountId} personId={nextPersonId} sessionId={nextSessionId} testId="reactiontime" />,
  )
  expect(await screen.findByTestId("reaction-game")).toBeVisible()

  await act(async () => {
    const result = savedResult(value)
    finishPost?.(new Response(JSON.stringify({ session: session({ results: [result] }), result }), {
      headers: { "Content-Type": "application/json" },
    }))
  })

  expect(screen.getByTestId("reaction-game")).toBeVisible()
  expect(screen.queryByText("服务器成绩：80 分")).not.toBeInTheDocument()
})

it("does not accept a POST response for another person", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (!url.endsWith("/results")) {
      return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
    }
    const result = savedResult(value)
    return new Response(JSON.stringify({
      session: session({ person_id: "f1000000-0000-4000-8000-000000000099", results: [result] }),
      result,
    }), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText(/服务器响应无法确认本次结果/)).toHaveTextContent("清空浏览器数据")
  expect(screen.queryByText("服务器成绩：80 分")).not.toBeInTheDocument()
  expect(sessionStorage.length).toBe(1)
})

it("does not accept a POST result missing from the returned session", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    if (!String(input).endsWith("/results")) {
      return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
    }
    return new Response(JSON.stringify({ session: session(), result: savedResult(value) }), {
      headers: { "Content-Type": "application/json" },
    })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText(/服务器响应无法确认本次结果/)).toBeVisible()
  expect(screen.queryByText("服务器成绩：80 分")).not.toBeInTheDocument()
  expect(sessionStorage.length).toBe(1)
})

it("strips runtime extras before a recovered pending result reaches the network", async () => {
  const value = pendingSubmission()
  const unsafe = {
    ...value,
    answer: "secret-answer",
    email: "student@example.com",
    raw: { ...value.raw, answer: "raw-secret" },
    device: { ...value.device, token: "device-token" },
  }
  sessionStorage.setItem(
    `erp:talent-tests:pending:${accountId}:${sessionId}:reactiontime`,
    JSON.stringify({ savedAt: Date.now(), value: unsafe }),
  )
  let posted: unknown
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (!url.endsWith("/results")) {
      return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
    }
    posted = JSON.parse(String(init?.body))
    const result = savedResult(value)
    return new Response(JSON.stringify({ session: session({ results: [result] }), result }), {
      headers: { "Content-Type": "application/json" },
    })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  await userEvent.setup().click(await screen.findByRole("button", { name: "手动重试" }))

  expect(await screen.findByText("服务器成绩：80 分")).toBeVisible()
  expect(posted).toEqual(value)
  expect(JSON.stringify(posted)).not.toContain("secret")
  expect(JSON.stringify(posted)).not.toContain("student@example.com")
  expect(JSON.stringify(posted)).not.toContain("device-token")
})

it("submits dynamic vision with the explicit engine version and four-key raw payload", async () => {
  const dynamicRules: RuleSnapshot = {
    ...rules,
    catalog_version: "talent-training-v2",
    scoring_version: "talent-score-v2",
    report_version: "talent-report-v2",
    test_ids: [...rules.test_ids, "dynamic-vision"],
    tests: {
      ...rules.tests,
      "dynamic-vision": {
        kind: "dynamic_vision",
        engine_version: "dynamic-vision-v1",
        source_label: "内部试行训练规则",
        unit: "count",
        base_points_per_correct: 10,
        levels: [
          { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
          { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
          { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
          { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
          { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
        ],
      },
    },
  }
  const completed: CompletedGame = {
    test_id: "dynamic-vision",
    raw: {
      selected_level: 4,
      outcomes: ["correct", "correct", "incorrect", "skipped", "correct"],
      measured_display_ms: [36, 36, 36, 36, 36],
      font_size_px: 56,
    },
    device: {
      input_kind: "touch",
      viewport_width: 1280,
      viewport_height: 720,
      geometry: { width: 800, height: 420, target_diameter: null },
    },
    duration_ms: 13_000,
    background_interrupted: false,
  }
  const submissionId = "f1000000-0000-4000-8000-000000000088"
  vi.spyOn(crypto, "randomUUID").mockReturnValue(submissionId)
  let posted: Submission | undefined
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const dynamicSession = session({ rules: dynamicRules })
    if (!url.endsWith("/results")) {
      return new Response(JSON.stringify(dynamicSession), { headers: { "Content-Type": "application/json" } })
    }
    posted = JSON.parse(String(init?.body)) as Submission
    const result: ResultView = {
      ...posted,
      id: "f1000000-0000-4000-8000-000000000089",
      raw_value: 3,
      unit: "count",
      score: 54,
      grade: "medium",
      quality_flags: [],
      scoring_version: "talent-score-v2",
      created_at: "2026-09-20T00:01:00Z",
    }
    return new Response(JSON.stringify({
      session: session({ rules: dynamicRules, results: [result] }),
      result,
    }), { headers: { "Content-Type": "application/json" } })
  }))
  const hook = renderHook(() => useTalentSession(accountId, personId, sessionId))
  await waitFor(() => expect(hook.result.current.session).not.toBeNull())

  await act(async () => hook.result.current.submit(completed))

  expect(posted).toEqual({
    ...completed,
    submission_id: submissionId,
    engine_version: "dynamic-vision-v1",
    locale: "zh-CN",
  })
  expect(hook.result.current.saveState).toBe("saved")
  expect(sessionStorage.length).toBe(0)
})

it("announces a completed session once for its server report id", async () => {
  const onSessionCompleted = vi.fn()
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(session({
    status: "completed",
    completed_at: "2026-09-20T00:02:00Z",
    report_id: "f1000000-0000-4000-8000-000000000099",
  })), { headers: { "Content-Type": "application/json" } })))

  const rendered = render(
    <StrictMode>
      <GameHost
        accountId={accountId}
        personId={personId}
        sessionId={sessionId}
        testId="reactiontime"
        onSessionCompleted={onSessionCompleted}
      />
    </StrictMode>,
  )

  await waitFor(() => expect(onSessionCompleted).toHaveBeenCalledWith(
    "f1000000-0000-4000-8000-000000000099",
  ))
  expect(onSessionCompleted).toHaveBeenCalledTimes(1)

  rendered.rerender(
    <StrictMode>
      <GameHost
        accountId={accountId}
        personId={personId}
        sessionId={sessionId}
        testId="reactiontime"
        onSessionCompleted={onSessionCompleted}
      />
    </StrictMode>,
  )
  expect(onSessionCompleted).toHaveBeenCalledTimes(1)
})

it.each([
  ["completed", {
    status: "completed",
    completed_at: "2026-09-20T00:02:00Z",
    report_id: "f1000000-0000-4000-8000-000000000099",
  }],
  ["abandoned", {
    status: "abandoned",
    abandoned_at: "2026-09-20T00:02:00Z",
    report_id: null,
  }],
])("warns that an unmatched pending result remains after a %s GET", async (_name, terminal) => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    ...session(),
    ...terminal,
  }), { headers: { "Content-Type": "application/json" } })))

  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />)

  expect(await screen.findByText(/本轮已结束，不能处理待提交结果/)).toHaveTextContent("清空浏览器数据")
  expect(readPending(accountId, sessionId, "reactiontime", Date.now())).toEqual(value)
  expect(screen.queryByRole("button", { name: "手动重试" })).not.toBeInTheDocument()
})

it.each([
  ["unknown status", { status: "finished" }],
  ["malformed completed report id", {
    status: "completed",
    completed_at: "2026-09-20T00:02:00Z",
    report_id: "../not-a-report-id",
  }],
  ["report id on an in-progress session", {
    report_id: "f1000000-0000-4000-8000-000000000099",
  }],
  ["missing completed timestamp", {
    status: "completed",
    completed_at: null,
    report_id: "f1000000-0000-4000-8000-000000000099",
  }],
])("rejects %s without publishing it or announcing a report", async (_name, invalid) => {
  const onSessionCompleted = vi.fn()
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    ...session(),
    ...invalid,
  }), { headers: { "Content-Type": "application/json" } })))

  render(
    <GameHost
      accountId={accountId}
      personId={personId}
      sessionId={sessionId}
      testId="reactiontime"
      onSessionCompleted={onSessionCompleted}
    />,
  )

  expect(await screen.findByText(/无法验证本轮归属/)).toBeVisible()
  expect(screen.queryByTestId("reaction-game")).not.toBeInTheDocument()
  expect(onSessionCompleted).not.toHaveBeenCalled()
})

it("warns about pending-data loss when abandon is not confirmed", async () => {
  const value = pendingSubmission()
  putPending(accountId, sessionId, value, Date.now())
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    if (String(input).endsWith("/abandon")) return problemResponse(503, "unavailable")
    return new Response(JSON.stringify(session()), { headers: { "Content-Type": "application/json" } })
  }))
  const hook = renderHook(() => useTalentSession(accountId, personId, sessionId))
  await waitFor(() => expect(hook.result.current.session).not.toBeNull())

  let caught: unknown
  await act(async () => {
    try {
      await hook.result.current.abandon()
    } catch (error) {
      caught = error
    }
  })

  expect(caught).toBeInstanceOf(Error)
  expect(hook.result.current.statusText).toContain("清空浏览器数据")
  expect(readPending(accountId, sessionId, "reactiontime", Date.now())).toEqual(value)
})
