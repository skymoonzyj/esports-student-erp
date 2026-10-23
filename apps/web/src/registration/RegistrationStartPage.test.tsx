import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { RegistrationStartPage } from "./RegistrationStartPage"
import { ProblemDetailsError } from "../api/client"

const api = vi.hoisted(() => ({
  getRegistrationChannelSettings: vi.fn(),
  startRegistration: vi.fn(),
  startGuardianInvitationRegistration: vi.fn(),
  getGuardianInvitationContext: vi.fn(),
  issueRegistrationChallenge: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  getRegistrationChannelSettings: api.getRegistrationChannelSettings,
  startRegistration: api.startRegistration,
  startGuardianInvitationRegistration: api.startGuardianInvitationRegistration,
  getGuardianInvitationContext: api.getGuardianInvitationContext,
  issueRegistrationChallenge: api.issueRegistrationChallenge,
}))

function VerifyStateProbe() {
  const location = useLocation()
  return <pre data-testid="verify-state">{JSON.stringify(location.state)}</pre>
}

function renderPage(initialEntry = "/register") {
  render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/register" element={<RegistrationStartPage />} />
        <Route path="/register/verify" element={<VerifyStateProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

function renderInvitationPage() {
  render(
    <MemoryRouter initialEntries={["/register/guardian-invitation"]}>
      <Routes>
        <Route
          path="/register/guardian-invitation"
          element={<RegistrationStartPage guardianInvitation />}
        />
        <Route path="/register/verify" element={<VerifyStateProbe />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  api.getRegistrationChannelSettings.mockReset().mockResolvedValue({
    phone_enabled: false,
    email_enabled: true,
    phone_signup_allowed_by_deployment: true,
    updated_at: null,
    request_id: "registration-channels",
  })
  api.startRegistration.mockReset().mockResolvedValue({
    application_id: "application-1",
    state: "draft",
    masked_identifier: "s***@example.com",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "registration-start",
  })
  api.startGuardianInvitationRegistration.mockReset().mockResolvedValue({
    application_id: "guardian-application-1",
    state: "draft",
    masked_identifier: "138****8888",
    expires_at: "2026-08-16T10:10:00+08:00",
    request_id: "guardian-registration-start",
  })
  api.getGuardianInvitationContext.mockReset().mockResolvedValue({
    masked_identifier: "138****8888",
    relationship: "母亲",
    is_primary: true,
    expires_at: "2026-08-23T10:00:00+08:00",
    consent_version: "2026-08",
    request_id: "guardian-context",
  })
  api.issueRegistrationChallenge.mockReset().mockResolvedValue({
    challenge_id: "challenge-1",
    expires_at: "2026-08-16T10:10:00+08:00",
    retry_after_seconds: 60,
    masked_destination: "s***@example.com",
    request_id: "registration-challenge",
  })
})

it("offers four single-select purposes and starts with an email contact", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(screen.getAllByRole("radio")).toHaveLength(4)
  expect(screen.getByRole("link", { name: "已有档案，申请激活" })).toHaveAttribute("href", "/claim-profile")
  expect(screen.getByRole("radio", { name: /学员/ })).toBeVisible()
  expect(screen.getByRole("radio", { name: /监护人/ })).toBeVisible()
  expect(screen.getByRole("radio", { name: /工作人员申请/ })).toBeVisible()
  expect(screen.getByRole("radio", { name: /临时访客/ })).toBeVisible()
  expect(screen.queryByRole("radio", { name: "手机号" })).not.toBeInTheDocument()

  await user.click(screen.getByRole("radio", { name: /工作人员申请/ }))
  await user.type(
    screen.getByRole("textbox", { name: "邮箱" }),
    "student@example.com",
  )
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledWith({
    primary_purpose: "staff_applicant",
    identifier_kind: "email",
    identifier: "student@example.com",
  })
  expect(api.issueRegistrationChallenge).toHaveBeenCalledWith("application-1")
  expect(await screen.findByTestId("verify-state")).toHaveTextContent(
    '"maskedDestination":"s***@example.com"',
  )
  expect(screen.getByTestId("verify-state")).not.toHaveTextContent("primaryPurpose")
})

it("shows and submits the phone channel when an administrator opens it", async () => {
  api.getRegistrationChannelSettings.mockResolvedValueOnce({
    phone_enabled: true,
    email_enabled: false,
    phone_signup_allowed_by_deployment: true,
    updated_at: null,
    request_id: "registration-phone-channel",
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  expect(await screen.findByRole("radio", { name: "手机号" })).toBeVisible()
  await user.type(screen.getByRole("textbox", { name: "手机号" }), "13800138000")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledWith({
    primary_purpose: "student",
    identifier_kind: "phone",
    identifier: "13800138000",
  })
})

it("locks a scanned invitation to student registration and submits its code", async () => {
  const user = userEvent.setup()
  renderPage("/register?invite=coach-2026")

  expect(screen.getByText("已通过邀请二维码进入")).toBeVisible()
  expect(screen.queryByRole("radio", { name: /监护人/ })).not.toBeInTheDocument()
  const referralInput = screen.getByRole("textbox", {
    name: "邀请人或邀请机构代码",
  })
  expect(referralInput).toHaveValue("COACH-2026")
  expect(referralInput).toHaveAttribute("readonly")

  await user.type(await screen.findByRole("textbox", { name: "邮箱" }), "invitee@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledWith({
    primary_purpose: "student",
    identifier_kind: "email",
    identifier: "invitee@example.com",
    referral_code: "COACH-2026",
  })
})

it("blocks self-registration when both channels are closed", async () => {
  api.getRegistrationChannelSettings.mockResolvedValueOnce({
    phone_enabled: false,
    email_enabled: false,
    phone_signup_allowed_by_deployment: true,
    updated_at: null,
    request_id: "registration-no-channel",
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  expect(await screen.findByRole("status")).toHaveTextContent(
    "当前暂未开放手机号或邮箱申请账号",
  )
  expect(screen.getByRole("button", { name: "发送验证码" })).toBeDisabled()
  expect(api.startRegistration).not.toHaveBeenCalled()
})

it("locks invitation registration to the server-provided guardian contact", async () => {
  const user = userEvent.setup()
  renderInvitationPage()

  expect(await screen.findByText(/138\*\*\*\*8888/)).toBeVisible()
  expect(screen.queryAllByRole("radio")).toHaveLength(0)
  expect(screen.queryByLabelText("手机号")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("邮箱")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "向受邀联系方式发送验证码" }))

  expect(api.startGuardianInvitationRegistration).toHaveBeenCalledTimes(1)
  expect(api.startRegistration).not.toHaveBeenCalled()
  expect(api.issueRegistrationChallenge).toHaveBeenCalledWith(
    "guardian-application-1",
  )
  expect(await screen.findByTestId("verify-state")).toHaveTextContent(
    '"guardianInvitationFlow":true',
  )
  expect(screen.getByTestId("verify-state")).not.toHaveTextContent("13800008888")
})

it("shows the backend-approved pending application detail", async () => {
  const pendingDetail = "已有一条未完成的注册申请，请继续原申请或等待过期后重试"
  api.startRegistration.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Registration application pending",
      status: 429,
      detail: pendingDetail,
      code: "registration_application_pending",
      request_id: "registration-pending",
    }),
  )
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  await user.type(await screen.findByRole("textbox", { name: "邮箱" }), "pending@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(pendingDetail)
  expect(screen.getByRole("alert")).not.toHaveTextContent("验证码请求过于频繁")
})

it("keeps an unrecognized 429 error on the safe generic message", async () => {
  api.startRegistration.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Rate limited",
      status: 429,
      detail: "internal throttling detail",
      code: "new_unrecognized_rate_limit",
      request_id: "registration-rate-limit",
    }),
  )
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  await user.type(await screen.findByRole("textbox", { name: "邮箱" }), "rate-limit@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("验证码请求过于频繁，请稍后重试")
  expect(screen.getByRole("alert")).not.toHaveTextContent("internal throttling detail")
})

