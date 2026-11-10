import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, useLocation } from "react-router-dom"
import { readFileSync } from "node:fs"
import { afterEach, expect, it, vi } from "vitest"

import type { ContextView, ReportSummary, SessionView } from "./contracts"
import { getOrCreateTalentSessionRequest } from "./pending"
import { TalentTestsPage } from "./TalentTestsPage"

const context: ContextView = {
  catalog: {
    catalog_version: "talent-training-v1",
    scoring_version: "talent-score-v1",
    report_version: "talent-report-v1",
    test_ids: [
      "reactiontime",
      "aim",
      "sequence",
      "number-memory",
      "verbal-memory",
      "chimp",
      "memory",
    ],
    tests: {
      reactiontime: { engine_version: "reactiontime-clone-v1", anchors: [500, 275, 225, 175, 150, 100], source_label: "公开训练规则", unit: "ms" },
      aim: { engine_version: "aim-clone-v1", anchors: [1000, 550, 450, 350, 300, 250], source_label: "公开训练规则", unit: "ms" },
      sequence: { engine_version: "sequence-clone-v1", anchors: [0, 8, 10, 18, 25, 35], source_label: "公开训练规则", unit: "level" },
      "number-memory": { engine_version: "number-memory-clone-v1", anchors: [0, 7, 9, 13, 16, 20], source_label: "公开训练规则", unit: "digits" },
      "verbal-memory": { engine_version: "verbal-memory-clone-v1", anchors: [0, 20, 50, 100, 200, 300], source_label: "内部试行训练目标", unit: "count" },
      chimp: { engine_version: "chimp-clone-v1", anchors: [0, 9, 11, 15, 20, 25], source_label: "公开训练规则", unit: "count" },
      memory: { engine_version: "memory-clone-v1", anchors: [0, 6, 10, 18, 25, 35], source_label: "内部试行训练目标", unit: "level" },
    },
  },
  can_take: true,
  can_read_self: true,
  can_read_reports: true,
  active_session_id: "f1000000-0000-4000-8000-000000000201",
}

const activeSession: SessionView = {
  id: "f1000000-0000-4000-8000-000000000201",
  person_id: "person-self",
  status: "in_progress",
  created_at: "2026-09-20T01:00:00Z",
  completed_at: null,
  abandoned_at: null,
  rules: context.catalog,
  results: [
    {
      id: "f1000000-0000-4000-8000-000000000202",
      submission_id: "f1000000-0000-4000-8000-000000000203",
      created_at: "2026-09-20T01:01:00Z",
      test_id: "reactiontime",
      raw_value: 200,
      unit: "ms",
      score: 60,
      grade: "medium",
      quality_flags: [],
      raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
      device: { input_kind: "mouse", viewport_width: 1280, viewport_height: 720, geometry: null },
      duration_ms: 3000,
      background_interrupted: false,
      engine_version: "reactiontime-clone-v1",
      scoring_version: "talent-score-v1",
      locale: "zh-CN",
    },
  ],
  report_id: null,
}

const newCatalog: ContextView["catalog"] = {
  ...context.catalog,
  catalog_version: "talent-training-v2", scoring_version: "talent-score-v2", report_version: "talent-report-v2",
  test_ids: [...context.catalog.test_ids, "dynamic-vision"],
  tests: {
    ...context.catalog.tests,
    "dynamic-vision": {
      kind: "dynamic_vision", engine_version: "dynamic-vision-v1", source_label: "内部试行训练规则",
      unit: "count", base_points_per_correct: 10,
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

function renderCreationPage() {
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentTestsPage accountId="account-first-choice" personId="person-self" />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

it('keeps ReactionX a separate specialty entry without adding a ninth round item', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ ...context, catalog: newCatalog, active_session_id: null, can_read_reports: false })))
  renderCreationPage()
  expect(await screen.findByRole('link', { name: '进入实体灯专项' })).toHaveAttribute('href', '/talent-tests/reactionx')
  const round = screen.getByRole('region', { name: '本人测试轮次' })
  expect(within(round).getAllByRole('button', { name: /^选择/ })).toHaveLength(8)
  expect(within(round).queryByText('实体灯专项')).not.toBeInTheDocument()
})

