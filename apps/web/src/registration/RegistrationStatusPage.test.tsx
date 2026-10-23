import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { RegistrationStatusPage } from "./RegistrationStatusPage"

const api = vi.hoisted(() => ({
  getRegistrationMe: vi.fn(),
  getOwnStaffApplication: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...api,
}))

beforeEach(() => {
  api.getRegistrationMe.mockReset().mockResolvedValue({
    state: "pending_guardian",
    masked_identifier: "138****7701",
    next_action: "complete_guardian",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "registration-status",
  })
  api.getOwnStaffApplication.mockReset()
})

it("maps registration next actions to approved public guidance", async () => {
  render(<MemoryRouter><RegistrationStatusPage /></MemoryRouter>)

  expect(await screen.findByText("请完成监护人绑定")).toBeVisible()
  expect(screen.getByRole("link", { name: "管理监护人邀请" })).toHaveAttribute(
    "href",
    "/register/guardian",
  )
  expect(screen.queryByText("registration-status")).not.toBeInTheDocument()
})

it("shows a staff applicant only their public status and requested role", async () => {
  api.getRegistrationMe.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Unauthorized",
      status: 401,
      detail: "registration internals",
      code: "registration_session_invalid",
      request_id: "registration-private",
    }),
  )
  api.getOwnStaffApplication.mockResolvedValueOnce({
    application_id: "10000000-0000-4000-8000-000000000159",
    requested_staff_type: "coach",
    application_reason: "青训教学申请",
    status: "pending",
    reviewed_at: null,
    created_at: "2026-08-16T10:00:00+08:00",
  })
  render(<MemoryRouter><RegistrationStatusPage /></MemoryRouter>)

  expect(await screen.findByText("工作人员申请审核中")).toBeVisible()
  expect(screen.getByText("教练")).toBeVisible()
  expect(screen.getByText("青训教学申请")).toBeVisible()
  expect(document.body).not.toHaveTextContent("reviewed_by_user_id")
  expect(document.body).not.toHaveTextContent("registration internals")
})
