import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import type { HousingCatalog, HousingOverview, HousingRoom } from "../housing/api"
import { housingKeys } from "../housing/housingKeys"
import type { PortalContext } from "../portal/api"
import { appRoutes } from "./router"
import type * as LazyModule from "./loading/lazyModule"

const heldImport = vi.hoisted(() => {
  let release!: () => void
  const wait = new Promise<void>(resolve => { release = resolve })
  return { wait, release, started: false, completed: false, mode: undefined as string | undefined }
})
vi.mock('./loading/lazyModule', async original => {
  const actual = await original<typeof LazyModule>()
  return { ...actual, createLazyModule: (id: string, loader: Parameters<typeof actual.createLazyModule>[1], mode: Parameters<typeof actual.createLazyModule>[2]) => actual.createLazyModule(id, async () => {
    if (id !== 'HousingOverviewPage') return loader()
    heldImport.started = true
    heldImport.mode = mode
    await heldImport.wait
    const module = await loader()
    heldImport.completed = true
    return module
  }, mode) }
})

const accountId = "account-housing-focus"
const context: PortalContext = {
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
}

function catalog(accessRevision: string): HousingCatalog {
  return {
    access_revision: accessRevision,
    can_create_building: false,
    buildings: [{
      id: "building-a", code: "A", name: "一号楼", display_order: 1,
      is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "",
    }],
    floors: [{
      id: "floor-a4", building_id: "building-a", building_name: "一号楼", number: 4,
      name: "四层", display_order: 4, is_active: true, notes: null,
      allowed_actions: [], created_at: "", updated_at: "",
    }],
  }
}

function overview(accessRevision: string, page: number): HousingOverview {
  const room: HousingRoom = {
    id: `room-${page === 2 ? "421" : "401"}`,
    building_id: "building-a", floor_id: "floor-a4", building_code: "A",
    building_name: "一号楼", building_is_active: true, floor_number: 4, floor_name: "四层",
    floor_is_active: true, room_number: page === 2 ? "421" : "401", room_type: "single",
    capacity: 1, area_square_meters: "18.00", verification_status: "verified",
    service_status: "available", notes: null, version: 1, current_occupants: 1,
    remaining_capacity: 0, current_gender: "male", has_future_reservation: false,
    occupancy_status: "occupied", is_assignable: false, status_text: "已入住",
    allowed_actions: { edit: false, delete: false, verify: false, create_stay: false, report_issue: false, view_detail: false },
    occupants: [], future_reservation_count: 0, open_issue_summary: null,
    pending_transfer_count: 0, created_at: "", updated_at: "",
  }
  return {
    access_revision: accessRevision, as_of: "2026-10-10T01:30:00Z",
    summary: {
      total_rooms: 21, occupied_rooms: 21, unoccupied_rooms: 0, assignable_rooms: 0,
      unassignable_rooms: 21, problem_rooms: 0, pending_verification_rooms: 0,
      total_capacity: 21, current_occupants: 21, remaining_capacity: 0,
      occupancy_rate: "100.00", single_rooms: 21, double_rooms: 0, triple_rooms: 0,
      custom_rooms: 0, functional_rooms: 0,
    },
    rooms: [room], page, page_size: 20, total: 21,
  }
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })
}

