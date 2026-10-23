import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import { TeachingReportsPage } from "./TeachingReportsPage"
import { reportKeys } from "./reportKeys"

const accountId = "10000000-0000-4000-8000-000000000097"
const enrollmentId = "20000000-0000-4000-8000-000000000001"
const studentId = "30000000-0000-4000-8000-000000000001"
const firstCohortId = "40000000-0000-4000-8000-000000000001"
const secondCohortId = "40000000-0000-4000-8000-000000000002"
const firstProjectId = "50000000-0000-4000-8000-000000000001"
const secondProjectId = "50000000-0000-4000-8000-000000000002"
const firstClassId = "60000000-0000-4000-8000-000000000001"
const secondClassId = "60000000-0000-4000-8000-000000000002"
const draftReportId = "70000000-0000-4000-8000-000000000001"
const publishedReportId = "70000000-0000-4000-8000-000000000002"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function targetPage(items: unknown[] = [targetItem()]) {
  return {
    items,
    facets: {
      cohorts: [
        {
          id: firstCohortId,
          name: "2026 夏季营",
          starts_at: "2026-08-01T00:00:00Z",
          ends_at: "2026-08-21T15:59:59Z",
        },
        {
          id: secondCohortId,
          name: "2026 秋季营",
          starts_at: "2026-09-01T00:00:00Z",
          ends_at: "2026-09-21T15:59:59Z",
        },
      ],
      projects: [
        { id: firstProjectId, name: "英雄联盟", cohort_ids: [firstCohortId] },
        { id: secondProjectId, name: "和平精英", cohort_ids: [secondCohortId] },
      ],
      classes: [
        {
          id: firstClassId,
          name: "英雄联盟一班",
          cohort_id: firstCohortId,
          project_id: firstProjectId,
        },
        {
          id: secondClassId,
          name: "和平精英一班",
          cohort_id: secondCohortId,
          project_id: secondProjectId,
        },
      ],
    },
    total: items.length,
    page: 1,
    page_size: 20,
  }
}

function targetItem() {
  return {
    enrollment_id: enrollmentId,
    student_person_id: studentId,
    student_name: "授权学员",
    student_number: "STU-2026-001",
    cohort_id: firstCohortId,
    cohort_name: "2026 夏季营",
    cohort_starts_at: "2026-08-01T00:00:00Z",
    cohort_ends_at: "2026-08-21T15:59:59Z",
    project_id: firstProjectId,
    project_name: "英雄联盟",
    class_id: firstClassId,
    class_name: "英雄联盟一班",
  }
}

function slot(
  kind: "daily" | "weekly",
  sequence: number,
  overrides: Record<string, unknown> = {},
) {
  return {
    enrollment_id: enrollmentId,
    student_person_id: studentId,
    cohort_id: firstCohortId,
    cohort_name: "2026 夏季营",
    project_id: firstProjectId,
    project_name: "英雄联盟",
    class_id: firstClassId,
    class_name: "英雄联盟一班",
    report_kind: kind,
    period_sequence: sequence,
    period_start_date: "2026-08-09",
    period_end_date: kind === "daily" ? "2026-08-09" : "2026-08-15",
    submission_opens_at: "2026-08-08T16:00:00Z",
    initial_submission_deadline: "2026-08-24T15:59:59.999999Z",
    report_id: null,
    status: null,
    lock_version: null,
    draft_body: null,
    draft_dimension_values: null,
    draft_revision: null,
    allowed_actions: ["save_draft"],
    ...overrides,
  }
}

function slotList() {
  return {
    items: [
      slot("daily", 9, {
        report_id: draftReportId,
        status: "draft",
        lock_version: 1,
        draft_body: "作者已保存的日报",
        draft_dimension_values: {},
        draft_revision: 1,
        allowed_actions: ["save_draft", "submit"],
      }),
      slot("weekly", 2, {
        report_id: publishedReportId,
        status: "published",
        lock_version: 3,
        allowed_actions: ["start_revision"],
      }),
      slot("daily", 10, {
        period_start_date: "2026-08-10",
        period_end_date: "2026-08-10",
        report_id: "70000000-0000-4000-8000-000000000003",
        status: "pending_review",
        lock_version: 2,
        allowed_actions: [],
      }),
    ],
  }
}

