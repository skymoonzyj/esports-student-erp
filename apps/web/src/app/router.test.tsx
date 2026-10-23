import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { evaluationKeys } from "../evaluations/api"
import { housingKeys } from "../housing/housingKeys"
import { reportingKeys } from "../housing/reportingKeys"
import type { PortalContext } from "../portal/api"
import { portalKeys } from "../portal/api"
import { appRoutes } from "./router"
import type { HousingOverviewPageProps } from "../housing/HousingOverviewPage"

vi.mock(import("../housing/HousingOverviewPage"), () => ({
  HousingOverviewPage: ({ permissions = new Set(), scopedPermissions = new Set(), onViewRoom }: HousingOverviewPageProps = {}) => (
    <section aria-label="housing-route-props" data-global={[...permissions].join(",")} data-scoped={[...scopedPermissions].join(",")}>
      <h1>住宿房态</h1>
      <button type="button" onClick={() => onViewRoom?.("room-409")}>打开房间 409</button>
    </section>
  ),
}))

vi.mock(import("../housing/HousingRoomDetailPage"), () => ({
  HousingRoomDetailPage: () => <h1>房间运营详情</h1>,
}))

vi.mock(import("../housing/HousingReportsPage"), () => ({
  HousingReportsPage: () => <h1>实名入住报送</h1>,
}))

const noPortalContext = {
  coach_available: false,
  learning_available: false,
  payments_available: false,
  housing_management_available: false,
  housing_available: false,
  learning_mode: null,
}

afterEach(() => {
  focusManager.setFocused(undefined)
  vi.unstubAllGlobals()
})

function housingRouteFetch({
  context,
  permissions = [],
  scopedPermissions = [],
}: {
  context: PortalContext
  permissions?: string[]
  scopedPermissions?: string[]
}) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return new Response(JSON.stringify({ account_id: "account-housing", person_id: "person-housing", full_name: "房态用户", email: "housing@example.com", force_password_change: false, permissions, scoped_permissions: scopedPermissions }), { status: 200, headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/auth/csrf")) return new Response(JSON.stringify({ csrf_token: "housing-csrf" }), { status: 200, headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/portal/context")) return new Response(JSON.stringify(context), { status: 200, headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/me/housing")) return new Response(JSON.stringify({ as_of: "2026-08-06T08:00:00+08:00", subjects: [] }), { status: 200, headers: { "Content-Type": "application/json" } })
    throw new Error(`Unexpected request: ${url}`)
  })
}

it.each([
  ["global", ["housing.read"], [], "housing.read", ""],
  ["scoped", [], ["housing.read"], "", "housing.read"],
] as const)("renders housing management for a %s reader capability without promoting permission scope", async (_label, permissions, scopedPermissions, globalValue, scopedValue) => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: { ...noPortalContext, housing_management_available: true },
    permissions: [...permissions],
    scopedPermissions: [...scopedPermissions],
  }))
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  expect(await screen.findByRole("heading", { name: "住宿房态" })).toBeVisible()
  const props = screen.getByLabelText("housing-route-props")
  expect(props).toHaveAttribute("data-global", globalValue)
  expect(props).toHaveAttribute("data-scoped", scopedValue)
  expect(router.state.location.pathname).toBe("/housing")
})

it.each([
  ["global", ["housing.reports.export_sensitive"], []],
  ["scoped", [], ["housing.reports.export_sensitive"]],
] as const)("opens housing reports for an effective %s sensitive permission", async (
  _label,
  permissions,
  scopedPermissions,
) => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: noPortalContext,
    permissions: [...permissions],
    scopedPermissions: [...scopedPermissions],
  }))
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/housing/reports"],
  })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "实名入住报送" })).toBeVisible()
  expect(screen.getAllByRole("link", { name: "实名报送" })).toHaveLength(2)
  expect(router.state.location.pathname).toBe("/housing/reports")
})

it("removes only the current account report cache when access is unavailable", async () => {
  vi.stubGlobal("fetch", housingRouteFetch({ context: noPortalContext }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const currentKey = reportingKeys.detail(
    "account-housing",
    "revision-current",
    "batch-current",
  )
  const otherKey = reportingKeys.detail(
    "account-other",
    "revision-other",
    "batch-other",
  )
  queryClient.setQueryData(currentKey, { private_name: "当前账号实名缓存" })
  queryClient.setQueryData(otherKey, { private_name: "其他账号实名缓存" })
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/housing/reports"],
  })

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  await waitFor(() => expect(queryClient.getQueryData(currentKey)).toBeUndefined())
  expect(queryClient.getQueryData(otherKey)).toEqual({
    private_name: "其他账号实名缓存",
  })
})

it("opens a room detail route through the housing overview callback under the same server capability", async () => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: { ...noPortalContext, housing_management_available: true },
    permissions: ["housing.read"],
  }))
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)

  await user.click(await screen.findByRole("button", { name: "打开房间 409" }))

  expect(await screen.findByRole("heading", { name: "房间运营详情" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/housing/rooms/room-409")
})

it.each([
  ["global", ["housing.read"], []],
  ["scoped", [], ["housing.read"]],
] as const)("does not let a raw %s housing.read grant bypass a false management capability", async (_label, permissions, scopedPermissions) => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: noPortalContext,
    permissions: [...permissions],
    scopedPermissions: [...scopedPermissions],
  }))
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "住宿房态" })).not.toBeInTheDocument()
  expect(router.state.location.pathname).toBe("/")
})

it("clears only the current personal housing cache when another portal route confirms it unavailable", async () => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: { ...noPortalContext, housing_management_available: true },
    permissions: ["housing.read"],
  }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(portalKeys.housing("account-housing"), {
    private_name: "当前账号旧住宿缓存",
  })
  queryClient.setQueryData(portalKeys.housing("account-other"), {
    private_name: "其他账号住宿缓存",
  })
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  render(<QueryClientProvider client={queryClient}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)

  expect(await screen.findByRole("heading", { name: "住宿房态" })).toBeVisible()
  await waitFor(() =>
    expect(
      queryClient.getQueryData(portalKeys.housing("account-housing")),
    ).toBeUndefined(),
  )
  expect(queryClient.getQueryData(portalKeys.housing("account-other"))).toEqual({
    private_name: "其他账号住宿缓存",
  })
})

