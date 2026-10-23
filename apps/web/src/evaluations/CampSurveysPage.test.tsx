import { focusManager, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useNavigationType } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import {
  getFoodPreferenceCatalog,
  getFoodPreferenceProfile,
} from "../foodPreferences/api"
import { CampSurveysPage } from "./CampSurveysPage"
import { getSurveyContext, submitSurvey, submitSurveySupplement } from "./surveyApi"
import type { SurveyContext } from "./surveyApi"
import { surveyKeys } from "./surveyKeys"

vi.mock("./surveyApi", () => ({
  getSurveyContext: vi.fn(),
  submitSurvey: vi.fn().mockResolvedValue({}),
  reviseSurvey: vi.fn().mockResolvedValue({}),
  submitSurveySupplement: vi.fn().mockResolvedValue({}),
}))
vi.mock("../foodPreferences/api", () => ({
  getFoodPreferenceCatalog: vi.fn().mockResolvedValue({
    items: [
      { id: "00000000-0000-4000-8000-000000000004", name: "川菜", sort_order: 1, is_active: true, is_other: false },
      { id: "00000000-0000-4000-8000-000000000005", name: "其他", sort_order: 99, is_active: true, is_other: true },
    ],
    allowed_actions: [],
    access_revision: "a".repeat(64),
  }),
  getFoodPreferenceProfile: vi.fn().mockResolvedValue({
    person_id: "00000000-0000-4000-8000-000000000002",
    version: 3,
    cuisines: [{ cuisine_option_id: "00000000-0000-4000-8000-000000000004", name: "川菜", custom_name: null }],
    dishes: ["鱼香肉丝"],
    updated_at: null,
    allowed_actions: ["update"],
    access_revision: "a".repeat(64),
  }),
}))
vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ user: { account_id: "00000000-0000-4000-8000-000000000010" } }) }))

const baseContext: SurveyContext = {
  cycle_id: "00000000-0000-4000-8000-000000000001",
  student_person_id: "00000000-0000-4000-8000-000000000002",
  student_name: "测试学员",
  opens_at: "2026-01-01T00:00:00Z",
  due_at: "2027-01-01T00:00:00Z",
  snapshot_version: 1,
  respondent_kind: "student",
  can_submit: true,
  can_revise: false,
  can_supplement: false,
  submission_id: null,
  latest_version: null,
  latest_submission: null,
  staff: [{
    id: "00000000-0000-4000-8000-000000000003",
    staff_name: "教练甲",
    role_key: "coach",
    role_name: "教练",
    role_category: "teaching",
    roles: [],
    service_intervals: [],
  }],
  supplement_staff: [],
}

afterEach(() => {
  vi.clearAllMocks()
  focusManager.setFocused(undefined)
})
beforeEach(() => {
  vi.mocked(getSurveyContext).mockResolvedValue([baseContext])
})

function setupContext(context: SurveyContext = baseContext) {
  vi.mocked(getSurveyContext).mockResolvedValue([context])
}

setupContext()

const accountId = "00000000-0000-4000-8000-000000000010"
const otherAccountId = "00000000-0000-4000-8000-000000000099"

function privateFailure(status: 403 | 404): ProblemDetailsError {
  return new ProblemDetailsError({
    type: "about:blank",
    title: status === 403 ? "Forbidden" : "Not Found",
    status,
    detail: "营期评价访问已撤销",
    code: status === 403 ? "permission_denied" : "resource_not_found",
    request_id: `camp-survey-${status}`,
  })
}

function SafeEvaluationTarget() {
  const navigationType = useNavigationType()
  return <main><h1>评价中心安全页</h1><p>导航方式：{navigationType}</p></main>
}

function renderProtectedPage(queryClient: QueryClient) {
  return render(
    <MemoryRouter initialEntries={["/evaluations/surveys"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route path="/evaluations/surveys" element={<CampSurveysPage />} />
          <Route path="/evaluations" element={<SafeEvaluationTarget />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

function seededQueryClient(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false, staleTime: 30_000 },
    },
  })
  queryClient.setQueryData([...surveyKeys.root(accountId), "private-seed"], { draft: "当前账号私密草稿" })
  queryClient.setQueryData([...surveyKeys.root(otherAccountId), "private-seed"], { draft: "其他账号保留" })
  return queryClient
}

it("renders an actionable camp survey and staff rating", async () => {
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByRole("heading", { name: "营期评价" })).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "填写问卷" }))
  expect(screen.getByLabelText("教练甲评分")).toBeVisible()
  expect(screen.getByRole("button", { name: "提交问卷" })).toBeVisible()
})

