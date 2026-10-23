import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import { ActivateProfilePage } from "./ActivateProfilePage"

const ACCEPTED = "请求已受理；如未收到邮件，可能资料尚未获批或邮件发送失败，请稍后重试或联系管理员"

function json(body: unknown, status = 200, headers?: HeadersInit) {
  return new Response(JSON.stringify(body), { status, headers })
}

function accepted(accepted = true, waitSeconds = 60) {
  return json({
    accepted,
    resend_available_at: new Date(Date.now() + waitSeconds * 1000).toISOString(),
    expires_at: new Date(Date.now() + 600_000).toISOString(),
  })
}

function renderPage() {
  const router = createMemoryRouter([
    { path: "/activate-profile", element: <ActivateProfilePage /> },
    { path: "/login", element: <h1>登录页面</h1> },
  ], { initialEntries: ["/activate-profile"] })
  render(<RouterProvider router={router} />)
  return router
}

function activationNetwork() {
  const fetchMock = vi.fn<typeof fetch>(async (url) => {
    if (url === "/api/v1/auth/profile-activation/start") return accepted()
    if (url === "/api/v1/auth/profile-activation/verify") return json({ masked_name: "李*", kind: "both" })
    if (url === "/api/v1/auth/profile-activation/complete") return json({ completed: true })
    throw new Error(`Unexpected request: ${String(url)}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  return fetchMock
}

async function requestCode(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("本人邮箱"), "activation@example.test")
  await user.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  await screen.findByLabelText("验证码")
}

async function verifyCode(user: ReturnType<typeof userEvent.setup>) {
  await requestCode(user)
  await user.type(screen.getByLabelText("验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "验证邮箱" }))
  await screen.findByLabelText("新密码")
}

afterEach(() => {
  setCsrfToken(null)
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("starts email-only without disclosing an identity or pretending phone verification is available", () => {
  const fetchMock = activationNetwork()
  renderPage()
  expect(screen.getByLabelText("本人邮箱")).toHaveAttribute("type", "email")
  expect(screen.getByText(/暂不支持手机号激活，请使用本人邮箱。/)).toBeVisible()
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument()
  expect(screen.queryByText("李*")).not.toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()
})

it("accepts a server-authorized staff preview without claiming account activation or granting staff authority", async () => {
  const fetchMock = activationNetwork()
  const respond = fetchMock.getMockImplementation()!
  fetchMock.mockImplementation((url, init) => url === "/api/v1/auth/profile-activation/verify"
    ? Promise.resolve(json({ masked_name: "工*", kind: "staff" })) : respond(url, init))
  const user = userEvent.setup()
  renderPage()
  await verifyCode(user)
  expect(screen.getByText("工作人员")).toBeVisible()
  expect(screen.getByText(/预览不代表账号已激活/)).toBeVisible()
  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls.some(([, init]) => String(init?.body).includes("role"))).toBe(false)
})

it.each([true, false])("does not claim delivery or disclose account matching when accepted=%s", async (value) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => accepted(value)))
  const user = userEvent.setup()
  renderPage()
  await requestCode(user)
  expect(screen.getByRole("status")).toHaveTextContent(ACCEPTED)
  expect(screen.queryByText(/验证码已发送|邮件已发送|档案不存在|未获批的邮箱/)).not.toBeInTheDocument()
  expect(screen.queryByText("李*")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: /重新请求验证码.*60/ })).toBeDisabled()
})

it("verifies then completes with the same in-memory code, returns to login and never establishes a session", async () => {
  const fetchMock = activationNetwork()
  const storage = vi.spyOn(Storage.prototype, "setItem")
  const user = userEvent.setup()
  const router = renderPage()
  await verifyCode(user)
  expect(screen.getByText("李*")).toBeVisible()
  expect(screen.getByText("学员兼队员")).toBeVisible()
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  const password = screen.getByLabelText("新密码")
  await user.type(password, "Valid123!")
  await user.click(screen.getByRole("button", { name: "显示密码" }))
  expect(password).toHaveAttribute("type", "text")
  await user.click(screen.getByRole("button", { name: "隐藏密码" }))
  expect(password).toHaveAttribute("type", "password")
  expect(router.state.location).toMatchObject({ pathname: "/activate-profile", search: "", hash: "", state: null })
  await user.click(screen.getByRole("button", { name: "完成激活" }))
  expect(await screen.findByRole("heading", { name: "登录页面" })).toBeVisible()
  expect(router.state.location).toMatchObject({ pathname: "/login", search: "", hash: "", state: null })
  expect(fetchMock.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
    ["/api/v1/auth/profile-activation/start", { email: "activation@example.test" }],
    ["/api/v1/auth/profile-activation/verify", { code: "123456" }],
    ["/api/v1/auth/profile-activation/complete", { code: "123456", password: "Valid123!" }],
  ])
  expect(storage).not.toHaveBeenCalled()
  await act(() => router.navigate("/activate-profile"))
  expect(screen.getByLabelText("本人邮箱")).toHaveValue("")
  expect(screen.queryByText("李*")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument()
})

it("rejects non-six-digit codes without requesting preview", async () => {
  const fetchMock = activationNetwork()
  const user = userEvent.setup()
  renderPage()
  await requestCode(user)
  fireEvent.change(screen.getByLabelText("验证码"), { target: { value: "12345x" } })
  await user.click(screen.getByRole("button", { name: "验证邮箱" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("请输入 6 位数字验证码")
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it.each(["short1A", "abcdefgh1", "ABCDEFGH1", "Abcdefghi", "Abcdefgh123456789", "Ａbcdefg1", "abcde壹12"])("does not complete for invalid password %s", async (value) => {
  const fetchMock = activationNetwork()
  const user = userEvent.setup()
  renderPage()
  await verifyCode(user)
  fireEvent.change(screen.getByLabelText("新密码"), { target: { value } })
  await user.click(screen.getByRole("button", { name: "完成激活" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("密码需为 8–16 位，并同时包含大写、小写字母和数字")
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it("keeps verified code and password on recoverable completion failure for retry", async () => {
  const fetchMock = activationNetwork()
  const user = userEvent.setup()
  renderPage()
  await verifyCode(user)
  await user.type(screen.getByLabelText("新密码"), "Valid123!")
  fetchMock.mockResolvedValueOnce(json({ detail: "private failure", code: "activation_unavailable" }, 503))
  await user.click(screen.getByRole("button", { name: "完成激活" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法完成操作，请稍后重试")
  expect(screen.getByLabelText("新密码")).toHaveValue("Valid123!")
  expect(screen.getByText("李*")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "完成激活" }))
  expect(await screen.findByRole("heading", { name: "登录页面" })).toBeVisible()
  expect(JSON.parse(String(fetchMock.mock.calls[3]?.[1]?.body))).toEqual({ code: "123456", password: "Valid123!" })
})

it("invalidates code, masked preview and password when email changes", async () => {
  const fetchMock = activationNetwork()
  const user = userEvent.setup()
  renderPage()
  await verifyCode(user)
  await user.type(screen.getByLabelText("新密码"), "Valid123!")
  await user.clear(screen.getByLabelText("本人邮箱"))
  await user.type(screen.getByLabelText("本人邮箱"), "other@example.test")
  expect(screen.queryByText("李*")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it("preserves step and drafts across focus and background events", async () => {
  activationNetwork()
  const user = userEvent.setup()
  renderPage()
  await requestCode(user)
  await user.type(screen.getByLabelText("验证码"), "123456")
  fireEvent(window, new Event("blur"))
  fireEvent(document, new Event("visibilitychange"))
  fireEvent(window, new Event("focus"))
  expect(screen.getByLabelText("验证码")).toHaveValue("123456")
  await user.click(screen.getByRole("button", { name: "验证邮箱" }))
  await user.type(await screen.findByLabelText("新密码"), "Valid123!")
  fireEvent(window, new Event("blur"))
  fireEvent(document, new Event("visibilitychange"))
  fireEvent(window, new Event("focus"))
  expect(screen.getByLabelText("新密码")).toHaveValue("Valid123!")
  expect(screen.getByText("李*")).toBeVisible()
})

it.each([429, 503])("honors Retry-After for start HTTP %s without revealing server details and permits retry", async (status) => {
  const now = vi.spyOn(Date, "now").mockReturnValue(1_792_289_600_000)
  const fetchMock = activationNetwork()
  fetchMock.mockResolvedValueOnce(json({ detail: "Matching private account", code: "activation_unavailable" }, status, { "Retry-After": "2" }))
  const user = userEvent.setup()
  renderPage()
  await user.type(screen.getByLabelText("本人邮箱"), "activation@example.test")
  await user.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  expect(await screen.findByRole("alert")).not.toHaveTextContent("Matching private account")
  expect(screen.getByRole("button", { name: /请求邮箱验证码.*2/ })).toBeDisabled()
  now.mockReturnValue(1_792_289_602_100)
  await waitFor(() => expect(screen.getByRole("button", { name: "请求邮箱验证码" })).toBeEnabled())
  await user.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  expect(await screen.findByRole("status")).toHaveTextContent(ACCEPTED)
})

it.each(["start", "verify", "complete"] as const)("cancels departing %s and fences its late reply from a fresh activation page", async (stage) => {
  const fetchMock = activationNetwork()
  const respond = fetchMock.getMockImplementation()!
  let release!: (response: Response) => void
  const deferred = new Promise<Response>((resolve) => { release = resolve })
  let oldSignal: AbortSignal | undefined
  let held = false
  fetchMock.mockImplementation((url, init) => {
    if (url === `/api/v1/auth/profile-activation/${stage}` && !held) {
      held = true
      oldSignal = init?.signal ?? undefined
      // Deliberately ignore cancellation: even an already arriving response
      // must not let a departed component update state or navigate.
      return deferred
    }
    return respond(url, init)
  })
  const storage = vi.spyOn(Storage.prototype, "setItem")
  const user = userEvent.setup()
  const router = renderPage()
  if (stage === "start") {
    await user.type(screen.getByLabelText("本人邮箱"), "activation@example.test")
    await user.click(screen.getByRole("button", { name: "请求邮箱验证码" }))
  } else if (stage === "verify") {
    await requestCode(user)
    await user.type(screen.getByLabelText("验证码"), "123456")
    await user.click(screen.getByRole("button", { name: "验证邮箱" }))
  } else {
    await verifyCode(user)
    await user.type(screen.getByLabelText("新密码"), "Valid123!")
    await user.click(screen.getByRole("button", { name: "完成激活" }))
  }

  fireEvent(window, new Event("blur"))
  fireEvent(document, new Event("visibilitychange"))
  fireEvent(window, new Event("focus"))
  expect(oldSignal?.aborted).not.toBe(true)
  await user.click(screen.getByRole("link", { name: "返回登录" }))
  const departedWasAborted = oldSignal?.aborted
  await act(() => router.navigate("/activate-profile"))
  const freshEmail = screen.getByLabelText("本人邮箱")
  await user.type(freshEmail, "fresh@example.test")

  await act(async () => {
    release(stage === "start" ? accepted() : stage === "verify"
      ? json({ masked_name: "旧*", kind: "player" }) : json({ completed: true }))
    await deferred
  })
  expect(router.state.location).toMatchObject({ pathname: "/activate-profile", search: "", hash: "", state: null })
  expect(screen.getByLabelText("本人邮箱")).toBe(freshEmail)
  expect(freshEmail).toHaveValue("fresh@example.test")
  expect(screen.getByRole("button", { name: "请求邮箱验证码" })).toBeEnabled()
  expect(screen.queryByLabelText("验证码")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("新密码")).not.toBeInTheDocument()
  expect(screen.queryByText("旧*")).not.toBeInTheDocument()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(departedWasAborted).toBe(true)
  expect(storage).not.toHaveBeenCalled()
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(
    stage === "start" ? ["/api/v1/auth/profile-activation/start"] : stage === "verify"
      ? ["/api/v1/auth/profile-activation/start", "/api/v1/auth/profile-activation/verify"]
      : ["/api/v1/auth/profile-activation/start", "/api/v1/auth/profile-activation/verify", "/api/v1/auth/profile-activation/complete"],
  )
})