function versionPage() {
  return {
    items: [
      {
        report_id: publishedReportId,
        version_id: "80000000-0000-4000-8000-000000000001",
        version: 1,
        source: "initial",
        body: "已发布周报版本正文",
        dimension_values: {},
        submitted_at: "2026-08-16T02:00:00Z",
        is_current: true,
        is_published: true,
        review: {
          decision: "approved",
          reason: null,
          reviewed_at: "2026-08-16T04:00:00Z",
        },
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPage(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/evaluations/reports"]}>
        <Routes>
          <Route path="/evaluations/reports" element={<TeachingReportsPage />} />
          <Route path="/evaluations" element={<h1>评价中心</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
  return { queryClient, view }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

it("uses authorized facets, a trimmed search, and only the selected enrollment", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      return jsonResponse(targetPage())
    }
    if (url.includes("/evaluations/reports/slots?")) {
      return jsonResponse(slotList())
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  expect(await screen.findByText("授权学员")).toBeVisible()
  expect(screen.getByText("STU-2026-001")).toBeVisible()
  await user.selectOptions(screen.getByLabelText("营期"), firstCohortId)
  expect(
    within(screen.getByLabelText("项目")).queryByRole("option", {
      name: "和平精英",
    }),
  ).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("项目"), firstProjectId)
  expect(
    within(screen.getByLabelText("班级")).getByRole("option", {
      name: "英雄联盟一班",
    }),
  ).toBeVisible()

  await user.type(screen.getByLabelText("搜索学员"), " 学员_% ")
  await user.click(screen.getByRole("button", { name: "搜索" }))
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(([input]) =>
        String(input).includes(`query=${encodeURIComponent("学员_%")}`),
      ),
    ).toBe(true)
  })

  await user.click(screen.getByRole("button", { name: "选择授权学员" }))
  expect(await screen.findByRole("region", { name: "评语任务" })).toBeVisible()
  expect(
    screen.getByText("当前可提交 · 截止 2026-08-24"),
  ).toBeVisible()
  const slotCall = fetchMock.mock.calls.find(([input]) =>
    String(input).includes("/evaluations/reports/slots?"),
  )
  expect(String(slotCall?.[0])).toBe(
    `/api/v1/evaluations/reports/slots?enrollment_id=${enrollmentId}`,
  )
  expect(slotCall?.[1]?.signal).toBeInstanceOf(AbortSignal)
})

it("filters slot cards locally without inventing report actions", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) return jsonResponse(targetPage())
    if (url.includes("/evaluations/reports/slots?")) return jsonResponse(slotList())
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))

  expect(await screen.findByRole("article", { name: "日报 第 9 期" })).toBeVisible()
  expect(screen.getByRole("article", { name: "日报 第 10 期" })).toBeVisible()
  expect(
    within(screen.getByRole("article", { name: "日报 第 10 期" })).queryByRole(
      "button",
      { name: /编辑|整改|修订/ },
    ),
  ).not.toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText("评语类型"), "weekly")
  expect(screen.queryByRole("article", { name: "日报 第 9 期" })).not.toBeInTheDocument()
  expect(screen.getByRole("article", { name: "周报 第 2 期" })).toBeVisible()
  await user.selectOptions(screen.getByLabelText("任务状态"), "published")
  expect(screen.getByRole("article", { name: "周报 第 2 期" })).toBeVisible()
})

it("opens versions and invalidates every private workspace projection after save", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) return jsonResponse(targetPage())
    if (url.includes("/evaluations/reports/slots?")) return jsonResponse(slotList())
    if (url.includes(`/${publishedReportId}/versions?`)) {
      return jsonResponse(versionPage())
    }
    if (url.endsWith(`/${draftReportId}/draft`) && init?.method === "PUT") {
      return jsonResponse({
        report_id: draftReportId,
        status: "draft",
        lock_version: 2,
        draft_revision: 2,
        body: "保存后的日报正文",
        dimension_values: {},
        idempotent: false,
        allowed_actions: ["save_draft", "submit"],
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  const { queryClient } = renderPage(fetchMock)
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const user = userEvent.setup()
  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))

  await user.click(
    await screen.findByRole("button", { name: "查看周报第 2 期版本" }),
  )
  expect(await screen.findByText("已发布周报版本正文")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) =>
      String(input).includes(`/${publishedReportId}/versions?`),
    ),
  ).toBe(true)

  await user.click(screen.getByRole("button", { name: "编辑日报第 9 期" }))
  const dialog = screen.getByRole("dialog", { name: "编辑教学评语" })
  await user.type(within(dialog).getByLabelText("评语正文"), "补充")
  await user.click(within(dialog).getByRole("button", { name: "保存草稿" }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())

  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...reportKeys.root(accountId), "targets"],
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: reportKeys.slots(accountId, enrollmentId),
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: reportKeys.detail(accountId, draftReportId),
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...reportKeys.root(accountId), "list"],
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...reportKeys.root(accountId), "versions"],
  })
})