function renderHousingRoute(nextRevision = "revision-a") {
  let contextRequests = 0
  let reauthorizing = false
  let resolveContext!: (response: Response) => void
  const pendingContext = new Promise<Response>((resolve) => { resolveContext = resolve })
  const overviewRequests: URLSearchParams[] = []
  const authUser = {
    account_id: accountId, person_id: "person-housing-focus", full_name: "房态管理员",
    email: "housing-focus@example.test", force_password_change: false,
    permissions: ["housing.read"], scoped_permissions: [],
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = new URL(String(input), "http://localhost")
    if (url.pathname.endsWith("/auth/me")) return jsonResponse(authUser)
    if (url.pathname.endsWith("/auth/login")) return jsonResponse({ user: authUser, csrf_token: "housing-login-csrf" })
    if (url.pathname.endsWith("/auth/logout")) return new Response(null, { status: 204 })
    if (url.pathname.endsWith("/auth/csrf")) return jsonResponse({ csrf_token: "housing-focus-csrf" })
    if (url.pathname.endsWith("/team-management/context")) return jsonResponse({
      can_open_management: false, can_read_self: false, can_manage_catalog: false,
      can_assign_managers: false, can_transfer: false,
    })
    if (url.pathname.endsWith("/portal/context")) {
      contextRequests += 1
      return !reauthorizing ? jsonResponse(context) : pendingContext.then((response) => response.clone())
    }
    const revision = reauthorizing ? nextRevision : "revision-a"
    if (url.pathname.endsWith("/housing/catalog")) return jsonResponse(catalog(revision))
    if (url.pathname.endsWith("/housing/overview")) {
      overviewRequests.push(url.searchParams)
      return jsonResponse(overview(revision, Number(url.searchParams.get("page") ?? 1)))
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/housing"] })
  render(<QueryClientProvider client={queryClient}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  return {
    router, queryClient, overviewRequests,
    contextRequests: () => contextRequests,
    beginReauthorization: () => { reauthorizing = true },
    resolveContext: (value = context) => resolveContext(jsonResponse(value)),
  }
}

async function filterAndOpenSecondPage() {
  const user = userEvent.setup()
  expect(await screen.findByRole("article", { name: "房间 401：已入住 1 / 1" })).toBeVisible()
  await user.selectOptions(screen.getByLabelText("楼栋"), "building-a")
  await user.selectOptions(screen.getByLabelText("楼层"), "floor-a4")
  await user.selectOptions(screen.getByLabelText("入住状态"), "occupied")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  expect(await screen.findByRole("article", { name: "房间 401：已入住 1 / 1" })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByRole("article", { name: "房间 421：已入住 1 / 1" })).toBeVisible()
  expect(screen.getByLabelText("楼栋")).toHaveValue("building-a")
  expect(screen.getByLabelText("楼层")).toHaveValue("floor-a4")
  expect(screen.getByLabelText("入住状态")).toHaveValue("occupied")
  expect(screen.getByText("第 2 / 2 页")).toBeVisible()
}

async function refocusWhileContextIsPending(route: ReturnType<typeof renderHousingRoute>) {
  // The lazy shell can mount the guard after the shell's context completed.
  // Hold only the requested focus reauthorization, not initial guard mounting.
  const previousRequests = route.contextRequests()
  route.beginReauthorization()
  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await waitFor(() => expect(route.contextRequests()).toBe(previousRequests + 1))
  expect(screen.getByText("正在安全连接工作台…")).toBeVisible()
  expect(screen.queryByRole("article", { name: "房间 421：已入住 1 / 1" })).not.toBeInTheDocument()
  expect(screen.queryByRole("list", { name: "房间列表" })).not.toBeInTheDocument()
}

afterEach(() => {
  focusManager.setFocused(undefined)
  vi.unstubAllGlobals()
})

it("revokes the real housing guard during its held import without resurrecting private DOM or issuing old housing queries", async () => {
  const route = renderHousingRoute()
  await waitFor(() => expect(heldImport.started).toBe(true))
  expect(heldImport.mode).toBe('controlled')
  expect(screen.queryByRole('heading', { name: '房态管理' })).not.toBeInTheDocument()
  expect(route.overviewRequests).toHaveLength(0)
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).endsWith('/housing/catalog'))).toBe(false)
  route.queryClient.setQueryData(housingKeys.scope(accountId, 'revision-old'), { private: true })
  await refocusWhileContextIsPending(route)
  await act(async () => route.resolveContext({ ...context, housing_management_available: false }))
  await waitFor(() => expect(route.router.state.location.pathname).toBe('/'))
  expect(route.queryClient.getQueryCache().findAll({ queryKey: housingKeys.root(accountId) })).toHaveLength(0)
  await act(async () => heldImport.release())
  await waitFor(() => expect(heldImport.completed).toBe(true))
  expect(screen.queryByRole('heading', { name: '房态管理' })).not.toBeInTheDocument()
  expect(screen.queryByRole('list', { name: '房间列表' })).not.toBeInTheDocument()
  expect(route.overviewRequests).toHaveLength(0)
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).endsWith('/housing/catalog'))).toBe(false)
  expect(route.queryClient.getQueryCache().findAll({ queryKey: housingKeys.root(accountId) })).toHaveLength(0)
})

