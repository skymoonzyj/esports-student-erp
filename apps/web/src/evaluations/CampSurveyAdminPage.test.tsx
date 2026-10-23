import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useNavigationType } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { getAdminSurveyFeedback } from "./surveyApi"
import { surveyKeys } from "./surveyKeys"
import { CampSurveyAdminPage } from "./CampSurveyAdminPage"

const accountId = "10000000-0000-4000-8000-000000000020"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: vi.fn(() => ({
    user: {
      account_id: accountId,
      permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
      scoped_permissions: [],
    },
  })),
}))

vi.mock("./surveyApi", () => ({
  getAdminSurveyFeedback: vi.fn(),
}))

const feedback = {
  items: [
    {
      cycle_id: "00000000-0000-4000-8000-000000000001",
      enrollment_id: "00000000-0000-4000-8000-000000000002",
      cohort_id: "00000000-0000-4000-8000-000000000003",
      version_id: "00000000-0000-4000-8000-000000000004",
      version: 1,
      ordinal: 1,
      staff_snapshot_id: "00000000-0000-4000-8000-000000000005",
      staff_person_id: "00000000-0000-4000-8000-000000000006",
      staff_name: "教练甲",
      role_key: "coach",
      role_name: "教练",
      role_category: "teaching",
      score: 10,
      reason: "反馈具体",
      suggestion: "继续",
      respondent_label: "学员甲",
      respondent_person_id: "00000000-0000-4000-8000-000000000007",
      respondent_name: "学员甲",
      respondent_kind: "self",
      course_training_score: 9,
      course_training_reason: "课程原因",
      life_service_score: 8,
      life_service_reason: "生活原因",
      service_management_score: 9,
      service_management_reason: "管理原因",
      overall_suggestion: "总体建议",
      favorite_cuisines: [{ name: "川菜" }],
      favorite_dishes: ["宫保鸡丁"],
    },
  ],
  total: 1,
  page: 1,
  page_size: 50,
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <CampSurveyAdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function privateFailure(status: 403 | 404): ProblemDetailsError {
  return new ProblemDetailsError({
    type: "about:blank",
    title: status === 403 ? "Forbidden" : "Not Found",
    status,
    detail: "实名评价访问已撤销",
    code: status === 403 ? "permission_denied" : "resource_not_found",
    request_id: `survey-admin-${status}`,
  })
}

function SafeEvaluationTarget() {
  const navigationType = useNavigationType()
  return <main><h1>评价中心安全页</h1><p>导航方式：{navigationType}</p></main>
}

afterEach(() => vi.clearAllMocks())

it("keeps admin feedback anonymous by default and applies scope filters", async () => {
  vi.mocked(getAdminSurveyFeedback).mockResolvedValue(feedback)
  const user = userEvent.setup()

  renderPage()

  expect(await screen.findByRole("heading", { name: "营期评价管理" })).toBeVisible()
  expect(screen.getByText("教练甲 · 匿名评价 #1")).toBeVisible()

  await user.type(screen.getByLabelText("届次 ID"), "cohort-filter")
  await user.type(screen.getByLabelText("项目 ID"), "project-filter")
  await user.type(screen.getByLabelText("角色 key"), "primary_coach")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))

  await waitFor(() =>
    expect(getAdminSurveyFeedback).toHaveBeenLastCalledWith(
      expect.objectContaining({
        cohortId: "cohort-filter",
        projectId: "project-filter",
        roleKey: "primary_coach",
        identity: "hidden",
      }),
      expect.anything(),
    ),
  )
})

it("shows the real-name switch only with capability and refreshes the result", async () => {
  vi.mocked(getAdminSurveyFeedback).mockResolvedValue(feedback)
  const user = userEvent.setup()

  renderPage()

  await screen.findByRole("heading", { name: "营期评价管理" })
  const identity = screen.getByRole("checkbox", { name: "查看实名" })
  expect(identity).not.toBeChecked()
  await user.click(identity)

  await waitFor(() =>
    expect(getAdminSurveyFeedback).toHaveBeenLastCalledWith(
      expect.objectContaining({ identity: "real_name" }),
      expect.anything(),
    ),
  )
  expect(await screen.findByText("学员甲 · 教练甲")).toBeVisible()
})

it("offers recovery when the admin query fails", async () => {
  vi.mocked(getAdminSurveyFeedback)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(feedback)

  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("无权访问或数据暂时不可用")
  await userEvent.click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByText("教练甲 · 匿名评价 #1")).toBeVisible()
})

it("loads the next admin feedback page", async () => {
  vi.mocked(getAdminSurveyFeedback).mockImplementation(async (filters) => ({
    ...feedback,
    page: filters?.page ?? 1,
    total: 51,
  }))
  const user = userEvent.setup()
  renderPage()

  await screen.findByText("教练甲 · 匿名评价 #1")
  await user.click(screen.getByRole("button", { name: "下一页评价" }))

  await waitFor(() => expect(getAdminSurveyFeedback).toHaveBeenLastCalledWith(
    expect.objectContaining({ page: 2, pageSize: 50 }),
    expect.anything(),
  ))

  await user.type(screen.getByLabelText("届次 ID"), "new-cohort-filter")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))
  await waitFor(() => expect(getAdminSurveyFeedback).toHaveBeenLastCalledWith(
    expect.objectContaining({ cohortId: "new-cohort-filter", page: 1 }),
    expect.anything(),
  ))
})

