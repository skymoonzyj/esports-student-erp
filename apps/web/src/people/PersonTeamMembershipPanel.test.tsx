import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { PersonTeamProjection, TeamContext, TeamMutationResult, TeamSummary } from "../teams/api"
import { PersonTeamMembershipPanel } from "./PersonTeamMembershipPanel"

const api = vi.hoisted(() => ({ getTeamContext: vi.fn(), listTeams: vi.fn(), join: vi.fn(), leave: vi.fn(), transfer: vi.fn() }))
const actor = vi.hoisted(() => ({ accountId: "actor-a" }))
vi.mock("../teams/api", () => api)
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({
  status: "authenticated", user: { account_id: actor.accountId, person_id: "actor-person", full_name: "管理员", email: null, force_password_change: false, permissions: [] },
}) }))

const context: TeamContext = { can_open_management: true, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: true }
const source: TeamSummary = { id: "team-a", project_id: "division-a", project_name: "王者荣耀", name: "一队", is_active: true, member_count: 1, allowed_actions: ["read", "manage", "transfer"] }
const target: TeamSummary = { id: "team-b", project_id: "division-b", project_name: "英雄联盟", name: "二队", is_active: true, member_count: 0, allowed_actions: ["read", "manage", "transfer"] }
const unassigned: PersonTeamProjection = { visibility: "allowed", current: null }
const assigned: PersonTeamProjection = { visibility: "allowed", current: { membership_id: "membership-a", project_id: "division-a", project_name: "王者荣耀", team_id: "team-a", team_name: "一队", joined_on: "2026-10-01" } }
const result: TeamMutationResult = { operation_id: "operation-a", membership_id: "membership-b", closed_membership_id: null, assignment_id: null }
function page(items: TeamSummary[]) { return { items, page: 1, page_size: 100, total: items.length } }
function problem(status: number, detail: string, code = "forbidden") {
  return new ProblemDetailsError({ type: "about:blank", title: "失败", status, detail, code, request_id: "request-a" })
}
function renderPanel(projection = unassigned, onSaved = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const element = (personId = "person-a", nextProjection = projection) => <QueryClientProvider client={client}><PersonTeamMembershipPanel personId={personId} projection={nextProjection} onSaved={onSaved} /></QueryClientProvider>
  return { ...render(element()), client, element, onSaved }
}
async function selectTarget() {
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole("combobox", { name: "分部" }), "division-b")
  await user.selectOptions(screen.getByRole("combobox", { name: "队伍" }), "team-b")
  return user
}
beforeEach(() => {
  vi.resetAllMocks()
  actor.accountId = "actor-a"
  api.getTeamContext.mockResolvedValue(context)
  api.listTeams.mockResolvedValue(page([source, target]))
  api.join.mockResolvedValue(result)
  api.leave.mockResolvedValue(result)
  api.transfer.mockResolvedValue(result)
})

// Break caught: introducing a client-side active-account gate or writing the actor ID instead of the target person.
it("joins the selected existing team for the target person and refreshes independent caches", async () => {
  const { client, onSaved } = renderPanel()
  client.setQueryData(["person", "person-a", "actor-a", "scope"], { marker: "profile" })
  client.setQueryData(["people", "actor-a"], { marker: "list" })
  client.setQueryData(["teams", "actor-a", "members", "team-b"], { marker: "roster" })
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  expect(await screen.findByRole("status")).toHaveTextContent("队伍归属已保存")
  expect(api.join).toHaveBeenCalledWith("team-b", { person_id: "person-a", idempotency_key: expect.stringMatching(/^[0-9a-f-]{36}$/i) })
  expect(api.leave).not.toHaveBeenCalled()
  expect(onSaved).toHaveBeenCalledTimes(1)
  for (const key of [["person", "person-a", "actor-a", "scope"], ["people", "actor-a"], ["teams", "actor-a", "members", "team-b"]]) {
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  }
})

// Break caught: silently slicing the catalog to its first page.
it("loads subsequent catalog pages before offering division and team choices", async () => {
  api.listTeams.mockImplementation(({ page: number }: { page: number }) => Promise.resolve(number === 1
    ? { items: [source], page: 1, page_size: 1, total: 2 }
    : { items: [target], page: 2, page_size: 1, total: 2 }))
  renderPanel()
  await selectTarget()
  expect(api.listTeams).toHaveBeenCalledWith({ page: 2, pageSize: 100 })
  expect(screen.getByRole("combobox", { name: "队伍" })).toHaveValue("team-b")
})

