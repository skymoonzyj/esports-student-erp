import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { AssistedProfileCorrectionPanel } from "./AssistedProfileCorrectionPanel"
import { CoachResponsibilityPanel } from "./CoachResponsibilityPanel"

const auth = vi.hoisted(() => ({ account: "10000000-0000-4000-8000-000000000001" }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", user: { account_id: auth.account, permissions: ["people.update"] } }) }))
const person = "30000000-0000-4000-8000-000000000001"
const coach = "30000000-0000-4000-8000-000000000002"
const unavailable = "30000000-0000-4000-8000-000000000003"
const metadata = { person_id: person, kind: "both", profile_version: 4, can_edit_activation_email: true, current_coach_person_ids: [coach], eligible_coaches: [{ id: coach, label: "测试教练" }], allowed_actions: ["correct_profile", "replace_coaches"] }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } })
let current = metadata
let post: (body: Record<string, unknown>) => Promise<Response>
let writes: Record<string, unknown>[]
let writeUrls: string[]
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>
beforeEach(() => {
  auth.account = "10000000-0000-4000-8000-000000000001"
  current = metadata; writes = []; writeUrls = []
  post = async () => json({ request_id: null, person_id: person, approval_id: coach })
  setCsrfToken("test")
  fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).endsWith("editor-context")) return json(current)
    if (String(url).endsWith("readiness")) return json({ approval_valid: true, housing_ready: false, completion_allowed: true, activation_profile_ready: false,
      approval_id: coach, version: 4, kind: "staff", profile_missing: ["sex", "date_of_birth"], housing_missing: ["sex"], activation_missing: ["sex", "date_of_birth", "email"] })
    if (init?.method === "POST" || init?.method === "PUT") {
      const body = JSON.parse(String(init.body)); writes.push(body); writeUrls.push(String(url)); return post(body)
    }
    throw new Error("Unexpected endpoint")
  })
  vi.stubGlobal("fetch", fetcher)
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })
function show(both = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = () => <QueryClientProvider client={client}><AssistedProfileCorrectionPanel personId={person} />{both ? <CoachResponsibilityPanel personId={person} /> : null}</QueryClientProvider>
  return { client, tree, ...render(tree()) }
}
async function fillAdult() {
  for (const [label, value] of [["姓名", "测试本人"], ["身份证", "110105199001021238"], ["手机号", "13800000000"], ["邮箱", "adult@example.test"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "1990-01-02" } })
  await userEvent.selectOptions(screen.getByLabelText("性别"), "male")
  await userEvent.type(screen.getByLabelText("资料更正原因"), "资料核对")
}

it.each([
  { kind: "staff", canEditEmail: true },
  { kind: "staff", canEditEmail: false },
  { kind: "student", canEditEmail: true },
  { kind: "player", canEditEmail: true },
])("corrects strict $kind facts with protected-email=$canEditEmail without changing identity, affiliations or activation roles", async ({ kind, canEditEmail }) => {
  current = Object.assign({}, metadata, { kind, intake_mode: "strict", can_edit_activation_email: canEditEmail,
    current_coach_person_ids: [], eligible_coaches: [], allowed_actions: ["correct_profile"] })
  post = async () => json({}, 500)
  show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(screen.queryByLabelText("人员身份")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("主要身份")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("所属队伍")).not.toBeInTheDocument()
  expect(screen.queryByRole("group", { name: "账号激活角色（激活后生效）" })).not.toBeInTheDocument()
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/management-identity-context"))).toBe(false)
  if (!canEditEmail) expect(screen.getByLabelText("邮箱")).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(writes).toHaveLength(0)
  expect(screen.getByLabelText("手机号")).toHaveAttribute("aria-invalid", "true")
  await fillAdult()
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await screen.findByText(/操作未成功/)
  expect(writes).toHaveLength(1)
  expect(writes[0]).toEqual({ full_name: "测试本人", identity_number: "110105199001021238", phone: "13800000000",
    ...(canEditEmail ? { email: "adult@example.test" } : {}), sex: "male", date_of_birth: "1990-01-02", guardians: [],
    expected_version: 4, reason: "资料核对", idempotency_key: expect.any(String) })
  expect(writeUrls[0]).toBe(`/api/v1/personnel-intake/people/${person}/corrections`)
  post = async () => json({ request_id: null, person_id: person, approval_id: coach })
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(await screen.findByText("资料更正成功。")).toBeVisible()
  expect(writes).toHaveLength(2)
  expect(writes[1]).toEqual(writes[0])
})