it.each([
  ["reactiontime", "反应速度"], ["aim", "瞄准速度"], ["sequence", "顺序记忆"],
  ["number-memory", "数字记忆"], ["verbal-memory", "中文词语记忆"],
  ["chimp", "黑猩猩测试"], ["memory", "视觉记忆"], ["dynamic-vision", "动态视力"],
])("creates a round and opens %s as the selected first test", async (testId, name) => {
  const bodies: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/context")) {
      return json({ ...context, catalog: newCatalog, active_session_id: null, can_read_reports: false })
    }
    if (String(input).endsWith("/sessions") && init?.method === "POST") {
      bodies.push(JSON.parse(String(init.body)))
      return json({ ...activeSession, rules: newCatalog, results: [] })
    }
    throw new Error("Unexpected creation request")
  }))
  renderCreationPage()
  await userEvent.click(await screen.findByRole("button", { name: `选择${name}` }))

  await waitFor(() => expect(screen.getByTestId("location")).toHaveTextContent(
    `/talent-tests/sessions/${activeSession.id}/${testId}`,
  ))
  expect(bodies).toEqual([{ request_id: expect.any(String), catalog_version: "talent-training-v2" }])
})

it("disables every first-test choice during creation and retries the preferred test with the same request", async () => {
  const bodies: unknown[] = []
  let rejectFirst!: (reason: Error) => void
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/context")) {
      return json({ ...context, catalog: newCatalog, active_session_id: null, can_read_reports: false })
    }
    bodies.push(JSON.parse(String(init?.body)))
    if (bodies.length === 1) return new Promise<Response>((_resolve, reject) => { rejectFirst = reject })
    return json({ ...activeSession, rules: newCatalog, results: [] })
  }))
  renderCreationPage()
  await userEvent.click(await screen.findByRole("button", { name: "选择动态视力" }))
  for (const button of screen.getAllByRole("button", { name: /^选择/ })) expect(button).toBeDisabled()
  expect(screen.getByRole("button", { name: "正在创建…" })).toBeDisabled()
  await act(async () => { rejectFirst(new TypeError("lost response")) })
  expect(await screen.findByRole("alert")).toHaveTextContent("保留本次请求编号后重试")
  await userEvent.click(screen.getByRole("button", { name: "选择动态视力" }))

  expect(await screen.findByTestId("location")).toHaveTextContent(`/talent-tests/sessions/${activeSession.id}/dynamic-vision`)
  expect(bodies).toHaveLength(2)
  expect(bodies[1]).toEqual(bodies[0])
})

it.each(["动态视力", "反应速度"])("uses the replayed round's frozen remaining tests when selected %s is absent or saved", async (name) => {
  const request = getOrCreateTalentSessionRequest("account-first-choice", "talent-training-v1")
  let posted: unknown
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/context")) {
      return json({ ...context, catalog: newCatalog, active_session_id: null, can_read_reports: false })
    }
    posted = JSON.parse(String(init?.body))
    return json(activeSession)
  }))
  renderCreationPage()
  await userEvent.click(await screen.findByRole("button", { name: `选择${name}` }))

  expect(await screen.findByTestId("location")).toHaveTextContent(`/talent-tests/sessions/${activeSession.id}/aim`)
  expect(posted).toEqual({ request_id: request.request_id, catalog_version: "talent-training-v1" })
})

it("does not navigate the preferred test after identity changes while refreshing creation context", async () => {
  let contextReads = 0
  let resolveRefresh!: (response: Response) => void
  const refreshResponse = new Promise<Response>((resolve) => { resolveRefresh = resolve })
  const inactive = { ...context, catalog: newCatalog, active_session_id: null, can_read_reports: false }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/context")) {
      if (++contextReads === 2) return refreshResponse
      return json(inactive)
    }
    if (String(input).endsWith("/sessions") && init?.method === "POST") {
      return json({ ...activeSession, rules: newCatalog, results: [] })
    }
    throw new Error("Unexpected identity-scoped request")
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const content = (accountId: string, personId: string) => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TalentTestsPage accountId={accountId} personId={personId} />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>
  )
  const view = render(content("account-first-choice", "person-self"))
  await userEvent.click(await screen.findByRole("button", { name: "选择瞄准速度" }))
  await waitFor(() => expect(contextReads).toBe(2))
  view.rerender(content("account-new", "person-new"))
  await screen.findByRole("button", { name: "选择动态视力" })
  await act(async () => { resolveRefresh(json(inactive)); await refreshResponse })

  expect(screen.getByTestId("location")).toHaveTextContent(/^\/$/)
})

const report: ReportSummary = {
  id: "report-readable",
  session_id: "session-readable",
  person: { id: "person-readable", name: "授权学员", nickname: "小授权" },
  completed_at: "2026-09-19T16:30:00Z",
  catalog_version: "talent-training-v1",
  scoring_version: "talent-score-v1",
}

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

