import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { AuthProvider } from "../auth/AuthProvider"
import { RegistrationApplicationsRoute, RegistrationProcessingPage } from "../registration/RegistrationProcessingPage"
import { ClaimReviewPage } from "./ClaimReviewPage"
import { claimAdminKeys } from "./adminApi"
import type { ClaimDetail } from "./adminApi"

const id = "30000000-0000-4000-8000-000000000001"
const personId = "30000000-0000-4000-8000-000000000002"
const initial = {
  id, version: 1, status: "pending", masked_name: "张*", masked_email: "t***@example.test",
  verified_at: "2026-10-20T01:00:00Z", expires_at: "2026-10-21T01:00:00Z", allowed_actions: ["approve", "reject"],
  person_id: personId, applicant_masked_name: "张*", applicant_masked_identity: "1101**********1238", applicant_email: "test@example.test",
  profile_masked_name: "张*", profile_masked_identity: "1101**********1238", profile_masked_email: null,
  private_match_reason: null as string | null, same_target_pending_count: 2, can_approve: true,
}
let detail: typeof initial & Pick<Partial<ClaimDetail>, "applicant_document_type" | "applicant_issuing_country" | "applicant_completion" | "profile_completion">
let permissions: string[]
let canEnter: boolean
let failDecision: number | null
let detailDenied: number | null
let holdDetail: ((response: Response) => void) | null
let holdAuth: ((response: Response) => void) | null
let pauseDetail: boolean
let pauseAuth: boolean
let pauseReveal: boolean
let holdReveal: ((response: Response) => void) | null
const requests: Array<{ url: string; init?: RequestInit }> = []
const authBody = () => ({ account_id: "account-reviewer", person_id: "reviewer", full_name: "审核员", email: null, force_password_change: false, permissions, scoped_permissions: [] })
const json = (value: unknown, status = 200) => Response.json(value, { status })
beforeEach(() => {
  detail = { ...initial }; permissions = ["accounts.manage"]; canEnter = true; failDecision = null; detailDenied = null
  holdDetail = null; holdAuth = null; pauseDetail = false; pauseAuth = false; pauseReveal = false; holdReveal = null; requests.length = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input); requests.push({ url, init })
    if (url.endsWith("/auth/me")) return pauseAuth ? new Promise((resolve) => { holdAuth = resolve }) : json(authBody())
    if (url.endsWith("/auth/csrf")) return json({ csrf_token: "formal-csrf" })
    if (url.endsWith("/profile-claims/context")) return json({ can_enter: canEnter })
    if (url.includes("/profile-claims?")) {
      const item = { id: detail.id, version: detail.version, status: detail.status, masked_name: detail.masked_name,
        masked_email: detail.masked_email, verified_at: detail.verified_at, expires_at: detail.expires_at, allowed_actions: detail.allowed_actions }
      const page = Number(new URL(url, "http://localhost").searchParams.get("page"))
      return json({ items: [item], total: 42, pending_count: 3, page, page_size: 20 })
    }
    if (url.endsWith(`/profile-claims/${id}`)) {
      if (pauseDetail) return new Promise((resolve) => { holdDetail = resolve })
      return detailDenied ? json({ detail: "无权访问" }, detailDenied) : json(detail)
    }
    if (/\/profile-claims\/.+\/(approve|reject|revoke)$/.test(url)) {
      if (failDecision) return json({ detail: "操作冲突" }, failDecision)
      detail = { ...detail, version: detail.version + 1, status: url.endsWith("approve") ? "approved" : url.endsWith("reject") ? "rejected" : "revoked", allowed_actions: url.endsWith("approve") ? ["revoke"] : [], can_approve: false }
      return json(detail)
    }
    if (url.endsWith("/sensitive-reveal")) return pauseReveal ? new Promise((resolve) => { holdReveal = resolve }) : json({ value: "110105199001021238" })
    if (url.endsWith("/registration-admin/channel-settings")) return json({ phone_enabled: false, email_enabled: true, phone_signup_allowed_by_deployment: false, updated_at: null, request_id: "channels" })
    if (url.endsWith("/registration-admin/applications")) return json([])
    throw new Error(`Unexpected request: ${url}`)
  }))
})
afterEach(() => vi.unstubAllGlobals())

