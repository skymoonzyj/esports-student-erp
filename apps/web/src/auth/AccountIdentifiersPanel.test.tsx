import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { registrationKeys } from "../registration/keys"
import { AccountIdentifiersPanel } from "./AccountIdentifiersPanel"

const api = vi.hoisted(() => ({
  listAccountIdentifiers: vi.fn(),
  issueAccountIdentifierChallenge: vi.fn(),
  verifyAccountIdentifier: vi.fn(),
}))

vi.mock("../registration/api", async (importOriginal) => ({
  ...(await importOriginal()),
  listAccountIdentifiers: api.listAccountIdentifiers,
  issueAccountIdentifierChallenge: api.issueAccountIdentifierChallenge,
  verifyAccountIdentifier: api.verifyAccountIdentifier,
}))

beforeEach(() => {
  window.sessionStorage.clear()
  api.listAccountIdentifiers.mockReset().mockResolvedValue({
    items: [
      {
        kind: "email",
        masked_identifier: "s***@example.com",
        verified_at: "2026-08-16T10:00:00+08:00",
      },
    ],
    request_id: "identifier-list",
  })
  api.issueAccountIdentifierChallenge.mockReset().mockResolvedValue({
    challenge_id: "identifier-challenge-1",
    expires_at: new Date(Date.now() + 10 * 60 * 1000).toISOString(),
    retry_after_seconds: 60,
    masked_destination: "138****7777",
    request_id: "identifier-challenge",
  })
  api.verifyAccountIdentifier.mockReset().mockResolvedValue({
    items: [
      {
        kind: "email",
        masked_identifier: "s***@example.com",
        verified_at: "2026-08-16T10:00:00+08:00",
      },
      {
        kind: "phone",
        masked_identifier: "138****7777",
        verified_at: "2026-08-16T10:05:00+08:00",
      },
    ],
    request_id: "identifier-verified",
  })
})

it("verifies another login method without ever preloading its full value", async () => {
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountIdentifiersPanel accountId="account-1" />
    </QueryClientProvider>,
  )

  expect(await screen.findByText("s***@example.com · 已验证")).toBeVisible()
  expect(screen.queryByText("主登录")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "添加手机号" }))
  await user.type(screen.getByLabelText("新手机号"), "13800007777")
  expect(screen.getByRole("button", { name: "发送验证码" })).toBeDisabled()
  await user.type(screen.getByLabelText("当前密码"), "Current-password-2026")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  expect(api.issueAccountIdentifierChallenge).toHaveBeenCalledWith({
    identifier_kind: "phone",
    identifier: "13800007777",
    current_password: "Current-password-2026",
  })
  expect(screen.getByText("138****7777")).toBeVisible()
  expect(screen.queryByDisplayValue("13800007777")).not.toBeInTheDocument()
  await user.type(screen.getByLabelText("验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "确认绑定" }))

  expect(api.verifyAccountIdentifier).toHaveBeenCalledWith({
    challenge_id: "identifier-challenge-1",
    code: "123456",
    identifier_kind: "phone",
    identifier: "13800007777",
    current_password: "Current-password-2026",
  })
  expect(await screen.findByText("138****7777 · 已验证")).toBeVisible()
  expect(screen.queryByDisplayValue("13800007777")).not.toBeInTheDocument()
})

it("closes the form and clears only current account data after private 404", async () => {
  api.issueAccountIdentifierChallenge.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Not Found",
      status: 404,
      detail: "private account details must not be shown",
      code: "registration_action_unavailable",
      request_id: "identifier-private",
    }),
  )
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const otherKey = registrationKeys.accountIdentifiers("account-other")
  client.setQueryData(otherKey, { private: "other account remains" })
  const user = userEvent.setup()
  render(
    <QueryClientProvider client={client}>
      <AccountIdentifiersPanel accountId="account-1" />
    </QueryClientProvider>,
  )

  await screen.findByText("s***@example.com · 已验证")
  await user.click(screen.getByRole("button", { name: "添加手机号" }))
  await user.type(screen.getByLabelText("新手机号"), "13800007777")
  await user.type(screen.getByLabelText("当前密码"), "Current-password-2026")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  await waitFor(() => {
    expect(
      client.getQueryData(registrationKeys.accountIdentifiers("account-1")),
    ).toBeUndefined()
  })
  expect(client.getQueryData(otherKey)).toEqual({ private: "other account remains" })
  expect(screen.queryByLabelText("新手机号")).not.toBeInTheDocument()
  expect(screen.getByRole("alert")).not.toHaveTextContent("private account details")
  expect(JSON.stringify(client.getQueryCache().getAll())).not.toContain("13800007777")
})

it("restores an in-flight challenge after leaving and returning to the profile page", async () => {
  const user = userEvent.setup()
  const firstView = render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountIdentifiersPanel accountId="account-1" />
    </QueryClientProvider>,
  )

  await screen.findByText("s***@example.com · 已验证")
  await user.click(screen.getByRole("button", { name: "添加手机号" }))
  await user.type(screen.getByLabelText("新手机号"), "13800007777")
  await user.type(screen.getByLabelText("当前密码"), "Current-password-2026")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  expect(await screen.findByText("138****7777")).toBeVisible()

  firstView.unmount()
  render(
    <QueryClientProvider client={new QueryClient()}>
      <AccountIdentifiersPanel accountId="account-1" />
    </QueryClientProvider>,
  )

  expect(await screen.findByText("138****7777")).toBeVisible()
  expect(screen.getByLabelText("验证码")).toBeVisible()
  expect(screen.queryByLabelText("新手机号")).not.toBeInTheDocument()
  expect(screen.getByLabelText("当前密码")).toHaveValue("")

  await user.type(screen.getByLabelText("当前密码"), "Current-password-2026")
  await user.type(screen.getByLabelText("验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "确认绑定" }))
  expect(api.verifyAccountIdentifier).toHaveBeenCalledWith(
    expect.objectContaining({
      challenge_id: "identifier-challenge-1",
      identifier: "13800007777",
      code: "123456",
    }),
  )
  await waitFor(() => expect(window.sessionStorage.length).toBe(0))
})
