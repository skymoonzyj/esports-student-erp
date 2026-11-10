import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"
import { PersonAccountRolesPanel } from "./PersonAccountRolesPanel"

const auth = vi.hoisted(() => ({ user: { account_id: "admin", is_super_admin: false, permissions: ["permissions.manage"], scoped_permissions: [] as string[] }, refreshUser: vi.fn(async () => undefined) }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", ...auth }) }))
afterEach(() => { vi.unstubAllGlobals(); auth.refreshUser.mockClear(); auth.user = { account_id: "admin", is_super_admin: false, permissions: ["permissions.manage"], scoped_permissions: [] } })
const roles = [{ id: "student-role", key: "student", name: "学员" }, { id: "admin-role", key: "admin", name: "管理员" }]
function json(value: unknown, status = 200) { return new Response(status === 204 ? null : JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } }) }
function mount(accountId: string | null = "target", canManageAccountRoles = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = (account = accountId, allowed = canManageAccountRoles) => <QueryClientProvider client={client}><PersonAccountRolesPanel personId="p1" accountId={account} canManageAccountRoles={allowed} accountStatus={account ? "active" : "not_activated"} /></QueryClientProvider>
  return { ...render(ui()), ui, client }
}

// Catches people.update being treated as RBAC authority or capability absence being ignored.
it.each(["capability", "permission", "unactivated"])("does not download or mutate roles with missing %s", async absent => {
  const requests: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => { requests.push(String(url)); return json([]) }))
  if (absent === "permission") auth.user.permissions = ["people.update"]
  mount(absent === "unactivated" ? null : "target", absent !== "capability")
  if (absent === "unactivated") expect(screen.getByText(/激活账号后/)).toBeVisible()
  expect(screen.queryByRole("button", { name: "分配账号角色" })).not.toBeInTheDocument()
  await act(async () => undefined)
  expect(requests).toEqual([])
})

// Catches automatic role grants and accidental endpoint/payload divergence from existing RBAC.
it("shows role labels and only grants/revokes after an explicit confirmation", async () => {
  const writes: Array<{ url: string; method: string; body?: unknown }> = []
  let assigned = [roles[0]]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).endsWith("/roles") && !init?.method) return json(roles)
    if (init?.method === "POST") { writes.push({ url: String(url), method: "POST", body: JSON.parse(String(init.body)) }); assigned = roles; return json(undefined, 204) }
    if (init?.method === "DELETE") { writes.push({ url: String(url), method: "DELETE" }); assigned = [roles[1]]; return json(undefined, 204) }
    return json({ account_id: "target", roles: assigned, permission_overrides: [], effective_permissions: [] })
  }))
  mount()
  expect(await screen.findByRole("button", { name: "移除 学员" })).toBeVisible()
  expect(writes).toEqual([])
  await userEvent.selectOptions(screen.getByLabelText("添加账号角色"), "admin-role")
  await userEvent.click(screen.getByRole("button", { name: "分配账号角色" }))
  expect(writes).toEqual([])
  expect(screen.getByRole("dialog")).toHaveTextContent("管理员")
  await userEvent.click(screen.getByRole("button", { name: "确认分配角色" }))
  expect(await screen.findByRole("status")).toHaveTextContent("账号角色已更新")
  expect(writes).toEqual([{ url: "/api/v1/accounts/target/roles", method: "POST", body: { role_id: "admin-role" } }])
  await userEvent.click(screen.getByRole("button", { name: "移除 学员" }))
  await userEvent.click(screen.getByRole("button", { name: "确认移除角色" }))
  await waitFor(() => expect(screen.queryByRole("button", { name: "移除 学员" })).not.toBeInTheDocument())
  expect(writes[1]).toEqual({ url: "/api/v1/accounts/target/roles/student-role", method: "DELETE" })
})

it("clears role data and pending confirmation when permission is revoked", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => json(String(url).endsWith("/roles") ? roles : { account_id: "target", roles: [roles[0]], permission_overrides: [], effective_permissions: [] })))
  const { rerender, ui } = mount()
  await userEvent.click(await screen.findByRole("button", { name: "移除 学员" }))
  expect(screen.getByRole("dialog")).toBeVisible()
  auth.user.permissions = ["people.update"]
  rerender(ui())
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByText("学员")).not.toBeInTheDocument()
})

