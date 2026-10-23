import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  useLocation,
} from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { evaluationKeys } from "./api"
import { evaluationFoundationKeys } from "./foundationApi"
import { GameAccountsPage } from "./GameAccountsPage"

const accountId = "10000000-0000-4000-8000-000000000001"
const selfId = "20000000-0000-4000-8000-000000000001"
const familyId = "20000000-0000-4000-8000-000000000002"
const projectId = "30000000-0000-4000-8000-000000000001"
const activeAccountId = "40000000-0000-4000-8000-000000000001"
const selfTarget = {
  student_person_id: selfId,
  student_name: "本人学员",
  student_number: "S-SELF-001",
  access_source: "self" as const,
  active_count: 1,
  active_limit: 3,
}

const familyTarget = {
  student_person_id: familyId,
  student_name: "家庭学员",
  student_number: "S-FAMILY-002",
  access_source: "family" as const,
  active_count: 2,
  active_limit: 4,
}

const activeAccount = {
  id: activeAccountId,
  project_id: projectId,
  project_name: "英雄联盟",
  game_id: "Authorized#2026",
  status: "active" as const,
  valid_from: "2026-08-01T09:30:00+08:00",
  unbound_at: null,
  unbind_reason: null,
}

const unboundAccount = {
  ...activeAccount,
  id: "40000000-0000-4000-8000-000000000002",
  game_id: "Historical#2025",
  status: "unbound" as const,
  valid_from: "2025-08-01T09:30:00+08:00",
  unbound_at: "2025-08-22T18:00:00+08:00",
  unbind_reason: "旧账号停用",
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": status >= 400 ? "application/problem+json" : "application/json" },
  })
}

function problemResponse(code: string, detail: string, status = 409): Response {
  return jsonResponse(
    {
      type: "about:blank",
      title: status === 403 ? "Forbidden" : "Conflict",
      status,
      detail,
      code,
      request_id: `game-account-${code}`,
    },
    status,
  )
}

