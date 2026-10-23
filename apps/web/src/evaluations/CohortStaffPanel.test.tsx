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

import { evaluationKeys } from "./api"
import { CohortStaffPanel } from "./CohortStaffPanel"
import { EvaluationAuthorizationProvider } from "./EvaluationAuthorization"
import { evaluationFoundationKeys } from "./foundationApi"

const accountId = "10000000-0000-4000-8000-000000000001"
const cohortId = "70000000-0000-4000-8000-000000000001"
const nextCohortId = "70000000-0000-4000-8000-000000000002"
const projectId = "30000000-0000-4000-8000-000000000001"
const classId = "80000000-0000-4000-8000-000000000001"
const roleId = "41000000-0000-4000-8000-000000000001"
const staffId = "50000000-0000-4000-8000-000000000001"
const assignmentId = "51000000-0000-4000-8000-000000000001"
const refreshUser = vi.fn(async () => undefined)
let globalPermissions = ["cohort_staff.manage"]
let scopedPermissions: string[] = []

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      account_id: accountId,
      permissions: globalPermissions,
      scoped_permissions: scopedPermissions,
    },
    refreshUser,
  }),
}))

const primaryCoach = {
  id: "90000000-0000-4000-8000-000000000001",
  source: "coach_assignment",
  staff_person_id: "50000000-0000-4000-8000-000000000011",
  staff_name: "王主教练",
  role_name: "主教练",
  role_category: "teaching",
  scope_kind: "class",
  project_id: projectId,
  project_name: "英雄联盟",
  class_id: classId,
  class_name: "英雄联盟一班",
  permission_keys: [],
  valid_from: "2026-08-01T09:00:00+08:00",
  valid_until: null,
  ended_at: null,
  end_reason: null,
  manage_allowed: false,
}

const historicalAssistant = {
  ...primaryCoach,
  id: "90000000-0000-4000-8000-000000000002",
  staff_name: "李助理教练",
  role_name: "助理教练",
  ended_at: "2026-08-05T18:00:00+08:00",
  end_reason: "教学排班结束",
}

const activeService = {
  id: assignmentId,
  source: "cohort_staff_assignment",
  staff_person_id: staffId,
  staff_name: "赵生活老师",
  role_name: "生活老师",
  role_category: "life_service",
  scope_kind: "project",
  project_id: projectId,
  project_name: "英雄联盟",
  class_id: null,
  class_name: null,
  permission_keys: ["evaluation_media.original.read"],
  valid_from: "2026-08-01T09:00:00+08:00",
  valid_until: "2026-08-22T18:00:00+08:00",
  ended_at: null,
  end_reason: null,
  manage_allowed: true,
}

