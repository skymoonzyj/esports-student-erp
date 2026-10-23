import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect, useReducer } from "react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { registrationKeys } from "./keys"
import { StaffApplicationsAdminPage } from "./StaffApplicationsAdminPage"

const auth = vi.hoisted(() => ({
  status: "authenticated" as "authenticated" | "loading",
  permissions: ["staff_identity_applications.review"] as string[],
  refreshUser: vi.fn(),
  notify: () => {},
}))
const api = vi.hoisted(() => ({
  listStaffApplications: vi.fn(),
  getStaffApplication: vi.fn(),
  reviewStaffApplication: vi.fn(),
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: auth.status,
    user: {
      account_id: "account-staff-reviewer",
      person_id: "person-staff-reviewer",
      full_name: "身份审核员",
      email: null,
      force_password_change: false,
      permissions: auth.permissions,
      scoped_permissions: [],
    },
    refreshUser: auth.refreshUser,
  }),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  ...api,
}))

const application = {
  application_id: "10000000-0000-4000-8000-000000000171",
  requested_staff_type: "coach" as const,
  status: "pending" as const,
  created_at: "2026-08-16T10:00:00+08:00",
}

const sensitiveApplication = {
  ...application,
  application_reason: "有职业队青训经验",
  person_id: "20000000-0000-4000-8000-000000000171",
  full_name: "申请教练",
  identity_number: "110105199001021238",
  sex: "male" as const,
  date_of_birth: "1990-01-02",
  phone: "13800007771",
  email: "coach-applicant@example.com",
}

function StaffAuthBoundary() {
  const [, forceRender] = useReducer((revision: number) => revision + 1, 0)
  useEffect(() => {
    auth.notify = forceRender
    return () => {
      auth.notify = () => {}
    }
  }, [])
  return auth.status === "loading"
    ? <p role="status">正在刷新权限…</p>
    : <StaffApplicationsAdminPage />
}

function renderPage(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <MemoryRouter initialEntries={["/settings/staff-applications"]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/settings/staff-applications" element={<StaffApplicationsAdminPage />} />
          <Route path="/" element={<h1>工作台</h1>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

function renderPageWithAuthBoundary(
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  render(
    <MemoryRouter initialEntries={["/settings/staff-applications"]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/settings/staff-applications" element={<StaffAuthBoundary />} />
          <Route path="/" element={<h1>工作台</h1>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  auth.status = "authenticated"
  auth.permissions = ["staff_identity_applications.review"]
  auth.notify = () => {}
  auth.refreshUser.mockReset().mockResolvedValue(undefined)
  api.listStaffApplications.mockReset().mockResolvedValue([application])
  api.getStaffApplication.mockReset().mockResolvedValue({
    ...application,
    application_reason: "有职业队青训经验",
  })
  api.reviewStaffApplication.mockReset().mockResolvedValue({
    application_id: application.application_id,
    status: "approved",
    requested_staff_type: "coach",
    assigned_role: "coach",
    request_id: "staff-approved",
  })
})

it("separates staff review capability from sensitive identity reading", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText("教练")).toBeVisible()
  expect(screen.queryByRole("button", { name: "查看完整资料" })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "处理申请" }))
  expect(await screen.findByText("有职业队青训经验")).toBeVisible()
  expect(screen.queryByText("110105199001021238")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "批准申请" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "驳回申请" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "批准申请" }))

  expect(api.reviewStaffApplication).toHaveBeenCalledWith(
    application.application_id,
    "approve",
    null,
  )
})

it("still requires a review note when rejecting an application", async () => {
  const user = userEvent.setup()
  api.reviewStaffApplication.mockResolvedValueOnce({
    application_id: application.application_id,
    status: "rejected",
    requested_staff_type: "coach",
    assigned_role: null,
    request_id: "staff-rejected",
  })
  renderPage()

  await screen.findByText("教练")
  await user.click(screen.getByRole("button", { name: "处理申请" }))
  const rejectButton = await screen.findByRole("button", { name: "驳回申请" })
  expect(rejectButton).toBeDisabled()
  await user.type(
    screen.getByLabelText("审核备注（批准选填，驳回必填）"),
    "申请资料不完整",
  )
  expect(rejectButton).toBeEnabled()
  await user.click(rejectButton)

  expect(api.reviewStaffApplication).toHaveBeenCalledWith(
    application.application_id,
    "reject",
    "申请资料不完整",
  )
})

