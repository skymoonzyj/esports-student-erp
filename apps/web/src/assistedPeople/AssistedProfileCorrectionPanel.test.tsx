import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { AssistedProfileCorrectionPanel } from "./AssistedProfileCorrectionPanel"
import { CoachResponsibilityPanel } from "./CoachResponsibilityPanel"

const auth = vi.hoisted(() => ({ account: "10000000-0000-4000-8000-000000000001" }))
vi.mock("../auth/AuthProvider", () => ({ useOptionalAuth: () => ({ status: "authenticated", user: { account_id: auth.account, permissions: ["people.update"] } }), useAuth: () => ({ status: "authenticated", user: { account_id: auth.account } }) }))
const person = "30000000-0000-4000-8000-000000000001"
const coach = "30000000-0000-4000-8000-000000000002"
const unavailable = "30000000-0000-4000-8000-000000000003"
const metadata = { person_id: person, kind: "both", profile_version: 4, current_coach_person_ids: [coach], eligible_coaches: [{ id: coach, label: "测试教练" }], allowed_actions: ["correct_profile", "replace_coaches"] }
const json = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } })
let current = metadata
let post: (body: Record<string, unknown>) => Promise<Response>
let writes: Record<string, unknown>[]
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>
beforeEach(() => {
  auth.account = "10000000-0000-4000-8000-000000000001"
  current = metadata; writes = []
  post = async () => json({ request_id: null, person_id: person, approval_id: coach })
  setCsrfToken("test")
  fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (String(url).endsWith("editor-context")) return json(current)
    if (init?.method === "POST" || init?.method === "PUT") {
      const body = JSON.parse(String(init.body)); writes.push(body); return post(body)
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
  await screen.findByLabelText("姓名")
  for (const [label, value] of [["姓名", "测试本人"], ["身份证", "110105199001021238"], ["手机号", "13800000000"], ["邮箱", "adult@example.test"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: "1990-01-02" } })
  await userEvent.selectOptions(screen.getByLabelText("性别"), "male")
  await userEvent.type(screen.getByLabelText("资料更正原因"), "资料核对")
}

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
  expect(await screen.findByLabelText("姓名")).toHaveValue("")
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
