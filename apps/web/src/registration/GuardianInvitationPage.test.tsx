import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { GuardianInvitationPage } from "./GuardianInvitationPage"

const auth = vi.hoisted(() => ({
  status: "anonymous" as "anonymous" | "authenticated" | "loading",
  user: null as null | { account_id: string; full_name: string },
}))

const api = vi.hoisted(() => ({
  handoffGuardianInvitation: vi.fn(),
  getGuardianInvitationContext: vi.fn(),
  getGuardianInvitationAcceptance: vi.fn(),
  acceptHandedOffGuardianInvitation: vi.fn(),
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => auth,
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...api,
}))

function LocationProbe() {
  const location = useLocation()
  return <pre data-testid="location-state">{JSON.stringify(location.state)}</pre>
}

function pageContent({ strictMode = false }: { strictMode?: boolean } = {}) {
  const content = (
    <MemoryRouter initialEntries={["/guardian-invitation"]}>
      <Routes>
        <Route path="/guardian-invitation" element={<GuardianInvitationPage />} />
        <Route path="/login" element={<LocationProbe />} />
      </Routes>
    </MemoryRouter>
  )
  return strictMode ? <StrictMode>{content}</StrictMode> : content
}

function renderPage(options: { strictMode?: boolean } = {}) {
  return render(pageContent(options))
}

beforeEach(() => {
  auth.status = "anonymous"
  auth.user = null
  window.history.replaceState({}, "", "/guardian-invitation")
  window.localStorage.clear()
  window.sessionStorage.clear()
  api.handoffGuardianInvitation.mockReset().mockResolvedValue(undefined)
  api.getGuardianInvitationContext.mockReset().mockResolvedValue({
    masked_identifier: "138****8888",
    relationship: "母亲",
    is_primary: true,
    expires_at: "2026-08-23T10:00:00+08:00",
    consent_version: "2026-08",
    request_id: "guardian-context",
  })
  api.acceptHandedOffGuardianInvitation.mockReset().mockResolvedValue({
    status: "accepted",
    student_finalized: true,
    request_id: "guardian-accepted",
  })
  api.getGuardianInvitationAcceptance.mockReset().mockResolvedValue({
    status: "accepted",
    student_finalized: true,
    request_id: "guardian-acceptance-receipt",
  })
})

it("exchanges and clears the fragment before offering safe login or guardian registration", async () => {
  const user = userEvent.setup()
  const token = "10000000000040008000000000000181"
  window.history.replaceState({}, "", `/guardian-invitation#${token}`)
  renderPage()

  expect(await screen.findByText(/138\*\*\*\*8888/)).toBeVisible()
  expect(api.handoffGuardianInvitation).toHaveBeenCalledWith(token)
  expect(window.location.hash).toBe("")
  expect(document.body.textContent).not.toContain(token)
  expect(screen.getByRole("link", { name: "注册监护人账号" })).toHaveAttribute(
    "href",
    "/register/guardian-invitation",
  )
  await user.click(screen.getByRole("link", { name: "已有账号登录" }))
  expect(await screen.findByTestId("location-state")).toHaveTextContent(
    '"returnTo":"/guardian-invitation"',
  )
})

