import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { PersonStaffRow, TeamAction } from "./api"
import { TeamStaffPanel } from "./TeamStaffPanel"

const api = vi.hoisted(() => ({ staffCandidates: vi.fn(), listStaffPositions: vi.fn(), personStaffCandidates: vi.fn(), assignPersonStaff: vi.fn(), endPersonStaff: vi.fn(), reconfirmPersonStaff: vi.fn(), assignStaff: vi.fn(), endStaffAssignment: vi.fn() }))
vi.mock("./api", () => api)
const candidate = { account_id: "staff-a", person_id: "person-a", full_name: "张三", nickname: "小张", age: null, is_minor: null }
const assignment: PersonStaffRow = { row_key: "account:assignment-coach", source_kind: "account", person_position_id: null, account_assignment_id: "assignment-coach", person_id: "person-a", account_id: "staff-a", full_name: "张三", nickname: "小张", account_status: "active", authorization_status: "valid", version: null, allowed_actions: ["end"], started_on: "2026-11-06", ended_on: null, staff_role: "coach" }
const personPosition: PersonStaffRow = { ...assignment, row_key: "person:position-coach", source_kind: "person", person_position_id: "position-coach", account_assignment_id: null, account_id: null, account_status: "not_activated", version: 3, authorization_status: "reconfirmation_required", allowed_actions: ["end", "reconfirm"] }
const page = (items: PersonStaffRow[]) => ({ items, page: 1, page_size: 20, total: items.length })

function setup(initialActions: TeamAction[] = ["read", "assign_managers"]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const changed = vi.fn(async () => { await client.invalidateQueries({ queryKey: ["teams", "admin"] }) })
  const panel = (accountId = "admin", allowedActions: TeamAction[] = ["read", "assign_managers"], teamId = "team-a") => <QueryClientProvider client={client}><TeamStaffPanel accountId={accountId} teamId={teamId} teamIsActive allowedActions={allowedActions} onChanged={changed} /></QueryClientProvider>
  return { ...render(panel("admin", initialActions)), client, changed, panel }
}

beforeEach(() => {
  vi.resetAllMocks()
  api.listStaffPositions.mockResolvedValue(page([]))
  api.personStaffCandidates.mockResolvedValue({ items: [{ person_id: "person-pending", account_id: null, full_name: "待激活甲", nickname: "小甲" }], next_cursor: null })
  api.staffCandidates.mockImplementation(async (_teamId, options) => ({ items: [{ ...candidate, full_name: options.staffRole === "coach" ? "教练甲" : "助教乙" }], next_cursor: null }))
  api.assignStaff.mockResolvedValue({ operation_id: "operation-a", membership_id: null, closed_membership_id: null, assignment_id: "new-staff" })
  api.endStaffAssignment.mockResolvedValue({ operation_id: "operation-b", membership_id: null, closed_membership_id: null, assignment_id: assignment.account_assignment_id })
  api.assignPersonStaff.mockResolvedValue({ operation_id: "operation-c", membership_id: null, closed_membership_id: null, assignment_id: "position-new" })
  api.endPersonStaff.mockResolvedValue({ operation_id: "operation-d", membership_id: null, closed_membership_id: null, assignment_id: "position-coach" })
  api.reconfirmPersonStaff.mockResolvedValue({ operation_id: "operation-e", membership_id: null, closed_membership_id: null, assignment_id: "position-coach" })
})

const changedPositions: { label: string; patch: Partial<PersonStaffRow> }[] = [
  { label: "newer version", patch: { version: 4 } },
  { label: "revoked actions", patch: { allowed_actions: [] } },
  { label: "ended position", patch: { ended_on: "2026-11-07", allowed_actions: [] } },
]

