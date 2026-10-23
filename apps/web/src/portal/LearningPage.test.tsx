import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { foodPreferenceKeys } from "../foodPreferences/keys"
import type { LearningEnrollment, LearningStudent } from "./api"
import { portalKeys } from "./api"
import { LearningPage } from "./LearningPage"

const activeEnrollment: LearningEnrollment = {
  enrollment_id: "enrollment-active",
  cohort_id: "cohort-active",
  cohort_name: "英雄联盟暑期营",
  starts_at: "2030-08-01T09:00:00+08:00",
  ends_at: "2030-08-10T18:00:00+08:00",
  project_name: "英雄联盟",
  class_id: "class-active",
  class_name: "英雄联盟菁英训练班",
  class_type: "elite",
  professional_team_name: "星河职业队",
  status: "active",
  confirmed_at: "2026-07-01T08:00:00+08:00",
  cancelled_at: null,
  completed_at: null,
}

const completedEnrollment: LearningEnrollment = {
  ...activeEnrollment,
  enrollment_id: "enrollment-completed",
  cohort_id: "cohort-completed",
  cohort_name: "英雄联盟春季营",
  class_id: "class-completed",
  class_name: "英雄联盟普通班",
  class_type: "standard",
  professional_team_name: null,
  status: "completed",
  starts_at: "2029-03-01T09:00:00+08:00",
  ends_at: "2029-03-10T18:00:00+08:00",
  completed_at: "2029-03-10T18:00:00+08:00",
}

const minorStudent: LearningStudent = {
  student_person_id: "10000000-0000-4000-8000-000000000011",
  student_name: "未成年在营学员",
  student_number: "S-MINOR",
  age_status: "minor",
  access_source: "family",
  current_enrollment: activeEnrollment,
}

const adultStudent: LearningStudent = {
  student_person_id: "10000000-0000-4000-8000-000000000012",
  student_name: "成年后仍绑定学员",
  student_number: "S-ADULT",
  age_status: "adult",
  access_source: "family",
  current_enrollment: { ...activeEnrollment, enrollment_id: "adult-active" },
}

const foodRevision = "a".repeat(64)
const cuisineId = "20000000-0000-4000-8000-000000000011"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function problem(status: number, code: string, detail: string): Response {
  return jsonResponse(
    {
      type: "about:blank",
      title: "Request failed",
      status,
      detail,
      code,
      request_id: `learning-${code}`,
    },
    status,
  )
}

function teaching(student: LearningStudent) {
  return {
    student,
    items: [student.current_enrollment ?? activeEnrollment, completedEnrollment],
    page: 1,
    page_size: 20,
    total: 2,
  }
}

function foodCatalog() {
  return {
    items: [
      {
        id: cuisineId,
        name: "川菜",
        sort_order: 1,
        is_active: true,
        is_other: false,
      },
    ],
    allowed_actions: [],
    access_revision: foodRevision,
  }
}

function foodProfile(studentId: string) {
  const student =
    studentId === adultStudent.student_person_id ? adultStudent : minorStudent
  return {
    person_id: studentId,
    version: 2,
    cuisines: [
      {
        cuisine_option_id: cuisineId,
        name: "川菜",
        custom_name: null,
      },
    ],
    dishes: [`${student.student_name}的菜`],
    updated_at: "2026-08-12T10:00:00+08:00",
    allowed_actions: ["update"],
    access_revision: foodRevision,
  }
}