it.each([["hk_id", "香港身份证号码", "A123456(3)", "A1234563"], ["hk_mo_permit", "回乡证号码", "h1234567802", "H12345678"]])("retains the %s discriminator and version in strict correction evidence", async (documentType, label, number, canonical) => {
  show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  fireEvent.change(screen.getByLabelText("证件类型"), { target: { value: documentType } })
  fireEvent.change(screen.getByLabelText(label), { target: { value: number } })
  fireEvent.change(screen.getByLabelText("性别"), { target: { value: "male" } })
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "1990-01-02" } })
  fireEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toMatchObject({ document_type: documentType, identity_number: canonical, expected_version: 4 })
})

function pendingMetadata() {
  current = Object.assign({}, metadata, { kind: "staff", intake_mode: "pending", can_edit_activation_email: false, current_coach_person_ids: [], allowed_actions: ["correct_pending_profile"] })
}
async function fillPending() {
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await userEvent.selectOptions(screen.getByLabelText("证件类型"), "passport")
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "Passport Person" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "AB123456" } })
  fireEvent.change(screen.getByLabelText("资料更正原因"), { target: { value: "真实资料核对" } })
}

it.each([
  { intake_mode: "strict", allowed_actions: ["correct_pending_profile"] },
  { intake_mode: "pending", allowed_actions: ["correct_profile"] },
])("requires both explicit pending source and the server pending-correction capability %#", async (override) => {
  current = Object.assign({}, metadata, override)
  show()
  await waitFor(() => expect(fetcher).toHaveBeenCalled())
  expect(screen.queryByRole("button", { name: "更正本人及监护资料" })).not.toBeInTheDocument()
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("/readiness"))).toBe(false)
})

it.each([["passport", "护照号码", "AB123456", "AB123456"], ["hk_mo_permit", "回乡证号码", "m8765432102", "M87654321"]])("%s pending correction posts only nullable facts and retains retry key, version and protected email", async (documentType, label, number, canonical) => {
  pendingMetadata(); post = async () => json({ detail: "private server data" }, 500)
  show(); await fillPending()
  fireEvent.change(screen.getByLabelText("证件类型"), { target: { value: documentType } })
  fireEvent.change(screen.getByLabelText(label), { target: { value: number } })
  expect(screen.queryByLabelText("人员身份")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("队伍归属状态")).not.toBeInTheDocument()
  expect(screen.getByLabelText("邮箱")).toBeDisabled()
  expect(await screen.findByText(/资料待补：性别、出生日期/)).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await screen.findByText(/操作未成功/)
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writeUrls).toEqual([`/api/v1/personnel-intake/people/${person}/pending-corrections`, `/api/v1/personnel-intake/people/${person}/pending-corrections`])
  expect(writes[0]).toEqual({ full_name: "Passport Person", identity_number: canonical, document_type: documentType, document_issuing_country: null,
    phone: null, sex: null, date_of_birth: null, guardians: [], expected_version: 4, reason: "真实资料核对", idempotency_key: expect.any(String) })
  expect(writes[1]).toEqual(writes[0])
  expect(screen.queryByText("private server data")).not.toBeInTheDocument()
})

it("requires explicit trusted-channel identity confirmation before sending an authorized pending email correction", async () => {
  pendingMetadata(); current = { ...current, can_edit_activation_email: true }
  show(); await fillPending()
  fireEvent.change(screen.getByLabelText("邮箱"), { target: { value: "corrected@example.test" } })
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  expect(writes).toHaveLength(0)
  await userEvent.click(screen.getByRole("checkbox", { name: "已通过可信渠道核实本人及邮箱更正" }))
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toMatchObject({ email: "corrected@example.test", identity_confirmed: true, expected_version: 4, reason: "真实资料核对" })
})

it("pending draft survives route return and metadata refocus without rebasing its approved version", async () => {
  pendingMetadata(); post = async () => json({}, 500)
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await screen.findByText(/操作未成功/)
  view.rerender(<QueryClientProvider client={view.client}><p>其他页面</p></QueryClientProvider>)
  current = { ...current, profile_version: 8 }
  view.rerender(view.tree())
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  expect(screen.getByLabelText("资料更正原因")).toHaveValue("真实资料核对")
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-editor"] }) })
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
})

