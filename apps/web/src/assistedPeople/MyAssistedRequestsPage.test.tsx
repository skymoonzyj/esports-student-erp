import { QueryClient, QueryClientProvider, focusManager } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { afterEach, expect, it, vi } from "vitest"
import { AuthProvider, useAuth } from "../auth/AuthProvider"
import { MyAssistedRequestsPage } from "./MyAssistedRequestsPage"
import { AssistedRequestReviewPage } from "./AssistedRequestReviewPage"
import { AssistedDirectCreatePage } from "./AssistedDirectCreatePage"
import { useAssistedPeopleContext } from "./useAssistedPeopleContext"

const account = "30000000-0000-4000-8000-000000000001"
const id = "30000000-0000-4000-8000-000000000002"
const coach = "30000000-0000-4000-8000-000000000003"
const context = { can_submit: true, can_review: true, can_direct_create: true, can_direct_create_pending: false, can_read_managed: false, allowed_team_ids: [], eligible_coaches: [{ id: coach, label: "负责教练" }], eligible_teams: [] }
const person = { kind: "student", full_name: "测试人员", identity_number: "11010519491231002X", phone: "13800138000", email: "person@example.com", sex: "female", date_of_birth: "1949-12-31", guardians: [], coach_person_ids: [coach], team_id: null }
const item = { id, version: 2, status: "returned", kind: "student", allowed_actions: ["resubmit", "withdraw"], approved_person_id: null, submitted_by: { id: account, label: "提交人甲" } }
const detail = { ...item, scoped_payload: person, returned_reason: "请核对", profile_version: null }
type Handler = (path: string, init: RequestInit) => Response | Promise<Response>
function install(handler?: Handler, caps = context) {
  const calls: Array<{ path: string; init: RequestInit }> = []
  vi.stubGlobal("fetch", vi.fn(async (input, init: RequestInit = {}) => {
    const path = String(input)
    calls.push({ path, init })
    if (path.endsWith("/auth/me")) return Response.json({ account_id: account, person_id: account, full_name: "测试管理员", email: null, force_password_change: false, permissions: [] })
    if (path.endsWith("/auth/csrf")) return Response.json({ csrf_token: "test-csrf" })
    if (handler) return handler(path, init)
    if (path.endsWith("/context")) return Response.json(caps)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 21, page: 1, page_size: 20 })
    return Response.json(detail)
  }))
  return calls
}
function SwitchAccount() { const auth = useAuth(); return <button onClick={() => void auth.login("other", "password")}>切换账号</button> }
function mount(page = <MyAssistedRequestsPage />) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const result = render(<QueryClientProvider client={client}><AuthProvider><SwitchAccount />{page}</AuthProvider></QueryClientProvider>)
  return { ...result, client }
}
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); focusManager.setFocused(undefined) })

it.each(["mine", "review"])("reads an approved pending staff passport in the %s workspace without activating or editing it", async (view) => {
  const staff = { ...item, kind: "staff", status: "approved", approved_person_id: id, allowed_actions: [] }
  const calls = install((path) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [staff], total: 1, page: 1, page_size: 20 })
    return Response.json({ ...staff, returned_reason: null, profile_version: 2, scoped_payload: {
      material_version: 2, kind: "staff", full_name: "Passport Person", identity_number: "Ab123456", document_type: "passport", document_issuing_country: null,
      phone: null, email: null, sex: null, date_of_birth: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending",
    } })
  })
  mount(view === "mine" ? <MyAssistedRequestsPage /> : <AssistedRequestReviewPage />)
  await userEvent.click(await screen.findByRole("button", { name: "查看申请 1" }))
  expect(await screen.findByText("Passport Person")).toBeVisible()
  expect(screen.getByText("年龄待补")).toBeVisible()
  expect(screen.getByText("Ab123456")).toBeVisible()
  expect(screen.getByText(/已通过仅表示建档审批，不代表账号已激活/)).toBeVisible()
  expect(screen.getByText("性别待补，暂不能办理实际入住")).toBeVisible()
  expect(screen.queryByText("成年人", { exact: true })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /提交申请|审核通过|退回修改|撤回申请|提交待补更正/ })).not.toBeInTheDocument()
  expect(calls.some((call) => call.init.method === "POST")).toBe(false)
  expect(calls.some((call) => call.path.endsWith("/readiness"))).toBe(false)
})

