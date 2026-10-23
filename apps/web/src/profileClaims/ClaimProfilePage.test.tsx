import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { ActivateProfilePage } from "../auth/ActivateProfilePage"
import { AuthProvider } from "../auth/AuthProvider"
import { ClaimProfilePage } from "./ClaimProfilePage"
import { clearProfileClaimSession } from "./api"

const started = () => ({ accepted: true, resend_available_at: new Date(Date.now() + 60_000).toISOString(), expires_at: new Date(Date.now() + 600_000).toISOString() })
let status = "waiting_review"
let fetcher: ReturnType<typeof vi.fn<typeof fetch>>
beforeEach(() => {
  status = "waiting_review"
  fetcher = vi.fn<typeof fetch>(async (url) => {
    if (String(url).endsWith("/start")) return Response.json(started())
    return Response.json({ status, reason: null })
  })
  vi.stubGlobal("fetch", fetcher)
})
afterEach(() => { clearProfileClaimSession(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers() })
function show() {
  const router = createMemoryRouter([
    { path: "/claim-profile", element: <ClaimProfilePage /> },
    { path: "/activate-profile", element: <ActivateProfilePage /> },
    { path: "/login", element: <h1>登录页面</h1> },
  ], { initialEntries: ["/claim-profile"] })
  render(<RouterProvider router={router} />)
  return router
}
function fill() {
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "测试本人" } })
  fireEvent.change(screen.getByLabelText("身份证"), { target: { value: "110105199001021238" } })
  fireEvent.change(screen.getByLabelText("本人邮箱"), { target: { value: "applicant@example.test" } })
}

it("keeps the public claim form closed until initial auth restore settles", async () => {
  let finishRestore!: (response: Response) => void
  fetcher.mockImplementation(async (url) => {
    if (String(url).endsWith("/auth/me")) return new Promise((resolve) => { finishRestore = resolve })
    if (String(url).endsWith("/profile-claims/start")) return Response.json(started())
    throw new Error(`Unexpected request: ${url}`)
  })
  const router = createMemoryRouter([
    { path: "/claim-profile", element: <ClaimProfilePage /> },
  ], { initialEntries: ["/claim-profile"] })
  render(<QueryClientProvider client={new QueryClient()}><AuthProvider><RouterProvider router={router} /></AuthProvider></QueryClientProvider>)

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith("/api/v1/auth/me", expect.anything()))
  expect(screen.queryByLabelText("姓名")).not.toBeInTheDocument()

  await act(async () => finishRestore(Response.json({ code: "invalid_session" }, { status: 401 })))
  fill()
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await screen.findByLabelText("验证码")
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
})
async function verify() {
  fill()
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await screen.findByLabelText("验证码")
  fireEvent.change(screen.getByLabelText("验证码"), { target: { value: "123456" } })
  fireEvent.click(screen.getByRole("button", { name: "验证邮箱并申请核对" }))
  await screen.findByText(/等待管理员核对/)
}

it("claim form survives mail app switch without persistence and clears on route departure", async () => {
  const storage = vi.spyOn(Storage.prototype, "setItem")
  const router = show()
  fill()
  fireEvent(window, new Event("blur")); fireEvent(document, new Event("visibilitychange")); fireEvent(window, new Event("focus"))
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
  expect(screen.getByLabelText("身份证")).toHaveValue("110105199001021238")
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("applicant@example.test")
  expect(storage).not.toHaveBeenCalled()
  expect(router.state.location).toMatchObject({ pathname: "/claim-profile", search: "", hash: "", state: null })
  let finish!: (response: Response) => void
  fetcher.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  const signal = fetcher.mock.calls[0][1]?.signal
  await act(() => router.navigate("/login"))
  expect(signal?.aborted).toBe(true)
  await act(async () => finish(Response.json(started())))
  await act(() => router.navigate("/claim-profile"))
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("")
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
})

it("claim approval does not show activated or request password; original activation starts with fresh proof", async () => {
  const router = show()
  await verify()
  expect(screen.queryByText("已激活", { exact: true })).not.toBeInTheDocument()
  expect(screen.queryByText("原姓名")).not.toBeInTheDocument()
  expect(document.querySelector('input[type="password"]')).toBeNull()
  status = "approved"
  fireEvent.click(screen.getByRole("button", { name: "查询审核状态" }))
  expect(await screen.findByText("待本人激活")).toBeVisible()
  expect(screen.queryByText("已激活")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("link", { name: "前往本人激活" }))
  await screen.findByRole("heading", { name: "激活已有档案" })
  expect(router.state.location).toMatchObject({ pathname: "/activate-profile", search: "", hash: "", state: null })
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("")
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(3)
})

it("session boundary clears draft and aborts requests independently of focus", async () => {
  let finish!: (response: Response) => void
  fetcher.mockImplementationOnce(() => new Promise((resolve) => { finish = resolve }))
  show(); fill()
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
  const signal = fetcher.mock.calls[0][1]?.signal
  act(() => clearProfileClaimSession())
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("")
  expect(signal?.aborted).toBe(true)
  await act(async () => finish(Response.json(started())))
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
})

