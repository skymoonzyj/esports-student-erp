import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect, useReducer } from "react"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { registrationKeys } from "./keys"
import { RegistrationAdminPage } from "./RegistrationAdminPage"

const auth = vi.hoisted(() => ({
  status: "authenticated" as "authenticated" | "loading",
  permissions: ["registration.review"] as string[],
  refreshUser: vi.fn(),
  notify: () => {},
}))
const api = vi.hoisted(() => ({
  getRegistrationChannelSettings: vi.fn(),
  listRegistrationConflicts: vi.fn(),
  getRegistrationConflict: vi.fn(),
  rejectRegistrationConflict: vi.fn(),
  resolveRegistrationConflict: vi.fn(),
  saveRegistrationChannelSettings: vi.fn(),
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: auth.status,
    user: {
      account_id: "account-reviewer",
      person_id: "person-reviewer",
      full_name: "注册审核员",
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

const conflict = {
  application_id: "10000000-0000-4000-8000-000000000161",
  primary_purpose: "student" as const,
  state: "conflict" as const,
  masked_identifier: "138****7761",
  conflict_code: "multiple_person_match",
  candidate_count: 1,
  expires_at: "2026-09-16T10:00:00+08:00",
  created_at: "2026-08-16T10:00:00+08:00",
}

const sensitiveConflict = {
  ...conflict,
  full_name: "冲突学员",
  identity_number: "110105199001021238",
  sex: "male" as const,
  date_of_birth: "1990-01-02",
  verified_identifier: "13800007761",
  candidates: [{
    person_id: "20000000-0000-4000-8000-000000000161",
    full_name: "候选学员",
    identity_number: "110105199001021238",
    sex: "male" as const,
    date_of_birth: "1990-01-02",
    deleted: false,
  }],
}

function LocationProbe() {
  return <span data-testid="location">{useLocation().pathname}</span>
}

function RegistrationAuthBoundary() {
  const [, forceRender] = useReducer((revision: number) => revision + 1, 0)
  useEffect(() => {
    auth.notify = forceRender
    return () => {
      auth.notify = () => {}
    }
  }, [])
  return auth.status === "loading"
    ? <p role="status">正在刷新权限…</p>
    : <RegistrationAdminPage />
}

function renderPage(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <MemoryRouter initialEntries={["/settings/registrations"]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/settings/registrations" element={<RegistrationAdminPage />} />
          <Route path="/" element={<><h1>工作台</h1><LocationProbe /></>} />
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
    <MemoryRouter initialEntries={["/settings/registrations"]}>
      <QueryClientProvider client={client}>
        <Routes>
          <Route path="/settings/registrations" element={<RegistrationAuthBoundary />} />
          <Route path="/" element={<><h1>工作台</h1><LocationProbe /></>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  auth.status = "authenticated"
  auth.permissions = ["registration.review"]
  auth.notify = () => {}
  auth.refreshUser.mockReset().mockResolvedValue(undefined)
  api.getRegistrationChannelSettings.mockReset().mockResolvedValue({
    phone_enabled: false,
    email_enabled: true,
    phone_signup_allowed_by_deployment: true,
    updated_at: null,
    request_id: "registration-channels",
  })
  api.listRegistrationConflicts.mockReset().mockResolvedValue([conflict])
  api.getRegistrationConflict.mockReset().mockResolvedValue(conflict)
  api.rejectRegistrationConflict.mockReset().mockResolvedValue({
    state: "rejected",
    next_action: "closed",
    request_id: "registration-rejected",
  })
  api.resolveRegistrationConflict.mockReset()
  api.saveRegistrationChannelSettings.mockReset().mockImplementation(async (input) => ({
    ...input,
    phone_signup_allowed_by_deployment: true,
    updated_at: "2026-08-20T00:00:00Z",
    request_id: "registration-channels-saved",
  }))
})

it("lets a reviewer open or close phone and email registration channels", async () => {
  const user = userEvent.setup()
  renderPage()

  const phone = await screen.findByRole("checkbox", { name: "手机号申请账号" })
  const email = screen.getByRole("checkbox", { name: "邮箱申请账号" })
  expect(phone).not.toBeChecked()
  expect(email).toBeChecked()

  await user.click(email)
  expect(screen.getByText("两种申请方式均已关闭，普通用户将无法自助申请账号。"))
    .toBeVisible()
  await user.click(screen.getByRole("button", { name: "保存渠道设置" }))

  expect(api.saveRegistrationChannelSettings.mock.calls[0]?.[0]).toEqual({
    phone_enabled: false,
    email_enabled: false,
  })
  expect(await screen.findByText("账号申请渠道已保存")).toBeVisible()

  await user.click(phone)
  await user.click(screen.getByRole("button", { name: "保存渠道设置" }))
  expect(api.saveRegistrationChannelSettings.mock.calls.at(-1)?.[0]).toEqual({
    phone_enabled: true,
    email_enabled: false,
  })
})

it("keeps full-detail actions absent without sensitive-read permission", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText("138****7761")).toBeVisible()
  expect(screen.queryByRole("button", { name: "查看完整资料" })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "处理申请" }))
  expect(api.getRegistrationConflict).toHaveBeenCalledWith(conflict.application_id)
  expect(screen.queryByText("110105199001021238")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "驳回申请" })).toBeDisabled()
  await user.type(screen.getByLabelText("审核原因"), "实名资料需要线下核对")
  expect(screen.getByRole("button", { name: "驳回申请" })).toBeEnabled()
})

