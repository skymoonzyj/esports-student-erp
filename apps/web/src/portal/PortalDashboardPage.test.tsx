import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import type { HousingPropertyCapabilities, HousingPropertyChange } from "../housing/propertyApi"
import type { PortalContext } from "./api"
import { portalKeys } from "./api"
import type { DepartureReminders } from "./departureApi"
import { PortalDashboardPage } from "./PortalDashboardPage"

const accountId = "10000000-0000-4000-8000-000000000090"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function authUser(permissions: string[] = [], scopedPermissions: string[] = []) {
  return {
    account_id: accountId,
    person_id: "20000000-0000-4000-8000-000000000090",
    full_name: "门户测试用户",
    email: "portal-dashboard@example.test",
    force_password_change: false,
    permissions,
    scoped_permissions: scopedPermissions,
  }
}

function coachResponse(overrides: Record<string, unknown> = {}) {
  return {
    observed_at: "2026-07-30T08:00:00+08:00",
    selected_starts_at: "2030-08-01T09:00:00+08:00",
    options: [
      {
        anchor_cohort_id: "cohort-1",
        starts_at: "2030-08-01T09:00:00+08:00",
        cohort_names: ["英雄联盟暑期营"],
        class_count: 2,
      },
    ],
    classes: [],
    items: [],
    page: 1,
    page_size: 1,
    total: 18,
    ...overrides,
  }
}

function renderDashboard(input: {
  context: PortalContext
  permissions?: string[]
  scopedPermissions?: string[]
  coach?: ReturnType<typeof coachResponse>
  students?: { items: unknown[]; total: number }
  portalError?: boolean
  queryClient?: QueryClient
  departures?: Partial<DepartureReminders>
  departureStatus?: number
  rentals?: boolean
  roomCards?: boolean
  propertyCapabilities?: HousingPropertyCapabilities
  propertyChanges?: HousingPropertyChange[]
  propertyList?: () => Promise<Response>
  contextResponse?: () => Promise<Response>
}) {
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser(input.permissions, input.scopedPermissions))
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "portal-dashboard-csrf" })
    }
    if (url.endsWith("/portal/context")) return input.contextResponse ? input.contextResponse() : jsonResponse(input.context)
    if (url.endsWith("/housing/catalog")) return jsonResponse({ access_revision: "property-rev", buildings: [], floors: [], can_create_building: false })
    if (url.includes("/housing/property/capabilities")) return jsonResponse(input.propertyCapabilities ?? { can_submit: false, can_review: false, can_export: false, show_basic_export: false })
    if (url.includes("/housing/property/changes?")) return input.propertyList ? input.propertyList() : jsonResponse({ items: input.propertyChanges ?? [], total: input.propertyChanges?.length ?? 0, page: 1, page_size: 20 })
    if (url.endsWith("/housing/change-requests/proposal-1/review")) {
      const item = input.propertyChanges![0]
      input.propertyChanges = [{ ...item, status: "approved", can_review: false }]
      return jsonResponse(input.propertyChanges[0])
    }
    if (url.includes("/portal/room-card-reminders?")) return jsonResponse({ available: input.roomCards ?? false, observed_at: "2026-11-05T08:00:00Z", business_date: "2026-11-05", next_refresh_at: "2026-11-05T16:00:00Z", items: input.roomCards ? [{ stay_id: "stay-1", person_id: "person-1", person_name: "房卡测试人员", nickname: null, building_name: "A", room_number: "A-101", issued_on: "2026-11-01", expires_on: "2026-11-12", days_remaining: 7 }] : [], total: input.roomCards ? 1 : 0, page: 1, page_size: 20 })
    if (url.includes("/portal/rental-reminders?")) return jsonResponse({ available: input.rentals ?? false, can_manage: false, observed_at: "2026-10-25T08:00:00Z", business_date: "2026-10-25", next_refresh_at: "2026-10-25T16:00:00Z", items: input.rentals ? [{ asset_id: "10000000-0000-4000-8000-000000000001", asset_code: "RENT-1", name: "租用电脑", person_name: null, location: "测试房间", rental_expires_on: "2026-10-24", days_remaining: -1, rental_revision: 0 }] : [], total: input.rentals ? 1 : 0, page: 1, page_size: 20 })
    if (url.includes("/portal/departure-reminders?")) {
      if (input.departureStatus) return jsonResponse({ type: "about:blank", title: "Request failed", status: input.departureStatus, detail: "private error", code: "departure_denied", request_id: "departure-error" }, input.departureStatus)
      return jsonResponse({ available: false, observed_at: "2026-10-24T08:00:00+08:00", business_date: "2026-10-24", timezone: "Asia/Shanghai", next_refresh_at: "2026-10-25T00:00:00+08:00", window: "three_days", today_count: 0, three_day_count: 0, page: 1, page_size: 20, total: 0, items: [], ...input.departures })
    }
    if (url.includes("/coach/next-cohorts?")) {
      if (input.portalError) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Server Error",
            status: 500,
            detail: "门户暂时不可用",
            code: "internal_error",
            request_id: "dashboard-error",
          },
          500,
        )
      }
      return jsonResponse(input.coach ?? coachResponse())
    }
    if (url.endsWith("/family/students")) {
      return jsonResponse(input.students ?? { items: [], total: 2 })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient =
    input.queryClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <PortalDashboardPage />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  return { fetchMock, queryClient }
}