function defaultFetch() {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return jsonResponse({
        account_id: accountId,
        person_id: selfId,
        full_name: "本人学员",
        email: "game-account@example.test",
        force_password_change: false,
        permissions: ["evaluation_center.access"],
        scoped_permissions: [],
      })
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "game-account-csrf" })
    }
    if (url.endsWith("/evaluations/context")) {
      return jsonResponse({
        accessible: true,
        system_evaluation_available: true,
        embedded_start_url: "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
        standalone_start_url:
          "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
        upload_available: false,
        video_available: false,
        reports_available: false,
        surveys_available: false,
        survey_sections: {
          respondent: false,
          staff_feedback: false,
          administration: false,
        },
        game_accounts_available: true,
        admin_available: false,
        admin_sections: {
          cohort_staff: false,
          student_matching: false,
          analysis_profiles: false,
          system_tasks: false,
          adapter_certifications: false,
        },
        integration_api_version: "v1",
      })
    }
    if (url.endsWith("/evaluations/game-account-targets")) {
      return jsonResponse([selfTarget, familyTarget])
    }
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`)) {
      return jsonResponse([activeAccount, unboundAccount])
    }
    if (url.includes(`/evaluations/students/${familyId}/game-accounts?`)) {
      return jsonResponse([
        { ...activeAccount, id: "family-game-1", game_id: "Family#One" },
        { ...activeAccount, id: "family-game-2", game_id: "Family#Two" },
      ])
    }
    if (url.includes("/game-account-projects?")) {
      return jsonResponse({
        items: [
          {
            id: projectId,
            name: "英雄联盟",
            is_active: true,
          },
        ],
        page: 1,
        page_size: 100,
        total: 1,
      })
    }
    if (url.includes("/game-accounts") && init?.method === "POST") {
      return jsonResponse(activeAccount, 201)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderPage(fetchMock = defaultFetch()) {
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const router = createMemoryRouter(
    [
      {
        element: (
          <>
            <Outlet />
            <LocationProbe />
          </>
        ),
        children: [
          { path: "/", element: <p>ERP 工作台</p> },
          { path: "/evaluations", element: <p>评价中心落点</p> },
          { path: "/evaluations/game-accounts", element: <GameAccountsPage /> },
        ],
      },
    ],
    { initialEntries: ["/evaluations/game-accounts"] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { fetchMock, queryClient, router }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it("defaults to self, switches to a current family target, and shows complete authorized history", async () => {
  const user = userEvent.setup()
  renderPage()

  const target = await screen.findByLabelText("管理对象")
  expect(target).toHaveValue(selfId)
  expect(screen.getByText("当前有效 1 / 3 个")).toBeVisible()
  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  expect(screen.getAllByText("英雄联盟")[0]).toBeVisible()
  expect(screen.getByText("2026-08-01")).toBeVisible()
  expect(screen.getByText("Historical#2025")).toBeVisible()
  expect(screen.getByText("旧账号停用")).toBeVisible()
  expect(screen.getByText("2025-08-22")).toBeVisible()

  await user.selectOptions(target, familyId)

  expect(await screen.findByText("当前有效 2 / 4 个")).toBeVisible()
  expect(screen.getByText("Family#One")).toBeVisible()
  expect(screen.getByText("Family#Two")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
})

it("loads older authorized game-account history only after user action", async () => {
  const firstPage = Array.from({ length: 50 }, (_, index) => ({
    ...unboundAccount,
    id: `page-one-${index}`,
    game_id: `PageOne#${index}`,
  }))
  const oldest = {
    ...unboundAccount,
    id: "page-two-oldest",
    game_id: "OldestAuthorized#History",
  }
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`)) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse(page === "1" ? firstPage : page === "2" ? [oldest] : [])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })

  renderPage(fetchMock)

  expect(await screen.findByText("PageOne#0")).toBeVisible()
  expect(screen.queryByText("OldestAuthorized#History")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(
        `/evaluations/students/${selfId}/game-accounts?include_unbound=true&page=2&page_size=50`,
      ),
    ),
  ).toBe(false)

  await userEvent.setup().click(screen.getByRole("button", { name: "加载更早记录" }))

  expect(await screen.findByText("OldestAuthorized#History")).toBeVisible()
  expect(
    fetchMock.mock.calls.filter(([input]) =>
      String(input).includes(
        `/evaluations/students/${selfId}/game-accounts?include_unbound=true&page=2&page_size=50`,
      ),
    ),
  ).toHaveLength(1)
})

it("deduplicates a repeated full history page and stops pagination", async () => {
  const repeatedPage = Array.from({ length: 50 }, (_, index) => ({
    ...unboundAccount,
    id: `repeated-page-${index}`,
    game_id: `RepeatedPage#${index}`,
  }))
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`)) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse(page === "1" || page === "2" ? repeatedPage : [])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()

  renderPage(fetchMock)

  expect(await screen.findByText("RepeatedPage#0")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载更早记录" }))

  await waitFor(() => {
    expect(screen.queryByRole("button", { name: "加载更早记录" })).not.toBeInTheDocument()
  })
  expect(screen.getAllByText("RepeatedPage#0")).toHaveLength(1)
  expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(51)
  expect(
    fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("page=3&page_size=50"),
    ),
  ).toHaveLength(0)
})

it("loads bind projects from the current evaluation target directory", async () => {
  const user = userEvent.setup()
  const { fetchMock } = renderPage()

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))

  expect(await screen.findByRole("option", { name: "英雄联盟" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(
        `/evaluations/students/${selfId}/game-account-projects?page=1&page_size=100`,
      ),
    ),
  ).toBe(true)
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("/api/v1/projects?")),
  ).toBe(false)
})

it("loads every authorized bind project page", async () => {
  const secondProjectId = "30000000-0000-4000-8000-000000000099"
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-account-projects?`)) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse({
        items:
          page === "2"
            ? [{ id: secondProjectId, name: "第二页授权项目", is_active: true }]
            : [{ id: projectId, name: "英雄联盟", is_active: true }],
        page: Number(page ?? 1),
        page_size: 100,
        total: 101,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))

  expect(await screen.findByRole("option", { name: "第二页授权项目" })).toHaveValue(
    secondProjectId,
  )
  expect(
    fetchMock.mock.calls.filter(([input]) =>
      String(input).includes(`/game-account-projects?page=2&page_size=100`),
    ),
  ).toHaveLength(1)
})

it("shows a loading state while switching to an uncached student history", async () => {
  let resolveFamily!: (response: Response) => void
  const pendingFamily = new Promise<Response>((resolve) => {
    resolveFamily = resolve
  })
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).includes(`/evaluations/students/${familyId}/game-accounts?`)) {
      return pendingFamily
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)

  const target = await screen.findByLabelText("管理对象")
  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  await user.selectOptions(target, familyId)

  expect(await screen.findByText("正在读取游戏账号...")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()

  resolveFamily(
    jsonResponse([
      { ...activeAccount, id: "family-game-1", game_id: "Family#One" },
      { ...activeAccount, id: "family-game-2", game_id: "Family#Two" },
    ]),
  )
  expect(await screen.findByText("Family#One")).toBeVisible()
})

