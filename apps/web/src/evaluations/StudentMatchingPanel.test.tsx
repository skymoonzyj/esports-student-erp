import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Profiler } from "react"
import type { ProfilerOnRenderCallback } from "react"
import type * as ReactModule from "react"
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  useLocation,
} from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { evaluationKeys } from "./api"
import { EvaluationAuthorizationProvider } from "./EvaluationAuthorization"
import { evaluationFoundationKeys } from "./foundationApi"
import { StudentMatchingPanel } from "./StudentMatchingPanel"

const matchingStateTracker = vi.hoisted(() => ({
  enabled: false,
  hookCalls: 0,
  phoneSetterCalls: [] as unknown[],
  reasonSetterCalls: [] as unknown[],
}))

vi.mock("react", async (importOriginal) => {
  const actual = await importOriginal<typeof ReactModule>()
  return {
    ...actual,
    useState: <State,>(initialState: State | (() => State)) => {
      const [value, setValue] = actual.useState(initialState)
      const stack = new Error().stack ?? ""
      if (!matchingStateTracker.enabled || !stack.includes("StudentMatchingPanel")) {
        return [value, setValue] as const
      }
      const slot = matchingStateTracker.hookCalls % 11
      matchingStateTracker.hookCalls += 1
      const trackedSetter: typeof setValue = (next) => {
        if (slot === 6) matchingStateTracker.phoneSetterCalls.push(next)
        if (slot === 7) matchingStateTracker.reasonSetterCalls.push(next)
        setValue(next)
      }
      return [value, trackedSetter] as const
    },
  }
})

const accountId = "10000000-0000-4000-8000-000000000001"
const studentId = "20000000-0000-4000-8000-000000000021"
const nextStudentId = "20000000-0000-4000-8000-000000000022"
const projectId = "30000000-0000-4000-8000-000000000001"
const phoneId = "60000000-0000-4000-8000-000000000001"
const refreshUser = vi.fn(async () => undefined)

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId }, refreshUser }),
}))