it("distinguishes multiple students and shows Shanghai survey dates", async () => {
  vi.mocked(getSurveyContext).mockResolvedValue([
    {
      ...baseContext,
      student_name: "学员甲",
      opens_at: "2026-08-01T16:30:00Z",
      due_at: "2026-08-31T16:30:00Z",
    },
    {
      ...baseContext,
      cycle_id: "00000000-0000-4000-8000-000000000011",
      student_person_id: "00000000-0000-4000-8000-000000000012",
      student_name: "学员乙",
      opens_at: "2026-08-02T01:15:00Z",
      due_at: "2026-09-01T01:15:00Z",
    },
  ] as SurveyContext[])

  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)

  expect(await screen.findByRole("heading", { name: "学员甲 · 截止 2026-09-01" })).toBeVisible()
  expect(screen.getByRole("heading", { name: "学员乙 · 截止 2026-09-01" })).toBeVisible()
  expect(screen.getAllByText("开放时间：2026-08-02")).toHaveLength(2)
})

it("loads later respondent context pages", async () => {
  const firstPage = Array.from({ length: 20 }, (_, index) => ({
    ...baseContext,
    cycle_id: `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`,
    student_name: `第一页学员${index + 1}`,
  }))
  vi.mocked(getSurveyContext).mockImplementation(async (page = 1) =>
    page === 1
      ? firstPage
      : [{
          ...baseContext,
          cycle_id: "00000000-0000-4000-8000-000000000099",
          student_name: "第二页学员",
        }],
  )
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)

  await screen.findByRole("heading", { name: /第一页学员1 · 截止/ })
  await user.click(screen.getByRole("button", { name: "下一页问卷" }))

  expect(await screen.findByRole("heading", { name: /第二页学员 · 截止/ })).toBeVisible()
  expect(getSurveyContext).toHaveBeenLastCalledWith(2, 20, expect.anything())

  await user.click(screen.getByRole("button", { name: "上一页问卷" }))
  expect(await screen.findByRole("heading", { name: /第一页学员1 · 截止/ })).toBeVisible()
})

it("prefills the target student's real food profile instead of inventing a cuisine id", async () => {
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await userEvent.click(await screen.findByRole("button", { name: "填写问卷" }))
  expect(
    await screen.findByRole("button", { name: "移除菜品 鱼香肉丝" }),
  ).toBeVisible()
  expect(
    screen.getByRole("button", { name: "移除菜系 川菜" }),
  ).toBeVisible()
  expect(screen.getByText("已读取学员餐饮偏好（版本 3）")).toBeVisible()
})

it("directly adds three custom cuisines in an other-only camp survey", async () => {
  const otherOnlyCatalog = {
    items: [
      {
        id: "00000000-0000-4000-8000-000000000005",
        name: "其他",
        sort_order: 99,
        is_active: true,
        is_other: true,
      },
    ],
    allowed_actions: [],
    access_revision: "a".repeat(64),
  }
  vi.mocked(getFoodPreferenceCatalog)
    .mockResolvedValueOnce(otherOnlyCatalog)
    .mockResolvedValueOnce(otherOnlyCatalog)
  vi.mocked(getFoodPreferenceProfile).mockResolvedValueOnce({
    person_id: baseContext.student_person_id,
    version: 0,
    cuisines: [],
    dishes: [],
    updated_at: null,
    allowed_actions: ["update"],
    access_revision: "a".repeat(64),
  })
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <CampSurveysPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  await user.click(await screen.findByRole("button", { name: "填写问卷" }))

  const cuisineInput = await screen.findByLabelText("菜系名称")
  expect(
    screen.queryByRole("button", { name: "添加菜系 其他" }),
  ).not.toBeInTheDocument()
  await user.type(cuisineInput, "川菜{Enter}")
  await user.type(cuisineInput, "粤菜{Enter}")
  await user.type(cuisineInput, "东北菜{Enter}")
  await user.type(screen.getByLabelText("菜品名称"), "宫保鸡丁{Enter}")

  expect(screen.getByText("菜系 3/3")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "提交问卷" }))
  await waitFor(() =>
    expect(submitSurvey).toHaveBeenCalledWith(
      baseContext.cycle_id,
      expect.objectContaining({
        favorite_cuisines: [
          expect.objectContaining({
            cuisine_option_id: "00000000-0000-4000-8000-000000000005",
            custom_name: "川菜",
          }),
          expect.objectContaining({
            cuisine_option_id: "00000000-0000-4000-8000-000000000005",
            custom_name: "粤菜",
          }),
          expect.objectContaining({
            cuisine_option_id: "00000000-0000-4000-8000-000000000005",
            custom_name: "东北菜",
          }),
        ],
        favorite_dishes: ["宫保鸡丁"],
      }),
    ),
  )
})