it("shows full identity only when sensitive read is separately granted", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  api.getStaffApplication.mockResolvedValueOnce(sensitiveApplication)
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("教练")

  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("110105199001021238")).toBeVisible()
  expect(screen.getByText("13800007771")).toBeVisible()
})

it("purges displayed and cached staff identity before a manual permission refresh completes", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  api.getStaffApplication.mockResolvedValueOnce(sensitiveApplication)
  const user = userEvent.setup()
  const client = renderPage()
  await screen.findByText("教练")
  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007771")).toBeVisible()
  client.setQueryData(
    registrationKeys.staffAdminApplication(application.application_id, true),
    sensitiveApplication,
  )

  let finishRefresh: (() => void) | undefined
  auth.refreshUser.mockImplementationOnce(() => {
    client.clear()
    return new Promise<void>((resolve) => {
      finishRefresh = () => {
        auth.permissions = ["staff_identity_applications.review"]
        resolve()
      }
    })
  })
  api.listStaffApplications.mockClear()
  api.getStaffApplication.mockClear()

  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(auth.refreshUser).toHaveBeenCalledTimes(1)
  expect(screen.queryByText("13800007771")).not.toBeInTheDocument()
  expect(screen.queryByText("coach-applicant@example.com")).not.toBeInTheDocument()
  expect(
    client.getQueryData(
      registrationKeys.staffAdminApplication(application.application_id, true),
    ),
  ).toBeUndefined()
  expect(api.listStaffApplications).not.toHaveBeenCalled()
  expect(api.getStaffApplication).not.toHaveBeenCalled()

  finishRefresh?.()
  await waitFor(() => expect(api.listStaffApplications).toHaveBeenCalledTimes(1))
  expect(api.getStaffApplication).not.toHaveBeenCalled()
  expect(screen.queryByRole("heading", { name: "审核身份申请" })).not.toBeInTheDocument()
})

it("refreshes auth on window focus before reloading the current staff identity", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  api.getStaffApplication.mockResolvedValueOnce(sensitiveApplication)
  renderPage()
  await screen.findByText("教练")
  await userEvent.setup().click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007771")).toBeVisible()

  let finishRefresh: (() => void) | undefined
  auth.refreshUser.mockImplementationOnce(() => new Promise<void>((resolve) => {
    finishRefresh = resolve
  }))
  api.listStaffApplications.mockClear()
  api.getStaffApplication.mockReset().mockResolvedValue({
    ...sensitiveApplication,
    phone: "13900008891",
  })

  window.dispatchEvent(new Event("focus"))

  await waitFor(() => expect(auth.refreshUser).toHaveBeenCalledTimes(1))
  expect(screen.queryByText("13800007771")).not.toBeInTheDocument()
  expect(api.listStaffApplications).not.toHaveBeenCalled()
  expect(api.getStaffApplication).not.toHaveBeenCalled()

  finishRefresh?.()
  expect(await screen.findByText("13900008891")).toBeVisible()
  expect(api.listStaffApplications).toHaveBeenCalledTimes(1)
  expect(api.getStaffApplication).toHaveBeenCalledWith(application.application_id)
})

