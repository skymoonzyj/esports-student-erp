import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  createMemoryRouter,
  Outlet,
  RouterProvider,
  useLocation,
} from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AnalysisProfilesPanel } from "./AnalysisProfilesPanel"
import { evaluationKeys } from "./api"
import { EvaluationAuthorizationProvider } from "./EvaluationAuthorization"
import { evaluationFoundationKeys } from "./foundationApi"

const accountId = "10000000-0000-4000-8000-000000000001"
const projectId = "30000000-0000-4000-8000-000000000001"
const nextProjectId = "30000000-0000-4000-8000-000000000002"
const refreshUser = vi.fn(async () => undefined)

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId }, refreshUser }),
}))

const currentProfile = {
  id: "90000000-0000-4000-8000-000000000002",
  project_id: projectId,
  project_name: "英雄联盟",
  version: 2,
  mode: "manual_only",
  adapter_id: null,
  adapter_version: null,
  scoring_rule_version: null,
  required_fields: [],
  confidence_thresholds: {},
  auto_publish_warning_levels: [],
  publish_mode: "review_required",
  effective_from: "2026-08-05T09:00:00+08:00",
  configured_by_name: "配置管理员",
  change_reason: "调整人工复核流程",
  is_current: true,
}

const historicalProfile = {
  ...currentProfile,
  id: "90000000-0000-4000-8000-000000000001",
  version: 1,
  required_fields: [],
  effective_from: "2026-08-01T09:00:00+08:00",
  change_reason: "初始人工配置",
  is_current: false,
}

const nextProjectProfile = {
  ...currentProfile,
  id: "90000000-0000-4000-8000-000000000102",
  project_id: nextProjectId,
  project_name: "无畏契约",
  version: 7,
  change_reason: "无畏契约人工分析配置",
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function baseFetch() {
  let created = false
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/projects?")) {
      return jsonResponse({
        items: [{ id: projectId, name: "英雄联盟", is_active: true }],
        page: 1,
        page_size: 100,
        total: 1,
      })
    }
    if (url.endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`)) {
      if (init?.method === "POST") {
        created = true
        return jsonResponse(
          {
            ...currentProfile,
            id: "90000000-0000-4000-8000-000000000003",
            version: 3,
            required_fields: [],
            effective_from: "2026-08-10T09:00:00+08:00",
            change_reason: "安排下期人工配置",
          },
          201,
        )
      }
      return jsonResponse(
        created
          ? [
              {
                ...currentProfile,
                id: "90000000-0000-4000-8000-000000000003",
                version: 3,
                required_fields: [],
                effective_from: "2026-08-10T09:00:00+08:00",
                change_reason: "安排下期人工配置",
              },
              { ...currentProfile, is_current: false },
              historicalProfile,
            ]
          : [currentProfile, historicalProfile],
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderPanel(
  fetchMock = baseFetch(),
  options: { queryClient?: QueryClient; initialEntry?: string } = {},
) {
  vi.stubGlobal("fetch", fetchMock)
  const queryClient =
    options.queryClient ??
    new QueryClient({
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
          { path: "/", element: <p>ERP 工作台落点</p> },
          { path: "/evaluations", element: <p>评价中心落点</p> },
          { path: "*", element: <AnalysisProfilesPanel /> },
        ],
      },
    ],
    { initialEntries: [options.initialEntry ?? "/evaluations/admin"] },
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
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it("never exposes the panel before the default project's profiles finish loading", async () => {
  let profileCalls = 0
  let resolveProfiles!: (response: Response) => void
  const pendingProfiles = new Promise<Response>((resolve) => {
    resolveProfiles = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (
      String(input).endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`) &&
      !init?.method
    ) {
      profileCalls += 1
      return pendingProfiles
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const exposedPanels: Element[] = []
  const observer = new MutationObserver((records) => {
    for (const record of records) {
      for (const addedNode of record.addedNodes) {
        if (!(addedNode instanceof Element)) continue
        if (addedNode.matches('[aria-label="分析配置"]')) exposedPanels.push(addedNode)
        exposedPanels.push(
          ...addedNode.querySelectorAll('[aria-label="分析配置"]'),
        )
      }
    }
  })
  observer.observe(document.body, { childList: true, subtree: true })

  try {
    renderPanel(fetchMock)

    await waitFor(() => expect(profileCalls).toBe(1))
    await Promise.resolve()
    expect(exposedPanels).toHaveLength(0)
    expect(screen.getByText("正在更新分析配置...")).toBeVisible()

    resolveProfiles(jsonResponse([currentProfile, historicalProfile]))
    expect(await screen.findByRole("region", { name: "分析配置" })).toBeVisible()
  } finally {
    observer.disconnect()
  }
})