afterEach(() => {
  focusManager.setFocused(undefined)
  vi.unstubAllGlobals()
})

const propertyProposal: HousingPropertyChange = { id: "proposal-1", room_id: "room1", room_number: "101", building_name: "一号楼", base_version: 8, changes: { notes: "私有巡查建议" }, before: { notes: "旧备注" }, reason: "漏水巡查", status: "pending", submitted_by_name: "物业甲", submitted_at: "2026-11-05T00:00:00Z", reviewed_at: null, review_note: null, can_review: true, can_withdraw: false }

it("lets server-authorized workspace reviewers confirm an approval without submitting or withdrawing", async () => {
  const { fetchMock } = renderDashboard({ context: { ...emptyContext, housing_management_available: true }, permissions: ["housing.read"], propertyCapabilities: { can_submit: true, can_review: true, can_export: true, show_basic_export: false }, propertyChanges: [propertyProposal] })
  expect(await screen.findByRole("region", { name: "物业变更审批" })).toBeVisible()
  expect(await screen.findByText("旧备注 → 私有巡查建议")).toBeVisible()
  const listCalls = fetchMock.mock.calls.filter(([url]) => String(url).includes("/housing/property/changes?"))
  expect(new URL(String(listCalls[0][0]), "https://test.invalid").searchParams.get("view")).toBe("review")
  expect(screen.queryByRole("button", { name: "撤回申请" })).toBeNull()
  expect(screen.queryByRole("button", { name: "提交待审申请" })).toBeNull()
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "通过申请" }))
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/review"))).toBe(false)
  await user.click(screen.getByRole("button", { name: "确认通过并生效" }))
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/housing/change-requests/proposal-1/review"))).toBe(true))
  await waitFor(() => expect(screen.queryByRole("button", { name: "通过申请" })).toBeNull())
})

it.each([false, true])("does not read approval rows for nonreviewers (housing read: %s)", async (housingRead) => {
  const { fetchMock, queryClient } = renderDashboard({ context: { ...emptyContext, housing_management_available: housingRead }, permissions: housingRead ? ["housing.read"] : [], propertyCapabilities: { can_submit: true, can_review: false, can_export: true, show_basic_export: true } })
  if (housingRead) await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/housing/property/capabilities"))).toBe(true))
  else await screen.findByRole("region", { name: "暂无工作项" })
  await waitFor(() => expect(queryClient.isFetching()).toBe(0))
  expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull()
  expect(screen.queryByText("无法核验物业申请权限，请刷新重试。")).toBeNull()
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/housing/property/changes?"))).toBe(false)
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/housing/property/capabilities"))).toBe(housingRead)
  expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/housing/catalog"))).toBe(housingRead)
})

it("suppresses late private review rows after authoritative capability revocation", async () => {
  let finish!: (response: Response) => void
  const input = { context: { ...emptyContext, housing_management_available: true }, permissions: ["housing.read"], propertyCapabilities: { can_submit: false, can_review: true, can_export: false, show_basic_export: false }, propertyList: () => new Promise<Response>((resolve) => { finish = resolve }) }
  const { fetchMock, queryClient } = renderDashboard(input)
  await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("/housing/property/changes?"))).toBe(true))
  input.propertyCapabilities = { can_submit: false, can_review: false, can_export: false, show_basic_export: false }
  const key = queryClient.getQueryCache().findAll().find((query) => query.queryKey.includes("capabilities"))!.queryKey
  await act(async () => { await queryClient.refetchQueries({ queryKey: key, exact: true }) })
  await waitFor(() => expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull())
  await act(async () => { finish(jsonResponse({ items: [propertyProposal], total: 1, page: 1, page_size: 20 })) })
  expect(screen.queryByText("物业甲")).toBeNull()
  expect(screen.queryByText("旧备注 → 私有巡查建议")).toBeNull()
  expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull()
})