it.each(changedPositions)("revokes an open personnel confirmation after an authoritative same-row $label", async ({ patch }) => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "解绑 张三 的教练岗位" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "过时原因")
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, ...patch }]))
  await act(async () => { await view.client.refetchQueries({ queryKey: ["teams", "admin", "staff-positions", "team-a"] }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.getByRole("status")).toHaveTextContent("岗位状态已更新")
  expect(api.endPersonStaff).not.toHaveBeenCalled()
  if (patch.version === 4) {
    await user.click(screen.getByRole("button", { name: "解绑 张三 的教练岗位" }))
    expect(screen.getByRole("textbox", { name: "操作原因" })).toHaveValue("")
    await user.type(screen.getByRole("textbox", { name: "操作原因" }), "核对最新岗位")
    await user.click(screen.getByRole("button", { name: "确认解绑" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
    expect(api.endPersonStaff.mock.calls[0]?.slice(0, 2)).toEqual(["position-coach", { expected_version: 4, reason: "核对最新岗位", idempotency_key: expect.any(String) }])
  }
})

it.each(changedPositions)("revokes a frozen retry without inferring the original result after same-row $label", async ({ patch }) => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  api.reconfirmPersonStaff.mockRejectedValue(new Error("response lost"))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "过时重试原因")
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  await screen.findByRole("button", { name: "用原操作键重试" })
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, ...patch }]))
  await act(async () => { await view.client.refetchQueries({ queryKey: ["teams", "admin", "staff-positions", "team-a"] }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByRole("button", { name: "用原操作键重试" })).not.toBeInTheDocument()
  expect(screen.getByRole("status")).toHaveTextContent("若已提交请求，不能据此判断原操作是否生效")
  expect(view.changed).not.toHaveBeenCalled()
  expect(api.reconfirmPersonStaff).toHaveBeenCalledTimes(1)
})

it("aborts and fences a stale in-flight result without closing a newly opened latest-version dialog", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  let resolve!: (value: { assignment_id: string }) => void
  api.reconfirmPersonStaff.mockImplementation(() => new Promise(done => { resolve = done }))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "旧版本请求")
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  const signal = api.reconfirmPersonStaff.mock.calls[0][2] as AbortSignal
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, version: 4 }]))
  await act(async () => { await view.client.refetchQueries({ queryKey: ["teams", "admin", "staff-positions", "team-a"] }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(signal.aborted).toBe(true)
  await user.click(screen.getByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "新版本草稿")
  await act(async () => { resolve({ assignment_id: "position-coach" }) })
  expect(view.changed).not.toHaveBeenCalled()
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(screen.getByRole("textbox", { name: "操作原因" })).toHaveValue("新版本草稿")
  expect(screen.getByRole("button", { name: "确认重新授权" })).toBeEnabled()
})

it.each(["page", "filter", "error", "compatible refresh"])("does not revoke an uncertain command merely because of list %s", async mode => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue({ ...page([personPosition]), total: 21 })
  api.endPersonStaff.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({ assignment_id: "position-coach" })
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "解绑 张三 的教练岗位" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "原操作原因")
  await user.click(screen.getByRole("button", { name: "确认解绑" }))
  await screen.findByRole("button", { name: "用原操作键重试" })
  if (mode === "error") {
    api.listStaffPositions.mockRejectedValue(new Error("temporary read failure"))
    await act(async () => { await view.client.refetchQueries({ queryKey: ["teams", "admin", "staff-positions", "team-a"] }) })
    await screen.findByText("暂时无法读取队内工作人员，请稍后重试。")
  } else if (mode === "page") {
    api.listStaffPositions.mockResolvedValue({ items: [], page: 2, page_size: 20, total: 21 })
    await user.click(screen.getByRole("button", { name: "下一页" }))
    await screen.findByText("第 2 页，共 21 条")
  } else if (mode === "filter") {
    api.listStaffPositions.mockResolvedValue(page([]))
    await user.selectOptions(screen.getByRole("combobox", { name: "岗位绑定状态" }), "ended")
    await screen.findByText("暂无已结束队内岗位绑定。")
  } else {
    api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, nickname: "更新昵称" }]))
    await act(async () => { await view.client.refetchQueries({ queryKey: ["teams", "admin", "staff-positions", "team-a"] }) })
    await screen.findByText(/更新昵称/)
  }
  expect(screen.getByRole("dialog")).toBeVisible()
  expect(screen.queryByText(/岗位状态已更新/)).not.toBeInTheDocument()
  api.listStaffPositions.mockResolvedValue(page([]))
  await user.click(screen.getByRole("button", { name: "用原操作键重试" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.endPersonStaff.mock.calls[1]?.slice(0, 2)).toEqual(api.endPersonStaff.mock.calls[0]?.slice(0, 2))
})