it("honors Retry-After for 429 and does not automatically retry SMTP failures", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  fetcher.mockResolvedValueOnce(Response.json({ code: "rate_limited", detail: "private backend text" }, { status: 429, headers: { "Retry-After": "90" } }))
  show(); fill()
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await screen.findByRole("alert")
  expect(screen.getByRole("button", { name: /请求邮箱验证码.*90 秒/ })).toBeDisabled()
  expect(screen.queryByText("private backend text")).not.toBeInTheDocument()
  await act(async () => { await vi.advanceTimersByTimeAsync(90_000) })
  fetcher.mockResolvedValueOnce(Response.json({ code: "delivery_failed" }, { status: 503, headers: { "Retry-After": "60" } }))
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await screen.findByText(/邮件暂时无法发送/)
  expect(screen.getByLabelText("姓名")).toHaveValue("测试本人")
  expect(screen.getByRole("button", { name: /请求邮箱验证码.*60 秒/ })).toBeDisabled()
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(screen.getByRole("button", { name: "请求邮箱验证码" })).toBeEnabled()
})

it("expired private status requires fresh verification without resending or background polling", async () => {
  show(); await verify()
  fetcher.mockResolvedValueOnce(Response.json({ code: "profile_claim_session_expired" }, { status: 401 }))
  fireEvent.click(screen.getByRole("button", { name: "查询审核状态" }))
  expect(await screen.findByText(/会话已过期，请重新验证邮箱后查询/)).toBeVisible()
  expect(fetcher).toHaveBeenCalledTimes(3)
  expect(screen.queryByRole("button", { name: "查询审核状态" })).not.toBeInTheDocument()
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("applicant@example.test")
})

it("throttled status lookup respects Retry-After without preventing immediate initial email verification", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  show(); await verify()
  fetcher.mockResolvedValueOnce(Response.json({ code: "rate_limited" }, { status: 429, headers: { "Retry-After": "90" } }))
  fireEvent.click(screen.getByRole("button", { name: "查询审核状态" }))
  await screen.findByRole("alert")
  expect(screen.getByRole("button", { name: /查询审核状态/ })).toBeDisabled()
  fireEvent.click(screen.getByRole("button", { name: /查询审核状态/ }))
  expect(fetcher).toHaveBeenCalledTimes(3)
  await act(async () => { await vi.advanceTimersByTimeAsync(90_000) })
  expect(screen.getByRole("button", { name: /查询审核状态/ })).toBeEnabled()
})

it("a fresh start clears the old proof even when the replacement send fails", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  show(); await verify()
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
  fetcher.mockResolvedValueOnce(Response.json({ code: "delivery_failed" }, { status: 503 }))
  fireEvent.click(screen.getByRole("button", { name: "重新请求验证码" }))
  await screen.findByRole("alert")
  expect(screen.queryByText(/等待管理员核对/)).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /查询审核状态/ })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("applicant@example.test")
})

it("local ten-minute expiry hides old status and never polls or resends on return from email", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
  show(); await verify()
  await act(async () => { await vi.advanceTimersByTimeAsync(600_000) })
  fireEvent(window, new Event("focus")); fireEvent(document, new Event("visibilitychange"))
  expect(screen.getByRole("alert")).toHaveTextContent("会话已过期，请重新验证邮箱后查询")
  expect(screen.queryByRole("button", { name: "查询审核状态" })).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it.each([
  ["rejected", "申请未获批准"], ["expired", "申请已过期"], ["closed", "申请已关闭"],
])("renders terminal public state %s without creating an activation success", async (nextStatus, message) => {
  show(); await verify()
  status = nextStatus
  fireEvent.click(screen.getByRole("button", { name: "查询审核状态" }))
  expect(await screen.findByText(new RegExp(message))).toBeVisible()
  expect(screen.queryByRole("link", { name: "前往本人激活" })).not.toBeInTheDocument()
  expect(document.querySelector('input[type="password"]')).toBeNull()
})

it("collects passport and self-entered nullable facts without a hidden-profile fetch or CN derivation", async () => {
  const storage = vi.spyOn(Storage.prototype, "setItem")
  show(); fill()
  fireEvent.change(screen.getByLabelText("证件类型"), { target: { value: "passport" } })
  fireEvent.change(screen.getByLabelText("护照号码"), { target: { value: "  aBc123  " } })
  expect(screen.getByLabelText("性别（可暂缺）")).toHaveValue("")
  expect(screen.getByLabelText("出生日期（可暂缺）")).toHaveValue("")
  fireEvent.change(screen.getByLabelText("签发国家（两位代码，可暂缺）"), { target: { value: "us" } })
  fireEvent.change(screen.getByLabelText("联系电话（可暂缺）"), { target: { value: "13800000000" } })
  fireEvent(window, new Event("blur")); fireEvent(window, new Event("focus"))
  fireEvent.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await screen.findByLabelText("验证码")
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ full_name: "测试本人", email: "applicant@example.test",
    document_type: "passport", identity_number: "  aBc123  ", issuing_country: "US",
    completion: { phone: "13800000000", sex: null, date_of_birth: null, issuing_country: null, guardians: [] } })
  expect(storage).not.toHaveBeenCalled()
  act(() => clearProfileClaimSession())
  expect(screen.getByLabelText("联系电话（可暂缺）")).toHaveValue("")
  expect(screen.getByLabelText("证件类型")).toHaveValue("cn_id")
})

it("changing completion after verification discards proof and requires a new application", async () => {
  show(); await verify()
  fireEvent.change(screen.getByLabelText("性别（可暂缺）"), { target: { value: "female" } })
  expect(screen.queryByText(/等待管理员核对/)).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "查询审核状态" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledTimes(2)
  fireEvent.click(screen.getByRole("button", { name: "清空资料并重新开始" }))
  expect(screen.getByLabelText("姓名")).toHaveValue("")
  expect(screen.getByLabelText("性别（可暂缺）")).toHaveValue("")
})