it("serializes fragment handoff before context during StrictMode effect replay", async () => {
  const token = "10000000000040008000000000000181"
  let finishHandoff: (() => void) | undefined
  let handedOff = false
  api.handoffGuardianInvitation.mockImplementation(() => new Promise<void>((resolve) => {
    finishHandoff = () => {
      handedOff = true
      resolve()
    }
  }))
  api.getGuardianInvitationContext.mockImplementation(async () => {
    if (!handedOff) throw new Error("context requested before handoff")
    return {
      masked_identifier: "138****8888",
      relationship: "母亲",
      is_primary: true,
      expires_at: "2026-08-23T10:00:00+08:00",
      consent_version: "2026-08",
      request_id: "guardian-context",
    }
  })
  window.history.replaceState({}, "", `/guardian-invitation#${token}`)

  renderPage({ strictMode: true })

  expect(window.location.hash).toBe("")
  expect(api.handoffGuardianInvitation).toHaveBeenCalledTimes(1)
  expect(api.getGuardianInvitationContext).not.toHaveBeenCalled()
  finishHandoff?.()
  expect(await screen.findByRole("link", { name: "注册监护人账号" })).toBeVisible()
  expect(api.getGuardianInvitationContext).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

it("never recovers an older receipt when a new handoff fails and retries the same token", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  const token = "new-invitation-token-kept-only-in-memory-0001"
  window.history.replaceState({}, "", `/guardian-invitation#${token}`)
  api.handoffGuardianInvitation
    .mockRejectedValueOnce(new TypeError("handoff request failed"))
    .mockResolvedValueOnce(undefined)
  api.getGuardianInvitationContext.mockResolvedValueOnce({
    masked_identifier: "139****0002",
    relationship: "父亲",
    is_primary: false,
    expires_at: "2026-08-24T10:00:00+08:00",
    consent_version: "2026-08",
    request_id: "new-invitation-context",
  })

  const user = userEvent.setup()
  const rendered = renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("可以重试")
  expect(api.getGuardianInvitationAcceptance).not.toHaveBeenCalled()
  expect(screen.queryByText("监护关系已建立")).not.toBeInTheDocument()
  expect(window.location.hash).toBe("")
  expect(document.body.textContent).not.toContain(token)
  expect(Object.values(window.localStorage)).not.toContain(token)
  expect(Object.values(window.sessionStorage)).not.toContain(token)

  auth.status = "loading"
  rendered.rerender(pageContent())
  await waitFor(() => {
    expect(api.handoffGuardianInvitation).toHaveBeenCalledTimes(1)
  })
  auth.status = "authenticated"
  rendered.rerender(pageContent())
  await waitFor(() => {
    expect(api.handoffGuardianInvitation).toHaveBeenCalledTimes(1)
  })

  await user.click(screen.getByRole("button", { name: "重试确认邀请" }))

  expect(api.handoffGuardianInvitation).toHaveBeenNthCalledWith(2, token)
  expect(await screen.findByText("受邀联系方式：139****0002")).toBeVisible()
  expect(api.getGuardianInvitationAcceptance).not.toHaveBeenCalled()
  expect(window.location.hash).toBe("")
  expect(document.body.textContent).not.toContain(token)
  expect(Object.values(window.localStorage)).not.toContain(token)
  expect(Object.values(window.sessionStorage)).not.toContain(token)
})

it("recovers this flow receipt when handoff succeeds but context response is lost", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  const token = "new-invitation-context-loss-token-0002"
  window.history.replaceState({}, "", `/guardian-invitation#${token}`)
  api.getGuardianInvitationContext.mockRejectedValueOnce(
    new TypeError("context response lost"),
  )

  renderPage()

  expect(await screen.findByText("监护关系已建立")).toBeVisible()
  expect(api.handoffGuardianInvitation).toHaveBeenCalledTimes(1)
  expect(api.handoffGuardianInvitation).toHaveBeenCalledWith(token)
  expect(api.getGuardianInvitationAcceptance).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
  expect(window.location.hash).toBe("")
  expect(document.body.textContent).not.toContain(token)
  expect(Object.values(window.localStorage)).not.toContain(token)
  expect(Object.values(window.sessionStorage)).not.toContain(token)
})

it("requires explicit consent and clears invitation detail after acceptance", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText(/138\*\*\*\*8888/)).toBeVisible()
  const accept = screen.getByRole("button", { name: "确认建立监护关系" })
  expect(accept).toBeDisabled()
  await user.click(screen.getByRole("checkbox", { name: /确认接受监护关系/ }))
  await user.click(accept)

  expect(api.acceptHandedOffGuardianInvitation).toHaveBeenCalledWith("2026-08")
  expect(await screen.findByText("监护关系已建立")).toBeVisible()
  expect(screen.queryByText(/138\*\*\*\*8888/)).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain(
    "10000000-0000-4000-8000-000000000181",
  )
})

it("recovers an accepted receipt when the accept response is lost", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  api.acceptHandedOffGuardianInvitation.mockRejectedValueOnce(
    new TypeError("connection closed after commit"),
  )
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText(/138\*\*\*\*8888/)).toBeVisible()
  await user.click(screen.getByRole("checkbox", { name: /确认接受监护关系/ }))
  await user.click(screen.getByRole("button", { name: "确认建立监护关系" }))

  expect(api.getGuardianInvitationAcceptance).toHaveBeenCalledTimes(1)
  expect(await screen.findByText("监护关系已建立")).toBeVisible()
  expect(screen.queryByText(/138\*\*\*\*8888/)).not.toBeInTheDocument()
})

it("keeps the invitation and allows retry when neither accept nor receipt is available", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  api.acceptHandedOffGuardianInvitation
    .mockRejectedValueOnce(new TypeError("temporary network failure"))
    .mockResolvedValueOnce({
      status: "accepted",
      student_finalized: true,
      request_id: "guardian-accepted-retry",
    })
  api.getGuardianInvitationAcceptance.mockRejectedValueOnce(
    new TypeError("receipt temporarily unavailable"),
  )
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText(/138\*\*\*\*8888/)).toBeVisible()
  await user.click(screen.getByRole("checkbox", { name: /确认接受监护关系/ }))
  await user.click(screen.getByRole("button", { name: "确认建立监护关系" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("可以直接重试")
  expect(screen.getByText(/138\*\*\*\*8888/)).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认建立监护关系" }))
  expect(await screen.findByText("监护关系已建立")).toBeVisible()
  expect(api.acceptHandedOffGuardianInvitation).toHaveBeenCalledTimes(2)
})

it("restores the same-account acceptance receipt after a page refresh", async () => {
  auth.status = "authenticated"
  auth.user = { account_id: "account-1", full_name: "受邀监护人" }
  api.getGuardianInvitationContext.mockRejectedValueOnce(
    new Error("accepted invitations no longer expose pending context"),
  )

  renderPage()

  expect(await screen.findByText("监护关系已建立")).toBeVisible()
  expect(api.getGuardianInvitationAcceptance).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})
