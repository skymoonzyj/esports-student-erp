import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { AssistedDirectCreatePage } from "./AssistedDirectCreatePage"

const auth = vi.hoisted(() => ({ account: "10000000-0000-4000-8000-000000000001" }))
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ status: "authenticated", user: { account_id: auth.account, permissions: ["people.create"] } }) }))
const person = "30000000-0000-4000-8000-000000000001"
const context = { can_submit: false, can_review: false, can_direct_create: true, can_read_managed: false, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] }
let allowed: boolean
let otherAccess: boolean
let pendingCapability: boolean | undefined
let postStatus: number
let contextFailure: "network" | number | null
let writes: { url: string; body: Record<string, unknown> }[]
let reads: string[]
beforeEach(() => {
  allowed = true; otherAccess = false; pendingCapability = true; postStatus = 500; writes = []; reads = []
  contextFailure = null
  auth.account = "10000000-0000-4000-8000-000000000001"
  setCsrfToken("test")
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === "POST") {
      writes.push({ url: String(url), body: JSON.parse(String(init.body)) })
      return Response.json(postStatus === 200 ? { request_id: null, person_id: person, approval_id: person } : { code: "private", detail: "raw-identity-must-not-leak" }, { status: postStatus })
    }
    reads.push(String(url))
    if (String(url).endsWith("/context") && contextFailure) {
      if (contextFailure === "network") throw new TypeError("offline")
      return Response.json({ detail: "unavailable" }, { status: contextFailure })
    }
    if (String(url).endsWith("/readiness")) return Response.json({ approval_valid: true, housing_ready: false, completion_allowed: true, activation_profile_ready: false,
      approval_id: person, version: 1, kind: "staff", profile_missing: ["sex", "date_of_birth"], housing_missing: ["sex"], activation_missing: ["sex", "date_of_birth", "email"] })
    return Response.json({ ...context, can_direct_create: allowed, can_submit: otherAccess, can_review: otherAccess,
      ...(pendingCapability === undefined ? {} : { can_direct_create_pending: pendingCapability }) })
  }))
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null); focusManager.setFocused(undefined) })
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (visible = true) => <QueryClientProvider client={client}>{visible ? <AssistedDirectCreatePage /> : null}</QueryClientProvider>
  return { client, tree, ...render(tree()) }
}
async function fillPending() {
  await userEvent.click(await screen.findByRole("button", { name: "资料待补建档" }))
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "Passport Person" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "AB123456" } })
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
}

it.each([500, "network"] as const)("retains draft and uncertain operation key through focus refresh %s and revalidates before retry", async (failure) => {
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  const initialReads = reads.length
  contextFailure = failure
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(reads.length).toBeGreaterThan(initialReads))
  await waitFor(() => expect(screen.queryByLabelText("护照号码")).not.toBeInTheDocument())
  expect(writes).toHaveLength(1)
  contextFailure = null
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  expect(screen.getByLabelText("姓名")).toHaveValue("Passport Person")
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeDefined()
})

it("explicit discard clears the pending facts and uncertain key", async () => {
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  const old = writes[0].body.idempotency_key
  await userEvent.click(screen.getByRole("button", { name: "清除待补草稿" }))
  expect(screen.getByLabelText("身份证")).toHaveValue("")
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined()
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "Passport Person" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "AB123456" } })
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].body.idempotency_key).not.toBe(old)
})

it("a late old-account focus response cannot restore its draft or overwrite new-account facts", async () => {
  const view = show(); await fillPending()
  const firstAccount = auth.account
  const original = vi.mocked(fetch).getMockImplementation()!
  let resolveOld!: (value: Response) => void
  let pause = true
  vi.mocked(fetch).mockImplementation((url, init) => {
    if (pause && String(url).endsWith("/context")) return new Promise(resolve => { resolveOld = resolve })
    return original(url, init)
  })
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(resolveOld).toBeTypeOf("function"))
  pause = false; auth.account = "10000000-0000-4000-8000-000000000002"
  view.rerender(view.tree())
  await fillPending()
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "New Account Person" } })
  await act(async () => resolveOld(Response.json({ ...context, can_direct_create_pending: true })))
  expect(screen.getByLabelText("姓名")).toHaveValue("New Account Person")
  expect(view.client.getQueryData(["assisted-people", firstAccount, "pending-create-draft"])).toBeUndefined()
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toMatchObject({ draft: { full_name: "New Account Person" } })
})

