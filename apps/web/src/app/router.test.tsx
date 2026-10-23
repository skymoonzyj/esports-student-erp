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
import { ProblemDetailsError } from "../api/client"
import { evaluationKeys } from "../evaluations/api"
import { reportKeys } from "../evaluations/reportKeys"
import { systemEvaluationKeys } from "../evaluations/systemEvaluationKeys"
import { foodOnboardingPath } from "../foodPreferences/FoodOnboardingPage"
import { foodPreferenceKeys } from "../foodPreferences/keys"
import { housingKeys } from "../housing/housingKeys"
import { reportingKeys } from "../housing/reportingKeys"
import type { PortalContext } from "../portal/api"
import { portalKeys } from "../portal/api"
import { registrationKeys } from "../registration/keys"
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

vi.mock(import("../procurement/ProcurementPage"), () => ({
  ProcurementPage: ({
    canManage,
    canSettle,
    canExport,
    canManageCenters,
  }: {
    canManage: boolean
    canSettle: boolean
    canExport: boolean
    canManageCenters: boolean
  }) => (
    <section
      aria-label="procurement-route-props"
      data-manage={String(canManage)}
      data-settle={String(canSettle)}
      data-export={String(canExport)}
      data-centers={String(canManageCenters)}
    >
      <h1>日常采购</h1>
    </section>
  ),
}))

vi.mock(import("../talentTests/TalentTestsPage"), () => ({
  TalentTestsPage: ({ accountId, personId }: { accountId: string; personId: string }) => (
    <h1 data-account={accountId} data-person={personId}>天赋测试目录</h1>
  ),
}))

vi.mock(import("../talentTests/TalentHistoryPage"), () => ({
  TalentHistoryPage: () => <h1>天赋测试历史</h1>,
}))

vi.mock(import("../talentTests/TalentReportPage"), () => ({
  TalentReportPage: ({ reportId }: { reportId: string }) => <h1>天赋报告 {reportId}</h1>,
}))

vi.mock(import("../talentTests/GameHost"), () => ({
  GameHost: ({
    accountId,
    personId,
    sessionId,
    testId,
    onSessionCompleted,
  }: {
    accountId: string
    personId: string
    sessionId: string
    testId: string
    onSessionCompleted?: (reportId: string) => void
  }) => (
    <>
      <h1
        data-account={accountId}
        data-person={personId}
        data-session={sessionId}
        data-test={testId}
      >
        天赋游戏
      </h1>
      <button
        type="button"
        onClick={() => onSessionCompleted?.("20000000-0000-4000-8000-000000000012")}
      >
        完成并生成报告
      </button>
      <button type="button" onClick={() => onSessionCompleted?.("not-a-report-id")}>
        返回错误报告编号
      </button>
    </>
  ),
}))

vi.mock(import("../esportsAssessments/AssessmentHome"), () => {
  const AssessmentHome = () => <h1>独立天赋评测目录</h1>
  return { AssessmentHome, default: AssessmentHome }
})

const noPortalContext = {
  coach_available: false,
  learning_available: false,
  payments_available: false,
  housing_management_available: false,
  housing_available: false,
  adult_guardian_invitations_available: false,
  food_profile_available: false,
  food_preferences_onboarding_required: false,
  learning_mode: null,
}

it("opens team management from live context without a global people grant", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const body = url.endsWith("/auth/me")
      ? { account_id: "account-manager", person_id: "person-manager", full_name: "队伍管理", email: null, force_password_change: false, permissions: [], scoped_permissions: [] }
      : url.endsWith("/auth/csrf") ? { csrf_token: "csrf" }
      : url.endsWith("/portal/context") ? noPortalContext
      : url.endsWith("/team-management/context") ? { can_open_management: true, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
      : url.includes("/team-management/teams") ? { items: [], page: 1, page_size: 100, total: 0 }
      : null
    if (body === null) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/people/teams"] })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  expect(await screen.findByRole("heading", { name: "队伍管理" })).toBeVisible()
  expect(screen.queryByRole("link", { name: "人员中心" })).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/team-management/teams"))).toBe(true)
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/people?page"))).toBe(false)
})

it("opens the existing personal profile for a pure team member", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const body = url.endsWith("/auth/me")
      ? { account_id: "account-member", person_id: "person-member", full_name: "队员", email: null, force_password_change: false, permissions: [], scoped_permissions: [] }
      : url.endsWith("/auth/csrf") ? { csrf_token: "csrf" }
      : url.endsWith("/portal/context") ? noPortalContext
      : url.endsWith("/team-management/context") ? { can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
      : url.endsWith("/people/me/profile") ? { person_id: "person-member", full_name: "队员甲", nickname: null, person_types: [], staff_type: null, sex: "male", date_of_birth: "2005-01-01", age_status: "adult", masked_identity: "****", masked_phone: null, masked_email: null }
      : url.includes("/team-management/me?") ? { current: { membership_id: "m1", project_id: "p1", project_name: "和平精英", team_id: "t1", team_name: "青训队", joined_on: "2026-09-01" }, history: { items: [], page: 1, page_size: 20, total: 0 } }
      : null
    if (body === null) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  }))
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/me/profile"] })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  expect(await screen.findByText("和平精英 · 青训队")).toBeVisible()
  expect(screen.getByRole("heading", { name: "个人资料" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "添加队员" })).not.toBeInTheDocument()
})

it("keeps a team-only profile available when unrelated portal context is forbidden", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const staleFoodKey = foodPreferenceKeys.self("account-member", "old-revision")
  client.setQueryData(staleFoodKey, { private_food: "must not leak" })
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/portal/context")) return new Response(JSON.stringify({ type: "about:blank", title: "Forbidden", status: 403, detail: "门户不可用", code: "forbidden", request_id: "portal-denied" }), { status: 403, headers: { "Content-Type": "application/json" } })
    const body = url.endsWith("/auth/me")
      ? { account_id: "account-member", person_id: "person-member", full_name: "队员", email: null, force_password_change: false, permissions: [], scoped_permissions: [] }
      : url.endsWith("/auth/csrf") ? { csrf_token: "csrf" }
      : url.endsWith("/team-management/context") ? { can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
      : url.endsWith("/people/me/profile") ? { person_id: "person-member", full_name: "队员甲", nickname: null, person_types: [], staff_type: null, sex: "male", date_of_birth: "2005-01-01", age_status: "adult", masked_identity: "****", masked_phone: null, masked_email: null }
      : url.includes("/team-management/me?") ? { current: { membership_id: "m1", project_id: "p1", project_name: "和平精英", team_id: "t1", team_name: "青训队", joined_on: "2026-09-01" }, history: { items: [], page: 1, page_size: 20, total: 0 } }
      : null
    if (body === null) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/me/profile"] })
  render(<QueryClientProvider client={client}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  expect(await screen.findByText("和平精英 · 青训队")).toBeVisible()
  expect(router.state.location.pathname).toBe("/me/profile")
  expect(client.getQueryData(staleFoodKey)).toBeUndefined()
  expect(screen.queryByText("must not leak")).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([request]) => String(request).endsWith("/me/food-preferences"))).toBe(false)
})

it("still redirects a non-team profile when portal access is forbidden", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/portal/context")) return new Response(JSON.stringify({ type: "about:blank", title: "Forbidden", status: 403, detail: "门户不可用", code: "forbidden", request_id: "portal-denied" }), { status: 403, headers: { "Content-Type": "application/json" } })
    const body = url.endsWith("/auth/me")
      ? { account_id: "account-no-team", person_id: "person-no-team", full_name: "无团队", email: null, force_password_change: false, permissions: [], scoped_permissions: [] }
      : url.endsWith("/auth/csrf") ? { csrf_token: "csrf" }
      : url.endsWith("/team-management/context") ? { can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
      : null
    if (body === null) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/me/profile"] })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(router.state.location.pathname).toBe("/")
  expect(fetchMock.mock.calls.some(([request]) => String(request).endsWith("/me/food-preferences"))).toBe(false)
  const portalCallCount = () => fetchMock.mock.calls.filter(([request]) => String(request).endsWith("/portal/context")).length
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) })
  expect(portalCallCount()).toBeLessThanOrEqual(5)
  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
    await new Promise((resolve) => setTimeout(resolve, 100))
  })
  expect(portalCallCount()).toBeLessThanOrEqual(8)
  await act(async () => { await router.navigate("/me/profile") })
  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) })
  expect(portalCallCount()).toBeLessThanOrEqual(12)
})