const nextCohortService = {
  ...activeService,
  id: "51000000-0000-4000-8000-000000000002",
  staff_person_id: "50000000-0000-4000-8000-000000000002",
  staff_name: "钱营期老师",
  project_name: "无畏契约",
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
    if (url.endsWith("/evaluations/admin/staff-roles") && !init?.method) {
      return jsonResponse([
        {
          id: roleId,
          key: "life_teacher",
          name: "生活老师",
          category: "life_service",
          is_builtin: true,
          is_active: true,
        },
        {
          id: "41000000-0000-4000-8000-000000000002",
          key: "team_manager",
          name: "战队领队",
          category: "operations",
          is_builtin: false,
          is_active: true,
        },
      ])
    }
    if (
      url.includes("/evaluations/admin/staff-directory?") &&
      url.includes(`cohort_id=${cohortId}`)
    ) {
      return jsonResponse({
        items: [
          {
            staff_person_id: staffId,
            staff_name: "赵生活老师",
            staff_type: "life_teacher",
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      })
    }
    if (url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`)) {
      if (init?.method === "POST") return jsonResponse(activeService, 201)
      return jsonResponse([primaryCoach, historicalAssistant, activeService])
    }
    if (url.includes("/evaluations/admin/cohorts?")) {
      return jsonResponse({
        items: [{ id: cohortId, name: "评价权限营期" }],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    if (
      url.includes(`/evaluations/admin/cohorts/${cohortId}/staff-scopes?`) &&
      url.includes("scope_kind=project")
    ) {
      return jsonResponse({
        items: [
          {
            id: projectId,
            name: "英雄联盟",
            project_id: projectId,
            project_name: "英雄联盟",
            is_active: true,
          },
        ],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    if (
      url.includes(`/evaluations/admin/cohorts/${cohortId}/staff-scopes?`) &&
      url.includes("scope_kind=class")
    ) {
      return jsonResponse({
        items: [
          {
            id: classId,
            name: "英雄联盟一班",
            project_id: projectId,
            project_name: "英雄联盟",
            is_active: true,
          },
        ],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    if (url.endsWith(`/evaluations/admin/staff-assignments/${assignmentId}/end`)) {
      return jsonResponse({
        ...activeService,
        ended_at: "2026-08-10T18:00:00+08:00",
        end_reason: "服务周期结束",
      })
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
  options: {
    queryClient?: QueryClient
    initialEntry?: string
    fixedCohort?: boolean
    onAccessRevoked?: () => void
  } = {},
) {
  vi.stubGlobal("fetch", fetchMock)
  const queryClient =
    options.queryClient ??
    new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
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
          {
            path: "*",
            element: (
              <CohortStaffPanel
                cohortId={options.fixedCohort === false ? undefined : cohortId}
                onAccessRevoked={options.onAccessRevoked}
              />
            ),
          },
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
  return {
    fetchMock,
    queryClient,
    setAuthorization(next: { pending: boolean; revision: number }) {
      authorization = next
      view.rerender(tree())
    },
  }
}

afterEach(() => {
  globalPermissions = ["cohort_staff.manage"]
  scopedPermissions = []
  refreshUser.mockClear()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it("revalidates staff directories without exposing cached data across failure and retry", async () => {
  let roleCalls = 0
  let assignmentCalls = 0
  let resolveRoles!: (response: Response) => void
  let resolveAssignments!: (response: Response) => void
  let resolveRoleRetry!: (response: Response) => void
  let resolveAssignmentRetry!: (response: Response) => void
  const pendingRoles = new Promise<Response>((resolve) => {
    resolveRoles = resolve
  })
  const pendingAssignments = new Promise<Response>((resolve) => {
    resolveAssignments = resolve
  })
  const pendingRoleRetry = new Promise<Response>((resolve) => {
    resolveRoleRetry = resolve
  })
  const pendingAssignmentRetry = new Promise<Response>((resolve) => {
    resolveAssignmentRetry = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/evaluations/admin/staff-roles") && !init?.method) {
      roleCalls += 1
      if (roleCalls === 2) return pendingRoles
      if (roleCalls === 3) return pendingRoleRetry
    }
    if (url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) && !init?.method) {
      assignmentCalls += 1
      if (assignmentCalls === 2) return pendingAssignments
      if (assignmentCalls === 3) return pendingAssignmentRetry
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const result = renderPanel(fetchMock)

  expect(await screen.findByText("赵生活老师")).toBeVisible()
  expect(screen.getAllByText("生活老师").length).toBeGreaterThan(0)

  result.setAuthorization({ pending: false, revision: 2 })
  await waitFor(() => {
    expect(roleCalls).toBe(2)
    expect(assignmentCalls).toBe(2)
  })
  expect(screen.getByText("正在更新工作人员数据...")).toBeVisible()
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()
  expect(screen.queryByText("王主教练")).not.toBeInTheDocument()

  const failure = jsonResponse(
    {
      type: "about:blank",
      title: "Service unavailable",
      status: 503,
      detail: "工作人员授权目录暂时不可用",
      code: "staff_directory_unavailable",
      request_id: "staff-directory-refresh",
    },
    503,
  )
  resolveRoles(failure)
  resolveAssignments(
    jsonResponse(
      {
        type: "about:blank",
        title: "Service unavailable",
        status: 503,
        detail: "工作人员授权目录暂时不可用",
        code: "staff_directory_unavailable",
        request_id: "staff-assignment-refresh",
      },
      503,
    ),
  )

  const error = await screen.findByRole("alert")
  expect(error).toHaveTextContent("工作人员授权目录暂时不可用")
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()
  await user.click(within(error).getByRole("button", { name: "重试工作人员数据" }))

  await waitFor(() => {
    expect(roleCalls).toBe(3)
    expect(assignmentCalls).toBe(3)
  })
  expect(screen.getByText("正在更新工作人员数据...")).toBeVisible()
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()

  resolveRoleRetry(
    jsonResponse([
      {
        id: roleId,
        key: "life_teacher",
        name: "生活老师",
        category: "life_service",
        is_builtin: true,
        is_active: true,
      },
    ]),
  )
  resolveAssignmentRetry(jsonResponse([activeService]))

  expect(await screen.findByText("赵生活老师")).toBeVisible()
})

it("keeps global role governance hidden from scoped staff managers", async () => {
  globalPermissions = []
  scopedPermissions = ["cohort_staff.manage"]
  const user = userEvent.setup()
  renderPanel()

  expect(await screen.findByText("赵生活老师")).toBeVisible()
  expect(screen.queryByRole("button", { name: "新增工作人员角色" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "停用" })).not.toBeInTheDocument()
  const assignmentButton = screen.getByRole("button", { name: "新增工作人员服务" })
  expect(assignmentButton).toBeVisible()

  await user.click(assignmentButton)
  expect(
    await within(screen.getByRole("dialog", { name: "新增工作人员服务" })).findByRole(
      "option",
      { name: "战队领队" },
    ),
  ).toBeVisible()
})

it("keeps primary and assistant coach history read-only beside manageable staff service", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (
      String(input).endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
      !init?.method
    ) {
      return jsonResponse([
        { ...primaryCoach, manage_allowed: true },
        historicalAssistant,
        activeService,
      ])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  renderPanel(fetchMock)

  expect(await screen.findByText("王主教练")).toBeVisible()
  expect(screen.getByText("主教练")).toBeVisible()
  expect(screen.getByText("李助理教练")).toBeVisible()
  expect(screen.getByText("教学排班结束")).toBeVisible()
  expect(screen.getByText("赵生活老师")).toBeVisible()
  expect(screen.getByText("查看评价媒体原图")).toBeVisible()
  expect(screen.queryByRole("button", { name: "结束 王主教练 的服务" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "结束 李助理教练 的服务" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "结束 赵生活老师 的服务" })).toBeVisible()
})

it("defaults a new staff service to the current Shanghai minute", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date("2027-01-15T02:34:00Z"))
  const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
  renderPanel()

  await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))

  expect(
    within(screen.getByRole("dialog", { name: "新增工作人员服务" })).getByLabelText(
      "开始时间",
    ),
  ).toHaveValue("2027-01-15T10:34")
})

it("searches the eligible staff directory instead of limiting assignment to the first page", async () => {
  const initialFetch = baseFetch()
  const searchedStaffId = "50000000-0000-4000-8000-000000000099"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/staff-directory?") && url.includes("query=%E9%92%B1")) {
      return jsonResponse({
        items: [
          {
            staff_person_id: searchedStaffId,
            staff_name: "钱教练",
            staff_type: "coach",
          },
        ],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)

  await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
  const dialog = screen.getByRole("dialog", { name: "新增工作人员服务" })
  await user.type(within(dialog).getByLabelText("搜索工作人员"), "钱")

  const refreshedDialog = await screen.findByRole("dialog", { name: "新增工作人员服务" })
  expect(await within(refreshedDialog).findByRole("option", { name: "钱教练" })).toHaveValue(
    searchedStaffId,
  )
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(
        `staff-directory?cohort_id=${cohortId}&query=%E9%92%B1&page=1&page_size=100`,
      ),
    ),
  ).toBe(true)
})

it("loads staff and assignment scopes from cohort-scoped evaluation directories", async () => {
  const user = userEvent.setup()
  const { fetchMock } = renderPanel()

  await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
  const dialog = screen.getByRole("dialog", { name: "新增工作人员服务" })

  expect(await within(dialog).findByRole("option", { name: "赵生活老师" })).toBeVisible()
  expect(await within(dialog).findByRole("option", { name: "英雄联盟" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(
        `/evaluations/admin/staff-directory?cohort_id=${cohortId}&query=&page=1&page_size=100`,
      ),
    ),
  ).toBe(true)
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(
        `/evaluations/admin/cohorts/${cohortId}/staff-scopes?scope_kind=project&page=1&page_size=100`,
      ),
    ),
  ).toBe(true)
  expect(
    fetchMock.mock.calls.some(([input]) =>
      /\/api\/v1\/(projects|classes)\?/.test(String(input)),
    ),
  ).toBe(false)
})

it("makes an assignment scope from page 2 reachable", async () => {
  const secondProjectId = "30000000-0000-4000-8000-000000000101"
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.includes(`/evaluations/admin/cohorts/${cohortId}/staff-scopes?`) &&
      url.includes("scope_kind=project")
    ) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse({
        items:
          page === "2"
            ? [
                {
                  id: secondProjectId,
                  name: "第二页服务项目",
                  project_id: secondProjectId,
                  project_name: "第二页服务项目",
                  is_active: true,
                },
              ]
            : [
                {
                  id: projectId,
                  name: "英雄联盟",
                  project_id: projectId,
                  project_name: "英雄联盟",
                  is_active: true,
                },
              ],
        total: 101,
        page: Number(page ?? 1),
        page_size: 100,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  const { fetchMock: renderedFetch } = renderPanel(fetchMock)

  await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))

  const dialog = await screen.findByRole("dialog", { name: "新增工作人员服务" })
  expect(await within(dialog).findByRole("option", { name: "第二页服务项目" })).toHaveValue(
    secondProjectId,
  )
  expect(
    renderedFetch.mock.calls.filter(([input]) =>
      String(input).includes(
        `/evaluations/admin/cohorts/${cohortId}/staff-scopes?scope_kind=project&page=2&page_size=100`,
      ),
    ),
  ).toHaveLength(1)
})

it("loads the selectable cohort list from the evaluation directory", async () => {
  const { fetchMock } = renderPanel(baseFetch(), { fixedCohort: false })

  await screen.findByText("赵生活老师")
  expect(screen.getByRole("option", { name: "评价权限营期" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith("/evaluations/admin/cohorts?query=&page=1&page_size=100"),
    ),
  ).toBe(true)
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("/api/v1/cohorts?")),
  ).toBe(false)
})

it("switches to a remaining authorized cohort without requesting revoked assignments under a new revision", async () => {
  let directoryCalls = 0
  let resolveDirectoryRefresh!: (response: Response) => void
  const pendingDirectoryRefresh = new Promise<Response>((resolve) => {
    resolveDirectoryRefresh = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/cohorts?") && !init?.method) {
      directoryCalls += 1
      if (directoryCalls === 2) return pendingDirectoryRefresh
      return jsonResponse({
        items: [
          { id: cohortId, name: "评价权限营期" },
          { id: nextCohortId, name: "保留权限营期" },
        ],
        total: 2,
        page: 1,
        page_size: 100,
      })
    }
    if (
      url.endsWith(`/evaluations/admin/cohorts/${nextCohortId}/staff-assignments`) &&
      !init?.method
    ) {
      return jsonResponse([nextCohortService])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const result = renderPanel(fetchMock, { fixedCohort: false })

  expect(await screen.findByText("赵生活老师")).toBeVisible()

  result.setAuthorization({ pending: false, revision: 2 })

  await waitFor(() => expect(directoryCalls).toBe(2))
  expect(screen.getByText("正在更新工作人员数据...")).toBeVisible()
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith(
          `/evaluations/admin/cohorts/${cohortId}/staff-assignments`,
        ) && !init?.method,
    ),
  ).toHaveLength(1)

  resolveDirectoryRefresh(
    jsonResponse({
      items: [{ id: nextCohortId, name: "保留权限营期" }],
      total: 1,
      page: 1,
      page_size: 100,
    }),
  )

  expect(await screen.findByText("钱营期老师")).toBeVisible()
  expect(screen.getByLabelText("营期")).toHaveValue(nextCohortId)
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.filter(
      ([input, init]) =>
        String(input).endsWith(
          `/evaluations/admin/cohorts/${cohortId}/staff-assignments`,
        ) && !init?.method,
    ),
  ).toHaveLength(1)
})

it("makes a selectable cohort from page 2 reachable", async () => {
  const secondCohortId = "70000000-0000-4000-8000-000000000101"
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/cohorts?")) {
      const page = new URL(url, "http://test").searchParams.get("page")
      return jsonResponse({
        items:
          page === "2"
            ? [{ id: secondCohortId, name: "第二页评价营期" }]
            : [{ id: cohortId, name: "评价权限营期" }],
        total: 101,
        page: Number(page ?? 1),
        page_size: 100,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })

  const { fetchMock: renderedFetch } = renderPanel(fetchMock, { fixedCohort: false })

  expect(await screen.findByRole("option", { name: "第二页评价营期" })).toHaveValue(
    secondCohortId,
  )
  expect(
    renderedFetch.mock.calls.filter(([input]) =>
      String(input).endsWith("/evaluations/admin/cohorts?query=&page=2&page_size=100"),
    ),
  ).toHaveLength(1)
})

it("uses the evaluation cohort directory as the project scope source", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.includes(`/evaluations/admin/cohorts/${cohortId}/staff-scopes?`) &&
      url.includes("scope_kind=project")
    ) {
      return jsonResponse({
        items: [
          {
            id: projectId,
            name: "评价营期项目",
            project_id: projectId,
            project_name: "评价营期项目",
            is_active: true,
          },
        ],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)

  await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
  const projectScope = within(
    screen.getByRole("dialog", { name: "新增工作人员服务" }),
  ).getByLabelText("项目")

  expect(within(projectScope).getByRole("option", { name: "评价营期项目" })).toHaveValue(
    projectId,
  )
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(
        `/evaluations/admin/cohorts/${cohortId}/staff-scopes?scope_kind=project&page=1&page_size=100`,
      ),
    ),
  ).toBe(true)
})

it("creates a scoped assignment with dates and permissions", async () => {
  const user = userEvent.setup()
  const { fetchMock } = renderPanel()

  await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
  const dialog = screen.getByRole("dialog", { name: "新增工作人员服务" })
  await user.selectOptions(within(dialog).getByLabelText("工作人员"), staffId)
  await user.selectOptions(within(dialog).getByLabelText("角色"), roleId)
  await user.selectOptions(within(dialog).getByLabelText("服务范围"), "project")
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.clear(within(dialog).getByLabelText("开始时间"))
  await user.type(within(dialog).getByLabelText("开始时间"), "2026-08-01T09:00")
  await user.clear(within(dialog).getByLabelText("结束时间"))
  await user.type(within(dialog).getByLabelText("结束时间"), "2026-08-22T18:00")
  await user.click(within(dialog).getByLabelText("查看评价媒体原图"))
  await user.click(within(dialog).getByRole("button", { name: "确认创建" }))

  await waitFor(() => {
    const call = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith(`/cohorts/${cohortId}/staff-assignments`) &&
        init?.method === "POST",
    )
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      role_id: roleId,
      staff_person_id: staffId,
      scope_kind: "project",
      scope_id: projectId,
      valid_from: "2026-08-01T09:00:00+08:00",
      valid_until: "2026-08-22T18:00:00+08:00",
      permission_keys: ["evaluation_media.original.read"],
    })
  })
})

it.each([
  { operation: "create" as const, status: 403 },
  { operation: "end" as const, status: 404 },
])(
  "clears fixed cohort staff data and notifies the parent when assignment $operation gets object $status",
  async ({ operation, status }) => {
    const initialFetch = baseFetch()
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const deniedCreate =
        operation === "create" &&
        url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
        init?.method === "POST"
      const deniedEnd =
        operation === "end" &&
        url.endsWith(`/evaluations/admin/staff-assignments/${assignmentId}/end`) &&
        init?.method === "POST"
      if (deniedCreate || deniedEnd) {
        return jsonResponse(
          {
            type: "about:blank",
            title: status === 403 ? "Forbidden" : "Not found",
            status,
            detail: "当前营期工作人员权限不存在",
            code: "cohort_staff_access_denied",
            request_id: `assignment-${operation}-${status}`,
          },
          status,
        )
      }
      return initialFetch.getMockImplementation()!(input, init)
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const cohortStaffCacheKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "staff",
      "cached",
      cohortId,
    ] as const
    const otherCohortStaffCacheKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "staff",
      "cached",
      "70000000-0000-4000-8000-000000000099",
    ] as const
    queryClient.setQueryData(cohortStaffCacheKey, { staff_name: "当前营期缓存" })
    queryClient.setQueryData(otherCohortStaffCacheKey, { staff_name: "其他营期缓存" })
    const onAccessRevoked = vi.fn()
    const user = userEvent.setup()
    renderPanel(fetchMock, {
      queryClient,
      initialEntry: `/teaching/cohorts/${cohortId}`,
      onAccessRevoked,
    })

    if (operation === "create") {
      await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
      const dialog = screen.getByRole("dialog", { name: "新增工作人员服务" })
      await user.selectOptions(within(dialog).getByLabelText("工作人员"), staffId)
      await user.selectOptions(within(dialog).getByLabelText("角色"), roleId)
      await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
      await user.click(within(dialog).getByRole("button", { name: "确认创建" }))
    } else {
      await user.click(await screen.findByRole("button", { name: "结束 赵生活老师 的服务" }))
      const dialog = screen.getByRole("dialog", { name: "结束工作人员服务" })
      await user.type(within(dialog).getByLabelText("结束原因"), "对象权限已失效")
      await user.click(within(dialog).getByRole("button", { name: "确认结束" }))
    }

    const dialogName = operation === "create" ? "新增工作人员服务" : "结束工作人员服务"
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: dialogName })).not.toBeInTheDocument()
      expect(onAccessRevoked).toHaveBeenCalledTimes(1)
    })
    expect(queryClient.getQueryData(cohortStaffCacheKey)).toBeUndefined()
    expect(queryClient.getQueryData(otherCohortStaffCacheKey)).toEqual({
      staff_name: "其他营期缓存",
    })
    expect(screen.getByTestId("location")).toHaveTextContent(`/teaching/cohorts/${cohortId}`)
    expect(refreshUser).not.toHaveBeenCalled()
  },
)

it.each([
  { operation: "create" as const, status: 403, code: "evaluation_operation_denied" },
  { operation: "create" as const, status: 404, code: "evaluation_resource_not_found" },
  { operation: "end" as const, status: 403, code: "evaluation_operation_denied" },
  { operation: "end" as const, status: 404, code: "evaluation_resource_not_found" },
])(
  "recovers standalone cohort B without clearing it when cohort A assignment $operation gets object $status",
  async ({ operation, status, code }) => {
    let directoryCalls = 0
    let resolveDenied!: (response: Response) => void
    const pendingDenied = new Promise<Response>((resolve) => {
      resolveDenied = resolve
    })
    const initialFetch = baseFetch()
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.includes("/evaluations/admin/cohorts?") && !init?.method) {
        directoryCalls += 1
        return jsonResponse({
          items:
            directoryCalls === 1
              ? [
                  { id: cohortId, name: "评价权限营期" },
                  { id: nextCohortId, name: "保留权限营期" },
                ]
              : [{ id: nextCohortId, name: "保留权限营期" }],
          total: directoryCalls === 1 ? 2 : 1,
          page: 1,
          page_size: 100,
        })
      }
      const deniedCreate =
        operation === "create" &&
        url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
        init?.method === "POST"
      const deniedEnd =
        operation === "end" &&
        url.endsWith(`/evaluations/admin/staff-assignments/${assignmentId}/end`) &&
        init?.method === "POST"
      if (deniedCreate || deniedEnd) return pendingDenied
      if (
        url.endsWith(`/evaluations/admin/cohorts/${nextCohortId}/staff-assignments`) &&
        !init?.method
      ) {
        return jsonResponse([nextCohortService])
      }
      return initialFetch.getMockImplementation()!(input, init)
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const affectedCacheKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "staff",
      "cached",
      cohortId,
    ] as const
    const unrelatedCacheKey = [
      ...evaluationFoundationKeys.admin(accountId),
      "staff",
      "cached",
      nextCohortId,
    ] as const
    queryClient.setQueryData(affectedCacheKey, { staff_name: "A 营期缓存" })
    queryClient.setQueryData(unrelatedCacheKey, { staff_name: "B 营期缓存" })
    const onAccessRevoked = vi.fn()
    const user = userEvent.setup()
    const result = renderPanel(fetchMock, {
      fixedCohort: false,
      queryClient,
      onAccessRevoked,
    })

    expect(await screen.findByText("赵生活老师")).toBeVisible()
    if (operation === "create") {
      await user.click(screen.getByRole("button", { name: "新增工作人员服务" }))
      const dialog = screen.getByRole("dialog", { name: "新增工作人员服务" })
      await user.selectOptions(within(dialog).getByLabelText("工作人员"), staffId)
      await user.selectOptions(within(dialog).getByLabelText("角色"), roleId)
      await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
      await user.click(within(dialog).getByRole("button", { name: "确认创建" }))
    } else {
      await user.click(screen.getByRole("button", { name: "结束 赵生活老师 的服务" }))
      const dialog = screen.getByRole("dialog", { name: "结束工作人员服务" })
      await user.type(within(dialog).getByLabelText("结束原因"), "A 营期权限失效")
      await user.click(within(dialog).getByRole("button", { name: "确认结束" }))
    }
    await waitFor(() => {
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true)
    })

    result.setAuthorization({ pending: false, revision: 2 })

    expect(await screen.findByText("钱营期老师")).toBeVisible()
    expect(screen.getByLabelText("营期")).toHaveValue(nextCohortId)
    expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()

    resolveDenied(
      jsonResponse(
        {
          type: "about:blank",
          title: status === 403 ? "Forbidden" : "Not found",
          status,
          detail: "A 营期工作人员权限不存在",
          code,
          request_id: `standalone-assignment-${operation}-${status}`,
        },
        status,
      ),
    )

    await waitFor(() => {
      expect(
        screen.queryByRole("dialog", {
          name: operation === "create" ? "新增工作人员服务" : "结束工作人员服务",
        }),
      ).not.toBeInTheDocument()
      expect(queryClient.getQueryData(affectedCacheKey)).toBeUndefined()
    })
    expect(screen.getByText("钱营期老师")).toBeVisible()
    expect(screen.getByLabelText("营期")).toHaveValue(nextCohortId)
    expect(queryClient.getQueryData(unrelatedCacheKey)).toEqual({
      staff_name: "B 营期缓存",
    })
    expect(screen.getByTestId("location")).toHaveTextContent("/evaluations/admin")
    expect(onAccessRevoked).not.toHaveBeenCalled()
    expect(refreshUser).not.toHaveBeenCalled()
    expect(
      fetchMock.mock.calls.filter(
        ([input, init]) =>
          String(input).endsWith(
            `/evaluations/admin/cohorts/${cohortId}/staff-assignments`,
          ) && !init?.method,
      ),
    ).toHaveLength(1)
  },
)

it("does not reselect a denied standalone cohort while its directory refetch is pending", async () => {
  let directoryCalls = 0
  let affectedAssignmentReads = 0
  let resolveDirectory!: (response: Response) => void
  const pendingDirectory = new Promise<Response>((resolve) => {
    resolveDirectory = resolve
  })
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/admin/cohorts?") && !init?.method) {
      directoryCalls += 1
      if (directoryCalls > 1) return pendingDirectory
      return jsonResponse({
        items: [
          { id: cohortId, name: "评价权限营期" },
          { id: nextCohortId, name: "保留权限营期" },
        ],
        total: 2,
        page: 1,
        page_size: 100,
      })
    }
    if (
      url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
      !init?.method
    ) {
      affectedAssignmentReads += 1
    }
    if (
      url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
      init?.method === "POST"
    ) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "A 营期工作人员权限不存在",
          code: "evaluation_operation_denied",
          request_id: "standalone-assignment-create-slow-directory",
        },
        403,
      )
    }
    if (
      url.endsWith(`/evaluations/admin/cohorts/${nextCohortId}/staff-assignments`) &&
      !init?.method
    ) {
      return jsonResponse([nextCohortService])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const cohortDirectoryKey = [
    ...evaluationFoundationKeys.staffCohorts(accountId),
    "authorization",
    1,
  ] as const
  const user = userEvent.setup()
  renderPanel(fetchMock, { fixedCohort: false, queryClient })

  expect(await screen.findByText("赵生活老师")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "新增工作人员服务" }))
  const dialog = screen.getByRole("dialog", { name: "新增工作人员服务" })
  await user.selectOptions(within(dialog).getByLabelText("工作人员"), staffId)
  await user.selectOptions(within(dialog).getByLabelText("角色"), roleId)
  await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
  await user.click(within(dialog).getByRole("button", { name: "确认创建" }))

  await waitFor(() => {
    expect(directoryCalls).toBe(2)
    expect(screen.getByText("正在更新工作人员数据...")).toBeVisible()
  })
  expect(
    screen.queryByRole("dialog", { name: "新增工作人员服务" }),
  ).not.toBeInTheDocument()
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(queryClient.getQueryData(cohortDirectoryKey)).toEqual([
    { id: nextCohortId, name: "保留权限营期" },
  ])
  expect(affectedAssignmentReads).toBe(1)

  resolveDirectory(
    jsonResponse({
      items: [{ id: nextCohortId, name: "保留权限营期" }],
      total: 1,
      page: 1,
      page_size: 100,
    }),
  )

  expect(await screen.findByText("钱营期老师")).toBeVisible()
  expect(screen.getByLabelText("营期")).toHaveValue(nextCohortId)
  expect(screen.queryByText("赵生活老师")).not.toBeInTheDocument()
  expect(affectedAssignmentReads).toBe(1)
  expect(screen.getByTestId("location")).toHaveTextContent("/evaluations/admin")
  expect(refreshUser).not.toHaveBeenCalled()
})

it("requires an end reason and preserves the ended assignment as history", async () => {
  let ended = false
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith(`/evaluations/admin/staff-assignments/${assignmentId}/end`)) {
      ended = true
      return jsonResponse({
        ...activeService,
        ended_at: "2026-08-10T18:00:00+08:00",
        end_reason: "服务周期结束",
      })
    }
    if (
      url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
      !init?.method &&
      ended
    ) {
      return jsonResponse([
        primaryCoach,
        historicalAssistant,
        {
          ...activeService,
          ended_at: "2026-08-10T18:00:00+08:00",
          end_reason: "服务周期结束",
        },
      ])
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)

  await user.click(await screen.findByRole("button", { name: "结束 赵生活老师 的服务" }))
  const dialog = screen.getByRole("dialog", { name: "结束工作人员服务" })
  await user.click(within(dialog).getByRole("button", { name: "确认结束" }))
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/end"))).toBe(false)

  await user.type(within(dialog).getByLabelText("结束原因"), "服务周期结束")
  await user.click(within(dialog).getByRole("button", { name: "确认结束" }))

  expect(await screen.findByText("服务周期结束")).toBeVisible()
  expect(screen.getByText("赵生活老师")).toBeVisible()
  expect(screen.queryByRole("button", { name: "结束 赵生活老师 的服务" })).not.toBeInTheDocument()
})

it("focuses and lets Escape close every idle staff dialog", async () => {
  const user = userEvent.setup()
  renderPanel()
  await screen.findByText("赵生活老师")

  const cases = [
    {
      opener: "新增工作人员服务",
      title: "新增工作人员服务",
      focus: "工作人员",
      close: "关闭新增工作人员服务",
      submit: "确认创建",
    },
    {
      opener: "新增工作人员角色",
      title: "新增工作人员角色",
      focus: "角色标识",
      close: "关闭新增工作人员角色",
      submit: "确认创建",
    },
    {
      opener: "结束 赵生活老师 的服务",
      title: "结束工作人员服务",
      focus: "结束原因",
      close: "关闭结束工作人员服务",
      submit: "确认结束",
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
    expect(screen.getByRole("button", { name: item.opener })).toHaveFocus()
  }
})

it("locks duplicate submit, close, and Escape for every pending staff dialog", async () => {
  let pendingResponse: Promise<Response> | null = null
  let resolvePending!: (response: Response) => void
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === "POST" && pendingResponse) {
      return pendingResponse
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const user = userEvent.setup()
  renderPanel(fetchMock)
  await screen.findByText("赵生活老师")

  const cases = [
    {
      opener: "新增工作人员服务",
      title: "新增工作人员服务",
      close: "关闭新增工作人员服务",
      submit: "确认创建",
      response: activeService,
      fill: async (dialog: HTMLElement) => {
        await within(dialog).findByRole("option", { name: "赵生活老师" })
        await within(dialog).findByRole("option", { name: "生活老师" })
        await within(dialog).findByRole("option", { name: "英雄联盟" })
        await user.selectOptions(within(dialog).getByLabelText("工作人员"), staffId)
        await user.selectOptions(within(dialog).getByLabelText("角色"), roleId)
        await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
      },
    },
    {
      opener: "新增工作人员角色",
      title: "新增工作人员角色",
      close: "关闭新增工作人员角色",
      submit: "确认创建",
      response: {
        id: "41000000-0000-4000-8000-000000000099",
        key: "team_manager",
        name: "战队领队",
        category: "operations",
        is_builtin: false,
        is_active: true,
      },
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("角色标识"), "team_manager")
        await user.type(within(dialog).getByLabelText("角色名称"), "战队领队")
      },
    },
    {
      opener: "结束 赵生活老师 的服务",
      title: "结束工作人员服务",
      close: "关闭结束工作人员服务",
      submit: "确认结束",
      response: { ...activeService, ended_at: "2026-08-10T18:00:00+08:00" },
      fill: async (dialog: HTMLElement) => {
        await user.type(within(dialog).getByLabelText("结束原因"), "等待结束完成")
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

    resolvePending(jsonResponse(item.response, 201))
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: item.title })).not.toBeInTheDocument()
    })
  }
})

it("clears evaluation data without leaving an embedded cohort page when access is revoked", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/evaluations/admin/staff-roles") && init?.method === "POST") {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "staff-access-revoked",
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
    game_id: "CachedStaffVisible#ID",
  })
  const user = userEvent.setup()
  renderPanel(fetchMock, {
    queryClient,
    initialEntry: `/teaching/cohorts/${cohortId}`,
  })

  await user.click(await screen.findByRole("button", { name: "新增工作人员角色" }))
  await user.type(screen.getByLabelText("角色标识"), "team_manager")
  await user.type(screen.getByLabelText("角色名称"), "战队领队")
  await user.click(screen.getByRole("button", { name: "确认创建" }))

  expect(await screen.findByText("正在更新基础数据权限...")).toBeVisible()
  expect(screen.getByTestId("location")).toHaveTextContent(`/teaching/cohorts/${cohortId}`)
  expect(screen.queryByText("ERP 工作台落点")).not.toBeInTheDocument()
  await waitFor(() => {
    expect(queryClient.getQueriesData({ queryKey: evaluationKeys.root(accountId) })).toEqual([])
  })
})

it("keeps the embedded cohort page and cached data for an object-level denial", async () => {
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/evaluations/admin/staff-roles") && init?.method === "POST") {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "当前账号不能创建工作人员角色",
          code: "evaluation_operation_denied",
          request_id: "staff-operation-denied",
        },
        403,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const cachedKey = [...evaluationKeys.root(accountId), "cached-staff-data"] as const
  queryClient.setQueryData(cachedKey, { game_id: "CachedStaff#ID" })
  const user = userEvent.setup()
  renderPanel(fetchMock, {
    queryClient,
    initialEntry: `/teaching/cohorts/${cohortId}`,
  })

  await user.click(await screen.findByRole("button", { name: "新增工作人员角色" }))
  await user.type(screen.getByLabelText("角色标识"), "team_manager")
  await user.type(screen.getByLabelText("角色名称"), "战队领队")
  await user.click(screen.getByRole("button", { name: "确认创建" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "当前账号不能创建工作人员角色",
  )
  expect(screen.getByTestId("location")).toHaveTextContent(`/teaching/cohorts/${cohortId}`)
  expect(queryClient.getQueryData(cachedKey)).toEqual({ game_id: "CachedStaff#ID" })
})

it("shows a role deactivation error without discarding scoped staff access", async () => {
  const roleToDeactivate = "41000000-0000-4000-8000-000000000002"
  const initialFetch = baseFetch()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (
      String(input).endsWith(
        `/evaluations/admin/staff-roles/${roleToDeactivate}/deactivate`,
      ) && init?.method === "POST"
    ) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "当前角色仍被有效服务使用，暂时无法停用",
          code: "evaluation_operation_denied",
          request_id: "role-deactivate-denied",
        },
        403,
      )
    }
    return initialFetch.getMockImplementation()!(input, init)
  })
  const onAccessRevoked = vi.fn()
  const user = userEvent.setup()
  renderPanel(fetchMock, { onAccessRevoked })

  await user.click(await screen.findByRole("button", { name: "停用" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "当前角色仍被有效服务使用，暂时无法停用",
  )
  expect(screen.getByText("战队领队")).toBeVisible()
  expect(screen.getByText("赵生活老师")).toBeVisible()
  expect(screen.getByRole("region", { name: "工作人员服务" })).toBeVisible()
  expect(onAccessRevoked).not.toHaveBeenCalled()
  expect(refreshUser).not.toHaveBeenCalled()
})

it.each([
  { operation: "create" as const, status: 409 },
  { operation: "create" as const, status: 422 },
  { operation: "create" as const, status: 503 },
  { operation: "end" as const, status: 409 },
  { operation: "end" as const, status: 422 },
  { operation: "end" as const, status: 503 },
])(
  "keeps assignment $operation fields recoverable after a $status response",
  async ({ operation, status }) => {
    const detail = `工作人员服务可恢复错误 ${status}`
    const initialFetch = baseFetch()
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const failedCreate =
        operation === "create" &&
        url.endsWith(`/evaluations/admin/cohorts/${cohortId}/staff-assignments`) &&
        init?.method === "POST"
      const failedEnd =
        operation === "end" &&
        url.endsWith(`/evaluations/admin/staff-assignments/${assignmentId}/end`) &&
        init?.method === "POST"
      if (failedCreate || failedEnd) {
        return jsonResponse(
          {
            type: "about:blank",
            title: status === 409 ? "Conflict" : status === 422 ? "Unprocessable" : "Unavailable",
            status,
            detail,
            code: "staff_assignment_recoverable",
            request_id: `assignment-${operation}-${status}`,
          },
          status,
        )
      }
      return initialFetch.getMockImplementation()!(input, init)
    })
    const onAccessRevoked = vi.fn()
    const user = userEvent.setup()
    renderPanel(fetchMock, { onAccessRevoked })

    if (operation === "create") {
      await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
      await user.type(
        within(screen.getByRole("dialog", { name: "新增工作人员服务" })).getByLabelText(
          "搜索工作人员",
        ),
        "赵",
      )
      const dialog = await screen.findByRole("dialog", { name: "新增工作人员服务" })
      await within(dialog).findByRole("option", { name: "赵生活老师" })
      await user.selectOptions(within(dialog).getByLabelText("工作人员"), staffId)
      await user.selectOptions(within(dialog).getByLabelText("角色"), roleId)
      await user.selectOptions(within(dialog).getByLabelText("项目"), projectId)
      await user.clear(within(dialog).getByLabelText("开始时间"))
      await user.type(within(dialog).getByLabelText("开始时间"), "2026-08-01T09:00")
      await user.type(within(dialog).getByLabelText("结束时间"), "2026-08-22T18:00")
      await user.click(within(dialog).getByLabelText("上传评价媒体"))
      await user.click(within(dialog).getByRole("button", { name: "确认创建" }))

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(detail)
      expect(dialog).toBeVisible()
      expect(within(dialog).getByLabelText("搜索工作人员")).toHaveValue("赵")
      expect(within(dialog).getByLabelText("工作人员")).toHaveValue(staffId)
      expect(within(dialog).getByLabelText("角色")).toHaveValue(roleId)
      expect(within(dialog).getByLabelText("项目")).toHaveValue(projectId)
      expect(within(dialog).getByLabelText("开始时间")).toHaveValue("2026-08-01T09:00")
      expect(within(dialog).getByLabelText("结束时间")).toHaveValue("2026-08-22T18:00")
      expect(within(dialog).getByLabelText("上传评价媒体")).toBeChecked()
    } else {
      await user.click(await screen.findByRole("button", { name: "结束 赵生活老师 的服务" }))
      const dialog = screen.getByRole("dialog", { name: "结束工作人员服务" })
      await user.type(within(dialog).getByLabelText("结束原因"), "请保留这个结束原因")
      await user.click(within(dialog).getByRole("button", { name: "确认结束" }))

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(detail)
      expect(dialog).toBeVisible()
      expect(within(dialog).getByLabelText("结束原因")).toHaveValue("请保留这个结束原因")
    }

    expect(onAccessRevoked).not.toHaveBeenCalled()
  },
)