it("distinguishes active account from stale team authorization", async () => {
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, account_id: "staff-a", account_assignment_id: "realized-a", account_status: "active" }]))
  setup()
  expect(await screen.findByText("已激活")).toBeInTheDocument()
  expect(screen.getByText("授权待重新确认")).toBeInTheDocument()
  expect(screen.queryByText("未激活")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "重新确认授权" })).toBeEnabled()
  expect(screen.queryByText("授权正常")).not.toBeInTheDocument()
})

it("shows accountless name nickname and independent roles without exposing account actions", async () => {
  api.listStaffPositions.mockResolvedValue(page([personPosition, { ...personPosition, row_key: "person:position-assistant", person_position_id: "position-assistant", staff_role: "assistant_coach", allowed_actions: [] }]))
  setup()
  const list = await screen.findByRole("list", { name: "队内岗位绑定" })
  await within(list).findAllByText("张三")
  expect(within(list).getAllByText("未激活")).toHaveLength(2)
  expect(within(list).getAllByText(/小张/)).toHaveLength(2)
  expect(screen.getByText("第 1 页，共 2 条")).toBeVisible()
  expect(screen.queryByRole("button", { name: "解绑 张三 的助教岗位" })).not.toBeInTheDocument()
  expect(screen.queryByText(/手机号|身份证|邮箱|密码/)).not.toBeInTheDocument()
})

it("keeps the person row and count stable after realization and still uses versioned person maintenance", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  const view = setup()
  const before = (await screen.findByText("张三")).closest("li")
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, account_id: "staff-a", account_assignment_id: "realized-a", account_status: "active" }]))
  await view.client.refetchQueries({ queryKey: ["teams", "admin"] })
  expect((await screen.findByText("已激活")).closest("li")).toBe(before)
  expect(screen.getByText("第 1 页，共 1 条")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重新确认授权" }))
  expect(screen.getByRole("button", { name: "确认重新授权" })).toBeDisabled()
  expect(screen.getByRole("textbox", { name: "操作原因" })).toHaveAttribute("maxlength", "200")
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "重新核实该岗位")
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.reconfirmPersonStaff).toHaveBeenCalledWith("position-coach", { expected_version: 3, reason: "重新核实该岗位", idempotency_key: expect.any(String) }, expect.any(AbortSignal))
  expect(api.endStaffAssignment).not.toHaveBeenCalled()
})

it("retires an acknowledged reconfirmation before its real cache refresh advances the position version", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "重新核实岗位")
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, version: 4, authorization_status: "valid", allowed_actions: ["end"] }]))
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(await screen.findByText("授权已确认")).toBeVisible()
  expect(view.client.getQueryData(["teams", "admin", "staff-positions", "team-a", "current", 1, 20])).toEqual(page([{ ...personPosition, version: 4, authorization_status: "valid", allowed_actions: ["end"] }]))
  expect(screen.queryByText(/岗位状态已更新|不能据此判断原操作|结果未确认/)).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "解绑 张三 的教练岗位" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "结束最新岗位")
  api.listStaffPositions.mockResolvedValue(page([]))
  await user.click(screen.getByRole("button", { name: "确认解绑" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.endPersonStaff.mock.calls[0]?.[1]).toEqual({ expected_version: 4, reason: "结束最新岗位", idempotency_key: expect.any(String) })
})

it.each(["onChanged", "roster"])("keeps an acknowledged save distinct from an unknown write outcome when %s refresh fails", async mode => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "重新核实岗位")
  if (mode === "onChanged") view.changed.mockRejectedValueOnce(new Error("parent refresh failed"))
  else api.listStaffPositions.mockRejectedValue(new Error("roster refresh failed"))
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  expect(await screen.findByRole("status")).toHaveTextContent("岗位操作已保存，但名单刷新失败")
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "用原操作键重试" })).not.toBeInTheDocument()
  expect(screen.queryByText(/结果未确认|不能据此判断原操作/)).not.toBeInTheDocument()
  expect(api.reconfirmPersonStaff).toHaveBeenCalledTimes(1)
})