it("a version-2 payload cannot enter strict resubmit or review mutation paths even with legacy action strings", async () => {
  const pending = { ...item, kind: "staff", status: "returned", allowed_actions: ["resubmit", "withdraw", "approve", "return"] }
  install((path) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [pending], total: 1, page: 1, page_size: 20 })
    return Response.json({ ...pending, returned_reason: null, profile_version: 2, scoped_payload: {
      material_version: 2, kind: "staff", full_name: "Passport Person", identity_number: "Ab123456", document_type: "passport", document_issuing_country: null,
      phone: null, email: null, sex: null, date_of_birth: null, guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending",
    } })
  })
  mount()
  await userEvent.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await screen.findByText("Passport Person")
  expect(screen.queryByRole("button", { name: /提交申请|撤回申请/ })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument()
})

it("does not invent a missing issuing-country requirement for a pending CN identity", async () => {
  install((path) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [{ ...item, status: "approved", allowed_actions: [] }], total: 1, page: 1, page_size: 20 })
    return Response.json({ ...detail, status: "approved", allowed_actions: [], scoped_payload: { ...person, material_version: 2, document_type: "cn_id", document_issuing_country: null, team_allocation_status: "pending" } })
  })
  mount()
  await userEvent.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await screen.findByText("测试人员")
  expect(screen.queryByText("签发国家", { exact: true })).not.toBeInTheDocument()
  expect(screen.getByText("成年人", { exact: true })).toBeVisible()
})

it("shows absent approved email as not provided without altering the legacy detail", async () => {
  install((path) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [{ ...item, status: "approved", allowed_actions: [] }], total: 1, page: 1, page_size: 20 })
    return Response.json({ ...detail, status: "approved", allowed_actions: [], scoped_payload: { ...person, email: null } })
  })
  mount()
  fireEvent.click(await screen.findByRole("button", { name: "查看申请 1" }))
  expect(await screen.findByText("未提供")).toBeVisible()
  expect(screen.getByText("测试人员")).toBeVisible()
})

it.each([["2026-02-27", "未成年人"], ["2026-02-28", "成年人"], ["2026-03-01", "成年人"]])("labels a leap-day subject consistently on %s", async (today, label) => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  vi.setSystemTime(new Date(`${today}T04:00:00Z`))
  install((path) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    return Response.json({ ...detail, status: "approved", allowed_actions: [], scoped_payload: { ...person, date_of_birth: "2008-02-29" } })
  })
  mount()
  fireEvent.click(await screen.findByRole("button", { name: "查看申请 1" }))
  expect(await screen.findByText(label, { exact: true })).toBeVisible()
})

it("loads only own authorized requests without global people APIs and sends server filters before pagination", async () => {
  const calls = install()
  mount()
  await screen.findByText("共 21 条")
  expect(calls.find((call) => call.path.includes("/requests?"))?.path).toContain("view=mine")
  expect(calls.some((call) => /\/people|\/teams/.test(call.path))).toBe(false)
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText("状态筛选"), "returned")
  await user.selectOptions(screen.getByLabelText("身份筛选"), "student")
  await user.click(screen.getByRole("button", { name: "下一页" }))
  await waitFor(() => expect(calls.some((call) => call.path.includes("page=2") && call.path.includes("status=returned") && call.path.includes("kind=student"))).toBe(true))
})