it("offers real-name mode without exposing print when only identity permission is present", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000020",
      full_name: "测试管理员",
      email: "survey-admin@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.identity.read"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  vi.mocked(getAdminSurveyFeedback).mockResolvedValue(feedback)
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("heading", { name: "营期评价管理" })
  const identity = screen.getByRole("checkbox", { name: "查看实名" })
  expect(identity).toBeVisible()
  await user.click(identity)
  await waitFor(() => expect(getAdminSurveyFeedback).toHaveBeenLastCalledWith(
    expect.objectContaining({ identity: "real_name" }),
    expect.anything(),
  ))
  expect(screen.queryByRole("link", { name: "打开打印投影" })).not.toBeInTheDocument()
})

it("downgrades an active real-name view and purges its account cache after identity access is revoked", async () => {
  let currentUser = {
    account_id: accountId,
    permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
    scoped_permissions: [] as string[],
  }
  vi.mocked(useAuth).mockImplementation(() => ({ user: currentUser }) as ReturnType<typeof useAuth>)
  vi.mocked(getAdminSurveyFeedback)
    .mockResolvedValueOnce(feedback)
    .mockResolvedValue({ ...feedback, items: feedback.items.map((item) => ({ ...item, respondent_name: null })) })
  const user = userEvent.setup()
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")
  const removeQueries = vi.spyOn(queryClient, "removeQueries")
  const { rerender } = render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <CampSurveyAdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("heading", { name: "营期评价管理" })
  await user.click(screen.getByRole("checkbox", { name: "查看实名" }))
  expect(await screen.findByText("学员甲 · 教练甲")).toBeVisible()

  currentUser = { account_id: accountId, permissions: [], scoped_permissions: [] }
  rerender(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <CampSurveyAdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await waitFor(() => expect(getAdminSurveyFeedback).toHaveBeenLastCalledWith(
    expect.objectContaining({ identity: "hidden" }),
    expect.anything(),
  ))
  expect(screen.queryByText("学员甲 · 教练甲")).not.toBeInTheDocument()
  expect(screen.queryByRole("checkbox", { name: "查看实名" })).not.toBeInTheDocument()
  expect(cancelQueries).toHaveBeenCalled()
  expect(removeQueries).toHaveBeenCalled()
  expect(queryClient.getQueryData(surveyKeys.admin(accountId, { identity: "real_name" }))).toBeUndefined()
})

it("closes real-name state and replaces the route when the server revokes admin access", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000020",
      full_name: "测试管理员",
      email: "survey-admin@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  vi.mocked(getAdminSurveyFeedback).mockImplementation(async (filters) => {
    if (filters?.identity === "real_name") throw privateFailure(403)
    return feedback
  })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const otherAccountId = "10000000-0000-4000-8000-000000000099"
  queryClient.setQueryData(surveyKeys.admin(accountId, { identity: "real_name" }), feedback)
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { respondent_name: "其他账号保留" })
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={["/evaluations/surveys/admin"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys/admin" element={<CampSurveyAdminPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await screen.findByRole("heading", { name: "营期评价管理" })
  await user.type(screen.getByLabelText("届次 ID"), "撤权前实名筛选")
  await user.click(screen.getByRole("checkbox", { name: "查看实名" }))

  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
  expect(screen.getByText("导航方式：REPLACE")).toBeVisible()
  expect(screen.queryByText("学员甲 · 教练甲")).not.toBeInTheDocument()
  expect(screen.queryByRole("checkbox", { name: "查看实名" })).not.toBeInTheDocument()
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: surveyKeys.root(accountId) })
  await waitFor(() => expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0))
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ respondent_name: "其他账号保留" })
})

it("does not reuse fresh admin feedback after the protected page unmounts", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000020",
      full_name: "测试管理员",
      email: "survey-admin@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  })
  const otherAccountId = "10000000-0000-4000-8000-000000000099"
  queryClient.setQueryData(surveyKeys.admin(accountId, { identity: "hidden" }), feedback)
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { respondent_name: "其他账号保留" })
  vi.mocked(getAdminSurveyFeedback).mockResolvedValue(feedback)
  const renderProtectedAdmin = () => render(
    <MemoryRouter initialEntries={["/evaluations/surveys/admin"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys/admin" element={<CampSurveyAdminPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const firstView = renderProtectedAdmin()
  expect(await screen.findByRole("heading", { name: "营期评价管理" })).toBeVisible()

  firstView.unmount()
  expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ respondent_name: "其他账号保留" })

  vi.mocked(getAdminSurveyFeedback).mockRejectedValueOnce(privateFailure(403))
  renderProtectedAdmin()
  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
})