it("rechecks live team context on focus and clears revoked roster cache", async () => {
  let allowed = true
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const body = url.endsWith("/auth/me")
      ? { account_id: "account-manager", person_id: "person-manager", full_name: "管理甲", email: null, force_password_change: false, permissions: [], scoped_permissions: [] }
      : url.endsWith("/auth/csrf") ? { csrf_token: "csrf" }
      : url.endsWith("/portal/context") ? noPortalContext
      : url.endsWith("/team-management/context") ? { can_open_management: allowed, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
      : url.includes("/team-management/teams") ? { items: [{ id: "t1", project_id: "p1", project_name: "王者荣耀", name: "一队", is_active: true, member_count: 0, allowed_actions: ["read"] }], page: 1, page_size: 100, total: 1 }
      : null
    if (body === null) throw new Error(`Unexpected request: ${url}`)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  })
  vi.stubGlobal("fetch", fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/people/teams"] })
  render(<QueryClientProvider client={client}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  expect(await screen.findByRole("button", { name: "查看 一队" })).toBeVisible()
  expect(client.getQueryData(["teams", "account-manager", "all-authorized"])).toBeDefined()
  allowed = false
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.queryByRole("button", { name: "查看 一队" })).not.toBeInTheDocument())
  expect(client.getQueryData(["teams", "account-manager", "all-authorized"])).toBeUndefined()
  expect(screen.queryByRole("link", { name: "队伍管理" })).not.toBeInTheDocument()
})

afterEach(() => {
  focusManager.setFocused(undefined)
  vi.unstubAllGlobals()
})