it("resumes the current staff detail refresh after the auth loading boundary remounts", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  api.getStaffApplication.mockResolvedValueOnce(sensitiveApplication)
  const user = userEvent.setup()
  const client = renderPageWithAuthBoundary()
  await screen.findByText("教练")
  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007771")).toBeVisible()

  let finishRefresh: (() => void) | undefined
  auth.refreshUser.mockImplementationOnce(async () => {
    auth.status = "loading"
    auth.notify()
    client.clear()
    await new Promise<void>((resolve) => {
      finishRefresh = resolve
    })
    auth.permissions = [
      "staff_identity_applications.review",
      "registration.sensitive.read",
    ]
    auth.status = "authenticated"
    auth.notify()
  })
  api.listStaffApplications.mockClear()
  api.getStaffApplication.mockReset().mockResolvedValue({
    ...sensitiveApplication,
    phone: "13900008892",
  })

  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(await screen.findByRole("status")).toHaveTextContent("正在刷新权限")
  expect(screen.queryByText("13800007771")).not.toBeInTheDocument()
  expect(api.listStaffApplications).not.toHaveBeenCalled()
  expect(api.getStaffApplication).not.toHaveBeenCalled()

  finishRefresh?.()
  expect(await screen.findByText("13900008892")).toBeVisible()
  expect(api.listStaffApplications).toHaveBeenCalledTimes(1)
  expect(api.getStaffApplication).toHaveBeenCalledWith(application.application_id)
})

it("does not restore an in-flight staff identity after a focus refresh", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  let finishDetail: ((value: typeof sensitiveApplication) => void) | undefined
  api.getStaffApplication.mockImplementationOnce(() => new Promise((resolve) => {
    finishDetail = resolve
  }))
  renderPage()
  await screen.findByText("教练")
  await userEvent.setup().click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(api.getStaffApplication).toHaveBeenCalledTimes(1)
  api.listStaffApplications.mockClear()

  window.dispatchEvent(new Event("focus"))

  await waitFor(() => expect(api.listStaffApplications).toHaveBeenCalledTimes(1))
  expect(screen.getByRole("button", { name: "查看完整资料" })).toBeEnabled()
  finishDetail?.(sensitiveApplication)
  await waitFor(() => {
    expect(screen.queryByText("13800007771")).not.toBeInTheDocument()
  })
})

it("keeps the staff reviewer on the page when permission refresh fails after purging identity", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  api.getStaffApplication.mockResolvedValueOnce(sensitiveApplication)
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("教练")
  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007771")).toBeVisible()
  auth.refreshUser.mockRejectedValueOnce(new TypeError("network unavailable"))
  api.listStaffApplications.mockClear()
  api.getStaffApplication.mockClear()

  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法刷新权限")
  expect(screen.getByRole("heading", { name: "身份申请" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "工作台" })).not.toBeInTheDocument()
  expect(screen.queryByText("13800007771")).not.toBeInTheDocument()
  expect(api.listStaffApplications).not.toHaveBeenCalled()
  expect(api.getStaffApplication).not.toHaveBeenCalled()
})

it("uses the existing private-revocation flow when staff review is removed", async () => {
  auth.permissions = [
    "staff_identity_applications.review",
    "registration.sensitive.read",
  ]
  auth.refreshUser.mockImplementationOnce(async () => {
    auth.permissions = []
  })
  const client = renderPage()
  expect(await screen.findByText("教练")).toBeVisible()

  await userEvent.setup().click(screen.getByRole("button", { name: "刷新" }))

  await waitFor(() => {
    expect(client.getQueryCache().findAll({ queryKey: registrationKeys.staffAdminRoot() })).toHaveLength(0)
  })
  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "身份申请" })).not.toBeInTheDocument()
})

it("clears only staff administration cache on private object revocation", async () => {
  const client = renderPage()
  expect(await screen.findByText("教练")).toBeVisible()
  api.getStaffApplication.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Not Found",
      status: 404,
      detail: "private applicant detail",
      code: "staff_application_not_found",
      request_id: "staff-private",
    }),
  )
  const user = userEvent.setup()
  await user.click(screen.getByRole("button", { name: "处理申请" }))

  await waitFor(() => {
    expect(client.getQueryCache().findAll({ queryKey: registrationKeys.staffAdminRoot() })).toHaveLength(0)
  })
  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "身份申请" })).not.toBeInTheDocument()
})