it("shows sensitive candidates and resolves only with the separate permission", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  api.getRegistrationConflict.mockResolvedValueOnce(sensitiveConflict)
  api.resolveRegistrationConflict.mockResolvedValueOnce({
    state: "finalized",
    next_action: "login",
    request_id: "registration-resolved",
  })
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("138****7761")

  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("110105199001021238")).toBeVisible()
  await user.click(screen.getByRole("radio", { name: /候选学员/ }))
  await user.type(screen.getByLabelText("审核原因"), "确认档案信息完全一致")
  await user.click(screen.getByRole("button", { name: "确认关联并开通" }))

  expect(api.resolveRegistrationConflict).toHaveBeenCalledWith(
    conflict.application_id,
    {
      candidate_person_id: "20000000-0000-4000-8000-000000000161",
      reason: "确认档案信息完全一致",
    },
  )
})

it("purges displayed and cached sensitive detail before a manual permission refresh completes", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  api.getRegistrationConflict.mockResolvedValueOnce(sensitiveConflict)
  const user = userEvent.setup()
  const client = renderPage()
  await screen.findByText("138****7761")
  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007761")).toBeVisible()
  client.setQueryData(
    registrationKeys.adminApplication(conflict.application_id, true),
    sensitiveConflict,
  )

  let finishRefresh: (() => void) | undefined
  auth.refreshUser.mockImplementationOnce(() => {
    client.clear()
    return new Promise<void>((resolve) => {
      finishRefresh = () => {
        auth.permissions = ["registration.review"]
        resolve()
      }
    })
  })
  api.listRegistrationConflicts.mockClear()
  api.getRegistrationConflict.mockClear()

  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(auth.refreshUser).toHaveBeenCalledTimes(1)
  expect(screen.queryByText("13800007761")).not.toBeInTheDocument()
  expect(screen.queryByRole("radio", { name: /候选学员/ })).not.toBeInTheDocument()
  expect(
    client.getQueryData(
      registrationKeys.adminApplication(conflict.application_id, true),
    ),
  ).toBeUndefined()
  expect(api.listRegistrationConflicts).not.toHaveBeenCalled()
  expect(api.getRegistrationConflict).not.toHaveBeenCalled()

  finishRefresh?.()
  await waitFor(() => expect(api.listRegistrationConflicts).toHaveBeenCalledTimes(1))
  expect(api.getRegistrationConflict).not.toHaveBeenCalled()
  expect(screen.queryByRole("heading", { name: "审核注册申请" })).not.toBeInTheDocument()
})

it("refreshes auth on window focus before reloading the current sensitive detail", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  api.getRegistrationConflict.mockResolvedValueOnce(sensitiveConflict)
  renderPage()
  await screen.findByText("138****7761")
  await userEvent.setup().click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007761")).toBeVisible()

  let finishRefresh: (() => void) | undefined
  auth.refreshUser.mockImplementationOnce(() => new Promise<void>((resolve) => {
    finishRefresh = resolve
  }))
  api.listRegistrationConflicts.mockClear()
  api.getRegistrationConflict.mockReset().mockResolvedValue({
    ...sensitiveConflict,
    verified_identifier: "13900008881",
  })

  window.dispatchEvent(new Event("focus"))

  await waitFor(() => expect(auth.refreshUser).toHaveBeenCalledTimes(1))
  expect(screen.queryByText("13800007761")).not.toBeInTheDocument()
  expect(api.listRegistrationConflicts).not.toHaveBeenCalled()
  expect(api.getRegistrationConflict).not.toHaveBeenCalled()

  finishRefresh?.()
  expect(await screen.findByText("13900008881")).toBeVisible()
  expect(api.listRegistrationConflicts).toHaveBeenCalledTimes(1)
  expect(api.getRegistrationConflict).toHaveBeenCalledWith(conflict.application_id)
})

