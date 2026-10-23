import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { StrictMode } from "react"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
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
  student_person_id: "student-minor",
  student_name: "未成年在营学员",
  student_number: "S-MINOR",
  age_status: "minor",
  access_source: "family",
  current_enrollment: activeEnrollment,
}

const adultStudent: LearningStudent = {
  student_person_id: "student-adult",
  student_name: "成年后仍绑定学员",
  student_number: "S-ADULT",
  age_status: "adult",
  access_source: "family",
  current_enrollment: { ...activeEnrollment, enrollment_id: "adult-active" },
}

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

function renderLearningRoute(input: {
  path: string
  students: (call: number) => LearningStudent[] | Promise<LearningStudent[]>
  teaching?: (studentId: string, call: number) => Response | Promise<Response>
  strictMode?: boolean
}) {
  let studentsRequests = 0
  let teachingRequests = 0
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
    const match = url.match(/\/family\/students\/([^/]+)\/teaching\?/)
    if (match) {
      teachingRequests += 1
      const studentId = match[1]
      if (input.teaching) return input.teaching(studentId, teachingRequests)
      return jsonResponse(teaching(studentId === adultStudent.student_person_id ? adultStudent : minorStudent))
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

it("shows the disabled review boundary without making a review request", async () => {
  const { fetchMock } = renderLearningRoute({
    path: `/learning/${minorStudent.student_person_id}`,
    students: () => [minorStudent],
  })

  expect(await screen.findByText("学习评价")).toBeVisible()
  expect(screen.getByText("下一阶段开放")).toBeVisible()
  expect(screen.queryByRole("link", { name: /学习评价/ })).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([request]) => String(request).includes("review")),
  ).toBe(false)
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