it("retains the typed ID after a 409 and maps the conflict to a non-leaking message", async () => {
  const fetchMock = defaultFetch()
  fetchMock.mockImplementation(async (input, init) => {
    const url = String(input)
    if (url.includes("/game-accounts") && init?.method === "POST") {
      return problemResponse(
        "game_id_already_bound",
        "账号属于张三，联系电话 13800138000",
      )
    }
    return defaultFetch().getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  const gameIdInput = within(dialog).getByLabelText("游戏账号 ID")
  await user.type(gameIdInput, "Typed#Keep")
  await user.click(within(dialog).getByRole("button", { name: "确认绑定" }))

  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "该游戏账号已被绑定，无法重复绑定",
  )
  expect(gameIdInput).toHaveValue("Typed#Keep")
  expect(screen.queryByText(/张三|13800138000/)).not.toBeInTheDocument()
  await waitFor(() => {
    expect(
      queryClient
        .getMutationCache()
        .getAll()
        .some((mutation) => JSON.stringify(mutation.state.variables).includes("Typed#Keep")),
    ).toBe(false)
  })
})

it("returns to one fresh history page after a mutation without refetching older pages", async () => {
  const firstPage = Array.from({ length: 50 }, (_, index) => ({
    ...unboundAccount,
    id: `mutation-page-one-${index}`,
    game_id: `MutationPageOne#${index}`,
  }))
  const older = {
    ...unboundAccount,
    id: "mutation-page-two-oldest",
    game_id: "MutationPageTwo#Oldest",
  }
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`) && !init?.method) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse(page === "1" ? firstPage : page === "2" ? [older] : [])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)

  await user.click(await screen.findByRole("button", { name: "加载更早记录" }))
  expect(await screen.findByText("MutationPageTwo#Oldest")).toBeVisible()
  const pageTwoCallsBeforeMutation = fetchMock.mock.calls.filter(([input]) =>
    String(input).includes("page=2&page_size=50"),
  ).length

  await user.click(screen.getByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.type(within(dialog).getByLabelText("游戏账号 ID"), "FreshAfterMutation#ID")
  await user.click(within(dialog).getByRole("button", { name: "确认绑定" }))

  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()
  })
  expect(screen.queryByText("MutationPageTwo#Oldest")).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.filter(([input]) =>
    String(input).includes("page=2&page_size=50"),
  )).toHaveLength(pageTwoCallsBeforeMutation)
  expect(screen.getByRole("button", { name: "加载更早记录" })).toBeVisible()
})

it("clears evaluation caches, refreshes auth, and leaves protected routes when a bind revokes center access", async () => {
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).includes("/game-accounts") && init?.method === "POST") {
      return problemResponse(
        "evaluation_access_denied",
        "评价中心权限已收回",
        403,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)
  queryClient.setQueryData([...evaluationKeys.root(accountId), "cached-private-id"], {
    game_id: "CachedPrivate#ID",
  })

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.type(within(dialog).getByLabelText("游戏账号 ID"), "Revoked#ID")
  await user.click(within(dialog).getByRole("button", { name: "确认绑定" }))

  expect(await screen.findByText("ERP 工作台")).toBeVisible()
  await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/"))
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/me")),
    ).toHaveLength(2)
  })
  await waitFor(() => {
    expect(queryClient.getQueriesData({ queryKey: evaluationKeys.root(accountId) })).toEqual([])
  })
  expect(screen.queryByText(/Revoked#ID|CachedPrivate#ID/)).not.toBeInTheDocument()
})

it("keeps the page, typed ID, and evaluation cache after an object-level bind denial", async () => {
  let contextCalls = 0
  let targetCalls = 0
  let accountCalls = 0
  let resolveAccounts!: (response: Response) => void
  const pendingAccounts = new Promise<Response>((resolve) => {
    resolveAccounts = resolve
  })
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/evaluations/context")) contextCalls += 1
    if (url.endsWith("/evaluations/game-account-targets")) targetCalls += 1
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`) && !init?.method) {
      accountCalls += 1
      if (accountCalls === 2) return pendingAccounts
    }
    if (url.includes("/game-accounts") && init?.method === "POST") {
      return problemResponse(
        "evaluation_operation_denied",
        "当前账号不能绑定该学员游戏账号",
        403,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)
  const cachedKey = [...evaluationKeys.root(accountId), "object-denial-cache"] as const
  queryClient.setQueryData(cachedKey, { game_id: "CachedOperationDenied#ID" })

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  const gameIdInput = within(dialog).getByLabelText("游戏账号 ID")
  await user.type(gameIdInput, "KeepAfterDenied#ID")
  await user.click(within(dialog).getByRole("button", { name: "确认绑定" }))

  expect(await screen.findByText("正在更新游戏账号权限...")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  await waitFor(() => {
    expect(contextCalls).toBe(2)
    expect(targetCalls).toBe(2)
    expect(accountCalls).toBe(2)
  })

  resolveAccounts(jsonResponse([activeAccount, unboundAccount]))
  const refreshedDialog = await screen.findByRole("dialog", { name: "添加游戏账号" })
  expect(await within(refreshedDialog).findByRole("alert")).toHaveTextContent(
    "当前账号不能绑定该学员游戏账号",
  )
  expect(within(refreshedDialog).getByLabelText("游戏账号 ID")).toHaveValue(
    "KeepAfterDenied#ID",
  )
  expect(screen.getByTestId("location")).toHaveTextContent("/evaluations/game-accounts")
  expect(queryClient.getQueryData(cachedKey)).toEqual({
    game_id: "CachedOperationDenied#ID",
  })
  expect(
    fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/me")),
  ).toHaveLength(1)
})

it("refreshes targets and clears a hidden object after a private 404 mutation denial", async () => {
  let targetCalls = 0
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      return jsonResponse(targetCalls === 1 ? [selfTarget, familyTarget] : [familyTarget])
    }
    if (url.includes("/game-accounts") && init?.method === "POST") {
      return problemResponse(
        "evaluation_resource_not_found",
        "评价资源不存在",
        404,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.type(within(dialog).getByLabelText("游戏账号 ID"), "HiddenAfter404#ID")
  await user.click(within(dialog).getByRole("button", { name: "确认绑定" }))

  await waitFor(() => expect(targetCalls).toBe(2))
  await waitFor(() => {
    expect(screen.getByLabelText("管理对象")).toHaveValue(familyId)
  })
  expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("HiddenAfter404#ID")).not.toBeInTheDocument()
  expect(screen.getByTestId("location")).toHaveTextContent("/evaluations/game-accounts")
})

it("requires an unbind reason and refreshes only this target plus one history page", async () => {
  const user = userEvent.setup()
  const { fetchMock, queryClient } = renderPage()
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const fetchHistory = vi.spyOn(queryClient, "fetchInfiniteQuery")

  await user.click(await screen.findByRole("button", { name: "解绑 Authorized#2026" }))
  const dialog = screen.getByRole("dialog", { name: "解绑游戏账号" })
  await user.click(within(dialog).getByRole("button", { name: "确认解绑" }))
  expect(
    fetchMock.mock.calls.some(
      ([input, init]) => String(input).endsWith("/unbind") && init?.method === "POST",
    ),
  ).toBe(false)

  await user.type(within(dialog).getByLabelText("解绑原因"), "学员更换账号")
  await user.click(within(dialog).getByRole("button", { name: "确认解绑" }))

  await waitFor(() => {
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: evaluationFoundationKeys.targets(accountId),
    })
    expect(fetchHistory).toHaveBeenCalledWith(
      expect.objectContaining({
        queryKey: evaluationFoundationKeys.gameAccountHistory(accountId, selfId, 50),
        pages: 1,
      }),
    )
  })
  const invalidatedKeys = invalidate.mock.calls.map(([filters]) => filters?.queryKey)
  expect(invalidatedKeys).toEqual([evaluationFoundationKeys.targets(accountId)])
})