it.each(["network", "500"])("a fresh editor-context %s failure after route return preserves pending facts/version/retry/conflict while blocked", async (failure) => {
  pendingMetadata(); post = async () => json({ code: "personnel_intake_version_conflict" }, 409)
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await screen.findByText(/版本冲突/)
  const draftKey = ["assisted-editor-draft", auth.account, person]
  const saved = view.client.getQueryData(draftKey)
  view.rerender(<QueryClientProvider client={view.client}><p>其他页面</p></QueryClientProvider>)
  await waitFor(() => expect(view.client.getQueryData(["assisted-editor", auth.account, person])).toBeUndefined())
  const original = fetcher.getMockImplementation()!
  let fail = true
  fetcher.mockImplementation(async (url, init) => {
    if (fail && String(url).endsWith("editor-context")) {
      if (failure === "network") throw new TypeError("Synthetic network failure")
      return json({ code: "unavailable" }, 500)
    }
    return original(url, init)
  })
  view.rerender(view.tree())
  await waitFor(() => expect(view.client.getQueryState(["assisted-editor", auth.account, person])?.status).toBe("error"))
  expect(view.client.getQueryData(draftKey)).toEqual(saved)
  expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument()
  expect(writes).toHaveLength(1)
  fail = false
  await userEvent.click(await screen.findByRole("button", { name: "重新核实管理权限" }))
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  expect(screen.getByLabelText("资料更正原因")).toHaveValue("真实资料核对")
  expect(screen.getByRole("button", { name: "提交待补更正" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "重新读取最新管理信息" }))
  await userEvent.click(await screen.findByRole("button", { name: "已核对，使用最新版本" }))
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1]).toEqual(writes[0])
})

it("server housing-sex conflict remains blocked with a safe single-cause explanation and no workaround", async () => {
  pendingMetadata(); post = async () => json({ code: "pending_housing_sex_conflict", detail: "untrusted private identity" }, 409)
  show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  expect(await screen.findByText(/已有计划或当前住宿，不能在此更改已知性别/)).toBeVisible()
  expect(screen.getByRole("button", { name: "提交待补更正" })).toBeDisabled()
  expect(screen.queryByText("untrusted private identity")).not.toBeInTheDocument()
  expect(writes).toHaveLength(1)
})

it("a stale pending approval stays blocked across route return until an explicit latest-version review", async () => {
  pendingMetadata(); post = async () => json({ code: "personnel_intake_version_conflict" }, 409)
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await screen.findByText(/版本冲突/)
  view.rerender(<QueryClientProvider client={view.client}><p>其他页面</p></QueryClientProvider>)
  current = { ...current, profile_version: 8 }
  view.rerender(view.tree())
  expect(await screen.findByLabelText("护照号码")).toHaveValue("AB123456")
  expect(screen.getByRole("button", { name: "提交待补更正" })).toBeDisabled()
  expect(writes).toHaveLength(1)
  await userEvent.click(screen.getByRole("button", { name: "重新读取最新管理信息" }))
  await userEvent.click(await screen.findByRole("button", { name: "已核对，使用最新版本" }))
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[0].expected_version).toBe(4)
  expect(writes[1].expected_version).toBe(8)
  expect(writes[1].idempotency_key).not.toBe(writes[0].idempotency_key)
})

it.each([403, 404])("pending mutation denial %s clears the draft and cached retry evidence", async (status) => {
  pendingMetadata(); post = async () => json({}, status)
  const view = show(); await fillPending()
  await userEvent.click(screen.getByRole("button", { name: "提交待补更正" }))
  await waitFor(() => expect(screen.queryByDisplayValue("AB123456")).not.toBeInTheDocument())
  expect(view.client.getQueryData(["assisted-editor-draft", auth.account, person])).toBeUndefined()
})

it("protected activation email cannot be edited or cleared through ordinary correction", async () => {
  current = { ...metadata, can_edit_activation_email: false }
  show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  expect(screen.getByLabelText("邮箱")).toBeDisabled()
  expect(screen.getByLabelText("邮箱")).toHaveValue("")
  expect(screen.getByText(/请管理员在认领审核中撤销邮箱绑定/)).toBeVisible()
  await fillAdult()
  fireEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).not.toHaveProperty("email")
  expect(writes[0]).toMatchObject({ full_name: "测试本人", expected_version: 4 })
})

it("a revoked email-edit capability is applied to an existing draft without reusing the old email", async () => {
  const { client } = show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  current = { ...metadata, can_edit_activation_email: false }
  await act(async () => { await client.refetchQueries({ queryKey: ["assisted-editor"] }) })
  await waitFor(() => expect(screen.getByLabelText("邮箱")).toBeDisabled())
  expect(screen.getByLabelText("邮箱")).toHaveValue("")
  fireEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).not.toHaveProperty("email")
})