const matchingProfile = {
  student_person_id: studentId,
  student_name: "张同学",
  student_number: "S-2026-0021",
  active_count: 2,
  active_limit: 5,
  game_accounts: [
    {
      id: "40000000-0000-4000-8000-000000000001",
      project_id: projectId,
      project_name: "英雄联盟",
      game_id: "FullAuthorized#ID",
      status: "active",
      valid_from: "2026-08-01T09:30:00+08:00",
      unbound_at: null,
      unbind_reason: null,
    },
  ],
  game_accounts_total: 101,
  game_accounts_truncated: true,
  auxiliary_phones: [
    {
      id: phoneId,
      masked_phone: "138****8000",
      status: "active",
      created_at: "2026-08-01T09:30:00+08:00",
      disabled_at: null,
    },
  ],
  auxiliary_phones_total: 76,
  auxiliary_phones_truncated: true,
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function baseFetch() {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/students?")) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse({
        items:
          page === "2"
            ? [
                {
                  student_person_id: nextStudentId,
                  student_name: "李同学",
                  student_number: "S-2026-0022",
                },
              ]
            : [
                {
                  student_person_id: studentId,
                  student_name: "张同学",
                  student_number: "S-2026-0021",
                },
              ],
        total: 21,
        page: Number(page ?? 1),
        page_size: 20,
      })
    }
    if (url.endsWith(`/evaluations/admin/students/${studentId}/matching-profile`)) {
      return jsonResponse(matchingProfile)
    }
    if (url.endsWith(`/evaluations/admin/students/${nextStudentId}/matching-profile`)) {
      return jsonResponse({
        ...matchingProfile,
        student_person_id: nextStudentId,
        student_name: "李同学",
        student_number: "S-2026-0022",
      })
    }
    if (url.endsWith(`/evaluations/admin/students/${studentId}/game-account-limit`)) {
      return jsonResponse({ student_person_id: studentId, active_count: 2, active_limit: 6 })
    }
    if (url.endsWith(`/evaluations/admin/students/${studentId}/auxiliary-phones`)) {
      return jsonResponse(
        {
          id: "60000000-0000-4000-8000-000000000002",
          masked_phone: "139****9000",
          status: "active",
          created_at: "2026-08-02T09:30:00+08:00",
          disabled_at: null,
        },
        201,
      )
    }
    if (url.endsWith(`/auxiliary-phones/${phoneId}/replace`)) {
      return jsonResponse(
        {
          id: "60000000-0000-4000-8000-000000000003",
          masked_phone: "137****7000",
          status: "active",
          created_at: "2026-08-03T09:30:00+08:00",
          disabled_at: null,
        },
        201,
      )
    }
    if (url.endsWith(`/auxiliary-phones/${phoneId}/deactivate`)) {
      return jsonResponse({
        ...matchingProfile.auxiliary_phones[0],
        status: "inactive",
        disabled_at: "2026-08-04T09:30:00+08:00",
      })
    }
    throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`)
  })
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderPanel(
  fetchMock = baseFetch(),
  fixedStudentId?: string,
  queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  }),
  initialEntry = "/evaluations/admin",
  onPanelCommit?: ProfilerOnRenderCallback,
) {
  vi.stubGlobal("fetch", fetchMock)
  const panel = <StudentMatchingPanel studentId={fixedStudentId} />
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
          { path: "/", element: <p>ERP 工作台落点</p> },
          { path: "/evaluations", element: <p>评价中心落点</p> },
          {
            path: "*",
            element: onPanelCommit ? (
              <Profiler id="student-matching-panel" onRender={onPanelCommit}>
                {panel}
              </Profiler>
            ) : panel,
          },
        ],
      },
    ],
    { initialEntries: [initialEntry] },
  )
  let authorization = { pending: false, revision: 1 }
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <EvaluationAuthorizationProvider {...authorization}>
        <RouterProvider router={router} />
      </EvaluationAuthorizationProvider>
    </QueryClientProvider>
  )
  const view = render(tree())
  return Object.assign(fetchMock, {
    setAuthorization(next: { pending: boolean; revision: number }) {
      authorization = next
      view.rerender(tree())
    },
  })
}

afterEach(() => {
  refreshUser.mockClear()
  vi.unstubAllGlobals()
})

it("shows explicit authorized history truncation without inventing complete IDs", async () => {
  renderPanel(baseFetch(), studentId)

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  expect(screen.getByText("显示最近 1 条，共 101 条")).toBeVisible()
  expect(screen.getByText("显示最近 1 条，共 76 条")).toBeVisible()
  expect(screen.queryByText("UnreturnedComplete#ID")).not.toBeInTheDocument()
})

it("loads older matching histories only after separate explicit actions", async () => {
  const initialFetch = baseFetch()
  const olderGameId = "OlderAuthorized#ID"
  const olderPhoneId = "60000000-0000-4000-8000-000000000099"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.includes(`/evaluations/students/${studentId}/game-accounts?`) &&
      !init?.method
    ) {
      return jsonResponse([
        {
          ...matchingProfile.game_accounts[0],
          id: "40000000-0000-4000-8000-000000000099",
          game_id: olderGameId,
          status: "unbound",
        },
      ])
    }
    if (
      url.includes(
        `/evaluations/admin/students/${studentId}/auxiliary-phones?include_inactive=true&page=2&page_size=50`,
      ) &&
      !init?.method
    ) {
      return jsonResponse([
        {
          ...matchingProfile.auxiliary_phones[0],
          id: olderPhoneId,
          masked_phone: "137****7000",
          status: "inactive",
        },
      ])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock, studentId)

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  expect(screen.queryByText(olderGameId)).not.toBeInTheDocument()
  expect(screen.queryByText("137****7000")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("page=2&page_size=50")),
  ).toBe(false)

  await user.click(screen.getByRole("button", { name: "加载更早游戏账号记录" }))
  expect(await screen.findByText(olderGameId)).toBeVisible()
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/evaluations/students/${studentId}/game-accounts?include_unbound=true&page=2&page_size=50`,
    expect.objectContaining({ credentials: "include" }),
  )

  await user.click(screen.getByRole("button", { name: "加载更早辅助手机号记录" }))
  expect(await screen.findByText("137****7000")).toBeVisible()
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones?include_inactive=true&page=2&page_size=50`,
    expect.objectContaining({ credentials: "include" }),
  )
})

it.each([
  ["game-accounts", 403, "evaluation_operation_denied"],
  ["game-accounts", 404, "evaluation_resource_not_found"],
  ["auxiliary-phones", 403, "evaluation_operation_denied"],
  ["auxiliary-phones", 404, "evaluation_resource_not_found"],
])(
  "clears only the affected student's sensitive caches after older %s HTTP %i %s",
  async (historyKind, status, code) => {
    const cachedOlderGameId = "CachedOlderAuthorized#ID"
    const cachedOlderPhone = "137****7333"
    const adminKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "student-matching",
    ] as const
    const profileKey = [
      ...adminKey,
      "profile",
      studentId,
      "authorization",
      1,
    ] as const
    const historyKey = [
      ...adminKey,
      "older-history",
      studentId,
      "authorization",
      1,
    ] as const
    const gameHistoryKey = [...historyKey, "game-accounts"] as const
    const phoneHistoryKey = [...historyKey, "auxiliary-phones"] as const
    const unrelatedProfileKey = [
      ...adminKey,
      "profile",
      nextStudentId,
      "authorization",
      1,
    ] as const
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    queryClient.setQueryData(gameHistoryKey, {
      pages: [
        Array.from({ length: 50 }, (_, index) => ({
          ...matchingProfile.game_accounts[0],
          id: `cached-game-${index}`,
          game_id: index === 0 ? cachedOlderGameId : `CachedOlder#${index}`,
          status: "unbound",
        })),
      ],
      pageParams: [2],
    })
    queryClient.setQueryData(phoneHistoryKey, {
      pages: [
        Array.from({ length: 50 }, (_, index) => ({
          ...matchingProfile.auxiliary_phones[0],
          id: `cached-phone-${index}`,
          masked_phone: index === 0 ? cachedOlderPhone : `136****${String(index).padStart(4, "0")}`,
          status: "inactive",
        })),
      ],
      pageParams: [2],
    })
    queryClient.setQueryData(unrelatedProfileKey, {
      student_person_id: nextStudentId,
      game_accounts: [{ game_id: "UnrelatedStudent#ID" }],
    })
    const sensitiveCommitVisibility: boolean[] = []
    const deniedHistoryKey = historyKind === "game-accounts" ? gameHistoryKey : phoneHistoryKey
    const sensitiveTexts = [
      "FullAuthorized#ID",
      "138****8000",
      cachedOlderGameId,
      cachedOlderPhone,
    ]
    const initialFetch = baseFetch()
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const requestedHistory =
        (historyKind === "game-accounts" &&
          url.includes(`/evaluations/students/${studentId}/game-accounts?`) &&
          url.includes("page=3")) ||
        (historyKind === "auxiliary-phones" &&
          url.includes(`/evaluations/admin/students/${studentId}/auxiliary-phones?`) &&
          url.includes("page=3"))
      if (requestedHistory && !init?.method) {
        return jsonResponse(
          {
            type: "about:blank",
            title: status === 403 ? "Forbidden" : "Not Found",
            status,
            detail: "更早匹配历史已不可见",
            code,
            request_id: `older-${historyKind}-${status}`,
          },
          status,
        )
      }
      return initialFetch.getMockImplementation()!(input, init)
    })
    const user = userEvent.setup()
    renderPanel(
      fetchMock,
      studentId,
      queryClient,
      `/people/${studentId}`,
      () => {
        if (!queryClient.getQueryState(deniedHistoryKey)?.error) return
        const renderedText = document.body.textContent ?? ""
        sensitiveCommitVisibility.push(
          sensitiveTexts.some((text) => renderedText.includes(text)),
        )
      },
    )

    expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
    expect(screen.getByText(cachedOlderGameId)).toBeVisible()
    expect(screen.getByText(cachedOlderPhone)).toBeVisible()
    await user.click(
      screen.getByRole("button", {
        name:
          historyKind === "game-accounts"
            ? "加载更早游戏账号记录"
            : "加载更早辅助手机号记录",
      }),
    )

    expect(await screen.findByRole("alert")).toHaveTextContent("更早匹配历史已不可见")
    expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
    expect(screen.queryByText("138****8000")).not.toBeInTheDocument()
    expect(screen.queryByText(cachedOlderGameId)).not.toBeInTheDocument()
    expect(screen.queryByText(cachedOlderPhone)).not.toBeInTheDocument()
    expect(sensitiveCommitVisibility.length).toBeGreaterThan(0)
    expect(sensitiveCommitVisibility).not.toContain(true)
    await waitFor(() => {
      expect(queryClient.getQueryState(profileKey)).toBeUndefined()
      expect(queryClient.getQueriesData({ queryKey: historyKey })).toEqual([])
    })
    expect(queryClient.getQueryData(unrelatedProfileKey)).toEqual({
      student_person_id: nextStudentId,
      game_accounts: [{ game_id: "UnrelatedStudent#ID" }],
    })
    expect(screen.getByTestId("location")).toHaveTextContent(`/people/${studentId}`)
    expect(refreshUser).not.toHaveBeenCalled()
  },
)