it("removes an expired family target on context refetch and falls back to self", async () => {
  let targetCalls = 0
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      return jsonResponse(targetCalls === 1 ? [selfTarget, familyTarget] : [selfTarget])
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  const selector = await screen.findByLabelText("管理对象")
  await user.selectOptions(selector, familyId)
  expect(await screen.findByText("Family#One")).toBeVisible()

  await queryClient.invalidateQueries({
    queryKey: evaluationKeys.context(accountId),
  })

  await waitFor(() => expect(targetCalls).toBe(2))
  await waitFor(() => expect(screen.getByLabelText("管理对象")).toHaveValue(selfId))
  const refreshedSelector = screen.getByLabelText("管理对象")
  expect(within(refreshedSelector).queryByRole("option", { name: /家庭学员/ })).not.toBeInTheDocument()
  expect(await screen.findByText("Authorized#2026")).toBeVisible()
})

it("hides cached targets, complete IDs, and dialogs while only targets refetch", async () => {
  let targetCalls = 0
  let resolveTargets!: (response: Response) => void
  const pendingTargets = new Promise<Response>((resolve) => {
    resolveTargets = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      if (targetCalls === 2) return pendingTargets
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  const selector = await screen.findByLabelText("管理对象")
  await user.selectOptions(selector, familyId)
  expect(await screen.findByText("Family#One")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "添加游戏账号" }))
  expect(await screen.findByRole("dialog", { name: "添加游戏账号" })).toBeVisible()

  void queryClient.invalidateQueries({
    queryKey: evaluationFoundationKeys.targets(accountId),
  })
  await waitFor(() => expect(targetCalls).toBe(2))

  expect(screen.queryByRole("option", { name: /家庭学员/ })).not.toBeInTheDocument()
  expect(screen.queryByText("Family#One")).not.toBeInTheDocument()
  expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()
  expect(screen.getByText("正在更新游戏账号权限...")).toBeVisible()

  resolveTargets(jsonResponse([selfTarget, familyTarget]))
  expect(await screen.findByText("Family#One")).toBeVisible()
})

