import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import { TeamAllocationPanel } from "./TeamAllocationPanel"

const auth = vi.hoisted(() => ({ user: { account_id: "admin", is_super_admin: true, permissions: ["people.update"] }, refreshUser: vi.fn(async () => undefined) }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", ...auth }) }))
afterEach(() => { vi.unstubAllGlobals(); auth.user = { account_id: "admin", is_super_admin: true, permissions: ["people.update"] } })
const pending = { visibility: "allowed", status: "pending", version: 3, can_update: true } as const
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const ui = <MemoryRouter><QueryClientProvider client={client}><TeamAllocationPanel personId="p1" projection={pending} /></QueryClientProvider></MemoryRouter>
  return { client, ...render(ui) }
}

it("writes only status with trimmed reason and stable retry operation, then invalidates list detail and counts", async () => {
  const writes: Array<{ url: string; body: Record<string, unknown> }> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    writes.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    if (writes.length === 1) throw new TypeError("offline")
    return new Response(JSON.stringify({ visibility: "allowed", status: "not_required", version: 4 }), { headers: { "Content-Type": "application/json" } })
  }))
  const { client } = setup()
  client.setQueryData(["people", {}], { private: true })
  client.setQueryData(["people-counts"], { pending_team_allocation: 1 })
  client.setQueryData(["person", "p1"], { private: true })
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "  人员无需参训  " } })
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  await screen.findByRole("alert")
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  await screen.findByText("归属标记已保存")
  expect(writes.map(write => write.url)).toEqual(["/api/v1/people/p1/team-allocation", "/api/v1/people/p1/team-allocation"])
  expect(writes[0].body).toEqual({ status: "not_required", expected_version: 3, reason: "人员无需参训", idempotency_key: expect.stringMatching(/^[0-9a-f-]{36}$/) })
  expect(writes[1].body).toEqual(writes[0].body)
  for (const key of [["people", {}], ["people-counts"], ["person", "p1"]]) expect(client.getQueryState(key)?.isInvalidated).toBe(true)
})

it.each(["not-admin", "no-update", "denied"])("hides mutation for %s without hiding independent status", (condition) => {
  if (condition === "no-update") auth.user.permissions = []
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><TeamAllocationPanel personId="p1" projection={condition === "denied" ? { visibility: "denied", status: null, version: null, can_update: false } : { ...pending, can_update: false }} /></QueryClientProvider></MemoryRouter>)
  expect(screen.getByText(condition === "denied" ? "无查看权限" : "待划分队伍")).toBeVisible()
  expect(screen.queryByRole("button", { name: "标记无需分队" })).not.toBeInTheDocument()
})

it.each([403, 404])("clears private data and rejects late success after authority loss (%s)", async (status) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ status, title: "Forbidden", detail: "无权访问" }), { status, headers: { "Content-Type": "application/problem+json" } })))
  const { client } = setup()
  client.setQueryData(["people", {}], { private: true })
  client.setQueryData(["person", "p1"], { private: true })
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "核对" } })
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("队伍归属权限已失效")
  expect(screen.queryByText("待划分队伍")).not.toBeInTheDocument()
  await waitFor(() => expect(client.getQueryData(["people", {}])).toBeUndefined())
  expect(client.getQueryData(["person", "p1"])).toBeUndefined()
})

it("rejects late write results when admin permission disappears, preserving drafts on harmless rerender", async () => {
  let resolve!: (response: Response) => void
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => new Promise<Response>(done => { resolve = done })))
  const { rerender, client } = setup()
  const view = () => <MemoryRouter><QueryClientProvider client={client}><TeamAllocationPanel personId="p1" projection={{ ...pending, can_update: auth.user.permissions.includes("people.update") }} /></QueryClientProvider></MemoryRouter>
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "保留草稿" } })
  rerender(view())
  expect(screen.getByLabelText("队伍归属变更原因")).toHaveValue("保留草稿")
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  auth.user.permissions = []
  rerender(view())
  await act(async () => resolve(new Response(JSON.stringify({ visibility: "allowed", status: "not_required", version: 4 }), { headers: { "Content-Type": "application/json" } })))
  expect(screen.queryByText("归属标记已保存")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("队伍归属变更原因")).not.toBeInTheDocument()
  auth.user.permissions = ["people.update"]
  rerender(view())
  expect(screen.getByRole("button", { name: "标记无需分队" })).toBeVisible()
  expect(screen.queryByDisplayValue("保留草稿")).not.toBeInTheDocument()
})

