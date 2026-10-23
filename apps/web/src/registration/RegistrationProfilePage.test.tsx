import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { RegistrationProfilePage } from "./RegistrationProfilePage"

const api = vi.hoisted(() => ({
  restoreRegistrationCsrf: vi.fn(),
  getRegistrationMe: vi.fn(),
  updateRegistrationProfile: vi.fn(),
  finalizeRegistration: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  restoreRegistrationCsrf: api.restoreRegistrationCsrf,
  getRegistrationMe: api.getRegistrationMe,
  updateRegistrationProfile: api.updateRegistrationProfile,
  finalizeRegistration: api.finalizeRegistration,
}))

function renderProfile(
  primaryPurpose = "staff_applicant",
  routePurpose?: string,
) {
  api.getRegistrationMe.mockResolvedValueOnce({
    state: "contact_verified",
    primary_purpose: primaryPurpose,
    masked_identifier: "s***@example.com",
    next_action: "complete_profile",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "registration-profile-state",
  })
  render(
    <MemoryRouter
      initialEntries={[
        {
          pathname: "/register/profile",
          state: routePurpose === undefined ? null : { primaryPurpose: routePurpose },
        },
      ]}
    >
      <RegistrationProfilePage />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  api.restoreRegistrationCsrf.mockReset().mockResolvedValue("restored-csrf")
  api.getRegistrationMe.mockReset().mockResolvedValue({
    state: "contact_verified",
    primary_purpose: "staff_applicant",
    masked_identifier: "s***@example.com",
    next_action: "complete_profile",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "registration-profile-state",
  })
  api.updateRegistrationProfile.mockReset().mockResolvedValue({
    state: "ready_to_finalize",
    masked_identifier: "s***@example.com",
    next_action: "finalize",
    expires_at: "2026-09-16T10:00:00+08:00",
    request_id: "registration-profile-write",
    primary_purpose: "staff_applicant",
  })
  api.finalizeRegistration.mockReset().mockResolvedValue({
    state: "conflict",
    next_action: "await_review",
    request_id: "registration-finalize",
  })
})

it("uses the server purpose after refresh and ignores forged route state", async () => {
  renderProfile("guardian", "staff_applicant")

  await screen.findByText("s***@example.com")
  expect(screen.queryByLabelText("申请岗位")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("申请说明")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("来访说明")).not.toBeInTheDocument()
})

it("clears sensitive fields immediately when the registration session expires", async () => {
  api.updateRegistrationProfile.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Unauthorized",
      status: 401,
      detail: "session expired",
      code: "registration_session_invalid",
      request_id: "expired-profile-session",
    }),
  )
  const user = userEvent.setup()
  renderProfile("student")
  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "敏感姓名")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  await user.type(screen.getByLabelText("密码", { exact: true }), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))

  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("注册会话不可用")
  expect(screen.queryByDisplayValue("敏感姓名")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("110105199001021238")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("Aa1-valid")).not.toBeInTheDocument()
})

it.each([
  [401, "Unauthorized", "registration_session_invalid"],
  [404, "Not Found", "registration_action_unavailable"],
] as const)(
  "returns to the registration session boundary when finalization reports %i",
  async (status, title, code) => {
    api.updateRegistrationProfile.mockResolvedValueOnce({
      state: "ready_to_finalize",
      primary_purpose: "student",
      masked_identifier: "s***@example.com",
      next_action: "finalize",
      expires_at: "2026-09-16T10:00:00+08:00",
      request_id: "registration-profile-write-before-finalize-error",
    })
    api.finalizeRegistration.mockRejectedValueOnce(
      new ProblemDetailsError({
        type: "about:blank",
        title,
        status,
        detail: "private session details",
        code,
        request_id: "registration-finalize-session-boundary",
      }),
    )
    const user = userEvent.setup()
    renderProfile("student")
    await screen.findByText("s***@example.com")
    await user.type(screen.getByLabelText("姓名"), "敏感姓名")
    await user.type(screen.getByLabelText("身份证"), "110105199001021238")
    await user.type(screen.getByLabelText("密码", { exact: true }), "Aa1-valid")
    await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
    await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))

    await user.click(screen.getByRole("button", { name: "提交实名资料" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("注册会话不可用")
    expect(screen.getByRole("alert")).not.toHaveTextContent("private session details")
    expect(screen.queryByRole("button", { name: "重试完成注册" })).not.toBeInTheDocument()
    expect(screen.queryByText("实名资料已保存，请重试完成注册")).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue("敏感姓名")).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue("110105199001021238")).not.toBeInTheDocument()
    expect(screen.queryByDisplayValue("Aa1-valid")).not.toBeInTheDocument()
  },
)

it("derives birth and sex, submits staff fields, and never stores secrets", async () => {
  const setItem = vi.spyOn(Storage.prototype, "setItem")
  const user = userEvent.setup()
  renderProfile()

  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册教练")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  expect(screen.getByText(/1990-01-02/)).toBeVisible()
  expect(screen.getByText(/男/)).toBeVisible()
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.selectOptions(screen.getByLabelText("申请岗位"), "coach")
  await user.type(screen.getByLabelText("申请说明"), "拥有青训教学经验")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(api.updateRegistrationProfile).toHaveBeenCalledWith({
    password: "Aa1-valid",
    full_name: "注册教练",
    identity_number: "110105199001021238",
    sex: "male",
    date_of_birth: "1990-01-02",
    consent_version: "2026-08",
    purpose_details: {
      requested_staff_type: "coach",
      staff_application_reason: "拥有青训教学经验",
      visit_reason: null,
    },
  })
  expect(api.finalizeRegistration).toHaveBeenCalledTimes(1)
  expect(await screen.findByText("资料已提交审核")).toBeVisible()
  expect(setItem).not.toHaveBeenCalled()
})