function renderLearningRoute(input: {
  path: string
  students: (call: number) => LearningStudent[] | Promise<LearningStudent[]>
  teaching?: (studentId: string, call: number) => Response | Promise<Response>
  published?: (studentId: string, call: number) => Response | Promise<Response>
  foodProfile?: (studentId: string, call: number) => Response | Promise<Response>
  strictMode?: boolean
}) {
  let studentsRequests = 0
  let teachingRequests = 0
  let publishedRequests = 0
  let foodProfileRequests = 0
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return jsonResponse({
        account_id: "account-guardian",
        person_id: "person-guardian",
        full_name: "学员监护人",
        email: "guardian@example.test",
        force_password_change: false,
        permissions: [],
      })
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "learning-page-csrf" })
    }
    if (url.endsWith("/family/students")) {
      studentsRequests += 1
      const items = await input.students(studentsRequests)
      return jsonResponse({ items, total: items.length })
    }
    if (url.includes("/food-preferences/cuisines?")) {
      return jsonResponse(foodCatalog())
    }
    const foodProfileMatch = url.match(/\/people\/([^/]+)\/food-preferences$/)
    if (foodProfileMatch) {
      foodProfileRequests += 1
      const studentId = foodProfileMatch[1]
      if (input.foodProfile) {
        return input.foodProfile(studentId, foodProfileRequests)
      }
      return jsonResponse(foodProfile(studentId))
    }
    const match = url.match(/\/family\/students\/([^/]+)\/teaching\?/)
    if (match) {
      teachingRequests += 1
      const studentId = match[1]
      if (input.teaching) return input.teaching(studentId, teachingRequests)
      return jsonResponse(teaching(studentId === adultStudent.student_person_id ? adultStudent : minorStudent))
    }
    const publishedMatch = url.match(
      /\/evaluations\/students\/([^/]+)\/published-reports\?/,
    )
    if (publishedMatch) {
      publishedRequests += 1
      const studentId = publishedMatch[1]
      if (input.published) return input.published(studentId, publishedRequests)
      return jsonResponse({ items: [], total: 0, page: 1, page_size: 10 })
    }
    if (url.includes("/evaluations/system/tasks?")) {
      return jsonResponse({
        api_version: "v2",
        items: [],
        total: 0,
        page: 1,
        page_size: 10,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(
    [
      { path: "/learning", element: <LearningPage /> },
      { path: "/learning/:studentId", element: <LearningPage /> },
      { path: "/", element: <p>工作台</p> },
    ],
    { initialEntries: [input.path] },
  )
  const content = (
    <AuthProvider>
      <RouterProvider router={router} />
    </AuthProvider>
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      {input.strictMode ? <StrictMode>{content}</StrictMode> : content}
    </QueryClientProvider>,
  )
  return {
    router,
    fetchMock,
    queryClient,
    get studentsRequests() {
      return studentsRequests
    },
    get foodProfileRequests() {
      return foodProfileRequests
    },
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it("redirects the learning landing route to the first authorized student", async () => {
  const { router } = renderLearningRoute({
    path: "/learning",
    students: () => [minorStudent, adultStudent],
  })

  expect(
    await screen.findByRole("heading", { name: minorStudent.student_name }),
  ).toBeVisible()
  expect(router.state.location.pathname).toBe(
    `/learning/${minorStudent.student_person_id}`,
  )
})

it("switches learners and keeps the student id in the query key and URL", async () => {
  const user = userEvent.setup()
  const { router } = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: () => [minorStudent, adultStudent],
  })

  const selector = await screen.findByLabelText("当前学员")
  await user.selectOptions(selector, adultStudent.student_person_id)

  expect(router.state.location.pathname).toBe(
    `/learning/${adultStudent.student_person_id}`,
  )
  expect(
    await screen.findByRole("heading", { name: adultStudent.student_name }),
  ).toBeVisible()
})

it("shows the current learner's food profile and switches the exact target", async () => {
  const user = userEvent.setup()
  const scenario = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: () => [minorStudent, adultStudent],
  })

  expect(
    await screen.findByText(`${minorStudent.student_name}的菜`),
  ).toBeVisible()
  await user.selectOptions(
    screen.getByLabelText("当前学员"),
    adultStudent.student_person_id,
  )

  expect(
    await screen.findByText(`${adultStudent.student_name}的菜`),
  ).toBeVisible()
  expect(
    scenario.fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith(
        `/people/${minorStudent.student_person_id}/food-preferences`,
      ),
    ),
  ).toBe(true)
  expect(
    scenario.fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith(
        `/people/${adultStudent.student_person_id}/food-preferences`,
      ),
    ),
  ).toBe(true)
})

it("loads only published system evaluations for the selected learner", async () => {
  const { fetchMock } = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: () => [minorStudent],
  })

  expect(await screen.findByRole("region", { name: "系统评价历史" })).toBeVisible()
  expect(screen.getByText("该学员暂无已发布的系统评价")).toBeVisible()
  expect(screen.queryByText("下一阶段开放")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).includes(
        `/evaluations/system/tasks?student_id=${minorStudent.student_person_id}&published_only=true`,
      ),
    ),
  ).toBe(true)
})

