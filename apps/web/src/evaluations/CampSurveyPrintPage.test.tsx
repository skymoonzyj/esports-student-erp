import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useNavigationType } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { getSurveyPrint } from "./surveyApi"
import { surveyKeys } from "./surveyKeys"
import { CampSurveyPrintPage } from "./CampSurveyPrintPage"

const accountId = "00000000-0000-4000-8000-000000000030"

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
  getSurveyPrint: vi.fn(),
}))

const projection = {
  identity: "hidden" as const,
  items: [
    {
      cohort_id: "00000000-0000-0000-0000-000000000031",
      project_id: null,
      staff_person_id: null,
      staff_name: null,
      role_key: "coach",
      role_name: "教练",
      role_category: "teaching",
      service_intervals: [{ from: "2026-01-01", to: "2026-02-01" }],
      average_score: 8.5,
      item_count: 2,
      details: [{ score: 9, reason: "清楚", suggestion: "继续", ordinal: 1, respondent_person_id: null, respondent_name: null, respondent_kind: null }],
      team_suggestions: ["增加复盘"],
      roles: [{ role_key: "coach", role_name: "教练", role_category: "teaching" }],
    },
  ],
}

const realNameProjection = {
  ...projection,
  identity: "real_name" as const,
  items: projection.items.map((item) => ({
    ...item,
    staff_person_id: "00000000-0000-0000-0000-000000000032",
    staff_name: "教练甲",
  })),
}

function renderPage(
  initialEntries = ["/evaluations/surveys/print"],
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
) {
  const view = render(
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={queryClient}>
        <CampSurveyPrintPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { ...view, queryClient }
}

function privateFailure(status: 403 | 404): ProblemDetailsError {
  return new ProblemDetailsError({
    type: "about:blank",
    title: status === 403 ? "Forbidden" : "Not Found",
    status,
    detail: "实名打印访问已撤销",
    code: status === 403 ? "permission_denied" : "resource_not_found",
    request_id: `survey-print-${status}`,
  })
}

function SafeEvaluationTarget() {
  const navigationType = useNavigationType()
  return <main><h1>评价中心安全页</h1><p>导航方式：{navigationType}</p></main>
}

afterEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

it("renders hidden print details, roles, intervals, and suggestions", async () => {
  vi.mocked(getSurveyPrint).mockResolvedValue(projection)

  renderPage()

  expect(await screen.findByRole("heading", { name: "营期评价打印" })).toBeVisible()
  expect(screen.getByText("服务角色：教练（teaching）")).toBeVisible()
  expect(screen.getByText("服务区间")).toBeVisible()
  expect(screen.getByText("原因：清楚")).toBeVisible()
  expect(screen.getByText("建议：继续")).toBeVisible()
  expect(screen.getByText("增加复盘")).toBeVisible()
  expect(screen.queryByText("教练甲")).not.toBeInTheDocument()
})

it("refetches a real-name projection and exposes a keyboard-print action", async () => {
  vi.mocked(getSurveyPrint)
    .mockResolvedValueOnce(projection)
    .mockResolvedValueOnce(realNameProjection)
  const printMock = vi.fn()
  vi.stubGlobal("print", printMock)
  const user = userEvent.setup()

  renderPage()

  await screen.findByRole("heading", { name: "营期评价打印" })
  await user.click(screen.getByRole("checkbox", { name: "打印实名" }))
  await waitFor(() =>
    expect(getSurveyPrint).toHaveBeenLastCalledWith(
      expect.objectContaining({ identity: "real_name" }),
      expect.anything(),
    ),
  )
  expect(await screen.findByRole("heading", { name: "教练 · 教练甲" })).toBeVisible()
  await user.click(screen.getByRole("button", { name: "打印" }))
  expect(printMock).toHaveBeenCalledOnce()
})

it("offers recovery when print projection fails", async () => {
  vi.mocked(getSurveyPrint)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(projection)

  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("无权打印评价")
  await userEvent.click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByText(/平均评分 8\.5/)).toBeVisible()
})

it("does not offer real-name printing with only one sensitive permission", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000030",
      full_name: "测试打印管理员",
      email: "survey-print@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.identity.read"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  vi.mocked(getSurveyPrint).mockResolvedValue(projection)
  renderPage()
  await screen.findByRole("heading", { name: "营期评价打印" })
  expect(screen.queryByRole("checkbox", { name: "打印实名" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "打印" })).not.toBeInTheDocument()
})