// Break caught: denied projections interpreted as unassigned or leaking directory data.
it("does not load or reveal a denied projection's membership or edit choices", () => {
  renderPanel({ visibility: "denied", current: assigned.current })
  expect(screen.getByText("无查看队伍归属权限")).toBeVisible()
  expect(screen.queryByText("未分配队伍")).not.toBeInTheDocument()
  expect(screen.queryByText(/王者荣耀|一队/)).not.toBeInTheDocument()
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  expect(api.getTeamContext).not.toHaveBeenCalled()
  expect(api.listTeams).not.toHaveBeenCalled()
})

// Break caught: showing editable controls to actors without management capability.
it("shows current division and team read-only without management capability", async () => {
  api.getTeamContext.mockResolvedValue({ ...context, can_open_management: false, can_transfer: false })
  renderPanel(assigned)
  expect(screen.getByText("王者荣耀 · 一队")).toBeVisible()
  expect(await screen.findByText("当前仅可查看队伍归属")).toBeVisible()
  expect(api.listTeams).not.toHaveBeenCalled()
  expect(screen.queryByRole("button", { name: /离队|转队|加入队伍/ })).not.toBeInTheDocument()
})

// Break caught: bypassing source capability checks or using global capability as a team grant.
it.each(["source", "context", "target"])("requires source and target transfer permissions plus context: %s denied", async (denied) => {
  if (denied === "context") api.getTeamContext.mockResolvedValue({ ...context, can_transfer: false })
  api.listTeams.mockResolvedValue(page([
    denied === "source" ? { ...source, allowed_actions: ["read"] } : source,
    denied === "target" ? { ...target, allowed_actions: ["read", "manage"] } : target,
  ]))
  renderPanel(assigned)
  await screen.findByText("队伍归属独立保存，不更改业务身份、项目教练、账号权限或历史。")
  await waitFor(() => expect(screen.queryByText("正在读取授权队伍…")).not.toBeInTheDocument())
  await waitFor(() => expect(api.listTeams).toHaveBeenCalled())
  expect(screen.queryByRole("button", { name: "转队" })).not.toBeInTheDocument()
  if (denied === "source") expect(screen.queryByRole("button", { name: "离队" })).not.toBeInTheDocument()
  expect(api.transfer).not.toHaveBeenCalled()
})

// Break caught: transferring without reason/confirmation or implementing leave-then-join.
it("transfers atomically only after an explicit reason and confirmation", async () => {
  renderPanel(assigned)
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "转队" }))
  const dialog = within(screen.getByRole("dialog", { name: "确认转队" }))
  expect(dialog.getByRole("button", { name: "确认转队" })).toBeDisabled()
  expect(api.transfer).not.toHaveBeenCalled()
  await user.type(dialog.getByRole("textbox", { name: "转队原因" }), "训练调整")
  await user.click(dialog.getByRole("button", { name: "确认转队" }))
  expect(await screen.findByRole("status")).toHaveTextContent("队伍归属已保存")
  expect(api.transfer).toHaveBeenCalledWith("membership-a", { target_team_id: "team-b", reason: "训练调整", idempotency_key: expect.any(String) })
  expect(api.leave).not.toHaveBeenCalled()
  expect(api.join).not.toHaveBeenCalled()
})

// Break caught: presenting same-team transfer as a write.
it("treats the current team as unchanged instead of creating a transfer", async () => {
  renderPanel(assigned)
  const user = userEvent.setup()
  await user.selectOptions(await screen.findByRole("combobox", { name: "分部" }), "division-a")
  await user.selectOptions(screen.getByRole("combobox", { name: "队伍" }), "team-a")
  expect(screen.getByText("当前已在所选队伍，无需保存")).toBeVisible()
  expect(screen.getByRole("button", { name: "转队" })).toBeDisabled()
  expect(api.transfer).not.toHaveBeenCalled()
})

// Break caught: ending membership before confirmation or sending the team ID as membership ID.
it("requires confirmation before leaving and preserves historical identities", async () => {
  renderPanel(assigned)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "离队" }))
  const dialog = within(screen.getByRole("dialog", { name: "确认离队" }))
  expect(dialog.getByText(/不删除账号、学员身份或历史/)).toBeVisible()
  expect(api.leave).not.toHaveBeenCalled()
  await user.click(dialog.getByRole("button", { name: "确认离队" }))
  expect(await screen.findByRole("status")).toHaveTextContent("队伍归属已保存")
  expect(api.leave).toHaveBeenCalledWith("membership-a", { idempotency_key: expect.any(String) })
})

// Break caught: changing operation key on retry or retaining it for a changed payload.
it("retries a lost response with the same key but regenerates it after changing target", async () => {
  api.join.mockRejectedValueOnce(new Error("lost response")).mockRejectedValueOnce(new Error("lost again")).mockResolvedValue(result)
  renderPanel()
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  await waitFor(() => expect(api.join).toHaveBeenCalledTimes(2))
  expect(api.join.mock.calls[1][1].idempotency_key).toBe(api.join.mock.calls[0][1].idempotency_key)
  await user.selectOptions(screen.getByRole("combobox", { name: "分部" }), "division-a")
  await user.selectOptions(screen.getByRole("combobox", { name: "队伍" }), "team-a")
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  await screen.findByRole("status")
  expect(api.join.mock.calls[2][1].idempotency_key).not.toBe(api.join.mock.calls[0][1].idempotency_key)
})

