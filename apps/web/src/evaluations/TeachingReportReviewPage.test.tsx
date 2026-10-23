import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import { EvaluationAuthorizationProvider } from "./EvaluationAuthorization"
import { reportKeys } from "./reportKeys"
import { TeachingReportReviewPage } from "./TeachingReportReviewPage"

const accountId = "10000000-0000-4000-8000-000000000001"
const reportId = "20000000-0000-4000-8000-000000000001"
const enrollmentId = "30000000-0000-4000-8000-000000000001"
const cohortId = "40000000-0000-4000-8000-000000000001"
const projectId = "50000000-0000-4000-8000-000000000001"
const classId = "60000000-0000-4000-8000-000000000001"
const versionId = "70000000-0000-4000-8000-000000000001"
const previousVersionId = "70000000-0000-4000-8000-000000000002"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function queueItem() {
  return {
    report_id: reportId,
    enrollment_id: enrollmentId,
    cohort_id: cohortId,
    cohort_name: "2026 夏季营",
    project_id: projectId,
    project_name: "英雄联盟",
    class_id: classId,
    class_name: "一班",
    report_kind: "daily",
    period_sequence: 9,
    period_start_date: "2026-08-09",
    period_end_date: "2026-08-09",
    status: "pending_review",
    lock_version: 2,
    author_name: "主教练甲",
    role_name: "主教练",
    version_id: versionId,
    version: 2,
    source: "revision",
    body: "需要审核的私密日报正文",
    dimension_values: {},
    submitted_at: "2026-08-09T12:30:00Z",
  }
}

function versionPage() {
  return {
    items: [
      {
        report_id: reportId,
        version_id: versionId,
        version: 2,
        source: "revision",
        body: "当前修订版本正文",
        dimension_values: {},
        submitted_at: "2026-08-09T12:30:00Z",
        is_current: true,
        is_published: false,
        review: null,
      },
      {
        report_id: reportId,
        version_id: previousVersionId,
        version: 1,
        source: "initial",
        body: "上一已发布版本正文",
        dimension_values: {},
        submitted_at: "2026-08-08T12:30:00Z",
        is_current: false,
        is_published: true,
        review: {
          decision: "approved",
          reason: null,
          reviewed_at: "2026-08-08T13:30:00Z",
        },
      },
    ],
    total: 2,
    page: 1,
    page_size: 20,
  }
}

function versionResponse(url: string): Response | null {
  return url.includes(`/evaluations/reports/${reportId}/versions?`)
    ? jsonResponse(versionPage())
    : null
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPage(
  fetchMock: ReturnType<typeof vi.fn>,
  idempotencyKeyFactory?: () => string,
) {
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("review-csrf")
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <EvaluationAuthorizationProvider pending={false} revision={0}>
        <TeachingReportReviewPage
          {...(idempotencyKeyFactory ? { idempotencyKeyFactory } : {})}
        />
      </EvaluationAuthorizationProvider>
    </QueryClientProvider>,
  )
  return queryClient
}

