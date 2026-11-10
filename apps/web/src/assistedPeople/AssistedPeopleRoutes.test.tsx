import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import { AuthProvider } from "../auth/AuthProvider"
import { appRoutes } from "../app/router"

const id = "30000000-0000-4000-8000-000000000001"
const coach = "30000000-0000-4000-8000-000000000002"
const caps = { can_submit: true, can_review: false, can_direct_create: false, can_read_managed: true, allowed_team_ids: [], eligible_coaches: [{ id: coach, label: "负责教练" }], eligible_teams: [] }
const portal = { coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null }
function install(options: { context?: typeof caps; failContext?: boolean; permissions?: string[] } = {}) {
  const calls: string[] = []
  vi.stubGlobal("fetch", vi.fn(async (input) => {
    const url = String(input)
    calls.push(url)
    if (url.endsWith("/auth/me")) return Response.json({ account_id: id, person_id: id, full_name: "教练", email: null, force_password_change: false, permissions: options.permissions ?? [] })
    if (url.endsWith("/auth/csrf")) return Response.json({ csrf_token: "csrf" })
    if (url.endsWith("/portal/context")) return Response.json(portal)
    if (url.endsWith("/team-management/context")) return Response.json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (url.endsWith("/personnel-intake/context")) return options.failContext ? Response.json({ code: "unavailable" }, { status: 503 }) : Response.json(options.context ?? caps)
    if (url.endsWith("/personnel-intake/activation-role-options")) return Response.json({ items: [{ key: "coach", name: "教练", assignable: true, reason: null }] })
    if (url.endsWith("/personnel-intake/management-identity-context")) return Response.json({ role_options: [{ key: "student", name: "学员", assignable: true, reason: null }], can_assign_staff_teams: false, staff_team_reason: "须由有权人员分配", staff_team_options: [] })
    if (url.includes("/personnel-intake/requests?")) return Response.json({ items: [], total: 0, page: 1, page_size: 20 })
    if (url.includes("/personnel-intake/managed-people?")) return Response.json({ items: [], total: 0, next_cursor: null })
    if (url.includes("/people/counts")) return Response.json({ total: 0, student: 0, guardian: 0, staff: 0, coach: 0, life_teacher: 0, customer_service: 0, administration: 0, other_staff: 0, visitor: 0 })
    if (/\/people\/assisted-/.test(url)) return Response.json({ code: "not_found" }, { status: 404 })
    return Response.json({ items: [], total: 0, page: 1, page_size: 20 })
  }))
  return calls
}
function mount(path: string) {
  const router = createMemoryRouter(appRoutes, { initialEntries: [path] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)
  return router
}
afterEach(() => { vi.unstubAllGlobals(); focusManager.setFocused(undefined) })

it.each([true, false])("person-scoped reviewer with empty selectors has review route/menu only when allowed=%s", async (allowed) => {
  const calls = install({ context: { ...caps, can_submit: false, can_review: allowed, can_direct_create: false,
    can_read_managed: false, eligible_coaches: [], eligible_teams: [] } })
  mount("/people/assisted-review")
  if (allowed) {
    expect(await screen.findByRole("heading", { name: "建档审核" })).toBeVisible()
    expect(await screen.findByText("共 0 条")).toBeVisible()
  } else expect(await screen.findByText("无建档审核权限。")).toBeVisible()
  const nav = within(screen.getByRole("navigation", { name: "主导航" }))
  if (allowed) expect(nav.getByRole("link", { name: "建档审核" })).toHaveAttribute("href", "/people/assisted-review")
  else expect(nav.queryByRole("link", { name: "建档审核" })).not.toBeInTheDocument()
  expect(nav.queryByRole("link", { name: "管理建档" })).not.toBeInTheDocument()
  expect(nav.queryByRole("link", { name: "我的建档申请" })).not.toBeInTheDocument()
  expect(calls.some((url) => url.includes("/requests?") && url.includes("view=review"))).toBe(allowed)
})

it("routes a coach without people.list to own requests and owner list via actual menu", async () => {
  const calls = install()
  mount("/people/assisted-requests")
  expect(await screen.findByRole("heading", { name: "我的建档申请" })).toBeVisible()
  const nav = screen.getByRole("navigation", { name: "主导航" })
  expect(within(nav).getByRole("link", { name: "我的建档申请" })).toHaveAttribute("href", "/people/assisted-requests")
  expect(within(nav).queryByRole("link", { name: "建档审核" })).not.toBeInTheDocument()
  expect(within(nav).queryByRole("link", { name: "管理建档" })).not.toBeInTheDocument()
  expect(within(nav).queryByRole("link", { name: "人员中心" })).not.toBeInTheDocument()
  await userEvent.setup().click(within(nav).getByRole("link", { name: "我的学员／队员" }))
  expect(await screen.findByRole("heading", { name: "我的学员／队员" })).toBeVisible()
  await screen.findByText("共 0 人")
  expect(calls.some((url) => /\/people\?|\/people\/counts/.test(url))).toBe(false)
})

it.each([
  ["/people/assisted-review", "无建档审核权限。"],
  ["/people/assisted-create", "无管理建档权限。"],
  ["/my-people", "无所属人员查看权限。"],
])("denies direct route %s with no matching context capability despite global grants", async (path, denial) => {
  const calls = install({ context: { ...caps, can_submit: false, can_read_managed: false }, permissions: ["people.list", "people.create"] })
  mount(path)
  expect(await screen.findByText(denial)).toBeVisible()
  expect(calls.some((url) => url.includes("/managed-people?") || url.includes("/requests?"))).toBe(false)
})

it("focus revalidates the composed route without replacing an existing staff draft", async () => {
  install()
  mount("/people/assisted-requests")
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "新建申请" }))
  const input = screen.getByLabelText("姓名")
  await user.type(input, "未提交草稿")
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.getByLabelText("姓名")).toBe(input))
  expect(input).toHaveValue("未提交草稿")
})

it("routes explicit management creation without inferring confirmation from administrator grants", async () => {
  const calls = install({ context: { ...caps, can_direct_create: true } })
  mount("/people/assisted-create")
  expect(await screen.findByRole("heading", { name: "管理建档" })).toBeVisible()
  expect(await screen.findByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" })).not.toBeChecked()
  expect(screen.getByText(/不是普通待审核申请/)).toBeVisible()
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText("主要身份"), "student")
  await user.type(screen.getByLabelText("姓名"), "测试人员")
  await user.type(screen.getByLabelText("身份证"), "11010519491231002X")
  await user.type(screen.getByLabelText("手机号"), "13800138000")
  await user.type(screen.getByLabelText("邮箱"), "person@example.com")
  await user.selectOptions(screen.getByLabelText("性别"), "female")
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "1949-12-31" } })
  await user.click(screen.getByLabelText("负责教练"))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(await screen.findByText("请先确认管理建档操作。")).toBeVisible()
  expect(calls.some((url) => url.endsWith("/direct-create"))).toBe(false)
})

it("context errors hide new private entries but preserve unrelated legacy directory", async () => {
  install({ failContext: true, permissions: ["people.list"] })
  const router = mount("/people")
  expect(await screen.findByRole("heading", { name: "人员中心" })).toBeVisible()
  await waitFor(() => expect(router.state.location.pathname).toBe("/people"))
  expect(screen.queryByRole("link", { name: "我的学员／队员" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "我的建档申请" })).not.toBeInTheDocument()
})
