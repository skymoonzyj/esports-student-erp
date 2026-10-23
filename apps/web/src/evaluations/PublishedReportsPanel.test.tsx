import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PublishedReportsPanel } from "./PublishedReportsPanel"
import { reportKeys } from "./reportKeys"

const accountId = "10000000-0000-4000-8000-000000000001"
const firstStudentId = "20000000-0000-4000-8000-000000000001"
const secondStudentId = "30000000-0000-4000-8000-000000000001"
const enrollmentId = "40000000-0000-4000-8000-000000000001"
const refreshUser = vi.fn(async () => undefined)

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId }, refreshUser }),
}))

function publishedItem(reportId: string, overrides: Record<string, unknown> = {}) {
  return {
    report_id: reportId,
    enrollment_id: enrollmentId,
    cohort_id: "50000000-0000-4000-8000-000000000001",
    cohort_name: "2026 夏季营",
    project_id: "60000000-0000-4000-8000-000000000001",
    project_name: "英雄联盟",
    class_id: "70000000-0000-4000-8000-000000000001",
    class_name: "一班",
    report_kind: "daily",
    period_sequence: 2,
    period_start_date: "2026-08-02",
    period_end_date: "2026-08-02",
    author_name: "主教练甲",
    role_name: "主教练",
    version_id: "80000000-0000-4000-8000-000000000001",
    version: 1,
    body: "仅已发布的家庭评语正文",
    dimension_values: {},
    published_at: "2026-08-03T10:00:00Z",
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPanel(
  queryClient: QueryClient,
  studentId = firstStudentId,
) {
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PublishedReportsPanel studentId={studentId} />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

afterEach(() => {
  refreshUser.mockClear()
  vi.unstubAllGlobals()
})

it("renders only published family cards, forwards cancellation, and never reads versions", async () => {
  const forbidden = "private-draft-and-rejection-reason"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    expect(init?.signal).toBeInstanceOf(AbortSignal)
    expect(url).not.toContain("/versions")
    if (url.includes("page=1&page_size=10")) {
      return jsonResponse({
        items: [
          publishedItem("90000000-0000-4000-8000-000000000001", {
            draft_body: forbidden,
            rejection_reason: forbidden,
          }),
        ],
        total: 11,
        page: 1,
        page_size: 10,
      })
    }
    if (url.includes("page=2&page_size=10")) {
      return jsonResponse({
        items: [
          publishedItem("90000000-0000-4000-8000-000000000002", {
            report_kind: "weekly",
            period_sequence: 1,
            period_end_date: "2026-08-07",
            body: "第二页周报正文",
          }),
        ],
        total: 11,
        page: 2,
        page_size: 10,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const user = userEvent.setup()
  renderPanel(queryClient)

  expect(await screen.findByRole("heading", { name: "教学评语" })).toBeVisible()
  expect(await screen.findByText("仅已发布的家庭评语正文")).toBeVisible()
  expect(screen.getByText("主教练甲 · 主教练")).toBeVisible()
  expect(document.body.textContent).not.toContain(forbidden)

  await user.click(screen.getByRole("button", { name: "下一页评语" }))
  expect(await screen.findByText("第二页周报正文")).toBeVisible()
  expect(screen.getByText("周报 · 第 1 期")).toBeVisible()
})

it("isolates current-student data and releases the previous private cache", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const body = url.includes(secondStudentId) ? "学员乙的已发布评语" : "学员甲的已发布评语"
    return jsonResponse({
      items: [publishedItem("90000000-0000-4000-8000-000000000003", { body })],
      total: 1,
      page: 1,
      page_size: 10,
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = renderPanel(queryClient)

  expect(await screen.findByText("学员甲的已发布评语")).toBeVisible()
  const firstKey = reportKeys.published(accountId, {
    studentId: firstStudentId,
    enrollmentId: null,
    page: 1,
    pageSize: 10,
  })
  expect(queryClient.getQueryData(firstKey)).toBeDefined()

  view.rerender(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PublishedReportsPanel studentId={secondStudentId} />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("学员乙的已发布评语")).toBeVisible()
  await waitFor(() => expect(queryClient.getQueryData(firstKey)).toBeUndefined())
  expect(screen.queryByText("学员甲的已发布评语")).not.toBeInTheDocument()
})

it("uses a non-disclosing recoverable error state", async () => {
  const privateDetail = "guardian relationship missing for private student"
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
          request_id: "published-private",
        },
        404,
      ),
    ),
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  renderPanel(queryClient)

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("已发布评语暂时无法读取")
  expect(alert).not.toHaveTextContent(privateDetail)
  expect(screen.getByRole("button", { name: "重试教学评语" })).toBeVisible()
})

it("removes a cached private family report after a private 404", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "guardian relationship no longer authorizes this student",
          code: "evaluation_resource_not_found",
          request_id: "published-private-cache",
        },
        404,
      ),
    ),
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const key = reportKeys.published(accountId, {
    studentId: firstStudentId,
    enrollmentId: null,
    page: 1,
    pageSize: 10,
  })
  queryClient.setQueryData(key, {
    items: [publishedItem("90000000-0000-4000-8000-000000000004")],
    total: 1,
    page: 1,
    page_size: 10,
  })

  renderPanel(queryClient)

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "已发布评语暂时无法读取",
  )
  await waitFor(() => expect(queryClient.getQueryData(key)).toBeUndefined())
})

it("handles global evaluation revocation from the family report route", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "evaluation center access was revoked",
          code: "evaluation_access_denied",
          request_id: "published-global-revocation",
        },
        403,
      ),
    ),
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const currentPrivateKey = [
    ...reportKeys.root(accountId),
    "private-family-body",
  ] as const
  const otherPrivateKey = [
    ...reportKeys.root("other-family-account"),
    "private-family-body",
  ] as const
  queryClient.setQueryData(currentPrivateKey, { body: "当前账号私密评语" })
  queryClient.setQueryData(otherPrivateKey, { body: "其他账号评语" })

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/learning"]}>
        <Routes>
          <Route
            path="/learning"
            element={<PublishedReportsPanel studentId={firstStudentId} />}
          />
          <Route path="/" element={<h1>家庭工作台</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )

  expect(await screen.findByRole("heading", { name: "家庭工作台" })).toBeVisible()
  expect(refreshUser).toHaveBeenCalledTimes(1)
  expect(queryClient.getQueryData(currentPrivateKey)).toBeUndefined()
  expect(queryClient.getQueryData(otherPrivateKey)).toEqual({
    body: "其他账号评语",
  })
})