function setup(path = "/settings/registrations/claims") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><AuthProvider><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/" element={<h1>工作台</h1>} />
    <Route path="/settings/registrations" element={<RegistrationProcessingPage />}>
      <Route index element={<RegistrationApplicationsRoute />} />
      <Route path="claims" element={<ClaimReviewPage />} />
    </Route>
  </Routes></MemoryRouter></AuthProvider></QueryClientProvider>)
  return { client, ...view }
}
async function openDetail() { await userEvent.click(await screen.findByRole("button", { name: "查看认领详情" })); await screen.findByText("test@example.test") }
const decisionRequests = () => requests.filter(({ url }) => /\/(approve|reject|revoke)$/.test(url))

it("separates submitted passport facts from authorized old facts without treating unavailable old facts as missing", async () => {
  detail = { ...initial, applicant_document_type: "passport", applicant_issuing_country: "US",
    applicant_completion: { phone: "19990000001", sex: "female", date_of_birth: "2001-02-03", issuing_country: null, guardians: [] },
    profile_completion: null }
  setup(); await openDetail()
  const applicant = screen.getByRole("region", { name: "申请人提交的补充资料" })
  expect(within(applicant).getByText("19990000001")).toBeVisible()
  expect(within(applicant).getByText("2001-02-03")).toBeVisible()
  expect(screen.getByText("护照")).toBeVisible()
  expect(screen.getByText("US")).toBeVisible()
  const previous = screen.getByRole("region", { name: "已获授权的原档案补充资料" })
  expect(within(previous).getByText(/不可用.*不代表原档案缺少资料/)).toBeVisible()
  expect(within(previous).queryByText("未提供")).not.toBeInTheDocument()
  expect(screen.getByRole("checkbox", { name: /已人工核对/ })).not.toBeChecked()
  expect(decisionRequests()).toHaveLength(0)
})

it.each([
  ["completion_missing", "补充资料尚不完整"], ["completion_conflicts", "补充资料与原档案存在冲突"], ["completion_invalid", "补充资料未通过有效性校验"],
])("explains private %s and displays old authorized facts separately", async (reason, message) => {
  detail = { ...initial, can_approve: false, private_match_reason: reason,
    applicant_document_type: "cn_id", applicant_issuing_country: null,
    applicant_completion: { phone: "19990000001", sex: null, date_of_birth: null, issuing_country: null, guardians: [] },
    profile_completion: { phone: "19990000004", sex: "male", date_of_birth: "2000-01-02", issuing_country: null, guardians: [] } }
  setup(); await openDetail()
  expect(screen.getByText(new RegExp(message))).toBeVisible()
  expect(within(screen.getByRole("region", { name: "已获授权的原档案补充资料" })).getByText("19990000004")).toBeVisible()
  expect(within(screen.getByRole("region", { name: "申请人提交的补充资料" })).queryByText("19990000004")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "批准认领" })).toBeDisabled()
})

it("keeps distinct nonempty guardian identity/contact/relationship facts confined to each comparison region", async () => {
  const applicantGuardian = { full_name: "申请监护人", identity_number: "110105198001020015", phone: "13800000011", email: "applicant-guardian@example.test", relationship: "父亲", sex: "male" as const, date_of_birth: "1980-01-02", is_primary: true }
  const originalGuardian = { full_name: "原档监护人", identity_number: "110105198201020027", phone: "13800000022", email: "original-guardian@example.test", relationship: "母亲", sex: "female" as const, date_of_birth: "1982-01-02", is_primary: false }
  detail = { ...initial,
    applicant_completion: { phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [applicantGuardian] },
    profile_completion: { phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [originalGuardian] } }
  setup(); await openDetail()
  const applicant = within(screen.getByRole("region", { name: "申请人提交的补充资料" }))
  const original = within(screen.getByRole("region", { name: "已获授权的原档案补充资料" }))
  for (const value of ["申请监护人", "110105198001020015", "13800000011", "applicant-guardian@example.test", "父亲", "1980-01-02"]) {
    expect(applicant.getByText(value)).toBeVisible()
    expect(original.queryByText(value)).not.toBeInTheDocument()
  }
  for (const value of ["原档监护人", "110105198201020027", "13800000022", "original-guardian@example.test", "母亲", "1982-01-02"]) {
    expect(original.getByText(value)).toBeVisible()
    expect(applicant.queryByText(value)).not.toBeInTheDocument()
  }
  expect(decisionRequests()).toHaveLength(0)
})

