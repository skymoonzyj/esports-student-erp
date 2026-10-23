import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { EvaluationTaskList } from "./EvaluationTaskList"
import { systemEvaluationRefetchInterval } from "./useSystemEvaluationContext"
import type {
  SystemEvaluationTask,
  SystemEvaluationTaskFilterOptions,
  SystemEvaluationTaskPage,
} from "./systemEvaluationApi"

const accountId = "10000000-0000-4000-8000-000000000001"
const taskId = "10000000-0000-4000-8000-000000000011"
const originalMatchMedia = window.matchMedia

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

const noActions = {
  match: false,
  save_manual_draft: false,
  submit_review: false,
  create_correction: false,
  create_revision: false,
  review: false,
  archive: false,
  reopen: false,
  view_preview: false,
}

function task(
  overrides: Partial<SystemEvaluationTask> = {},
): SystemEvaluationTask {
  return {
    id: taskId,
    public_number: "EV-2026-011",
    status: "published",
    lock_version: 2,
    created_at: "2026-08-08T10:00:00Z",
    student_person_id: "40000000-0000-4000-8000-000000000001",
    enrollment_id: "50000000-0000-4000-8000-000000000001",
    cohort_id: "60000000-0000-4000-8000-000000000001",
    project_id: "70000000-0000-4000-8000-000000000001",
    class_id: "80000000-0000-4000-8000-000000000001",
    identity_summary: "学* · **0001",
    cohort_name: "2026 夏季营",
    project_name: "英雄联盟",
    class_name: "菁英班",
    media_count: 1,
    current_result: null,
    published_result: null,
    result_versions: [],
    allowed_actions: noActions,
    allowed_reopen_modes: [],
    ...overrides,
  }
}

function page(
  items: SystemEvaluationTask[],
  overrides: Partial<SystemEvaluationTaskPage> = {},
): SystemEvaluationTaskPage {
  return {
    api_version: "v2",
    items,
    total: items.length,
    page: 1,
    page_size: 20,
    ...overrides,
  }
}

function filterOptions(): SystemEvaluationTaskFilterOptions {
  return {
    api_version: "v2",
    cohorts: [
      { id: "60000000-0000-4000-8000-000000000002", label: "目录营期" },
    ],
    projects: [
      { id: "70000000-0000-4000-8000-000000000002", label: "目录项目" },
    ],
    classes: [
      { id: "80000000-0000-4000-8000-000000000002", label: "目录班级" },
    ],
    students: [
      { id: "40000000-0000-4000-8000-000000000002", label: "目* / S***02" },
    ],
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function installMatchMedia(width: number): void {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: vi.fn((query: string) => ({
      matches: query === "(max-width: 768px)" ? width <= 768 : false,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(() => true),
    })),
  })
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{`${location.pathname}${location.search}`}</output>
}

function renderList(input: {
  width: number
  entry?: string
  projection?: "account" | "admin"
  response: (url: string) => SystemEvaluationTaskPage
  directoryResponse?: () => Response
}) {
  installMatchMedia(input.width)
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.includes("task-filter-options")) {
      return input.directoryResponse?.() ?? jsonResponse(filterOptions())
    }
    return jsonResponse(input.response(url))
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter initialEntries={[input.entry ?? "/evaluations/system/tasks"]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/evaluations/system/tasks"
            element={<EvaluationTaskList projection={input.projection ?? "account"} />}
          />
        </Routes>
        <LocationProbe />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { fetchMock, queryClient }
}

function requestedUrls(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>): string[] {
  return fetchMock.mock.calls.map(([request]) => String(request))
}

afterEach(() => {
  vi.unstubAllGlobals()
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  })
})