it("downgrades a real-name URL before requesting print data when print identity access is absent", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000030",
      full_name: "测试打印管理员",
      email: "survey-print@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.print"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  vi.mocked(getSurveyPrint).mockResolvedValue(projection)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(surveyKeys.print(accountId, { identity: "real_name" }), realNameProjection)
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")
  const removeQueries = vi.spyOn(queryClient, "removeQueries")
  renderPage([
    "/evaluations/surveys/print?identity=real_name",
  ], queryClient)

  expect(await screen.findByRole("heading", { name: "营期评价打印" })).toBeVisible()
  await waitFor(() => expect(getSurveyPrint).toHaveBeenCalledWith(
    expect.objectContaining({ identity: "hidden" }),
    expect.anything(),
  ))
  expect(getSurveyPrint).not.toHaveBeenCalledWith(
    expect.objectContaining({ identity: "real_name" }),
    expect.anything(),
  )
  expect(screen.queryByText("教练甲")).not.toBeInTheDocument()
  expect(queryClient.getQueryData(surveyKeys.print(accountId, { identity: "real_name" }))).toBeUndefined()
  expect(cancelQueries).toHaveBeenCalled()
  expect(removeQueries).toHaveBeenCalled()
})

it("shows the print action with print access even without identity access", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000030",
      full_name: "测试打印管理员",
      email: "survey-print@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.print"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  vi.mocked(getSurveyPrint).mockResolvedValue(projection)
  renderPage()
  await screen.findByRole("heading", { name: "营期评价打印" })
  expect(screen.getByRole("button", { name: "打印" })).toBeVisible()
  expect(screen.queryByRole("checkbox", { name: "打印实名" })).not.toBeInTheDocument()
})

it("clears cached real-name print data and replaces the route after a private 404", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000030",
      full_name: "测试打印管理员",
      email: "survey-print@example.test",
      force_password_change: false,
      permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
      scoped_permissions: [],
    },
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshUser: vi.fn(),
  })
  vi.mocked(getSurveyPrint).mockRejectedValueOnce(privateFailure(404))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const otherAccountId = "00000000-0000-4000-8000-000000000099"
  const sensitiveProjection = {
    ...realNameProjection,
    items: realNameProjection.items.map((item) => ({
      ...item,
      details: item.details.map((detail) => ({ ...detail, respondent_name: "学员甲" })),
    })),
  }
  queryClient.setQueryData(surveyKeys.print(accountId, { identity: "real_name" }), sensitiveProjection)
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { respondent_name: "其他账号保留" })
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")

  render(
    <MemoryRouter initialEntries={["/evaluations/surveys/print?identity=real_name"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys/print" element={<CampSurveyPrintPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
  expect(screen.getByText("导航方式：REPLACE")).toBeVisible()
  expect(screen.queryByText(/学员甲/)).not.toBeInTheDocument()
  expect(screen.queryByRole("checkbox", { name: "打印实名" })).not.toBeInTheDocument()
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: surveyKeys.root(accountId) })
  await waitFor(() => expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0))
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ respondent_name: "其他账号保留" })
})

it("does not reuse a fresh print projection after the protected page unmounts", async () => {
  vi.mocked(useAuth).mockReturnValue({
    status: "authenticated",
    user: {
      account_id: accountId,
      person_id: "20000000-0000-4000-8000-000000000030",
      full_name: "测试打印管理员",
      email: "survey-print@example.test",
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
  const otherAccountId = "00000000-0000-4000-8000-000000000099"
  queryClient.setQueryData(surveyKeys.print(accountId, { identity: "hidden" }), projection)
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { respondent_name: "其他账号保留" })
  vi.mocked(getSurveyPrint).mockResolvedValue(projection)
  const renderProtectedPrint = () => render(
    <MemoryRouter initialEntries={["/evaluations/surveys/print"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys/print" element={<CampSurveyPrintPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const firstView = renderProtectedPrint()
  expect(await screen.findByRole("heading", { name: "营期评价打印" })).toBeVisible()

  firstView.unmount()
  expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ respondent_name: "其他账号保留" })

  vi.mocked(getSurveyPrint).mockRejectedValueOnce(privateFailure(404))
  renderProtectedPrint()
  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
})
