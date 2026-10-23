import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PublishedEvaluationPanel } from "./PublishedEvaluationPanel"
import type { SystemEvaluationResult, SystemEvaluationTask } from "./systemEvaluationApi"

const studentId = "40000000-0000-4000-8000-000000000001"
const taskId = "10000000-0000-4000-8000-000000000011"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: "account-guardian" } }),
}))

function result(
  version: number,
  advice: string,
  overrides: Partial<SystemEvaluationResult> = {},
): SystemEvaluationResult {
  return {
    id: `20000000-0000-4000-8000-00000000000${version}`,
    version,
    source: version === 1 ? "automatic" : "revision",
    fields: { kills: 12 },
    confidences: { kills: 0.98 },
    warnings: [],
    components: [],
    missing_fields: [],
    score: version === 1 ? 88 : 99,
    grade: version === 1 ? "A" : "S",
    advice,
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "rule-1",
    result_schema_version: "schema-1",
    review_decision: version === 1 ? "approved" : "pending",
    reviewed_at: version === 1 ? "2026-08-08T12:00:00Z" : null,
    rejection_reason: null,
    published_at: version === 1 ? "2026-08-08T12:00:00Z" : null,
    previous_version_id: version === 1 ? null : "20000000-0000-4000-8000-000000000001",
    ...overrides,
  }
}

function task(): SystemEvaluationTask {
  const published = result(1, "家庭可以查看的已发布建议")
  const working = result(2, "尚未发布的私有修订意见")
  return {
    id: taskId,
    public_number: "EV-2026-011",
    status: "revision_draft",
    lock_version: 4,
    created_at: "2026-08-08T10:00:00Z",
    student_person_id: studentId,
    enrollment_id: "50000000-0000-4000-8000-000000000001",
    cohort_id: "60000000-0000-4000-8000-000000000001",
    project_id: "70000000-0000-4000-8000-000000000001",
    class_id: "80000000-0000-4000-8000-000000000001",
    identity_summary: "学* · **0001",
    cohort_name: "2026 夏季营",
    project_name: "英雄联盟",
    class_name: "菁英班",
    media_count: 1,
    current_result: working,
    published_result: published,
    result_versions: [published, working],
    allowed_actions: {
      match: false,
      save_manual_draft: false,
      submit_review: false,
      create_correction: false,
      create_revision: false,
      review: false,
      archive: false,
      reopen: false,
      view_preview: false,
    },
    allowed_reopen_modes: [],
  }
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPanel(items = [task()]) {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    jsonResponse({ api_version: "v2", items, total: items.length, page: 1, page_size: 10 }),
  )
  vi.stubGlobal("fetch", fetchMock)
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <PublishedEvaluationPanel studentId={studentId} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return fetchMock
}

afterEach(() => vi.unstubAllGlobals())

it("requests pagination-safe published history for the selected learner", async () => {
  const fetchMock = renderPanel()

  expect(await screen.findByRole("region", { name: "系统评价历史" })).toBeVisible()
  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    `/api/v1/evaluations/system/tasks?student_id=${studentId}&published_only=true&page=1&page_size=10`,
  )
})

it("renders only the published result even when a working revision is present", async () => {
  renderPanel()

  const history = await screen.findByRole("region", { name: "系统评价历史" })
  const item = within(history).getByRole("article", { name: "EV-2026-011 已发布评价" })
  expect(item).toHaveTextContent("88 分 · A")
  expect(item).toHaveTextContent("家庭可以查看的已发布建议")
  expect(item).not.toHaveTextContent("99 分 · S")
  expect(item).not.toHaveTextContent("尚未发布的私有修订意见")
})

it("shows an explicit empty state without falling back to a working result", async () => {
  renderPanel([])

  expect(await screen.findByText("该学员暂无已发布的系统评价")).toBeVisible()
  expect(screen.queryByRole("article")).not.toBeInTheDocument()
})

it("returns to page one before requesting a newly selected learner", async () => {
  const nextStudentId = "40000000-0000-4000-8000-000000000002"
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const page = new URL(String(input), "http://erp.test").searchParams.get("page")
    return jsonResponse({
      api_version: "v2",
      items: [],
      total: 20,
      page: Number(page),
      page_size: 10,
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (selectedStudentId: string) => (
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <PublishedEvaluationPanel studentId={selectedStudentId} />
      </QueryClientProvider>
    </MemoryRouter>
  )
  const view = render(tree(studentId))
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "下一页" }))
  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([input]) =>
      String(input).includes(`student_id=${studentId}`) && String(input).includes("page=2"),
    )).toBe(true)
  })

  view.rerender(tree(nextStudentId))

  await waitFor(() => {
    expect(fetchMock.mock.calls.some(([input]) =>
      String(input).includes(`student_id=${nextStudentId}`) && String(input).includes("page=1"),
    )).toBe(true)
  })
  expect(fetchMock.mock.calls.some(([input]) =>
    String(input).includes(`student_id=${nextStudentId}`) && String(input).includes("page=2"),
  )).toBe(false)

  view.rerender(tree(studentId))

  expect(await screen.findByText("第 1 页")).toBeVisible()
})