// Break caught: a failed leave regenerates its UUID or retries through another membership operation.
it("retries an unchanged leave after a lost response with the original operation key", async () => {
  api.leave.mockRejectedValueOnce(new Error("leave response lost"))
    .mockResolvedValueOnce({ ...result, membership_id: null, closed_membership_id: "membership-a" })
  const { onSaved } = renderPanel(assigned)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "离队" }))
  const dialog = within(screen.getByRole("dialog", { name: "确认离队" }))
  await user.click(dialog.getByRole("button", { name: "确认离队" }))
  expect(await dialog.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(onSaved).not.toHaveBeenCalled()
  expect(api.leave).toHaveBeenNthCalledWith(1, "membership-a", {
    idempotency_key: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i),
  })
  const originalKey = api.leave.mock.calls[0][1].idempotency_key
  await user.click(dialog.getByRole("button", { name: "确认离队" }))
  expect(await screen.findByRole("status")).toHaveTextContent("队伍归属已保存")
  expect(screen.queryByRole("dialog", { name: "确认离队" })).not.toBeInTheDocument()
  expect(api.leave).toHaveBeenCalledTimes(2)
  expect(api.leave).toHaveBeenNthCalledWith(2, "membership-a", { idempotency_key: originalKey })
  expect(api.join).not.toHaveBeenCalled()
  expect(api.transfer).not.toHaveBeenCalled()
  expect(onSaved).toHaveBeenCalledTimes(1)
})

// Break caught: a failed transfer changes its UUID/payload or falls back to leave-then-join on retry.
it("retries an unchanged transfer after a lost response with the original operation key", async () => {
  api.transfer.mockRejectedValueOnce(new Error("transfer response lost"))
    .mockResolvedValueOnce({ ...result, closed_membership_id: "membership-a" })
  const { onSaved } = renderPanel(assigned)
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "转队" }))
  const dialog = within(screen.getByRole("dialog", { name: "确认转队" }))
  await user.type(dialog.getByRole("textbox", { name: "转队原因" }), "训练调整")
  await user.click(dialog.getByRole("button", { name: "确认转队" }))
  expect(await dialog.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(dialog.getByRole("textbox", { name: "转队原因" })).toHaveValue("训练调整")
  expect(screen.getByRole("combobox", { name: "队伍" })).toHaveValue("team-b")
  expect(onSaved).not.toHaveBeenCalled()
  expect(api.transfer).toHaveBeenNthCalledWith(1, "membership-a", {
    target_team_id: "team-b", reason: "训练调整",
    idempotency_key: expect.stringMatching(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i),
  })
  const originalKey = api.transfer.mock.calls[0][1].idempotency_key
  await user.click(dialog.getByRole("button", { name: "确认转队" }))
  expect(await screen.findByRole("status")).toHaveTextContent("队伍归属已保存")
  expect(screen.queryByRole("dialog", { name: "确认转队" })).not.toBeInTheDocument()
  expect(api.transfer).toHaveBeenCalledTimes(2)
  expect(api.transfer).toHaveBeenNthCalledWith(2, "membership-a", {
    target_team_id: "team-b", reason: "训练调整", idempotency_key: originalKey,
  })
  expect(api.leave).not.toHaveBeenCalled()
  expect(api.join).not.toHaveBeenCalled()
  expect(onSaved).toHaveBeenCalledTimes(1)
})

// Break caught: replacing useful role/eligibility errors with a generic transport error.
it("preserves the selected team and displays backend account eligibility errors", async () => {
  api.join.mockRejectedValue(problem(409, "已有账号必须启用且拥有队员角色", "team_candidate_ineligible"))
  renderPanel()
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("已有账号必须启用且拥有队员角色")
  expect(screen.getByRole("combobox", { name: "队伍" })).toHaveValue("team-b")
})

// Break caught: rendering previously authorized cached choices after a permission failure.
it.each([403, 404])("drops stale choices when catalog refresh returns %s", async (status) => {
  const { client } = renderPanel()
  await selectTarget()
  api.listTeams.mockRejectedValue(problem(status, "权限已变更"))
  await act(async () => { await client.invalidateQueries({ queryKey: ["teams", "actor-a", "person-membership-options"] }) })
  await waitFor(() => expect(screen.queryByRole("combobox")).not.toBeInTheDocument())
  expect(screen.queryByRole("option", { name: "二队" })).not.toBeInTheDocument()
  expect(client.getQueryData(["teams", "actor-a", "person-membership-options"])).toBeUndefined()
})