it("does not persist unadded cuisine text after closing a camp survey", async () => {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <CampSurveysPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  await user.click(await screen.findByRole("button", { name: "填写问卷" }))
  await user.type(await screen.findByLabelText("菜系名称"), "尚未添加的私密内容")

  await user.click(screen.getByRole("button", { name: "取消" }))
  await user.click(await screen.findByRole("button", { name: "填写问卷" }))

  expect(await screen.findByLabelText("菜系名称")).toHaveValue("")
})

it("keeps an inactive standard cuisine snapshot readable in the survey draft", async () => {
  vi.mocked(getFoodPreferenceProfile).mockResolvedValueOnce({
    person_id: baseContext.student_person_id,
    version: 4,
    cuisines: [
      {
        cuisine_option_id: "00000000-0000-4000-8000-000000000099",
        name: "停用菜系旧名",
        custom_name: null,
      },
    ],
    dishes: ["鱼香肉丝"],
    updated_at: null,
    allowed_actions: ["update"],
    access_revision: "a".repeat(64),
  })
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <CampSurveysPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "填写问卷" }))

  expect(
    await screen.findByRole("button", { name: "移除菜系 停用菜系旧名" }),
  ).toBeVisible()
  expect(screen.getByText("餐饮偏好已填写完整。")).toBeVisible()
})

it("rehydrates the respondent's latest version when editing", async () => {
  setupContext({
    ...baseContext,
    can_submit: false,
    can_revise: true,
    submission_id: "00000000-0000-4000-8000-000000000006",
    latest_version: 1,
    latest_submission: {
      version: 1,
      course_training_score: 10,
      course_training_reason: "已保存课程原因",
      life_service_score: 6,
      life_service_reason: "已保存生活原因",
      service_management_score: 7,
      service_management_reason: "已保存管理原因",
      overall_suggestion: "已保存建议",
      favorite_cuisines: [{
        cuisine_option_id: "00000000-0000-4000-8000-000000000004",
        name: "川菜",
        custom_name: null,
      }],
      favorite_dishes: ["鱼香肉丝"],
      staff_ratings: [{
        staff_snapshot_id: "00000000-0000-4000-8000-000000000003",
        score: 10,
        reason: "已保存评分原因",
        suggestion: "已保存评分建议",
      }],
    },
  })
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "修改问卷" }))
  expect(await screen.findByDisplayValue("已保存课程原因")).toBeVisible()
  expect(screen.getByRole("combobox", { name: "课程训练评分" })).toHaveValue("10")
  expect(screen.getByDisplayValue("已保存建议")).toBeVisible()
  expect(screen.getByRole("combobox", { name: "教练甲评分" })).toHaveValue("10")
})

it("keeps a success notice after closing the submitted editor", async () => {
  setupContext()
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "填写问卷" }))
  await user.click(await screen.findByRole("button", { name: "提交问卷" }))
  expect(await screen.findByRole("status")).toHaveTextContent("问卷已提交")
})