it("clears only the current management housing tree when management capability is unavailable", async () => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: { ...noPortalContext, housing_available: true },
  }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const currentManagementKey = housingKeys.detail("account-housing", "revision-current", "room-current")
  const otherManagementKey = housingKeys.detail("account-other", "revision-other", "room-other")
  queryClient.setQueryData(currentManagementKey, { private_name: "当前账号管理缓存" })
  queryClient.setQueryData(otherManagementKey, { private_name: "其他账号管理缓存" })
  queryClient.setQueryData(portalKeys.housing("account-housing"), {
    private_name: "当前账号个人住宿缓存",
  })
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  render(<QueryClientProvider client={queryClient}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  await waitFor(() => expect(queryClient.getQueryData(currentManagementKey)).toBeUndefined())
  expect(queryClient.getQueryData(otherManagementKey)).toEqual({
    private_name: "其他账号管理缓存",
  })
  expect(queryClient.getQueryData(portalKeys.housing("account-housing"))).toEqual({
    private_name: "当前账号个人住宿缓存",
  })
})

it("keeps personal housing and housing management capabilities independent", async () => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: { ...noPortalContext, housing_available: true },
  }))
  const personalRouter = createMemoryRouter(appRoutes, { initialEntries: ["/me/housing"] })
  const view = render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={personalRouter} /></AuthProvider></QueryClientProvider>)

  expect(await screen.findByRole("heading", { name: "暂无可查看学员" })).toBeVisible()
  expect(personalRouter.state.location.pathname).toBe("/me/housing")

  view.unmount()
  vi.stubGlobal("fetch", housingRouteFetch({
    context: { ...noPortalContext, housing_available: true },
  }))
  const managementRouter = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  const managementView = render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={managementRouter} /></AuthProvider></QueryClientProvider>)

  await waitFor(() => expect(managementRouter.state.location.pathname).toBe("/"))
  expect(screen.queryByRole("heading", { name: "住宿房态" })).not.toBeInTheDocument()

  managementView.unmount()
  const managementOnlyFetch = housingRouteFetch({
    context: {
      ...noPortalContext,
      housing_management_available: true,
      housing_available: false,
    },
  })
  vi.stubGlobal("fetch", managementOnlyFetch)
  const personalRouteForManagementOnly = createMemoryRouter(appRoutes, {
    initialEntries: ["/me/housing"],
  })
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={personalRouteForManagementOnly} /></AuthProvider></QueryClientProvider>)

  await waitFor(() =>
    expect(personalRouteForManagementOnly.state.location.pathname).toBe("/"),
  )
  expect(screen.queryByRole("heading", { name: "住宿信息" })).not.toBeInTheDocument()
  expect(
    managementOnlyFetch.mock.calls.some(([request]) =>
      String(request).endsWith("/me/housing"),
    ),
  ).toBe(false)
})

it("redirects an anonymous visitor from business pages to login", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "登录已失效",
          code: "invalid_session",
          request_id: "route-guard",
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/"] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/login")
})

it("allows object-scoped accounts to let the API authorize a person detail", async () => {
  const personId = "30000000-0000-4000-8000-000000000001"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000001",
          person_id: "20000000-0000-4000-8000-000000000001",
          full_name: "学员家长",
          email: "guardian@example.com",
          force_password_change: false,
          permissions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "family-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(
        JSON.stringify({
          id: personId,
          full_name: "家庭学员",
          person_types: ["student"],
          identity_number: "110105********002X",
          phone: null,
          email: null,
          sex: "female",
          date_of_birth: "2012-01-02",
          created_at: "2026-07-26T10:00:00+08:00",
          student_number: "S-FAMILY-001",
          student_status: "active",
          staff_type: null,
          visit_reason: null,
          linked_student_id: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, {
    initialEntries: [`/people/${personId}`],
  })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "家庭学员" })).toBeVisible()
  expect(router.state.location.pathname).toBe(`/people/${personId}`)
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).endsWith(`/people/${personId}`)),
  ).toBe(true)
})

it.each([
  ["/settings/accounts", "账号管理"],
  ["/settings/permissions", "权限管理"],
  ["/settings/audit", "审计管理"],
])("does not render %s when an authenticated user lacks its permission", async (path, heading) => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      if (String(input).endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000001",
            person_id: "20000000-0000-4000-8000-000000000001",
            full_name: "普通用户",
            email: "user@example.com",
            force_password_change: false,
            permissions: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (String(input).endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "route-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      throw new Error(`Unexpected request: ${String(input)}`)
    }),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: heading })).not.toBeInTheDocument()
  expect(router.state.location.pathname).toBe("/")
})

it("opens permission administration when the account has an object-scoped grant", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000002",
            person_id: "20000000-0000-4000-8000-000000000002",
            full_name: "范围权限管理员",
            email: "scoped@example.com",
            force_password_change: false,
            permissions: [],
            scoped_permissions: ["permissions.manage"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "scoped-route-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/permission-account-directory")) {
        return new Response(
          JSON.stringify([
            {
              id: "10000000-0000-4000-8000-000000000002",
              full_name: "范围权限管理员",
              status: "active",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/roles") || url.endsWith("/permissions")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/accounts/10000000-0000-4000-8000-000000000002/access")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000002",
            roles: [],
            permission_overrides: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/settings/permissions"],
  })
  const user = userEvent.setup()

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "权限管理" })).toBeVisible()
  await user.selectOptions(
    screen.getByLabelText("账号"),
    await screen.findByRole("option", {
      name: "范围权限管理员 · 已启用 · 账号 10000000-0000-4000-8000-000000000002",
    }),
  )
  const scope = screen.getByLabelText("数据范围")
  expect(scope).toHaveValue("global")
  expect(screen.queryByRole("option", { name: "指定班级" })).not.toBeInTheDocument()
  expect(screen.queryByRole("option", { name: "指定营期" })).not.toBeInTheDocument()
  expect(screen.queryByRole("option", { name: "指定项目" })).not.toBeInTheDocument()
  expect(screen.queryByRole("option", { name: "指定报名" })).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("permission-class-directory")),
  ).toBe(false)
  expect(screen.getAllByRole("link", { name: "权限管理" })).toHaveLength(2)
  expect(router.state.location.pathname).toBe("/settings/permissions")
})