it("hides workspace review content but retains an open draft during same-account portal revalidation", async () => {
  const input = { context: { ...emptyContext, housing_management_available: true }, permissions: ["housing.read"], propertyCapabilities: { can_submit: false, can_review: true, can_export: false, show_basic_export: false }, propertyChanges: [propertyProposal], contextResponse: undefined as (() => Promise<Response>) | undefined }
  const { queryClient, fetchMock } = renderDashboard(input)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "驳回申请" }))
  await user.type(screen.getByLabelText("审核说明"), "核实巡检资料")
  let finish!: (response: Response) => void
  input.contextResponse = () => new Promise<Response>((resolve) => { finish = resolve })
  let refresh!: Promise<void>
  act(() => { refresh = queryClient.refetchQueries({ queryKey: portalKeys.context(accountId), exact: true }) })
  await waitFor(() => expect(screen.queryByRole("region", { name: "物业变更审批" })).toBeNull())
  expect(screen.getByLabelText("审核说明")).not.toBeVisible()
  await act(async () => { finish(jsonResponse(input.context)); await refresh })
  await waitFor(() => expect(screen.getByLabelText("审核说明")).toBeVisible())
  expect(screen.getByLabelText("审核说明")).toHaveValue("核实巡检资料")
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/review"))).toBe(false)
})

it("renders rental reminders once alongside existing back office cards", async () => {
  renderDashboard({ context: emptyContext, permissions: ["people.list", "assets.read"], rentals: true })
  expect(await screen.findByRole("heading", { name: "租用资产到期提醒" })).toBeVisible()
  expect(screen.getAllByRole("heading", { name: "租用资产到期提醒" })).toHaveLength(1)
  expect(await screen.findByText("租用电脑")).toBeVisible()
  expect(screen.getByText("已逾期 1 天")).toBeVisible()
  expect(screen.getByRole("link", { name: "查看资产台账" })).toHaveAttribute("href", "/assets")
})

it("shows a holder's room card reminder without requiring another portal capability", async () => {
  renderDashboard({ context: emptyContext, roomCards: true })
  expect(await screen.findByRole("heading", { name: "房卡到期提醒" })).toBeVisible()
  expect(screen.getAllByRole("heading", { name: "房卡到期提醒" })).toHaveLength(1)
  expect(await screen.findByText("房卡测试人员")).toBeVisible()
  expect(screen.getByText("7 天后到期")).toBeVisible()
  expect(screen.queryByText("当前暂无可用工作项")).not.toBeInTheDocument()
})

it("does not restore room-card requests or rows after a failed focus permission check with cached context", async () => {
  const { fetchMock, queryClient } = renderDashboard({ context: emptyContext, roomCards: true })
  expect(await screen.findByText("房卡测试人员")).toBeVisible()
  const epoch = queryClient.getQueryState(portalKeys.context(accountId))?.dataUpdatedAt
  const base = fetchMock.getMockImplementation()!
  let finish!: (response: Response) => void
  fetchMock.mockImplementation((request, init) => String(request).endsWith("/portal/context") ? new Promise((resolve) => { finish = resolve }) : base(request, init))
  const cardRequests = () => fetchMock.mock.calls.filter(([request]) => String(request).includes("/portal/room-card-reminders?")).length
  expect(cardRequests()).toBe(1)
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(finish).toBeDefined())
  await waitFor(() => expect(screen.queryByText("房卡测试人员")).not.toBeInTheDocument())
  await act(async () => finish(jsonResponse({ detail: "权限校验暂时失败" }, 500)))
  await waitFor(() => expect(queryClient.getQueryState(portalKeys.context(accountId))?.status).toBe("error"))
  expect(queryClient.getQueryState(portalKeys.context(accountId))?.dataUpdatedAt).toBe(epoch)
  expect(cardRequests()).toBe(1)
  expect(screen.queryByText("房卡测试人员")).not.toBeInTheDocument()
  expect(screen.getByText("房卡提醒暂时无法更新")).toBeVisible()
  fetchMock.mockImplementation(base)
  await act(async () => { await queryClient.invalidateQueries({ queryKey: portalKeys.context(accountId), exact: true }) })
  expect(await screen.findByText("房卡测试人员")).toBeVisible()
  expect(cardRequests()).toBe(2)
})