it("honors each metadata allowed action and never renders unauthorized editors", async () => {
  current = { ...metadata, allowed_actions: [] }
  show(true)
  await waitFor(() => expect(fetcher).toHaveBeenCalled())
  expect(screen.queryByRole("button", { name: "更正本人及监护资料" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "变更负责教练" })).not.toBeInTheDocument()
})

it("starts blank instead of prefilling masks and sends complete facts with profile version and stable retry UUID", async () => {
  post = async () => json({ detail: "server secret must not leak" }, 500)
  show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(screen.getByLabelText("身份证")).toHaveValue("")
  expect(screen.queryByLabelText("人员身份")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("所属队伍")).not.toBeInTheDocument()
  await fillAdult()
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await screen.findByText(/操作未成功/)
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[0]).toEqual({ full_name: "测试本人", identity_number: "110105199001021238", phone: "13800000000", email: "adult@example.test", sex: "male", date_of_birth: "1990-01-02", guardians: [], expected_version: 4, reason: "资料核对", idempotency_key: expect.stringMatching(/^[\da-f]{8}-[\da-f-]{27}$/i) })
  expect(writes[1]).toEqual(writes[0])
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "调整姓名" } })
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(3))
  expect(writes[2].idempotency_key).not.toBe(writes[1].idempotency_key)
  expect(screen.queryByText("server secret must not leak")).not.toBeInTheDocument()
  expect(fetcher.mock.calls.filter(([url]) => !String(url).endsWith("editor-context") && !String(url).endsWith("corrections"))).toHaveLength(0)
})

it("does not rebase or lose an open draft on focus; 409 requires explicit metadata review before retry", async () => {
  post = async () => json({ detail: "conflict" }, 409)
  const { client } = show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  current = { ...metadata, profile_version: 8 }
  await act(async () => { await client.refetchQueries({ queryKey: ["assisted-editor"] }) })
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await screen.findByText(/版本冲突/)
  expect(writes[0].expected_version).toBe(4)
  expect(screen.getByRole("button", { name: "提交更正" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "重新读取最新管理信息" }))
  await userEvent.click(await screen.findByRole("button", { name: "已核对，使用最新版本" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(2))
  expect(writes[1].expected_version).toBe(8)
  expect(writes[1].idempotency_key).not.toBe(writes[0].idempotency_key)
})

it("clears private draft immediately on account switch and ignores a late mutation completion", async () => {
  let resolve!: (response: Response) => void
  post = () => new Promise((done) => { resolve = done })
  const view = show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  const submittedSignal = fetcher.mock.calls.find(([, init]) => init?.method === "POST")?.[1]?.signal
  auth.account = "10000000-0000-4000-8000-000000000002"
  view.rerender(view.tree())
  expect(screen.queryByDisplayValue("测试本人")).not.toBeInTheDocument()
  expect(submittedSignal?.aborted).toBe(true)
  await act(async () => resolve(json({ request_id: null, person_id: person, approval_id: coach })))
  expect(screen.queryByText("资料更正成功。")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("测试本人")).not.toBeInTheDocument()
})

it("clears an open draft and coach choices after metadata revalidation revokes access", async () => {
  const view = show(true)
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  fetcher.mockImplementation(async () => json({ detail: "denied" }, 403))
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-editor"] }) })
  await waitFor(() => expect(screen.queryByDisplayValue("测试本人")).not.toBeInTheDocument())
  expect(screen.queryByText("测试教练")).not.toBeInTheDocument()
  expect(view.client.getQueryData(["assisted-editor", auth.account, person])).toBeFalsy()
})

it("keeps a draft through a transient focus revalidation failure but blocks writes until permissions are rechecked", async () => {
  const view = show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  fetcher.mockImplementationOnce(async () => json({ detail: "unavailable" }, 503))
  await act(async () => { await view.client.refetchQueries({ queryKey: ["assisted-editor"] }) })
  await waitFor(() => expect(screen.getByRole("button", { name: "提交更正" })).toBeDisabled())
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
  await userEvent.click(screen.getByRole("button", { name: "重新核实管理权限" }))
  await waitFor(() => expect(screen.getByRole("button", { name: "提交更正" })).toBeEnabled())
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
})

it("re-enters complete minor and guardian facts while guardian email stays optional", async () => {
  show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105201201020029" } })
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "2012-01-02" } })
  await userEvent.selectOptions(screen.getByLabelText("性别"), "female")
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(screen.getByText("未成年人至少需要一名成年监护人")).toBeVisible()
  expect(writes).toHaveLength(0)
  await userEvent.click(screen.getByRole("button", { name: "添加监护人" }))
  for (const [label, value] of [["监护人姓名", "测试监护"], ["监护人身份证", "110105199001021238"], ["监护人出生日期", "1990-01-02"], ["监护人手机号", "13800000000"], ["与本人关系", "父亲"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  await userEvent.selectOptions(screen.getByLabelText("监护人性别"), "male")
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toMatchObject({ identity_number: "110105201201020029", sex: "female", date_of_birth: "2012-01-02", expected_version: 4, guardians: [{ full_name: "测试监护", identity_number: "110105199001021238", sex: "male", date_of_birth: "1990-01-02", phone: "13800000000", email: null, relationship: "父亲", is_primary: true }] })
  expect(writes[0]).not.toHaveProperty("kind")
  expect(writes[0]).not.toHaveProperty("team_id")
  expect(writes[0]).not.toHaveProperty("coach_person_ids")
})

it("aborts account-scoped metadata and ignores late private choices after switching accounts", async () => {
  let resolve!: (response: Response) => void
  fetcher.mockImplementationOnce(() => new Promise((done) => { resolve = done }))
  const view = show(true)
  await waitFor(() => expect(fetcher).toHaveBeenCalled())
  const oldSignal = fetcher.mock.calls[0][1]?.signal
  auth.account = "10000000-0000-4000-8000-000000000002"
  current = { ...metadata, allowed_actions: [] }
  view.rerender(view.tree())
  expect(oldSignal?.aborted).toBe(true)
  await act(async () => resolve(json({ ...metadata, eligible_coaches: [{ id: coach, label: "私有教练姓名" }] })))
  expect(screen.queryByText("私有教练姓名")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "更正本人及监护资料" })).not.toBeInTheDocument()
  await waitFor(() => expect(view.client.getQueryData(["assisted-editor", "10000000-0000-4000-8000-000000000001", person])).toBeUndefined())
})