it.each([
  ["/settings/accounts", "账号管理"],
  ["/settings/permissions", "权限管理"],
  ["/settings/audit", "安全审计"],
])("renders the real permitted settings page at %s", async (path, heading) => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000001",
            person_id: "20000000-0000-4000-8000-000000000001",
            full_name: "安全管理员",
            email: "admin@example.com",
            force_password_change: false,
            permissions: ["accounts.manage", "permissions.manage", "audit.read"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "settings-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/accounts?")) {
        return new Response(
          JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/permission-account-directory")) {
        return new Response(
          JSON.stringify([
            {
              id: "10000000-0000-4000-8000-000000000001",
              full_name: "安全管理员",
              status: "active",
            },
          ]),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/roles") || url.endsWith("/permissions")) {
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/accounts/10000000-0000-4000-8000-000000000001/access")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000001",
            roles: [],
            permission_overrides: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.includes("/audit-events?")) {
        return new Response(
          JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
  if (path === "/settings/permissions") {
    const user = userEvent.setup()
    await user.selectOptions(
      screen.getByLabelText("账号"),
      await screen.findByRole("option", {
        name: "安全管理员 · 已启用 · 账号 10000000-0000-4000-8000-000000000001",
      }),
    )
    expect(screen.getByRole("option", { name: "指定班级" })).toBeVisible()
    expect(screen.getByRole("option", { name: "指定营期" })).toBeVisible()
    expect(screen.getByRole("option", { name: "指定项目" })).toBeVisible()
    expect(screen.getByRole("option", { name: "指定报名" })).toBeVisible()
  }
  expect(router.state.location.pathname).toBe(path)
})

it.each([
  ["没有教学权限", []],
  ["只有范围教学权限", ["teaching.read"]],
])("redirects %s away from the teaching catalog", async (_label, scopedPermissions) => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000010",
          person_id: "20000000-0000-4000-8000-000000000010",
          full_name: "教学受限用户",
          email: "limited@example.com",
          force_password_change: false,
          permissions: [],
          scoped_permissions: scopedPermissions,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "teaching-limited-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/teaching/catalog"],
  })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("/projects?")),
  ).toBe(false)
})

it("renders the teaching catalog for a global teaching reader", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000011",
          person_id: "20000000-0000-4000-8000-000000000011",
          full_name: "教学目录读者",
          email: "catalog@example.com",
          force_password_change: false,
          permissions: ["teaching.read"],
          scoped_permissions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "teaching-catalog-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/projects?") || url.includes("/professional-teams?")) {
      return new Response(
        JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/teaching/catalog"],
  })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("tab", { name: "电竞项目" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/teaching/catalog")
})

it("opens the authorized cohort list for a scoped-only teaching reader", async () => {
  const visibleCohort = {
    id: "70000000-0000-4000-8000-000000000001",
    name: "教练授权营期",
    starts_at: "2026-08-01T00:00:00+08:00",
    ends_at: "2026-08-08T00:00:00+08:00",
    default_check_in_at: "2026-08-01T00:00:00+08:00",
    default_check_out_at: "2026-08-08T00:00:00+08:00",
    status: "upcoming",
    auto_start_enabled: true,
    auto_complete_enabled: true,
    automation_issues: [],
    enabled_class_count: 1,
    missing_primary_class_count: 0,
    created_at: "2026-07-01T00:00:00+08:00",
    updated_at: "2026-07-01T00:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000020",
          person_id: "20000000-0000-4000-8000-000000000020",
          full_name: "范围教练",
          email: "coach@example.com",
          force_password_change: false,
          permissions: [],
          scoped_permissions: ["teaching.read"],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "scoped-teaching-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/cohorts?")) {
      return new Response(
        JSON.stringify({ items: [visibleCohort], page: 1, page_size: 20, total: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/teaching/cohorts"] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "营期管理" })).toBeVisible()
  expect(await screen.findByRole("link", { name: "教练授权营期" })).toBeVisible()
  expect(screen.queryByText("未授权营期")).not.toBeInTheDocument()
  expect(screen.getByText("第 1 / 1 页，共 1 条")).toBeVisible()
  expect(screen.queryByRole("link", { name: "项目与职业队伍" })).not.toBeInTheDocument()
  expect(router.state.location.pathname).toBe("/teaching/cohorts")
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("/projects?"))).toBe(false)
})

it("renders a cohort detail for a global teaching reader", async () => {
  const cohortId = "70000000-0000-4000-8000-000000000021"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000021",
          person_id: "20000000-0000-4000-8000-000000000021",
          full_name: "教学读者",
          email: "reader@example.com",
          force_password_change: false,
          permissions: ["teaching.read"],
          scoped_permissions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "global-teaching-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/cohorts/${cohortId}`)) {
      return new Response(
        JSON.stringify({
          id: cohortId,
          name: "全局可见营期",
          starts_at: "2026-08-01T00:00:00+08:00",
          ends_at: "2026-08-08T00:00:00+08:00",
          default_check_in_at: "2026-08-01T00:00:00+08:00",
          default_check_out_at: "2026-08-08T00:00:00+08:00",
          status: "draft",
          auto_start_enabled: false,
          auto_complete_enabled: false,
          automation_issues: [],
          enabled_class_count: 0,
          missing_primary_class_count: 0,
          created_at: "2026-07-01T00:00:00+08:00",
          updated_at: "2026-07-01T00:00:00+08:00",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes(`/cohorts/${cohortId}/status-events?`)) {
      return new Response(
        JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, {
    initialEntries: [`/teaching/cohorts/${cohortId}`],
  })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "全局可见营期" })).toBeVisible()
  expect(router.state.location.pathname).toBe(`/teaching/cohorts/${cohortId}`)
})

it.each([
  "/teaching/cohorts",
  "/teaching/cohorts/70000000-0000-4000-8000-000000000099",
])("redirects users without global or scoped teaching access from %s", async (path) => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000022",
            person_id: "20000000-0000-4000-8000-000000000022",
            full_name: "无教学权限用户",
            email: "denied@example.com",
            force_password_change: false,
            permissions: [],
            scoped_permissions: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "denied-teaching-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
})

const admissionsPermissions = [
  "teaching.read",
  "payments.read",
  "admission_cases.read",
  "enrollments.read",
]