it("loads only published teaching reports for the selected learner", async () => {
  const { fetchMock } = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: () => [minorStudent],
    published: (studentId) =>
      jsonResponse({
        items: [
          {
            report_id: "10000000-0000-4000-8000-000000000001",
            enrollment_id: "20000000-0000-4000-8000-000000000001",
            cohort_id: "30000000-0000-4000-8000-000000000001",
            cohort_name: "英雄联盟暑期营",
            project_id: "40000000-0000-4000-8000-000000000001",
            project_name: "英雄联盟",
            class_id: "50000000-0000-4000-8000-000000000001",
            class_name: "英雄联盟菁英训练班",
            report_kind: "weekly",
            period_sequence: 1,
            period_start_date: "2026-08-01",
            period_end_date: "2026-08-07",
            author_name: "主教练甲",
            role_name: "主教练",
            version_id: "60000000-0000-4000-8000-000000000001",
            version: 1,
            body: `只属于${studentId}的已发布周报`,
            dimension_values: {},
            published_at: "2026-08-08T10:00:00Z",
          },
        ],
        total: 1,
        page: 1,
        page_size: 10,
      }),
  })

  expect(await screen.findByRole("heading", { name: "教学评语" })).toBeVisible()
  expect(
    await screen.findByText(
      `只属于${minorStudent.student_person_id}的已发布周报`,
    ),
  ).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).includes(
        `/evaluations/students/${minorStudent.student_person_id}/published-reports?`,
      ),
    ),
  ).toBe(true)
})

it("refetches candidates before falling back after relationship revocation", async () => {
  const scenario = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    strictMode: true,
    students: async (call) => {
      if (call === 1) return [minorStudent, adultStudent]
      await new Promise((resolve) => setTimeout(resolve, 0))
      return [adultStudent]
    },
    teaching: (studentId) =>
      studentId === minorStudent.student_person_id
        ? problem(404, "family_student_not_found", "学员教学信息不存在")
        : jsonResponse(teaching(adultStudent)),
  })

  expect(
    await screen.findByRole("heading", { name: adultStudent.student_name }),
  ).toBeVisible()
  expect(scenario.router.state.location.pathname).toBe(
    `/learning/${adultStudent.student_person_id}`,
  )
  expect(screen.queryByText(minorStudent.student_name)).not.toBeInTheDocument()
  expect(scenario.studentsRequests).toBe(2)
})

it("closes the food editor and selects the next safe learner after relationship revocation", async () => {
  const user = userEvent.setup()
  const scenario = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: (call) =>
      call === 1 ? [minorStudent, adultStudent] : [adultStudent],
    foodProfile: (studentId, call) =>
      studentId === minorStudent.student_person_id && call === 2
        ? problem(404, "food_preference_not_found", "关系已解除")
        : jsonResponse(foodProfile(studentId)),
  })
  const oldFoodKey = foodPreferenceKeys.person(
    "account-guardian",
    foodRevision,
    minorStudent.student_person_id,
  )
  const otherAccountKey = foodPreferenceKeys.person(
    "other-account",
    foodRevision,
    minorStudent.student_person_id,
  )
  scenario.queryClient.setQueryData(otherAccountKey, { private: "other" })

  await user.click(
    await screen.findByRole("button", { name: "编辑餐饮偏好" }),
  )
  expect(screen.getByRole("button", { name: "取消编辑" })).toBeVisible()

  await act(async () => {
    await scenario.queryClient.refetchQueries({
      queryKey: oldFoodKey,
      exact: true,
    })
  })

  expect(
    await screen.findByRole("heading", { name: adultStudent.student_name }),
  ).toBeVisible()
  expect(scenario.router.state.location.pathname).toBe(
    `/learning/${adultStudent.student_person_id}`,
  )
  expect(screen.queryByText(`${minorStudent.student_name}的菜`)).not.toBeInTheDocument()
  expect(scenario.queryClient.getQueryData(oldFoodKey)).toBeUndefined()
  expect(scenario.queryClient.getQueryData(otherAccountKey)).toEqual({
    private: "other",
  })
})

it("keeps learning visible when only the current learner's food access is revoked", async () => {
  const scenario = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: (call) =>
      call === 1 ? [minorStudent, adultStudent] : [adultStudent, minorStudent],
    teaching: (_studentId, call) =>
      call === 1
        ? jsonResponse(teaching(minorStudent))
        : problem(500, "internal_error", "教学信息不应重新请求"),
    foodProfile: (_studentId, call) =>
      call === 2
        ? problem(403, "permission_denied", "餐饮偏好权限已撤销")
        : jsonResponse(foodProfile(minorStudent.student_person_id)),
  })
  const currentFoodKey = foodPreferenceKeys.person(
    "account-guardian",
    foodRevision,
    minorStudent.student_person_id,
  )
  const currentTeachingKey = portalKeys.teaching(
    "account-guardian",
    minorStudent.student_person_id,
    1,
  )

  expect(
    await screen.findByText(`${minorStudent.student_name}的菜`),
  ).toBeVisible()
  expect(screen.getByText(activeEnrollment.class_name)).toBeVisible()
  expect(scenario.queryClient.getQueryData(currentTeachingKey)).toEqual(
    teaching(minorStudent),
  )

  await act(async () => {
    await scenario.queryClient.refetchQueries({
      queryKey: currentFoodKey,
      exact: true,
    })
  })
  await waitFor(() => expect(scenario.studentsRequests).toBe(2))

  expect(scenario.router.state.location.pathname).toBe(
    `/learning/${minorStudent.student_person_id}`,
  )
  expect(
    screen.getByRole("heading", { name: minorStudent.student_name }),
  ).toBeVisible()
  expect(screen.getByText(activeEnrollment.class_name)).toBeVisible()
  expect(scenario.queryClient.getQueryData(currentTeachingKey)).toEqual(
    teaching(minorStudent),
  )
  await waitFor(() =>
    expect(
      screen.queryByRole("region", { name: "餐饮偏好" }),
    ).not.toBeInTheDocument(),
  )
})