it("renders a semantic desktop table in server order with server pagination metadata", async () => {
  const second = task({
    id: "10000000-0000-4000-8000-000000000012",
    public_number: "EV-2026-012",
    identity_summary: "王* · **0012",
    status: "rejected",
  })
  const first = task()
  const result = renderList({
    width: 1440,
    entry: "/evaluations/system/tasks?status=published&page=2",
    response: () => page([second, first], { total: 22, page: 2 }),
  })

  const table = await screen.findByRole("table", { name: "系统评价任务" })
  expect(screen.queryByRole("region", { name: "系统评价任务卡片" })).not.toBeInTheDocument()
  const rows = within(table).getAllByRole("row")
  expect(rows).toHaveLength(3)
  expect(rows[1]).toHaveTextContent("EV-2026-012")
  expect(rows[2]).toHaveTextContent("EV-2026-011")
  expect(within(rows[1]!).getByText("王* · **0012")).toBeVisible()
  expect(within(rows[1]!).getByText("被驳回")).toBeVisible()
  expect(within(rows[1]!).getByRole("link", { name: "查看 EV-2026-012" })).toHaveAttribute(
    "href",
    "/evaluations/system/tasks/10000000-0000-4000-8000-000000000012",
  )
  expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled()
  expect(requestedUrls(result.fetchMock)).toContain(
    "/api/v1/evaluations/system/tasks?status=published&page=2&page_size=20",
  )
})

it("renders one mobile card per task without a duplicate desktop table at 768px", async () => {
  renderList({
    width: 768,
    response: () => page([task()]),
  })

  const cards = await screen.findByRole("region", { name: "系统评价任务卡片" })
  expect(within(cards).getAllByRole("article")).toHaveLength(1)
  expect(within(cards).getByRole("article", { name: "EV-2026-011" })).toHaveTextContent(
    "英雄联盟",
  )
  expect(screen.queryByRole("table", { name: "系统评价任务" })).not.toBeInTheDocument()
})

it("uses the admin queue projection without inferring it from local roles", async () => {
  const result = renderList({
    width: 1024,
    projection: "admin",
    entry: "/evaluations/system/tasks?queue=needs_review",
    response: () => page([]),
  })

  expect(await screen.findByText("没有符合条件的系统评价任务")).toBeVisible()
  expect(requestedUrls(result.fetchMock)).toContain(
    "/api/v1/evaluations/admin/system-tasks?queue=needs_review&page=1&page_size=20",
  )
  expect(requestedUrls(result.fetchMock)).toContain(
    "/api/v1/evaluations/admin/system-task-filter-options",
  )
})

it("uses an account aggregate queue from a dashboard link", async () => {
  const result = renderList({
    width: 1024,
    entry: "/evaluations/system/tasks?queue=processing",
    response: () => page([]),
  })

  expect(await screen.findByText("没有符合条件的系统评价任务")).toBeVisible()
  expect(screen.getByLabelText("任务状态")).toHaveValue("queue:processing")
  expect(requestedUrls(result.fetchMock)).toContain(
    "/api/v1/evaluations/system/tasks?queue=processing&page=1&page_size=20",
  )
})

it("renders all directory-backed filters even when the current task page is empty", async () => {
  const result = renderList({
    width: 1440,
    response: () => page([]),
  })

  expect(await screen.findByRole("option", { name: "目录营期" })).toBeVisible()
  expect(screen.getByRole("option", { name: "目录项目" })).toBeVisible()
  expect(screen.getByRole("option", { name: "目录班级" })).toBeVisible()
  expect(screen.getByRole("option", { name: "目* / S***02" })).toBeVisible()
  expect(screen.getByLabelText("营期")).toBeVisible()
  expect(screen.getByLabelText("项目")).toBeVisible()
  expect(screen.getByLabelText("班级")).toBeVisible()
  expect(screen.getByLabelText("学员")).toBeVisible()
  expect(screen.getByText("没有符合条件的系统评价任务")).toBeVisible()
  expect(requestedUrls(result.fetchMock)).toContain(
    "/api/v1/evaluations/system/task-filter-options",
  )
})