it.each(["actor", "team", "permission", "unmount"])("fences an acknowledged operation's late refresh failure after %s revocation", async mode => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  const view = setup()
  let rejectRefresh!: (failure: Error) => void
  view.changed.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectRefresh = reject }))
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "重新核实岗位")
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  await waitFor(() => expect(view.changed).toHaveBeenCalledTimes(1))
  const signal = api.reconfirmPersonStaff.mock.calls[0][2] as AbortSignal
  if (mode === "actor") view.rerender(view.panel("new-actor"))
  else if (mode === "team") view.rerender(view.panel("admin", ["read", "assign_managers"], "team-b"))
  else if (mode === "permission") view.rerender(view.panel("admin", ["read"]))
  else view.unmount()
  expect(signal.aborted).toBe(true)
  await act(async () => { rejectRefresh(new Error("late refresh failed")) })
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByText(/岗位操作已保存|结果未确认|不能据此判断原操作/)).not.toBeInTheDocument()
})

it("assigns unactivated candidates separately using person identity and no extra private fields", async () => {
  const user = userEvent.setup()
  setup()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  await user.selectOptions(screen.getByRole("combobox", { name: "候选来源" }), "person")
  await user.type(screen.getByRole("textbox", { name: "搜索工作人员" }), "甲")
  await user.click(await screen.findByRole("button", { name: "待激活甲 · 小甲 · 未激活" }))
  expect(api.staffCandidates).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "确认绑定" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.assignPersonStaff).toHaveBeenCalledWith("team-a", { person_id: "person-pending", staff_role: "manager", idempotency_key: expect.any(String) }, expect.any(AbortSignal))
  expect(api.assignStaff).not.toHaveBeenCalled()
})

it("ends a person role with reason and version, retrying the identical command after a lost response", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  api.endPersonStaff.mockRejectedValueOnce(new Error("lost")).mockResolvedValue({ assignment_id: "position-coach" })
  setup()
  await user.click(await screen.findByRole("button", { name: "解绑 张三 的教练岗位" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "  岗位结束  ")
  await user.click(screen.getByRole("button", { name: "确认解绑" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(screen.getByRole("textbox", { name: "操作原因" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "用原操作键重试" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.endPersonStaff.mock.calls[0]?.slice(0, 2)).toEqual(["position-coach", { reason: "岗位结束", expected_version: 3, idempotency_key: expect.any(String) }])
  expect(api.endPersonStaff.mock.calls[1]?.slice(0, 2)).toEqual(api.endPersonStaff.mock.calls[0]?.slice(0, 2))
})

it("does not fetch or render a staff entry for ordinary read-only viewers", async () => {
  setup(["read"])
  expect(screen.queryByRole("region", { name: "队内工作人员（只读）" })).not.toBeInTheDocument()
  expect(api.listStaffPositions).not.toHaveBeenCalled()
  expect(api.personStaffCandidates).not.toHaveBeenCalled()
})

it("keeps positions independent across teams and never carries a reason draft into another team", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockImplementation(async (team) => page([{ ...personPosition, full_name: team === "team-a" ? "甲队合成人员" : "乙队合成人员" }]))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "解绑 甲队合成人员 的教练岗位" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "甲队岗位原因")
  view.rerender(view.panel("admin", ["read", "assign_managers"], "team-b"))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(await screen.findByText("乙队合成人员")).toBeVisible()
  expect(screen.queryByText("甲队合成人员")).not.toBeInTheDocument()
  expect(screen.getByText("第 1 页，共 1 条")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "解绑 乙队合成人员 的教练岗位" }))
  expect(screen.getByRole("textbox", { name: "操作原因" })).toHaveValue("")
  expect(api.endPersonStaff).not.toHaveBeenCalled()
})

it("fences a late person mutation result when the actor account changes", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([personPosition]))
  let resolve!: (value: { assignment_id: string }) => void
  api.reconfirmPersonStaff.mockImplementation(() => new Promise(done => { resolve = done }))
  const view = setup()
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "核实授权")
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  const signal = api.reconfirmPersonStaff.mock.calls[0][2] as AbortSignal
  view.rerender(view.panel("new-actor"))
  expect(signal.aborted).toBe(true)
  resolve({ assignment_id: "position-coach" })
  await screen.findByText("张三")
  expect(view.changed).not.toHaveBeenCalled()
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
})