it("falls back to the empty family route after the last learner revokes food access", async () => {
  const scenario = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: (call) => (call === 1 ? [minorStudent] : []),
    foodProfile: (studentId, call) =>
      call === 2
        ? problem(404, "food_preference_not_found", "关系已解除")
        : jsonResponse(foodProfile(studentId)),
  })
  const oldFoodKey = foodPreferenceKeys.person(
    "account-guardian",
    foodRevision,
    minorStudent.student_person_id,
  )
  const oldTeachingKey = portalKeys.teaching(
    "account-guardian",
    minorStudent.student_person_id,
    1,
  )
  const removeQueries = vi.spyOn(scenario.queryClient, "removeQueries")

  expect(
    await screen.findByText(`${minorStudent.student_name}的菜`),
  ).toBeVisible()
  await act(async () => {
    await scenario.queryClient.refetchQueries({
      queryKey: oldFoodKey,
      exact: true,
    })
  })

  expect(await screen.findByLabelText("暂无可查看学员")).toBeVisible()
  expect(scenario.router.state.location.pathname).toBe("/learning")
  expect(scenario.queryClient.getQueryData(oldFoodKey)).toBeUndefined()
  expect(scenario.queryClient.getQueryData(oldTeachingKey)).toBeUndefined()
  expect(removeQueries).toHaveBeenCalledWith({
    queryKey: oldTeachingKey,
    exact: true,
  })
})

it("removes old learning data and offers retry after a server failure", async () => {
  const { queryClient } = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: () => [minorStudent],
    teaching: (_studentId, call) =>
      call === 1
        ? jsonResponse(teaching(minorStudent))
        : problem(500, "internal_error", "学习信息暂时不可用"),
  })
  expect(
    await screen.findByRole("heading", { name: minorStudent.student_name }),
  ).toBeVisible()

  await act(async () => {
    await queryClient.refetchQueries({
      queryKey: portalKeys.teaching(
        "account-guardian",
        minorStudent.student_person_id,
        1,
      ),
      exact: true,
    })
  })

  expect(await screen.findByRole("alert")).toHaveTextContent("学习信息暂时不可用")
  expect(screen.queryByText(minorStudent.student_name)).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
})

it.each([
  [403, "permission_denied"],
  [404, "housing_resource_not_found"],
])(
  "clears the complete account portal branch and returns home after private %i",
  async (status, code) => {
    const { queryClient, router } = renderLearningRoute({
      path: `/learning/${minorStudent.student_person_id}`,
      students: () => [minorStudent],
      teaching: (_studentId, call) =>
        call === 1
          ? jsonResponse(teaching(minorStudent))
          : problem(status, code, "学习权限已撤销"),
    })
    expect(
      await screen.findByRole("heading", { name: minorStudent.student_name }),
    ).toBeVisible()
    queryClient.setQueryData(portalKeys.housing("account-guardian"), {
      subjects: [{ subject_name: "旧住宿学员" }],
    })
    queryClient.setQueryData(portalKeys.payments("account-guardian", 1), {
      items: [{ amount_cents: 88_000 }],
    })

    await act(async () => {
      await queryClient.refetchQueries({
        queryKey: portalKeys.teaching(
          "account-guardian",
          minorStudent.student_person_id,
          1,
        ),
        exact: true,
      })
    })

    expect(await screen.findByText("工作台")).toBeVisible()
    expect(router.state.location.pathname).toBe("/")
    expect(screen.queryByText(minorStudent.student_name)).not.toBeInTheDocument()
    expect(
      queryClient.getQueryData(portalKeys.housing("account-guardian")),
    ).toBeUndefined()
    expect(
      queryClient.getQueryData(portalKeys.payments("account-guardian", 1)),
    ).toBeUndefined()
  },
)
