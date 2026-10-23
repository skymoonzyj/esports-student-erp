import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { portalKeys } from "./api"
import { CoachNextCohortsPage } from "./CoachNextCohortsPage"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function coachResponse(input: { later?: boolean; empty?: boolean } = {}) {
  if (input.empty) {
    return {
      observed_at: "2026-07-30T08:00:00+08:00",
      selected_starts_at: null,
      options: [],
      classes: [],
      items: [],
      page: 1,
      page_size: 20,
      total: 0,
    }
  }
  const later = input.later === true
  const cohortId = later ? "cohort-later" : "cohort-nearest"
  const classId = later ? "class-later" : "class-nearest"
  const startsAt = later
    ? "2030-08-15T09:00:00+08:00"
    : "2030-08-01T09:00:00+08:00"
  const studentName = later ? "稍后营期学员" : "最近一班学员"
  return {
    observed_at: "2026-07-30T08:00:00+08:00",
    selected_starts_at: startsAt,
    options: [
      {
        anchor_cohort_id: "cohort-nearest",
        starts_at: "2030-08-01T09:00:00+08:00",
        cohort_names: ["最近营期"],
        class_count: 1,
      },
      {
        anchor_cohort_id: "cohort-later",
        starts_at: "2030-08-15T09:00:00+08:00",
        cohort_names: ["稍后营期"],
        class_count: 1,
      },
    ],
    classes: [
      {
        cohort_id: cohortId,
        cohort_name: later ? "稍后营期" : "最近营期",
        starts_at: startsAt,
        ends_at: "2030-08-25T09:00:00+08:00",
        class_id: classId,
        class_name: later ? "稍后普通班" : "最近普通班",
        project_name: "英雄联盟",
        class_type: "standard",
        professional_team_name: null,
        assignment_type: "primary",
        student_count: 1,
      },
    ],
    items: [
      {
        enrollment_id: later ? "enrollment-later" : "enrollment-nearest",
        student_person_id: later ? "student-later" : "student-nearest",
        student_name: studentName,
        student_number: later ? "S-LATER" : "S-NEAREST",
        age_status: later ? "adult" : "minor",
        cohort_id: cohortId,
        cohort_name: later ? "稍后营期" : "最近营期",
        project_name: "英雄联盟",
        class_id: classId,
        class_name: later ? "稍后普通班" : "最近普通班",
        class_type: "standard",
        professional_team_name: null,
        assignment_type: "primary",
        enrollment_status: "active",
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  }
}

function problem(status: number, code: string, detail: string): Response {
  return jsonResponse(
    {
      type: "about:blank",
      title: "Request failed",
      status,
      detail,
      code,
      request_id: `coach-${code}`,
    },
    status,
  )
}

function renderCoachPage(input: {
  initialSearch?: string
  roster: (url: string, call: number) => Response | Promise<Response>
}) {
  let rosterCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return jsonResponse({
        account_id: "account-coach",
        person_id: "person-coach",
        full_name: "名单教练",
        email: "coach@example.test",
        force_password_change: false,
        permissions: [],
      })
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "coach-page-csrf" })
    }
    if (url.includes("/coach/next-cohorts?")) {
      rosterCalls += 1
      return input.roster(url, rosterCalls)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(
    [
      { path: "/coach/next-cohorts", element: <CoachNextCohortsPage /> },
      { path: "/", element: <p>工作台</p> },
    ],
    {
      initialEntries: [
        `/coach/next-cohorts${input.initialSearch ?? ""}`,
      ],
    },
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { router, fetchMock, queryClient }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it("shows the nearest start group and switches to a later group", async () => {
  const user = userEvent.setup()
  renderCoachPage({
    roster: (url) => jsonResponse(coachResponse({ later: url.includes("cohort-later") })),
  })

  expect(await screen.findByRole("heading", { name: "下一期学员" })).toBeVisible()
  expect(await screen.findByText("最近一班学员")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "2030-08-15" }))
  expect(await screen.findByText("稍后营期学员")).toBeVisible()
  await waitFor(() => {
    expect(screen.queryByText("最近一班学员")).not.toBeInTheDocument()
  })
})

it("does not render sensitive or financial fields", async () => {
  renderCoachPage({ roster: () => jsonResponse(coachResponse()) })

  await screen.findByText("最近一班学员")
  expect(document.body.textContent).not.toMatch(/身份证|手机号|邮箱|付款|学费|¥/)
})

it("shows an honest empty state when no future assignment exists", async () => {
  renderCoachPage({ roster: () => jsonResponse(coachResponse({ empty: true })) })

  expect(await screen.findByText("暂未安排下一期")).toBeVisible()
})

it("hides the old roster while refetching and shows a retryable server error", async () => {
  const { queryClient } = renderCoachPage({
    roster: (_url, call) =>
      call === 1
        ? jsonResponse(coachResponse())
        : problem(500, "internal_error", "暂时无法读取下一期名单"),
  })
  expect(await screen.findByText("最近一班学员")).toBeVisible()

  await act(async () => {
    await queryClient.refetchQueries({
      queryKey: portalKeys.coach("account-coach", undefined, 1),
      exact: true,
    })
  })

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "暂时无法读取下一期名单",
  )
  expect(screen.queryByText("最近一班学员")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
})

it("falls back to the nearest group when the selected anchor is revoked", async () => {
  const { router } = renderCoachPage({
    initialSearch: "?anchor_cohort_id=cohort-later",
    roster: (url) =>
      url.includes("anchor_cohort_id=cohort-later")
        ? problem(404, "coach_cohort_not_found", "下一期营期不存在")
        : jsonResponse(coachResponse()),
  })

  expect(await screen.findByText("最近一班学员")).toBeVisible()
  expect(router.state.location.search).not.toContain("cohort-later")
  expect(screen.getByRole("heading", { name: "下一期学员" })).toHaveFocus()
})
