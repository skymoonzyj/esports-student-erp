import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ForgotPasswordPage } from "./ForgotPasswordPage"

const api = vi.hoisted(() => ({
  issuePasswordResetChallenge: vi.fn(),
  completePasswordReset: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  issuePasswordResetChallenge: api.issuePasswordResetChallenge,
  completePasswordReset: api.completePasswordReset,
}))

beforeEach(() => {
  api.issuePasswordResetChallenge.mockReset().mockResolvedValue({
    challenge_id: "password-challenge-1",
    expires_at: "2026-08-16T10:10:00+08:00",
    retry_after_seconds: 60,
    masked_destination: "s***@example.com",
    request_id: "password-reset-start",
  })
  api.completePasswordReset.mockReset().mockResolvedValue({
    status: "completed",
    request_id: "password-reset-complete",
  })
})

it("uses a phone or email and finishes with a non-enumerating message", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <ForgotPasswordPage />
    </MemoryRouter>,
  )

  await user.type(screen.getByLabelText("手机号或邮箱"), "student@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  expect(await screen.findByText("s***@example.com")).toBeVisible()
  await user.type(screen.getByLabelText("验证码"), "123456")
  await user.type(screen.getByLabelText("新密码"), "Aa1-recover")
  await user.type(screen.getByLabelText("确认新密码"), "Aa1-recover")
  await user.click(screen.getByRole("button", { name: "重置密码" }))

  expect(api.issuePasswordResetChallenge).toHaveBeenCalledWith(
    "student@example.com",
  )
  expect(api.completePasswordReset).toHaveBeenCalledWith({
    challenge_id: "password-challenge-1",
    code: "123456",
    new_password: "Aa1-recover",
  })
  expect(await screen.findByText("如果账号信息有效，密码已完成更新")).toBeVisible()
  expect(screen.queryByText("student@example.com")).not.toBeInTheDocument()
})