it("preserves real housing filters and page after focus reauthorization without exposing private rooms while pending", async () => {
  const route = renderHousingRoute()
  await filterAndOpenSecondPage()
  expect(Object.fromEntries(route.overviewRequests.at(-1)!)).toEqual({
    building_id: "building-a", floor_id: "floor-a4", occupancy: "occupied", page: "2", page_size: "20",
  })
  const requestsBeforeFocus = route.overviewRequests.length

  await refocusWhileContextIsPending(route)
  await act(async () => route.resolveContext())

  await waitFor(() => {
    expect(screen.getByLabelText("楼栋")).toHaveValue("building-a")
    expect(screen.getByLabelText("楼层")).toHaveValue("floor-a4")
    expect(screen.getByLabelText("入住状态")).toHaveValue("occupied")
  })
  expect(await screen.findByRole("article", { name: "房间 421：已入住 1 / 1" })).toBeVisible()
  expect(screen.getByText("第 2 / 2 页")).toBeVisible()
  expect(route.router.state.location.pathname).toBe("/housing")
  await waitFor(() => expect(route.overviewRequests.length).toBeGreaterThan(requestsBeforeFocus))
  expect(Object.fromEntries(route.overviewRequests.at(-1)!)).toEqual({
    building_id: "building-a", floor_id: "floor-a4", occupancy: "occupied", page: "2", page_size: "20",
  })
})

it("clears the previous filters and returns to page one when focus detects a changed housing access revision", async () => {
  const route = renderHousingRoute("revision-b")
  await filterAndOpenSecondPage()

  await refocusWhileContextIsPending(route)
  await act(async () => route.resolveContext())

  expect(await screen.findByRole("article", { name: "房间 401：已入住 1 / 1" })).toBeVisible()
  expect(screen.getByLabelText("楼栋")).toHaveValue("")
  expect(screen.getByLabelText("楼层")).toHaveValue("")
  expect(screen.getByLabelText("入住状态")).toHaveValue("")
  expect(screen.getByText("第 1 / 2 页")).toBeVisible()
  expect(Object.fromEntries(route.overviewRequests.at(-1)!)).toEqual({ page: "1", page_size: "20" })
  await waitFor(() => expect(route.queryClient.getQueryCache().findAll({ queryKey: housingKeys.scope(accountId, "revision-a") })).toHaveLength(0))
})

it("keeps private housing rooms absent and purges their cache when focus revokes management capability", async () => {
  const route = renderHousingRoute()
  await filterAndOpenSecondPage()

  await refocusWhileContextIsPending(route)
  await act(async () => route.resolveContext({ ...context, housing_management_available: false }))

  await waitFor(() => expect(route.router.state.location.pathname).toBe("/"))
  expect(screen.queryByRole("list", { name: "房间列表" })).not.toBeInTheDocument()
  expect(screen.queryByRole("article", { name: "房间 421：已入住 1 / 1" })).not.toBeInTheDocument()
  expect(route.queryClient.getQueryCache().findAll({ queryKey: housingKeys.root(accountId) })).toHaveLength(0)
})

it("does not restore housing filters from the previous login when the same account signs in again", async () => {
  const route = renderHousingRoute()
  const user = userEvent.setup()
  await filterAndOpenSecondPage()
  const requestsBeforeLogout = route.overviewRequests.length
  // Let subsequent real capability requests complete after the new login.
  route.resolveContext()

  await user.click(screen.getByRole("button", { name: "退出登录" }))
  expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeVisible()
  expect(screen.queryByRole("list", { name: "房间列表" })).not.toBeInTheDocument()
  expect(route.queryClient.getQueryCache().findAll({ queryKey: housingKeys.root(accountId) })).toHaveLength(0)

  await user.type(screen.getByLabelText("手机号或邮箱"), "housing-focus@example.test")
  await user.type(screen.getByLabelText("密码"), "TestPassword123")
  await user.click(screen.getByRole("button", { name: "登录" }))
  await screen.findAllByRole("link", { name: "房态管理" })
  await user.click(screen.getAllByRole("link", { name: "房态管理" })[0])

  expect(await screen.findByRole("article", { name: "房间 401：已入住 1 / 1" })).toBeVisible()
  expect(route.router.state.location.pathname).toBe("/housing")
  expect(screen.getByLabelText("楼栋")).toHaveValue("")
  expect(screen.getByLabelText("楼层")).toHaveValue("")
  expect(screen.getByLabelText("入住状态")).toHaveValue("")
  expect(screen.getByText("第 1 / 2 页")).toBeVisible()
  const newLoginRequests = route.overviewRequests.slice(requestsBeforeLogout)
  expect(newLoginRequests.length).toBeGreaterThan(0)
  for (const request of newLoginRequests) {
    expect(Object.fromEntries(request)).toEqual({ page: "1", page_size: "20" })
  }
})