function renderPage(options: {
  readonlyMode?: boolean
  inactive?: boolean
  reports?: ReportSummary[]
} = {}) {
  const effectiveContext = options.readonlyMode
    ? { ...context, can_take: false, can_read_self: false, active_session_id: null }
    : options.inactive
      ? { ...context, active_session_id: null }
      : context
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/talent-tests/context")) return json(effectiveContext)
    if (url.endsWith("/talent-tests/sessions/f1000000-0000-4000-8000-000000000201")) return json(activeSession)
    if (url.includes("/talent-tests/subjects?")) {
      return json({ items: options.reports?.length ? [report.person] : [], total: options.reports?.length ?? 0, page: 1, page_size: 20 })
    }
    if (url.includes("/talent-tests/reports?")) {
      return json({ items: options.reports ?? [], total: options.reports?.length ?? 0, page: 1, page_size: 20 })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  return render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentTestsPage accountId="account-self" personId="person-self" />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

it("按服务端目录顺序显示七项并只用已保存结果计算进度", async () => {
  renderPage()

  expect(await screen.findByRole("heading", { name: "天赋测试" })).toBeVisible()
  expect(await screen.findByText("1/7")).toBeVisible()
  expect(within(screen.getByRole('region', { name: '本人测试轮次' })).getAllByRole("heading", { level: 2 }).map((item) => item.textContent)).toEqual([
    "反应速度",
    "瞄准速度",
    "顺序记忆",
    "数字记忆",
    "中文词语记忆",
    "黑猩猩测试",
    "视觉记忆",
  ])
  expect(screen.getByRole("link", { name: "继续瞄准速度" })).toHaveAttribute(
    "href",
    "/talent-tests/sessions/f1000000-0000-4000-8000-000000000201/aim",
  )
  for (const name of ["瞄准速度", "顺序记忆", "数字记忆", "中文词语记忆", "黑猩猩测试", "视觉记忆"]) {
    expect(screen.getByRole("link", { name: `选择${name}` })).toBeVisible()
  }
  expect(screen.queryByText(/中文打字/)).not.toBeInTheDocument()
  expect(screen.queryByText(/全部完成/)).not.toBeInTheDocument()
})

it("首次或两轮之间先显示新一轮的七项目录与零进度", async () => {
  renderPage({ inactive: true })

  const session = await screen.findByRole("region", { name: "本人测试轮次" })
  expect(within(session).getByText("0/7")).toBeVisible()
  expect(within(session).getAllByRole("heading", { level: 2 }).map((item) => item.textContent)).toEqual([
    "反应速度",
    "瞄准速度",
    "顺序记忆",
    "数字记忆",
    "中文词语记忆",
    "黑猩猩测试",
    "视觉记忆",
  ])
  expect(within(session).getByRole("button", { name: "开始新一轮" })).toBeVisible()
  expect(within(session).queryByRole("link", { name: /选择|继续/ })).not.toBeInTheDocument()
})

it("uses the frozen v2 catalog order and its actual eight-item progress", async () => {
  const dynamicRule = {
    kind: "dynamic_vision" as const,
    engine_version: "dynamic-vision-v1" as const,
    source_label: "内部试行训练规则" as const,
    unit: "count" as const,
    base_points_per_correct: 10 as const,
    levels: [
      { level: 1 as const, digit_count: 3 as const, nominal_display_ms: 100 as const, coefficient_tenths: 12 as const },
      { level: 2 as const, digit_count: 4 as const, nominal_display_ms: 100 as const, coefficient_tenths: 14 as const },
      { level: 3 as const, digit_count: 5 as const, nominal_display_ms: 100 as const, coefficient_tenths: 16 as const },
      { level: 4 as const, digit_count: 5 as const, nominal_display_ms: 36 as const, coefficient_tenths: 18 as const },
      { level: 5 as const, digit_count: 6 as const, nominal_display_ms: 36 as const, coefficient_tenths: 20 as const },
    ],
  }
  const v2Catalog = {
    ...context.catalog,
    catalog_version: "talent-training-v2",
    scoring_version: "talent-score-v2",
    report_version: "talent-report-v2",
    test_ids: [...context.catalog.test_ids, "dynamic-vision" as const],
    tests: { ...context.catalog.tests, "dynamic-vision": dynamicRule },
  }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/talent-tests/context")) {
      return json({ ...context, catalog: v2Catalog, active_session_id: activeSession.id })
    }
    if (url.endsWith(`/talent-tests/sessions/${activeSession.id}`)) {
      return json({ ...activeSession, rules: v2Catalog })
    }
    if (url.includes("/talent-tests/subjects?")) return json({ items: [], total: 0, page: 1, page_size: 20 })
    if (url.includes("/talent-tests/reports?")) return json({ items: [], total: 0, page: 1, page_size: 20 })
    throw new Error(`Unexpected request: ${url}`)
  }))
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentTestsPage accountId="account-v2" personId="person-self" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("1/8")).toBeVisible()
  expect(screen.getByRole("heading", { name: "动态视力" })).toBeVisible()
  expect(screen.getByText("瞬时数字识别")).toBeVisible()
  expect(screen.getByRole("link", { name: "选择动态视力" })).toHaveAttribute(
    "href",
    `/talent-tests/sessions/${activeSession.id}/dynamic-vision`,
  )
  expect(screen.getByText(/八项训练会保存在同一轮中/)).toBeVisible()
})

