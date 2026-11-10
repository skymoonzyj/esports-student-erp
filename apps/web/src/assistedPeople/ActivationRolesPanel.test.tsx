import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { PersonDetailPage } from "../people/PersonDetailPage"
import { setCsrfToken } from "../api/client"

const auth = vi.hoisted(() => ({ account: "10000000-0000-4000-8000-000000000001", permissions: [] as string[] }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", user: { account_id: auth.account, permissions: auth.permissions, scoped_permissions: [] } }) }))
const person = "30000000-0000-4000-8000-000000000001"
let available: boolean
let plan: { person_id: string; version: number; role_keys: string[]; role_names: string[]; can_edit: boolean; account_activated: boolean }
let status: number
let confirmationRequired: boolean
let loseConfirmedResponse: boolean
let reads: string[]
let writes: { url: string; body: Record<string, unknown> }[]
beforeEach(() => {
  auth.account = "10000000-0000-4000-8000-000000000001"; auth.permissions = []
  available = true; status = 200; confirmationRequired = false; loseConfirmedResponse = false; reads = []; writes = []
  plan = { person_id: person, version: 2, role_keys: ["coach"], role_names: ["教练"], can_edit: true, account_activated: false }
  setCsrfToken("test")
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "PUT") {
      const body = JSON.parse(String(init.body)); writes.push({ url: String(url), body })
      if (confirmationRequired && !body.confirm_end_staff_positions) return Response.json({ code: "activation_roles_staff_positions_confirmation_required", detail: "须确认结束岗位" }, { status: 409 })
      if (loseConfirmedResponse && body.confirm_end_staff_positions) { loseConfirmedResponse = false; throw new TypeError("network lost") }
      if (status !== 200) return Response.json({ code: "role_plan_conflict", detail: "private" }, { status })
      plan = { ...plan, version: plan.version + 1, role_keys: body.role_keys, role_names: body.role_keys.map((key: string) => key === "coach" ? "教练" : "监护人") }
      return Response.json(plan)
    }
    reads.push(String(url))
    if (String(url).endsWith("activation-role-options")) return Response.json({ items: [{ key: "coach", name: "教练", assignable: true, reason: null }, { key: "guardian", name: "监护人", assignable: true, reason: null }] })
    if (String(url).endsWith("activation-roles")) return Response.json(plan)
    return Response.json({ id: person, full_name: "合成档案", nickname: null, person_types: [], roles: [], identity_number: "***", phone: null, email: null,
      sex: null, date_of_birth: null, age_status: "unknown", assisted_kind: "staff", account_status: "not_activated", guardian_status: "not_applicable",
      student_number: null, student_status: null, staff_type: null, visit_reason: null, linked_student_id: null, created_at: "2026-11-06T00:00:00Z", activation_roles_available: available })
  }))
})

it("cancels staff-role removal without a second write and preserves the original role selection", async () => {
  confirmationRequired = true
  show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  expect(await screen.findByRole("dialog", { name: "确认结束队内岗位" })).toBeVisible()
  expect(screen.getByText(/同一事务.*结束.*岗位/)).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "取消移除角色" }))
  expect(writes).toHaveLength(1)
  expect(screen.getByRole("checkbox", { name: "教练" })).not.toBeChecked()
  expect(screen.queryByText(/角色计划已保存/)).not.toBeInTheDocument()
})

it("confirms staff-role removal atomically with the same version, a new operation key and stable network retry", async () => {
  confirmationRequired = true; loseConfirmedResponse = true
  show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  await userEvent.click(await screen.findByRole("button", { name: "确认移除角色并结束岗位" }))
  expect(await screen.findByText(/未确认保存成功/)).toBeVisible()
  expect(writes[1].body).toEqual({ role_keys: [], expected_version: 2, confirm_end_staff_positions: true, idempotency_key: expect.any(String) })
  expect(writes[1].body.idempotency_key).not.toBe(writes[0].body.idempotency_key)
  await userEvent.click(screen.getByRole("button", { name: "确认移除角色并结束岗位" }))
  await screen.findByText(/角色计划已保存/)
  expect(writes[2].body).toEqual(writes[1].body)
  expect(writes.map(write => write.url)).toEqual(Array(3).fill(`/api/v1/personnel-intake/people/${person}/activation-roles`))
})