it("does not expose pending mode merely because the user has global create permission", async () => {
  allowed = false
  show()
  expect(await screen.findByRole("alert")).toHaveTextContent("无管理建档权限")
  expect(screen.queryByRole("button", { name: "资料待补建档" })).not.toBeInTheDocument()
  expect(reads.some((url) => url.endsWith("/readiness"))).toBe(false)
})

it.each([false, undefined])("ordinary create remains available without pending capability %s, and pending mode is never inferred", async (capability) => {
  pendingCapability = capability
  show()
  expect(await screen.findByRole("heading", { name: "管理建档" })).toBeVisible()
  expect(screen.getByRole("button", { name: "普通严格建档" })).toBeVisible()
  expect(screen.getByLabelText("身份证")).toHaveValue("")
  expect(screen.queryByRole("button", { name: "资料待补建档" })).not.toBeInTheDocument()
  expect(writes).toHaveLength(0)
})

it("retries the dedicated pending create with one UUID and keeps unknown facts null", async () => {
  show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[0].url).toBe("/api/v1/personnel-intake/direct-create-pending")
  expect(writes[0].body).toEqual({ full_name: "Passport Person", identity_number: "AB123456", kind: "staff", document_type: "passport", document_issuing_country: null,
    sex: null, date_of_birth: null, phone: null, email: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending", idempotency_key: expect.any(String) })
  expect(writes[1]).toEqual(writes[0])
  expect(screen.queryByText("raw-identity-must-not-leak")).not.toBeInTheDocument()
})

it("revoking direct creation purges its sensitive pending draft even when submit/review remain authorized", async () => {
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeDefined()
  allowed = false; pendingCapability = false; otherAccess = true
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await screen.findByText("无管理建档权限。")
  await waitFor(() => expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined())
  expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument()
  expect(writes).toHaveLength(1)
})

it("revoking only pending creation clears its draft/key/confirmation while preserving ordinary create", async () => {
  otherAccess = true
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  pendingCapability = false
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await waitFor(() => expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined())
  expect(screen.getByRole("button", { name: "普通严格建档" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "资料待补建档" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" })).not.toBeChecked()
  pendingCapability = true
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].body.idempotency_key).not.toBe(writes[0].body.idempotency_key)
})

it("a pending retry key is discarded on capability revocation even after switching to strict mode", async () => {
  otherAccess = true
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  await userEvent.click(screen.getByRole("button", { name: "普通严格建档" }))
  expect(screen.getByLabelText("身份证")).toHaveValue("")
  pendingCapability = false
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await waitFor(() => expect(view.client.getQueryData(["assisted-people", auth.account, "pending-create-draft"])).toBeUndefined())
  expect(screen.getByRole("button", { name: "普通严格建档" })).toBeVisible()
  pendingCapability = true
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people", auth.account, "context"] }) })
  await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  const { idempotency_key: oldKey, ...oldFacts } = writes[0].body
  const { idempotency_key: newKey, ...newFacts } = writes[1].body
  expect(newFacts).toEqual(oldFacts)
  expect(newKey).not.toBe(oldKey)
})

it("retains a draft and retry UUID on route return and refocus without browser persistence", async () => {
  const local = vi.spyOn(Storage.prototype, "setItem")
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await screen.findByText(/操作未成功/)
  view.rerender(view.tree(false))
  view.rerender(view.tree())
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-people"] }) })
  expect(screen.getByLabelText("姓名")).toHaveValue("Passport Person")
  await userEvent.click(screen.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案" }))
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
  expect(local).not.toHaveBeenCalled()
  expect(window.location.href).not.toContain("AB123456")
  local.mockRestore()
})

it("success displays actual server missing facts and stays explicitly unactivated", async () => {
  postStatus = 200
  show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  expect(await screen.findByText(/资料待补：性别、出生日期/)).toBeVisible()
  expect(screen.getByText(/账号未激活/)).toBeVisible()
  expect(screen.queryByLabelText("护照号码")).not.toBeInTheDocument()
  expect(reads.filter((url) => url.endsWith("/readiness"))).toEqual([`/api/v1/personnel-intake/people/${person}/readiness`])
})

it.each([403, 404])("private denial %s clears the pending draft and prevents another write", async (status) => {
  postStatus = status
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "建立待补档案" }))
  await waitFor(() => expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument())
  expect(writes).toHaveLength(1)
  view.rerender(view.tree(false)); view.rerender(view.tree())
  await screen.findByRole("heading", { name: "管理建档" })
  await userEvent.click(screen.getByRole("button", { name: "资料待补建档" }))
  expect(screen.getByLabelText("身份证")).toHaveValue("")
})