function renderPageWithAuthorization(fetchMock: ReturnType<typeof vi.fn>) {
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("review-csrf")
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const tree = (pending: boolean, revision: number) => (
    <QueryClientProvider client={queryClient}>
      <EvaluationAuthorizationProvider pending={pending} revision={revision}>
        <TeachingReportReviewPage />
      </EvaluationAuthorizationProvider>
    </QueryClientProvider>
  )
  const view = render(tree(false, 1))
  return {
    queryClient,
    setAuthorization: (pending: boolean, revision: number) =>
      view.rerender(tree(pending, revision)),
  }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

it("renders the independent review queue and approves through the shared dialog", async () => {
  const item = queueItem()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse({
        report_id: reportId,
        status: "published",
        lock_version: 3,
        published_version_id: versionId,
        review_id: "80000000-0000-4000-8000-000000000001",
        decision: "approved",
        idempotent: false,
      })
    }
    throw new Error(`Unexpected request: ${url} ${init?.method ?? "GET"}`)
  })
  const queryClient = renderPage(fetchMock)
  const user = userEvent.setup()

  expect(await screen.findByRole("heading", { name: "教学评语审核" })).toBeVisible()
  expect(await screen.findByText(item.body)).toBeVisible()
  expect(screen.getAllByText("主教练甲 · 主教练")).toHaveLength(2)

  const opener = screen.getByRole("button", { name: "审核主教练甲的日报" })
  await user.click(opener)
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(await screen.findByText("审核已提交。")).toBeVisible()
  const reviewCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith(`/evaluations/reports/${reportId}/reviews`),
  )
  expect(reviewCall).toBeDefined()
  const headers = new Headers(reviewCall?.[1]?.headers)
  expect(headers.get("X-CSRF-Token")).toBe("review-csrf")
  expect(headers.get("Idempotency-Key")).toBeTruthy()
  expect(JSON.parse(String(reviewCall?.[1]?.body))).toEqual({
    expected_lock_version: 2,
    decision: "approved",
    reason: null,
  })
  expect(
    JSON.stringify(
      queryClient.getMutationCache().getAll().map((mutation) =>
        mutation.state.variables,
      ),
    ),
  ).not.toContain(item.body)
})

it("invalidates the active filtered review queue after a successful review", async () => {
  const item = queueItem()
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse({
        report_id: reportId,
        status: "published",
        lock_version: 3,
        published_version_id: versionId,
        review_id: "80000000-0000-4000-8000-000000000001",
        decision: "approved",
        idempotent: false,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  const queryClient = renderPage(fetchMock)
  const invalidate = vi.spyOn(queryClient, "invalidateQueries")
  const user = userEvent.setup()

  await screen.findByRole("button", { name: "审核主教练甲的日报" })
  await user.selectOptions(screen.getByLabelText("营期"), cohortId)
  await waitFor(() => {
    const queueCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/evaluations/reports/review-queue?"),
    )
    expect(queueCalls).toHaveLength(2)
  })

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => {
    const queueCalls = fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/evaluations/reports/review-queue?"),
    )
    expect(queueCalls).toHaveLength(3)
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...reportKeys.root(accountId), "review-queue"],
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: [...reportKeys.root(accountId), "versions", { reportId }],
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: reportKeys.detail(accountId, reportId),
  })
})

