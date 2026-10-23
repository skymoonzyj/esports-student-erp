import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { EvaluationAuthorizationProvider } from "./EvaluationAuthorization"
import { ReportVersionTimeline } from "./ReportVersionTimeline"
import { reportKeys } from "./reportKeys"

const accountId = "10000000-0000-4000-8000-000000000096"
const firstReportId = "20000000-0000-4000-8000-000000000001"
const secondReportId = "20000000-0000-4000-8000-000000000002"
const firstVersionId = "30000000-0000-4000-8000-000000000001"
const secondVersionId = "30000000-0000-4000-8000-000000000002"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function version(
  reportId: string,
  versionNumber: number,
  overrides: Record<string, unknown> = {},
) {
  return {
    report_id: reportId,
    version_id: versionNumber === 1 ? firstVersionId : secondVersionId,
    version: versionNumber,
    source: versionNumber === 1 ? "initial" : "revision",
    body: versionNumber === 1 ? "第一版完整评语" : "第二版完整评语",
    dimension_values: {},
    submitted_at:
      versionNumber === 1 ? "2026-08-21T02:00:00Z" : "2026-08-22T02:00:00Z",
    is_current: versionNumber === 2,
    is_published: versionNumber === 1,
    review:
      versionNumber === 1
        ? {
            decision: "approved",
            reason: null,
            reviewed_at: "2026-08-21T04:00:00Z",
          }
        : {
            decision: "rejected",
            reason: "需要补充复盘结论",
            reviewed_at: "2026-08-22T04:00:00Z",
          },
    ...overrides,
  }
}

function page(
  reportId: string,
  overrides: Record<string, unknown> = {},
) {
  return {
    items: [version(reportId, 2), version(reportId, 1)],
    total: 2,
    page: 1,
    page_size: 20,
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderTimeline(
  reportId: string,
  input: {
    queryClient?: QueryClient
    compareCurrent?: boolean
    pending?: boolean
    revision?: number
    onUnavailable?: (reportId: string) => Promise<void> | void
  } = {},
) {
  const queryClient =
    input.queryClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={queryClient}>
      <EvaluationAuthorizationProvider
        pending={input.pending ?? false}
        revision={input.revision ?? 0}
      >
        <ReportVersionTimeline
          reportId={reportId}
          compareCurrent={input.compareCurrent}
          onUnavailable={input.onUnavailable}
        />
      </EvaluationAuthorizationProvider>
    </QueryClientProvider>,
  )
  return { queryClient, view }
}

afterEach(() => vi.unstubAllGlobals())

it("renders immutable versions and compares the newest two without duplicating them", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse(page(firstReportId)),
  )
  vi.stubGlobal("fetch", fetchMock)
  renderTimeline(firstReportId, { compareCurrent: true })

  expect(await screen.findByRole("heading", { name: "版本记录" })).toBeVisible()
  expect(await screen.findByText("版本 2")).toBeVisible()
  expect(screen.getByText("需要补充复盘结论")).toBeVisible()
  expect(screen.getByText("当前版本")).toBeVisible()
  expect(screen.getByText("已发布版本")).toBeVisible()
  expect(
    screen.getByRole("region", { name: "版本 2 与版本 1 对比" }),
  ).toBeVisible()
  expect(screen.getAllByText("第二版完整评语")).toHaveLength(1)
  expect(fetchMock.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal)
})

it("paginates the private version timeline", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.includes("page=2")) {
      return jsonResponse({
        items: [version(firstReportId, 1, { body: "第二页版本正文" })],
        total: 21,
        page: 2,
        page_size: 20,
      })
    }
    return jsonResponse(page(firstReportId, { total: 21 }))
  })
  vi.stubGlobal("fetch", fetchMock)
  renderTimeline(firstReportId)
  const user = userEvent.setup()

  await screen.findByText("第二版完整评语")
  await user.click(screen.getByRole("button", { name: "下一页版本" }))

  expect(await screen.findByText("第二页版本正文")).toBeVisible()
  expect(screen.getByText("第 2 / 2 页")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("page=2&page_size=20")),
  ).toBe(true)
})

it("shows a recoverable error without disclosing the private server detail", async () => {
  const privateDetail = "private report relationship and reviewer scope"
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: privateDetail,
          code: "evaluation_resource_not_found",
        },
        404,
      ),
    )
    .mockResolvedValueOnce(jsonResponse(page(firstReportId)))
  vi.stubGlobal("fetch", fetchMock)
  renderTimeline(firstReportId)
  const user = userEvent.setup()

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("版本记录暂时无法读取")
  expect(alert).not.toHaveTextContent(privateDetail)
  await user.click(screen.getByRole("button", { name: "重试版本记录" }))
  expect(await screen.findByText("第二版完整评语")).toBeVisible()
})

it("contains a rejected private-target recovery callback", async () => {
  const recoveryFailure = new Error("target refresh failed")
  const observeRejection = vi.fn()
  const rejectedThenable = {
    then(
      _resolve: (value: void) => void,
      reject: (reason: unknown) => void,
    ): void {
      observeRejection()
      reject(recoveryFailure)
    },
  } as unknown as Promise<void>
  const onUnavailable = vi.fn().mockReturnValue(rejectedThenable)
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "private target relationship",
          code: "evaluation_resource_not_found",
        },
        404,
      ),
    ),
  )

  renderTimeline(firstReportId, { onUnavailable })

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "版本记录暂时无法读取",
  )
  await waitFor(() => expect(onUnavailable).toHaveBeenCalledWith(firstReportId))
  await waitFor(() => expect(observeRejection).toHaveBeenCalledOnce())
})

it("hides cached private versions while authorization is pending", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const key = [
    ...reportKeys.versions(accountId, {
      reportId: firstReportId,
      page: 1,
      pageSize: 20,
    }),
    { authorizationRevision: 0 },
  ] as const
  queryClient.setQueryData(key, page(firstReportId, {
    items: [version(firstReportId, 2, { body: "不得短暂显示的缓存正文" })],
  }))
  const fetchMock = vi.fn<typeof fetch>()
  vi.stubGlobal("fetch", fetchMock)

  renderTimeline(firstReportId, { queryClient, pending: true })

  expect(screen.queryByText("不得短暂显示的缓存正文")).not.toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "版本记录" })).not.toBeInTheDocument()
  expect(fetchMock).not.toHaveBeenCalled()
})

it("releases the previous report query when the report id changes", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const reportId = url.includes(secondReportId) ? secondReportId : firstReportId
    return jsonResponse(
      page(reportId, {
        items: [
          version(reportId, 2, {
            body: reportId === firstReportId ? "第一份报告正文" : "第二份报告正文",
          }),
        ],
      }),
    )
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const result = renderTimeline(firstReportId, { queryClient })

  expect(await screen.findByText("第一份报告正文")).toBeVisible()
  const firstKey = [
    ...reportKeys.versions(accountId, {
      reportId: firstReportId,
      page: 1,
      pageSize: 20,
    }),
    { authorizationRevision: 0 },
  ] as const
  expect(queryClient.getQueryData(firstKey)).toBeDefined()

  result.view.rerender(
    <QueryClientProvider client={queryClient}>
      <EvaluationAuthorizationProvider pending={false} revision={0}>
        <ReportVersionTimeline reportId={secondReportId} />
      </EvaluationAuthorizationProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("第二份报告正文")).toBeVisible()
  await waitFor(() => expect(queryClient.getQueryData(firstKey)).toBeUndefined())
  expect(screen.queryByText("第一份报告正文")).not.toBeInTheDocument()
})