it("shows coach, learning and payment tasks for a multi-identity account", async () => {
  renderDashboard({
    context: {
      coach_available: true,
      learning_available: true,
      payments_available: true,
      housing_management_available: false,
      housing_available: true,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: "family",
    },
  })

  expect(await screen.findByRole("link", { name: "查看下一期名单" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看家庭信息" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看本人付款" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看住宿信息" })).toBeVisible()
  expect(screen.getByText("英雄联盟暑期营")).toBeVisible()
  expect(screen.getByText("18 名待查看")).toBeVisible()
})

const emptyContext: PortalContext = {
  coach_available: false, learning_available: false, payments_available: false, housing_management_available: false,
  housing_available: false, invitations_available: false, adult_guardian_invitations_available: false,
  food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null,
}

it.each([
  ["mixed portal and back-office", { ...emptyContext, coach_available: true, learning_available: true }, ["people.list"]],
  ["pure back-office", emptyContext, ["people.list"]],
  ["related housing summary only", emptyContext, []],
] as const)("renders one authorized reminder panel for %s", async (_label, context, permissions) => {
  renderDashboard({ context, permissions: [...permissions], departures: { available: true } })
  expect(await screen.findByText("所选日期内暂无退宿或结营提醒")).toBeVisible()
  expect(screen.getAllByRole("region", { name: "退宿／结营提醒" })).toHaveLength(1)
  expect(screen.queryByText("当前暂无可用工作项")).not.toBeInTheDocument()
  if (permissions.length) expect(screen.getByText("人员与实名档案")).toBeVisible()
  if (_label === "pure back-office") expect(screen.getByRole("region", { name: "业务快捷入口" })).toBeVisible()
  if (context.coach_available) {
    expect(await screen.findByRole("link", { name: "查看下一期名单" })).toBeVisible()
    expect(screen.getByRole("link", { name: "查看学习信息" })).toBeVisible()
  }
})

it("does not render a reminder module without server capability", async () => {
  renderDashboard({ context: emptyContext })
  expect(await screen.findByText("当前暂无可用工作项")).toBeVisible()
  expect(screen.queryByRole("region", { name: "退宿／结营提醒" })).not.toBeInTheDocument()
})

it("keeps existing coach and learning cards usable when reminders fail", async () => {
  renderDashboard({ context: { ...emptyContext, coach_available: true, learning_available: true }, departureStatus: 400 })
  expect(await screen.findByText("退宿／结营提醒暂时无法更新")).toBeVisible()
  expect(screen.getByRole("link", { name: "查看下一期名单" })).toBeVisible()
  expect(screen.getByText("18 名待查看")).toBeVisible()
  expect(screen.getByRole("link", { name: "查看学习信息" })).toBeVisible()
  expect(screen.queryByText("工作台暂时无法更新")).not.toBeInTheDocument()
})

it("routes a reminder denial through account-scoped portal cache cleanup", async () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const currentKey = portalKeys.students(accountId)
  const otherKey = portalKeys.students("another-account")
  queryClient.setQueryData(currentKey, { items: [{ full_name: "旧授权私密学员" }], total: 1 })
  queryClient.setQueryData(otherKey, { items: [], total: 0 })
  renderDashboard({ context: emptyContext, departureStatus: 403, queryClient })
  await waitFor(() => expect(queryClient.getQueryData(currentKey)).toBeUndefined())
  expect(queryClient.getQueryData(otherKey)).toEqual({ items: [], total: 0 })
  expect(screen.queryByText("旧授权私密学员")).not.toBeInTheDocument()
  expect(screen.queryByText("private error")).not.toBeInTheDocument()
  queryClient.clear()
})

it("keeps the existing back-office overview for a pure admin", async () => {
  renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: false,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    permissions: ["people.list", "accounts.manage"],
  })

  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(screen.getByRole("link", { name: "进入人员中心" })).toHaveAttribute("href", "/people")
  expect(screen.queryByRole("link", { name: "进入住宿管理" })).not.toBeInTheDocument()
  expect(screen.queryByText("教练工作")).not.toBeInTheDocument()
})

