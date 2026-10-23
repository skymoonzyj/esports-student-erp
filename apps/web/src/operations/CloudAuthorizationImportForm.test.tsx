import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import { CloudAuthorizationImportForm } from "./CloudAuthorizationImportForm"

it("requires consent before opening the fixed external page and explains third-party renewal", async () => {
  render(<CloudAuthorizationImportForm provider="baidu" disabled={false} clearVersion={0} onSubmit={vi.fn()} />)
  expect(screen.queryByRole("link", { name: "打开授权页面" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "打开授权页面" })).toBeDisabled()
  expect(screen.getByText(/第三方服务会处理网盘授权凭据/)).toBeVisible()
  expect(screen.getByText(/百度后续续期也依赖/)).toBeVisible()
  await userEvent.setup().click(screen.getByRole("checkbox", { name: /已阅读/ }))
  const link = screen.getByRole("link", { name: "打开授权页面" })
  expect(link).toHaveAttribute("href", "https://api.oplist.org/")
  expect(link).toHaveAttribute("target", "_blank")
  expect(link).toHaveAttribute("rel", "noopener noreferrer")
  expect(link).toHaveAttribute("referrerpolicy", "no-referrer")
})

it("accepts mobile paste into masked write-only fields and clears only on confirmed acceptance", async () => {
  const onSubmit = vi.fn()
  const view = render(<CloudAuthorizationImportForm provider="115" disabled={false} clearVersion={0} onSubmit={onSubmit} />)
  const access = screen.getByLabelText("访问令牌（Access Token）")
  const refresh = screen.getByLabelText("刷新令牌（Refresh Token）")
  expect(access).toHaveAttribute("type", "password")
  fireEvent.change(access, { target: { value: "synthetic-access" } })
  fireEvent.change(refresh, { target: { value: "synthetic-refresh" } })
  await userEvent.setup().click(screen.getByRole("checkbox", { name: /已阅读/ }))
  await userEvent.setup().click(screen.getByRole("button", { name: "验证授权" }))
  expect(onSubmit).toHaveBeenCalledWith("synthetic-access", "synthetic-refresh")
  expect(access).toHaveValue("synthetic-access")
  view.rerender(<CloudAuthorizationImportForm provider="115" disabled={true} clearVersion={1} onSubmit={onSubmit} />)
  await waitFor(() => expect(access).toHaveValue(""))
  expect(refresh).toHaveValue("")
})

it("rejects pasted URLs, internal whitespace and empty tokens without submitting", async () => {
  const onSubmit = vi.fn()
  render(<CloudAuthorizationImportForm provider="115" disabled={false} clearVersion={0} onSubmit={onSubmit} />)
  fireEvent.change(screen.getByLabelText("访问令牌（Access Token）"), { target: { value: "https://example.invalid/callback" } })
  fireEvent.change(screen.getByLabelText("刷新令牌（Refresh Token）"), { target: { value: "synthetic refresh" } })
  await userEvent.setup().click(screen.getByRole("checkbox", { name: /已阅读/ }))
  await userEvent.setup().click(screen.getByRole("button", { name: "验证授权" }))
  expect(onSubmit).not.toHaveBeenCalled()
  expect(screen.getByRole("alert")).not.toHaveTextContent("example.invalid")
})
