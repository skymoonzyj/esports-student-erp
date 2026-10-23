import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, expect, it, vi } from "vitest"

import { MyTeamPanel } from "./MyTeamPanel"
import { ProblemDetailsError } from "../api/client"

const api = vi.hoisted(() => ({ getTeamContext: vi.fn(), myTeam: vi.fn() }))
vi.mock("./api", () => api)

function renderPanel() {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MyTeamPanel accountId="member-a" /></QueryClientProvider>)
}

const ownHistory = {
  id: "membership-old", person_id: "person-self", full_name: "王明", nickname: "小王",
  age: 18, is_minor: false, role: "team_member", project_id: "project-old",
  project_name: "和平精英", team_id: "team-old", team_name: "旧队",
  status: "left", joined_on: "2025-09-29", left_on: "2026-01-10",
  end_kind: "left", account_status: "active", transfer_reason: null,
}
const ownAffiliation = {
  membership_id: "membership-current", project_id: "project-current", project_name: "王者荣耀",
  team_id: "team-current", team_name: "一队", joined_on: "2026-09-29",
}

beforeEach(() => { vi.resetAllMocks() })

it("labels every self-history end type", async () => {
  api.getTeamContext.mockResolvedValue({ can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  api.myTeam.mockResolvedValue({ current: null, history: { items: ["left", "transferred", "account_deleted"].map((kind) => ({ ...ownHistory, id: kind, end_kind: kind })), page: 1, page_size: 20, total: 3 } })
  renderPanel()
  for (const label of ["离队", "调队离开", "账号删除"]) expect(await screen.findByText(label)).toBeInTheDocument()
})

it("distinguishes no read permission from an empty affiliation", async () => {
  api.getTeamContext.mockResolvedValue({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  renderPanel()
  expect(await screen.findByText("无查看权限")).toBeInTheDocument()
  expect(api.myTeam).not.toHaveBeenCalled()
})

it("renders only the member's own current and historical team information", async () => {
  api.getTeamContext.mockResolvedValue({ can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  api.myTeam.mockResolvedValue({ current: ownAffiliation, history: { items: [ownHistory], page: 1, page_size: 20, total: 1 } })
  renderPanel()
  expect(await screen.findByText("王者荣耀 · 一队")).toBeInTheDocument()
  expect(screen.getByText("和平精英 · 旧队")).toBeInTheDocument()
  expect(screen.queryByText("未分配队伍")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /添加队员|调队|分配管理/ })).not.toBeInTheDocument()
  expect(screen.queryByText(/手机号|邮箱|身份证|其他队员/)).not.toBeInTheDocument()
})

it("hides cached affiliation and history immediately after self-read permission is revoked", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  api.getTeamContext.mockResolvedValueOnce({ can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    .mockResolvedValueOnce({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  api.myTeam.mockResolvedValue({ current: ownAffiliation, history: { items: [ownHistory], page: 1, page_size: 20, total: 1 } })
  render(<QueryClientProvider client={client}><MyTeamPanel accountId="member-a" /></QueryClientProvider>)
  expect(await screen.findByText("王者荣耀 · 一队")).toBeInTheDocument()
  await act(async () => { await client.invalidateQueries({ queryKey: ["teams", "member-a", "context"] }) })
  expect(await screen.findByText("无查看权限")).toBeInTheDocument()
  await waitFor(() => {
    expect(screen.queryByText("王者荣耀 · 一队")).not.toBeInTheDocument()
    expect(screen.queryByText("和平精英 · 旧队")).not.toBeInTheDocument()
  })
})

it("clears only the denied self resource after a 404 and never renders stale affiliation", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  api.getTeamContext.mockResolvedValue({ can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  api.myTeam.mockResolvedValueOnce({ current: ownAffiliation, history: { items: [ownHistory], page: 1, page_size: 20, total: 1 } })
    .mockRejectedValueOnce(new ProblemDetailsError({ type: "about:blank", title: "Not found", status: 404, detail: "归属不可读", code: "not_found", request_id: "request-self" }))
  render(<QueryClientProvider client={client}><MyTeamPanel accountId="member-a" /></QueryClientProvider>)
  expect(await screen.findByText("王者荣耀 · 一队")).toBeInTheDocument()
  await act(async () => { await client.invalidateQueries({ queryKey: ["teams", "member-a", "self"] }) })
  await waitFor(() => expect(screen.queryByText("王者荣耀 · 一队")).not.toBeInTheDocument())
  expect(screen.queryByText("和平精英 · 旧队")).not.toBeInTheDocument()
  expect(client.getQueryData(["teams", "member-a", "self", 1])).toBeUndefined()
})
