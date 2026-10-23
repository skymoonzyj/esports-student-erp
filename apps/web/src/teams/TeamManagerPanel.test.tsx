import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { TeamManagerPanel } from "./TeamManagerPanel"
import { ProblemDetailsError } from "../api/client"

const api = vi.hoisted(() => ({ listAssignments: vi.fn(), managerCandidates: vi.fn(), assign: vi.fn(), unassign: vi.fn() }))
vi.mock("./api", () => api)

beforeEach(() => { vi.resetAllMocks(); api.listAssignments.mockResolvedValue({ items: [], page: 1, page_size: 20, total: 0 }) })

it("labels unknown manager candidate age without bare years or adult status", async () => {
  api.managerCandidates.mockResolvedValue({ items: [{ account_id: "manager", person_id: "pending", full_name: "待补管理", nickname: null, age: null, is_minor: null }], next_cursor: null })
  render(<QueryClientProvider client={new QueryClient()}><TeamManagerPanel accountId="admin" teamId="a" teamIsActive allowedActions={["assign_managers"]} onChanged={vi.fn()} /></QueryClientProvider>)
  await userEvent.click(screen.getByRole("button", { name: "分配管理人员" }))
  await userEvent.type(screen.getByPlaceholderText("姓名或昵称"), "待补")
  expect(await screen.findByRole("button", { name: "待补管理 · 无昵称 · 年龄待补" })).toBeVisible()
  expect(screen.queryByText(/成年|null岁|· 岁/)).not.toBeInTheDocument()
})

it("shows disabled managers and distinguishes same-name candidates by age", async () => {
  const user = userEvent.setup()
  api.listAssignments.mockResolvedValue({ items: [{ id: "assignment", account_id: "manager", full_name: "停用管理", nickname: null, account_status: "disabled", started_on: "2026-09-29", ended_on: null }], page: 1, page_size: 20, total: 1 })
  api.managerCandidates.mockResolvedValue({ items: [16, 23].map((age) => ({ account_id: `m${age}`, person_id: `p${age}`, full_name: "张三", nickname: null, age, is_minor: age < 18 })), next_cursor: null })
  render(<QueryClientProvider client={new QueryClient()}><TeamManagerPanel accountId="admin" teamId="a" teamIsActive allowedActions={["assign_managers"]} onChanged={vi.fn()} /></QueryClientProvider>)
  expect(await screen.findByText("账号已停用")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "分配管理人员" }))
  await user.type(screen.getByPlaceholderText("姓名或昵称"), "张")
  expect(await screen.findByRole("button", { name: /张三.*16岁.*未成年/ })).toBeInTheDocument()
  expect(screen.getByRole("button", { name: /张三.*23岁/ })).toBeInTheDocument()
})

it.each([403, 404, "reconcile"])("closes manager dialog after denied mutation %s", async (status) => {
  const user = userEvent.setup()
  const denied = new ProblemDetailsError({ type: "about:blank", title: "Denied", status: typeof status === "number" ? status : 404, detail: "无权", code: "team_not_found", request_id: "denied" })
  api.listAssignments.mockResolvedValueOnce({ items: [{ id: "assignment", account_id: "manager", full_name: "管理甲", nickname: null, account_status: "active", started_on: "2026-09-29", ended_on: null }], page: 1, page_size: 20, total: 1 })
  api.unassign.mockRejectedValue(status === "reconcile" ? new Error("network") : denied)
  if (status === "reconcile") api.listAssignments.mockRejectedValue(denied)
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TeamManagerPanel accountId="admin" teamId="a" teamIsActive allowedActions={["assign_managers"]} onChanged={vi.fn()} /></QueryClientProvider>)
  await user.click(await screen.findByRole("button", { name: "取消 管理甲 的管理分配" }))
  await user.click(screen.getByRole("button", { name: "确认取消" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByText(/管理甲/)).not.toBeInTheDocument()
})

it("drops a manager draft when its team changes and contains focus", async () => {
  const user = userEvent.setup()
  const client = new QueryClient()
  const panel = (teamId: string) => <QueryClientProvider client={client}><TeamManagerPanel accountId="admin" teamId={teamId} teamIsActive allowedActions={["assign_managers"]} onChanged={vi.fn()} /></QueryClientProvider>
  const view = render(panel("a"))
  await user.click(screen.getByRole("button", { name: "分配管理人员" }))
  const dialog = screen.getByRole("dialog")
  for (let index = 0; index < 7; index += 1) { await user.tab(); expect(dialog).toContainElement(document.activeElement as HTMLElement) }
  view.rerender(panel("b"))
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(api.assign).not.toHaveBeenCalled()
})

it("keeps manager assignment controls available only for authorized resources", async () => {
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TeamManagerPanel accountId="admin-a" teamId="team-a" teamIsActive={true} allowedActions={["read"]} onChanged={vi.fn()} /></QueryClientProvider>)
  expect(screen.queryByRole("button", { name: "分配管理人员" })).not.toBeInTheDocument()
})

it("hides new assignments for an inactive roster and reuses a lost-response operation key", async () => {
  const user = userEvent.setup()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const renderPanel = (isActive: boolean) => <QueryClientProvider client={client}><TeamManagerPanel accountId="admin-a" teamId="team-a" teamIsActive={isActive} allowedActions={["read", "assign_managers"]} onChanged={vi.fn()} /></QueryClientProvider>
  const view = render(renderPanel(false))
  expect(screen.queryByRole("button", { name: "分配管理人员" })).not.toBeInTheDocument()
  view.rerender(renderPanel(true))
  api.managerCandidates.mockResolvedValue({ items: [{ account_id: "manager-a", person_id: "person-a", full_name: "张三", nickname: "小张", age: 23, is_minor: false }], next_cursor: null })
  api.assign.mockRejectedValueOnce(new Error("response lost")).mockResolvedValue({ operation_id: "op-a", membership_id: null, closed_membership_id: null, assignment_id: "assignment-a" })
  await user.click(screen.getByRole("button", { name: "分配管理人员" }))
  await user.type(screen.getByPlaceholderText("姓名或昵称"), "张")
  await user.click(await screen.findByRole("button", { name: "张三 · 小张 · 23岁" }))
  await user.click(screen.getByRole("button", { name: "确认分配" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("结果未确认")
  expect(screen.getByPlaceholderText("姓名或昵称")).toBeDisabled()
  expect(screen.getByRole("button", { name: "张三 · 小张 · 23岁" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "用原操作键重试" }))
  await waitFor(() => expect(api.assign).toHaveBeenCalledTimes(2))
  expect(api.assign.mock.calls[1][1].idempotency_key).toBe(api.assign.mock.calls[0][1].idempotency_key)
  expect(api.assign.mock.calls[1][1].account_id).toBe(api.assign.mock.calls[0][1].account_id)
})
