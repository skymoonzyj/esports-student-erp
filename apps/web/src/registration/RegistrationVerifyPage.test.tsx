import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { RegistrationVerifyPage } from "./RegistrationVerifyPage"

const api = vi.hoisted(() => ({
  verifyRegistration: vi.fn(),
  issueRegistrationChallenge: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  verifyRegistration: api.verifyRegistration,
  issueRegistrationChallenge: api.issueRegistrationChallenge,
}))

function ProfileStateProbe() {
  const location = useLocation()
  return <pre data-testid="profile-state">{JSON.stringify(location.state)}</pre>
}

function renderPage() {
  render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/register/verify",
          state: {
            applicationId: "application-1",
            challengeId: "challenge-1",
            maskedDestination: "138****7777",
            retryAfterSeconds: 60,
            guardianInvitationFlow: true,
          },
        },
      ]}
    >
      <Routes>
        <Route path="/register/verify" element={<RegistrationVerifyPage />} />
        <Route path="/register/profile" element={<ProfileStateProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  api.verifyRegistration.mockReset().mockResolvedValue({
    state: "contact_verified",
    masked_identifier: "138****7777",
    next_action: "complete_profile",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "registration-verify",
    csrf_token: "private-registration-csrf",
    primary_purpose: "student",
  })
  api.issueRegistrationChallenge.mockReset()
})

it("shows only the masked target and advances after a six-digit code", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(screen.getByText("138****7777")).toBeVisible()
  expect(screen.queryByText("13800007777")).not.toBeInTheDocument()
  await user.type(screen.getByLabelText("验证码"), "123456")
  await user.click(screen.getByRole("button", { name: "验证并继续" }))

  expect(api.verifyRegistration).toHaveBeenCalledWith("application-1", {
    challenge_id: "challenge-1",
    code: "123456",
  })
  expect(await screen.findByTestId("profile-state")).toHaveTextContent(
    '"guardianInvitationFlow":true',
  )
  expect(screen.getByTestId("profile-state")).not.toHaveTextContent("primaryPurpose")
  expect(screen.getByTestId("profile-state")).not.toHaveTextContent(
    "private-registration-csrf",
  )
})