// Break caught: a late old-person or old-actor mutation firing the next target's save callback.
it.each(["person", "actor"])("ignores a delayed mutation after switching %s", async (change) => {
  let finish: ((value: TeamMutationResult) => void) | undefined
  api.join.mockImplementation(() => new Promise((resolve) => { finish = resolve }))
  const { element, rerender, onSaved } = renderPanel()
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  if (change === "actor") actor.accountId = "actor-b"
  rerender(element(change === "person" ? "person-b" : "person-a"))
  await act(async () => { finish?.(result) })
  expect(onSaved).not.toHaveBeenCalled()
  expect(screen.queryByRole("status")).not.toBeInTheDocument()
})

// Break caught: stale actor-scoped directory responses appearing after an account switch.
it("never displays the previous actor's delayed catalog", async () => {
  let finish: ((value: ReturnType<typeof page>) => void) | undefined
  api.listTeams.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  const { element, rerender, client } = renderPanel()
  await waitFor(() => expect(api.listTeams).toHaveBeenCalledTimes(1))
  actor.accountId = "actor-b"
  api.getTeamContext.mockResolvedValue({ ...context, can_open_management: false })
  rerender(element())
  await screen.findByText("当前仅可查看队伍归属")
  await act(async () => { finish?.(page([target])) })
  expect(screen.queryByText("二队")).not.toBeInTheDocument()
  expect(client.getQueryData(["teams", "actor-b", "person-membership-options"])).toBeUndefined()
})

// Break caught: an old operation becomes current again when a previously revoked capability is restored.
it.each(["success", "failure"])("ignores a delayed transfer %s after capability revocation and restoration", async (outcome) => {
  let finish: ((value: TeamMutationResult) => void) | undefined
  let fail: ((error: unknown) => void) | undefined
  api.transfer.mockImplementation(() => new Promise((resolve, reject) => { finish = resolve; fail = reject }))
  const { client, onSaved } = renderPanel(assigned)
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "转队" }))
  await user.type(screen.getByRole("textbox", { name: "转队原因" }), "训练调整")
  await user.click(screen.getByRole("button", { name: "确认转队" }))
  api.getTeamContext.mockResolvedValue({ ...context, can_transfer: false })
  await act(async () => { await client.invalidateQueries({ queryKey: ["teams", "actor-a", "context"] }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  api.getTeamContext.mockResolvedValue(context)
  await act(async () => { await client.invalidateQueries({ queryKey: ["teams", "actor-a", "context"] }) })
  await act(async () => { if (outcome === "success") finish?.(result); else fail?.(problem(409, "旧操作错误")) })
  expect(onSaved).not.toHaveBeenCalled()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(screen.queryByRole("status")).not.toBeInTheDocument()
})

// Break caught: retaining a failed transfer's idempotency key for a new reason.
it("generates a new transfer key when the reason changes after failure", async () => {
  api.transfer.mockRejectedValueOnce(problem(409, "请检查原因", "reason_invalid")).mockResolvedValue(result)
  renderPanel(assigned)
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "转队" }))
  await user.type(screen.getByRole("textbox", { name: "转队原因" }), "训练调整")
  await user.click(screen.getByRole("button", { name: "确认转队" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("请检查原因")
  await user.type(screen.getByRole("textbox", { name: "转队原因" }), "补充")
  await user.click(screen.getByRole("button", { name: "确认转队" }))
  await screen.findByRole("status")
  expect(api.transfer.mock.calls[1][1].idempotency_key).not.toBe(api.transfer.mock.calls[0][1].idempotency_key)
  expect(api.transfer.mock.calls[1][1].reason).toBe("训练调整补充")
})

// Break caught: leaving mutation-denied team choices visible and actionable from cache.
it.each([403, 404])("clears the membership editor after a mutation returns %s", async (status) => {
  api.join.mockRejectedValue(problem(status, "队伍不可访问"))
  const { client, onSaved } = renderPanel()
  const user = await selectTarget()
  await user.click(screen.getByRole("button", { name: "加入队伍" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("队伍权限已变更")
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "加入队伍" })).not.toBeInTheDocument()
  expect(client.getQueryData(["teams", "actor-a", "person-membership-options"])).toBeUndefined()
  expect(onSaved).not.toHaveBeenCalled()
})

// Break caught: allowing inactive or read-only teams as join targets.
it.each(["inactive", "read-only"])("does not offer %s teams as join targets", async (restriction) => {
  api.listTeams.mockResolvedValue(page([{ ...target, is_active: restriction !== "inactive", allowed_actions: restriction === "read-only" ? ["read"] : ["read", "manage"] }]))
  renderPanel()
  await screen.findByText("当前仅可查看队伍归属")
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "加入队伍" })).not.toBeInTheDocument()
})