it("removes revision-scoped profile and older-history caches across authorization refresh", async () => {
  const olderGameId = "RevisionOneOlder#ID"
  const olderPhone = "137****7111"
  let profileCalls = 0
  let resolveRevisionTwoProfile!: (response: Response) => void
  const revisionTwoProfile = new Promise<Response>((resolve) => {
    resolveRevisionTwoProfile = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.endsWith(`/evaluations/admin/students/${studentId}/matching-profile`) &&
      !init?.method
    ) {
      profileCalls += 1
      if (profileCalls === 2) return revisionTwoProfile
    }
    if (
      url.includes(`/evaluations/students/${studentId}/game-accounts?`) &&
      !init?.method
    ) {
      return jsonResponse([
        {
          ...matchingProfile.game_accounts[0],
          id: "40000000-0000-4000-8000-000000000098",
          game_id: olderGameId,
          status: "unbound",
        },
      ])
    }
    if (
      url.includes(
        `/evaluations/admin/students/${studentId}/auxiliary-phones?include_inactive=true&page=2&page_size=50`,
      ) &&
      !init?.method
    ) {
      return jsonResponse([
        {
          ...matchingProfile.auxiliary_phones[0],
          id: "60000000-0000-4000-8000-000000000098",
          masked_phone: olderPhone,
          status: "inactive",
        },
      ])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const user = userEvent.setup()
  const result = renderPanel(fetchMock, studentId, queryClient)
  const adminKey = [
    ...evaluationFoundationKeys.admin(accountId),
    "student-matching",
  ] as const
  const revisionOneProfileKey = [
    ...adminKey,
    "profile",
    studentId,
    "authorization",
    1,
  ] as const
  const revisionOneHistoryKey = [
    ...adminKey,
    "older-history",
    studentId,
    "authorization",
    1,
  ] as const

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载更早游戏账号记录" }))
  expect(await screen.findByText(olderGameId)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载更早辅助手机号记录" }))
  expect(await screen.findByText(olderPhone)).toBeVisible()
  expect(queryClient.getQueryData(revisionOneProfileKey)).toEqual(matchingProfile)
  expect(queryClient.getQueriesData({ queryKey: revisionOneHistoryKey })).toHaveLength(2)

  result.setAuthorization({ pending: false, revision: 2 })

  expect(screen.getByText("正在更新匹配资料...")).toBeVisible()
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(screen.queryByText("138****8000")).not.toBeInTheDocument()
  expect(screen.queryByText(olderGameId)).not.toBeInTheDocument()
  expect(screen.queryByText(olderPhone)).not.toBeInTheDocument()
  expect(queryClient.getQueryState(revisionOneProfileKey)).toBeUndefined()
  expect(queryClient.getQueriesData({ queryKey: revisionOneHistoryKey })).toEqual([])
  await waitFor(() => expect(profileCalls).toBe(2))

  resolveRevisionTwoProfile(
    jsonResponse({
      ...matchingProfile,
      game_accounts: [
        { ...matchingProfile.game_accounts[0], game_id: "RevisionTwo#ID" },
      ],
      auxiliary_phones: [
        { ...matchingProfile.auxiliary_phones[0], masked_phone: "136****6222" },
      ],
    }),
  )

  expect(await screen.findByText("RevisionTwo#ID")).toBeVisible()
  expect(screen.getByText("136****6222")).toBeVisible()
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(screen.queryByText("138****8000")).not.toBeInTheDocument()
  expect(screen.queryByText(olderGameId)).not.toBeInTheDocument()
  expect(screen.queryByText(olderPhone)).not.toBeInTheDocument()
  await waitFor(() => {
    expect(queryClient.getQueryState(revisionOneProfileKey)).toBeUndefined()
    expect(queryClient.getQueriesData({ queryKey: revisionOneHistoryKey })).toEqual([])
  })
})

it("revalidates matching directories without exposing cached data across failure and retry", async () => {
  let studentCalls = 0
  let profileCalls = 0
  let resolveStudents!: (response: Response) => void
  let resolveProfile!: (response: Response) => void
  let resolveStudentRetry!: (response: Response) => void
  let resolveProfileRetry!: (response: Response) => void
  const pendingStudents = new Promise<Response>((resolve) => {
    resolveStudents = resolve
  })
  const pendingProfile = new Promise<Response>((resolve) => {
    resolveProfile = resolve
  })
  const pendingStudentRetry = new Promise<Response>((resolve) => {
    resolveStudentRetry = resolve
  })
  const pendingProfileRetry = new Promise<Response>((resolve) => {
    resolveProfileRetry = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/students?") && !init?.method) {
      studentCalls += 1
      if (studentCalls === 2) return pendingStudents
      if (studentCalls === 3) return pendingStudentRetry
    }
    if (
      url.endsWith(`/evaluations/admin/students/${studentId}/matching-profile`) &&
      !init?.method
    ) {
      profileCalls += 1
      if (profileCalls === 2) return pendingProfile
      if (profileCalls === 3) return pendingProfileRetry
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const result = renderPanel(fetchMock)

  await user.click(
    await screen.findByRole("button", {
      name: `选择 张同学 · S-2026-0021 · ${studentId}`,
    }),
  )
  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()

  result.setAuthorization({ pending: false, revision: 2 })
  await waitFor(() => {
    expect(studentCalls).toBe(2)
    expect(profileCalls).toBe(2)
  })
  expect(screen.getByText("正在更新匹配资料...")).toBeVisible()
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(screen.queryByText("138****8000")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /选择 张同学/ })).not.toBeInTheDocument()

  const problem = {
    type: "about:blank",
    title: "Service unavailable",
    status: 503,
    detail: "学员匹配授权目录暂时不可用",
    code: "matching_directory_unavailable",
    request_id: "matching-directory-refresh",
  }
  resolveStudents(jsonResponse(problem, 503))
  resolveProfile(jsonResponse({ ...problem, request_id: "matching-profile-refresh" }, 503))

  const error = await screen.findByRole("alert")
  expect(error).toHaveTextContent("学员匹配授权目录暂时不可用")
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  await user.click(within(error).getByRole("button", { name: "重试匹配资料" }))

  await waitFor(() => {
    expect(studentCalls).toBe(3)
    expect(profileCalls).toBe(3)
  })
  expect(screen.getByText("正在更新匹配资料...")).toBeVisible()

  resolveStudentRetry(
    jsonResponse({
      items: [
        {
          student_person_id: studentId,
          student_name: "张同学",
          student_number: "S-2026-0021",
        },
      ],
      total: 1,
      page: 1,
      page_size: 20,
    }),
  )
  resolveProfileRetry(jsonResponse(matchingProfile))

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
})

it.each([
  [403, "evaluation_operation_denied"],
  [404, "evaluation_resource_not_found"],
])(
  "keeps the standalone directory usable when the selected profile gets object HTTP %i after an authorization revision",
  async (status, code) => {
    let studentCalls = 0
    let profileCalls = 0
    let resolveStudents!: (response: Response) => void
    let resolveDeniedProfile!: (response: Response) => void
    const pendingStudents = new Promise<Response>((resolve) => {
      resolveStudents = resolve
    })
    const pendingDeniedProfile = new Promise<Response>((resolve) => {
      resolveDeniedProfile = resolve
    })
    const nextProfile = {
      ...matchingProfile,
      student_person_id: nextStudentId,
      student_name: "李同学",
      student_number: "S-2026-0022",
      game_accounts: [
        { ...matchingProfile.game_accounts[0], game_id: "RemainingStudent#ID" },
      ],
      auxiliary_phones: [
        { ...matchingProfile.auxiliary_phones[0], masked_phone: "136****6222" },
      ],
    }
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes("/evaluations/admin/students?") && !init?.method) {
        studentCalls += 1
        if (studentCalls === 2) return pendingStudents
        return jsonResponse({
          items: [
            {
              student_person_id: studentId,
              student_name: "张同学",
              student_number: "S-2026-0021",
            },
            {
              student_person_id: nextStudentId,
              student_name: "李同学",
              student_number: "S-2026-0022",
            },
          ],
          total: 2,
          page: 1,
          page_size: 20,
        })
      }
      if (
        url.endsWith(`/evaluations/admin/students/${studentId}/matching-profile`) &&
        !init?.method
      ) {
        profileCalls += 1
        if (profileCalls === 2) return pendingDeniedProfile
        return jsonResponse(matchingProfile)
      }
      if (
        url.endsWith(`/evaluations/admin/students/${nextStudentId}/matching-profile`) &&
        !init?.method
      ) {
        return jsonResponse(nextProfile)
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const adminKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "student-matching",
    ] as const
    const deniedHistoryKey = [
      ...adminKey,
      "older-history",
      studentId,
      "authorization",
      2,
      "game-accounts",
    ] as const
    const unrelatedProfileKey = [
      ...adminKey,
      "profile",
      nextStudentId,
      "authorization",
      0,
    ] as const
    queryClient.setQueryData(deniedHistoryKey, {
      pages: [[{ ...matchingProfile.game_accounts[0], game_id: "DeniedOlder#ID" }]],
      pageParams: [2],
    })
    queryClient.setQueryData(unrelatedProfileKey, {
      ...nextProfile,
      active_limit: 6,
    })
    const user = userEvent.setup()
    const result = renderPanel(fetchMock, undefined, queryClient)

    await user.click(
      await screen.findByRole("button", {
        name: `选择 张同学 · S-2026-0021 · ${studentId}`,
      }),
    )
    expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()

    result.setAuthorization({ pending: false, revision: 2 })

    await waitFor(() => {
      expect(studentCalls).toBe(2)
      expect(profileCalls).toBe(2)
    })
    expect(screen.getByText("正在更新匹配资料...")).toBeVisible()
    expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
    expect(screen.queryByText("DeniedOlder#ID")).not.toBeInTheDocument()

    resolveStudents(
      jsonResponse({
        items: [
          {
            student_person_id: nextStudentId,
            student_name: "李同学",
            student_number: "S-2026-0022",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      }),
    )
    resolveDeniedProfile(
      jsonResponse(
        {
          type: "about:blank",
          title: status === 403 ? "Forbidden" : "Not found",
          status,
          detail: "当前学员匹配资料权限不存在",
          code,
          request_id: `matching-profile-revision-${status}`,
        },
        status,
      ),
    )

    const remainingStudent = await screen.findByRole("button", {
      name: `选择 李同学 · S-2026-0022 · ${nextStudentId}`,
    })
    expect(remainingStudent).toBeVisible()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
    expect(screen.queryByText("DeniedOlder#ID")).not.toBeInTheDocument()
    await waitFor(() => {
      expect(
        queryClient.getQueriesData({ queryKey: [...adminKey, "profile", studentId] }),
      ).toEqual([])
      expect(
        queryClient.getQueriesData({
          queryKey: [...adminKey, "older-history", studentId],
        }),
      ).toEqual([])
    })
    expect(queryClient.getQueryData(unrelatedProfileKey)).toEqual({
      ...nextProfile,
      active_limit: 6,
    })
    expect(screen.getByTestId("location")).toHaveTextContent("/evaluations/admin")
    expect(refreshUser).not.toHaveBeenCalled()

    await user.click(remainingStudent)

    expect(await screen.findByText("RemainingStudent#ID")).toBeVisible()
    expect(screen.getByText("136****6222")).toBeVisible()
    expect(profileCalls).toBe(2)
  },
)

it("searches and paginates students, shows the full selected ID, and loads authorized matching data", async () => {
  const user = userEvent.setup()
  const fetchMock = renderPanel()

  await user.type(await screen.findByLabelText("搜索学员"), "张同学")
  await user.click(screen.getByRole("button", { name: "搜索" }))
  const student = await screen.findByRole("button", {
    name: `选择 张同学 · S-2026-0021 · ${studentId}`,
  })
  expect(student).toBeVisible()
  await user.click(student)

  expect(await screen.findByRole("heading", { name: "张同学" })).toBeVisible()
  expect(screen.getByText(studentId)).toBeVisible()
  expect(screen.getByText("当前有效 2 / 5 个")).toBeVisible()
  expect(screen.getByText("FullAuthorized#ID")).toBeVisible()
  expect(screen.getByText("138****8000")).toBeVisible()
  expect(screen.queryByText("13800138000")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes("query=%E5%BC%A0%E5%90%8C%E5%AD%A6&page=1&page_size=20"),
    ),
  ).toBe(true)

  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(
    await screen.findByRole("button", {
      name: `选择 李同学 · S-2026-0022 · ${nextStudentId}`,
    }),
  ).toBeVisible()
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("page=2&page_size=20"))).toBe(true)
})

it("hides cached profile data for the entire profile refetch", async () => {
  let profileCalls = 0
  let resolveProfile!: (response: Response) => void
  const pendingProfile = new Promise<Response>((resolve) => {
    resolveProfile = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith(`/evaluations/admin/students/${studentId}/matching-profile`)) {
      profileCalls += 1
      if (profileCalls === 2) return pendingProfile
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  renderPanel(fetchMock, studentId, queryClient, `/people/${studentId}`)

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  expect(screen.getByText("138****8000")).toBeVisible()
  void queryClient.invalidateQueries({
    queryKey: [...evaluationFoundationKeys.admin(accountId), "student-matching", "profile", studentId],
  })

  await waitFor(() => expect(profileCalls).toBe(2))
  expect(screen.getByText("正在更新匹配资料...")).toBeVisible()
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(screen.queryByText("138****8000")).not.toBeInTheDocument()

  resolveProfile(
    jsonResponse({
      ...matchingProfile,
      game_accounts: [{ ...matchingProfile.game_accounts[0], game_id: "FreshAuthorized#ID" }],
      auxiliary_phones: [
        { ...matchingProfile.auxiliary_phones[0], masked_phone: "137****7000" },
      ],
    }),
  )
  expect(await screen.findByText("FreshAuthorized#ID")).toBeVisible()
  expect(screen.getByText("137****7000")).toBeVisible()
})

it.each([
  [403, "evaluation_operation_denied", "当前账号不能查看该学员"],
  [404, "evaluation_resource_not_found", "该学员评价资料不存在"],
])("hides cached profile data after profile refetch HTTP %i %s", async (status, code, detail) => {
  let profileCalls = 0
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith(`/evaluations/admin/students/${studentId}/matching-profile`)) {
      profileCalls += 1
      if (profileCalls === 2) {
        return jsonResponse(
          {
            type: "about:blank",
            title: status === 403 ? "Forbidden" : "Not Found",
            status,
            detail,
            code,
            request_id: `matching-profile-${code}`,
          },
          status,
        )
      }
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  renderPanel(fetchMock, studentId, queryClient, `/people/${studentId}`)

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  void queryClient.invalidateQueries({
    queryKey: [...evaluationFoundationKeys.admin(accountId), "student-matching", "profile", studentId],
  })

  expect(await screen.findByRole("alert")).toHaveTextContent(detail)
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(screen.queryByText("138****8000")).not.toBeInTheDocument()
  expect(screen.getByTestId("location")).toHaveTextContent(`/people/${studentId}`)
  expect(refreshUser).not.toHaveBeenCalled()
})

it("requires a reason when changing the active limit and sends the exact mutation", async () => {
  const user = userEvent.setup()
  const fetchMock = renderPanel(undefined, studentId)

  await user.click(await screen.findByRole("button", { name: "调整账号上限" }))
  const dialog = screen.getByRole("dialog", { name: "调整游戏账号上限" })
  await user.clear(within(dialog).getByLabelText("账号上限"))
  await user.type(within(dialog).getByLabelText("账号上限"), "6")
  await user.click(within(dialog).getByRole("button", { name: "确认调整" }))
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(false)

  await user.type(within(dialog).getByLabelText("调整原因"), "赛事账号增加")
  await user.click(within(dialog).getByRole("button", { name: "确认调整" }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      max_active_accounts: 6,
      reason: "赛事账号增加",
    })
  })
})

it("adds, replaces, and deactivates auxiliary phones without rendering full phone values", async () => {
  const user = userEvent.setup()
  const fetchMock = renderPanel(undefined, studentId)
  await screen.findByText("138****8000")

  await user.click(screen.getByRole("button", { name: "添加辅助手机号" }))
  let dialog = screen.getByRole("dialog", { name: "添加辅助手机号" })
  await user.type(within(dialog).getByLabelText("手机号"), "13900139000")
  await user.click(within(dialog).getByRole("button", { name: "确认添加" }))
  await waitFor(() => expect(screen.queryByText("13900139000")).not.toBeInTheDocument())

  await user.click(screen.getByRole("button", { name: "替换 138****8000" }))
  dialog = screen.getByRole("dialog", { name: "替换辅助手机号" })
  await user.type(within(dialog).getByLabelText("新手机号"), "13700137000")
  await user.type(within(dialog).getByLabelText("替换原因"), "联系电话更新")
  await user.click(within(dialog).getByRole("button", { name: "确认替换" }))

  await user.click(screen.getByRole("button", { name: "停用 138****8000" }))
  dialog = screen.getByRole("dialog", { name: "停用辅助手机号" })
  await user.type(within(dialog).getByLabelText("停用原因"), "号码不再使用")
  await user.click(within(dialog).getByRole("button", { name: "确认停用" }))

  await waitFor(() => {
    const writes = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")
    expect(writes.map(([input]) => String(input))).toEqual([
      `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones`,
      `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones/${phoneId}/replace`,
      `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones/${phoneId}/deactivate`,
    ])
  })
  expect(document.body).not.toHaveTextContent(/13900139000|13700137000/)
})

it.each([
  ["add", 403, "evaluation_operation_denied"],
  ["add", 404, "evaluation_resource_not_found"],
  ["replace", 403, "evaluation_operation_denied"],
  ["replace", 404, "evaluation_resource_not_found"],
])(
  "closes and immediately clears sensitive %s dialog state after HTTP %i %s",
  async (kind, status, code) => {
    const rawPhone = kind === "add" ? "13900139000" : "13700137000"
    const rawReason = "对象权限变化后不得保留"
    matchingStateTracker.enabled = true
    matchingStateTracker.hookCalls = 0
    matchingStateTracker.phoneSetterCalls.length = 0
    matchingStateTracker.reasonSetterCalls.length = 0
    try {
      const initialFetch = baseFetch()
      const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
        const url = String(input)
        const rejectedMutation =
          init?.method === "POST" &&
          (kind === "add"
            ? url.endsWith(`/evaluations/admin/students/${studentId}/auxiliary-phones`)
            : url.endsWith(`/auxiliary-phones/${phoneId}/replace`))
        if (rejectedMutation) {
          return jsonResponse(
            {
              type: "about:blank",
              title: status === 403 ? "Forbidden" : "Not Found",
              status,
              detail: "该学员手机号已不可修改",
              code,
              request_id: `${kind}-phone-${status}`,
            },
            status,
          )
        }
        return initialFetch.getMockImplementation()!(input, init)
      })
      const user = userEvent.setup()
      renderPanel(fetchMock, studentId)
      await screen.findByText("138****8000")

      await user.click(
        screen.getByRole("button", {
          name: kind === "add" ? "添加辅助手机号" : "替换 138****8000",
        }),
      )
      const title = kind === "add" ? "添加辅助手机号" : "替换辅助手机号"
      const dialog = screen.getByRole("dialog", { name: title })
      await user.type(
        within(dialog).getByLabelText(kind === "add" ? "手机号" : "新手机号"),
        rawPhone,
      )
      if (kind === "replace") {
        await user.type(within(dialog).getByLabelText("替换原因"), rawReason)
      }
      matchingStateTracker.phoneSetterCalls.length = 0
      matchingStateTracker.reasonSetterCalls.length = 0
      await user.click(
        within(dialog).getByRole("button", {
          name: kind === "add" ? "确认添加" : "确认替换",
        }),
      )

      expect(await screen.findByRole("alert")).toHaveTextContent("该学员手机号已不可修改")
      expect(screen.queryByRole("dialog", { name: title })).not.toBeInTheDocument()
      expect(matchingStateTracker.phoneSetterCalls).toContain("")
      expect(matchingStateTracker.reasonSetterCalls).toContain("")
      expect(document.body).not.toHaveTextContent(rawPhone)
      expect(document.body).not.toHaveTextContent(rawReason)

      await user.click(screen.getByRole("button", { name: "重试匹配资料" }))
      const opener = await screen.findByRole("button", {
        name: kind === "add" ? "添加辅助手机号" : "替换 138****8000",
      })
      await user.click(opener)
      const reopened = screen.getByRole("dialog", { name: title })
      expect(
        within(reopened).getByLabelText(kind === "add" ? "手机号" : "新手机号"),
      ).toHaveValue("")
      if (kind === "replace") {
        expect(within(reopened).getByLabelText("替换原因")).toHaveValue("")
      }
    } finally {
      matchingStateTracker.enabled = false
    }
  },
)