it("blocks roles after a 403 instead of retaining assigned labels or permitting retry", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "DELETE") return json({ status: 403, detail: "无权限" }, 403)
    return json(String(url).endsWith("/roles") ? roles : { account_id: "target", roles: [roles[0]], permission_overrides: [], effective_permissions: [] })
  }))
  mount()
  await userEvent.click(await screen.findByRole("button", { name: "移除 学员" }))
  await userEvent.click(screen.getByRole("button", { name: "确认移除角色" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("权限已失效")
  expect(screen.queryByRole("button", { name: "移除 学员" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("添加账号角色")).not.toBeInTheDocument()
})

it("ignores a role write completing after the target account changes", async () => {
  let finish!: (value: Response) => void
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "DELETE") return new Promise<Response>(resolve => { finish = resolve })
    return json(String(url).endsWith("/roles") ? roles : { account_id: String(url).includes("other") ? "other" : "target", roles: [roles[0]], permission_overrides: [], effective_permissions: [] })
  }))
  const { rerender, ui } = mount()
  await userEvent.click(await screen.findByRole("button", { name: "移除 学员" }))
  await userEvent.click(screen.getByRole("button", { name: "确认移除角色" }))
  rerender(ui("other"))
  await screen.findByRole("button", { name: "移除 学员" })
  await act(async () => finish(json(undefined, 204)))
  expect(screen.queryByText("账号角色已更新")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "移除 学员" })).toBeEnabled()
})

// Catches declaring an inline confirmation modal without providing modal focus behavior.
it("keeps inline role confirmation nonmodal and the background role selector usable", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => json(String(url).endsWith("/roles") ? roles : { account_id: "target", roles: [roles[0]], permission_overrides: [], effective_permissions: [] })))
  mount()
  await userEvent.click(await screen.findByRole("button", { name: "移除 学员" }))
  expect(screen.getByRole("dialog", { name: "确认移除账号角色" })).not.toHaveAttribute("aria-modal", "true")
  const selector = screen.getByRole("combobox", { name: "添加账号角色" })
  expect(selector).toBeEnabled()
  await userEvent.selectOptions(selector, "admin-role")
  expect(selector).toHaveValue("admin-role")
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "分配账号角色" })).toBeEnabled()
})

const jobRoles = [
  { id: "coach-role", key: "coach", name: "教练" },
  { id: "finance-role", key: "finance", name: "财务" },
  { id: "service-role", key: "customer_service", name: "客服" },
  ...roles,
]

it("offers team manager and assistant coach jobs while requiring explicit concurrent retention", async () => {
  const extra = [{ id: "manager-role", key: "team_manager", name: "战队经理" }, { id: "assistant-role", key: "assistant_coach", name: "助教" }]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => json(String(url).endsWith("/roles") ? [...jobRoles, ...extra] : { account_id: "target", roles: [extra[1], roles[0]], permission_overrides: [], effective_permissions: [] })))
  mount()
  const original = await screen.findByRole("combobox", { name: "原岗位" })
  expect(original).toHaveTextContent("助教")
  await userEvent.selectOptions(original, "assistant-role")
  await userEvent.selectOptions(screen.getByLabelText("新岗位"), "manager-role")
  expect(screen.getByRole("button", { name: "预览岗位变更" })).toBeDisabled()
  await userEvent.click(screen.getByRole("radio", { name: "是，保留其他兼任岗位" }))
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  expect(screen.getByRole("dialog", { name: "确认岗位变更" })).toHaveTextContent("将新增：战队经理")
  expect(screen.getByRole("dialog")).toHaveTextContent("将撤销：助教")
  expect(screen.getByRole("dialog")).toHaveTextContent("将保留：学员")
})