function talentRouteFetch({
  permissions = [],
  scopedPermissions = [],
  isSuperAdmin = false,
  talentContext,
}: {
  permissions?: string[]
  scopedPermissions?: string[]
  isSuperAdmin?: boolean
  talentContext?: {
    can_take: boolean
    can_read_self: boolean
    can_read_reports: boolean
  }
}) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(JSON.stringify({
        account_id: "account-talent",
        person_id: "person-talent",
        full_name: "天赋测试用户",
        email: null,
        force_password_change: false,
        is_super_admin: isSuperAdmin,
        permissions,
        scoped_permissions: scopedPermissions,
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "csrf-talent" }), {
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
    if (url.endsWith("/talent-tests/context")) {
      return new Response(JSON.stringify({
        catalog: {},
        can_take: permissions.includes("talent_tests.take")
          || scopedPermissions.includes("talent_tests.take"),
        can_read_self: permissions.includes("talent_tests.read")
          || scopedPermissions.includes("talent_tests.read"),
        can_read_reports: false,
        active_session_id: null,
        ...talentContext,
      }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/permission-account-directory")) {
      return new Response(JSON.stringify([{ id: "target", full_name: "权限测试账号", status: "active" }]), { headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/roles")) return new Response("[]", { headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/permissions")) {
      return new Response(JSON.stringify([
        { id: "people", key: "people.read", name: "查看人员", sensitive: false },
        { id: "talent", key: "talent_tests.read", name: "查看天赋测试报告", sensitive: true },
      ]), { headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/accounts/target/access")) {
      return new Response(JSON.stringify({ account_id: "target", roles: [], permission_overrides: [], effective_permissions: [] }), { headers: { "Content-Type": "application/json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

it("keeps ordinary global permission management while restricting talent maintenance to actual super admins", async () => {
  renderTalentRoute("/settings/permissions", { permissions: ["permissions.manage"] })
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByLabelText("账号"), await screen.findByRole("option", { name: /权限测试账号/ }))
  expect(screen.getByRole("button", { name: "分配角色" })).toBeVisible()
  await user.selectOptions(screen.getByLabelText("权限能力"), "people")
  expect(within(screen.getByLabelText("数据范围")).getByRole("option", { name: "指定项目" })).toBeInTheDocument()
  expect(within(screen.getByLabelText("权限能力")).queryByRole("option", { name: "查看天赋测试报告" })).not.toBeInTheDocument()
})

it.each([
  { isSuperAdmin: true, permissions: ["permissions.manage"], scopedPermissions: [], allowed: true },
  { isSuperAdmin: true, permissions: [], scopedPermissions: ["permissions.manage"], allowed: true },
  { isSuperAdmin: false, permissions: [], scopedPermissions: ["permissions.manage"], allowed: false },
])("uses actual super-admin identity plus effective permission for talent maintenance: %j", async (access) => {
  renderTalentRoute("/settings/permissions", access)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByLabelText("账号"), await screen.findByRole("option", { name: /权限测试账号/ }))
  const capability = within(screen.getByLabelText("权限能力"))
  if (access.allowed) {
    expect(capability.getByRole("option", { name: "查看天赋测试报告" })).toBeInTheDocument()
  } else {
    expect(capability.queryByRole("option", { name: "查看天赋测试报告" })).not.toBeInTheDocument()
  }
})

function renderTalentRoute(
  path: string,
  access: Parameters<typeof talentRouteFetch>[0],
) {
  vi.stubGlobal("fetch", talentRouteFetch(access))
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )
  return router
}

it("opens the talent landing for an object-scoped read grant", async () => {
  const router = renderTalentRoute("/talent-tests", {
    scopedPermissions: ["talent_tests.read"],
  })

  const heading = await screen.findByRole("heading", { name: "天赋测试目录" })
  expect(heading).toHaveAttribute("data-account", "account-talent")
  expect(heading).toHaveAttribute("data-person", "person-talent")
  expect(screen.getAllByRole("link", { name: "天赋测试" })).toHaveLength(2)
  expect(router.state.location.pathname).toBe("/talent-tests")
})

it.each([
  ["ordinary administrator", false],
  ["super administrator after an effective deny", true],
] as const)("does not infer talent entry for %s", async (_label, isSuperAdmin) => {
  const router = renderTalentRoute("/talent-tests", { isSuperAdmin })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(screen.queryByRole("heading", { name: "天赋测试目录" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "天赋测试" })).not.toBeInTheDocument()
})

it("opens the independent assessment landing for a scoped module permission", async () => {
  const router = renderTalentRoute("/esports-assessments", {
    scopedPermissions: ["esports_assessments.read"],
  })

  expect(await screen.findByRole("heading", { name: "独立天赋评测目录" })).toBeVisible()
  expect(screen.getAllByRole("link", { name: "天赋评测 2" })).toHaveLength(2)
  expect(router.state.location.pathname).toBe("/esports-assessments")
})

it('registers the independent assessment host and report children without a duplicate report-list route', () => {
  const authenticated = appRoutes.find(route => route.children?.some(child => child.path === '/esports-assessments'))
  const assessment = authenticated?.children?.find(route => route.path === '/esports-assessments')
  expect(assessment?.children?.map(route => route.index ? 'index' : route.path)).toEqual(['index', 'sessions/:sessionId', 'practice', 'history', 'reports/:reportId'])
})

it("does not infer independent assessment access for an ordinary or super administrator", async () => {
  const router = renderTalentRoute("/esports-assessments", { isSuperAdmin: true })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(screen.queryByRole("heading", { name: "独立天赋评测目录" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "天赋评测 2" })).not.toBeInTheDocument()
})

it("requires effective take and read permissions together for a game URL", async () => {
  const denied = renderTalentRoute(
    "/talent-tests/sessions/session-one/reactiontime",
    { scopedPermissions: ["talent_tests.read"] },
  )
  await waitFor(() => expect(denied.state.location.pathname).toBe("/"))
  expect(screen.queryByRole("heading", { name: "天赋游戏" })).not.toBeInTheDocument()
})

it("rejects a game URL when scoped keys exist but server context denies self take", async () => {
  const router = renderTalentRoute(
    "/talent-tests/sessions/session-one/reactiontime",
    {
      scopedPermissions: ["talent_tests.take", "talent_tests.read"],
      talentContext: { can_take: false, can_read_self: false, can_read_reports: true },
    },
  )

  await waitFor(() => expect(router.state.location.pathname).toBe("/talent-tests"))
  expect(screen.queryByRole("heading", { name: "天赋游戏" })).not.toBeInTheDocument()
})

it("rejects self history when server context grants only family or assigned report reading", async () => {
  const router = renderTalentRoute(
    "/talent-tests/history",
    {
      scopedPermissions: ["talent_tests.read"],
      talentContext: { can_take: false, can_read_self: false, can_read_reports: true },
    },
  )

  await waitFor(() => expect(router.state.location.pathname).toBe("/talent-tests"))
  expect(screen.queryByRole("heading", { name: "天赋测试历史" })).not.toBeInTheDocument()
})

it("passes account, person, session and catalog test identity into the game host", async () => {
  const router = renderTalentRoute(
    "/talent-tests/sessions/session-one/reactiontime",
    { permissions: ["talent_tests.take"], scopedPermissions: ["talent_tests.read"] },
  )

  const heading = await screen.findByRole("heading", { name: "天赋游戏" })
  expect(heading).toHaveAttribute("data-account", "account-talent")
  expect(heading).toHaveAttribute("data-person", "person-talent")
  expect(heading).toHaveAttribute("data-session", "session-one")
  expect(heading).toHaveAttribute("data-test", "reactiontime")
  expect(router.state.location.pathname).toBe("/talent-tests/sessions/session-one/reactiontime")
})

it("allows the dynamic vision route while leaving round membership enforcement to GameHost", async () => {
  const router = renderTalentRoute(
    "/talent-tests/sessions/session-v2/dynamic-vision",
    { permissions: ["talent_tests.take"], scopedPermissions: ["talent_tests.read"] },
  )

  const heading = await screen.findByRole("heading", { name: "天赋游戏" })
  expect(heading).toHaveAttribute("data-session", "session-v2")
  expect(heading).toHaveAttribute("data-test", "dynamic-vision")
  expect(router.state.location.pathname).toBe(
    "/talent-tests/sessions/session-v2/dynamic-vision",
  )
})

it("navigates to the server report after the seventh result is confirmed", async () => {
  const router = renderTalentRoute(
    "/talent-tests/sessions/session-one/memory",
    { permissions: ["talent_tests.take", "talent_tests.read"] },
  )
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "完成并生成报告" }))

  expect(await screen.findByRole("heading", {
    name: "天赋报告 20000000-0000-4000-8000-000000000012",
  })).toBeVisible()
  expect(router.state.location.pathname).toBe(
    "/talent-tests/reports/20000000-0000-4000-8000-000000000012",
  )
})

it("ignores a malformed completion report id at the route boundary", async () => {
  const router = renderTalentRoute(
    "/talent-tests/sessions/session-one/memory",
    { permissions: ["talent_tests.take", "talent_tests.read"] },
  )
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "返回错误报告编号" }))

  expect(router.state.location.pathname).toBe("/talent-tests/sessions/session-one/memory")
  expect(screen.getByRole("heading", { name: "天赋游戏" })).toBeVisible()
})

it("rejects an unknown or typing test id before mounting the game host", async () => {
  renderTalentRoute(
    "/talent-tests/sessions/session-one/typing",
    { permissions: ["talent_tests.take", "talent_tests.read"] },
  )

  expect(await screen.findByRole("alert")).toHaveTextContent("该测试项目不可用")
  expect(screen.queryByRole("heading", { name: "天赋游戏" })).not.toBeInTheDocument()
})

it.each([
  {
    label: "people",
    path: "/people",
    queryKey: ["people"],
    dataPath: "/people?",
    staleText: "权限撤销前人员",
    revokedMessage: "人员名单权限已撤销",
    permissions: ["people.list"],
    isSuperAdmin: false,
  },
  {
    label: "audit",
    path: "/settings/audit",
    queryKey: ["audit-events"],
    dataPath: "/audit-events?",
    staleText: "权限撤销前操作人",
    revokedMessage: "安全审计权限已撤销",
    permissions: ["audit.read"],
    isSuperAdmin: true,
  },
])(
  "keeps a $label 403 denial mounted during a stale permission refresh",
  async ({
    path,
    queryKey,
    dataPath,
    staleText,
    revokedMessage,
    permissions,
    isSuperAdmin,
  }) => {
    const currentUser = {
      account_id: "account-revoked-route",
      person_id: "person-revoked-route",
      full_name: "权限刷新用户",
      email: "revoked@example.com",
      force_password_change: false,
      permissions,
      is_super_admin: isSuperAdmin,
    }
    let meCalls = 0
    let dataCalls = 0
    let finishPermissionRefresh: ((response: Response) => void) | undefined
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) {
        meCalls += 1
        if (meCalls === 1) {
          return new Response(JSON.stringify(currentUser), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          })
        }
        if (meCalls === 2) {
          return new Promise<Response>((resolve) => {
            finishPermissionRefresh = resolve
          })
        }
        throw new Error("permission refresh repeated after a retained 403 denial")
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "revoked-route-csrf" }), {
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
      if (url.endsWith("/people/counts")) {
        return new Response(JSON.stringify({
          total: 1,
          student: 1,
          guardian: 0,
          staff: 0,
          coach: 0,
          life_teacher: 0,
          customer_service: 0,
          administration: 0,
          other_staff: 0,
          visitor: 0,
        }), { status: 200, headers: { "Content-Type": "application/json" } })
      }
      if (url.includes(dataPath)) {
        dataCalls += 1
        if (dataCalls === 1) {
          const item = path === "/people"
            ? {
                id: "person-stale-route",
                full_name: staleText,
                nickname: null,
                person_types: ["student"],
                identity_number: "110105********002X",
                phone: "138****8000",
                email: "stale@example.com",
                sex: "female",
                date_of_birth: "2012-01-02",
                staff_type: null,
                created_at: "2026-07-26T10:00:00+08:00",
                age_status: "minor",
                guardian_status: "ready",
              }
            : {
                id: "audit-stale-route",
                actor_user_id: "account-stale-route",
                actor_name: staleText,
                action: "person.update",
                resource_type: "person",
                resource_id: "person-stale-route",
                purpose: "权限撤销前用途",
                outcome: "allowed",
                request_id: "audit-stale-route-request",
                metadata: {},
                created_at: "2026-07-26T10:00:00+08:00",
              }
          return new Response(
            JSON.stringify({ items: [item], page: 1, page_size: 20, total: 1 }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          )
        }
        if (dataCalls === 2) {
          return new Response(JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "权限已撤销",
            code: "forbidden",
            request_id: "revoked-route",
          }), { status: 403, headers: { "Content-Type": "application/problem+json" } })
        }
        return new Promise<Response>(() => undefined)
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
    render(
      <QueryClientProvider client={queryClient}>
        <AuthProvider><RouterProvider router={router} /></AuthProvider>
      </QueryClientProvider>,
    )

    expect(await screen.findByText(staleText)).toBeVisible()
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey })
    })
    await waitFor(() => expect(meCalls).toBe(2))
    expect(screen.getByRole("alert")).toHaveTextContent(revokedMessage)
    expect(screen.queryByText(staleText)).not.toBeInTheDocument()
    expect(router.state.location.pathname).toBe(path)

    await act(async () => {
      finishPermissionRefresh?.(new Response(JSON.stringify(currentUser), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }))
    })
    await waitFor(() => expect(dataCalls).toBe(2))
    expect(screen.getByRole("alert")).toHaveTextContent(revokedMessage)
    expect(meCalls).toBe(2)
    expect(router.state.location.pathname).toBe(path)
  },
)