it("mounts returned detail before editing and preserves draft on focus and conflict", async () => {
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    if (init.method === "POST") return Response.json({ code: "personnel_intake_version_conflict", detail: "private" }, { status: 409 })
    return Response.json(detail)
  })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  const name = await screen.findByLabelText("姓名")
  expect(name).toHaveValue("测试人员")
  await user.clear(name); await user.type(name, "保留草稿")
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(await screen.findByText(/资料版本或状态已变化/)).toBeVisible()
  expect(name).toHaveValue("保留草稿")
  const submit = calls.find((call) => call.path.endsWith("/resubmit"))
  expect(JSON.parse(String(submit?.init.body))).toMatchObject({ expected_version: 2, full_name: "保留草稿" })
  expect(calls.some((call) => call.path.endsWith("/direct-create"))).toBe(false)
})

it("review authority works independently of submit permission and approve needs no reason", async () => {
  const reviewItem = { ...item, status: "pending", allowed_actions: ["approve", "return"] }
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json({ ...context, can_submit: false })
    if (path.includes("/requests?")) return Response.json({ items: [reviewItem], total: 1, page: 1, page_size: 20 })
    if (init.method === "POST") return Response.json({ request_id: id, person_id: id, approval_id: id })
    return Response.json({ ...detail, ...reviewItem })
  })
  mount(<AssistedRequestReviewPage />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  expect(await screen.findByText("person@example.com")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "审核通过" }))
  await waitFor(() => expect(calls.some((call) => call.path.endsWith("/approve"))).toBe(true))
  expect(JSON.parse(String(calls.find((call) => call.path.endsWith("/approve"))?.init.body))).toEqual({ expected_version: 2 })
  expect(calls.some((call) => call.path.includes("view=review"))).toBe(true)
})

it("requires a trimmed return reason and honors server action restrictions for mixed-role self submissions", async () => {
  const calls = install()
  mount(<AssistedRequestReviewPage />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await screen.findByText("person@example.com")
  expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "退回修改" })).not.toBeInTheDocument()
  expect(calls.some((call) => call.init.method === "POST")).toBe(false)
})

it("hides private form and clears cached detail when context is revoked on focus", async () => {
  let revoked = false
  install((path) => {
    if (path.endsWith("/context")) return revoked ? Response.json({ code: "forbidden" }, { status: 403 }) : Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    return Response.json(detail)
  })
  const { client } = mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await screen.findByLabelText("姓名")
  revoked = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument())
  expect(JSON.stringify(client.getQueryData(["assisted-people", account, "detail", id])) ?? "").not.toContain("person@example.com")
})

async function fillAdult() {
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("姓名"), person.full_name)
  await user.type(screen.getByLabelText("身份证"), person.identity_number)
  await user.type(screen.getByLabelText("手机号"), person.phone)
  await user.type(screen.getByLabelText("邮箱"), person.email)
  await user.selectOptions(screen.getByLabelText("性别"), "female")
  fireEvent.change(screen.getByLabelText("出生日期"), { target: { value: person.date_of_birth } })
  await user.click(screen.getByLabelText("负责教练"))
  return user
}

it("requires a return reason, trims it, and refreshes current detail and list after success", async () => {
  let returned = false
  const reviewItem = { ...item, status: "pending", allowed_actions: ["approve", "return"] }
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (init.method === "POST") { returned = true; return Response.json(detail) }
    if (path.includes("/requests?")) return Response.json({ items: [returned ? item : reviewItem], total: 1, page: 1, page_size: 20 })
    return Response.json(returned ? detail : { ...detail, ...reviewItem })
  })
  mount(<AssistedRequestReviewPage />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await user.click(await screen.findByRole("button", { name: "退回修改" }))
  expect(await screen.findByText("请填写退回原因。")).toBeVisible()
  expect(calls.some((call) => call.init.method === "POST")).toBe(false)
  await user.type(screen.getByLabelText("退回原因"), "  核实监护关系  ")
  await user.click(screen.getByRole("button", { name: "退回修改" }))
  await waitFor(() => expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument())
  expect(JSON.parse(String(calls.find((call) => call.path.endsWith("/return"))?.init.body))).toEqual({ expected_version: 2, reason: "核实监护关系" })
  expect(calls.filter((call) => call.path.includes("/requests?")).length).toBeGreaterThan(1)
})