it("offers property as a staff job and previews an explicit role replacement", async () => {
  const property = { id: "property-role", key: "property", name: "物业" }
  const writes: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") { writes.push(JSON.parse(String(init.body))); return json({}, 409) }
    return json(String(url).endsWith("/roles") ? [...jobRoles, property] : {
      account_id: "target", roles: [jobRoles[0], roles[0]], permission_overrides: [], effective_permissions: [],
    })
  }))
  mount()
  const original = await screen.findByRole("combobox", { name: "原岗位" })
  await userEvent.selectOptions(original, "coach-role")
  const next = screen.getByRole("combobox", { name: "新岗位" })
  expect(next).toHaveTextContent("物业")
  await userEvent.selectOptions(next, "property-role")
  await userEvent.click(screen.getByRole("radio", { name: "否，撤销其他工作人员岗位" }))
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  const preview = screen.getByRole("dialog", { name: "确认岗位变更" })
  expect(preview).toHaveTextContent("将新增：物业")
  expect(preview).toHaveTextContent("将撤销：教练")
  expect(preview).toHaveTextContent("将保留：学员")
  expect(writes).toEqual([])
})

function jobServer(options: { status?: number; accountId?: string; hold?: boolean; noStaff?: boolean } = {}) {
  const writes: unknown[] = []
  let reads = 0
  let assigned = options.noStaff ? [roles[0]] : [jobRoles[0], jobRoles[1], roles[0]]
  let finish!: () => void
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") {
      const input = JSON.parse(String(init.body)); writes.push({ url: String(url), input })
      if (options.hold) await new Promise<void>(resolve => { finish = resolve })
      if (options.status) return json({ status: options.status, detail: "岗位更新失败" }, options.status)
      assigned = input.keep_concurrent_roles ? [jobRoles[1], jobRoles[2], roles[0]] : [jobRoles[2], roles[0]]
      return json({ account_id: options.accountId ?? "target", added_role_ids: ["service-role"], removed_role_ids: ["coach-role"], retained_role_ids: assigned.map(role => role.id), roles: assigned })
    }
    if (String(url).endsWith("/roles")) return json(jobRoles)
    reads += 1
    return json({ account_id: options.accountId ?? "target", roles: assigned, permission_overrides: [], effective_permissions: [] })
  }))
  return { writes, reads: () => reads, finish: () => finish() }
}
async function chooseJob(keep?: boolean) {
  await userEvent.selectOptions(await screen.findByRole("combobox", { name: "原岗位" }), "coach-role")
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "新岗位" }), "service-role")
  if (keep !== undefined) await userEvent.click(screen.getByRole("radio", { name: keep ? "是，保留其他兼任岗位" : "否，撤销其他工作人员岗位" }))
}

// Catches implicit retention defaults, partial grant/revoke requests, and identity removal in previews.
it.each([true, false])("atomically changes jobs with explicit concurrent retention=%s and full role snapshot", async keep => {
  const server = jobServer()
  const { client } = mount()
  client.setQueryData(["person", "p1"], { staff_type: "coach" })
  client.setQueryData(["people"], { items: [] })
  client.setQueryData(["person-assignments", "admin", "p1", "scope"], { version: "v1" })
  await chooseJob()
  expect(screen.getByRole("button", { name: "预览岗位变更" })).toBeDisabled()
  expect(server.writes).toEqual([])
  await userEvent.click(screen.getByRole("radio", { name: keep ? "是，保留其他兼任岗位" : "否，撤销其他工作人员岗位" }))
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  const dialog = screen.getByRole("dialog", { name: "确认岗位变更" })
  expect(dialog).toHaveTextContent(keep ? "将撤销：教练" : "将撤销：教练、财务")
  expect(dialog).toHaveTextContent(keep ? "将保留：财务、学员" : "将保留：学员")
  expect(dialog).toHaveTextContent("将新增：客服")
  expect(dialog).toHaveTextContent("单独 allow/deny 授权")
  expect(server.writes).toEqual([])
  await userEvent.click(screen.getByRole("button", { name: "确认岗位变更" }))
  expect(await screen.findByRole("status")).toHaveTextContent("岗位已更新")
  expect(server.writes).toEqual([{ url: "/api/v1/accounts/target/job-role-change", input: { from_role_id: "coach-role", to_role_id: "service-role", keep_concurrent_roles: keep, expected_role_ids: ["coach-role", "finance-role", "student-role"] } }])
  expect(client.getQueryState(["person", "p1"])?.isInvalidated).toBe(true)
  expect(client.getQueryState(["people"])?.isInvalidated).toBe(true)
  expect(client.getQueryState(["person-assignments", "admin", "p1", "scope"])?.isInvalidated).toBe(true)
  expect(screen.getByRole("button", { name: "移除 学员" })).toBeVisible()
})

