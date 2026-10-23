import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it } from "vitest"

import { PasswordInput } from "./PasswordInput"

it("shows and hides its own value without changing the input value", async () => {
  const user = userEvent.setup()
  render(<PasswordInput aria-label="测试密码" value="StrongA1" readOnly />)

  const input = screen.getByLabelText("测试密码")
  expect(input).toHaveAttribute("type", "password")
  await user.click(screen.getByRole("button", { name: "显示密码" }))
  expect(input).toHaveAttribute("type", "text")
  expect(input).toHaveValue("StrongA1")
  await user.click(screen.getByRole("button", { name: "隐藏密码" }))
  expect(input).toHaveAttribute("type", "password")
})
