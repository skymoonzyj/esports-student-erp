import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { RegistrationPage } from "./RegistrationPage"

afterEach(() => {
  vi.unstubAllGlobals()
})

it("allows each registration password to be shown or hidden independently", async () => {
  const user = userEvent.setup()
  render(<MemoryRouter><RegistrationPage /></MemoryRouter>)

  const password = screen.getByLabelText("密码")
  const confirmation = screen.getByLabelText("确认密码")
  await user.type(password, "StrongA1")

  expect(password).toHaveAttribute("type", "password")
  expect(confirmation).toHaveAttribute("type", "password")
  await user.click(screen.getAllByRole("button", { name: "显示密码" })[0])

  expect(password).toHaveAttribute("type", "text")
  expect(password).toHaveValue("StrongA1")
  expect(confirmation).toHaveAttribute("type", "password")
  expect(screen.getByRole("button", { name: "隐藏密码" })).toBeVisible()
})

it("submits the required profile, verifies email, and shows the minor guardrail", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/registration/start")) {
      return new Response(JSON.stringify({
        registration_id: "70000000-0000-4000-8000-000000000001",
        expires_at: "2026-08-19T12:10:00Z",
        message: "验证码已发送到邮箱，请在有效期内完成验证",
      }), { status: 202, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/auth/registration/verify")) {
      return new Response(JSON.stringify({
        registration_id: "70000000-0000-4000-8000-000000000001",
        account_status: "disabled",
        requires_guardian: true,
        message: "邮箱已验证。未成年学员需绑定有效监护人后才能登录。",
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(<MemoryRouter><RegistrationPage /></MemoryRouter>)
  await user.type(screen.getByLabelText("姓名"), "测试学员")
  await user.type(screen.getByLabelText("手机号"), "13800138000")
  await user.type(screen.getByLabelText("邮箱"), "student@example.com")
  await user.type(screen.getByLabelText("身份证号"), "110105201001010012")
  await user.selectOptions(screen.getByLabelText("性别"), "female")
  await user.type(screen.getByLabelText("出生日期"), "2010-01-01")
  await user.type(screen.getByLabelText("密码"), "StrongA1")
  await user.type(screen.getByLabelText("确认密码"), "StrongA1")
  await user.click(screen.getByRole("button", { name: "提交并验证邮箱" }))

  expect(await screen.findByLabelText("邮箱验证码")).toBeVisible()
  await user.type(screen.getByLabelText("邮箱验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "验证并完成注册" }))

  expect(await screen.findByText("邮箱已验证。未成年学员需绑定有效监护人后才能登录。"))
    .toBeVisible()
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  const startCall = fetchMock.mock.calls[0]
  expect(JSON.parse(String(startCall?.[1]?.body))).toMatchObject({
    full_name: "测试学员",
    person_type: "student",
    email: "student@example.com",
    staff_type: null,
  })
})
