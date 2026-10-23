import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import { AuthProvider, useAuth } from "../auth/AuthProvider"
import { ManagedPeoplePage } from "./ManagedPeoplePage"

const account = "30000000-0000-4000-8000-000000000001"
const id = "30000000-0000-4000-8000-000000000002"
const context = { can_submit: false, can_review: false, can_direct_create: false, can_read_managed: true, allowed_team_ids: [id], eligible_coaches: [], eligible_teams: [{ id, label: "授权队伍" }] }
const stay = { building_name: "一号楼", floor_name: "二层", room_name: "201", duration_mode: "long_term", planned_check_in_at: "2026-10-17T16:00:00Z", planned_check_out_at: null, actual_check_in_at: "2026-10-17T18:00:00Z", actual_check_out_at: null }
const person = { person_id: id, full_name: "私有学员甲", nickname: "昵称甲", kinds: ["student", "player"], authorized_affiliations: [{ kind: "team", id, label: "授权队伍" }], housing: { status: "checked_in", stay, next_planned_stay: null } }
const page = { items: [person], total: 41, next_cursor: "next-secret" }
type Handler = (url: URL, init: RequestInit) => Response | Promise<Response>
function install(handler?: Handler) {
  const calls: Array<{ url: URL; init: RequestInit }> = []
  vi.stubGlobal("fetch", vi.fn(async (input, init: RequestInit = {}) => {
    const url = new URL(String(input), "https://erp.example")
    calls.push({ url, init })
    if (url.pathname.endsWith("/auth/me")) return Response.json({ account_id: account, person_id: account, full_name: "教练", email: null, force_password_change: false, permissions: [] })
    if (url.pathname.endsWith("/auth/csrf")) return Response.json({ csrf_token: "csrf" })
    if (handler) return handler(url, init)
    if (url.pathname.endsWith("/context")) return Response.json(context)
    return Response.json(page)
  }))
  return calls
}
function SwitchAccount() { const auth = useAuth(); return <button onClick={() => void auth.login("other", "password")}>切换账号</button> }
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><AuthProvider><SwitchAccount /><ManagedPeoplePage /></AuthProvider></QueryClientProvider>)
  return client
}
afterEach(() => { vi.unstubAllGlobals(); focusManager.setFocused(undefined) })

it("renders only readonly authorized cards with China dates, housing and server count", async () => {
  const calls = install()
  mount()
  expect(await screen.findByText("私有学员甲")).toBeVisible()
  expect(screen.getByText("昵称甲")).toBeVisible()
  expect(screen.getByText("学员、队员")).toBeVisible()
  expect(screen.getByText("共 41 人")).toBeVisible()
  expect(screen.getAllByText("2026-10-18")).toHaveLength(2)
  expect(screen.getByText("长期入住")).toBeVisible()
  expect(screen.getByText("已入住", { selector: "p" })).toBeVisible()
  expect(screen.getByText("一号楼 / 二层 / 201")).toBeVisible()
  expect(screen.queryByRole("link")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /入住|退房|导出|调房|编辑/ })).not.toBeInTheDocument()
  expect(calls.some(({ url }) => /\/people|\/team-management/.test(url.pathname))).toBe(false)
})

it("accumulates cursor pages but resets all pages and cursors for every filter change", async () => {
  const calls = install((url) => {
    if (url.pathname.endsWith("/context")) return Response.json(context)
    if (url.searchParams.get("cursor")) return Response.json({ items: [{ ...person, person_id: account, full_name: "第二页人员" }], total: 41, next_cursor: null })
    if (url.searchParams.has("query") || url.searchParams.has("kind") || url.searchParams.has("housing_status") || url.searchParams.has("team_id")) return Response.json({ items: [], total: 0, next_cursor: null })
    return Response.json(page)
  })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "加载更多" }))
  expect(await screen.findByText("第二页人员")).toBeVisible()
  expect(screen.getByText("私有学员甲")).toBeVisible()
  await user.type(screen.getByLabelText("姓名或昵称"), "查")
  await screen.findByText("没有符合条件的学员／队员。")
  expect(screen.queryByText("私有学员甲")).not.toBeInTheDocument()
  expect(screen.queryByText("第二页人员")).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("身份筛选"), "both")
  await user.selectOptions(screen.getByLabelText("住宿状态筛选"), "unarranged")
  await user.selectOptions(screen.getByLabelText("所属队伍筛选"), id)
  await waitFor(() => expect(calls.some(({ url }) => url.searchParams.get("team_id") === id)).toBe(true))
  const filtered = calls.filter(({ url }) => url.searchParams.has("query"))
  expect(filtered.length).toBeGreaterThan(0)
  expect(filtered.every(({ url }) => !url.searchParams.has("cursor"))).toBe(true)
})