it.each([
  { verified: "2026-10-20T15:59:59Z", expires: "2026-10-20T16:00:00Z", verifyDate: "2026-10-20", expiryDate: "2026-10-21" },
  { verified: "2026-12-31T16:00:00Z", expires: "2027-01-01T16:00:00Z", verifyDate: "2027-01-01", expiryDate: "2027-01-02" },
])("renders verify and expiry as China business dates across UTC midnight $verified", async ({ verified, expires, verifyDate, expiryDate }) => {
  detail = { ...initial, verified_at: verified, expires_at: expires }
  setup()
  const queue = await screen.findByRole("region", { name: "认领申请队列" })
  expect(within(queue).getByText(`邮箱验证：${verifyDate}`)).toBeVisible()
  expect(within(queue).getByText(`有效期：${expiryDate}`)).toBeVisible()
  await openDetail()
  const panel = screen.getByRole("region", { name: "认领申请详情" })
  expect(within(panel).getByText("邮箱验证日期").nextElementSibling).toHaveTextContent(verifyDate)
  expect(within(panel).getByText("有效期").nextElementSibling).toHaveTextContent(expiryDate)
  expect(screen.queryByText((text) => text.includes(verified) || text.includes(expires))).not.toBeInTheDocument()
})

it.each([
  { registration: true, claim: false }, { registration: false, claim: true }, { registration: false, claim: false },
])("test_registration_processing_tabs_have_independent_authority $registration/$claim", async ({ registration, claim }) => {
  permissions = [...(registration ? ["registration.review"] : []), ...(claim ? ["accounts.manage"] : [])]; canEnter = claim
  setup("/settings/registrations")
  if (!registration && !claim) { expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible(); return }
  expect(await screen.findByRole("navigation", { name: "注册处理分类" })).toBeVisible()
  await waitFor(() => expect(screen.queryByRole("link", { name: "档案认领" }) !== null).toBe(claim))
  expect(screen.queryByRole("link", { name: "注册申请" }) !== null).toBe(registration)
  if (claim && !registration) {
    expect(await screen.findByRole("heading", { name: "档案认领审核" })).toBeVisible()
    expect(requests.some(({ url }) => url.includes("registration-admin"))).toBe(false)
  }
})

it("test_claim_review_never_auto_confirms_identity and permits approval without note", async () => {
  setup(); await openDetail()
  expect(screen.getByRole("checkbox", { name: /已人工核对/ })).not.toBeChecked()
  expect(screen.getByRole("button", { name: "批准认领" })).toBeDisabled()
  expect(screen.getByText(/批准不会立即创建账号/)).toBeVisible()
  await userEvent.click(screen.getByRole("checkbox", { name: /已人工核对/ }))
  await userEvent.click(screen.getByRole("button", { name: "批准认领" }))
  await waitFor(() => expect(decisionRequests()).toHaveLength(1))
  expect(JSON.parse(String(decisionRequests()[0].init?.body))).toMatchObject({ expected_version: 1, identity_confirmed: true, note: null })
  expect(await screen.findByRole("button", { name: "撤销绑定" })).toBeVisible()
})