it("explains an invalid identity checksum before submitting", async () => {
  const user = userEvent.setup()
  renderProfile("student")

  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册学员")
  await user.type(screen.getByLabelText("身份证"), "320411199306133415")
  expect(screen.getByText("身份证号码校验码不正确，请检查最后一位")).toBeVisible()
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(screen.getAllByText("身份证号码校验码不正确，请检查最后一位").length).toBe(2)
  expect(api.updateRegistrationProfile).not.toHaveBeenCalled()
})

it("translates server validation errors into a specific safe reason", async () => {
  api.updateRegistrationProfile.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Unprocessable Entity",
      status: 422,
      detail: "private identity details",
      code: "validation_error",
      errors: [{ field: "body.identity_number", message: "身份证校验码不正确" }],
      request_id: "profile-validation-error",
    }),
  )
  const user = userEvent.setup()
  renderProfile("student")

  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册学员")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "身份证号码校验码不正确，请检查最后一位",
  )
  expect(screen.getByRole("alert")).not.toHaveTextContent("private identity details")
})

it("explains a profile conflict without exposing server details", async () => {
  api.updateRegistrationProfile.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "database constraint uq_registration_applications_active_identity",
      code: "registration_profile_conflict",
      request_id: "profile-conflict",
    }),
  )
  const user = userEvent.setup()
  renderProfile("student")

  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册学员")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "当前申请与已有注册申请发生冲突",
  )
  expect(screen.getByRole("alert")).not.toHaveTextContent("uq_registration")
})

it("requires a staff application reason before sending the request", async () => {
  const user = userEvent.setup()
  renderProfile()

  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册教练")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.selectOptions(screen.getByLabelText("申请岗位"), "coach")
  await user.type(screen.getByLabelText("申请说明"), "一")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "请填写申请说明（至少 2 个字符）",
  )
  expect(api.updateRegistrationProfile).not.toHaveBeenCalled()
})

it("shows only the visitor reason for a visitor application", async () => {
  renderProfile("visitor")

  await screen.findByText("s***@example.com")
  expect(screen.getByLabelText("来访说明")).toBeVisible()
  expect(screen.queryByLabelText("申请岗位")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("申请说明")).not.toBeInTheDocument()
})

it("keeps private server details out of the profile error", async () => {
  api.getRegistrationMe.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Not Found",
      status: 404,
      detail: "application 110105199001021234 not found",
      code: "registration_action_unavailable",
      request_id: "private-profile-error",
    }),
  )
  renderProfile("student")

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "注册会话不可用，请重新开始",
  )
  expect(screen.getByRole("alert")).not.toHaveTextContent(
    "110105199001021234",
  )
})

it("prevents double submit while the profile write is pending", async () => {
  let release!: () => void
  api.updateRegistrationProfile.mockImplementationOnce(
    () => new Promise((resolve) => {
      release = () => resolve({
        state: "ready_to_finalize",
        masked_identifier: "s***@example.com",
        next_action: "finalize",
        expires_at: "2026-09-16T10:00:00+08:00",
        request_id: "registration-profile-write",
        primary_purpose: "student",
      })
    }),
  )
  const user = userEvent.setup()
  renderProfile("student")
  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册学员")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  const submit = screen.getByRole("button", { name: "提交实名资料" })

  await user.click(submit)

  expect(submit).toBeDisabled()
  expect(api.updateRegistrationProfile).toHaveBeenCalledTimes(1)
  release()
  await waitFor(() => expect(api.finalizeRegistration).toHaveBeenCalledTimes(1))
})

it("retries finalization without writing saved identity data a second time", async () => {
  api.finalizeRegistration
    .mockRejectedValueOnce(new Error("temporary finalize failure"))
    .mockResolvedValueOnce({
      state: "finalized",
      next_action: "login",
      request_id: "registration-finalized",
    })
  const user = userEvent.setup()
  renderProfile("student")
  await screen.findByText("s***@example.com")
  await user.type(screen.getByLabelText("姓名"), "注册学员")
  await user.type(screen.getByLabelText("身份证"), "110105199001021238")
  await user.type(screen.getByLabelText("密码"), "Aa1-valid")
  await user.type(screen.getByLabelText("确认密码"), "Aa1-valid")
  await user.click(screen.getByRole("checkbox", { name: /同意实名资料处理条款/ }))
  await user.click(screen.getByRole("button", { name: "提交实名资料" }))

  expect(await screen.findByText("实名资料已保存，请重试完成注册")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重试完成注册" }))

  expect(api.updateRegistrationProfile).toHaveBeenCalledTimes(1)
  expect(api.finalizeRegistration).toHaveBeenCalledTimes(2)
  expect(await screen.findByText("账号注册完成")).toBeVisible()
})