it("hides stale family targets and complete IDs throughout context revalidation", async () => {
  let contextCalls = 0
  let targetCalls = 0
  let resolveContext!: (response: Response) => void
  let resolveTargets!: (response: Response) => void
  const pendingContext = new Promise<Response>((resolve) => {
    resolveContext = resolve
  })
  const pendingTargets = new Promise<Response>((resolve) => {
    resolveTargets = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/evaluations/context")) {
      contextCalls += 1
      if (contextCalls === 2) return pendingContext
    }
    if (url.endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      if (targetCalls === 2) return pendingTargets
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  const selector = await screen.findByLabelText("管理对象")
  await user.selectOptions(selector, familyId)
  expect(await screen.findByText("Family#One")).toBeVisible()

  void queryClient.invalidateQueries({ queryKey: evaluationKeys.context(accountId) })
  await waitFor(() => expect(contextCalls).toBe(2))

  expect(screen.queryByRole("option", { name: /家庭学员/ })).not.toBeInTheDocument()
  expect(screen.queryByText("Family#One")).not.toBeInTheDocument()
  expect(screen.queryByText("Family#Two")).not.toBeInTheDocument()

  resolveContext(
    await baseFetch.getMockImplementation()!(
      "/api/v1/evaluations/context",
      { credentials: "include" },
    ),
  )
  await waitFor(() => expect(targetCalls).toBe(2))

  expect(screen.queryByRole("option", { name: /家庭学员/ })).not.toBeInTheDocument()
  expect(screen.queryByText("Family#One")).not.toBeInTheDocument()
  expect(screen.queryByText("Family#Two")).not.toBeInTheDocument()

  resolveTargets(jsonResponse([selfTarget]))
  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  expect(screen.queryByRole("option", { name: /家庭学员/ })).not.toBeInTheDocument()
})

it("keeps sensitive UI hidden until targets, selected history, and an open project directory revalidate", async () => {
  let targetCalls = 0
  let accountCalls = 0
  let projectCalls = 0
  let resolveTargets!: (response: Response) => void
  let resolveAccounts!: (response: Response) => void
  let resolveProjects!: (response: Response) => void
  const pendingTargets = new Promise<Response>((resolve) => {
    resolveTargets = resolve
  })
  const pendingAccounts = new Promise<Response>((resolve) => {
    resolveAccounts = resolve
  })
  const pendingProjects = new Promise<Response>((resolve) => {
    resolveProjects = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      if (targetCalls === 2) return pendingTargets
    }
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`) && !init?.method) {
      accountCalls += 1
      if (accountCalls === 2) return pendingAccounts
    }
    if (url.includes(`/evaluations/students/${selfId}/game-account-projects?`)) {
      projectCalls += 1
      if (projectCalls === 2) return pendingProjects
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "添加游戏账号" }))
  expect(await screen.findByRole("option", { name: "英雄联盟" })).toBeVisible()

  void queryClient.invalidateQueries({ queryKey: evaluationKeys.context(accountId) })
  await waitFor(() => expect(targetCalls).toBe(2))
  expect(screen.getByText("正在更新游戏账号权限...")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()

  resolveTargets(jsonResponse([selfTarget, familyTarget]))
  await waitFor(() => {
    expect(accountCalls).toBe(2)
    expect(projectCalls).toBe(2)
  })
  expect(screen.getByText("正在更新游戏账号权限...")).toBeVisible()

  resolveAccounts(jsonResponse([activeAccount, unboundAccount]))
  await waitFor(() => expect(screen.getByText("正在更新游戏账号权限...")).toBeVisible())
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()

  resolveProjects(
    jsonResponse({
      items: [{ id: projectId, name: "重新授权项目", is_active: true }],
      page: 1,
      page_size: 100,
      total: 1,
    }),
  )
  const dialog = await screen.findByRole("dialog", { name: "添加游戏账号" })
  expect(await within(dialog).findByRole("option", { name: "重新授权项目" })).toBeVisible()
  expect(screen.getByText("Authorized#2026")).toBeVisible()
})

it("does not restore an unbind dialog when account authorization disappears but the target remains", async () => {
  let accountCalls = 0
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`) && !init?.method) {
      accountCalls += 1
      if (accountCalls === 2) return jsonResponse([unboundAccount])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  await user.click(await screen.findByRole("button", { name: "解绑 Authorized#2026" }))
  const dialog = screen.getByRole("dialog", { name: "解绑游戏账号" })
  await user.type(within(dialog).getByLabelText("解绑原因"), "不应保留的草稿")

  await queryClient.invalidateQueries({ queryKey: evaluationKeys.context(accountId) })
  await waitFor(() => expect(accountCalls).toBe(2))
  await waitFor(() => {
    expect(screen.queryByText("正在更新游戏账号权限...")).not.toBeInTheDocument()
  })

  expect(screen.queryByRole("dialog", { name: "解绑游戏账号" })).not.toBeInTheDocument()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("不应保留的草稿")).not.toBeInTheDocument()
})

it("discards a bind draft when project authorization disappears but the target remains", async () => {
  let projectCalls = 0
  const initialFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-account-projects?`)) {
      projectCalls += 1
      if (projectCalls >= 2) {
        return jsonResponse({ items: [], page: 1, page_size: 100, total: 0 })
      }
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.type(within(dialog).getByLabelText("游戏账号 ID"), "RevokedProject#ID")

  await queryClient.invalidateQueries({ queryKey: evaluationKeys.context(accountId) })
  await waitFor(() => expect(projectCalls).toBe(2))
  await waitFor(() => {
    expect(screen.queryByText("正在更新游戏账号权限...")).not.toBeInTheDocument()
  })

  expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("RevokedProject#ID")).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "添加游戏账号" }))
  const freshDialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  expect(within(freshDialog).getByLabelText("游戏账号 ID")).toHaveValue("")
  expect(within(freshDialog).queryByRole("option", { name: "英雄联盟" })).not.toBeInTheDocument()
})

it("shows a controlled target-refresh error and retries without exposing cached data", async () => {
  let targetCalls = 0
  let resolveRetry!: (response: Response) => void
  const pendingRetry = new Promise<Response>((resolve) => {
    resolveRetry = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      if (targetCalls === 2) {
        return problemResponse("target_refresh_failed", "目标权限目录暂时不可用", 503)
      }
      if (targetCalls === 3) return pendingRetry
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  void queryClient.invalidateQueries({ queryKey: evaluationKeys.context(accountId) })

  const error = await screen.findByRole("alert")
  expect(error).toHaveTextContent("目标权限目录暂时不可用")
  expect(within(error).getByRole("button", { name: "重试权限检查" })).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()

  await user.click(within(error).getByRole("button", { name: "重试权限检查" }))
  expect(screen.getByText("正在更新游戏账号权限...")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  resolveRetry(jsonResponse([selfTarget, familyTarget]))

  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  expect(targetCalls).toBe(3)
})

it("never reveals cached complete IDs after selected-history authorization is denied", async () => {
  let accountCalls = 0
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`) && !init?.method) {
      accountCalls += 1
      if (accountCalls === 2) {
        return problemResponse(
          "evaluation_operation_denied",
          "当前项目授权已失效",
          403,
        )
      }
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const { queryClient } = renderPage(fetchMock)

  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  void queryClient.invalidateQueries({ queryKey: evaluationKeys.context(accountId) })

  const error = await screen.findByRole("alert")
  expect(error).toHaveTextContent("当前项目授权已失效")
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  expect(screen.queryByText("Historical#2025")).not.toBeInTheDocument()
})

