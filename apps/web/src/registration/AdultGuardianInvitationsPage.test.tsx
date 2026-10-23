import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { AdultGuardianInvitationsPage } from "./AdultGuardianInvitationsPage"

const api = vi.hoisted(() => ({
  listAdultGuardianInvitations: vi.fn(),
  createAdultGuardianInvitation: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...api,
}))

const invitation = {
  id: "10000000-0000-4000-8000-000000000181",
  masked_identifier: "138****8888",
  relationship: "母亲",
  is_primary: false,
  status: "pending" as const,
  expires_at: "2026-08-23T10:00:00+08:00",
}

const createdInvitation = {
  ...invitation,
  id: "10000000-0000-4000-8000-000000000182",
}

function renderPage() {
  render(
    <MemoryRouter>
      <AdultGuardianInvitationsPage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  api.listAdultGuardianInvitations.mockReset().mockResolvedValue([invitation])
  api.createAdultGuardianInvitation.mockReset().mockResolvedValue(createdInvitation)
})

it("lists masked invitations and clears the submitted adult guardian contact", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText("138****8888")).toBeVisible()
  await user.selectOptions(screen.getByLabelText("邀请方式"), "phone")
  await user.type(screen.getByLabelText("监护人手机号或邮箱"), "13800008888")
  await user.type(screen.getByLabelText("与学员关系"), "母亲")
  await user.click(screen.getByRole("button", { name: "发送监护人邀请" }))

  expect(api.createAdultGuardianInvitation).toHaveBeenCalledWith({
    identifier_kind: "phone",
    identifier: "13800008888",
    relationship: "母亲",
    is_primary: false,
  })
  expect(screen.queryByDisplayValue("13800008888")).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain("13800008888")
  expect(document.body.textContent).not.toContain(invitation.id)
  expect(screen.getByRole("status")).toHaveTextContent("邀请已发送至 138****8888")
  expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite")
  const invitationList = screen.getByRole("list", { name: "已发送邀请" })
  expect(within(invitationList).getAllByRole("listitem")).toHaveLength(2)
  expect(screen.getByRole("link", { name: "返回工作台" })).toHaveAttribute(
    "href",
    "/",
  )
})

it("shows one generic unavailable state for non-adult-student accounts", async () => {
  api.listAdultGuardianInvitations.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "private eligibility detail",
      code: "registration_action_unavailable",
      request_id: "adult-invite-unavailable",
    }),
  )
  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "当前账号无法管理成年学员监护邀请",
  )
  expect(screen.getByRole("alert")).not.toHaveTextContent("private eligibility detail")
  expect(screen.queryByRole("button", { name: "发送监护人邀请" })).not.toBeInTheDocument()
})
