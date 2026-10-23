import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { TeamManagementPage } from "./TeamManagementPage"

const api = vi.hoisted(() => ({
  getTeamContext: vi.fn(), listTeams: vi.fn(), listMemberships: vi.fn(), candidates: vi.fn(),
  managerCandidates: vi.fn(), myTeam: vi.fn(), listAssignments: vi.fn(),
  join: vi.fn(), leave: vi.fn(), transfer: vi.fn(), assign: vi.fn(), unassign: vi.fn(),
}))
const auth = vi.hoisted(() => ({ isSuperAdmin: false }))
vi.mock("./api", () => api)
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ user: { account_id: "account-a", is_super_admin: auth.isSuperAdmin } }) }))

const team = { id: "team-a", project_id: "division-a", project_name: "王者荣耀", name: "一队", is_active: true, member_count: 0, allowed_actions: ["read", "manage"] }
const member = { id: "membership-a", person_id: "person-a", full_name: "王明", nickname: "小王", age: 16, is_minor: true, role: "team_member", project_id: "division-a", project_name: "王者荣耀", team_id: "team-a", team_name: "一队", status: "current", joined_on: "2026-09-29", left_on: null, end_kind: null, account_status: "active", transfer_reason: null }

function page<T>(items: T[], pageNumber = 1) { return { items, page: pageNumber, page_size: 20, total: items.length } }
function renderPage(accountId = "account-a") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return { ...render(<QueryClientProvider client={client}><TeamManagementPage accountId={accountId} /></QueryClientProvider>), client }
}

it("labels unknown roster age rather than rendering bare years or adult status", async () => {
  api.listMemberships.mockResolvedValue(page([{ ...member, age: null, is_minor: null }]))
  renderPage()
  await userEvent.click(await screen.findByRole("button", { name: "查看 一队" }))
  const roster = within(screen.getByRole("region", { name: "一队 名单" }))
  expect(await roster.findByText(/小王 · 年龄待补/)).toBeVisible()
  expect(roster.queryByText(/成年|null岁|· 岁/)).not.toBeInTheDocument()
})