async function renderAdmissionsRoute({
  path,
  permissions = [],
  scopedPermissions = [],
}: {
  path: string
  permissions?: string[]
  scopedPermissions?: string[]
}) {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000030",
            person_id: "20000000-0000-4000-8000-000000000030",
            full_name: "招生管理员",
            email: "admissions@example.com",
            force_password_change: false,
            permissions,
            scoped_permissions: scopedPermissions,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "admissions-route-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/api/v1/enrollments?")) {
        return new Response(
          JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/api/v1/enrollments/enrollment-1/action-context")) {
        return new Response(
          JSON.stringify({
            manage_allowed: false,
            refund_allowed: false,
            read_only_reason: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/api/v1/enrollments/enrollment-1")) {
        return new Response(
          JSON.stringify({
            id: "enrollment-1",
            admission_case_id: "case-1",
            student_person_id: "student-1",
            student_name: "路由测试学员",
            cohort_id: "cohort-1",
            cohort_name: "路由测试营期",
            class_id: "class-1",
            class_name: "路由测试班级",
            agreed_tuition_cents: null,
            payment_status: "partial",
            paid_cents: null,
            refunded_cents: null,
            net_paid_cents: null,
            due_cents: null,
            overpaid_cents: null,
            status: "active",
            confirmed_at: "2026-08-01T10:00:00+08:00",
            cancelled_at: null,
            completed_at: null,
            created_at: "2026-08-01T10:00:00+08:00",
            updated_at: "2026-08-01T10:00:00+08:00",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return router
}

it.each([
  ["/teaching/payments", "付款与退款"],
  ["/teaching/admission-cases", "待分配"],
  ["/teaching/enrollments", "正式报名"],
  ["/teaching/enrollments/enrollment-1", "路由测试学员"],
])("opens the permitted admissions route %s", async (path, heading) => {
  const router = await renderAdmissionsRoute({
    path,
    permissions: admissionsPermissions,
  })

  expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
  expect(router.state.location.pathname).toBe(path)
  expect(
    screen.getByRole("navigation", { name: "招生教学二级导航" }),
  ).toBeVisible()
})

it("renders the real enrollment list workspace instead of its route placeholder", async () => {
  await renderAdmissionsRoute({
    path: "/teaching/enrollments",
    permissions: admissionsPermissions,
  })

  expect(
    await screen.findByText("当前状态下没有正式报名记录。"),
  ).toBeVisible()
  expect(globalThis.fetch).toHaveBeenCalledWith(
    expect.stringContaining("/api/v1/enrollments?"),
    expect.objectContaining({ credentials: "include" }),
  )
})

it("renders the real enrollment detail and reads its server action context", async () => {
  await renderAdmissionsRoute({
    path: "/teaching/enrollments/enrollment-1",
    permissions: admissionsPermissions,
  })

  expect(
    await screen.findByRole("heading", { name: "路由测试学员" }),
  ).toBeVisible()
  expect(screen.getByText("路由测试班级")).toBeVisible()
  expect(globalThis.fetch).toHaveBeenCalledWith(
    "/api/v1/enrollments/enrollment-1/action-context",
    expect.objectContaining({ credentials: "include" }),
  )
})

it.each([
  "/teaching/payments",
  "/teaching/admission-cases",
  "/teaching/enrollments",
  "/teaching/enrollments/enrollment-1",
])("redirects an explicitly denied admissions route %s", async (path) => {
  const router = await renderAdmissionsRoute({ path })

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
})

it.each([
  ["/teaching/admission-cases", "admission_cases.read", "待分配"],
  ["/teaching/enrollments", "enrollments.read", "正式报名"],
  ["/teaching/enrollments/enrollment-1", "enrollments.read", "路由测试学员"],
])(
  "lets a scoped reader open server-projected list route %s",
  async (path, permission, heading) => {
    const router = await renderAdmissionsRoute({
      path,
      scopedPermissions: [permission],
    })

    expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
    expect(router.state.location.pathname).toBe(path)
  },
)

it("keeps the payment management workspace global-only", async () => {
  const router = await renderAdmissionsRoute({
    path: "/teaching/payments",
    scopedPermissions: ["payments.read"],
  })

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
})

it("renders the unified portal dashboard at the authenticated index", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (request) => {
      const url = String(request)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000091",
            person_id: "20000000-0000-4000-8000-000000000091",
            full_name: "教练门户账号",
            email: "coach-portal@example.test",
            force_password_change: false,
            permissions: [],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "portal-route-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/portal/context")) {
        return new Response(
          JSON.stringify({ ...noPortalContext, coach_available: true }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.includes("/coach/next-cohorts?")) {
        return new Response(
          JSON.stringify({
            observed_at: "2026-07-30T08:00:00+08:00",
            selected_starts_at: null,
            options: [],
            classes: [],
            items: [],
            page: 1,
            page_size: 1,
            total: 0,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/"] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("link", { name: "查看下一期名单" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
})

it("renders the existing back-office overview at the authenticated index", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (request) => {
      const url = String(request)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            account_id: "10000000-0000-4000-8000-000000000092",
            person_id: "20000000-0000-4000-8000-000000000092",
            full_name: "纯后台账号",
            email: "admin-only@example.test",
            force_password_change: false,
            permissions: ["people.list", "accounts.manage"],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "admin-route-csrf" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/portal/context")) {
        return new Response(JSON.stringify(noPortalContext), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    }),
  )
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/"] })

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(screen.queryByText("教练工作")).not.toBeInTheDocument()
})

const portalRouteCases = [
  {
    path: "/coach/next-cohorts",
    capability: "coach_available" as const,
    expected: "暂未安排下一期",
    childRequest: "/coach/next-cohorts?",
  },
  {
    path: "/learning",
    capability: "learning_available" as const,
    expected: "暂无可查看学员",
    childRequest: "/family/students",
  },
  {
    path: "/learning/student-route",
    capability: "learning_available" as const,
    expected: "路由学员",
    childRequest: "/family/students/student-route/teaching?",
  },
  {
    path: "/me/payments",
    capability: "payments_available" as const,
    expected: "暂无付款记录",
    childRequest: "/me/payments?",
  },
  {
    path: "/me/housing",
    capability: "housing_available" as const,
    expected: "暂无可查看学员",
    childRequest: "/me/housing",
  },
] as const

function portalContextFor(
  capability: (typeof portalRouteCases)[number]["capability"],
): PortalContext {
  return {
    ...noPortalContext,
    [capability]: true,
    learning_mode: capability === "learning_available" ? "family" : null,
  }
}

function portalRouteFetch(
  contextResponse: Response | (() => Promise<Response>),
  options: {
    emptyLearning?: boolean
    paymentResponse?: () => Response | Promise<Response>
    housingResponse?: () => Response | Promise<Response>
  } = {},
) {
  return vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "account-portal-route",
          person_id: "person-portal-route",
          full_name: "门户路由账号",
          email: "portal-route@example.test",
          force_password_change: false,
          permissions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "portal-guard-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/portal/context")) {
      return typeof contextResponse === "function"
        ? contextResponse()
        : contextResponse.clone()
    }
    if (url.includes("/coach/next-cohorts?")) {
      return new Response(
        JSON.stringify({
          observed_at: "2026-07-30T08:00:00+08:00",
          selected_starts_at: null,
          options: [],
          classes: [],
          items: [],
          page: 1,
          page_size: 20,
          total: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/family/students")) {
      const items = options.emptyLearning
        ? []
        : [
            {
              student_person_id: "student-route",
              student_name: "路由学员",
              student_number: "S-ROUTE",
              age_status: "adult",
              access_source: "family",
              current_enrollment: null,
            },
          ]
      return new Response(JSON.stringify({ items, total: items.length }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/family/students/student-route/teaching?")) {
      return new Response(
        JSON.stringify({
          student: {
            student_person_id: "student-route",
            student_name: "路由学员",
            student_number: "S-ROUTE",
            age_status: "adult",
            access_source: "family",
            current_enrollment: null,
          },
          items: [],
          page: 1,
          page_size: 20,
          total: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/me/payments?")) {
      if (options.paymentResponse) return options.paymentResponse()
      return new Response(
        JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/me/housing")) {
      if (options.housingResponse) return options.housingResponse()
      return new Response(
        JSON.stringify({ as_of: "2026-08-06T08:00:00+08:00", subjects: [] }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function renderPortalRoute(path: string, fetchMock: ReturnType<typeof portalRouteFetch>, queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { router, queryClient }
}

it.each(portalRouteCases)(
  "opens $path only after $capability is confirmed",
  async ({ path, capability, expected }) => {
    const context = portalContextFor(capability)
    const fetchMock = portalRouteFetch(
      new Response(JSON.stringify(context), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
      { emptyLearning: path === "/learning" },
    )
    const { router } = renderPortalRoute(path, fetchMock)

    expect(await screen.findByText(expected)).toBeVisible()
    expect(router.state.location.pathname).toBe(path)
    expect(document.querySelector("main main")).not.toBeInTheDocument()
  },
)

it.each(portalRouteCases)(
  "redirects $path when $capability is absent",
  async ({ path, childRequest }) => {
    const fetchMock = portalRouteFetch(
      new Response(JSON.stringify(noPortalContext), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    const { router } = renderPortalRoute(path, fetchMock)

    expect(await screen.findByText("当前暂无可用工作项")).toBeVisible()
    expect(router.state.location.pathname).toBe("/")
    expect(
      fetchMock.mock.calls.some(([request]) => String(request).includes(childRequest)),
    ).toBe(false)
  },
)

it.each(portalRouteCases)(
  "keeps $path on the loading screen during its first pending context load",
  async ({ path, capability, expected, childRequest }) => {
    let resolveContext!: (response: Response) => void
    const contextPromise = new Promise<Response>((resolve) => {
      resolveContext = resolve
    })
    const fetchMock = portalRouteFetch(() => contextPromise, {
      emptyLearning: path === "/learning",
    })
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    expect(
      queryClient.getQueryData(portalKeys.context("account-portal-route")),
    ).toBeUndefined()
    renderPortalRoute(path, fetchMock, queryClient)

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(([request]) =>
          String(request).endsWith("/portal/context"),
        ),
      ).toBe(true)
    })
    expect(screen.getByText("正在安全连接工作台…")).toBeVisible()
    expect(screen.getAllByRole("main")).toHaveLength(1)
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).includes(childRequest),
      ),
    ).toBe(false)

    resolveContext(
      new Response(JSON.stringify(portalContextFor(capability)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    expect(await screen.findByText(expected)).toBeVisible()
  },
)

it("does not mount a portal child while cached context is refetching", async () => {
  let resolveContext!: (response: Response) => void
  const contextPromise = new Promise<Response>((resolve) => {
    resolveContext = resolve
  })
  const fetchMock = portalRouteFetch(() => contextPromise)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(portalKeys.context("account-portal-route"), {
    ...noPortalContext,
    payments_available: true,
  })
  renderPortalRoute("/me/payments", fetchMock, queryClient)

  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/portal/context"),
      ),
    ).toBe(true)
  })
  expect(screen.getByText("正在安全连接工作台…")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) => String(request).includes("/me/payments?")),
  ).toBe(false)

  resolveContext(
    new Response(
      JSON.stringify({ ...noPortalContext, payments_available: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  )
  expect(await screen.findByText("暂无付款记录")).toBeVisible()
})

it("hides old amounts and refetches a guarded child once after focus reauthorization", async () => {
  let contextCalls = 0
  let paymentCalls = 0
  let resolveRefocusContext!: (response: Response) => void
  let resolveRefocusPayment!: (response: Response) => void
  const refocusContext = new Promise<Response>((resolve) => {
    resolveRefocusContext = resolve
  })
  const refocusPayment = new Promise<Response>((resolve) => {
    resolveRefocusPayment = resolve
  })
  const context = portalContextFor("payments_available")
  const fetchMock = portalRouteFetch(
    () => {
      contextCalls += 1
      return contextCalls === 1
        ? Promise.resolve(
            new Response(JSON.stringify(context), {
              status: 200,
              headers: { "Content-Type": "application/json" },
            }),
          )
        : refocusContext
    },
    {
      paymentResponse: () => {
        paymentCalls += 1
        return paymentCalls === 1
          ? new Response(
              JSON.stringify({
                items: [
                  {
                    payment_id: "payment-route-focus",
                    source_kind: "manual",
                    amount_cents: 100_000,
                    currency: "CNY",
                    paid_at: "2026-07-01T09:00:00+08:00",
                    refunded_cents: 0,
                    net_cents: 100_000,
                    status: "paid",
                    related_student_name: "聚焦测试学员",
                    refunds: [],
                  },
                ],
                page: 1,
                page_size: 20,
                total: 1,
              }),
              { status: 200, headers: { "Content-Type": "application/json" } },
            )
          : refocusPayment
      },
    },
  )
  renderPortalRoute("/me/payments", fetchMock)

  expect(await screen.findByText("¥1,000.00")).toBeVisible()
  expect(paymentCalls).toBe(1)

  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await waitFor(() => expect(contextCalls).toBe(2))
  expect(screen.getByText("正在安全连接工作台…")).toBeVisible()
  expect(screen.queryByText("¥1,000.00")).not.toBeInTheDocument()
  expect(paymentCalls).toBe(1)

  await act(async () => {
    resolveRefocusContext(
      new Response(JSON.stringify(context), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
  })
  await waitFor(() => expect(paymentCalls).toBe(2))
  expect(screen.getByText("正在更新付款记录")).toBeVisible()
  expect(screen.queryByText("¥1,000.00")).not.toBeInTheDocument()

  await act(async () => {
    resolveRefocusPayment(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Server Error",
          status: 500,
          detail: "付款记录暂时无法读取",
          code: "internal_error",
          request_id: "payment-focus-500",
        }),
        { status: 500, headers: { "Content-Type": "application/json" } },
      ),
    )
  })
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "付款记录暂时无法读取",
  )
  expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
  expect(paymentCalls).toBe(2)
})

it("clears only the current management housing tree after focus revokes management", async () => {
  let contextCalls = 0
  const fetchMock = portalRouteFetch(() => {
    contextCalls += 1
    return Promise.resolve(new Response(JSON.stringify({
      ...noPortalContext,
      housing_available: true,
      housing_management_available: contextCalls === 1,
    }), { status: 200, headers: { "Content-Type": "application/json" } }))
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const currentManagementKey = housingKeys.detail("account-portal-route", "revision-current", "room-current")
  const otherManagementKey = housingKeys.detail("account-other", "revision-other", "room-other")
  queryClient.setQueryData(currentManagementKey, { private_name: "撤权后必须消失" })
  queryClient.setQueryData(otherManagementKey, { private_name: "其他账号应保留" })
  queryClient.setQueryData(portalKeys.housing("account-portal-route"), {
    private_name: "个人住宿应保留",
  })
  const { router } = renderPortalRoute("/housing", fetchMock, queryClient)

  expect(await screen.findByRole("heading", { name: "住宿房态" })).toBeVisible()
  expect(queryClient.getQueryData(currentManagementKey)).toEqual({ private_name: "撤权后必须消失" })
  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(queryClient.getQueryData(currentManagementKey)).toBeUndefined()
  expect(queryClient.getQueryData(otherManagementKey)).toEqual({ private_name: "其他账号应保留" })
  expect(queryClient.getQueryData(portalKeys.housing("account-portal-route"))).toEqual({
    private_name: "个人住宿应保留",
  })
})

it("clears only the current housing cache and ignores a delayed response after focus revokes housing", async () => {
  let contextCalls = 0
  let resolveHousing!: (response: Response) => void
  const delayedHousing = new Promise<Response>((resolve) => {
    resolveHousing = resolve
  })
  const fetchMock = portalRouteFetch(
    () => {
      contextCalls += 1
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ...noPortalContext,
            housing_available: contextCalls === 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
    },
    { housingResponse: () => delayedHousing },
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(portalKeys.housing("account-portal-route"), {
    as_of: "2026-08-06T08:00:00+08:00",
    subjects: [
      {
        subject_person_id: "30000000-0000-4000-8000-000000000099",
        subject_name: "必须消失的旧住宿学员",
        current_stay: null,
        planned_stays: [],
      },
    ],
  })
  queryClient.setQueryData(portalKeys.housing("account-other"), {
    private_name: "其他账号住宿缓存",
  })
  const { router } = renderPortalRoute("/me/housing", fetchMock, queryClient)

  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/me/housing"),
      ),
    ).toBe(true)
  })
  expect(screen.queryByText("必须消失的旧住宿学员")).not.toBeInTheDocument()

  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(screen.queryByText("必须消失的旧住宿学员")).not.toBeInTheDocument()
  expect(
    queryClient.getQueryData(portalKeys.housing("account-portal-route")),
  ).toBeUndefined()
  expect(
    queryClient.getQueryData(portalKeys.housing("account-other")),
  ).toEqual({ private_name: "其他账号住宿缓存" })

  await act(async () => {
    resolveHousing(
      new Response(
        JSON.stringify({
          as_of: "2026-08-06T09:00:00+08:00",
          subjects: [
            {
              subject_person_id: "30000000-0000-4000-8000-000000000098",
              subject_name: "延迟响应不得复活的学员",
              current_stay: null,
              planned_stays: [],
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
  })

  await waitFor(() =>
    expect(
      queryClient.getQueryData(portalKeys.housing("account-portal-route")),
    ).toBeUndefined(),
  )
  expect(screen.queryByText("延迟响应不得复活的学员")).not.toBeInTheDocument()
})

it("keeps a portal route in place and retries a context server error", async () => {
  const user = userEvent.setup()
  const fetchMock = portalRouteFetch(
    new Response(
      JSON.stringify({
        type: "about:blank",
        title: "Server Error",
        status: 500,
        detail: "门户能力暂时不可用",
        code: "internal_error",
        request_id: "portal-context-500",
      }),
      { status: 500, headers: { "Content-Type": "application/json" } },
    ),
  )
  const { router } = renderPortalRoute("/me/payments", fetchMock)

  expect(await screen.findByRole("alert")).toHaveTextContent("门户能力暂时不可用")
  expect(screen.getAllByRole("main")).toHaveLength(1)
  expect(router.state.location.pathname).toBe("/me/payments")
  await user.click(screen.getByRole("button", { name: "重试" }))
  expect(
    fetchMock.mock.calls.filter(([request]) =>
      String(request).endsWith("/portal/context"),
    ).length,
  ).toBeGreaterThan(1)
})

it("returns to login and clears portal cache on invalid session", async () => {
  const fetchMock = portalRouteFetch(
    new Response(
      JSON.stringify({
        type: "about:blank",
        title: "Unauthorized",
        status: 401,
        detail: "登录已失效",
        code: "invalid_session",
        request_id: "portal-context-401",
      }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ),
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(portalKeys.payments("account-portal-route", 1), {
    items: [{ amount_cents: 100_000 }],
  })
  const { router } = renderPortalRoute("/me/payments", fetchMock, queryClient)

  expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/login")
  expect(
    queryClient.getQueryData(portalKeys.payments("account-portal-route", 1)),
  ).toBeUndefined()
})

function evaluationRouteFetch(permissions: string[]) {
  return vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "account-evaluation-route",
          person_id: "person-evaluation-route",
          full_name: "评价路由账号",
          email: "evaluation-route@example.test",
          force_password_change: false,
          permissions,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "evaluation-route-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/portal/context")) {
      return new Response(JSON.stringify(noPortalContext), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/evaluations/context")) {
      return new Response(
        JSON.stringify({
          accessible: true,
          system_evaluation_available: true,
          embedded_start_url:
            "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
          standalone_start_url:
            "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
          upload_available: false,
          video_available: false,
          reports_available: false,
          surveys_available: false,
          game_accounts_available: false,
          admin_available: false,
          admin_sections: {
            cohort_staff: false,
            student_matching: false,
            analysis_profiles: false,
          },
          integration_api_version: "v1",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function renderEvaluationRoute(
  permissions: string[],
  path = "/evaluations",
) {
  const fetchMock = evaluationRouteFetch(permissions)
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, {
    initialEntries: [path],
  })
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { fetchMock, router }
}

it("redirects an account without evaluation entry permission", async () => {
  const { fetchMock, router } = renderEvaluationRoute(["people.list"])

  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/evaluations/context"),
    ),
  ).toBe(false)
})

it("opens the evaluation center for an account with entry permission", async () => {
  const { fetchMock, router } = renderEvaluationRoute([
    "evaluation_center.access",
  ])

  expect(
    await screen.findByRole("heading", { name: "评价中心" }),
  ).toBeVisible()
  expect(router.state.location.pathname).toBe("/evaluations")
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/evaluations/context"),
    ),
  ).toBe(true)
})

it("refreshes stale auth and leaves evaluation routes after global evaluation access is revoked", async () => {
  const accountId = "account-evaluation-revoked"
  let meCalls = 0
  let contextCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      return new Response(
        JSON.stringify({
          account_id: accountId,
          person_id: "person-evaluation-revoked",
          full_name: "评价撤权账号",
          email: "evaluation-revoked@example.test",
          force_password_change: false,
          permissions:
            meCalls === 1 ? ["evaluation_center.access"] : ["people.list"],
          scoped_permissions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "evaluation-revoked-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/portal/context")) {
      return new Response(JSON.stringify(noPortalContext), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/evaluations/context")) {
      contextCalls += 1
      if (contextCalls > 1) return new Promise<Response>(() => undefined)
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "evaluation-route-access-revoked",
        }),
        {
          status: 403,
          headers: { "Content-Type": "application/problem+json" },
        },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const cachedKey = [...evaluationKeys.root(accountId), "cached-private-data"] as const
  queryClient.setQueryData(cachedKey, { game_id: "CachedRevoked#ID" })
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/evaluations"] })

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
  expect(screen.queryByRole("heading", { name: "登录工作台" })).not.toBeInTheDocument()
  expect(meCalls).toBe(2)
  expect(queryClient.getQueryData(cachedKey)).toBeUndefined()
})

it("guards the system evaluation page with the entry permission", async () => {
  const denied = renderEvaluationRoute(
    ["people.list"],
    "/evaluations/system",
  )

  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(denied.router.state.location.pathname).toBe("/")
  expect(
    denied.fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/evaluations/context"),
    ),
  ).toBe(false)
})

it("opens the system evaluation page with the entry permission", async () => {
  const allowed = renderEvaluationRoute(
    ["evaluation_center.access"],
    "/evaluations/system",
  )

  expect(
    await screen.findByRole("heading", { name: "系统评价" }),
  ).toBeVisible()
  expect(allowed.router.state.location.pathname).toBe("/evaluations/system")
})

type EvaluationChildCapabilities = {
  game_accounts_available: boolean
  admin_available?: boolean
  admin_sections: {
    cohort_staff: boolean
    student_matching: boolean
    analysis_profiles: boolean
  }
}

function evaluationChildContext(
  capabilities: EvaluationChildCapabilities,
) {
  return {
    accessible: true,
    system_evaluation_available: true,
    embedded_start_url:
      "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
    standalone_start_url:
      "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
    upload_available: false,
    video_available: false,
    reports_available: false,
    surveys_available: false,
    game_accounts_available: capabilities.game_accounts_available,
    admin_available:
      capabilities.admin_available ?? Object.values(capabilities.admin_sections).some(Boolean),
    admin_sections: capabilities.admin_sections,
    integration_api_version: "v1",
  }
}

function renderEvaluationChildRoute(
  path: "/evaluations/game-accounts" | "/evaluations/admin",
  contexts: Array<EvaluationChildCapabilities | Promise<EvaluationChildCapabilities>>,
) {
  let contextCalls = 0
  const accountId = "account-evaluation-child"
  const studentId = "student-evaluation-child"
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          person_id: "person-evaluation-child",
          full_name: "评价子路由账号",
          email: "evaluation-child@example.test",
          force_password_change: false,
          permissions: ["evaluation_center.access"],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "evaluation-child-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/portal/context")) {
      return new Response(JSON.stringify(noPortalContext), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/evaluations/context")) {
      const context = await contexts[Math.min(contextCalls, contexts.length - 1)]
      contextCalls += 1
      return new Response(JSON.stringify(evaluationChildContext(context)), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/evaluations/game-account-targets")) {
      return new Response(
        JSON.stringify([
          {
            student_person_id: studentId,
            student_name: "路由学员",
            student_number: "S-ROUTE-001",
            access_source: "self",
            active_count: 0,
            active_limit: 3,
          },
        ]),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes(`/evaluations/students/${studentId}/game-accounts?`)) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes(`/evaluations/students/${studentId}/game-account-projects?`)) {
      return new Response(
        JSON.stringify({ items: [], page: 1, page_size: 100, total: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/projects?")) {
      return new Response(
        JSON.stringify({ items: [], page: 1, page_size: 100, total: 0 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/evaluations/admin/students?")) {
      return new Response(
        JSON.stringify({
          items: [
            {
              student_person_id: studentId,
              student_name: "路由学员",
              student_number: "S-ROUTE-001",
            },
          ],
          page: 1,
          page_size: 20,
          total: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/evaluations/admin/students/${studentId}/matching-profile`)) {
      return new Response(
        JSON.stringify({
          student_person_id: studentId,
          student_name: "路由学员",
          student_number: "S-ROUTE-001",
          active_count: 0,
          active_limit: 3,
          game_accounts: [],
          game_accounts_total: 0,
          game_accounts_truncated: false,
          auxiliary_phones: [],
          auxiliary_phones_total: 0,
          auxiliary_phones_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { accountId, contextCalls: () => contextCalls, fetchMock, queryClient, router }
}

it.each([
  [
    "/evaluations/game-accounts" as const,
    { game_accounts_available: false, admin_sections: { cohort_staff: false, student_matching: false, analysis_profiles: false } },
    "/evaluations/game-account-targets",
  ],
  [
    "/evaluations/admin" as const,
    { game_accounts_available: false, admin_sections: { cohort_staff: false, student_matching: false, analysis_profiles: false } },
    "/evaluations/admin/students?",
  ],
])("requires server capability for direct child route %s despite local entry permission", async (path, context, childRequest) => {
  const result = renderEvaluationChildRoute(path, [context])

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(
    result.fetchMock.mock.calls.some(([input]) => String(input).includes(childRequest)),
  ).toBe(false)
  expect(screen.queryByRole("heading", { name: "登录工作台" })).not.toBeInTheDocument()
})

it("requires explicit admin availability even when an admin section is true", async () => {
  const result = renderEvaluationChildRoute("/evaluations/admin", [
    {
      game_accounts_available: false,
      admin_available: false,
      admin_sections: {
        cohort_staff: false,
        student_matching: true,
        analysis_profiles: false,
      },
    },
  ])

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(
    result.fetchMock.mock.calls.some(([input]) =>
      String(input).includes("/evaluations/admin/students?"),
    ),
  ).toBe(false)
})

it.each([
  [
    "/evaluations/game-accounts" as const,
    { game_accounts_available: true, admin_sections: { cohort_staff: false, student_matching: false, analysis_profiles: false } },
    "我的游戏账号",
  ],
  [
    "/evaluations/admin" as const,
    { game_accounts_available: false, admin_sections: { cohort_staff: false, student_matching: true, analysis_profiles: false } },
    "基础数据管理",
  ],
])("opens server-enabled evaluation child route %s", async (path, context, heading) => {
  const result = renderEvaluationChildRoute(path, [context])

  expect(await screen.findByRole("heading", { name: heading })).toBeVisible()
  expect(result.router.state.location.pathname).toBe(path)
})

it("returns to the evaluation center without logout when a child capability disappears", async () => {
  const enabled = {
    game_accounts_available: true,
    admin_sections: { cohort_staff: false, student_matching: false, analysis_profiles: false },
  }
  const disabled = {
    game_accounts_available: false,
    admin_sections: { cohort_staff: false, student_matching: false, analysis_profiles: false },
  }
  const result = renderEvaluationChildRoute("/evaluations/game-accounts", [enabled, disabled])

  expect(await screen.findByRole("heading", { name: "我的游戏账号" })).toBeVisible()
  await result.queryClient.invalidateQueries({
    queryKey: evaluationKeys.context(result.accountId),
  })

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(result.contextCalls()).toBeGreaterThan(1)
  expect(
    result.fetchMock.mock.calls.some(
      ([input, init]) => String(input).endsWith("/auth/logout") && init?.method === "POST",
    ),
  ).toBe(false)
  expect(screen.queryByRole("heading", { name: "登录工作台" })).not.toBeInTheDocument()
})

it("hides a child during capability revalidation and restores its unsaved form state", async () => {
  const enabled = {
    game_accounts_available: true,
    admin_sections: { cohort_staff: false, student_matching: false, analysis_profiles: false },
  }
  let resolveContext!: (context: EvaluationChildCapabilities) => void
  const pendingContext = new Promise<EvaluationChildCapabilities>((resolve) => {
    resolveContext = resolve
  })
  const result = renderEvaluationChildRoute(
    "/evaluations/game-accounts",
    [enabled, pendingContext],
  )
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "添加游戏账号" }))
  await user.type(screen.getByLabelText("游戏账号 ID"), "Unsaved#DuringRefresh")

  const refresh = result.queryClient.invalidateQueries({
    queryKey: evaluationKeys.context(result.accountId),
  })
  expect(await screen.findByText("正在安全连接工作台…")).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "添加游戏账号" })).not.toBeInTheDocument()

  resolveContext(enabled)
  await refresh

  expect(await screen.findByRole("dialog", { name: "添加游戏账号" })).toBeVisible()
  expect(screen.getByLabelText("游戏账号 ID")).toHaveValue("Unsaved#DuringRefresh")
})

it("hides an admin portal and typed phone throughout capability revalidation", async () => {
  const enabled = {
    game_accounts_available: false,
    admin_sections: {
      cohort_staff: false,
      student_matching: true,
      analysis_profiles: false,
    },
  }
  let resolveContext!: (context: EvaluationChildCapabilities) => void
  const pendingContext = new Promise<EvaluationChildCapabilities>((resolve) => {
    resolveContext = resolve
  })
  const result = renderEvaluationChildRoute("/evaluations/admin", [enabled, pendingContext])
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", {
      name: "选择 路由学员 · S-ROUTE-001 · student-evaluation-child",
    }),
  )
  await user.click(await screen.findByRole("button", { name: "添加辅助手机号" }))
  await user.type(screen.getByLabelText("手机号"), "13800138000")

  const refresh = result.queryClient.invalidateQueries({
    queryKey: evaluationKeys.context(result.accountId),
  })
  expect(await screen.findByText("正在安全连接工作台…")).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "添加辅助手机号" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("13800138000")).not.toBeInTheDocument()

  resolveContext(enabled)
  await refresh

  const restored = await screen.findByRole("dialog", { name: "添加辅助手机号" })
  expect(within(restored).getByLabelText("手机号")).toHaveValue("13800138000")
})

it("preserves a valid evaluation connection while redirecting an anonymous user", async () => {
  const requestId = "90000000-0000-4000-8000-000000000001"
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "登录已失效",
          code: "invalid_session",
          request_id: "evaluation-connect-anonymous",
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )
  const router = createMemoryRouter(appRoutes, {
    initialEntries: [`/evaluation-connect/${requestId}`],
  })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/login")
  expect(router.state.location.state).toEqual({
    returnTo: `/evaluation-connect/${requestId}`,
  })
})