it("reuses the started application when challenge delivery fails", async () => {
  api.startRegistration.mockResolvedValueOnce({
    application_id: "application-1",
    state: "draft",
    masked_identifier: "s***@example.com",
    expires_at: "2099-08-16T10:00:00+08:00",
    request_id: "registration-start",
  })
  api.issueRegistrationChallenge
    .mockRejectedValueOnce(new Error("delivery failed"))
    .mockResolvedValueOnce({
      challenge_id: "challenge-retry",
      expires_at: "2099-08-16T10:10:00+08:00",
      retry_after_seconds: 60,
      masked_destination: "s***@example.com",
      request_id: "registration-challenge-retry",
    })
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  await user.type(await screen.findByRole("textbox", { name: "邮箱" }), "retry@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  await screen.findByRole("alert")

  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledTimes(1)
  expect(api.issueRegistrationChallenge).toHaveBeenNthCalledWith(1, "application-1")
  expect(api.issueRegistrationChallenge).toHaveBeenNthCalledWith(2, "application-1")
  expect(await screen.findByTestId("verify-state")).toHaveTextContent(
    '"challengeId":"challenge-retry"',
  )
})

it("starts a new application after the registration inputs change", async () => {
  api.startRegistration
    .mockResolvedValueOnce({
      application_id: "application-1",
      state: "draft",
      masked_identifier: "f***@example.com",
      expires_at: "2099-08-16T10:00:00+08:00",
      request_id: "registration-start-1",
    })
    .mockResolvedValueOnce({
      application_id: "application-2",
      state: "draft",
      masked_identifier: "s***@example.com",
      expires_at: "2099-08-16T10:00:00+08:00",
      request_id: "registration-start-2",
    })
  api.issueRegistrationChallenge.mockRejectedValueOnce(new Error("delivery failed"))
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  const identifier = await screen.findByRole("textbox", { name: "邮箱" })
  await user.type(identifier, "first@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  await screen.findByRole("alert")

  await user.clear(identifier)
  await user.type(identifier, "second@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledTimes(2)
  expect(api.startRegistration).toHaveBeenNthCalledWith(2, {
    primary_purpose: "student",
    identifier_kind: "email",
    identifier: "second@example.com",
  })
  expect(api.issueRegistrationChallenge).toHaveBeenLastCalledWith("application-2")
})

it("starts a new application when the cached application has expired", async () => {
  api.startRegistration.mockResolvedValueOnce({
    application_id: "expired-application",
    state: "draft",
    masked_identifier: "e***@example.com",
    expires_at: "1970-01-01T00:00:00.000Z",
    request_id: "registration-start-expired",
  })
  api.issueRegistrationChallenge.mockRejectedValueOnce(new Error("delivery failed"))
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  await user.type(await screen.findByRole("textbox", { name: "邮箱" }), "expired@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  await screen.findByRole("alert")

  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledTimes(2)
  expect(api.issueRegistrationChallenge).toHaveBeenLastCalledWith("application-1")
})

it("preserves the original application when a changed-key replacement is pending", async () => {
  api.startRegistration
    .mockResolvedValueOnce({
      application_id: "application-1",
      state: "draft",
      masked_identifier: "s***@example.com",
      expires_at: "2099-08-16T10:00:00+08:00",
      request_id: "registration-start-1",
    })
    .mockRejectedValueOnce(
      new ProblemDetailsError({
        type: "about:blank",
        title: "Registration application pending",
        status: 429,
        detail: "已有一条未完成的注册申请，请继续原申请或等待过期后重试",
        code: "registration_application_pending",
        request_id: "registration-pending",
      }),
    )
  api.issueRegistrationChallenge
    .mockRejectedValueOnce(new Error("delivery failed"))
    .mockResolvedValueOnce({
      challenge_id: "challenge-original-retry",
      expires_at: "2099-08-16T10:10:00+08:00",
      retry_after_seconds: 60,
      masked_destination: "s***@example.com",
      request_id: "registration-challenge-retry",
    })
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("radio", { name: /学员/ }))
  await user.type(await screen.findByRole("textbox", { name: "邮箱" }), "same@example.com")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  await screen.findByRole("alert")

  const referral = screen.getByRole("textbox", { name: "邀请人或邀请机构代码（选填）" })
  await user.type(referral, "REF-1")
  await user.click(screen.getByRole("button", { name: "发送验证码" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("已有一条未完成的注册申请")

  await user.clear(referral)
  await user.click(screen.getByRole("button", { name: "发送验证码" }))

  expect(api.startRegistration).toHaveBeenCalledTimes(2)
  expect(api.issueRegistrationChallenge).toHaveBeenLastCalledWith("application-1")
  expect(await screen.findByTestId("verify-state")).toHaveTextContent(
    '"challengeId":"challenge-original-retry"',
  )
})