it("preserves every other URL filter and resets pagination for each directory filter", async () => {
  const user = userEvent.setup()
  renderList({
    width: 1440,
    entry:
      "/evaluations/system/tasks?status=published&published_only=true&source=dashboard&page=4",
    response: () => page([]),
  })

  await screen.findByRole("option", { name: "目录营期" })
  await user.selectOptions(screen.getByLabelText("营期"), filterOptions().cohorts[0]!.id)
  expect(screen.getByTestId("location")).toHaveTextContent(
    "status=published&published_only=true&source=dashboard&page=1&cohort_id=60000000-0000-4000-8000-000000000002",
  )

  await user.selectOptions(screen.getByLabelText("项目"), filterOptions().projects[0]!.id)
  expect(screen.getByTestId("location")).toHaveTextContent(
    "status=published&published_only=true&source=dashboard&page=1&cohort_id=60000000-0000-4000-8000-000000000002&project_id=70000000-0000-4000-8000-000000000002",
  )

  await user.selectOptions(screen.getByLabelText("班级"), filterOptions().classes[0]!.id)
  expect(screen.getByTestId("location")).toHaveTextContent(
    "cohort_id=60000000-0000-4000-8000-000000000002&project_id=70000000-0000-4000-8000-000000000002&class_id=80000000-0000-4000-8000-000000000002",
  )

  await user.selectOptions(screen.getByLabelText("学员"), filterOptions().students[0]!.id)
  expect(screen.getByTestId("location")).toHaveTextContent(
    "cohort_id=60000000-0000-4000-8000-000000000002&project_id=70000000-0000-4000-8000-000000000002&class_id=80000000-0000-4000-8000-000000000002&student_id=40000000-0000-4000-8000-000000000002",
  )
  expect(screen.getByTestId("location")).toHaveTextContent("page=1")
  expect(screen.getByTestId("location")).toHaveTextContent("source=dashboard")
})

it("keeps an already-loaded task list visible when the filter directory fails", async () => {
  renderList({
    width: 1440,
    response: () => page([task()]),
    directoryResponse: () =>
      jsonResponse(
        {
          type: "about:blank",
          title: "Service unavailable",
          status: 503,
          detail: "private-upstream-detail",
        },
        503,
      ),
  })

  expect(await screen.findByRole("table", { name: "系统评价任务" })).toBeVisible()
  expect(screen.getByRole("alert")).toHaveTextContent(
    "筛选选项暂时无法加载，请重试。",
  )
  expect(screen.getByRole("button", { name: "重试筛选选项" })).toBeVisible()
  expect(screen.queryByText("private-upstream-detail")).not.toBeInTheDocument()
})

it("changes only the server page and preserves filters when paging", async () => {
  const user = userEvent.setup()
  renderList({
    width: 1440,
    entry: "/evaluations/system/tasks?status=needs_review&page=2",
    response: (url) =>
      page([task({ status: "needs_review" })], {
        total: 41,
        page: url.includes("page=1") ? 1 : 2,
      }),
  })

  await screen.findByRole("table", { name: "系统评价任务" })
  await user.click(screen.getByRole("button", { name: "上一页" }))
  expect(screen.getByTestId("location")).toHaveTextContent(
    "/evaluations/system/tasks?status=needs_review&page=1",
  )
})

it("polls every non-terminal task status and stops for published, archived, or hidden pages", () => {
  const activeStatuses: SystemEvaluationTask["status"][] = [
    "uploaded",
    "identifying",
    "pending_match",
    "queued",
    "processing",
    "failed",
    "manual_input",
    "needs_review",
    "rejected",
    "correction_pending",
    "revision_draft",
  ]
  for (const status of activeStatuses) {
    expect(systemEvaluationRefetchInterval(page([task({ status })]), "visible")).toBe(5_000)
  }
  expect(
    systemEvaluationRefetchInterval(
      page([task({ status: "published" }), task({ status: "archived" })]),
      "visible",
    ),
  ).toBe(false)
  expect(
    systemEvaluationRefetchInterval(page([task({ status: "processing" })]), "hidden"),
  ).toBe(false)
})