it("ordinary submission validates the real form and never becomes direct-create for an administrator", async () => {
  const calls = install()
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "新建申请" }))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByLabelText("姓名")).toHaveAttribute("aria-invalid", "true")
  expect(calls.some((call) => call.init.method === "POST")).toBe(false)
  await fillAdult()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await waitFor(() => expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument())
  expect(JSON.parse(String(calls.find((call) => call.init.method === "POST")?.init.body))).toEqual(person)
  expect(calls.find((call) => call.init.method === "POST")?.path).toBe("/api/v1/personnel-intake/requests")
})

it("direct-create confirms explicitly, reuses retry key, changes key for edited payload, and starts a fresh operation", async () => {
  let writes = 0
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json({ ...context, can_submit: false })
    if (init.method === "POST") {
      writes += 1
      return writes < 3 ? Response.json({ code: "unavailable" }, { status: 503 }) : Response.json({ request_id: null, person_id: id, approval_id: id })
    }
    throw new Error(`unexpected endpoint ${path}`)
  })
  mount(<AssistedDirectCreatePage />)
  await screen.findByLabelText("姓名")
  const user = await fillAdult()
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(await screen.findByText("请先确认管理建档操作。")).toBeVisible()
  expect(writes).toBe(0)
  await user.click(screen.getByLabelText("我已核对资料，确认直接创建已通过档案"))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await screen.findByText("操作未成功，请检查资料或稍后重试。")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await waitFor(() => expect(writes).toBe(2))
  await user.type(screen.getByLabelText("姓名"), "更正")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await screen.findByText("管理建档成功。")
  const bodies = calls.filter((call) => call.init.method === "POST").map((call) => JSON.parse(String(call.init.body)))
  expect(bodies[0].idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
  expect(bodies[1].idempotency_key).toBe(bodies[0].idempotency_key)
  expect(bodies[2].idempotency_key).not.toBe(bodies[0].idempotency_key)
  expect(bodies[0]).toMatchObject(person)
  expect(calls.filter((call) => call.init.method === "POST").every((call) => call.path.endsWith("/direct-create"))).toBe(true)
  await user.click(screen.getByRole("button", { name: "开始新的管理建档" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(screen.queryByText("操作成功。")).not.toBeInTheDocument()
  expect(screen.getByLabelText("我已核对资料，确认直接创建已通过档案")).not.toBeChecked()
  await fillAdult()
  await user.click(screen.getByLabelText("我已核对资料，确认直接创建已通过档案"))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await screen.findByText("管理建档成功。")
  const last = JSON.parse(String(calls.filter((call) => call.init.method === "POST").at(-1)?.init.body))
  expect(last.idempotency_key).not.toBe(bodies[2].idempotency_key)
})

it("clears sensitive detail cache on detail-only permission revocation", async () => {
  let revoked = false
  install((path) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    return revoked ? Response.json({ code: "personnel_intake_not_found" }, { status: 404 }) : Response.json(detail)
  })
  const { client } = mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await screen.findByLabelText("姓名")
  revoked = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByText(/详情无法访问/)
  expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument()
  expect(client.getQueryData(["assisted-people", account, "detail", id])).toBeUndefined()
})

it("account switch resets a memory draft and excludes a late old-account detail response", async () => {
  const other = "30000000-0000-4000-8000-000000000004"
  let resolveDetail!: (value: Response) => void
  let detailSignal: AbortSignal | null | undefined
  let current = account
  install((path, init) => {
    if (path.endsWith("/auth/login")) {
      current = other
      return Response.json({ user: { account_id: other, person_id: other, full_name: "账号乙", email: null, force_password_change: false, permissions: [] }, csrf_token: "other-csrf" })
    }
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: current === account ? [item] : [], total: current === account ? 1 : 0, page: 1, page_size: 20 })
    detailSignal = init.signal
    return new Promise<Response>((resolve) => { resolveDetail = resolve })
  })
  const { client } = mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "新建申请" }))
  await user.type(screen.getByLabelText("姓名"), "账号甲私密草稿")
  await user.click(screen.getByRole("button", { name: "查看申请 1" }))
  await screen.findByText("正在加载详情…")
  await user.click(screen.getByRole("button", { name: "切换账号" }))
  await screen.findByText("共 0 条")
  expect(detailSignal?.aborted).toBe(true)
  await act(async () => resolveDetail(Response.json(detail)))
  expect(screen.queryByText("person@example.com")).not.toBeInTheDocument()
  expect(client.getQueryData(["assisted-people", account, "detail", id])).toBeUndefined()
  await user.click(screen.getByRole("button", { name: "新建申请" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(client.getQueryData(["assisted-people", other, "context"])).toEqual(context)
})

it("shows minor and complete guardian facts using authorized assignment labels without edit controls", async () => {
  install((path) => {
    if (path.endsWith("/context")) return Response.json({ ...context, can_submit: false })
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    return Response.json({ ...detail, allowed_actions: [], scoped_payload: { ...person, identity_number: "110105201201020029", date_of_birth: "2012-01-02", guardians: [{ full_name: "测试监护人", identity_number: "110105199001021238", phone: "13800138000", email: null, relationship: "父亲", sex: "male", date_of_birth: "1990-01-02", is_primary: true }] } })
  })
  mount(<AssistedRequestReviewPage />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  expect(await screen.findByText("未成年人")).toBeVisible()
  expect(screen.getByText("测试监护人")).toBeVisible()
  expect(screen.getByText("父亲")).toBeVisible()
  expect(screen.getByText("负责教练", { selector: "dd" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "提交申请" })).not.toBeInTheDocument()
  expect(screen.queryByText(coach)).not.toBeInTheDocument()
})

it("denied submit/review capabilities do not fetch any request list even for an admin-named account", async () => {
  const calls = install(undefined, { ...context, can_submit: false, can_review: false })
  mount(<AssistedRequestReviewPage />)
  expect(await screen.findByText("无建档审核权限。")).toBeVisible()
  expect(calls.some((call) => call.path.includes("/requests"))).toBe(false)
  expect(screen.queryByRole("button", { name: "新建申请" })).not.toBeInTheDocument()
})

it("pending withdraw cannot duplicate, accepts expected_version, and refreshes to a noneditable withdrawn record", async () => {
  let finish!: (value: Response) => void
  let withdrawn = false
  const pending = { ...item, status: "pending", allowed_actions: ["withdraw"] }
  const withdrawnItem = { ...item, version: 3, status: "withdrawn", allowed_actions: [] }
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [withdrawn ? withdrawnItem : pending], total: 1, page: 1, page_size: 20 })
    if (init.method === "POST") return new Promise<Response>((resolve) => { finish = resolve })
    return Response.json({ ...detail, ...(withdrawn ? withdrawnItem : pending) })
  })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  const button = await screen.findByRole("button", { name: "撤回申请" })
  expect(screen.queryByRole("button", { name: "提交申请" })).not.toBeInTheDocument()
  await user.click(button)
  expect(button).toBeDisabled()
  await user.click(button)
  expect(calls.filter((call) => call.init.method === "POST")).toHaveLength(1)
  expect(JSON.parse(String(calls.find((call) => call.path.endsWith("/withdraw"))?.init.body))).toEqual({ expected_version: 2 })
  withdrawn = true
  await act(async () => finish(Response.json({ ...detail, ...withdrawnItem })))
  await waitFor(() => expect(screen.queryByRole("button", { name: "撤回申请" })).not.toBeInTheDocument())
})