it("requires public rejection reason and explicit revoke reason plus confirmation", async () => {
  setup(); await openDetail()
  expect(screen.getByRole("button", { name: "拒绝申请" })).toBeDisabled()
  expect(screen.getByText(/勿填写他人隐私/)).toBeVisible()
  fireEvent.change(screen.getByLabelText("拒绝／撤销原因"), { target: { value: "资料需重新核对" } })
  await userEvent.click(screen.getByRole("button", { name: "拒绝申请" }))
  await waitFor(() => expect(decisionRequests()).toHaveLength(1))
  expect(JSON.parse(String(decisionRequests()[0].init?.body)).reason).toBe("资料需重新核对")
})

it("only allows revocation for a server-authorized approved binding", async () => {
  detail = { ...initial, status: "approved", allowed_actions: ["revoke"], can_approve: false }
  setup(); await openDetail()
  expect(screen.getByRole("button", { name: "撤销绑定" })).toBeDisabled()
  fireEvent.change(screen.getByLabelText("拒绝／撤销原因"), { target: { value: "绑定对象需复核" } })
  expect(screen.getByRole("button", { name: "撤销绑定" })).toBeDisabled()
  await userEvent.click(screen.getByRole("checkbox", { name: /确认撤销/ }))
  await userEvent.click(screen.getByRole("button", { name: "撤销绑定" }))
  await waitFor(() => expect(decisionRequests()[0].url).toMatch(/revoke$/))
})

it("retains operation key for a transient retry but rotates it after input changes", async () => {
  failDecision = 503; setup(); await openDetail()
  await userEvent.click(screen.getByRole("checkbox", { name: /已人工核对/ }))
  await userEvent.click(screen.getByRole("button", { name: "批准认领" })); await screen.findByRole("alert")
  await userEvent.click(screen.getByRole("button", { name: "批准认领" })); await waitFor(() => expect(decisionRequests()).toHaveLength(2))
  const first = JSON.parse(String(decisionRequests()[0].init?.body)).idempotency_key
  expect(JSON.parse(String(decisionRequests()[1].init?.body)).idempotency_key).toBe(first)
  fireEvent.change(screen.getByLabelText("批准备注（可选）"), { target: { value: "人工核对完成" } })
  await userEvent.click(screen.getByRole("button", { name: "批准认领" })); await waitFor(() => expect(decisionRequests()).toHaveLength(3))
  expect(JSON.parse(String(decisionRequests()[2].init?.body)).idempotency_key).not.toBe(first)
})

it("conceals private detail on focus and preserves filters and paging after background validation", async () => {
  detail = { ...initial, applicant_completion: { phone: "19990000001", sex: null, date_of_birth: null, issuing_country: null, guardians: [] },
    profile_completion: { phone: "19990000004", sex: null, date_of_birth: null, issuing_country: null, guardians: [] } }
  setup(); await screen.findByRole("button", { name: "查看认领详情" })
  fireEvent.change(screen.getByLabelText("认领状态"), { target: { value: "approved" } })
  await userEvent.click(await screen.findByRole("button", { name: "下一页" })); await openDetail()
  pauseAuth = true
  act(() => window.dispatchEvent(new Event("focus")))
  expect(screen.queryByText("test@example.test")).not.toBeInTheDocument()
  expect(screen.queryByText("19990000001")).not.toBeInTheDocument()
  expect(screen.queryByText("19990000004")).not.toBeInTheDocument()
  expect(screen.getByLabelText("认领状态")).toHaveValue("approved")
  await waitFor(() => expect(holdAuth).not.toBeNull())
  pauseAuth = false; await act(async () => holdAuth?.(json(authBody())))
  await waitFor(() => expect(screen.getByText(/第 2 页/)).toBeVisible())
  expect(screen.getByLabelText("认领状态")).toHaveValue("approved")
})