it("requires a correction reason and prevents duplicate actions while its request is pending", async () => {
  let resolve!: (response: Response) => void
  post = () => new Promise((done) => { resolve = done })
  show()
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  await fillAdult()
  fireEvent.change(screen.getByLabelText("资料更正原因"), { target: { value: "  " } })
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(screen.getByText(/请填写 1 至 500 字/)).toBeVisible()
  expect(writes).toHaveLength(0)
  fireEvent.change(screen.getByLabelText("资料更正原因"), { target: { value: "资料核对" } })
  await userEvent.dblClick(screen.getByRole("button", { name: "提交更正" }))
  expect(writes).toHaveLength(1)
  expect(screen.getByLabelText("姓名")).toBeDisabled()
  await act(async () => resolve(json({ request_id: null, person_id: person, approval_id: coach })))
  expect(await screen.findByText("资料更正成功。")).toBeVisible()
})

it("coach changes show live choices, permit removing unavailable responsibility, require reason and explicit confirmation", async () => {
  current = { ...metadata, current_coach_person_ids: [unavailable] }
  post = async () => new Response(null, { status: 204 })
  show(true)
  await userEvent.click(await screen.findByRole("button", { name: "变更负责教练" }))
  await userEvent.click(screen.getByLabelText("测试教练"))
  await userEvent.type(screen.getByLabelText("教练变更原因"), "  职责调整  ")
  await userEvent.click(screen.getByLabelText("确认按当前选择变更负责教练"))
  expect(screen.getByRole("button", { name: "确认变更教练" })).toBeDisabled()
  await userEvent.click(screen.getByRole("button", { name: "移除不可用教练 1" }))
  expect(screen.getByRole("button", { name: "确认变更教练" })).toBeDisabled()
  await userEvent.click(screen.getByLabelText("确认按当前选择变更负责教练"))
  await userEvent.click(screen.getByRole("button", { name: "确认变更教练" }))
  await waitFor(() => expect(writes).toHaveLength(1))
  expect(writes[0]).toEqual({ coach_person_ids: [coach], expected_version: 4, reason: "职责调整" })
  expect(screen.getByLabelText("教练变更原因")).toHaveAttribute("maxlength", "200")
})
