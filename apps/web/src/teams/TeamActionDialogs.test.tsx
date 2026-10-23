import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { TeamActionDialogs } from "./TeamActionDialogs"
import type { TeamAction } from "./api"

const api = vi.hoisted(() => ({ candidates: vi.fn(), listMemberships: vi.fn(), listTeams: vi.fn(), join: vi.fn(), leave: vi.fn(), transfer: vi.fn() }))
vi.mock("./api", () => api)

beforeEach(() => vi.resetAllMocks())

const team = { id: "team-a", project_id: "project-a", project_name: "王者荣耀", name: "一队", is_active: true, member_count: 1, allowed_actions: ["read"] as TeamAction[] }
const member = { id: "membership-a", person_id: "person-a", full_name: "王明", nickname: null, age: 18, is_minor: false, role: "team_member" as const, project_id: "project-a", project_name: "王者荣耀", team_id: "team-a", team_name: "一队", status: "current" as const, joined_on: "2026-09-29", left_on: null, end_kind: null, account_status: "active" as const, transfer_reason: null }

it("labels unknown candidate and selected ages without inferring adulthood", async () => {
  api.candidates.mockResolvedValue({ items: [{ account_id: null, person_id: "pending", full_name: "待补队员", nickname: null, age: null, is_minor: null }], next_cursor: null })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TeamActionDialogs accountId="account-a" team={team} action="join" allowedActions={["manage"]} onClose={vi.fn()} onChanged={vi.fn()} /></QueryClientProvider>)
  await userEvent.type(screen.getByLabelText("搜索队员"), "待补")
  expect(await screen.findByText("无昵称 · 年龄待补 · 未激活")).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "选择 待补队员 无昵称" }))
  expect(screen.getByText("已选：待补队员 · 无昵称 · 年龄待补")).toBeVisible()
  expect(screen.queryByText(/成年|null岁|· 岁/)).not.toBeInTheDocument()
})

it("adds an accountless approved player by person ID and labels the candidate unactivated", async () => {
  api.candidates.mockResolvedValue({ items: [{ account_id: null, person_id: "accountless-person", full_name: "档案队员", nickname: null, age: 20, is_minor: false }], next_cursor: null })
  api.join.mockResolvedValue({ operation_id: "op", membership_id: "member", closed_membership_id: null, assignment_id: null })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TeamActionDialogs accountId="account-a" team={team} action="join" allowedActions={["manage"]} onClose={vi.fn()} onChanged={vi.fn()} /></QueryClientProvider>)
  await userEvent.type(screen.getByLabelText("搜索队员"), "档案")
  await userEvent.click(await screen.findByRole("button", { name: "选择 档案队员 无昵称" }))
  expect(screen.getByText(/20岁 · 未激活/)).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "确认添加" }))
  await waitFor(() => expect(api.join).toHaveBeenCalledWith("team-a", { person_id: "accountless-person", idempotency_key: expect.any(String) }))
})

it("does not expose mutation dialog controls without the resource action", () => {
  render(<QueryClientProvider client={new QueryClient()}><TeamActionDialogs accountId="account-a" team={team} member={member} action="leave" allowedActions={["read"]} onClose={vi.fn()} onChanged={vi.fn()} /></QueryClientProvider>)
  expect(screen.queryByRole("dialog", { name: "移出队员" })).not.toBeInTheDocument()
})

it("loads every authorized page before offering an active transfer target", async () => {
  const source = { ...team, allowed_actions: ["read", "transfer"] as TeamAction[] }
  const filler = Array.from({ length: 100 }, (_, index) => ({ ...team, id: `filler-${index}`, name: `队伍 ${index}` }))
  const target = { ...team, id: "team-target", name: "二队", project_name: "和平精英", allowed_actions: ["read"] as TeamAction[] }
  api.listTeams.mockResolvedValueOnce({ items: filler, page: 1, page_size: 100, total: 101 })
    .mockResolvedValueOnce({ items: [target], page: 2, page_size: 100, total: 101 })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TeamActionDialogs accountId="account-a" team={source} member={member} action="transfer" allowedActions={["read", "transfer"]} onClose={vi.fn()} onChanged={vi.fn()} /></QueryClientProvider>)
  expect(await screen.findByRole("option", { name: "和平精英 · 二队" })).toBeInTheDocument()
  expect(api.listTeams).toHaveBeenCalledTimes(2)
})

it("locks transfer draft after a lost response and retries its original payload and key", async () => {
  const user = userEvent.setup()
  const source = { ...team, allowed_actions: ["read", "transfer"] as TeamAction[] }
  const target = { ...team, id: "team-target", name: "二队" }
  api.listTeams.mockResolvedValue({ items: [source, target], page: 1, page_size: 100, total: 2 })
  api.listMemberships.mockResolvedValue({ items: [], page: 1, page_size: 100, total: 0 })
  api.transfer.mockRejectedValueOnce(new Error("response lost"))
    .mockResolvedValue({ operation_id: "operation-a", membership_id: "membership-new", closed_membership_id: member.id, assignment_id: null })
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TeamActionDialogs accountId="account-a" team={source} member={member} action="transfer" allowedActions={["read", "transfer"]} onClose={vi.fn()} onChanged={vi.fn()} /></QueryClientProvider>)
  await screen.findByRole("option", { name: "王者荣耀 · 二队" })
  await user.selectOptions(screen.getByLabelText("目标队伍"), "team-target")
  await user.type(screen.getByLabelText("调队原因"), "转入二队")
  await user.click(screen.getByRole("button", { name: "确认调队" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(screen.getByLabelText("目标队伍")).toBeDisabled()
  expect(screen.getByLabelText("调队原因")).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "用原操作键重试" }))
  await waitFor(() => expect(api.transfer).toHaveBeenCalledTimes(2))
  expect(api.transfer.mock.calls[1][1]).toEqual(api.transfer.mock.calls[0][1])
})