it("resumes the current detail refresh after the auth loading boundary remounts", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  api.getRegistrationConflict.mockResolvedValueOnce(sensitiveConflict)
  const user = userEvent.setup()
  const client = renderPageWithAuthBoundary()
  await screen.findByText("138****7761")
  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007761")).toBeVisible()

  let finishRefresh: (() => void) | undefined
  auth.refreshUser.mockImplementationOnce(async () => {
    auth.status = "loading"
    auth.notify()
    client.clear()
    await new Promise<void>((resolve) => {
      finishRefresh = resolve
    })
    auth.permissions = ["registration.review", "registration.sensitive.read"]
    auth.status = "authenticated"
    auth.notify()
  })
  api.listRegistrationConflicts.mockClear()
  api.getRegistrationConflict.mockReset().mockResolvedValue({
    ...sensitiveConflict,
    verified_identifier: "13900008882",
  })

  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(await screen.findByRole("status")).toHaveTextContent("正在刷新权限")
  expect(screen.queryByText("13800007761")).not.toBeInTheDocument()
  expect(api.listRegistrationConflicts).not.toHaveBeenCalled()
  expect(api.getRegistrationConflict).not.toHaveBeenCalled()

  finishRefresh?.()
  expect(await screen.findByText("13900008882")).toBeVisible()
  expect(api.listRegistrationConflicts).toHaveBeenCalledTimes(1)
  expect(api.getRegistrationConflict).toHaveBeenCalledWith(conflict.application_id)
})

it("does not restore an in-flight sensitive detail after a focus refresh", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  let finishDetail: ((value: typeof sensitiveConflict) => void) | undefined
  api.getRegistrationConflict.mockImplementationOnce(() => new Promise((resolve) => {
    finishDetail = resolve
  }))
  renderPage()
  await screen.findByText("138****7761")
  await userEvent.setup().click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(api.getRegistrationConflict).toHaveBeenCalledTimes(1)
  api.listRegistrationConflicts.mockClear()

  window.dispatchEvent(new Event("focus"))

  await waitFor(() => expect(api.listRegistrationConflicts).toHaveBeenCalledTimes(1))
  expect(screen.getByRole("button", { name: "查看完整资料" })).toBeEnabled()
  finishDetail?.(sensitiveConflict)
  await waitFor(() => {
    expect(screen.queryByText("13800007761")).not.toBeInTheDocument()
  })
})

it("keeps the reviewer on the page when permission refresh fails after purging detail", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  api.getRegistrationConflict.mockResolvedValueOnce(sensitiveConflict)
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("138****7761")
  await user.click(screen.getByRole("button", { name: "查看完整资料" }))
  expect(await screen.findByText("13800007761")).toBeVisible()
  auth.refreshUser.mockRejectedValueOnce(new TypeError("network unavailable"))
  api.listRegistrationConflicts.mockClear()
  api.getRegistrationConflict.mockClear()

  await user.click(screen.getByRole("button", { name: "刷新" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法刷新权限")
  expect(screen.getByRole("heading", { name: "注册处理" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "工作台" })).not.toBeInTheDocument()
  expect(screen.queryByText("13800007761")).not.toBeInTheDocument()
  expect(api.listRegistrationConflicts).not.toHaveBeenCalled()
  expect(api.getRegistrationConflict).not.toHaveBeenCalled()
})

it("uses the existing private-revocation flow when registration review is removed", async () => {
  auth.permissions = ["registration.review", "registration.sensitive.read"]
  auth.refreshUser.mockImplementationOnce(async () => {
    auth.permissions = []
  })
  const client = renderPage()
  expect(await screen.findByText("138****7761")).toBeVisible()

  await userEvent.setup().click(screen.getByRole("button", { name: "刷新" }))

  await waitFor(() => {
    expect(client.getQueryCache().findAll({ queryKey: registrationKeys.adminRoot() })).toHaveLength(0)
  })
  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "注册处理" })).not.toBeInTheDocument()
})

it("removes registration administration immediately after revocation", async () => {
  const client = renderPage()
  expect(await screen.findByText("138****7761")).toBeVisible()
  api.listRegistrationConflicts.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      detail: "private reviewer detail",
      code: "registration_review_denied",
      request_id: "registration-revoked",
    }),
  )

  await userEvent.setup().click(screen.getByRole("button", { name: "刷新" }))

  await waitFor(() => {
    expect(client.getQueryCache().findAll({ queryKey: registrationKeys.adminRoot() })).toHaveLength(0)
  })
  expect(await screen.findByRole("heading", { name: "工作台" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "注册处理" })).not.toBeInTheDocument()
})