beforeEach(() => {
  vi.resetAllMocks()
  auth.isSuperAdmin = false
  api.getTeamContext.mockResolvedValue({ can_open_management: true, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  api.listTeams.mockResolvedValue(page([team]))
  api.listMemberships.mockResolvedValue(page([]))
  api.candidates.mockResolvedValue({ items: [
    { account_id: "account-1", person_id: "person-a", full_name: "王明", nickname: "小王", age: 16, is_minor: true },
    { account_id: "account-2", person_id: "person-b", full_name: "王明", nickname: "阿明", age: 19, is_minor: false },
  ], next_cursor: null })
  api.listAssignments.mockResolvedValue(page([]))
  api.managerCandidates.mockResolvedValue({ items: [], next_cursor: null })
  api.join.mockResolvedValue({ operation_id: "operation-a", membership_id: "membership-a", closed_membership_id: null, assignment_id: null })
})

it.each([403, 404, "reconcile"])("clears all denied team data without reload after mutation %s", async (status) => {
  const user = userEvent.setup()
  const denied = new ProblemDetailsError({ type: "about:blank", title: "Denied", status: typeof status === "number" ? status : 404, detail: "无权", code: "team_not_found", request_id: "denied" })
  api.listTeams.mockResolvedValue(page([team, { ...team, id: "team-b", name: "二队" }]))
  api.listMemberships.mockResolvedValueOnce(page([member]))
  api.leave.mockRejectedValue(status === "reconcile" ? new Error("network") : denied)
  if (status === "reconcile") api.listMemberships.mockRejectedValue(denied)
  const { client } = renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  await user.click(await screen.findByRole("button", { name: "移出 王明" }))
  client.setQueryData(["teams", "account-a", "candidates", "team-a", "王", null], { private: true })
  client.setQueryData(["teams", "account-a", "members", "team-b", {}], page([]))
  await user.click(screen.getByRole("button", { name: "确认移出" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByText("王明")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "查看 一队" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "查看 二队" })).toBeInTheDocument()
  expect(client.getQueryCache().findAll({ queryKey: ["teams", "account-a"] }).filter((query) => query.queryKey[3] === "team-a")).toHaveLength(0)
  expect(client.getQueryData(["teams", "account-a", "members", "team-b", {}])).toBeDefined()
})

it("shows account and lifecycle labels including disabled current members without deleted links", async () => {
  const user = userEvent.setup()
  api.listMemberships.mockResolvedValue(page([
    { ...member, account_status: "disabled" },
    ...["left", "transferred", "account_deleted"].map((kind) => ({ ...member, id: kind, full_name: kind, status: "left", left_on: "2026-09-29", end_kind: kind, account_status: kind === "account_deleted" ? "deleted" : "active" })),
  ]))
  renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  const roster = screen.getByRole("region", { name: "一队 名单" })
  for (const label of ["账号已停用", "账号已删除", "账号正常", "在队", "已离队", "离队", "调队离开", "账号删除", "未成年", "队员"]) {
    expect(within(roster).getAllByText(label).length).toBeGreaterThan(0)
  }
  expect(within(roster).queryByRole("link")).not.toBeInTheDocument()
  await user.click(within(roster).getByRole("button", { name: "移出 王明" }))
  expect(screen.getByRole("dialog")).toBeInTheDocument()
})

it("shows server-counted accountless roster members without granting read-only managers extra actions", async () => {
  api.listTeams.mockResolvedValue(page([{ ...team, member_count: 1, allowed_actions: ["read"] }]))
  api.listMemberships.mockResolvedValue(page([{ ...member, account_status: "not_activated" }]))
  renderPage()
  await userEvent.click(await screen.findByRole("button", { name: "查看 一队" }))
  const roster = within(screen.getByRole("region", { name: "一队 名单" }))
  expect(await roster.findByText("未激活")).toBeVisible()
  expect(roster.getByText("授权在队人数 1")).toBeVisible()
  expect(roster.getAllByText("王明")).toHaveLength(1)
  expect(roster.queryByRole("button", { name: /添加队员|移出|调队|分配管理/ })).not.toBeInTheDocument()
  expect(roster.queryByText(/手机号|身份证|邮箱/)).not.toBeInTheDocument()
})

it("contains and restores keyboard focus for leave dialogs", async () => {
  const user = userEvent.setup()
  api.listMemberships.mockResolvedValue(page([member]))
  renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  const trigger = await screen.findByRole("button", { name: "移出 王明" })
  await user.click(trigger)
  const dialog = screen.getByRole("dialog")
  expect(dialog).toContainElement(document.activeElement as HTMLElement)
  for (let index = 0; index < 8; index += 1) {
    await user.tab()
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
  }
  await user.keyboard("{Escape}")
  expect(trigger).toHaveFocus()
})

it("test_lost_response_retries_same_operation", async () => {
  const user = userEvent.setup()
  api.join.mockRejectedValueOnce(new Error("response lost"))
  api.listMemberships.mockResolvedValueOnce(page([])).mockResolvedValueOnce(page([])).mockResolvedValue(page([member]))
  renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  await user.click(screen.getByRole("button", { name: "添加队员" }))
  await user.type(screen.getByRole("textbox", { name: "搜索队员" }), "王")
  expect(await screen.findByText("小王 · 16岁")).toBeInTheDocument()
  expect(screen.getByText("阿明 · 19岁")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "选择 王明 小王" }))
  await user.click(screen.getByRole("button", { name: "确认添加" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(screen.getByRole("textbox", { name: "搜索队员" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "选择 王明 小王" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "用原操作键重试" }))
  await waitFor(() => expect(api.join).toHaveBeenCalledTimes(2))
  const first = api.join.mock.calls[0][1]
  const second = api.join.mock.calls[1][1]
  expect(second.idempotency_key).toBe(first.idempotency_key)
  expect(second.person_id).toBe(first.person_id)
  expect(screen.queryByRole("dialog", { name: "添加队员" })).not.toBeInTheDocument()
  expect(await screen.findAllByText("王明")).toHaveLength(1)
})

it("test_only_eligible_actions_and_minimal_fields_render", async () => {
  const user = userEvent.setup()
  api.listMemberships.mockResolvedValue(page([member]))
  renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  expect(screen.getByText("王明")).toBeInTheDocument()
  expect(screen.getByText(/小王 · 16岁/)).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /调队|分配管理|编辑目录/ })).not.toBeInTheDocument()
  expect(screen.queryByText(/手机号|邮箱|身份证/)).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "移出 王明" }))
  expect(within(screen.getByRole("dialog", { name: "移出队员" })).getByText("仅结束队伍归属，不删除账号、学员身份或历史")).toBeInTheDocument()
})

it("lets a pure superadmin see only resource-authorized actions and a capable catalog link", async () => {
  const user = userEvent.setup()
  auth.isSuperAdmin = true
  api.getTeamContext.mockResolvedValue({ can_open_management: true, can_read_self: false, can_manage_catalog: true, can_assign_managers: true, can_transfer: true })
  api.listTeams.mockResolvedValue(page([{ ...team, member_count: 1, allowed_actions: ["read", "manage", "transfer", "assign_managers"] }]))
  api.listMemberships.mockResolvedValue(page([member]))
  renderPage()
  expect(await screen.findByRole("link", { name: "编辑目录" })).toHaveAttribute("href", "/teaching/catalog")
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  expect(await screen.findByRole("button", { name: "添加队员" })).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "调队 王明" })).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "分配管理人员" })).toBeInTheDocument()
})