it("removes settled sensitive phone mutation variables immediately", async () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const user = userEvent.setup()
  renderPanel(undefined, studentId, queryClient)
  await screen.findByText("138****8000")

  await user.click(screen.getByRole("button", { name: "添加辅助手机号" }))
  const dialog = screen.getByRole("dialog", { name: "添加辅助手机号" })
  await user.type(within(dialog).getByLabelText("手机号"), "13900139000")
  await user.click(within(dialog).getByRole("button", { name: "确认添加" }))

  await waitFor(() => {
    expect(screen.queryByRole("dialog", { name: "添加辅助手机号" })).not.toBeInTheDocument()
  })
  await waitFor(() => {
    expect(
      queryClient
        .getMutationCache()
        .getAll()
        .some((mutation) => {
          const variables = mutation.state.variables
          return typeof variables === "object" && variables !== null && "write" in variables
        }),
    ).toBe(false)
  })
})

it("focuses and lets Escape close every idle matching dialog", async () => {
  const user = userEvent.setup()
  renderPanel(undefined, studentId)
  await screen.findByText("138****8000")

  const cases = [
    {
      opener: "调整账号上限",
      title: "调整游戏账号上限",
      focus: "账号上限",
      close: "关闭调整游戏账号上限",
      submit: "确认调整",
    },
    {
      opener: "添加辅助手机号",
      title: "添加辅助手机号",
      focus: "手机号",
      close: "关闭添加辅助手机号",
      submit: "确认添加",
    },
    {
      opener: "替换 138****8000",
      title: "替换辅助手机号",
      focus: "新手机号",
      close: "关闭替换辅助手机号",
      submit: "确认替换",
    },
    {
      opener: "停用 138****8000",
      title: "停用辅助手机号",
      focus: "停用原因",
      close: "关闭停用辅助手机号",
      submit: "确认停用",
    },
  ]

  for (const item of cases) {
    const opener = screen.getByRole("button", { name: item.opener })
    await user.click(opener)
    const dialog = screen.getByRole("dialog", { name: item.title })
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(within(dialog).getByLabelText(item.focus)).toHaveFocus()
    const close = within(dialog).getByRole("button", { name: item.close })
    expect(close).toBeVisible()
    expect(within(dialog).getByRole("button", { name: "取消" })).toBeVisible()
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

it("captures the selected student when opening a matching mutation dialog", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/students?")) {
      return jsonResponse({
        items: [
          { student_person_id: studentId, student_name: "张同学", student_number: "S-2026-0021" },
          { student_person_id: nextStudentId, student_name: "李同学", student_number: "S-2026-0022" },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)

  const firstStudent = await screen.findByRole("button", {
    name: `选择 张同学 · S-2026-0021 · ${studentId}`,
  })
  await user.click(firstStudent)
  await screen.findByText("FullAuthorized#ID")
  await user.click(screen.getByRole("button", { name: "添加辅助手机号" }))
  expect(screen.getByRole("dialog", { name: "添加辅助手机号" })).toBeVisible()
  fireEvent.click(
    screen.getByRole("button", {
      name: `选择 李同学 · S-2026-0022 · ${nextStudentId}`,
    }),
  )
  await screen.findByRole("heading", { name: "李同学" })
  const dialog = screen.getByRole("dialog", { name: "添加辅助手机号" })
  await user.type(within(dialog).getByLabelText("手机号"), "13900139000")
  await user.click(within(dialog).getByRole("button", { name: "确认添加" }))

  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(
        ([input, request]) =>
          String(input).endsWith(
            `/evaluations/admin/students/${studentId}/auxiliary-phones`,
          ) && request?.method === "POST",
      ),
    ).toBe(true)
  })
})

it("locks duplicate submit, close, and Escape for every pending matching dialog", async () => {
  let pendingResponse: Promise<Response> | null = null
  let resolvePending!: (response: Response) => void
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if ((init?.method === "POST" || init?.method === "PUT") && pendingResponse) {
      return pendingResponse
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock, studentId)
  await screen.findByText("138****8000")

  const cases = [
    {
      opener: "调整账号上限",
      title: "调整游戏账号上限",
      close: "关闭调整游戏账号上限",
      submit: "确认调整",
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("调整原因"), "等待调整完成")
      },
    },
    {
      opener: "添加辅助手机号",
      title: "添加辅助手机号",
      close: "关闭添加辅助手机号",
      submit: "确认添加",
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("手机号"), "13900139000")
      },
    },
    {
      opener: "替换 138****8000",
      title: "替换辅助手机号",
      close: "关闭替换辅助手机号",
      submit: "确认替换",
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("新手机号"), "13700137000")
        await user.type(within(dialog).getByLabelText("替换原因"), "联系电话更新")
      },
    },
    {
      opener: "停用 138****8000",
      title: "停用辅助手机号",
      close: "关闭停用辅助手机号",
      submit: "确认停用",
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("停用原因"), "号码不再使用")
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
    expect(
      fetchMock.mock.calls.filter(
        ([, init]) => init?.method === "POST" || init?.method === "PUT",
      ),
    ).toHaveLength(index + 1)

    resolvePending(jsonResponse(matchingProfile.auxiliary_phones[0], 201))
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: item.title })).not.toBeInTheDocument()
    })
  }
})