it("hides cached complete IDs through a direct history refetch failure and retry", async () => {
  let accountCalls = 0
  let resolveRefetch!: (response: Response) => void
  let resolveRetry!: (response: Response) => void
  const pendingRefetch = new Promise<Response>((resolve) => {
    resolveRefetch = resolve
  })
  const pendingRetry = new Promise<Response>((resolve) => {
    resolveRetry = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes(`/evaluations/students/${selfId}/game-accounts?`) && !init?.method) {
      accountCalls += 1
      if (accountCalls === 2) return pendingRefetch
      if (accountCalls === 3) return pendingRetry
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage(fetchMock)

  expect(await screen.findByText("Authorized#2026")).toBeVisible()
  expect(screen.getByText("Historical#2025")).toBeVisible()

  void queryClient.invalidateQueries({
    queryKey: evaluationFoundationKeys.gameAccounts(accountId, selfId),
  })
  await waitFor(() => expect(accountCalls).toBe(2))

  expect(screen.getByText("正在读取游戏账号...")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  expect(screen.queryByText("Historical#2025")).not.toBeInTheDocument()

  resolveRefetch(problemResponse("history_refresh_failed", "游戏账号历史暂时不可用", 503))
  const error = await screen.findByRole("alert")
  expect(error).toHaveTextContent("游戏账号历史暂时不可用")
  expect(within(error).getByRole("button", { name: "重试游戏账号" })).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()
  expect(screen.queryByText("Historical#2025")).not.toBeInTheDocument()

  await user.click(within(error).getByRole("button", { name: "重试游戏账号" }))
  await waitFor(() => expect(accountCalls).toBe(3))
  expect(screen.getByText("正在读取游戏账号...")).toBeVisible()
  expect(screen.queryByText("Authorized#2026")).not.toBeInTheDocument()

  resolveRetry(jsonResponse([activeAccount, unboundAccount]))
  expect(await screen.findByText("Authorized#2026")).toBeVisible()
})

it("focuses and lets Escape close every idle game account dialog", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("Authorized#2026")

  const cases = [
    {
      opener: "添加游戏账号",
      title: "添加游戏账号",
      focus: "项目",
      close: "关闭添加游戏账号",
      submit: "确认绑定",
    },
    {
      opener: "解绑 Authorized#2026",
      title: "解绑游戏账号",
      focus: "解绑原因",
      close: "关闭解绑游戏账号",
      submit: "确认解绑",
    },
  ]

  for (const item of cases) {
    const opener = screen.getByRole("button", { name: item.opener })
    await user.click(opener)
    const dialog = screen.getByRole("dialog", { name: item.title })
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(within(dialog).getByLabelText(item.focus)).toHaveFocus()
    expect(within(dialog).getByRole("button", { name: item.close })).toBeVisible()
    expect(within(dialog).getByRole("button", { name: "取消" })).toBeVisible()
    const close = within(dialog).getByRole("button", { name: item.close })
    const submit = within(dialog).getByRole("button", { name: item.submit })
    expect(submit).toBeVisible()

    close.focus()
    await user.keyboard("{Shift>}{Tab}{/Shift}")
    expect(submit).toHaveFocus()
    submit.focus()
    await user.tab()
    expect(close).toHaveFocus()

    await user.keyboard("{Escape}")
    expect(screen.queryByRole("dialog", { name: item.title })).not.toBeInTheDocument()
    expect(opener).toHaveFocus()
  }
})

it("returns focus to the live bind opener after a successful refresh", async () => {
  let targetCalls = 0
  let resolveTargets!: (response: Response) => void
  const pendingTargets = new Promise<Response>((resolve) => {
    resolveTargets = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      if (targetCalls === 2) return pendingTargets
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)
  await screen.findByText("Authorized#2026")

  await user.click(screen.getByRole("button", { name: "添加游戏账号" }))
  const dialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.type(within(dialog).getByLabelText("游戏账号 ID"), "Focus#Bind")
  await user.click(within(dialog).getByRole("button", { name: "确认绑定" }))

  expect(await screen.findByText("正在更新游戏账号权限...")).toBeVisible()
  expect(screen.queryByRole("button", { name: "添加游戏账号" })).not.toBeInTheDocument()
  resolveTargets(jsonResponse([selfTarget, familyTarget]))
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()
  })
  const liveOpener = await screen.findByRole("button", { name: "添加游戏账号" })
  await waitFor(() => expect(liveOpener).toBe(document.activeElement))
  expect(liveOpener.isConnected).toBe(true)
})

it("falls back to the live bind opener when a successful unbind removes its opener", async () => {
  let targetCalls = 0
  let unbindCompleted = false
  let resolveTargets!: (response: Response) => void
  const pendingTargets = new Promise<Response>((resolve) => {
    resolveTargets = resolve
  })
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.endsWith(
        `/evaluations/students/${selfId}/game-accounts/${activeAccountId}/unbind`,
      ) &&
      init?.method === "POST"
    ) {
      unbindCompleted = true
      return jsonResponse({
        ...activeAccount,
        status: "unbound",
        unbound_at: "2026-08-03T10:00:00+08:00",
        unbind_reason: "验证成功后的焦点",
      })
    }
    if (
      unbindCompleted &&
      url.includes(`/evaluations/students/${selfId}/game-accounts?`)
    ) {
      return jsonResponse([
        {
          ...activeAccount,
          status: "unbound",
          unbound_at: "2026-08-03T10:00:00+08:00",
          unbind_reason: "验证成功后的焦点",
        },
        unboundAccount,
      ])
    }
    if (url.endsWith("/evaluations/game-account-targets")) {
      targetCalls += 1
      if (targetCalls === 2) return pendingTargets
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)
  await screen.findByText("Authorized#2026")

  await user.click(screen.getByRole("button", { name: "解绑 Authorized#2026" }))
  const dialog = screen.getByRole("dialog", { name: "解绑游戏账号" })
  await user.type(within(dialog).getByLabelText("解绑原因"), "验证成功后的焦点")
  await user.click(within(dialog).getByRole("button", { name: "确认解绑" }))

  expect(await screen.findByText("正在更新游戏账号权限...")).toBeVisible()
  expect(
    screen.queryByRole("button", { name: "解绑 Authorized#2026" }),
  ).not.toBeInTheDocument()
  resolveTargets(jsonResponse([selfTarget, familyTarget]))
  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "解绑游戏账号" })).not.toBeInTheDocument()
  })
  await waitFor(() => {
    expect(
      screen.queryByRole("button", { name: "解绑 Authorized#2026" }),
    ).not.toBeInTheDocument()
  })
  const fallbackOpener = await screen.findByRole("button", { name: "添加游戏账号" })
  await waitFor(() => expect(fallbackOpener).toBe(document.activeElement))
  expect(fallbackOpener.isConnected).toBe(true)
})