it("does not broaden role removals or split staff endings when the confirmed atomic request is denied", async () => {
  confirmationRequired = true
  show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  await screen.findByRole("dialog", { name: "确认结束队内岗位" })
  status = 403
  await userEvent.click(screen.getByRole("button", { name: "确认移除角色并结束岗位" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("当前无权")
  expect(writes).toHaveLength(2)
  expect(writes[1].body.role_keys).toEqual(writes[0].body.role_keys)
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(screen.queryByText(/角色计划已保存/)).not.toBeInTheDocument()
})

it("discards a confirmation after a newer role plan arrives and traps keyboard focus until cancellation", async () => {
  confirmationRequired = true
  const view = show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  const dialog = await screen.findByRole("dialog", { name: "确认结束队内岗位" })
  for (let index = 0; index < 5; index += 1) { await userEvent.tab(); expect(dialog).toContainElement(document.activeElement as HTMLElement) }
  plan = { ...plan, version: 3, role_keys: ["guardian"], role_names: ["监护人"] }
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "activation-roles", person] }) })
  expect(view.client.getQueryData(["assisted-people", auth.account, "activation-roles", person])).toMatchObject({ version: 3 })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.getByRole("checkbox", { name: "监护人" })).toBeChecked()
  expect(writes).toHaveLength(1)
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); focusManager.setFocused(undefined) })
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = () => <MemoryRouter initialEntries={[`/people/${person}`]}><QueryClientProvider client={client}><Routes><Route path="/people/:personId" element={<PersonDetailPage />} /></Routes></QueryClientProvider></MemoryRouter>
  return { client, tree, ...render(tree()) }
}

// Catches accidentally loading a private plan merely from permissions.manage or account-opening authority.
it("does not request role data without the dedicated server capability", async () => {
  available = false; auth.permissions = ["permissions.manage"]
  show(); await screen.findByRole("heading", { name: "合成档案" })
  expect(reads).toEqual([`/api/v1/people/${person}`])
  expect(screen.queryByRole("region", { name: "激活角色计划" })).not.toBeInTheDocument()
})

it("requires explicit versioned save, permits reconfirmation and explains activation timing", async () => {
  show()
  expect(await screen.findByRole("checkbox", { name: "教练" })).toBeChecked()
  expect(writes).toHaveLength(0)
  await userEvent.click(screen.getByRole("checkbox", { name: "监护人" }))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  expect(await screen.findByText(/角色计划已保存，激活后生效/)).toBeVisible()
  expect(writes[0]).toEqual({ url: `/api/v1/personnel-intake/people/${person}/activation-roles`, body: { role_keys: ["coach", "guardian"], expected_version: 2, idempotency_key: expect.any(String) } })
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].body.expected_version).toBe(3)
  expect(writes[1].body.idempotency_key).not.toBe(writes[0].body.idempotency_key)
})

it("refreshes conflicting versions without claiming success or overwriting the latest plan", async () => {
  const view = show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "监护人" }))
  status = 409; plan = { ...plan, version: 5, role_keys: [], role_names: [] }
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  expect(await screen.findByText(/版本已变化.*重新核对/)).toBeVisible()
  expect(screen.queryByText(/角色计划已保存/)).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "监护人" })).not.toBeChecked()
  expect(view.client.getQueryData(["assisted-people", auth.account, "activation-roles", person])).toMatchObject({ version: 5 })
})

it("reads an activated plan without fetching edit options and points to existing permissions management", async () => {
  plan = { ...plan, can_edit: false, account_activated: true }
  show()
  expect(await screen.findByText(/账号已激活.*已有权限管理/)).toBeVisible()
  expect(screen.queryByRole("checkbox", { name: "教练" })).not.toBeInTheDocument()
  expect(reads.some(url => url.endsWith("activation-role-options"))).toBe(false)
  expect(writes).toHaveLength(0)
})

it("clears extra roles explicitly without cancelling minimum person-identity activation", async () => {
  show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "教练" }))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  await screen.findByText(/角色计划已保存/)
  expect(writes[0].body.role_keys).toEqual([])
  expect(screen.getByText(/未设置，保留原最低本人角色规则/)).toBeVisible()
})