it("supports a first staff job without including identity roles in job selectors", async () => {
  const server = jobServer({ noStaff: true })
  mount()
  const oldRole = await screen.findByRole("combobox", { name: "原岗位" })
  await userEvent.selectOptions(oldRole, "none")
  await userEvent.selectOptions(screen.getByRole("combobox", { name: "新岗位" }), "service-role")
  expect(screen.getByRole("combobox", { name: "新岗位" }).textContent).not.toContain("学员")
  await userEvent.click(screen.getByRole("radio", { name: "是，保留其他兼任岗位" }))
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  await userEvent.click(screen.getByRole("button", { name: "确认岗位变更" }))
  await screen.findByRole("status")
  expect(server.writes).toEqual([{ url: "/api/v1/accounts/target/job-role-change", input: { from_role_id: null, to_role_id: "service-role", keep_concurrent_roles: true, expected_role_ids: ["student-role"] } }])
})

it.each([409, 500])("clears failed job preview and rereads current roles before retry after %s", async status => {
  const server = jobServer({ status })
  mount()
  await chooseJob(true)
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  await userEvent.click(screen.getByRole("button", { name: "确认岗位变更" }))
  await waitFor(() => expect(server.reads()).toBe(2))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "预览岗位变更" })).toBeDisabled()
  expect(screen.getByRole("radio", { name: "是，保留其他兼任岗位" })).not.toBeChecked()
  expect(server.writes).toHaveLength(1)
})

it("blocks and clears a job preview after server permission revocation", async () => {
  const server = jobServer({ status: 403 })
  mount()
  await chooseJob(false)
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  await userEvent.click(screen.getByRole("button", { name: "确认岗位变更" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("权限已失效")
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByRole("combobox", { name: "原岗位" })).not.toBeInTheDocument()
  expect(server.writes).toHaveLength(1)
  expect(auth.refreshUser).toHaveBeenCalled()
})

it("freezes job selection and double submission and refreshes self authorization", async () => {
  const server = jobServer({ accountId: "admin", hold: true })
  mount("admin")
  await chooseJob(true)
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  await userEvent.dblClick(screen.getByRole("button", { name: "确认岗位变更" }))
  expect(screen.getByRole("combobox", { name: "原岗位" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "取消岗位操作" })).toBeDisabled()
  expect(server.writes).toHaveLength(1)
  await act(async () => server.finish())
  expect(await screen.findByRole("status")).toHaveTextContent("岗位已更新")
  expect(auth.refreshUser).toHaveBeenCalledWith({ background: true })
})

it("does not reuse stale roles when an uncertain write and its recovery read both fail", async () => {
  let reads = 0
  let writes = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") { writes += 1; throw new TypeError("network disconnected") }
    if (String(url).endsWith("/roles")) return json(jobRoles)
    reads += 1
    if (reads > 1) throw new TypeError("network disconnected")
    return json({ account_id: "target", roles: [jobRoles[0], jobRoles[1], roles[0]], permission_overrides: [], effective_permissions: [] })
  }))
  mount()
  await chooseJob(true)
  await userEvent.click(screen.getByRole("button", { name: "预览岗位变更" }))
  await userEvent.click(screen.getByRole("button", { name: "确认岗位变更" }))
  await waitFor(() => expect(screen.getByRole("combobox", { name: "原岗位" })).toBeDisabled())
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(writes).toBe(1)
  expect(screen.getByRole("button", { name: "重新读取账号角色" })).toBeEnabled()
})