it.each([
  ["/register", "创建账号"],
  ["/register/verify", "验证联系方式"],
  ["/register/profile", "填写实名资料"],
  ["/forgot-password", "找回密码"],
] as const)("renders anonymous public route %s outside the app shell", async (path, heading) => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) {
        return new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Unauthorized",
            status: 401,
            detail: "请登录",
            code: "invalid_session",
            request_id: "public-route-auth",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        )
      }
      if (url.endsWith("/registration/me")) {
        return new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Unauthorized",
            status: 401,
            detail: "注册会话已失效",
            code: "registration_session_invalid",
            request_id: "public-route-registration",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
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
  expect(screen.queryByRole("button", { name: "退出登录" })).not.toBeInTheDocument()
  expect(router.state.location.pathname).toBe(path)
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
  [
    "administration",
    [
      "procurement.read",
      "procurement.manage",
      "procurement.settle",
      "procurement.export",
      "procurement.centers.manage",
    ],
    [],
    ["true", "true", "true", "true"],
  ],
  [
    "finance",
    ["procurement.read", "procurement.settle", "procurement.export"],
    [],
    ["false", "true", "true", "false"],
  ],
  [
    "scoped administration",
    [],
    [
      "procurement.read",
      "procurement.manage",
      "procurement.settle",
      "procurement.export",
      "procurement.centers.manage",
    ],
    ["true", "true", "true", "true"],
  ],
] as const)("passes effective %s procurement permissions into the page", async (
  _label,
  permissions,
  scopedPermissions,
  expected,
) => {
  vi.stubGlobal("fetch", housingRouteFetch({
    context: noPortalContext,
    permissions: [...permissions],
    scopedPermissions: [...scopedPermissions],
  }))
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/procurement"] })
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "日常采购" })).toBeVisible()
  const props = screen.getByLabelText("procurement-route-props")
  expect(props).toHaveAttribute("data-manage", expected[0])
  expect(props).toHaveAttribute("data-settle", expected[1])
  expect(props).toHaveAttribute("data-export", expected[2])
  expect(props).toHaveAttribute("data-centers", expected[3])
  expect(screen.getAllByRole("link", { name: "日常采购" })).toHaveLength(2)
  expect(router.state.location.pathname).toBe("/procurement")
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
  ["/settings/operations", "运维监控"],
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
            permissions: path.startsWith("/settings/audit")
              ? ["audit.read"]
              : path.startsWith("/settings/operations")
                ? ["operations.monitor.read"]
                : [],
            is_super_admin: false,
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
  await user.click(screen.getAllByRole("button", { name: "人员中心" })[0])
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
            is_super_admin: true,
            permissions: ["accounts.read", "accounts.manage", "permissions.manage", "audit.read"],
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

const portalLearningStudentId = "30000000-0000-4000-8000-000000000097"

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
    path: `/learning/${portalLearningStudentId}`,
    capability: "learning_available" as const,
    expected: "路由学员",
    childRequest: `/family/students/${portalLearningStudentId}/teaching?`,
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
  {
    path: "/me/profile",
    capability: "food_profile_available" as const,
    expected: "个人资料",
    childRequest: "/me/food-preferences",
  },
  {
    path: "/me/guardian-invitations",
    capability: "adult_guardian_invitations_available" as const,
    expected: "成年学员监护邀请",
    childRequest: "/registration/guardian-invitations",
  },
  {
    path: foodOnboardingPath,
    capability: "food_preferences_onboarding_required" as const,
    expected: "完善餐饮偏好",
    childRequest: "/me/food-preferences",
  },
] as const

const portalFoodRevision = "b".repeat(64)
const portalCuisineId = "20000000-0000-4000-8000-000000000099"
const portalPersonId = "30000000-0000-4000-8000-000000000099"