it("shows only authorized operational shortcuts for a scoped housing manager", async () => {
  renderDashboard({ context: { ...emptyContext, housing_management_available: true }, scopedPermissions: ["housing.read"] })
  expect(await screen.findByRole("link", { name: "进入住宿管理" })).toHaveAttribute("href", "/housing")
  expect(screen.queryByRole("link", { name: "进入人员中心" })).not.toBeInTheDocument()
  expect(screen.queryByText("平台基础阶段")).not.toBeInTheDocument()
})

it("does not widen globally guarded operations from scoped-only permissions", async () => {
  renderDashboard({ context: emptyContext, scopedPermissions: ["people.list", "payments.read", "food_preferences.export", "audit.read"] })
  await screen.findByRole("heading", { name: "工作台" })
  await waitFor(() => expect(screen.queryByText("正在更新工作台")).not.toBeInTheDocument())
  expect(screen.queryByRole("link", { name: "进入人员中心" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "进入收款管理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "进入餐饮统计" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "进入安全审计" })).not.toBeInTheDocument()
})

it.each([
  ["accounts.read", "进入账号管理", "/settings/accounts"],
  ["revenue_share.finance", "进入财务结算", "/finance/revenue-share"],
])("preserves scoped-only %s access through its operational shortcut", async (permission, action, href) => {
  renderDashboard({ context: emptyContext, scopedPermissions: [permission] })
  expect(await screen.findByRole("link", { name: action })).toHaveAttribute("href", href)
  expect(screen.queryByRole("link", { name: "进入人员中心" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "进入收款管理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "进入安全审计" })).not.toBeInTheDocument()
})

it("gives a procurement-only account its authorized operational entry", async () => {
  renderDashboard({ context: emptyContext, scopedPermissions: ["procurement.read"] })
  expect(await screen.findByRole("link", { name: "进入日常采购" })).toHaveAttribute("href", "/procurement")
  expect(screen.queryByRole("link", { name: "进入人员中心" })).not.toBeInTheDocument()
})

it("summarizes authorized projections without additional private-data requests", async () => {
  const { fetchMock } = renderDashboard({ context: { ...emptyContext, coach_available: true, learning_available: true }, departures: { available: true, today_count: 3, three_day_count: 5 }, rentals: true })
  const summary = await screen.findByRole("region", { name: "工作概览" })
  await waitFor(() => expect(summary).toHaveTextContent("今日退宿／结营"))
  expect(summary).toHaveTextContent("待查看学员")
  expect(summary).toHaveTextContent("18")
  expect(summary).toHaveTextContent("租用到期提醒")
  expect(fetchMock.mock.calls.some(([request]) => String(request).includes("/housing/rooms"))).toBe(false)
})

it("does not read payment amounts just to render a payment task", async () => {
  const { fetchMock } = renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: true,
      housing_management_available: false,
      housing_available: false,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
  })

  expect(await screen.findByRole("link", { name: "查看本人付款" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) => String(request).includes("/me/payments")),
  ).toBe(false)
})

it("renders a personal housing task without reading the housing projection", async () => {
  const { fetchMock } = renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: true,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
  })

  const link = await screen.findByRole("link", { name: "查看住宿信息" })
  expect(link).toHaveAttribute("href", "/me/housing")
  expect(screen.getByText("当前住宿与未来预约")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/me/housing"),
    ),
  ).toBe(false)
  expect(screen.queryByText("人员与实名档案")).not.toBeInTheDocument()
})

it.each([
  ["global", ["housing.read"], []],
  ["scoped", [], ["housing.read"]],
] as const)("keeps the back-office dashboard for a %s housing manager without promoting personal housing", async (_label, permissions, scopedPermissions) => {
  renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: true,
      housing_available: false,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    permissions: [...permissions],
    scopedPermissions: [...scopedPermissions],
  })

  expect(await screen.findByRole("link", { name: "进入住宿管理" })).toBeVisible()
  expect(screen.queryByRole("link", { name: "查看住宿信息" })).not.toBeInTheDocument()
})

it("shows the evaluation card without reading the evaluation context", async () => {
  const { fetchMock } = renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: false,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    permissions: ["evaluation_center.access"],
  })

  expect(
    await screen.findByRole("link", { name: "进入评价中心" }),
  ).toBeVisible()
  expect(screen.queryByText("当前暂无可用工作项")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/evaluations/context"),
    ),
  ).toBe(false)
})