it("revalidates analysis directories without exposing cached data across failure and retry", async () => {
  let projectCalls = 0
  let profileCalls = 0
  let resolveProjects!: (response: Response) => void
  let resolveProfiles!: (response: Response) => void
  let resolveProjectRetry!: (response: Response) => void
  const pendingProjects = new Promise<Response>((resolve) => {
    resolveProjects = resolve
  })
  const pendingProfiles = new Promise<Response>((resolve) => {
    resolveProfiles = resolve
  })
  const pendingProjectRetry = new Promise<Response>((resolve) => {
    resolveProjectRetry = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/projects?") && !init?.method) {
      projectCalls += 1
      if (projectCalls === 2) return pendingProjects
      if (projectCalls === 3) return pendingProjectRetry
    }
    if (
      url.endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`) &&
      !init?.method
    ) {
      profileCalls += 1
      if (profileCalls === 2) return pendingProfiles
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const result = renderPanel(fetchMock)

  expect(await screen.findByText("当前版本 V2")).toBeVisible()
  expect(screen.getByRole("option", { name: "英雄联盟" })).toBeVisible()

  result.setAuthorization({ pending: false, revision: 2 })
  await waitFor(() => {
    expect(projectCalls).toBe(2)
    expect(profileCalls).toBe(1)
  })
  expect(screen.getByText("正在更新分析配置...")).toBeVisible()
  expect(screen.queryByText("当前版本 V2")).not.toBeInTheDocument()
  expect(screen.queryByRole("option", { name: "英雄联盟" })).not.toBeInTheDocument()

  resolveProjects(
    jsonResponse(
      {
        type: "about:blank",
        title: "Service unavailable",
        status: 503,
        detail: "分析授权目录暂时不可用",
        code: "analysis_directory_unavailable",
        request_id: "analysis-project-refresh",
      },
      503,
    ),
  )
  const error = await screen.findByRole("alert")
  expect(error).toHaveTextContent("分析授权目录暂时不可用")
  expect(screen.queryByText("当前版本 V2")).not.toBeInTheDocument()
  await user.click(within(error).getByRole("button", { name: "重试分析配置" }))

  await waitFor(() => {
    expect(projectCalls).toBe(3)
    expect(profileCalls).toBe(1)
  })
  expect(screen.getByText("正在更新分析配置...")).toBeVisible()
  expect(screen.queryByText("当前版本 V2")).not.toBeInTheDocument()

  resolveProjectRetry(
    jsonResponse({
      items: [{ id: projectId, name: "英雄联盟", is_active: true }],
      page: 1,
      page_size: 100,
      total: 1,
    }),
  )
  await waitFor(() => expect(profileCalls).toBe(2))
  resolveProfiles(jsonResponse([currentProfile, historicalProfile]))

  expect(await screen.findByText("当前版本 V2")).toBeVisible()
})

it("switches to a remaining authorized project without requesting the revoked project under a new revision", async () => {
  let directoryCalls = 0
  let resolveDirectoryRefresh!: (response: Response) => void
  const pendingDirectoryRefresh = new Promise<Response>((resolve) => {
    resolveDirectoryRefresh = resolve
  })
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/projects?") && !init?.method) {
      directoryCalls += 1
      if (directoryCalls === 2) return pendingDirectoryRefresh
      return jsonResponse({
        items: [
          { id: projectId, name: "英雄联盟", is_active: true },
          { id: nextProjectId, name: "无畏契约", is_active: true },
        ],
        page: 1,
        page_size: 100,
        total: 2,
      })
    }
    if (
      url.endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`) &&
      !init?.method
    ) {
      return jsonResponse([currentProfile, historicalProfile])
    }
    if (
      url.endsWith(`/evaluations/admin/projects/${nextProjectId}/analysis-profiles`) &&
      !init?.method
    ) {
      return jsonResponse([nextProjectProfile])
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  const user = userEvent.setup()
  const result = renderPanel(fetchMock)

  expect(await screen.findByText("当前版本 V2")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "创建新版本" }))
  expect(screen.getByRole("dialog", { name: "创建分析配置新版本" })).toBeVisible()

  result.setAuthorization({ pending: false, revision: 2 })

  await waitFor(() => expect(directoryCalls).toBe(2))
  expect(screen.getByText("正在更新分析配置...")).toBeVisible()
  expect(screen.queryByText("当前版本 V2")).not.toBeInTheDocument()
  expect(
    screen.queryByRole("dialog", { name: "创建分析配置新版本" }),
  ).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith(
          `/evaluations/admin/projects/${projectId}/analysis-profiles`,
        ) && !init?.method,
    ),
  ).toHaveLength(1)

  resolveDirectoryRefresh(
    jsonResponse({
      items: [{ id: nextProjectId, name: "无畏契约", is_active: true }],
      page: 1,
      page_size: 100,
      total: 1,
    }),
  )

  expect(await screen.findByRole("heading", { name: "无畏契约分析配置" })).toBeVisible()
  expect(screen.getByText("当前版本 V7")).toBeVisible()
  expect(screen.getByRole("option", { name: "无畏契约" })).toBeVisible()
  expect(
    screen.queryByRole("dialog", { name: "创建分析配置新版本" }),
  ).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith(
          `/evaluations/admin/projects/${projectId}/analysis-profiles`,
        ) && !init?.method,
    ),
  ).toHaveLength(1)
})