it("edits up to three cuisines and dishes and captures every rating reason", async () => {
  setupContext()
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "填写问卷" }))

  await user.type(await screen.findByLabelText("菜系名称"), "家常菜{Enter}")
  await user.type(screen.getByLabelText("菜品名称"), "宫保鸡丁{Enter}")
  await user.type(screen.getByLabelText("课程训练评分原因"), "课程原因")
  await user.type(screen.getByLabelText("生活服务评分原因"), "生活原因")
  await user.type(screen.getByLabelText("服务管理评分原因"), "管理原因")
  await user.type(screen.getByLabelText("总体建议"), "总体建议")
  await user.type(screen.getByLabelText("教练甲评分原因"), "教练原因")
  await user.type(screen.getByLabelText("教练甲评分建议"), "教练建议")

  expect(
    screen.getByRole("button", { name: "移除菜系 家常菜" }),
  ).toBeVisible()
  expect(
    screen.getByRole("button", { name: "移除菜品 宫保鸡丁" }),
  ).toBeVisible()
  await user.click(screen.getByRole("button", { name: "提交问卷" }))
  expect(vi.mocked(submitSurvey)).toHaveBeenCalledWith(
    baseContext.cycle_id,
    expect.objectContaining({
      favorite_cuisines: expect.arrayContaining([
        expect.objectContaining({ cuisine_option_id: "00000000-0000-4000-8000-000000000005" }),
      ]),
      favorite_dishes: expect.arrayContaining(["宫保鸡丁"]),
      course_training_reason: "课程原因",
      life_service_reason: "生活原因",
      service_management_reason: "管理原因",
      overall_suggestion: "总体建议",
      staff_ratings: [expect.objectContaining({ reason: "教练原因", suggestion: "教练建议" })],
    }),
  )
})

it("hides the Other shortcut and rejects a direct cuisine duplicate", async () => {
  setupContext()
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "填写问卷" }))
  const cuisineInput = await screen.findByLabelText("菜系名称")
  expect(
    screen.queryByRole("button", { name: "添加菜系 其他" }),
  ).not.toBeInTheDocument()

  await user.type(cuisineInput, " 川菜 {Enter}")

  expect(screen.getByText("不能添加重复菜系。")).toBeVisible()
  expect(screen.getAllByRole("button", { name: /移除菜系/ })).toHaveLength(1)
})

it("submits ratings for every pending supplement staff member", async () => {
  const context = {
    ...baseContext,
    snapshot_version: 2,
    can_submit: false,
    can_revise: false,
    can_supplement: true,
    submission_id: "00000000-0000-4000-8000-000000000006",
    latest_version: 1,
    staff: [
      ...baseContext.staff,
      {
        id: "00000000-0000-4000-8000-000000000007",
        staff_name: "生活老师乙",
        role_key: "life_service",
        role_name: "生活老师",
        role_category: "life_service",
        roles: [],
        service_intervals: [],
      },
      {
        id: "00000000-0000-4000-8000-000000000008",
        staff_name: "助教丙",
        role_key: "assistant_coach",
        role_name: "助教",
        role_category: "teaching",
        roles: [],
        service_intervals: [],
      },
    ],
    supplement_staff: [
      {
        id: "00000000-0000-4000-8000-000000000007",
        staff_name: "生活老师乙",
        role_key: "life_service",
        role_name: "生活老师",
        role_category: "life_service",
        roles: [],
        service_intervals: [],
      },
      {
        id: "00000000-0000-4000-8000-000000000008",
        staff_name: "助教丙",
        role_key: "assistant_coach",
        role_name: "助教",
        role_category: "teaching",
        roles: [],
        service_intervals: [],
      },
    ],
  }
  setupContext(context)
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "补充工作人员评价" }))
  expect(screen.queryByRole("checkbox")).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("生活老师乙评分"), "9")
  await user.type(screen.getByLabelText("生活老师乙评分原因"), "补充原因")
  await user.selectOptions(screen.getByLabelText("助教丙评分"), "7")
  await user.click(screen.getByRole("button", { name: "提交补充评价" }))
  expect(vi.mocked(submitSurveySupplement)).toHaveBeenCalledWith(
    context.cycle_id,
    context.submission_id,
    expect.objectContaining({
      expected_snapshot_version: 2,
      staff_ratings: [
        expect.objectContaining({ staff_snapshot_id: "00000000-0000-4000-8000-000000000007", score: 9, reason: "补充原因" }),
        expect.objectContaining({ staff_snapshot_id: "00000000-0000-4000-8000-000000000008", score: 7 }),
      ],
    }),
  )
})