it("captures the selected target for bind and unbind dialogs", async () => {
  const user = userEvent.setup()
  const { fetchMock } = renderPage()
  const selector = await screen.findByLabelText("管理对象")
  await screen.findByText("Authorized#2026")

  await user.click(screen.getByRole("button", { name: "添加游戏账号" }))
  expect(screen.getByRole("dialog", { name: "添加游戏账号" })).toBeVisible()
  fireEvent.change(selector, { target: { value: familyId } })
  await screen.findByText("Family#One")
  const bindDialog = screen.getByRole("dialog", { name: "添加游戏账号" })
  await user.selectOptions(within(bindDialog).getByLabelText("项目"), projectId)
  await user.type(within(bindDialog).getByLabelText("游戏账号 ID"), "Captured#Bind")
  await user.click(within(bindDialog).getByRole("button", { name: "确认绑定" }))
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith(`/evaluations/students/${selfId}/game-accounts`) &&
          init?.method === "POST",
      ),
    ).toBe(true)
  })

  await user.selectOptions(await screen.findByLabelText("管理对象"), selfId)
  await user.click(await screen.findByRole("button", { name: "解绑 Authorized#2026" }))
  expect(screen.getByRole("dialog", { name: "解绑游戏账号" })).toBeVisible()
  fireEvent.change(screen.getByLabelText("管理对象"), { target: { value: familyId } })
  await screen.findByText("Family#One")
  const unbindDialog = screen.getByRole("dialog", { name: "解绑游戏账号" })
  await user.type(within(unbindDialog).getByLabelText("解绑原因"), "捕获原管理对象")
  await user.click(within(unbindDialog).getByRole("button", { name: "确认解绑" }))

  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(
        ([input, init]) =>
          String(input).endsWith(
            `/evaluations/students/${selfId}/game-accounts/${activeAccountId}/unbind`,
          ) && init?.method === "POST",
      ),
    ).toBe(true)
  })
})