it("loads projects from the evaluation analysis directory", async () => {
  const fetchMock = renderPanel()

  await screen.findByText("当前版本 V2")
  expect(screen.getByRole("option", { name: "英雄联盟" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith("/evaluations/admin/projects?query=&page=1&page_size=100"),
    ),
  ).toBe(true)
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("/api/v1/projects?")),
  ).toBe(false)
})

it("makes an analysis project from page 2 reachable", async () => {
  const secondProjectId = "30000000-0000-4000-8000-000000000101"
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/projects?") && !init?.method) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse({
        items:
          page === "2"
            ? [{ id: secondProjectId, name: "第二页分析项目", is_active: true }]
            : [{ id: projectId, name: "英雄联盟", is_active: true }],
        page: Number(page ?? 1),
        page_size: 100,
        total: 101,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })

  const renderedFetch = renderPanel(fetchMock)

  expect(await screen.findByRole("option", { name: "第二页分析项目" })).toHaveValue(
    secondProjectId,
  )
  expect(
    renderedFetch.mock.calls.filter(([input]) =>
      String(input).endsWith("/evaluations/admin/projects?query=&page=2&page_size=100"),
    ),
  ).toHaveLength(1)
})

it("shows the current profile and immutable historical versions", async () => {
  renderPanel()

  expect(await screen.findByRole("heading", { name: "英雄联盟分析配置" })).toBeVisible()
  expect(screen.getByText("当前版本 V2")).toBeVisible()
  expect(screen.getByText("历史版本 V1")).toBeVisible()
  expect(screen.getAllByText("-")).toHaveLength(2)
  expect(screen.getByText("调整人工复核流程")).toBeVisible()
  expect(screen.getByText("初始人工配置")).toBeVisible()
  expect(screen.queryByRole("button", { name: /编辑 V|删除 V/ })).not.toBeInTheDocument()
})

it("allows administrators to select automatic analysis configuration", async () => {
  renderPanel()

  await screen.findByText("当前版本 V2")
  expect(screen.getByRole("option", { name: "自动分析" })).toBeEnabled()
  expect(screen.queryByText("当前没有已验收的自动分析适配器")).not.toBeInTheDocument()
})

it("defaults a new profile version to the current Shanghai date", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date("2027-01-15T02:34:00Z"))
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  renderPanel()

  await user.click(await screen.findByRole("button", { name: "创建新版本" }))

  expect(
    within(screen.getByRole("dialog", { name: "创建分析配置新版本" })).getByLabelText(
      "生效日期",
    ),
  ).toHaveValue("2027-01-15")
})