it("server action refusal hides sensitive editing data and never reflects private error content", async () => {
  install((path, init) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    if (init.method === "POST") return Response.json({ code: "forbidden", detail: "sensitive person@example.com" }, { status: 403 })
    return Response.json(detail)
  })
  const { client } = mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await user.click(await screen.findByRole("button", { name: "提交申请" }))
  expect(await screen.findByText(/当前无权访问此建档资料/)).toBeVisible()
  expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument()
  expect(screen.queryByText(/sensitive person/)).not.toBeInTheDocument()
  expect(client.getQueryData(["assisted-people", account, "detail", id])).toBeUndefined()
})

it("keeps edited form and original version on authorized focus rechecks, and only explicit reload replaces it", async () => {
  let updated = false
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json({ ...context, eligible_coaches: [{ id: coach, label: updated ? "新教练标签" : "负责教练" }] })
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    if (init.method === "POST") return Response.json({ code: "version_conflict" }, { status: 409 })
    return Response.json(updated ? { ...detail, version: 3, scoped_payload: { ...person, full_name: "服务器新姓名" } } : detail)
  })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await user.clear(await screen.findByLabelText("姓名")); await user.type(screen.getByLabelText("姓名"), "未提交的编辑")
  updated = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByLabelText("新教练标签")
  expect(screen.getByLabelText("姓名")).toHaveValue("未提交的编辑")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await screen.findByText(/资料版本或状态已变化/)
  expect(JSON.parse(String(calls.find((call) => call.path.endsWith("/resubmit"))?.init.body)).expected_version).toBe(2)
  await user.click(screen.getByRole("button", { name: "重新载入最新资料（会替换当前草稿）" }))
  await waitFor(() => expect(screen.getByLabelText("姓名")).toHaveValue("服务器新姓名"))
})

