import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { RegistrationGuardianPage } from "./RegistrationGuardianPage"

const api = vi.hoisted(() => ({
  restoreRegistrationCsrf: vi.fn(),
  listMinorGuardianInvitations: vi.fn(),
  createMinorGuardianInvitation: vi.fn(),
  resendMinorGuardianInvitation: vi.fn(),
  revokeMinorGuardianInvitation: vi.fn(),
  issueRegistrationContactChallenge: vi.fn(),
  verifyRegistrationContactChange: vi.fn(),
  getRegistrationTermsConsent: vi.fn(),
  confirmRegistrationTermsConsent: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...api,
}))

const pendingInvitation = {
  id: "10000000-0000-4000-8000-000000000151",
  masked_identifier: "138****8888",
  relationship: "母亲",
  is_primary: true,
  status: "pending" as const,
  expires_at: "2026-08-23T10:00:00+08:00",
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <RegistrationGuardianPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  api.restoreRegistrationCsrf.mockReset().mockResolvedValue("restored-csrf")
  api.listMinorGuardianInvitations.mockReset().mockResolvedValue([pendingInvitation])
  api.createMinorGuardianInvitation.mockReset().mockResolvedValue(pendingInvitation)
  api.resendMinorGuardianInvitation.mockReset().mockResolvedValue({
    ...pendingInvitation,
    id: "10000000-0000-4000-8000-000000000152",
  })
  api.revokeMinorGuardianInvitation.mockReset().mockResolvedValue({
    ...pendingInvitation,
    masked_identifier: "联系方式已清除",
    status: "revoked",
  })
  api.issueRegistrationContactChallenge.mockReset().mockResolvedValue({
    challenge_id: "10000000-0000-4000-8000-000000000153",
    expires_at: "2026-08-16T10:10:00+08:00",
    retry_after_seconds: 60,
    masked_destination: "m***@example.com",
    request_id: "contact-challenge",
  })
  api.verifyRegistrationContactChange.mockReset().mockResolvedValue({
    state: "pending_guardian",
    masked_identifier: "m***@example.com",
    next_action: "complete_guardian",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "contact-changed",
    primary_purpose: "student",
  })
  api.getRegistrationTermsConsent.mockReset().mockResolvedValue({
    current_version: "2026-08",
    accepted_version: "2026-08",
    reconfirmation_required: false,
    request_id: "terms-current",
  })
  api.confirmRegistrationTermsConsent.mockReset().mockResolvedValue({
    current_version: "2026-08",
    accepted_version: "2026-08",
    reconfirmation_required: false,
    request_id: "terms-confirmed",
  })
})

it("requires explicit confirmation when registration terms changed", async () => {
  api.getRegistrationTermsConsent.mockResolvedValueOnce({
    current_version: "2026-08",
    accepted_version: "2026-07",
    reconfirmation_required: true,
    request_id: "terms-stale",
  })
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText("注册条款已更新")).toBeVisible()
  const checkbox = screen.getByRole("checkbox", { name: /重新阅读并同意/ })
  const button = screen.getByRole("button", { name: "确认新版注册条款" })
  expect(button).toBeDisabled()
  await user.click(checkbox)
  await user.click(button)

  expect(api.confirmRegistrationTermsConsent).toHaveBeenCalledWith("2026-08")
  expect(await screen.findByText("新版注册条款已确认")).toBeVisible()
})

it("restores csrf and replaces the minor's own contact after a fresh code", async () => {
  const user = userEvent.setup()
  renderPage()

  await screen.findByText("138****8888")
  expect(api.restoreRegistrationCsrf).toHaveBeenCalledTimes(1)
  await user.selectOptions(screen.getByLabelText("本人联系方式类型"), "email")
  await user.type(
    screen.getByLabelText("新的本人手机号或邮箱"),
    "minor-new@example.com",
  )
  await user.click(screen.getByRole("button", { name: "发送本人验证码" }))
  expect(api.issueRegistrationContactChallenge).toHaveBeenCalledWith({
    identifier_kind: "email",
    identifier: "minor-new@example.com",
  })
  expect(await screen.findByText("m***@example.com")).toBeVisible()
  await user.type(screen.getByLabelText("本人的验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "确认更换联系方式" }))

  expect(api.verifyRegistrationContactChange).toHaveBeenCalledWith({
    challenge_id: "10000000-0000-4000-8000-000000000153",
    code: "123456",
    identifier_kind: "email",
    identifier: "minor-new@example.com",
  })
  expect(screen.queryByDisplayValue("minor-new@example.com")).not.toBeInTheDocument()
})

it("shows only masked guardian targets and creates an invitation", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText("138****8888")).toBeVisible()
  expect(screen.queryByText("13800008888")).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("邀请方式"), "phone")
  await user.type(screen.getByLabelText("监护人手机号或邮箱"), "13800008888")
  await user.type(screen.getByLabelText("与学员关系"), "母亲")
  await user.click(screen.getByRole("checkbox", { name: "设为主要监护人" }))
  await user.click(screen.getByRole("button", { name: "发送监护人邀请" }))

  expect(api.createMinorGuardianInvitation).toHaveBeenCalledWith({
    identifier_kind: "phone",
    identifier: "13800008888",
    relationship: "母亲",
    is_primary: true,
  })
  expect(screen.queryByDisplayValue("13800008888")).not.toBeInTheDocument()
})

it("clears a full guardian identifier even when invitation creation fails", async () => {
  api.createMinorGuardianInvitation.mockRejectedValueOnce(new Error("network failed"))
  const user = userEvent.setup()
  renderPage()

  await screen.findByText("138****8888")
  await user.type(screen.getByLabelText("监护人手机号或邮箱"), "13800008889")
  await user.type(screen.getByLabelText("与学员关系"), "父亲")
  await user.click(screen.getByRole("button", { name: "发送监护人邀请" }))

  expect(await screen.findByRole("alert")).toBeVisible()
  expect(screen.queryByDisplayValue("13800008889")).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent("13800008889")
})

it("resends and revokes a pending invitation by its opaque id", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("138****8888")

  await user.click(screen.getByRole("button", { name: "重新发送给 138****8888" }))
  expect(api.resendMinorGuardianInvitation).toHaveBeenCalledWith(pendingInvitation.id)
  await user.click(screen.getByRole("button", { name: "撤销 138****8888" }))
  expect(api.revokeMinorGuardianInvitation).toHaveBeenCalledWith(
    "10000000-0000-4000-8000-000000000152",
  )
  expect(await screen.findByText("联系方式已清除")).toBeVisible()
})