it("appends a manual-only version with an editable non-empty screenshot contract", async () => {
  const user = userEvent.setup()
  const fetchMock = renderPanel()

  await user.click(await screen.findByRole("button", { name: "创建新版本" }))
  const dialog = screen.getByRole("dialog", { name: "创建分析配置新版本" })
  expect(within(dialog).getByText("基于最新版本 V2")).toBeVisible()
  await user.type(within(dialog).getByLabelText("必需字段"), "summary, teamwork")
  expect(within(dialog).getByLabelText("槽位 1 标识")).toHaveValue("match_result")
  expect(within(dialog).getByLabelText("槽位 1 名称")).toHaveValue("战绩截图")
  await user.clear(within(dialog).getByLabelText("生效日期"))
  await user.type(within(dialog).getByLabelText("生效日期"), "2026-08-10")
  await user.type(within(dialog).getByLabelText("变更原因"), "安排下期人工配置")
  await user.click(within(dialog).getByRole("button", { name: "确认创建" }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      expected_current_version: 2,
      mode: "manual_only",
      adapter_id: null,
      adapter_version: null,
      scoring_rule_version: null,
      required_fields: ["summary", "teamwork"],
      media_slots: [
        {
          key: "match_result",
          label: "战绩截图",
          required: true,
        },
      ],
      confidence_thresholds: {},
      auto_publish_warning_levels: [],
      publish_mode: "review_required",
      effective_from: "2026-08-10T00:00:00+08:00",
      change_reason: "安排下期人工配置",
    })
  })
  expect(await screen.findByText("当前版本 V3")).toBeVisible()
  expect(screen.getByText("历史版本 V2")).toBeVisible()
  expect(screen.getByText("历史版本 V1")).toBeVisible()
})

it("creates an automatic profile with an explicit adapter and schema tuple", async () => {
  const user = userEvent.setup()
  const fetchMock = renderPanel()

  await user.click(await screen.findByRole("button", { name: "创建新版本" }))
  const dialog = screen.getByRole("dialog", { name: "创建分析配置新版本" })
  await user.selectOptions(within(dialog).getByLabelText("分析模式"), "automatic")
  await user.type(within(dialog).getByLabelText("适配器 ID"), "lol")
  await user.type(within(dialog).getByLabelText("适配器版本"), "1.0.0")
  await user.type(within(dialog).getByLabelText("评分规则版本"), "1")
  await user.type(within(dialog).getByLabelText("结果模式版本"), "1")
  await user.selectOptions(within(dialog).getByLabelText("发布方式"), "auto_publish")
  await user.type(
    within(dialog).getByLabelText("必需字段"),
    "kills, deaths, assists",
  )
  await user.clear(within(dialog).getByLabelText("置信度阈值"))
  await user.type(within(dialog).getByLabelText("置信度阈值"), "0.8")
  await user.clear(within(dialog).getByLabelText("槽位 1 标识"))
  await user.type(within(dialog).getByLabelText("槽位 1 标识"), "overview")
  await user.clear(within(dialog).getByLabelText("槽位 1 名称"))
  await user.type(within(dialog).getByLabelText("槽位 1 名称"), "赛后概览")
  await user.clear(within(dialog).getByLabelText("生效日期"))
  await user.type(within(dialog).getByLabelText("生效日期"), "2026-08-10")
  await user.type(within(dialog).getByLabelText("变更原因"), "启用英雄联盟自动分析")
  await user.click(within(dialog).getByRole("button", { name: "确认创建" }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      expected_current_version: 2,
      mode: "automatic",
      adapter_id: "lol",
      adapter_version: "1.0.0",
      scoring_rule_version: "1",
      result_schema_version: "1",
      required_fields: ["kills", "deaths", "assists"],
      media_slots: [
        {
          key: "overview",
          label: "赛后概览",
          required: true,
        },
      ],
      confidence_thresholds: {
        kills: 0.8,
        deaths: 0.8,
        assists: 0.8,
      },
      auto_publish_warning_levels: [],
      publish_mode: "auto_publish",
      effective_from: "2026-08-10T00:00:00+08:00",
      change_reason: "启用英雄联盟自动分析",
    })
  })
})