it("shows empty and recoverable directory states without upstream details", async () => {
  const privateDetail = "private author assignment failure"
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse(
        {
          type: "about:blank",
          title: "Server Error",
          status: 500,
          detail: privateDetail,
          code: "internal_error",
        },
        500,
      ),
    )
    .mockResolvedValueOnce(jsonResponse(targetPage([])))
  renderPage(fetchMock)
  const user = userEvent.setup()

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("可填写学员名单暂时无法读取")
  expect(alert).not.toHaveTextContent(privateDetail)
  await user.click(screen.getByRole("button", { name: "重试学员名单" }))
  expect(await screen.findByText("暂无可填写学员")).toBeVisible()
})

it("purges the private workspace and returns to evaluations after access is revoked", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      return jsonResponse(targetPage())
    }
    if (url.includes("/evaluations/reports/slots?")) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "教学评语权限已撤销",
          code: "evaluation_operation_denied",
        },
        403,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  const { queryClient } = renderPage(fetchMock)
  const remove = vi.spyOn(queryClient, "removeQueries")
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))

  expect(await screen.findByRole("heading", { name: "评价中心" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "教学评语工作台" })).not.toBeInTheDocument()
  expect(remove).toHaveBeenCalledWith({ queryKey: reportKeys.root(accountId) })
})

it("keeps the author form mounted when a conflict removes the authoritative slot", async () => {
  const body = "权威任务消失后仍要保留的本地正文"
  let slotCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      return jsonResponse(targetPage())
    }
    if (url.includes("/evaluations/reports/slots?")) {
      slotCalls += 1
      const initial = slotList()
      return slotCalls === 1
        ? jsonResponse(initial)
        : jsonResponse({
            items: initial.items.filter(
              (item) => item.report_id !== draftReportId,
            ),
          })
    }
    if (url.endsWith(`/${draftReportId}/draft`) && init?.method === "PUT") {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "report slot was consumed concurrently",
          code: "evaluation_report_conflict",
          request_id: "author-slot-removed-conflict",
        },
        409,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))
  await user.click(
    await screen.findByRole("button", { name: "编辑日报第 9 期" }),
  )
  const dialog = screen.getByRole("dialog", { name: "编辑教学评语" })
  const textarea = within(dialog).getByLabelText("评语正文")
  await user.clear(textarea)
  await user.type(textarea, body)
  await user.click(within(dialog).getByRole("button", { name: "保存草稿" }))

  await waitFor(() => expect(slotCalls).toBeGreaterThanOrEqual(2))
  expect(screen.getByRole("dialog", { name: "编辑教学评语" })).toBeVisible()
  expect(screen.getByLabelText("评语正文")).toHaveValue(body)
  expect(screen.getByLabelText("评语正文")).toHaveAttribute("readonly")
  expect(
    within(screen.getByRole("dialog")).queryByRole("button", {
      name: "保存草稿",
    }),
  ).not.toBeInTheDocument()
  expect(screen.getByRole("alert")).toHaveTextContent("刷新并比较后重试")
})

it("retries a typed author conflict with refreshed slot revisions", async () => {
  const body = "刷新权威版本后重新确认的正文"
  let slotCalls = 0
  let resolveSlotRefresh!: (response: Response) => void
  const slotRefresh = new Promise<Response>((resolve) => {
    resolveSlotRefresh = resolve
  })
  const draftBodies: Array<Record<string, unknown>> = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      return jsonResponse(targetPage())
    }
    if (url.includes("/evaluations/reports/slots?")) {
      slotCalls += 1
      const page = slotList()
      if (slotCalls === 1) return jsonResponse(page)
      const refreshed = jsonResponse({
        items: page.items.map((item) =>
          item.report_id === draftReportId
            ? {
                ...item,
                lock_version: 2,
                draft_revision: 2,
                draft_body: "服务端并发更新后的正文",
              }
            : item,
        ),
      })
      return slotCalls === 2 ? slotRefresh : refreshed
    }
    if (url.endsWith(`/${draftReportId}/draft`) && init?.method === "PUT") {
      draftBodies.push(JSON.parse(String(init.body)) as Record<string, unknown>)
      if (draftBodies.length === 1) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Conflict",
            status: 409,
            detail: "draft revision changed concurrently",
            code: "evaluation_report_conflict",
            request_id: "author-slot-refreshed-conflict",
          },
          409,
        )
      }
      return jsonResponse({
        report_id: draftReportId,
        status: "draft",
        lock_version: 3,
        draft_revision: 3,
        body,
        dimension_values: {},
        idempotent: false,
        allowed_actions: ["save_draft", "submit"],
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))
  await user.click(
    await screen.findByRole("button", { name: "编辑日报第 9 期" }),
  )
  const dialog = screen.getByRole("dialog", { name: "编辑教学评语" })
  const textarea = within(dialog).getByLabelText("评语正文")
  await user.clear(textarea)
  await user.type(textarea, body)
  await user.click(within(dialog).getByRole("button", { name: "保存草稿" }))

  await waitFor(() => expect(slotCalls).toBe(2))
  expect(screen.getByLabelText("评语正文")).toHaveValue(body)
  expect(screen.getByLabelText("评语正文")).toHaveAttribute("readonly")
  expect(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "保存草稿",
    }),
  ).toBeDisabled()
  resolveSlotRefresh(
    jsonResponse({
      items: slotList().items.map((item) =>
        item.report_id === draftReportId
          ? {
              ...item,
              lock_version: 2,
              draft_revision: 2,
              draft_body: "服务端并发更新后的正文",
            }
          : item,
      ),
    }),
  )
  await waitFor(() =>
    expect(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "保存草稿",
      }),
    ).toBeEnabled(),
  )
  expect(screen.getByLabelText("评语正文")).toHaveValue(body)
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "保存草稿",
    }),
  )
  await waitFor(() => expect(draftBodies).toHaveLength(2))

  expect(draftBodies.map((request) => request.expected_draft_revision)).toEqual([
    1,
    2,
  ])
  expect(draftBodies.map((request) => request.body)).toEqual([body, body])
})