it("reuses the supplement idempotency key after an interrupted response", async () => {
  setupContext({
    ...baseContext,
    snapshot_version: 2,
    can_submit: false,
    can_supplement: true,
    submission_id: "00000000-0000-4000-8000-000000000006",
    latest_version: 1,
    supplement_staff: [{
      id: "00000000-0000-4000-8000-000000000007",
      staff_name: "生活老师乙",
      role_key: "life_service",
      role_name: "生活老师",
      role_category: "life_service",
      roles: [],
      service_intervals: [],
    }],
  })
  vi.mocked(submitSurveySupplement)
    .mockRejectedValueOnce(new Error("response lost"))
    .mockResolvedValueOnce({ supplement_id: "00000000-0000-4000-8000-000000000009", idempotent: true })
  const user = userEvent.setup()
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  await user.click(await screen.findByRole("button", { name: "补充工作人员评价" }))
  await user.click(screen.getByRole("button", { name: "提交补充评价" }))
  expect(await screen.findByRole("status")).toHaveTextContent("补充评价提交失败")
  await user.click(screen.getByRole("button", { name: "提交补充评价" }))
  await waitFor(() => expect(submitSurveySupplement).toHaveBeenCalledTimes(2))

  const firstPayload = vi.mocked(submitSurveySupplement).mock.calls[0]?.[2]
  const secondPayload = vi.mocked(submitSurveySupplement).mock.calls[1]?.[2]
  expect(firstPayload?.idempotency_key).toBeTruthy()
  expect(secondPayload?.idempotency_key).toBe(firstPayload?.idempotency_key)
})

it("lets a temporarily unavailable context recover with retry", async () => {
  vi.mocked(getSurveyContext)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce([baseContext])
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><CampSurveysPage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByRole("alert")).toHaveTextContent("问卷暂时不可用")
  await userEvent.click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByRole("heading", { name: "营期评价" })).toBeVisible()
})

it("replaces a revoked context route and purges only the current survey account", async () => {
  vi.mocked(getSurveyContext).mockRejectedValueOnce(privateFailure(404))
  const queryClient = seededQueryClient()
  const cancelQueries = vi.spyOn(queryClient, "cancelQueries")

  renderProtectedPage(queryClient)

  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
  expect(screen.getByText("导航方式：REPLACE")).toBeVisible()
  expect(cancelQueries).toHaveBeenCalledWith({ queryKey: surveyKeys.root(accountId) })
  expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ draft: "其他账号保留" })
})

it("closes the editor and removes its food profile when a protected read is revoked", async () => {
  const queryClient = seededQueryClient()
  const user = userEvent.setup()
  renderProtectedPage(queryClient)

  await user.click(await screen.findByRole("button", { name: "填写问卷" }))
  await user.type(await screen.findByLabelText("总体建议"), "撤权前的私密实名草稿")
  vi.mocked(getFoodPreferenceProfile).mockRejectedValueOnce(privateFailure(403))

  focusManager.setFocused(false)
  focusManager.setFocused(true)

  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
  expect(screen.queryByDisplayValue("撤权前的私密实名草稿")).not.toBeInTheDocument()
  expect(queryClient.getQueryCache().getAll().filter((query) =>
    query.queryKey.includes(accountId) &&
    (query.queryKey.includes("food") || query.queryKey[0] === "camp-survey-food")
  )).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ draft: "其他账号保留" })
})

it("revokes the same editor boundary when a first submission is forbidden", async () => {
  vi.mocked(submitSurvey).mockRejectedValueOnce(privateFailure(403))
  const queryClient = seededQueryClient()
  const user = userEvent.setup()
  renderProtectedPage(queryClient)

  await user.click(await screen.findByRole("button", { name: "填写问卷" }))
  await user.type(await screen.findByLabelText("总体建议"), "不可回显的提交草稿")
  await user.click(screen.getByRole("button", { name: "提交问卷" }))

  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
  expect(screen.queryByDisplayValue("不可回显的提交草稿")).not.toBeInTheDocument()
  expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ draft: "其他账号保留" })
})

it("does not reuse fresh survey data after the protected page unmounts", async () => {
  const queryClient = seededQueryClient()
  queryClient.setQueryData(surveyKeys.context(accountId), [baseContext])

  const firstView = renderProtectedPage(queryClient)
  expect(await screen.findByRole("heading", { name: "营期评价" })).toBeVisible()
  expect(getSurveyContext).not.toHaveBeenCalled()

  firstView.unmount()
  expect(queryClient.getQueryCache().findAll({ queryKey: surveyKeys.root(accountId) })).toHaveLength(0)
  expect(queryClient.getQueryData([...surveyKeys.root(otherAccountId), "private-seed"])).toEqual({ draft: "其他账号保留" })

  vi.mocked(getSurveyContext).mockRejectedValueOnce(privateFailure(404))
  renderProtectedPage(queryClient)
  expect(await screen.findByRole("heading", { name: "评价中心安全页" })).toBeVisible()
})