it("uses neutral introduction copy until a v1 active round loads under a v2 context", async () => {
  const dynamicRule = {
    kind: "dynamic_vision" as const,
    engine_version: "dynamic-vision-v1" as const,
    source_label: "内部试行训练规则" as const,
    unit: "count" as const,
    base_points_per_correct: 10 as const,
    levels: [
      { level: 1 as const, digit_count: 3 as const, nominal_display_ms: 100 as const, coefficient_tenths: 12 as const },
      { level: 2 as const, digit_count: 4 as const, nominal_display_ms: 100 as const, coefficient_tenths: 14 as const },
      { level: 3 as const, digit_count: 5 as const, nominal_display_ms: 100 as const, coefficient_tenths: 16 as const },
      { level: 4 as const, digit_count: 5 as const, nominal_display_ms: 36 as const, coefficient_tenths: 18 as const },
      { level: 5 as const, digit_count: 6 as const, nominal_display_ms: 36 as const, coefficient_tenths: 20 as const },
    ],
  }
  const v2Catalog = {
    ...context.catalog,
    catalog_version: "talent-training-v2",
    scoring_version: "talent-score-v2",
    report_version: "talent-report-v2",
    test_ids: [...context.catalog.test_ids, "dynamic-vision" as const],
    tests: { ...context.catalog.tests, "dynamic-vision": dynamicRule },
  }
  let resolveSession!: (response: Response) => void
  const sessionResponse = new Promise<Response>((resolve) => { resolveSession = resolve })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/talent-tests/context")) {
      return json({ ...context, catalog: v2Catalog, can_read_reports: false })
    }
    if (url.endsWith(`/talent-tests/sessions/${activeSession.id}`)) return sessionResponse
    throw new Error(`Unexpected request: ${url}`)
  }))
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentTestsPage accountId="account-mixed-version" personId="person-self" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("本轮训练项目会保存在同一轮中；结果只用于训练参考。")).toBeVisible()
  expect(screen.queryByText(/七项训练会保存在同一轮中|八项训练会保存在同一轮中/)).not.toBeInTheDocument()

  await act(async () => {
    resolveSession(json(activeSession))
    await sessionResponse
  })
  expect(await screen.findByText(/七项训练会保存在同一轮中/)).toBeVisible()
  expect(screen.queryByText(/八项训练会保存在同一轮中/)).not.toBeInTheDocument()
})

it("放弃当前轮次前要求明确确认", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "放弃本轮" }))
  const dialog = screen.getByRole("dialog", { name: "确认放弃本轮" })
  expect(within(dialog).getByText(/已保存的结果会保留/)).toBeVisible()
  expect(within(dialog).getByRole("button", { name: "确认放弃" })).toBeVisible()
})

it("只读监护人或教练只看到授权人员和报告，不渲染游戏操作", async () => {
  renderPage({ readonlyMode: true, reports: [report] })

  expect(await screen.findAllByText("授权学员（小授权）")).toHaveLength(2)
  expect(screen.getByRole("link", { name: "查看授权学员的报告" })).toHaveAttribute(
    "href",
    "/talent-tests/reports/report-readable",
  )
  expect(screen.queryByRole("button", { name: "开始新一轮" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /^选择/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "放弃本轮" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: /继续/ })).not.toBeInTheDocument()
})