it("recovers a private slot 404 by clearing only the selected target", async () => {
  const privateDetail = "private missing enrollment and assignment scope"
  let targetCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      targetCalls += 1
      return jsonResponse(targetPage())
    }
    if (url.includes("/evaluations/reports/slots?")) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
        },
        404,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))

  expect(
    await screen.findByText("该学员已不可访问，名单已刷新。"),
  ).toBeVisible()
  expect(screen.getByRole("heading", { name: "教学评语工作台" })).toBeVisible()
  expect(screen.queryByRole("region", { name: "评语任务" })).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent(privateDetail)
  await waitFor(() => expect(targetCalls).toBeGreaterThan(1))
})

it("recovers a private version 404 by closing the target workspace and refreshing targets", async () => {
  const privateDetail = "private version target no longer belongs to this author"
  let targetCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      targetCalls += 1
      return jsonResponse(targetPage())
    }
    if (url.includes("/evaluations/reports/slots?")) return jsonResponse(slotList())
    if (url.includes(`/${publishedReportId}/versions?`)) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
        },
        404,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))
  await user.click(
    await screen.findByRole("button", { name: "查看周报第 2 期版本" }),
  )

  expect(
    await screen.findByText("该学员已不可访问，名单已刷新。"),
  ).toBeVisible()
  expect(screen.queryByRole("region", { name: "评语任务" })).not.toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "版本记录" })).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent(privateDetail)
  await waitFor(() => expect(targetCalls).toBeGreaterThan(1))
})

it("recovers a private editor mutation 404 by closing the dialog and refreshing targets", async () => {
  const privateDetail = "private draft target was revoked before commit"
  let targetCalls = 0
  let resolveTargetRefresh!: (response: Response) => void
  const targetRefresh = new Promise<Response>((resolve) => {
    resolveTargetRefresh = resolve
  })
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/targets?")) {
      targetCalls += 1
      return targetCalls === 1 ? jsonResponse(targetPage()) : targetRefresh
    }
    if (url.includes("/evaluations/reports/slots?")) return jsonResponse(slotList())
    if (url.endsWith(`/${draftReportId}/draft`) && init?.method === "PUT") {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
        },
        404,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "选择授权学员" }))
  await user.click(await screen.findByRole("button", { name: "编辑日报第 9 期" }))
  const dialog = screen.getByRole("dialog", { name: "编辑教学评语" })
  await user.type(within(dialog).getByLabelText("评语正文"), "补充")
  await user.click(within(dialog).getByRole("button", { name: "保存草稿" }))

  await waitFor(() => expect(targetCalls).toBe(2))
  expect(
    await screen.findByText("该学员已不可访问，名单已刷新。"),
  ).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "编辑教学评语" })).not.toBeInTheDocument()
  expect(screen.queryByRole("region", { name: "评语任务" })).not.toBeInTheDocument()
  expect(screen.queryByText("授权学员")).not.toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus())
  expect(document.body).not.toHaveTextContent(privateDetail)

  resolveTargetRefresh(jsonResponse(targetPage([])))
  expect(await screen.findByText("暂无可填写学员")).toBeVisible()
})