it("revokes an owner-only capability on focus and excludes a delayed old private response", async () => {
  let revoked = false
  let release: (() => void) | undefined
  let signal: AbortSignal | undefined
  install((url, init) => {
    if (url.pathname.endsWith("/context")) return Response.json({ ...context, can_read_managed: !revoked })
    if (url.searchParams.has("cursor")) {
      signal = init.signal as AbortSignal
      return new Promise<Response>((resolve) => { release = () => resolve(Response.json({ ...page, items: [{ ...person, full_name: "迟到秘密" }] })) })
    }
    return Response.json(page)
  })
  const client = mount()
  const user = userEvent.setup()
  await screen.findByText("私有学员甲")
  await user.click(screen.getByRole("button", { name: "加载更多" }))
  revoked = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByText("无所属人员查看权限。")
  expect(screen.queryByText("私有学员甲")).not.toBeInTheDocument()
  expect(signal?.aborted).toBe(true)
  await act(async () => release?.())
  expect(screen.queryByText("迟到秘密")).not.toBeInTheDocument()
  expect(client.getQueryCache().getAll().some((entry) => entry.queryKey[2] === "managed")).toBe(false)
})

it("starts from the first page even when rapidly returning to an earlier filter before cache collection", async () => {
  let pageCalls = 0
  let resetSignal: AbortSignal | null | undefined
  const calls = install((url, init) => {
    if (url.pathname.endsWith("/context")) return Response.json(context)
    pageCalls += 1
    if (pageCalls === 1) return Response.json(page)
    if (url.searchParams.has("cursor")) return Response.json({ ...page, items: [{ ...person, person_id: account, full_name: "此前第二页" }], next_cursor: null })
    resetSignal = init.signal
    return new Promise<Response>(() => {})
  })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "加载更多" }))
  await screen.findByText("此前第二页")
  fireEvent.change(screen.getByLabelText("身份筛选"), { target: { value: "player" } })
  fireEvent.change(screen.getByLabelText("身份筛选"), { target: { value: "" } })
  expect(screen.queryByText("此前第二页")).not.toBeInTheDocument()
  expect(screen.queryByText("私有学员甲")).not.toBeInTheDocument()
  await waitFor(() => expect(pageCalls).toBe(4))
  expect(calls.at(-1)?.url.searchParams.has("cursor")).toBe(false)
  expect(resetSignal?.aborted).toBe(false)
})

it("clears old private records on account switch and ignores the prior account pending page", async () => {
  let switched = false
  let release: (() => void) | undefined
  install((url) => {
    if (url.pathname.endsWith("/auth/login")) {
      switched = true
      return Response.json({ user: { account_id: id, person_id: id, full_name: "新教练", email: null, force_password_change: false, permissions: [] }, csrf_token: "csrf" })
    }
    if (url.pathname.endsWith("/context")) return Response.json(context)
    if (!switched && url.searchParams.has("cursor")) return new Promise<Response>((resolve) => { release = () => resolve(Response.json({ ...page, items: [{ ...person, full_name: "旧账号迟到秘密" }] })) })
    return Response.json(switched ? { items: [], total: 0, next_cursor: null } : page)
  })
  mount()
  const user = userEvent.setup()
  await screen.findByText("私有学员甲")
  await user.click(screen.getByRole("button", { name: "加载更多" }))
  await user.click(screen.getByRole("button", { name: "切换账号" }))
  await screen.findByText("没有符合条件的学员／队员。")
  await act(async () => release?.())
  expect(screen.queryByText("私有学员甲")).not.toBeInTheDocument()
  expect(screen.queryByText("旧账号迟到秘密")).not.toBeInTheDocument()
})

it("renders unarranged and all supported housing statuses without operational controls", async () => {
  const statuses = ["unarranged", "reserved", "check_in_pending", "checked_in", "checkout_pending", "checked_out"]
  install((url) => url.pathname.endsWith("/context") ? Response.json(context) : Response.json({ items: statuses.map((status, index) => ({ ...person, person_id: `30000000-0000-4000-8000-00000000000${index + 3}`, housing: { status, stay: null, next_planned_stay: null } })), total: 6, next_cursor: null }))
  mount()
  await screen.findByText("共 6 人")
  for (const label of ["未安排住宿", "已预约", "待确认入住", "已入住", "待确认退房", "已退房"]) expect(screen.getByText(label, { selector: "p" })).toBeVisible()
  expect(screen.queryByRole("link")).not.toBeInTheDocument()
})