it("captures the current version for each newly editable lifecycle across two return-resubmit cycles", async () => {
  let current = detail
  const submitted: Array<{ expected_version: number; full_name: string }> = []
  install((path, init) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [{ ...item, version: current.version, status: current.status, allowed_actions: current.allowed_actions }], total: 1, page: 1, page_size: 20 })
    if (path.endsWith("/resubmit")) {
      const payload = JSON.parse(String(init.body))
      submitted.push(payload)
      if (payload.expected_version !== current.version) return Response.json({ code: "version_conflict" }, { status: 409 })
      current = { ...current, version: current.version + 1, status: "pending", allowed_actions: ["withdraw"], returned_reason: "", scoped_payload: { ...current.scoped_payload, full_name: payload.full_name } }
      return Response.json(current)
    }
    return Response.json(current)
  })
  mount()
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await user.clear(await screen.findByLabelText("姓名")); await user.type(screen.getByLabelText("姓名"), "第一轮编辑")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  await waitFor(() => expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument())
  expect(screen.getByText("第一轮编辑")).toBeVisible()
  expect(submitted[0]).toMatchObject({ expected_version: 2, full_name: "第一轮编辑" })

  // Reviewer returns the current pending revision; return does not increment it.
  current = { ...current, status: "returned", allowed_actions: ["resubmit", "withdraw"], returned_reason: "再次核实", scoped_payload: { ...current.scoped_payload, full_name: "第二轮服务器姓名" } }
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByLabelText("姓名")).toHaveValue("第二轮服务器姓名")
  await user.clear(screen.getByLabelText("姓名")); await user.type(screen.getByLabelText("姓名"), "第二轮编辑")
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(submitted[1]).toMatchObject({ expected_version: 3, full_name: "第二轮编辑" })
  await waitFor(() => expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument())
  expect(screen.getByText("第二轮编辑")).toBeVisible()
  expect(screen.queryByText(/资料版本或状态已变化/)).not.toBeInTheDocument()
})

it("does not write sensitive drafts to browser storage or URL", async () => {
  install()
  mount()
  const storage = vi.spyOn(Storage.prototype, "setItem")
  const location = window.location.href
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "新建申请" }))
  await user.type(screen.getByLabelText("姓名"), "只在内存的敏感姓名")
  expect(storage).not.toHaveBeenCalled()
  expect(window.location.href).toBe(location)
  storage.mockRestore()
})