it("blocks a cached writable plan while focus revalidation is in progress", async () => {
  show(); await screen.findByRole("checkbox", { name: "教练" })
  const original = vi.mocked(fetch).getMockImplementation()!
  let resolve!: (value: Response) => void
  vi.mocked(fetch).mockImplementation((url, init) => String(url).endsWith("activation-roles") ? new Promise(done => { resolve = done }) : original(url, init))
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(resolve).toBeTypeOf("function"))
  await waitFor(() => expect(screen.getByRole("button", { name: "保存并重新确认激活角色" })).toBeDisabled())
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  expect(writes).toHaveLength(0)
  await act(async () => resolve(Response.json({ ...plan, can_edit: false })))
  expect(await screen.findByText(/当前仅可查看/)).toBeVisible()
  expect(screen.queryByRole("button", { name: "保存并重新确认激活角色" })).not.toBeInTheDocument()
})

it("aborts an old-account save and fences a late success from the newly selected account", async () => {
  const view = show(); await screen.findByRole("checkbox", { name: "教练" })
  const original = vi.mocked(fetch).getMockImplementation()!
  let resolve!: (value: Response) => void
  let signal: AbortSignal | null | undefined
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (init?.method === "PUT") { signal = init.signal; return new Promise(done => { resolve = done }) }
    return original(url, init)
  })
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  auth.account = "10000000-0000-4000-8000-000000000002"
  view.rerender(view.tree())
  await screen.findByRole("checkbox", { name: "教练" })
  expect(signal?.aborted).toBe(true)
  await act(async () => resolve(Response.json({ ...plan, version: 3 })))
  expect(screen.queryByText(/角色计划已保存/)).not.toBeInTheDocument()
  expect(view.client.getQueryData(["assisted-people", auth.account, "activation-roles", person])).toMatchObject({ version: 2 })
})

it("does not restore an unsaved authorization draft after edit authority is revoked and restored", async () => {
  const view = show()
  await userEvent.click(await screen.findByRole("checkbox", { name: "监护人" }))
  plan = { ...plan, can_edit: false }
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "activation-roles", person] }) })
  await screen.findByText(/当前仅可查看/)
  plan = { ...plan, can_edit: true }
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "activation-roles", person] }) })
  expect(await screen.findByRole("checkbox", { name: "监护人" })).not.toBeChecked()
  expect(writes).toHaveLength(0)
})

it.each([
  { name: "a newer writable plan", version: 4, canEdit: true },
  { name: "a newer read-only plan", version: 4, canEdit: false },
  { name: "same-version edit revocation", version: 2, canEdit: false },
])("does not replace $name with a delayed same-account save response", async ({ version, canEdit }) => {
  const view = show()
  await screen.findByRole("checkbox", { name: "教练" })
  const original = vi.mocked(fetch).getMockImplementation()!
  const delayedResult = { ...plan, version: 3 }
  let resolveSave!: (value: Response) => void
  vi.mocked(fetch).mockImplementation((url, init) => init?.method === "PUT" ? new Promise(resolve => { resolveSave = resolve }) : original(url, init))
  await userEvent.click(screen.getByRole("button", { name: "保存并重新确认激活角色" }))
  await waitFor(() => expect(resolveSave).toBeTypeOf("function"))
  plan = { ...plan, version, role_keys: ["guardian"], role_names: ["监护人"], can_edit: canEdit }
  const key = ["assisted-people", auth.account, "activation-roles", person]
  await act(async () => { await view.client.refetchQueries({ queryKey: key }) })
  expect(view.client.getQueryData(key)).toEqual(plan)
  const readsBeforeLateResult = reads.filter(url => url.endsWith("activation-roles")).length
  await act(async () => resolveSave(Response.json(delayedResult)))
  // A save that was superseded must revalidate without overwriting the known
  // current plan or presenting its old approval/editability as current.
  expect(view.client.getQueryData(key)).toEqual(plan)
  expect(screen.queryByText(/角色计划已保存/)).not.toBeInTheDocument()
  await waitFor(() => expect(reads.filter(url => url.endsWith("activation-roles")).length).toBeGreaterThan(readsBeforeLateResult))
  if (canEdit) {
    expect(screen.getByRole("checkbox", { name: "监护人" })).toBeChecked()
    expect(screen.getByRole("checkbox", { name: "教练" })).not.toBeChecked()
    expect(screen.getByRole("button", { name: "保存并重新确认激活角色" })).toBeEnabled()
  } else {
    expect(screen.getByText(/当前仅可查看/)).toBeVisible()
    expect(screen.queryByRole("button", { name: "保存并重新确认激活角色" })).not.toBeInTheDocument()
  }
})