it("requires a rejection reason and clears it from inspectable mutation state", async () => {
  const item = queueItem()
  const reason = "需要补充当日复盘结论"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse({
        report_id: reportId,
        status: "rejected",
        lock_version: 3,
        published_version_id: null,
        review_id: "80000000-0000-4000-8000-000000000001",
        decision: "rejected",
        idempotent: false,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  const queryClient = renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "驳回" }))
  expect(within(dialog).getByRole("button", { name: "确认审核" })).toBeDisabled()
  await user.type(within(dialog).getByLabelText("驳回原因"), reason)
  expect(within(dialog).getByRole("button", { name: "确认审核" })).toBeEnabled()
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(
    JSON.stringify(
      queryClient.getMutationCache().getAll().map((mutation) =>
        mutation.state.variables,
      ),
    ),
  ).not.toContain(reason)
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

it("freezes the original review payload while retrying an unknown result", async () => {
  const item = queueItem()
  const reason = "网络未知时必须保持的原驳回原因"
  let reviewCalls = 0
  let queueCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      queueCalls += 1
      return jsonResponse({
        items: [queueCalls === 1 ? item : { ...item, lock_version: 9 }],
        total: 1,
        page: 1,
        page_size: 20,
      })
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      reviewCalls += 1
      if (reviewCalls === 1) throw new TypeError("network result unknown")
      return jsonResponse({
        report_id: reportId,
        status: "rejected",
        lock_version: 3,
        published_version_id: null,
        review_id: "80000000-0000-4000-8000-000000000009",
        decision: "rejected",
        idempotent: true,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  const keyFactory = vi.fn(() => "stable-review-retry-key")
  const queryClient = renderPage(fetchMock, keyFactory)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  const rejected = within(dialog).getByRole("radio", { name: "驳回" })
  const approved = within(dialog).getByRole("radio", { name: "通过" })
  await user.click(rejected)
  const textarea = within(dialog).getByLabelText("驳回原因")
  await user.type(textarea, reason)
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "审核暂时无法提交",
  )
  expect(approved).toBeDisabled()
  expect(rejected).toBeDisabled()
  expect(textarea).toHaveAttribute("readonly")
  await queryClient.invalidateQueries({
    queryKey: [...reportKeys.root(accountId), "review-queue"],
  })
  await waitFor(() => expect(queueCalls).toBe(2))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))
  await waitFor(() => expect(reviewCalls).toBe(2))

  const reviewRequests = fetchMock.mock.calls.filter(([input]) =>
    String(input).endsWith(`/evaluations/reports/${reportId}/reviews`),
  )
  expect(reviewRequests.map(([, init]) => String(init?.body))).toEqual([
    JSON.stringify({
      expected_lock_version: 2,
      decision: "rejected",
      reason,
    }),
    JSON.stringify({
      expected_lock_version: 2,
      decision: "rejected",
      reason,
    }),
  ])
  expect(
    reviewRequests.map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    ),
  ).toEqual(["stable-review-retry-key", "stable-review-retry-key"])
  expect(keyFactory).toHaveBeenCalledTimes(1)
})

it("shows a typed self-review conflict and keeps the dialog recoverable", async () => {
  const item = queueItem()
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "reviewer cannot review own report",
          code: "evaluation_review_conflict",
          request_id: "review-conflict",
        },
        409,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "不能审核自己提交的评语。",
  )
  await user.keyboard("{Escape}")
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
})

it("keeps the rejection form mounted while a concurrent review refreshes", async () => {
  const item = queueItem()
  const reason = "保留这段尚未重新确认的驳回原因"
  let queueCalls = 0
  let resolveQueueRefresh!: (response: Response) => void
  const queueRefresh = new Promise<Response>((resolve) => {
    resolveQueueRefresh = resolve
  })
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      queueCalls += 1
      if (queueCalls === 1) {
        return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
      }
      return queueRefresh
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "report lock version changed",
          code: "evaluation_invalid_transition",
          request_id: "review-state-conflict",
        },
        409,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "驳回" }))
  await user.type(within(dialog).getByLabelText("驳回原因"), reason)
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => expect(queueCalls).toBe(2))
  expect(screen.getByRole("dialog", { name: "审核教学评语" })).toBeVisible()
  expect(screen.getByLabelText("驳回原因")).toHaveValue(reason)

  resolveQueueRefresh(
    jsonResponse({
      items: [],
      total: 0,
      page: 1,
      page_size: 20,
    }),
  )
  expect(await screen.findByRole("dialog", { name: "审核教学评语" })).toBeVisible()
  expect(within(screen.getByRole("dialog")).getByRole("alert")).toHaveTextContent(
    "评语状态已经变化，请刷新队列后重试。",
  )
  expect(screen.getByLabelText("驳回原因")).toHaveValue(reason)
  expect(within(screen.getByRole("dialog")).getByRole("radio", {
    name: "通过",
  })).toBeDisabled()
  expect(within(screen.getByRole("dialog")).getByRole("radio", {
    name: "驳回",
  })).toBeDisabled()
  expect(screen.getByLabelText("驳回原因")).toHaveAttribute("readonly")
  expect(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "确认审核",
    }),
  ).toBeDisabled()
})