it("locks duplicate submit, close, and Escape for every pending game account dialog", async () => {
  let pendingResponse: Promise<Response> | null = null
  let resolvePending!: (response: Response) => void
  const baseFetch = defaultFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).includes("/game-accounts") && init?.method === "POST" && pendingResponse) {
      return pendingResponse
    }
    return baseFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPage(fetchMock)
  await screen.findByText("Authorized#2026")

  const cases = [
    {
      opener: "添加游戏账号",
      title: "添加游戏账号",
      close: "关闭添加游戏账号",
      submit: "确认绑定",
      fill: async (dialog: HTMLElement) => {
        await within(dialog).findByRole("option", { name: "英雄联盟" })
        await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
        await user.type(within(dialog).getByLabelText("游戏账号 ID"), "Pending#One")
      },
    },
    {
      opener: "解绑 Authorized#2026",
      title: "解绑游戏账号",
      close: "关闭解绑游戏账号",
      submit: "确认解绑",
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("解绑原因"), "等待解绑完成")
      },
    },
  ]

  for (const [index, item] of cases.entries()) {
    pendingResponse = new Promise<Response>((resolve) => {
      resolvePending = resolve
    })
    await user.click(screen.getByRole("button", { name: item.opener }))
    const dialog = screen.getByRole("dialog", { name: item.title })
    await item.fill(dialog)
    const submit = within(dialog).getByRole("button", { name: item.submit })
    const cancel = within(dialog).getByRole("button", { name: "取消" })
    await user.click(submit)
    await waitFor(() => expect(submit).toBeDisabled())
    await user.click(submit)
    await user.click(cancel)
    await user.keyboard("{Escape}")

    expect(cancel).toBeDisabled()
    expect(within(dialog).getByRole("button", { name: item.close })).toBeDisabled()
    expect(screen.getByRole("dialog", { name: item.title })).toBeVisible()
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(
      index + 1,
    )

    resolvePending(jsonResponse(activeAccount, 201))
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: item.title })).not.toBeInTheDocument()
    })
  }
})