it("does not turn global superadmin context into actions absent from this team's capability list", async () => {
  const user = userEvent.setup()
  auth.isSuperAdmin = true
  api.getTeamContext.mockResolvedValue({ can_open_management: true, can_read_self: false, can_manage_catalog: false, can_assign_managers: true, can_transfer: true })
  api.listTeams.mockResolvedValue(page([{ ...team, member_count: 1, allowed_actions: ["read"] }]))
  api.listMemberships.mockResolvedValue(page([member]))
  renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  expect(await screen.findByText("王明")).toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "编辑目录" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /添加队员|移出 王明|调队 王明|分配管理人员/ })).not.toBeInTheDocument()
})

it("retains candidate and explains a relationship conflict without creating a new key", async () => {
  const user = userEvent.setup()
  api.candidates.mockResolvedValueOnce({ items: [{ account_id: "account-1", person_id: "person-a", full_name: "王明", nickname: "小王", age: 16, is_minor: true }], next_cursor: null })
    .mockResolvedValue({ items: [], next_cursor: null })
  api.join.mockRejectedValueOnce(new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409, detail: "已在其他队伍", code: "team_already_assigned", request_id: "request-a" }))
  renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  await user.click(screen.getByRole("button", { name: "添加队员" }))
  await user.type(screen.getByRole("textbox", { name: "搜索队员" }), "王")
  await user.click(await screen.findByRole("button", { name: "选择 王明 小王" }))
  await user.click(screen.getByRole("button", { name: "确认添加" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("已在其他队伍")
  expect(screen.getByRole("dialog", { name: "添加队员" })).toBeInTheDocument()
  expect(screen.getByText("已选：王明 · 小王 · 16岁")).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "用原操作键重试" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "确认添加" })).toBeDisabled()
  await waitFor(() => expect(api.listMemberships.mock.calls.length).toBeGreaterThan(1))
})

it("does not render a previous account's delayed roster in the next account", async () => {
  let resolveOld: ((value: ReturnType<typeof page<typeof team>>) => void) | undefined
  api.listTeams.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve }))
  api.getTeamContext.mockResolvedValueOnce({ can_open_management: true, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    .mockResolvedValueOnce({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><TeamManagementPage accountId="account-a" /></QueryClientProvider>)
  await waitFor(() => expect(api.listTeams).toHaveBeenCalledTimes(1))
  view.rerender(<QueryClientProvider client={client}><TeamManagementPage accountId="account-b" /></QueryClientProvider>)
  expect(await screen.findByText("无查看权限")).toBeInTheDocument()
  resolveOld?.(page([team]))
  await waitFor(() => expect(client.getQueryData(["teams", "account-a", "all-authorized"])).toBeDefined())
  expect(screen.queryByText("一队")).not.toBeInTheDocument()
  expect(client.getQueryData(["teams", "account-b", "all-authorized"])).toBeUndefined()
})

it("test_revocation_and_account_switch_clear_team_data", async () => {
  const user = userEvent.setup()
  const denied = new ProblemDetailsError({ type: "about:blank", title: "Forbidden", status: 403, detail: "名单权限已撤销", code: "forbidden", request_id: "request-revoked" })
  const otherTeam = { ...team, id: "team-b", name: "二队", allowed_actions: ["read"] }
  api.listTeams.mockResolvedValue(page([team, otherTeam]))
  api.listMemberships.mockResolvedValueOnce(page([member])).mockRejectedValueOnce(denied).mockResolvedValue(page([]))
  const { client } = renderPage()
  await user.click(await screen.findByRole("button", { name: "查看 一队" }))
  expect(await screen.findByText("王明")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "移出 王明" }))
  expect(screen.getByRole("dialog", { name: "移出队员" })).toBeInTheDocument()
  await client.invalidateQueries({ queryKey: ["teams", "account-a", "members", "team-a"] })
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "移出队员" })).not.toBeInTheDocument())
  expect(screen.queryByText("王明")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "查看 二队" })).toBeInTheDocument()
  expect(client.getQueryData(["teams", "account-a", "members", "team-a", { status: "current", page: 1, pageSize: 20 }])).toBeUndefined()
})

it("drops a stale authorized-team directory after its resource returns 404", async () => {
  const denied = new ProblemDetailsError({ type: "about:blank", title: "Not found", status: 404, detail: "队伍目录不可读", code: "not_found", request_id: "request-list" })
  api.listTeams.mockResolvedValueOnce(page([team])).mockRejectedValueOnce(denied)
  const { client } = renderPage()
  expect(await screen.findByRole("button", { name: "查看 一队" })).toBeInTheDocument()
  await client.invalidateQueries({ queryKey: ["teams", "account-a", "all-authorized"] })
  await waitFor(() => expect(screen.queryByRole("button", { name: "查看 一队" })).not.toBeInTheDocument())
  expect(client.getQueryData(["teams", "account-a", "all-authorized"])).toBeUndefined()
})