function portalFoodSelfResponse() {
  return {
    profile: {
      person_id: portalPersonId,
      version: 2,
      cuisines: [
        {
          cuisine_option_id: portalCuisineId,
          name: "川菜",
          custom_name: null,
        },
      ],
      dishes: ["鱼香肉丝"],
      updated_at: "2026-08-12T10:00:00+08:00",
      allowed_actions: ["update"],
      access_revision: portalFoodRevision,
    },
    onboarding_required: true,
    onboarding_completed_at: null,
  }
}

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
    foodResponse?: (init?: RequestInit) => Response | Promise<Response>
    permissions?: string[]
  } = {},
) {
  return vi.fn<typeof fetch>(async (request, init) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "account-portal-route",
          person_id: portalPersonId,
          full_name: "门户路由账号",
          email: "portal-route@example.test",
          force_password_change: false,
          permissions: options.permissions ?? [],
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
    if (url.includes("/food-preferences/cuisines?")) {
      return new Response(
        JSON.stringify({
          items: [
            {
              id: portalCuisineId,
              name: "川菜",
              sort_order: 1,
              is_active: true,
              is_other: false,
            },
          ],
          allowed_actions: [],
          access_revision: portalFoodRevision,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/me/food-preferences")) {
      if (options.foodResponse) return options.foodResponse(init)
      return new Response(
        JSON.stringify(portalFoodSelfResponse()),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith(`/people/${portalLearningStudentId}/food-preferences`)) {
      return new Response(
        JSON.stringify({
          ...portalFoodSelfResponse().profile,
          person_id: portalLearningStudentId,
        }),
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
              student_person_id: portalLearningStudentId,
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
    if (
      url.includes(
        `/family/students/${portalLearningStudentId}/teaching?`,
      )
    ) {
      return new Response(
        JSON.stringify({
          student: {
            student_person_id: portalLearningStudentId,
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
    if (url.endsWith("/registration/guardian-invitations")) {
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
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

    const expectedContent = capability.startsWith("food_")
      ? await screen.findByRole("heading", { name: expected })
      : await screen.findByText(expected)
    expect(expectedContent).toBeVisible()
    expect(router.state.location.pathname).toBe(path)
    expect(document.querySelector("main main")).not.toBeInTheDocument()
  },
)

it("lets a pure guardian open the optional profile but not onboarding", async () => {
  const context = {
    ...noPortalContext,
    food_profile_available: true,
    food_preferences_onboarding_required: false,
  }
  const fetchMock = portalRouteFetch(
    new Response(JSON.stringify(context), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  const { router } = renderPortalRoute("/me/profile", fetchMock)

  expect(await screen.findByRole("heading", { name: "个人资料" })).toBeVisible()
  await waitFor(() =>
    expect(
      fetchMock.mock.calls.filter(([request]) =>
        String(request).endsWith("/me/food-preferences"),
      ),
    ).toHaveLength(1),
  )
  const foodCallsBefore = fetchMock.mock.calls.filter(([request]) =>
    String(request).endsWith("/me/food-preferences"),
  ).length

  await act(async () => router.navigate(foodOnboardingPath))

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(
    fetchMock.mock.calls.filter(([request]) =>
      String(request).endsWith("/me/food-preferences"),
    ),
  ).toHaveLength(foodCallsBefore)
})

it("returns home without clearing the server-backed task when onboarding is skipped", async () => {
  const user = userEvent.setup()
  const context = {
    ...noPortalContext,
    food_profile_available: true,
    food_preferences_onboarding_required: true,
  }
  const fetchMock = portalRouteFetch(
    new Response(JSON.stringify(context), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  const { router } = renderPortalRoute(foodOnboardingPath, fetchMock)

  await user.click(await screen.findByRole("button", { name: "稍后填写" }))

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(
    screen.getByRole("link", { name: "完善餐饮偏好" }),
  ).toBeVisible()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
    false,
  )
})

it("refreshes portal context and removes the onboarding task after completion", async () => {
  const user = userEvent.setup()
  let contextCalls = 0
  const fetchMock = portalRouteFetch(
    () => {
      contextCalls += 1
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ...noPortalContext,
            food_profile_available: true,
            food_preferences_onboarding_required: contextCalls === 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
    },
    {
      foodResponse: (init) =>
        new Response(
          JSON.stringify({
            ...portalFoodSelfResponse(),
            profile: {
              ...portalFoodSelfResponse().profile,
              version: init?.method === "PUT" ? 3 : 2,
            },
            onboarding_required: init?.method !== "PUT",
            onboarding_completed_at:
              init?.method === "PUT" ? "2026-08-12T10:30:00+08:00" : null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    },
  )
  const { router } = renderPortalRoute(foodOnboardingPath, fetchMock)

  await user.click(
    await screen.findByRole("checkbox", {
      name: "我确认以上是本人的餐饮偏好",
    }),
  )
  await user.click(screen.getByRole("button", { name: "完成填写" }))

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(
    screen.queryByRole("link", { name: "完善餐饮偏好" }),
  ).not.toBeInTheDocument()
  expect(contextCalls).toBeGreaterThanOrEqual(2)
  expect(
    fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT"),
  ).toHaveLength(1)
})

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
    const expectedContent = capability.startsWith("food_")
      ? await screen.findByRole("heading", { name: expected })
      : await screen.findByText(expected)
    expect(expectedContent).toBeVisible()
  },
)

it("keeps the navigation rail stable while cached context is refetching", async () => {
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
  expect(screen.getAllByRole("link", { name: "本人付款" })).toHaveLength(2)
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

it("clears only the current food cache and ignores a delayed response after profile revocation", async () => {
  let contextCalls = 0
  let resolveFood!: (response: Response) => void
  const delayedFood = new Promise<Response>((resolve) => {
    resolveFood = resolve
  })
  const fetchMock = portalRouteFetch(
    () => {
      contextCalls += 1
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ...noPortalContext,
            food_profile_available: contextCalls === 1,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      )
    },
    { foodResponse: () => delayedFood },
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const currentPrivateKey = foodPreferenceKeys.person(
    "account-portal-route",
    portalFoodRevision,
    "30000000-0000-4000-8000-000000000098",
  )
  const currentSelfKey = foodPreferenceKeys.self(
    "account-portal-route",
    portalFoodRevision,
  )
  const otherAccountKey = foodPreferenceKeys.person(
    "account-other",
    portalFoodRevision,
    portalPersonId,
  )
  queryClient.setQueryData(currentPrivateKey, { private_name: "必须清除" })
  queryClient.setQueryData(otherAccountKey, { private_name: "其他账号保留" })
  const { router } = renderPortalRoute("/me/profile", fetchMock, queryClient)

  await waitFor(() =>
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith("/me/food-preferences"),
      ),
    ).toBe(true),
  )

  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(queryClient.getQueryData(currentPrivateKey)).toBeUndefined()
  expect(queryClient.getQueryData(currentSelfKey)).toBeUndefined()
  expect(queryClient.getQueryData(otherAccountKey)).toEqual({
    private_name: "其他账号保留",
  })

  await act(async () => {
    resolveFood(
      new Response(JSON.stringify(portalFoodSelfResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
  })
  expect(queryClient.getQueryData(currentSelfKey)).toBeUndefined()
  expect(queryClient.getQueryData(otherAccountKey)).toEqual({
    private_name: "其他账号保留",
  })
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
  }, { permissions: ["housing.read"] })
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
          survey_sections: {
            respondent: false,
            staff_feedback: false,
            administration: false,
          },
          game_accounts_available: false,
          admin_available: false,
          admin_sections: {
            cohort_staff: false,
            student_matching: false,
            analysis_profiles: false,
            system_tasks: false,
            adapter_certifications: false,
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
  system_evaluation_available?: boolean
  admin_available?: boolean
  admin_sections: {
    cohort_staff: boolean
    student_matching: boolean
    analysis_profiles: boolean
    system_tasks?: boolean
    adapter_certifications?: boolean
  }
}

function evaluationChildContext(
  capabilities: EvaluationChildCapabilities,
) {
  const adminSections = {
    system_tasks: false,
    adapter_certifications: false,
    ...capabilities.admin_sections,
  }
  return {
    accessible: true,
    system_evaluation_available: capabilities.system_evaluation_available ?? true,
    embedded_start_url:
      "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
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
    game_accounts_available: capabilities.game_accounts_available,
    admin_available:
      capabilities.admin_available ?? Object.values(adminSections).some(Boolean),
    admin_sections: adminSections,
    integration_api_version: "v1",
  }
}

function renderEvaluationChildRoute(
  path: "/evaluations/game-accounts" | "/evaluations/admin" | "/evaluations/system/tasks",
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
    if (url.endsWith("/evaluations/system/tasks?page=1&page_size=20")) {
      return new Response(
        JSON.stringify({ api_version: "v2", items: [], total: 0, page: 1, page_size: 20 }),
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

type TeachingReportCapabilities = {
  author_available: boolean
  reviewer_available: boolean
  family_available: boolean
}

function renderTeachingReportRoute(
  path: "/evaluations/reports" | "/evaluations/reports/review",
  contexts: Array<TeachingReportCapabilities | Response>,
  options: {
    permissions?: string[]
    targets?: (init: RequestInit | undefined) => Promise<Response>
    reviewQueue?: (init: RequestInit | undefined) => Promise<Response>
    reviewMutation?: (init: RequestInit | undefined) => Promise<Response>
  } = {},
) {
  const accountId = "account-teaching-report-route"
  let reportContextCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: accountId,
          person_id: "person-teaching-report-route",
          full_name: "教学评语路由账号",
          email: "teaching-report-route@example.test",
          force_password_change: false,
          permissions: [
            "evaluation_center.access",
            ...(options.permissions ?? []),
          ],
          scoped_permissions: [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "teaching-report-route-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/portal/context")) {
      return new Response(
        JSON.stringify({
          ...noPortalContext,
          learning_available: true,
          learning_mode: "family",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/evaluations/context")) {
      return new Response(
        JSON.stringify({
          ...evaluationChildContext({
            game_accounts_available: false,
            admin_sections: {
              cohort_staff: false,
              student_matching: false,
              analysis_profiles: false,
            },
          }),
          reports_available: true,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/evaluations/reports/context")) {
      const context = contexts[Math.min(reportContextCalls, contexts.length - 1)]
      reportContextCalls += 1
      if (context instanceof Response) return context
      return new Response(JSON.stringify(context), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/evaluations/reports/targets?")) {
      if (options.targets) return options.targets(init)
      return new Response(
        JSON.stringify({
          items: [],
          facets: { cohorts: [], projects: [], classes: [] },
          total: 0,
          page: 1,
          page_size: 20,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/evaluations/reports/review-queue?")) {
      if (options.reviewQueue) return options.reviewQueue(init)
      return new Response(
        JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("/evaluations/reports/") && url.includes("/versions?")) {
      return new Response(
        JSON.stringify({ items: [], total: 0, page: 1, page_size: 20 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/reviews") && options.reviewMutation) {
      return options.reviewMutation(init)
    }
    if (url.endsWith("/family/students")) {
      return new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return {
    accountId,
    fetchMock,
    queryClient,
    reportContextCalls: () => reportContextCalls,
    router,
  }
}

it("opens the teaching report author workspace for an author", async () => {
  const result = renderTeachingReportRoute("/evaluations/reports", [
    { author_available: true, reviewer_available: false, family_available: false },
  ])

  expect(
    await screen.findByRole("heading", { name: "教学评语工作台" }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations/reports")
})

it("keeps the authorized author workspace available with no live report tasks", async () => {
  const result = renderTeachingReportRoute(
    "/evaluations/reports",
    [{ author_available: false, reviewer_available: false, family_available: false }],
    { permissions: ["student_reports.write"] },
  )

  expect(
    await screen.findByRole("heading", { name: "教学评语工作台" }),
  ).toBeVisible()
  expect(await screen.findByRole("heading", { name: "暂无可填写学员" })).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations/reports")
})

it("redirects a reviewer-only account from the author route to the review queue", async () => {
  const result = renderTeachingReportRoute("/evaluations/reports", [
    { author_available: false, reviewer_available: true, family_available: false },
  ])

  expect(
    await screen.findByRole("heading", { name: "教学评语审核" }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations/reports/review")
})

it("redirects a family-only account from reports to learning", async () => {
  const result = renderTeachingReportRoute("/evaluations/reports", [
    { author_available: false, reviewer_available: false, family_available: true },
  ])

  await waitFor(() => expect(result.router.state.location.pathname).toBe("/learning"))
  expect(screen.queryByRole("heading", { name: "登录工作台" })).not.toBeInTheDocument()
})

it("blocks the direct review route when reviewer capability is absent", async () => {
  const result = renderTeachingReportRoute("/evaluations/reports/review", [
    { author_available: true, reviewer_available: false, family_available: false },
  ])

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(
    result.fetchMock.mock.calls.some(([input]) =>
      String(input).includes("/evaluations/reports/review-queue?"),
    ),
  ).toBe(false)
})

it("revalidates report context after an operation-denied child query and leaves only after confirmed revocation", async () => {
  const enabled = {
    author_available: false,
    reviewer_available: true,
    family_available: false,
  }
  const result = renderTeachingReportRoute(
    "/evaluations/reports/review",
    [enabled, { ...enabled, reviewer_available: false }],
    {
      reviewQueue: async () =>
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "review permission no longer matches this queue",
            code: "evaluation_operation_denied",
            request_id: "teaching-report-operation-denied",
          }),
          {
            status: 403,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
    },
  )

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(result.reportContextCalls()).toBeGreaterThan(1)
})

it("revalidates report context after an operation-denied review mutation", async () => {
  const enabled = {
    author_available: false,
    reviewer_available: true,
    family_available: false,
  }
  const reportId = "20000000-0000-4000-8000-000000000010"
  const queueItem = {
    report_id: reportId,
    enrollment_id: "30000000-0000-4000-8000-000000000010",
    cohort_id: "40000000-0000-4000-8000-000000000010",
    cohort_name: "路由撤权营",
    project_id: "50000000-0000-4000-8000-000000000010",
    project_name: "英雄联盟",
    class_id: "60000000-0000-4000-8000-000000000010",
    class_name: "撤权班",
    report_kind: "daily",
    period_sequence: 1,
    period_start_date: "2026-08-09",
    period_end_date: "2026-08-09",
    status: "pending_review",
    lock_version: 1,
    author_name: "路由教练",
    role_name: "主教练",
    version_id: "70000000-0000-4000-8000-000000000010",
    version: 1,
    source: "initial",
    body: "提交审核时能力被撤销",
    dimension_values: {},
    submitted_at: "2026-08-09T12:30:00Z",
  }
  const result = renderTeachingReportRoute(
    "/evaluations/reports/review",
    [enabled, { ...enabled, reviewer_available: false }],
    {
      reviewQueue: async () =>
        new Response(
          JSON.stringify({ items: [queueItem], total: 1, page: 1, page_size: 20 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      reviewMutation: async () =>
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "review permission was removed before commit",
            code: "evaluation_operation_denied",
            request_id: "teaching-report-mutation-denied",
          }),
          {
            status: 403,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
    },
  )
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核路由教练的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(result.reportContextCalls()).toBeGreaterThan(1)
})

it("ignores an operation-denied mutation owned by another report account", async () => {
  const enabled = {
    author_available: false,
    reviewer_available: true,
    family_available: false,
  }
  const result = renderTeachingReportRoute(
    "/evaluations/reports/review",
    [enabled, { ...enabled, reviewer_available: false }],
  )

  expect(
    await screen.findByRole("heading", { name: "教学评语审核" }),
  ).toBeVisible()
  expect(result.reportContextCalls()).toBe(1)
  const mutation = result.queryClient.getMutationCache().build(
    result.queryClient,
    {
      mutationKey: [
        ...reportKeys.root("previous-report-account"),
        "mutation",
        "review",
      ],
      mutationFn: async () => {
        throw new ProblemDetailsError({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "the previous account lost review scope",
          code: "evaluation_operation_denied",
          request_id: "previous-account-operation-denied",
        })
      },
      retry: false,
    },
  )

  await act(async () => {
    await mutation.execute(undefined).catch(() => undefined)
    await Promise.resolve()
  })

  expect(result.router.state.location.pathname).toBe(
    "/evaluations/reports/review",
  )
  expect(result.reportContextCalls()).toBe(1)
})

it("handles global evaluation revocation returned by a report mutation", async () => {
  const enabled = {
    author_available: false,
    reviewer_available: true,
    family_available: false,
  }
  const reportId = "20000000-0000-4000-8000-000000000011"
  const queueItem = {
    report_id: reportId,
    enrollment_id: "30000000-0000-4000-8000-000000000011",
    cohort_id: "40000000-0000-4000-8000-000000000011",
    cohort_name: "全局撤权营",
    project_id: "50000000-0000-4000-8000-000000000011",
    project_name: "无畏契约",
    class_id: "60000000-0000-4000-8000-000000000011",
    class_name: "全局撤权班",
    report_kind: "daily",
    period_sequence: 1,
    period_start_date: "2026-08-09",
    period_end_date: "2026-08-09",
    status: "pending_review",
    lock_version: 1,
    author_name: "全局撤权教练",
    role_name: "主教练",
    version_id: "70000000-0000-4000-8000-000000000011",
    version: 1,
    source: "initial",
    body: "提交审核时评价中心权限被撤销",
    dimension_values: {},
    submitted_at: "2026-08-09T12:30:00Z",
  }
  const result = renderTeachingReportRoute(
    "/evaluations/reports/review",
    [enabled],
    {
      reviewQueue: async () =>
        new Response(
          JSON.stringify({ items: [queueItem], total: 1, page: 1, page_size: 20 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      reviewMutation: async () =>
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "evaluation center access was removed before review commit",
            code: "evaluation_access_denied",
            request_id: "teaching-report-mutation-global-revocation",
          }),
          {
            status: 403,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
    },
  )
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核全局撤权教练的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/")
  expect(
    result.fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/auth/me"),
    ).length,
  ).toBeGreaterThan(1)
})

it("keeps global evaluation revocation on the home route and clears the report root", async () => {
  const result = renderTeachingReportRoute("/evaluations/reports", [
    new Response(
      JSON.stringify({
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "evaluation center access was removed",
        code: "evaluation_access_denied",
        request_id: "teaching-report-global-revocation",
      }),
      {
        status: 403,
        headers: { "Content-Type": "application/problem+json" },
      },
    ),
  ])
  const privateKey = [...reportKeys.root(result.accountId), "private"] as const
  result.queryClient.setQueryData(privateKey, { student: "不得保留" })

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/")
  expect(result.queryClient.getQueryData(privateKey)).toBeUndefined()
  expect(
    result.fetchMock.mock.calls.some(
      ([input, init]) => String(input).endsWith("/auth/logout") && init?.method === "POST",
    ),
  ).toBe(false)
})

it("handles global evaluation revocation returned by a report child query", async () => {
  const enabled = {
    author_available: true,
    reviewer_available: false,
    family_available: false,
  }
  const result = renderTeachingReportRoute(
    "/evaluations/reports",
    [enabled],
    {
      targets: async () =>
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "evaluation center access was removed during target lookup",
            code: "evaluation_access_denied",
            request_id: "teaching-report-target-global-revocation",
          }),
          {
            status: 403,
            headers: { "Content-Type": "application/problem+json" },
          },
        ),
    },
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/")
  expect(
    result.fetchMock.mock.calls.filter(([input]) =>
      String(input).endsWith("/auth/me"),
    ).length,
  ).toBeGreaterThan(1)
})

it("purges only the current report root and prevents a delayed response from reviving it after revocation", async () => {
  const enabled = {
    author_available: true,
    reviewer_available: false,
    family_available: false,
  }
  let resolveTargets!: (response: Response) => void
  const targetRequest: { signal: AbortSignal | null } = { signal: null }
  const targets = vi.fn((init: RequestInit | undefined) => {
    targetRequest.signal = init?.signal ?? null
    return new Promise<Response>((resolve) => {
      resolveTargets = resolve
    })
  })
  const result = renderTeachingReportRoute(
    "/evaluations/reports",
    [enabled, { ...enabled, author_available: false }],
    { targets },
  )
  const currentPrivateKey = [
    ...reportKeys.root(result.accountId),
    "delayed-private-target",
  ] as const
  const otherPrivateKey = [
    ...reportKeys.root("other-teaching-report-account"),
    "private-target",
  ] as const

  expect(
    await screen.findByRole("heading", { name: "教学评语工作台" }),
  ).toBeVisible()
  await waitFor(() => expect(targets).toHaveBeenCalledTimes(1))
  result.queryClient.setQueryData(currentPrivateKey, { student: "当前账号私密学员" })
  result.queryClient.setQueryData(otherPrivateKey, { student: "其他账号私密学员" })

  await result.queryClient.invalidateQueries({
    queryKey: reportKeys.context(result.accountId),
  })

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(targetRequest.signal?.aborted).toBe(true)
  expect(result.queryClient.getQueryData(currentPrivateKey)).toBeUndefined()
  expect(result.queryClient.getQueryData(otherPrivateKey)).toEqual({
    student: "其他账号私密学员",
  })
  expect(
    result.fetchMock.mock.calls.some(
      ([input, init]) => String(input).endsWith("/auth/logout") && init?.method === "POST",
    ),
  ).toBe(false)

  resolveTargets(
    new Response(
      JSON.stringify({
        items: [],
        facets: { cohorts: [], projects: [], classes: [] },
        total: 0,
        page: 1,
        page_size: 20,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  )
  await Promise.resolve()
  expect(
    result.queryClient.getQueriesData({ queryKey: reportKeys.root(result.accountId) }),
  ).toEqual([])
})

it("opens the ERP-native task list through a fresh server evaluation guard", async () => {
  const result = renderEvaluationChildRoute("/evaluations/system/tasks", [
    {
      game_accounts_available: false,
      admin_sections: {
        cohort_staff: false,
        student_matching: false,
        analysis_profiles: false,
      },
    },
  ])

  expect(await screen.findByRole("heading", { name: "系统评价任务" })).toBeVisible()
  expect(result.contextCalls()).toBe(1)
  expect(
    result.fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith("/evaluations/system/tasks?page=1&page_size=20"),
    ),
  ).toBe(true)
})

it("blocks a direct task-list route when system evaluation capability is absent", async () => {
  const result = renderEvaluationChildRoute("/evaluations/system/tasks", [
    {
      game_accounts_available: false,
      system_evaluation_available: false,
      admin_sections: {
        cohort_staff: false,
        student_matching: false,
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
      String(input).includes("/evaluations/system/tasks?"),
    ),
  ).toBe(false)
})

it("purges cached system tasks when the server capability is revoked", async () => {
  const enabled = {
    game_accounts_available: false,
    system_evaluation_available: true,
    admin_sections: {
      cohort_staff: false,
      student_matching: false,
      analysis_profiles: false,
    },
  }
  const disabled = { ...enabled, system_evaluation_available: false }
  const result = renderEvaluationChildRoute(
    "/evaluations/system/tasks",
    [enabled, disabled],
  )
  const cachedDetailKey = systemEvaluationKeys.detail(
    result.accountId,
    "account",
    "cached-task",
  )

  expect(await screen.findByRole("heading", { name: "系统评价任务" })).toBeVisible()
  result.queryClient.setQueryData(cachedDetailKey, { private: "cached-result" })
  await result.queryClient.invalidateQueries({
    queryKey: evaluationKeys.context(result.accountId),
  })

  expect(
    await screen.findByRole("heading", { name: "评价中心", level: 1 }),
  ).toBeVisible()
  expect(result.router.state.location.pathname).toBe("/evaluations")
  expect(result.queryClient.getQueryData(cachedDetailKey)).toBeUndefined()
})

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

const foodAdministrationRevision = "f".repeat(64)

function foodAdministrationFetch({
  permissions = [],
  scopedPermissions = [],
  statisticsResponse,
  catalogResponse,
}: {
  permissions?: string[]
  scopedPermissions?: string[]
  statisticsResponse?: Response
  catalogResponse?: Response | Promise<Response>
} = {}) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(JSON.stringify({
        account_id: "account-food-route",
        person_id: "person-food-route",
        full_name: "餐饮路由账号",
        email: "food-route@example.test",
        force_password_change: false,
        permissions,
        scoped_permissions: scopedPermissions,
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "food-route-csrf" }), {
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
    if (url.includes("/food-preferences/cuisines?")) {
      if (catalogResponse) return catalogResponse
      return new Response(JSON.stringify({
        items: [],
        allowed_actions: permissions.includes("food_preferences.catalog.manage")
          ? ["create", "update", "disable"]
          : [],
        access_revision: foodAdministrationRevision,
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.includes("/food-preferences/statistics?")) {
      if (statisticsResponse) return statisticsResponse
      return new Response(JSON.stringify({
        population: "students",
        include_inactive: false,
        total_people: 0,
        filled_people: 0,
        unfilled_people: 0,
        filled_ratio: 0,
        cuisines: [],
        dishes: [],
        people: { items: [], page: 1, page_size: 20, total: 0 },
        access_revision: foodAdministrationRevision,
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  return fetchMock
}

it.each([
  ["/people/food-preferences", "food_preferences.export"],
  ["/settings/food-cuisines", "food_preferences.catalog.manage"],
] as const)("rejects scoped-only food administration deep links at %s", async (path, permission) => {
  const fetchMock = foodAdministrationFetch({ scopedPermissions: [permission] })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
  expect(fetchMock.mock.calls.some(([input]) =>
    String(input).includes("/food-preferences/cuisines?"),
  )).toBe(false)
})

it("waits for fresh server authorization before rendering food statistics", async () => {
  let resolveCatalog!: (response: Response) => void
  const pendingCatalog = new Promise<Response>((resolve) => {
    resolveCatalog = resolve
  })
  vi.stubGlobal("fetch", foodAdministrationFetch({
    permissions: ["food_preferences.export"],
    catalogResponse: pendingCatalog,
  }))
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/people/food-preferences"],
  })
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("正在确认餐饮后台权限…")).toBeVisible()
  expect(screen.queryByRole("heading", { name: "餐饮偏好统计" })).not.toBeInTheDocument()
  resolveCatalog(new Response(JSON.stringify({
    items: [],
    allowed_actions: [],
    access_revision: foodAdministrationRevision,
  }), { status: 200, headers: { "Content-Type": "application/json" } }))
  expect(await screen.findByRole("heading", { name: "餐饮偏好统计" })).toBeVisible()
})

it("purges only the current food cache and leaves the route after private revocation", async () => {
  const privateResponse = new Response(JSON.stringify({
    type: "about:blank",
    title: "Not Found",
    status: 404,
    detail: "资源不存在",
    code: "not_found",
    request_id: "food-route-private",
  }), { status: 404, headers: { "Content-Type": "application/problem+json" } })
  vi.stubGlobal("fetch", foodAdministrationFetch({
    permissions: ["food_preferences.export"],
    statisticsResponse: privateResponse,
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const currentKey = [...foodPreferenceKeys.accountRoot("account-food-route"), "private"] as const
  const otherKey = [...foodPreferenceKeys.accountRoot("account-food-other"), "private"] as const
  queryClient.setQueryData(currentKey, { purpose: "当前账号用途" })
  queryClient.setQueryData(otherKey, { purpose: "其他账号用途" })
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/people/food-preferences"],
  })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(router.state.location.pathname).toBe("/")
  expect(queryClient.getQueryData(currentKey)).toBeUndefined()
  expect(queryClient.getQueryData(otherKey)).toEqual({ purpose: "其他账号用途" })
})

function routeJsonResponse(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

function registrationReviewer(permissions: string[]) {
  return {
    account_id: "10000000-0000-4000-8000-000000000191",
    person_id: "20000000-0000-4000-8000-000000000191",
    full_name: "注册审核员",
    email: null,
    force_password_change: false,
    permissions,
    scoped_permissions: [],
  }
}

const registrationConflictRouteItem = {
  application_id: "10000000-0000-4000-8000-000000000192",
  primary_purpose: "student",
  state: "conflict",
  masked_identifier: "138****7792",
  conflict_code: "multiple_person_match",
  candidate_count: 1,
  expires_at: "2026-09-16T10:00:00+08:00",
  created_at: "2026-08-16T10:00:00+08:00",
}

const registrationConflictRouteDetail = {
  ...registrationConflictRouteItem,
  full_name: "跨挂载冲突学员",
  identity_number: "110105199001021292",
  sex: "male",
  date_of_birth: "1990-01-02",
  verified_identifier: "13800007792",
  candidates: [{
    person_id: "20000000-0000-4000-8000-000000000192",
    full_name: "跨挂载候选学员",
    identity_number: "110105199001021292",
    sex: "male",
    date_of_birth: "1990-01-02",
    deleted: false,
  }],
}

it("restores the current registration detail only after real auth refresh remounts the route", async () => {
  let meCalls = 0
  let listCalls = 0
  let detailCalls = 0
  let finishRefresh!: (response: Response) => void
  const pendingRefresh = new Promise<Response>((resolve) => {
    finishRefresh = resolve
  })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      if (meCalls === 1) {
        return routeJsonResponse(registrationReviewer([
          "registration.review",
          "registration.sensitive.read",
        ]))
      }
      return pendingRefresh
    }
    if (url.endsWith("/auth/csrf")) {
      return routeJsonResponse({ csrf_token: "registration-route-csrf" })
    }
    if (url.endsWith("/portal/context")) return routeJsonResponse(noPortalContext)
    if (url.endsWith("/registration-admin/applications")) {
      listCalls += 1
      return routeJsonResponse([registrationConflictRouteItem])
    }
    if (url.endsWith(`/registration-admin/applications/${registrationConflictRouteItem.application_id}`)) {
      detailCalls += 1
      return routeJsonResponse({
        ...registrationConflictRouteDetail,
        verified_identifier: detailCalls === 1 ? "13800007792" : "13900008882",
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/settings/registrations"],
  })
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  await user.click(await screen.findByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007792")).toBeVisible()
  queryClient.setQueryData(
    registrationKeys.adminApplication(registrationConflictRouteItem.application_id, true),
    registrationConflictRouteDetail,
  )
  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(await screen.findByText("正在安全连接工作台…")).toBeVisible()
  expect(screen.queryByText("13800007792")).not.toBeInTheDocument()
  expect(
    queryClient.getQueryData(
      registrationKeys.adminApplication(registrationConflictRouteItem.application_id, true),
    ),
  ).toBeUndefined()
  expect(listCalls).toBe(1)
  expect(detailCalls).toBe(1)

  finishRefresh(routeJsonResponse(registrationReviewer([
    "registration.review",
    "registration.sensitive.read",
  ])))

  expect(await screen.findByText("13900008882")).toBeVisible()
  expect(listCalls).toBe(2)
  expect(detailCalls).toBe(2)
  expect(router.state.location.pathname).toBe("/settings/registrations")
})

const staffRouteItem = {
  application_id: "10000000-0000-4000-8000-000000000193",
  requested_staff_type: "coach",
  status: "pending",
  created_at: "2026-08-16T10:00:00+08:00",
}

const staffRouteDetail = {
  ...staffRouteItem,
  application_reason: "跨挂载审核资料",
  person_id: "20000000-0000-4000-8000-000000000193",
  full_name: "跨挂载申请教练",
  identity_number: "110105199001021293",
  sex: "male",
  date_of_birth: "1990-01-02",
  phone: "13800007793",
  email: "route-coach@example.com",
}

it("closes staff identity after real auth refresh remounts without sensitive permission", async () => {
  let meCalls = 0
  let listCalls = 0
  let detailCalls = 0
  let finishRefresh!: (response: Response) => void
  const pendingRefresh = new Promise<Response>((resolve) => {
    finishRefresh = resolve
  })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      if (meCalls === 1) {
        return routeJsonResponse(registrationReviewer([
          "staff_identity_applications.review",
          "registration.sensitive.read",
        ]))
      }
      return pendingRefresh
    }
    if (url.endsWith("/auth/csrf")) {
      return routeJsonResponse({ csrf_token: "staff-route-csrf" })
    }
    if (url.endsWith("/portal/context")) return routeJsonResponse(noPortalContext)
    if (url.endsWith("/registration-admin/staff-applications")) {
      listCalls += 1
      return routeJsonResponse([staffRouteItem])
    }
    if (url.endsWith(`/registration-admin/staff-applications/${staffRouteItem.application_id}`)) {
      detailCalls += 1
      return routeJsonResponse(staffRouteDetail)
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/settings/staff-applications"],
  })
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  await user.click(await screen.findByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007793")).toBeVisible()
  queryClient.setQueryData(
    registrationKeys.staffAdminApplication(staffRouteItem.application_id, true),
    staffRouteDetail,
  )
  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(await screen.findByText("正在安全连接工作台…")).toBeVisible()
  expect(screen.queryByText("13800007793")).not.toBeInTheDocument()
  expect(
    queryClient.getQueryData(
      registrationKeys.staffAdminApplication(staffRouteItem.application_id, true),
    ),
  ).toBeUndefined()
  expect(listCalls).toBe(1)
  expect(detailCalls).toBe(1)

  finishRefresh(routeJsonResponse(registrationReviewer([
    "staff_identity_applications.review",
  ])))

  expect(await screen.findByRole("button", { name: "处理申请" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "审核身份申请" })).not.toBeInTheDocument()
  expect(screen.queryByText("13800007793")).not.toBeInTheDocument()
  expect(listCalls).toBe(2)
  expect(detailCalls).toBe(1)
  expect(router.state.location.pathname).toBe("/settings/staff-applications")
})

it("keeps the authenticated registration route closed after a real network refresh failure", async () => {
  let meCalls = 0
  let listCalls = 0
  let detailCalls = 0
  let failRefresh!: (error: unknown) => void
  const pendingRefresh = new Promise<Response>((_, reject) => {
    failRefresh = reject
  })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      if (meCalls === 1) {
        return routeJsonResponse(registrationReviewer([
          "registration.review",
          "registration.sensitive.read",
        ]))
      }
      return pendingRefresh
    }
    if (url.endsWith("/auth/csrf")) {
      return routeJsonResponse({ csrf_token: "registration-failure-csrf" })
    }
    if (url.endsWith("/portal/context")) return routeJsonResponse(noPortalContext)
    if (url.endsWith("/registration-admin/applications")) {
      listCalls += 1
      return routeJsonResponse([registrationConflictRouteItem])
    }
    if (url.endsWith(`/registration-admin/applications/${registrationConflictRouteItem.application_id}`)) {
      detailCalls += 1
      return routeJsonResponse(registrationConflictRouteDetail)
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(appRoutes, {
    initialEntries: ["/settings/registrations"],
  })
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider><RouterProvider router={router} /></AuthProvider>
    </QueryClientProvider>,
  )

  await user.click(await screen.findByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007792")).toBeVisible()
  queryClient.setQueryData(
    registrationKeys.adminApplication(registrationConflictRouteItem.application_id, true),
    registrationConflictRouteDetail,
  )
  await user.click(screen.getByRole("button", { name: "刷新" }))
  expect(await screen.findByText("正在安全连接工作台…")).toBeVisible()

  failRefresh(new TypeError("Failed to fetch"))

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法刷新权限")
  expect(screen.getByRole("heading", { name: "注册处理" })).toBeVisible()
  expect(screen.queryByText("13800007792")).not.toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "登录 ERP" })).not.toBeInTheDocument()
  expect(
    queryClient.getQueryData(registrationKeys.adminApplications()),
  ).toBeUndefined()
  expect(listCalls).toBe(1)
  expect(detailCalls).toBe(1)
  expect(router.state.location.pathname).toBe("/settings/registrations")
})
