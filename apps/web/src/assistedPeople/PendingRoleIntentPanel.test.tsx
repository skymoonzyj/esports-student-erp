import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { PendingRoleIntentPanel, PendingRoleIntentQueue } from "./PendingRoleIntentPanel"

const auth = vi.hoisted(() => ({ root: true }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", user: {
  account_id: "10000000-0000-4000-8000-000000000001", is_super_admin: auth.root,
  permissions: ["people.read", "personnel_intake.review", ...(auth.root ? ["permissions.manage"] : [])],
} }) }))
const person = "30000000-0000-4000-8000-000000000001"
let intent: { id: string; person_id: string; version: number; role_keys: string[]; role_names: string[]; status: string; account_activated: boolean; can_approve: boolean }
let writes: Record<string, unknown>[]
let fail: boolean
beforeEach(() => {
  auth.root = true; writes = []; fail = false
  setCsrfToken("isolated-test")
  intent = { id: "40000000-0000-4000-8000-000000000001", person_id: person, version: 1, role_keys: ["coach"], role_names: ["教练"], status: "pending", account_activated: true, can_approve: true }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") {
      writes.push(JSON.parse(String(init.body)))
      if (fail) { fail = false; throw new TypeError("lost connection") }
      intent = { ...intent, status: "approved", can_approve: false }
      return Response.json(intent)
    }
    if (String(url).includes("role-intents?")) return Response.json({ items: [{ ...intent, full_name: "隔离待批人员" }], page: 1, page_size: 20, total: 1 })
    return Response.json({ ...intent, can_approve: auth.root && intent.can_approve })
  }))
})
afterEach(() => vi.unstubAllGlobals())
function show(queue = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  return render(<QueryClientProvider client={client}><MemoryRouter>{queue ? <PendingRoleIntentQueue /> : <PendingRoleIntentPanel personId={person} />}</MemoryRouter></QueryClientProvider>)
}
it("keeps pending roles visible after base activation and requires explicit confirmation", async () => {
  show()
  expect(await screen.findByText(/基础账号已激活/)).toBeVisible()
  expect(screen.getByRole("button", { name: "批准账号角色" })).toBeDisabled()
  await userEvent.click(screen.getByRole("checkbox"))
  await userEvent.click(screen.getByRole("button", { name: "批准账号角色" }))
  expect(await screen.findByText("教练：已批准")).toBeVisible()
  expect(writes).toEqual([{ expected_version: 1, idempotency_key: expect.any(String) }])
  expect(screen.queryByRole("button", { name: "批准账号角色" })).not.toBeInTheDocument()
})
it("reuses the operation key after an uncertain network failure", async () => {
  fail = true; show()
  await userEvent.click(await screen.findByRole("checkbox"))
  await userEvent.click(screen.getByRole("button", { name: "批准账号角色" }))
  await screen.findByText(/尚未确认审批成功/)
  await waitFor(() => expect(screen.getByRole("button", { name: "批准账号角色" })).toBeEnabled())
  await userEvent.click(screen.getByRole("button", { name: "批准账号角色" }))
  await screen.findByText("教练：已批准")
  expect(writes).toHaveLength(2)
  expect(writes[1]).toEqual(writes[0])
})
it("administration can read the pending state but not approve", async () => {
  auth.root = false; show()
  await screen.findByText("教练：待超级管理员批准")
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  expect(writes).toEqual([])
})
it("provides a discoverable superadmin queue linking to the person's profile", async () => {
  show(true)
  expect(await screen.findByRole("link", { name: "隔离待批人员" })).toHaveAttribute("href", `/people/${person}`)
  expect(screen.getByText(/基础账号已激活/)).toBeVisible()
})
it("hides the role approval queue from administration", () => {
  auth.root = false; show(true)
  expect(screen.queryByRole("heading", { name: "待批准账号角色" })).not.toBeInTheDocument()
})
