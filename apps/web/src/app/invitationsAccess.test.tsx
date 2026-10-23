import { QueryClient, QueryClientProvider, focusManager, onlineManager } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { StrictMode } from "react"
import { afterEach, expect, it, vi } from "vitest"
import { AuthProvider, useAuth } from "../auth/AuthProvider"
import { appRoutes } from "./router"

const orgA = "e2900000-0000-4000-8000-000000000001"
const orgB = "e2900000-0000-4000-8000-000000000002"
const source = (id: string) => ({ source_kind: "organization", organization_id: id, name: id === orgA ? "组织甲" : "组织乙" })
const context = { coach_available: false, learning_available: false, payments_available: false, housing_management_available: false,
  housing_available: false, invitations_available: true, adult_guardian_invitations_available: false, food_profile_available: false,
  food_preferences_onboarding_required: false, learning_mode: null }
function RefreshAccount() { const auth = useAuth(); return <button onClick={() => void auth.refreshUser({ background: true })}>刷新测试账号</button> }
function setup(strict = false, available = true) {
  let account = "invitation-account"
  let housingPending: Promise<Response> | undefined
  let resolveHousing!: (response: Response) => void
  let failNetwork = false
  let housingStatus = 200
  let total = 1
  let sources = [source(orgA), source(orgB)]
  let pending: Promise<Response> | undefined
  let resolve!: (value: Response) => void
  const requests: string[] = []
  const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } })
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = new URL(String(input), "http://localhost"); requests.push(url.pathname + url.search)
    const authUser = { account_id: account, person_id: "visitor", full_name: "负责人", email: null, force_password_change: false, permissions: [], scoped_permissions: [] }
    if (url.pathname.endsWith("/auth/me")) return json(authUser)
    if (url.pathname.endsWith("/auth/login")) return json({ user: authUser, csrf_token: "test-login-csrf" })
    if (url.pathname.endsWith("/auth/logout")) return new Response(null, { status: 204 })
    if (url.pathname.endsWith("/auth/csrf")) return json({ csrf_token: "test-csrf" })
    if (url.pathname.endsWith("/portal/context")) return json({ ...context, invitations_available: available })
    if (url.pathname.endsWith("/team-management/context")) return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (url.pathname.endsWith("/referrals/me/sources")) { if (failNetwork) throw new TypeError("Failed to fetch"); return pending ?? json({ sources }) }
    if (url.pathname.endsWith("/referrals/me/housing")) {
      if (housingStatus !== 200) return json({}, housingStatus)
      if (url.searchParams.get("organization_id") === orgB && housingPending) return housingPending
      const page = Number(url.searchParams.get("page") ?? 1)
      return json({ as_of: "2026-10-11T00:00:00Z", page, page_size: 20, total, items: page > Math.ceil(total / 20) ? [] : [{ person_id: "e2000000-0000-4000-8000-000000000010", full_name: page === 2 ? "受邀甲第二页" : account === "other-account" ? "另一账号受邀人" : url.searchParams.get("organization_id") === orgB ? "受邀乙" : "受邀甲", nickname: null, source: source(orgA), housing: { status: "unarranged", stay: null, next_planned_stay: null } }] })
    }
    throw new Error(`Unexpected request ${url}`)
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const router = createMemoryRouter(appRoutes, { initialEntries: ["/me/invitations"] })
  const app = <QueryClientProvider client={client}><AuthProvider><RefreshAccount /><RouterProvider router={router} /></AuthProvider></QueryClientProvider>
  render(strict ? <StrictMode>{app}</StrictMode> : app)
  return { client, router, requests, hold: () => { pending = new Promise<Response>((r) => { resolve = r }) }, revokeB: () => { sources = [source(orgA)]; resolve(json({ sources })); pending = undefined }, fail: (status: number) => resolve(json({}, status)), offline: () => { failNetwork = true },
    switchAccount: () => { account = "other-account" }, changeTotal: (value: number) => { total = value }, failHousing: (status: number) => { housingStatus = status }, holdHousing: () => { housingPending = new Promise<Response>((r) => { resolveHousing = r }) },
    releaseHousing: () => { housingPending = undefined; resolveHousing(json({ as_of: "2026-10-11T00:00:00Z", page: 1, page_size: 20, total: 1, items: [{ person_id: "e2000000-0000-4000-8000-000000000010", full_name: "过期受邀人", nickname: null, source: source(orgA), housing: { status: "unarranged", stay: null, next_planned_stay: null } }] })) } }
}
afterEach(() => { focusManager.setFocused(undefined); onlineManager.setOnline(true); vi.unstubAllGlobals(); vi.useRealTimers() })
it("opens the real invitation route without financial permissions and hides private rows during independent source revalidation", async () => {
  const route = setup()
  expect(await screen.findByText("受邀甲")).toBeVisible()
  await userEvent.setup().selectOptions(screen.getByLabelText("邀请来源"), orgB)
  expect(await screen.findByText("受邀乙")).toBeVisible()
  route.hold()
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.queryByText("受邀乙")).not.toBeInTheDocument())
  expect(screen.queryByLabelText("邀请来源")).not.toBeInTheDocument()
  await act(async () => route.revokeB())
  expect(await screen.findByText("受邀甲")).toBeVisible()
  expect(screen.queryByRole("option", { name: "组织乙" })).not.toBeInTheDocument()
  expect(screen.getByLabelText("邀请来源")).toHaveValue("all")
  const requestsBeforeOldParameter = route.requests.length
  await act(async () => { await route.router.navigate(`/me/invitations?organization_id=${orgB}`) })
  expect(screen.getByLabelText("邀请来源")).toHaveValue("all")
  expect(route.requests.slice(requestsBeforeOldParameter).some((path) => path.includes(`organization_id=${orgB}`))).toBe(false)
  expect(route.requests.some((path) => /revenue-share|payments/.test(path))).toBe(false)
})
it("does not expose an invitation route or menu when capability is unavailable", async () => {
  const route = setup(false, false)
  await waitFor(() => expect(route.router.state.location.pathname).toBe("/"))
  expect(screen.queryByRole("heading", { name: "我的邀请" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "我的邀请" })).not.toBeInTheDocument()
  expect(route.requests.some((path) => path.includes("/referrals/"))).toBe(false)
})
it("loads private invitations under the application's StrictMode mount lifecycle", async () => {
  setup(true)
  expect(await screen.findByText("受邀甲")).toBeVisible()
})
it("can return to page one after periodic authorization reduces the permitted total below the current page", async () => {
  const route = setup(); route.changeTotal(21); const user = userEvent.setup()
  await screen.findByText("受邀甲")
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByText("受邀甲第二页")).toBeVisible()
  route.changeTotal(1)
  await user.click(screen.getByRole("button", { name: "重新验证授权" }))
  await user.click(await screen.findByRole("button", { name: "返回第一页" }))
  expect(await screen.findByText("受邀甲")).toBeVisible()
  expect(screen.getByText("第 1 / 1 页")).toBeVisible()
})
it("hides old rows after a disconnected source refresh", async () => {
  const route = setup(); await screen.findByText("受邀甲"); route.offline()
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByRole("alert")).toHaveTextContent("旧信息已隐藏")
  expect(screen.queryByText("受邀甲")).not.toBeInTheDocument()
})
it("revalidates sources within thirty seconds while global portal capability remains true", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] })
  focusManager.setFocused(true)
  const route = setup(); await screen.findByText("受邀甲"); route.hold()
  await act(async () => { await vi.advanceTimersByTimeAsync(30000) })
  await waitFor(() => expect(screen.queryByText("受邀甲")).not.toBeInTheDocument())
  expect(screen.queryByLabelText("邀请来源")).not.toBeInTheDocument()
  expect(route.requests.filter((path) => path.endsWith("/referrals/me/sources")).length).toBeGreaterThan(1)
  await act(async () => route.revokeB())
})
it("closes paused offline authorization refresh and requires fresh successful source validation after reconnect", async () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] })
  focusManager.setFocused(true)
  const route = setup()
  expect(await screen.findByText("受邀甲")).toBeVisible()
  route.hold()
  const requestsBeforeOffline = route.requests.filter((path) => path.endsWith("/referrals/me/sources")).length
  await act(async () => {
    onlineManager.setOnline(false)
    await vi.advanceTimersByTimeAsync(30000)
  })
  await waitFor(() => expect(screen.queryByText("受邀甲")).not.toBeInTheDocument())
  expect(screen.queryByLabelText("邀请来源")).not.toBeInTheDocument()
  expect(screen.queryByText("共 1 人")).not.toBeInTheDocument()
  await waitFor(() => expect(route.client.getQueryCache().findAll({ queryKey: ["portal", "invitation-account", "invitations", "housing"] })).toHaveLength(0))
  await act(async () => onlineManager.setOnline(true))
  await waitFor(() => expect(route.requests.filter((path) => path.endsWith("/referrals/me/sources")).length).toBeGreaterThan(requestsBeforeOffline))
  expect(screen.queryByText("受邀甲")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("邀请来源")).not.toBeInTheDocument()
  await act(async () => route.revokeB())
  expect(await screen.findByText("受邀甲")).toBeVisible()
  expect(screen.queryByRole("option", { name: "组织乙" })).not.toBeInTheDocument()
})
it("does not restore late housing responses when switching authorized sources", async () => {
  const route = setup(); await screen.findByText("受邀甲"); route.holdHousing()
  const user = userEvent.setup(); await user.selectOptions(screen.getByLabelText("邀请来源"), orgB)
  expect(screen.queryByRole("navigation", { name: "邀请分页" })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("邀请来源"), orgA)
  await act(async () => route.releaseHousing())
  // The held B request is aborted; current A has already completed fresh data.
  expect(await screen.findByText("受邀甲")).toBeVisible()
  expect(screen.queryByText("过期受邀人")).not.toBeInTheDocument()
})
it.each([500, 403, 404])("hides old source choices as well as rows after a housing request fails with %s", async (status) => {
  const route = setup(); await screen.findByText("受邀甲"); route.failHousing(status)
  await userEvent.setup().selectOptions(screen.getByLabelText("邀请来源"), orgB)
  await screen.findByRole("alert")
  await waitFor(() => expect(screen.queryByLabelText("邀请来源")).not.toBeInTheDocument())
  expect(screen.queryByText("受邀甲")).not.toBeInTheDocument()
})
it("isolates the real AuthProvider account transition and purges the previous invitation tree", async () => {
  const route = setup(); await screen.findByText("受邀甲"); route.switchAccount()
  await userEvent.setup().click(screen.getByRole("button", { name: "刷新测试账号" }))
  expect(await screen.findByText("另一账号受邀人")).toBeVisible()
  expect(screen.queryByText("受邀甲")).not.toBeInTheDocument()
  expect(route.client.getQueryCache().findAll({ queryKey: ["portal", "invitation-account", "invitations"] })).toHaveLength(0)
})
it("does not retain invitation filters or cached people after same-account logout and login", async () => {
  const route = setup(); const user = userEvent.setup(); await screen.findByText("受邀甲")
  await user.selectOptions(screen.getByLabelText("邀请来源"), orgB); await screen.findByText("受邀乙")
  await user.click(screen.getByRole("button", { name: "退出登录" }))
  expect(await screen.findByRole("heading", { name: "登录工作台" })).toBeVisible()
  expect(route.client.getQueryCache().findAll({ queryKey: ["portal", "invitation-account", "invitations"] })).toHaveLength(0)
  await user.type(screen.getByLabelText("手机号或邮箱"), "visitor@example.test"); await user.type(screen.getByLabelText("密码"), "TestPassword123")
  await user.click(screen.getByRole("button", { name: "登录" })); await screen.findByRole("link", { name: "查看我的邀请" })
  await user.click(screen.getByRole("link", { name: "查看我的邀请" }))
  expect(await screen.findByText("受邀甲")).toBeVisible(); expect(screen.getByLabelText("邀请来源")).toHaveValue("all")
})
it.each([500, 403, 404])("does not retain private rows after a source revalidation %s", async (status) => {
  const route = setup(); await screen.findByText("受邀甲"); route.hold()
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await act(async () => route.fail(status))
  await waitFor(() => expect(screen.queryByText("受邀甲")).not.toBeInTheDocument())
  expect(screen.queryByLabelText("邀请来源")).not.toBeInTheDocument()
  await waitFor(() => expect(route.client.getQueryCache().findAll({ queryKey: ["portal", "invitation-account", "invitations"] }).every((query) => query.state.data === undefined)).toBe(true))
})