it("review submitter filter uses authorized name search, not a global account/people picker", async () => {
  const calls = install()
  mount(<AssistedRequestReviewPage />)
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText("提交人姓名筛选"), "甲")
  await waitFor(() => expect(calls.some((call) => new URL(call.path, "https://erp.example").searchParams.get("submitter_query") === "甲")).toBe(true))
  expect(screen.queryByRole("combobox", { name: /提交人/ })).not.toBeInTheDocument()
})

it("direct-create loses confirmation and sensitive draft when live capability is revoked", async () => {
  let revoked = false
  install((path) => {
    if (path.endsWith("/context")) return Response.json({ ...context, can_direct_create: !revoked })
    throw new Error(`unexpected endpoint ${path}`)
  })
  mount(<AssistedDirectCreatePage />)
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText("姓名"), "管理建档私密草稿")
  await user.click(screen.getByLabelText("我已核对资料，确认直接创建已通过档案"))
  revoked = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByText("无管理建档权限。")
  expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument()
  revoked = false
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  expect(await screen.findByLabelText("姓名")).toHaveValue("")
  expect(screen.getByLabelText("我已核对资料，确认直接创建已通过档案")).not.toBeChecked()
})

it("revoked direct-create in flight aborts transport and excludes its late success", async () => {
  let revoked = false
  let finish!: (value: Response) => void
  let mutationSignal: AbortSignal | null | undefined
  const calls = install((path, init) => {
    if (path.endsWith("/context")) return Response.json({ ...context, can_direct_create: !revoked })
    if (path.endsWith("/direct-create")) {
      mutationSignal = init.signal
      return new Promise<Response>((resolve) => { finish = resolve })
    }
    throw new Error(`unexpected endpoint ${path}`)
  })
  mount(<AssistedDirectCreatePage />)
  await screen.findByLabelText("姓名")
  const user = await fillAdult()
  await user.click(screen.getByLabelText("我已核对资料，确认直接创建已通过档案"))
  await user.click(screen.getByRole("button", { name: "提交申请" }))
  expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "正在提交…" }))
  expect(calls.filter((call) => call.init.method === "POST")).toHaveLength(1)
  revoked = true
  await act(async () => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await screen.findByText("无管理建档权限。")
  expect(mutationSignal?.aborted).toBe(true)
  await act(async () => finish(Response.json({ request_id: null, person_id: id, approval_id: id })))
  expect(screen.queryByText("管理建档成功。")).not.toBeInTheDocument()
  expect(screen.queryByText("操作成功。")).not.toBeInTheDocument()
})

it("unmounting another shared context subscriber does not cancel this page's authorized detail", async () => {
  let finish!: (value: Response) => void
  const detailSignals: Array<AbortSignal | null | undefined> = []
  install((path, init) => {
    if (path.endsWith("/context")) return Response.json(context)
    if (path.includes("/requests?")) return Response.json({ items: [item], total: 1, page: 1, page_size: 20 })
    detailSignals.push(init.signal)
    return new Promise<Response>((resolve) => { finish = resolve })
  })
  function Subscriber() { useAssistedPeopleContext(account); return null }
  function SharedConsumer() {
    const [subscribed, setSubscribed] = useState(true)
    return <>{subscribed ? <Subscriber /> : null}<button onClick={() => setSubscribed(false)}>移除另一权限订阅</button><MyAssistedRequestsPage /></>
  }
  mount(<SharedConsumer />)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "查看申请 1" }))
  await screen.findByText("正在加载详情…")
  await user.click(screen.getByRole("button", { name: "移除另一权限订阅" }))
  expect(detailSignals).toHaveLength(1)
  expect(detailSignals[0]?.aborted).toBe(false)
  await act(async () => finish(Response.json(detail)))
  expect(await screen.findByLabelText("姓名")).toHaveValue("测试人员")
})

it("direct create is separate, requires confirmation, and denied capability has no form", async () => {
  install(undefined, { ...context, can_direct_create: false })
  mount(<AssistedDirectCreatePage />)
  expect(await screen.findByText(/无管理建档权限/)).toBeVisible()
  expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument()
})
