import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { SystemEvaluationDashboard } from "./SystemEvaluationDashboard"

const accountId = "10000000-0000-4000-8000-000000000001"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderDashboard(body: Record<string, unknown>) {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    jsonResponse(body),
  )
  vi.stubGlobal("fetch", fetchMock)
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <SystemEvaluationDashboard />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

it("renders seven server-authoritative count cards and native queue links", async () => {
  const fetchMock = renderDashboard({
    api_version: "v2",
    upload_available: true,
    processing: 1,
    pending_match: 2,
    manual_input: 3,
    needs_review: 4,
    rejected: 5,
    published: 6,
  })

  expect(await screen.findByRole("heading", { name: "系统评价概览" })).toBeVisible()
  expect(screen.getAllByRole("article")).toHaveLength(7)
  expect(within(screen.getByRole("article", { name: "可上传" })).getByText("可用")).toBeVisible()
  expect(
    within(screen.getByRole("article", { name: "可上传" })).getByRole("link", {
      name: "打开截图评价",
    }),
  ).toHaveAttribute("href", "/evaluations/system")

  const expectations = [
    ["处理中", "1", "/evaluations/system/tasks?queue=processing"],
    ["待匹配", "2", "/evaluations/system/tasks?status=pending_match"],
    ["待人工录入", "3", "/evaluations/system/tasks?queue=manual_input"],
    ["待审核", "4", "/evaluations/system/tasks?status=needs_review"],
    ["被驳回", "5", "/evaluations/system/tasks?status=rejected"],
    ["已发布", "6", "/evaluations/system/tasks?published_only=true"],
  ] as const
  for (const [label, value, href] of expectations) {
    const card = screen.getByRole("article", { name: label })
    expect(within(card).getByText(value)).toBeVisible()
    expect(within(card).getByRole("link", { name: `查看${label}` })).toHaveAttribute(
      "href",
      href,
    )
  }
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/evaluations/system/dashboard",
    expect.objectContaining({ credentials: "include" }),
  )
})

it("keeps the upload card read-only when the server disables uploads", async () => {
  renderDashboard({
    api_version: "v2",
    upload_available: false,
    processing: 0,
    pending_match: 0,
    manual_input: 0,
    needs_review: 0,
    rejected: 0,
    published: 0,
  })

  const upload = await screen.findByRole("article", { name: "可上传" })
  expect(within(upload).getByText("未开放")).toBeVisible()
  expect(within(upload).queryByRole("link")).not.toBeInTheDocument()
})