it("retries a typed review conflict with the refreshed queue lock", async () => {
  const item = queueItem()
  const refreshedItem = { ...item, lock_version: 3 }
  const reason = "读取新锁版本后重新确认的驳回原因"
  let queueCalls = 0
  let resolveQueueRefresh!: (response: Response) => void
  const queueRefresh = new Promise<Response>((resolve) => {
    resolveQueueRefresh = resolve
  })
  const reviewBodies: Array<Record<string, unknown>> = []
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      queueCalls += 1
      const current = queueCalls === 1 ? item : refreshedItem
      const response = jsonResponse({
        items: [current],
        total: 1,
        page: 1,
        page_size: 20,
      })
      return queueCalls === 2 ? queueRefresh : response
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      reviewBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>)
      if (reviewBodies.length === 1) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Conflict",
            status: 409,
            detail: "report lock version changed",
            code: "evaluation_invalid_transition",
            request_id: "review-refreshed-lock-conflict",
          },
          409,
        )
      }
      return jsonResponse({
        report_id: reportId,
        status: "published",
        lock_version: 4,
        published_version_id: versionId,
        review_id: "80000000-0000-4000-8000-000000000009",
        decision: "rejected",
        idempotent: false,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "驳回" }))
  await user.type(within(dialog).getByLabelText("驳回原因"), reason)
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => expect(queueCalls).toBe(2))
  expect(screen.getByLabelText("驳回原因")).toHaveValue(reason)
  expect(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "确认审核",
    }),
  ).toBeDisabled()
  resolveQueueRefresh(
    jsonResponse({
      items: [refreshedItem],
      total: 1,
      page: 1,
      page_size: 20,
    }),
  )
  await waitFor(() =>
    expect(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "确认审核",
      }),
    ).toBeEnabled(),
  )
  expect(screen.getByLabelText("驳回原因")).toHaveValue(reason)
  await user.click(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "确认审核",
    }),
  )
  await waitFor(() => expect(reviewBodies).toHaveLength(2))

  expect(reviewBodies.map((request) => request.expected_lock_version)).toEqual([
    2,
    3,
  ])
  expect(reviewBodies.map((request) => request.reason)).toEqual([reason, reason])
})

it("keeps the rejection form when the conflict refresh cannot reload the queue", async () => {
  const item = queueItem()
  const reason = "队列刷新失败时仍需保留的驳回原因"
  let queueCalls = 0
  const privateDetail = "private reviewer database outage"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      queueCalls += 1
      if (queueCalls === 1) {
        return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
      }
      return jsonResponse(
        {
          type: "about:blank",
          title: "Server Error",
          status: 500,
          detail: privateDetail,
          code: "internal_error",
        },
        500,
      )
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "report lock version changed",
          code: "evaluation_invalid_transition",
          request_id: "review-refresh-failed-conflict",
        },
        409,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "驳回" }))
  await user.type(within(dialog).getByLabelText("驳回原因"), reason)
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => expect(queueCalls).toBe(2))
  expect(await screen.findByRole("dialog", { name: "审核教学评语" })).toBeVisible()
  expect(screen.getByLabelText("驳回原因")).toHaveValue(reason)
  expect(
    within(screen.getByRole("dialog")).getByRole("button", {
      name: "确认审核",
    }),
  ).toBeDisabled()
  expect(document.body).not.toHaveTextContent(privateDetail)
})

it("does not disclose a private upstream detail when review authorization changes", async () => {
  const item = queueItem()
  const privateDetail = "report exists but reviewer assignment was removed"
  let queueCalls = 0
  let resolveQueueRefresh!: (response: Response) => void
  const queueRefresh = new Promise<Response>((resolve) => {
    resolveQueueRefresh = resolve
  })
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const versions = versionResponse(url)
    if (versions) return versions
    if (url.includes("/evaluations/reports/review-queue?")) {
      queueCalls += 1
      if (queueCalls === 1) {
        return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
      }
      return queueRefresh
    }
    if (url.endsWith(`/evaluations/reports/${reportId}/reviews`)) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
          request_id: "review-private-resource",
        },
        404,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  const dialog = screen.getByRole("dialog", { name: "审核教学评语" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "确认审核" }))

  await waitFor(() => expect(queueCalls).toBe(2))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByText(item.body)).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "审核主教练甲的日报" })).not.toBeInTheDocument()
  await waitFor(() => expect(screen.getByRole("main")).toHaveFocus())
  expect(document.body).not.toHaveTextContent(privateDetail)

  resolveQueueRefresh(
    jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }),
  )
  expect(await screen.findByRole("heading", { name: "暂无待审核评语" })).toBeVisible()
})