it("uses the highest scheduled version for optimistic creation while current display stays effective", async () => {
  const futureProfile = {
    ...currentProfile,
    id: "90000000-0000-4000-8000-000000000003",
    version: 3,
    effective_from: "2026-09-01T09:00:00+08:00",
    change_reason: "下月生效配置",
    is_current: false,
  }
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`)) {
      if (init?.method === "POST") {
        return jsonResponse({ ...futureProfile, version: 4, is_current: false }, 201)
      }
      return jsonResponse([futureProfile, currentProfile, historicalProfile])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)

  expect(await screen.findByText("当前版本 V2")).toBeVisible()
  expect(screen.getByText("历史版本 V3")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "创建新版本" }))
  const dialog = screen.getByRole("dialog", { name: "创建分析配置新版本" })
  expect(within(dialog).getByText("基于最新版本 V3")).toBeVisible()
  await user.type(within(dialog).getByLabelText("必需字段"), "summary")
  await user.type(within(dialog).getByLabelText("变更原因"), "追加未来配置版本")
  await user.click(within(dialog).getByRole("button", { name: "确认创建" }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")
    expect(JSON.parse(String(call?.[1]?.body))).toEqual(
      expect.objectContaining({
        expected_current_version: 3,
        mode: "manual_only",
        required_fields: ["summary"],
      }),
    )
  })
})

it("focuses the analysis dialog and lets Escape close it while idle", async () => {
  const user = userEvent.setup()
  renderPanel()

  const opener = await screen.findByRole("button", { name: "创建新版本" })
  await user.click(opener)
  const dialog = screen.getByRole("dialog", { name: "创建分析配置新版本" })
  expect(dialog).toHaveAttribute("aria-modal", "true")
  expect(within(dialog).getByLabelText("生效日期")).toHaveFocus()
  await user.type(within(dialog).getByLabelText("必需字段"), "summary")
  const close = within(dialog).getByRole("button", { name: "关闭创建分析配置新版本" })
  expect(close).toBeVisible()
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeVisible()
  const submit = within(dialog).getByRole("button", { name: "确认创建" })
  expect(submit).toBeVisible()

  close.focus()
  await user.keyboard("{Shift>}{Tab}{/Shift}")
  expect(submit).toHaveFocus()
  submit.focus()
  await user.tab()
  expect(close).toHaveFocus()

  await user.keyboard("{Escape}")
  expect(
    screen.queryByRole("dialog", { name: "创建分析配置新版本" }),
  ).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
})

it("locks duplicate submit and close while append-only creation is pending", async () => {
  let resolveCreate!: (response: Response) => void
  const pending = new Promise<Response>((resolve) => {
    resolveCreate = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (
      String(input).endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`) &&
      init?.method === "POST"
    ) {
      return pending
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)

  await user.click(await screen.findByRole("button", { name: "创建新版本" }))
  const dialog = screen.getByRole("dialog", { name: "创建分析配置新版本" })
  await user.type(within(dialog).getByLabelText("必需字段"), "summary")
  await user.type(within(dialog).getByLabelText("变更原因"), "保留等待状态测试")
  const submit = within(dialog).getByRole("button", { name: "确认创建" })
  const cancel = within(dialog).getByRole("button", { name: "取消" })
  await user.click(submit)
  await user.click(submit)
  await user.click(cancel)
  await user.keyboard("{Escape}")

  expect(submit).toBeDisabled()
  expect(cancel).toBeDisabled()
  expect(
    within(dialog).getByRole("button", { name: "关闭创建分析配置新版本" }),
  ).toBeDisabled()
  expect(screen.getByRole("dialog", { name: "创建分析配置新版本" })).toBeVisible()
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1)

  resolveCreate(
    jsonResponse(
      {
        ...currentProfile,
        id: "90000000-0000-4000-8000-000000000003",
        version: 3,
        change_reason: "保留等待状态测试",
      },
      201,
    ),
  )
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "创建分析配置新版本" })).not.toBeInTheDocument())
})