it("test_revoked_admin_or_sensitive_permission_hides_stale_detail_and_late_reply", async () => {
  permissions = ["accounts.manage", "people.identity.reveal"]
  const { client } = setup(); await openDetail()
  await userEvent.click(screen.getByRole("button", { name: "查看原档案完整证件号码" }))
  fireEvent.change(screen.getByLabelText("查看用途"), { target: { value: "人工核对认领资料" } })
  await userEvent.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("110105199001021238")).toBeVisible()
  pauseDetail = true
  await userEvent.click(screen.getByRole("button", { name: "查看认领详情" }))
  permissions = []; canEnter = false
  act(() => window.dispatchEvent(new Event("focus")))
  expect(screen.queryByText("110105199001021238")).not.toBeInTheDocument()
  expect(screen.queryByText("test@example.test")).not.toBeInTheDocument()
  await screen.findByRole("heading", { name: "工作台" })
  await act(async () => holdDetail?.(json(initial)))
  expect(screen.queryByText("test@example.test")).not.toBeInTheDocument()
  expect(client.getQueryCache().findAll({ queryKey: claimAdminKeys.privateRoot("account-reviewer") })).toHaveLength(0)
})

it.each([403, 404])("purges private data on detail denial %s without late restoration", async (status) => {
  const { client } = setup(); await openDetail()
  detailDenied = status
  await userEvent.click(screen.getByRole("button", { name: "查看认领详情" }))
  await screen.findByRole("alert")
  await waitFor(() => expect(screen.queryByText("test@example.test")).not.toBeInTheDocument())
  expect(client.getQueryCache().findAll({ queryKey: claimAdminKeys.privateRoot("account-reviewer") })).toHaveLength(0)
})

it("keeps masked detail available without reveal permission and blocks stale-version approval", async () => {
  failDecision = 409; setup(); await openDetail()
  expect(screen.getAllByText("1101**********1238").length).toBeGreaterThan(0)
  expect(screen.queryByRole("button", { name: "查看原档案完整证件号码" })).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("checkbox", { name: /已人工核对/ }))
  await userEvent.click(screen.getByRole("button", { name: "批准认领" }))
  expect(await screen.findByRole("alert")).toHaveTextContent(/版本|状态/)
  expect(screen.getByRole("button", { name: "批准认领" })).toBeDisabled()
})

it("loses only sensitive permission on focus without reviving a late plaintext reply", async () => {
  permissions = ["accounts.manage", "people.identity.reveal"]
  setup(); await openDetail(); pauseReveal = true
  await userEvent.click(screen.getByRole("button", { name: "查看原档案完整证件号码" }))
  fireEvent.change(screen.getByLabelText("查看用途"), { target: { value: "人工核对认领" } })
  await userEvent.click(screen.getByRole("button", { name: "确认查看" }))
  permissions = ["accounts.manage"]
  act(() => window.dispatchEvent(new Event("focus")))
  await waitFor(() => expect(screen.getByRole("button", { name: "查看认领详情" })).toBeEnabled())
  await act(async () => holdReveal?.(json({ value: "110105199001021238" })))
  expect(screen.queryByText("110105199001021238")).not.toBeInTheDocument()
  await openDetail()
  expect(screen.queryByRole("button", { name: "查看原档案完整证件号码" })).not.toBeInTheDocument()
  expect(screen.getAllByText("1101**********1238").length).toBeGreaterThan(0)
})

it("does not retain revealed plaintext across a successful approval", async () => {
  permissions = ["accounts.manage", "people.identity.reveal"]
  setup(); await openDetail()
  await userEvent.click(screen.getByRole("button", { name: "查看原档案完整证件号码" }))
  fireEvent.change(screen.getByLabelText("查看用途"), { target: { value: "人工核对认领" } })
  await userEvent.click(screen.getByRole("button", { name: "确认查看" }))
  await screen.findByText("110105199001021238")
  await userEvent.click(screen.getByRole("checkbox", { name: /已人工核对/ }))
  await userEvent.click(screen.getByRole("button", { name: "批准认领" }))
  await screen.findByRole("button", { name: "撤销绑定" })
  expect(screen.queryByText("110105199001021238")).not.toBeInTheDocument()
})

it("denies direct claim navigation to ordinary registration reviewers", async () => {
  permissions = ["registration.review"]; canEnter = false
  setup()
  expect(await screen.findByRole("heading", { name: "注册处理" })).toBeVisible()
  expect(requests.some(({ url }) => url.includes("/profile-claims"))).toBe(false)
})