it.each(["success", "403", "404", "409", "500", "network"] as const)("ignores old %s completion after capability is revoked and restored before the request settles", async outcome => {
  let finish!: () => void
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => new Promise<Response>((resolve, reject) => {
    finish = () => {
      if (outcome === "network") { reject(new TypeError("old network failure")); return }
      const status = outcome === "success" ? 200 : Number(outcome)
      resolve(new Response(JSON.stringify(status === 200 ? { ...pending, status: "not_required", version: 4 } : { status, title: "Old request", detail: "old private failure" }), { status, headers: { "Content-Type": "application/json" } }))
    }
  })))
  const { rerender, client } = setup()
  const view = (canUpdate: boolean) => <MemoryRouter><QueryClientProvider client={client}><TeamAllocationPanel personId="p1" projection={{ ...pending, can_update: canUpdate }} /></QueryClientProvider></MemoryRouter>
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "旧权限草稿" } })
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  rerender(view(false))
  expect(screen.queryByLabelText("队伍归属变更原因")).not.toBeInTheDocument()
  rerender(view(true))
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "新权限草稿" } })
  const keys = [["people", {}], ["people-counts"], ["person", "p1"]]
  for (const key of keys) client.setQueryData(key, { currentAuthority: true })
  const refreshCalls = auth.refreshUser.mock.calls.length
  await act(async () => finish())
  expect(screen.getByLabelText("队伍归属变更原因")).toHaveValue("新权限草稿")
  expect(screen.getByRole("button", { name: "保存归属标记" })).toBeEnabled()
  expect(screen.queryByText("归属标记已保存")).not.toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  for (const key of keys) {
    expect(client.getQueryData(key)).toEqual({ currentAuthority: true })
    expect(client.getQueryState(key)?.isInvalidated).toBe(false)
  }
  expect(auth.refreshUser.mock.calls.length).toBe(refreshCalls)
})

it("stops stale 409 writes rather than retrying with a fresh version or joining a team", async () => {
  const writes: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => { writes.push(String(url)); return new Response(JSON.stringify({ status: 409, title: "Conflict", detail: "conflict", code: "team_allocation_has_relationships" }), { status: 409, headers: { "Content-Type": "application/problem+json" } }) }))
  setup()
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "核对后无需分队" } })
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("归属状态或实际队伍关系已变化")
  expect(screen.getByRole("button", { name: "保存归属标记" })).toBeDisabled()
  expect(screen.getByLabelText("队伍归属变更原因")).toHaveValue("核对后无需分队")
  expect(writes).toEqual(["/api/v1/people/p1/team-allocation"])
})

it("preserves the draft's original expected version across a focus refresh", async () => {
  const writes: Array<Record<string, unknown>> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => { writes.push(JSON.parse(String(init?.body))); return new Response(JSON.stringify({ ...pending, status: "not_required", version: 5 }), { headers: { "Content-Type": "application/json" } }) }))
  const { rerender, client } = setup()
  await userEvent.click(screen.getByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "已核对" } })
  rerender(<MemoryRouter><QueryClientProvider client={client}><TeamAllocationPanel personId="p1" projection={{ ...pending, version: 4 }} /></QueryClientProvider></MemoryRouter>)
  expect(screen.getByLabelText("队伍归属变更原因")).toHaveValue("已核对")
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  await screen.findByText("归属标记已保存")
  expect(writes[0].expected_version).toBe(3)
})

it("can explicitly return a not-required person to pending without using role or membership operations", async () => {
  const writes: Array<{ url: string; body: Record<string, unknown> }> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    writes.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return new Response(JSON.stringify({ ...pending, version: 5 }), { headers: { "Content-Type": "application/json" } })
  }))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><TeamAllocationPanel personId="p2" projection={{ ...pending, status: "not_required", version: 4 }} /></QueryClientProvider></MemoryRouter>)
  await userEvent.click(screen.getByRole("button", { name: "标记待划分队伍" }))
  expect(screen.getByRole("button", { name: "保存归属标记" })).toBeDisabled()
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "   " } })
  expect(screen.getByRole("button", { name: "保存归属标记" })).toBeDisabled()
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "后续安排分队" } })
  await userEvent.click(screen.getByRole("button", { name: "保存归属标记" }))
  await screen.findByText("归属标记已保存")
  expect(writes).toEqual([{ url: "/api/v1/people/p2/team-allocation", body: { status: "pending", expected_version: 4, reason: "后续安排分队", idempotency_key: expect.stringMatching(/^[0-9a-f-]{36}$/) } }])
})