it("hides cached portal names and counts when reauthorization fails", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(portalKeys.coach(accountId, undefined, 1),
    coachResponse({
      options: [
        {
          anchor_cohort_id: "old-cohort",
          starts_at: "2030-08-01T09:00:00+08:00",
          cohort_names: ["旧缓存名单"],
          class_count: 1,
        },
      ],
      total: 9,
    }),
  )
  const user = userEvent.setup()
  renderDashboard({
    context: {
      coach_available: true,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: false,
      invitations_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    portalError: true,
    queryClient,
  })

  expect(await screen.findByRole("alert")).toHaveTextContent("门户暂时不可用")
  expect(screen.queryByText("旧缓存名单")).not.toBeInTheDocument()
  expect(screen.queryByText("9 名待查看")).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "重试" }))
  expect(screen.getByRole("alert")).toBeVisible()
})

it("reauthorizes before refreshing a dashboard projection once", async () => {
  let contextCalls = 0
  let coachCalls = 0
  let departureCalls = 0
  let resolveContext!: (response: Response) => void
  let resolveCoach!: (response: Response) => void
  const pendingContext = new Promise<Response>((resolve) => {
    resolveContext = resolve
  })
  const pendingCoach = new Promise<Response>((resolve) => {
    resolveCoach = resolve
  })
  const context: PortalContext = {
    coach_available: true,
    learning_available: false,
    payments_available: false,
    housing_management_available: false,
    housing_available: false,
    invitations_available: false,
    adult_guardian_invitations_available: false,
    food_profile_available: false,
    food_preferences_onboarding_required: false,
    learning_mode: null,
  }
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser())
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "dashboard-focus-csrf" })
    }
    if (url.endsWith("/portal/context")) {
      contextCalls += 1
      return contextCalls === 1 ? jsonResponse(context) : pendingContext
    }
    if (url.includes("/coach/next-cohorts?")) {
      coachCalls += 1
      return coachCalls === 1
        ? jsonResponse(coachResponse())
        : pendingCoach
    }
    if (url.includes("/portal/departure-reminders?")) {
      departureCalls += 1
      return jsonResponse({ available: true, observed_at: "2026-10-24T08:00:00+08:00", business_date: "2026-10-24", timezone: "Asia/Shanghai", next_refresh_at: "2026-10-25T00:00:00+08:00", window: "three_days", today_count: 1, three_day_count: 1, page: 1, page_size: 20, total: 1, items: [{ person_id: "10000000-0000-4000-8000-000000000001", full_name: departureCalls === 1 ? "旧提醒学员" : "新提醒学员", nickname: null, events: [{ kind: "planned_checkout", source_id: "20000000-0000-4000-8000-000000000001", date: "2026-10-24", detail_path: null, building_name: "一号楼", room_number: "101" }] }] })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <AuthProvider>
          <PortalDashboardPage />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("英雄联盟暑期营")).toBeVisible()
  expect(await screen.findByText("旧提醒学员")).toBeVisible()
  expect(coachCalls).toBe(1)
  expect(departureCalls).toBe(1)

  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await waitFor(() => expect(contextCalls).toBe(2))
  expect(screen.queryByText("英雄联盟暑期营")).not.toBeInTheDocument()
  expect(screen.queryByText("旧提醒学员")).not.toBeInTheDocument()
  expect(coachCalls).toBe(1)
  expect(departureCalls).toBe(1)

  await act(async () => {
    resolveContext(jsonResponse(context))
  })
  await waitFor(() => expect(coachCalls).toBe(2))
  expect(await screen.findByText("新提醒学员")).toBeVisible()
  expect(departureCalls).toBe(2)
  expect(screen.queryByText("英雄联盟暑期营")).not.toBeInTheDocument()

  await act(async () => {
    resolveCoach(
      jsonResponse(
        coachResponse({
          options: [
            {
              anchor_cohort_id: "cohort-refocused",
              starts_at: "2030-08-15T09:00:00+08:00",
              cohort_names: ["重新授权后的营期"],
              class_count: 1,
            },
          ],
        }),
      ),
    )
  })
  expect(await screen.findByText("重新授权后的营期")).toBeVisible()
  expect(coachCalls).toBe(2)
})