it("可搜索并翻页选择超过二十人的授权对象，且报告独立按服务端总数翻页", async () => {
  const calls: string[] = []
  const olderPerson = { id: "person-older", name: "旧授权学员", nickname: null }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    calls.push(url)
    if (url.endsWith("/talent-tests/context")) {
      return json({ ...context, can_take: false, can_read_self: false, active_session_id: null })
    }
    const parsed = new URL(url, "http://localhost")
    if (parsed.pathname.endsWith("/talent-tests/subjects")) {
      const page = Number(parsed.searchParams.get("page"))
      const name = parsed.searchParams.get("name")
      return json({
        items: page === 2 || name === "旧授权" ? [olderPerson] : [report.person],
        total: 21,
        page,
        page_size: 20,
      })
    }
    if (parsed.pathname.endsWith("/talent-tests/reports")) {
      const page = Number(parsed.searchParams.get("page"))
      const personId = parsed.searchParams.get("person_id")
      const person = personId === olderPerson.id ? olderPerson : report.person
      return json({
        items: [{ ...report, id: `${person.id}-report-${page}`, person }],
        total: 21,
        page,
        page_size: 20,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentTestsPage accountId="account-reader" personId="person-reader" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findAllByText("授权学员（小授权）")).toHaveLength(2)
  await user.click(screen.getByRole("button", { name: "下一页授权人员" }))
  expect(await screen.findByText("旧授权学员")).toBeVisible()
  expect(calls.some((url) => url.includes("/subjects?") && url.includes("page=2"))).toBe(true)

  await user.type(screen.getByRole("searchbox", { name: "搜索授权人员" }), " 旧授权 ")
  await user.click(screen.getByRole("button", { name: "搜索人员" }))
  expect(calls.some((url) => url.includes("name=%E6%97%A7%E6%8E%88%E6%9D%83"))).toBe(true)

  await user.click(screen.getByRole("button", { name: "筛选旧授权学员的报告" }))
  expect(await screen.findByRole("link", { name: "查看旧授权学员的报告" })).toHaveAttribute(
    "href",
    "/talent-tests/reports/person-older-report-1",
  )
  await user.click(screen.getByRole("button", { name: "下一页报告" }))
  expect(await screen.findByRole("link", { name: "查看旧授权学员的报告" })).toHaveAttribute(
    "href",
    "/talent-tests/reports/person-older-report-2",
  )
  expect(calls.some((url) => url.includes("person_id=person-older") && url.includes("page=2"))).toBe(true)
})

it("授权人员或报告读取失败时显示明确错误并可分别重试", async () => {
  let subjectAttempts = 0
  let reportAttempts = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/talent-tests/context")) {
      return json({ ...context, can_take: false, can_read_self: false, active_session_id: null })
    }
    if (url.includes("/talent-tests/subjects?")) {
      subjectAttempts += 1
      return subjectAttempts === 1
        ? problem(500, "人员读取失败")
        : json({ items: [report.person], total: 1, page: 1, page_size: 20 })
    }
    if (url.includes("/talent-tests/reports?")) {
      reportAttempts += 1
      return reportAttempts === 1
        ? problem(500, "报告读取失败")
        : json({ items: [report], total: 1, page: 1, page_size: 20 })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentTestsPage accountId="account-reader" personId="person-reader" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("alert", { name: "授权人员读取失败" })).toBeVisible()
  expect(screen.getByRole("alert", { name: "授权报告读取失败" })).toBeVisible()
  expect(screen.queryByText("暂无可查看的天赋测试报告")).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "重试人员列表" }))
  expect(await screen.findByRole("button", { name: "筛选授权学员的报告" })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重试报告列表" }))
  expect(await screen.findByRole("link", { name: "查看授权学员的报告" })).toBeVisible()
})

it("没有可读报告时显示空态且没有伪游戏操作按钮", async () => {
  renderPage({ readonlyMode: true, reports: [] })

  expect(await screen.findByText("暂无可查看的天赋测试报告")).toBeVisible()
  expect(screen.queryByRole("button", { name: "开始新一轮" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "放弃本轮" })).not.toBeInTheDocument()
})

it("在 390 与 360 像素宽度保持单列且不制造横向溢出", () => {
  const css = readFileSync("src/talentTests/talentTests.css", "utf8")

  expect(css).toMatch(/\.talent-page\s*\{[^}]*min-width:\s*0;/s)
  expect(css).toMatch(/\.talent-catalog-grid\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(min\(100%,\s*15rem\),\s*1fr\)\);/s)
  expect(css).toMatch(/\.talent-report-table-wrap\s*\{[^}]*overflow-x:\s*auto;/s)
  expect(css).toMatch(/@media\s*\(max-width:\s*390px\)/)
})