it("removes a review item immediately when its version timeline becomes private", async () => {
  const item = queueItem()
  const privateDetail = "private version relationship was removed"
  let queueCalls = 0
  let resolveQueueRefresh!: (response: Response) => void
  const queueRefresh = new Promise<Response>((resolve) => {
    resolveQueueRefresh = resolve
  })
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/review-queue?")) {
      queueCalls += 1
      if (queueCalls === 1) {
        return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
      }
      return queueRefresh
    }
    if (url.includes(`/evaluations/reports/${reportId}/versions?`)) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
          request_id: "review-version-private-resource",
        },
        404,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)

  expect(await screen.findByText(item.body)).toBeVisible()
  await waitFor(() => expect(queueCalls).toBe(2))
  expect(screen.queryByText(item.body)).not.toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "版本记录" })).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent(privateDetail)

  resolveQueueRefresh(
    jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }),
  )
  expect(await screen.findByRole("heading", { name: "暂无待审核评语" })).toBeVisible()
})

it("loads immutable versions with cancellation and compares the current review version", async () => {
  const item = queueItem()
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (
      url.includes("/evaluations/reports/review-queue?") ||
      url.includes(`/evaluations/reports/${reportId}/versions?`)
    ) {
      expect(init?.signal).toBeInstanceOf(AbortSignal)
    }
    if (url.includes("/evaluations/reports/review-queue?")) {
      return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
    }
    const versions = versionResponse(url)
    if (versions) return versions
    throw new Error(`Unexpected request: ${url}`)
  })
  renderPage(fetchMock)

  expect(
    await screen.findByRole("region", { name: "版本 2 与版本 1 对比" }),
  ).toBeVisible()
  expect(screen.getByText("当前修订版本正文")).toBeVisible()
  expect(screen.getByText("上一已发布版本正文")).toBeVisible()
})

it("removes the portaled rejection dialog and disables private reads while authorization is pending", async () => {
  const item = queueItem()
  const reason = "授权刷新期间不能留在门户里的驳回原因"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("/evaluations/reports/review-queue?")) {
      return jsonResponse({ items: [item], total: 1, page: 1, page_size: 20 })
    }
    const versions = versionResponse(url)
    if (versions) return versions
    throw new Error(`Unexpected request: ${url}`)
  })
  const view = renderPageWithAuthorization(fetchMock)
  const user = userEvent.setup()

  await user.click(
    await screen.findByRole("button", { name: "审核主教练甲的日报" }),
  )
  await user.click(
    within(screen.getByRole("dialog")).getByRole("radio", { name: "驳回" }),
  )
  await user.type(screen.getByLabelText("驳回原因"), reason)

  view.setAuthorization(true, 2)
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent(reason)
  const queueCallsBeforeRefresh = fetchMock.mock.calls.filter(([input]) =>
    String(input).includes("/evaluations/reports/review-queue?"),
  ).length
  await view.queryClient.invalidateQueries({
    queryKey: [...reportKeys.root(accountId), "review-queue"],
  })
  expect(
    fetchMock.mock.calls.filter(([input]) =>
      String(input).includes("/evaluations/reports/review-queue?"),
    ),
  ).toHaveLength(queueCallsBeforeRefresh)

  view.setAuthorization(false, 2)
  expect(await screen.findByText(item.body)).toBeVisible()
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(document.body).not.toHaveTextContent(reason)
})