it("clears evaluation data without leaving an embedded ERP page when access is revoked", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (
      String(input).endsWith(`/evaluations/admin/students/${studentId}/game-account-limit`) &&
      init?.method === "PUT"
    ) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "matching-access-revoked",
        },
        403,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  queryClient.setQueryData([...evaluationKeys.root(accountId), "cached-sensitive-game-id"], {
    game_id: "CachedFull#ID",
  })
  const user = userEvent.setup()
  renderPanel(fetchMock, studentId, queryClient, `/people/${studentId}`)

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "调整账号上限" }))
  await user.type(screen.getByLabelText("调整原因"), "撤权并发测试")
  await user.click(screen.getByRole("button", { name: "确认调整" }))

  expect(await screen.findByText("正在更新基础数据权限...")).toBeVisible()
  expect(screen.getByTestId("location")).toHaveTextContent(`/people/${studentId}`)
  expect(screen.queryByText("ERP 工作台落点")).not.toBeInTheDocument()
  await waitFor(() => {
    expect(queryClient.getQueriesData({ queryKey: evaluationKeys.root(accountId) })).toEqual([])
  })
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(refreshUser).toHaveBeenCalledTimes(1)
})

it.each([
  [403, "evaluation_operation_denied", "当前账号不能调整该学员"],
  [404, "evaluation_resource_not_found", "该学员评价资料不存在"],
])("keeps the page and ERP session while clearing student caches for object-level HTTP %i %s", async (status, code, detail) => {
  const olderGameId = "ObjectDeniedOlder#ID"
  const olderPhone = "137****7333"
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.endsWith(`/evaluations/admin/students/${studentId}/game-account-limit`) &&
      init?.method === "PUT"
    ) {
      return jsonResponse(
        {
          type: "about:blank",
          title: status === 403 ? "Forbidden" : "Not Found",
          status,
          detail,
          code,
          request_id: `matching-${code}`,
        },
        status,
      )
    }
    if (
      url.includes(`/evaluations/students/${studentId}/game-accounts?`) &&
      !init?.method
    ) {
      return jsonResponse([
        {
          ...matchingProfile.game_accounts[0],
          id: "40000000-0000-4000-8000-000000000097",
          game_id: olderGameId,
          status: "unbound",
        },
      ])
    }
    if (
      url.includes(
        `/evaluations/admin/students/${studentId}/auxiliary-phones?include_inactive=true&page=2&page_size=50`,
      ) &&
      !init?.method
    ) {
      return jsonResponse([
        {
          ...matchingProfile.auxiliary_phones[0],
          id: "60000000-0000-4000-8000-000000000097",
          masked_phone: olderPhone,
          status: "inactive",
        },
      ])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  queryClient.setQueryData([...evaluationKeys.root(accountId), "cached-sensitive-game-id"], {
    game_id: "CachedFull#ID",
  })
  const user = userEvent.setup()
  renderPanel(fetchMock, studentId, queryClient, `/people/${studentId}`)

  expect(await screen.findByText("FullAuthorized#ID")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载更早游戏账号记录" }))
  expect(await screen.findByText(olderGameId)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载更早辅助手机号记录" }))
  expect(await screen.findByText(olderPhone)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "调整账号上限" }))
  await user.type(screen.getByLabelText("调整原因"), "对象级失败测试")
  await user.click(screen.getByRole("button", { name: "确认调整" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(detail)
  expect(screen.getByTestId("location")).toHaveTextContent(`/people/${studentId}`)
  expect(screen.queryByText("FullAuthorized#ID")).not.toBeInTheDocument()
  expect(screen.queryByText("138****8000")).not.toBeInTheDocument()
  expect(screen.queryByText(olderGameId)).not.toBeInTheDocument()
  expect(screen.queryByText(olderPhone)).not.toBeInTheDocument()
  const adminKey = [
    ...evaluationFoundationKeys.admin(accountId),
    "student-matching",
  ] as const
  expect(
    queryClient.getQueriesData({ queryKey: [...adminKey, "profile", studentId] }),
  ).toEqual([])
  expect(
    queryClient.getQueriesData({ queryKey: [...adminKey, "older-history", studentId] }),
  ).toEqual([])
  expect(
    queryClient.getQueryData([...evaluationKeys.root(accountId), "cached-sensitive-game-id"]),
  ).toEqual({ game_id: "CachedFull#ID" })
  expect(refreshUser).not.toHaveBeenCalled()
})
