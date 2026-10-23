import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useNavigationType } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { getMySurveyFeedback } from "./surveyApi"
import { MySurveyFeedbackPage } from "./MySurveyFeedbackPage"
import { surveyKeys } from "./surveyKeys"

const accountId = "10000000-0000-4000-8000-000000000010"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

vi.mock("./surveyApi", () => ({
  getMySurveyFeedback: vi.fn(),
}))

const feedback = {
  items: [
    {
      cycle_id: "00000000-0000-4000-8000-000000000001",
      cohort_id: "00000000-0000-4000-8000-000000000002",
      version: 2,
      ordinal: 1,
      role_category: "teaching",
      score: 9,
      reason: "讲解清楚",
      suggestion: "继续保持",
      course_training_score: 8,
      course_training_reason: "课程原因",
      life_service_score: null,
      life_service_reason: null,
      service_management_score: null,
      service_management_reason: null,
      team_suggestion: "课程节奏很好",
      project_id: "00000000-0000-4000-8000-000000000003",
      role_key: "primary_coach",
      role_name: "主教练",
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
        <MySurveyFeedbackPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function privateFailure(status: 403 | 404): ProblemDetailsError {
  return new ProblemDetailsError({
    type: "about:blank",
    title: status === 403 ? "Forbidden" : "Not Found",
    status,
    detail: "匿名反馈访问已撤销",
    code: status === 403 ? "permission_denied" : "resource_not_found",
    request_id: `survey-feedback-${status}`,
  })
}

function SafeEvaluationTarget() {
  const navigationType = useNavigationType()
  return <main><h1>评价中心安全页</h1><p>导航方式：{navigationType}</p></main>
}

afterEach(() => vi.clearAllMocks())

it("renders the staff privacy projection and its teaching-only summary", async () => {
  vi.mocked(getMySurveyFeedback).mockResolvedValue(feedback)

  renderPage()

  expect(await screen.findByRole("heading", { name: "收到的匿名反馈" })).toBeVisible()
  expect(screen.getByText("评分 9/10")).toBeVisible()
  expect(screen.getByText("原因：讲解清楚")).toBeVisible()
  expect(screen.getByText("给工作人员的建议：继续保持")).toBeVisible()
  expect(screen.getByText("课程训练总体评分：8/10")).toBeVisible()
  expect(screen.getByText("课程训练评分原因：课程原因")).toBeVisible()
  expect(screen.getByText("给教学团队的建议：课程节奏很好")).toBeVisible()
  expect(screen.queryByText(/staff_person|匿名评价/)).not.toBeInTheDocument()
})

it("renders only the matching life-service and operations summaries", async () => {
  const roleFeedback = {
    ...feedback,
    items: [
      {
        ...feedback.items[0],
        role_category: "life_service",
        course_training_score: null,
        course_training_reason: null,
        life_service_score: 7,
        life_service_reason: "生活照护原因",
        service_management_score: null,
        service_management_reason: null,
        team_suggestion: null,
      },
      {
        ...feedback.items[0],
        ordinal: 2,
        role_category: "operations",
        course_training_score: null,
        course_training_reason: null,
        life_service_score: null,
        life_service_reason: null,
        service_management_score: 6,
        service_management_reason: "服务管理原因",
        team_suggestion: null,
      },
    ],
  }
  vi.mocked(getMySurveyFeedback).mockResolvedValue(roleFeedback)

  renderPage()

  expect(await screen.findByText("住宿生活总体评分：7/10")).toBeVisible()
  expect(screen.getByText("住宿生活评分原因：生活照护原因")).toBeVisible()
  expect(screen.getByText("服务管理总体评分：6/10")).toBeVisible()
  expect(screen.getByText("服务管理评分原因：服务管理原因")).toBeVisible()
  expect(screen.queryByText(/课程训练总体评分/)).not.toBeInTheDocument()
})

it("offers a retry action after a feedback request fails", async () => {
  vi.mocked(getMySurveyFeedback)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(feedback)

  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("反馈暂时不可用")
  await userEvent.click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByText("评分 9/10")).toBeVisible()
})

it("loads the next staff feedback page", async () => {
  vi.mocked(getMySurveyFeedback).mockImplementation(async (page) => {
    const resolvedPage = page ?? 1
    return {
      ...feedback,
      page: resolvedPage,
      total: 51,
      items: [{
        ...feedback.items[0],
        ordinal: resolvedPage === 1 ? 1 : 51,
        score: resolvedPage === 1 ? 9 : 7,
      }],
    }
  })
  const user = userEvent.setup()
  renderPage()

  await screen.findByText("评分 9/10")
  await user.click(screen.getByRole("button", { name: "下一页反馈" }))

  expect(await screen.findByText("评分 7/10")).toBeVisible()
  expect(getMySurveyFeedback).toHaveBeenLastCalledWith(2, 50, expect.anything(), {})

  await user.click(screen.getByRole("button", { name: "上一页反馈" }))
  expect(await screen.findByText("评分 9/10")).toBeVisible()
  expect(getMySurveyFeedback).toHaveBeenLastCalledWith(1, 50, expect.anything(), {})
})

it("replaces revoked feedback access and keeps another account's survey cache", async () => {
  vi.mocked(getMySurveyFeedback).mockRejectedValueOnce(privateFailure(403))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const otherAccountId = "10000000-0000-4000-8000-000000000099"
  queryClient.setQueryData([...surveyKeys.root(accountId), "private-seed"], { feedback: "当前账号" })
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { feedback: "其他账号" })
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")

  render(
    <MemoryRouter initialEntries={["/evaluations/surveys/feedback"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys/feedback" element={<MySurveyFeedbackPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
  expect(screen.getByText("导航方式：REPLACE")).toBeVisible()
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: surveyKeys.root(accountId) })
  await waitFor(() => expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0))
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ feedback: "其他账号" })
})

it("does not reuse fresh feedback after the protected page unmounts", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 30_000 } },
  })
  const otherAccountId = "10000000-0000-4000-8000-000000000099"
  queryClient.setQueryData(surveyKeys.feedback(accountId), feedback)
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { feedback: "其他账号" })
  const renderProtectedFeedback = () => render(
    <MemoryRouter initialEntries={["/evaluations/surveys/feedback"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys/feedback" element={<MySurveyFeedbackPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const firstView = renderProtectedFeedback()
  expect(await screen.findByRole("heading", { name: "收到的匿名反馈" })).toBeVisible()
  expect(getMySurveyFeedback).not.toHaveBeenCalled()

  firstView.unmount()
  expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ feedback: "其他账号" })

  vi.mocked(getMySurveyFeedback).mockRejectedValueOnce(privateFailure(403))
  renderProtectedFeedback()
  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
})