it("clears evaluation caches and leaves protected routes when profile administration is revoked", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (
      String(input).endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`) &&
      init?.method === "POST"
    ) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "analysis-access-revoked",
        },
        403,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  queryClient.setQueryData([...evaluationKeys.root(accountId), "cached-private-data"], {
    game_id: "CachedAnalysisVisible#ID",
  })
  const user = userEvent.setup()
  renderPanel(fetchMock, { queryClient })

  await user.click(await screen.findByRole("button", { name: "创建新版本" }))
  await user.type(screen.getByLabelText("必需字段"), "summary")
  await user.type(screen.getByLabelText("变更原因"), "撤权并发测试")
  await user.click(screen.getByRole("button", { name: "确认创建" }))

  expect(await screen.findByText("ERP 工作台落点")).toBeVisible()
  expect(screen.getByTestId("location")).toHaveTextContent("/")
  await waitFor(() => {
    expect(queryClient.getQueriesData({ queryKey: evaluationKeys.root(accountId) })).toEqual([])
  })
})

it.each([
  [403, "evaluation_operation_denied"],
  [404, "evaluation_resource_not_found"],
])(
  "clears only the affected project and recovers after object-level create HTTP %i",
  async (status, code) => {
    let directoryCalls = 0
    let resolveDirectory!: (response: Response) => void
    const pendingDirectory = new Promise<Response>((resolve) => {
      resolveDirectory = resolve
    })
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes("/evaluations/admin/projects?") && !init?.method) {
        directoryCalls += 1
        if (directoryCalls > 1) return pendingDirectory
        return jsonResponse({
          items: [
            { id: projectId, name: "英雄联盟", is_active: true },
            { id: nextProjectId, name: "无畏契约", is_active: true },
          ],
          page: 1,
          page_size: 100,
          total: 2,
        })
      }
      if (url.endsWith(`/evaluations/admin/projects/${projectId}/analysis-profiles`)) {
        if (init?.method === "POST") {
          return jsonResponse(
            {
              type: "about:blank",
              title: status === 403 ? "Forbidden" : "Not found",
              status,
              detail: "当前项目分析配置权限不存在",
              code,
              request_id: `analysis-object-${status}`,
            },
            status,
          )
        }
        return jsonResponse([currentProfile, historicalProfile])
      }
      if (
        url.endsWith(`/evaluations/admin/projects/${nextProjectId}/analysis-profiles`) &&
        !init?.method
      ) {
        return jsonResponse([nextProjectProfile])
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const adminKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "analysis-profiles",
    ] as const
    const affectedKey = [...adminKey, projectId, "authorization", 1] as const
    const unrelatedKey = [...adminKey, nextProjectId, "authorization", 0] as const
    const projectDirectoryKey = [
      ...evaluationFoundationKeys.analysisProjects(accountId),
      "authorization",
      1,
    ] as const
    queryClient.setQueryData(unrelatedKey, [{ ...nextProjectProfile, version: 6 }])
    const user = userEvent.setup()
    renderPanel(fetchMock, { queryClient, initialEntry: "/evaluations/admin" })

    expect(await screen.findByText("当前版本 V2")).toBeVisible()
    await user.click(screen.getByRole("button", { name: "创建新版本" }))
    await user.type(screen.getByLabelText("必需字段"), "summary")
    await user.type(screen.getByLabelText("变更原因"), "对象级失败测试")
    await user.click(screen.getByRole("button", { name: "确认创建" }))

    await waitFor(() => {
      expect(directoryCalls).toBe(2)
      expect(screen.getByText("正在更新分析配置...")).toBeVisible()
    })
    expect(
      screen.queryByRole("dialog", { name: "创建分析配置新版本" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText("当前版本 V2")).not.toBeInTheDocument()
    expect(screen.queryByRole("alert")).not.toBeInTheDocument()
    expect(queryClient.getQueryData(projectDirectoryKey)).toEqual([
      { id: nextProjectId, name: "无畏契约", is_active: true },
    ])
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith(
            `/evaluations/admin/projects/${projectId}/analysis-profiles`,
          ) && !init?.method,
      ),
    ).toHaveLength(1)

    resolveDirectory(
      jsonResponse({
        items: [{ id: nextProjectId, name: "无畏契约", is_active: true }],
        page: 1,
        page_size: 100,
        total: 1,
      }),
    )

    expect(await screen.findByRole("heading", { name: "无畏契约分析配置" })).toBeVisible()
    expect(screen.getByText("当前版本 V7")).toBeVisible()
    expect(
      screen.queryByRole("dialog", { name: "创建分析配置新版本" }),
    ).not.toBeInTheDocument()
    expect(screen.queryByText("当前版本 V2")).not.toBeInTheDocument()
    expect(queryClient.getQueryData(affectedKey)).toBeUndefined()
    expect(queryClient.getQueryData(unrelatedKey)).toEqual([
      { ...nextProjectProfile, version: 6 },
    ])
    expect(screen.getByTestId("location")).toHaveTextContent("/evaluations/admin")
    expect(refreshUser).not.toHaveBeenCalled()
    expect(directoryCalls).toBe(2)
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith(
            `/evaluations/admin/projects/${projectId}/analysis-profiles`,
          ) && !init?.method,
      ),
    ).toHaveLength(1)
  },
)