it("refreshes a stale personnel position and locks its old version without falling back to account maintenance", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([{ ...personPosition, account_id: "staff-a", account_assignment_id: "realized-a", account_status: "active" }]))
  api.reconfirmPersonStaff.mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409, detail: "岗位版本已变化", code: "team_assignment_stale", request_id: "synthetic" }))
  setup()
  await user.click(await screen.findByRole("button", { name: "重新确认授权" }))
  await user.type(screen.getByRole("textbox", { name: "操作原因" }), "核实授权")
  await user.click(screen.getByRole("button", { name: "确认重新授权" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("版本或并发状态已变化")
  expect(screen.getByRole("button", { name: "确认重新授权" })).toBeDisabled()
  expect(api.reconfirmPersonStaff).toHaveBeenCalledTimes(1)
  expect(api.endStaffAssignment).not.toHaveBeenCalled()
  expect(api.assignStaff).not.toHaveBeenCalled()
})

it("searches only the chosen role and confirms a read-only binding without roster privileges", async () => {
  const user = userEvent.setup()
  setup()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  await user.selectOptions(screen.getByRole("combobox", { name: "队内岗位" }), "coach")
  expect(api.staffCandidates).not.toHaveBeenCalled()
  await user.type(screen.getByRole("textbox", { name: "搜索工作人员" }), "甲")
  await user.click(await screen.findByRole("button", { name: /教练甲 · 小张 · 年龄待补/ }))
  await user.selectOptions(screen.getByRole("combobox", { name: "队内岗位" }), "assistant_coach")
  expect(screen.queryByRole("button", { name: /教练甲/ })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "确认绑定" })).toBeDisabled()
  await user.click(await screen.findByRole("button", { name: /助教乙 · 小张 · 年龄待补/ }))
  expect(screen.getByText(/不授予名单编辑、入住、调房或退房权限/)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认绑定" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.assignStaff).toHaveBeenCalledWith("team-a", { account_id: "staff-a", staff_role: "assistant_coach", idempotency_key: expect.any(String) })
  expect(screen.queryByText(/手机号|身份证|邮箱/)).not.toBeInTheDocument()
})