it("忽略切换账号后才返回的旧账号创建响应", async () => {
  let resolveCreate!: (response: Response) => void
  const createResponse = new Promise<Response>((resolve) => { resolveCreate = resolve })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/talent-tests/context")) {
      return json({ ...context, active_session_id: null, can_read_reports: false })
    }
    if (url.endsWith("/talent-tests/sessions") && init?.method === "POST") return createResponse
    throw new Error(`Unexpected request: ${url}`)
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TalentTestsPage accountId="account-old" personId="person-old" />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "开始新一轮" }))

  view.rerender(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TalentTestsPage accountId="account-new" personId="person-new" />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  await act(async () => {
    resolveCreate(json({
      ...activeSession,
      id: "f1000000-0000-4000-8000-000000000155",
      person_id: "unexpected-old-person",
      results: [],
    }))
    await createResponse
  })
  expect(screen.getByTestId("location")).toHaveTextContent("/")
  expect(screen.queryByText(/服务器响应无法确认新轮次/)).not.toBeInTheDocument()
})

it("shows the server catalog-conflict prompt and only changes the create payload after explicit reread", async () => {
  const dynamicRule = {
    kind: "dynamic_vision" as const,
    engine_version: "dynamic-vision-v1" as const,
    source_label: "内部试行训练规则" as const,
    unit: "count" as const,
    base_points_per_correct: 10 as const,
    levels: [
      { level: 1 as const, digit_count: 3 as const, nominal_display_ms: 100 as const, coefficient_tenths: 12 as const },
      { level: 2 as const, digit_count: 4 as const, nominal_display_ms: 100 as const, coefficient_tenths: 14 as const },
      { level: 3 as const, digit_count: 5 as const, nominal_display_ms: 100 as const, coefficient_tenths: 16 as const },
      { level: 4 as const, digit_count: 5 as const, nominal_display_ms: 36 as const, coefficient_tenths: 18 as const },
      { level: 5 as const, digit_count: 6 as const, nominal_display_ms: 36 as const, coefficient_tenths: 20 as const },
    ],
  }
  const v2Catalog = {
    ...context.catalog,
    catalog_version: "talent-training-v2",
    scoring_version: "talent-score-v2",
    report_version: "talent-report-v2",
    test_ids: [...context.catalog.test_ids, "dynamic-vision" as const],
    tests: { ...context.catalog.tests, "dynamic-vision": dynamicRule },
  }
  let contextReads = 0
  const createBodies: Array<{ request_id: string; catalog_version: string }> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/talent-tests/context")) {
      contextReads += 1
      return json({
        ...context,
        catalog: contextReads === 1 ? context.catalog : v2Catalog,
        active_session_id: null,
        can_read_reports: false,
      })
    }
    if (url.endsWith("/talent-tests/sessions") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { request_id: string; catalog_version: string }
      createBodies.push(body)
      if (createBodies.length === 1) {
        return problem(409, "训练目录已更新，请重新读取目录后开始新一轮。")
      }
      return json({
        ...activeSession,
        id: "f1000000-0000-4000-8000-000000000188",
        rules: v2Catalog,
        results: [],
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TalentTestsPage accountId="account-upgrade" personId="person-self" />
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "开始新一轮" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "训练目录已更新，请重新读取目录后开始新一轮。",
  )
  expect(createBodies).toHaveLength(1)
  expect(createBodies[0]?.catalog_version).toBe("talent-training-v1")
  expect(screen.getByRole("button", { name: "重新读取目录" })).toBeVisible()

  await user.click(screen.getByRole("button", { name: "重新读取目录" }))
  expect(await screen.findByText("0/8")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "开始新一轮" }))

  expect(createBodies).toHaveLength(2)
  expect(createBodies[1]?.catalog_version).toBe("talent-training-v2")
  expect(createBodies[1]?.request_id).not.toBe(createBodies[0]?.request_id)
  expect(await screen.findByTestId("location")).toHaveTextContent(
    "/talent-tests/sessions/f1000000-0000-4000-8000-000000000188/reactiontime",
  )
})

function LocationProbe() {
  return <span data-testid="location">{useLocation().pathname}</span>
}

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function problem(status: number, detail: string): Response {
  return new Response(JSON.stringify({
    type: "about:blank",
    title: "Request failed",
    status,
    detail,
    code: "request_failed",
    request_id: "talent-page-test",
  }), {
    status,
    headers: { "Content-Type": "application/problem+json" },
  })
}