it("ends only one of the same account's multiple roles and preserves the other role", async () => {
  const user = userEvent.setup()
  const assistant = { ...assignment, row_key: "account:assignment-assistant", account_assignment_id: "assignment-assistant", staff_role: "assistant_coach" as const }
  api.listStaffPositions.mockResolvedValue(page([assignment, assistant]))
  api.endStaffAssignment.mockImplementation(async (id) => { api.listStaffPositions.mockResolvedValue(page([assistant])); return { assignment_id: id } })
  setup()
  await user.click(await screen.findByRole("button", { name: "解绑 张三 的教练岗位" }))
  expect(screen.getByText(/不影响该账号的其他岗位/)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认解绑" }))
  await waitFor(() => expect(screen.queryByRole("button", { name: "解绑 张三 的教练岗位" })).not.toBeInTheDocument())
  expect(screen.getByRole("button", { name: "解绑 张三 的助教岗位" })).toBeVisible()
  expect(api.endStaffAssignment).toHaveBeenCalledWith("assignment-coach", { idempotency_key: expect.any(String) })
})

it("retains the exact payload and operation key when a binding response is lost", async () => {
  const user = userEvent.setup()
  api.assignStaff.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({ assignment_id: "new" })
  setup()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  await user.type(screen.getByRole("textbox", { name: "搜索工作人员" }), "乙")
  await user.click(await screen.findByRole("button", { name: /助教乙/ }))
  await user.click(screen.getByRole("button", { name: "确认绑定" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(screen.getByRole("combobox", { name: "队内岗位" })).toBeDisabled()
  expect(screen.getByRole("textbox", { name: "搜索工作人员" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "用原操作键重试" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(api.assignStaff.mock.calls[1]).toEqual(api.assignStaff.mock.calls[0])
})

it("does not reconcile a lost coach binding against a different role of the same account", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([{ ...assignment, staff_role: "manager" }]))
  api.assignStaff.mockRejectedValue(new Error("response lost"))
  setup()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  await user.selectOptions(screen.getByRole("combobox", { name: "队内岗位" }), "coach")
  await user.type(screen.getByRole("textbox", { name: "搜索工作人员" }), "甲")
  await user.click(await screen.findByRole("button", { name: /教练甲/ }))
  await user.click(screen.getByRole("button", { name: "确认绑定" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(screen.getByRole("dialog")).toBeVisible()
})

it.each([401, 403, 404])("clears private rows, candidates and dialog on denied mutation %s", async (status) => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([assignment]))
  api.endStaffAssignment.mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "Denied", status, detail: "无权", code: "team_not_found", request_id: "synthetic" }))
  const { client } = setup()
  await user.click(await screen.findByRole("button", { name: "解绑 张三 的教练岗位" }))
  await user.click(screen.getByRole("button", { name: "确认解绑" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByText(/张三/)).not.toBeInTheDocument()
  expect(screen.getByRole("alert")).toHaveTextContent(status === 401 ? "登录已失效" : status === 404 ? "队伍不存在或已不可见" : "队伍权限已变更")
  expect(client.getQueryCache().findAll({ queryKey: ["teams", "admin"] }).filter((query) => query.queryKey[3] === "team-a")).toHaveLength(0)
})

it("drops drafts and private cache when assignment permission is revoked then restored", async () => {
  const user = userEvent.setup()
  const { rerender, panel, client } = setup()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  await user.type(screen.getByRole("textbox", { name: "搜索工作人员" }), "乙")
  await screen.findByRole("button", { name: /助教乙/ })
  rerender(panel("admin", ["read"]))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "绑定队内工作人员" })).not.toBeInTheDocument()
  expect(client.getQueryCache().findAll({ queryKey: ["teams", "admin", "staff-candidates"] })).toHaveLength(0)
  rerender(panel())
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
})

it("clears the previous identity's staff cache when the account changes", async () => {
  const user = userEvent.setup()
  const { rerender, panel, client } = setup()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  await user.type(screen.getByRole("textbox", { name: "搜索工作人员" }), "乙")
  await screen.findByRole("button", { name: /助教乙/ })
  rerender(panel("new-account"))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(client.getQueryCache().findAll({ queryKey: ["teams", "admin", "staff-candidates"] })).toHaveLength(0)
})

it("shows ended assignments without an unbind action and keeps keyboard focus in the dialog", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockImplementation(async (_team, options) => page(options.status === "ended" ? [{ ...assignment, ended_on: "2026-11-07", allowed_actions: [] }] : [assignment]))
  setup()
  await user.selectOptions(screen.getByRole("combobox", { name: "岗位绑定状态" }), "ended")
  const list = await screen.findByRole("list", { name: "队内岗位绑定" })
  expect(await within(list).findByText(/2026-11-07/)).toBeVisible()
  expect(within(list).queryByRole("button", { name: /解绑/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "绑定队内工作人员" }))
  for (let index = 0; index < 9; index += 1) { await user.tab(); expect(screen.getByRole("dialog")).toContainElement(document.activeElement as HTMLElement) }
})

it("locks conflicting operations until the user starts a fresh operation", async () => {
  const user = userEvent.setup()
  api.listStaffPositions.mockResolvedValue(page([assignment]))
  api.endStaffAssignment.mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "Conflict", status: 409, detail: "版本已变化", code: "team_conflict", request_id: "synthetic" }))
  setup()
  await user.click(await screen.findByRole("button", { name: "解绑 张三 的教练岗位" }))
  await user.click(screen.getByRole("button", { name: "确认解绑" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("版本或并发状态已变化")
  expect(screen.getByRole("button", { name: "确认解绑" })).toBeDisabled()
})
