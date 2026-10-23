import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { EvaluationTaskDetail } from "./EvaluationTaskDetail"
import type {
  SystemEvaluationResult,
  SystemEvaluationTask,
  SystemEvaluationTaskWorkspace,
} from "./systemEvaluationApi"
import { systemEvaluationKeys } from "./systemEvaluationKeys"
import { systemEvaluationTaskRefetchInterval } from "./useSystemEvaluationContext"

const accountId = "10000000-0000-4000-8000-000000000001"
const taskId = "10000000-0000-4000-8000-000000000011"
const mediaId = "30000000-0000-4000-8000-000000000001"
const originalMatchMedia = window.matchMedia

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

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function result(
  overrides: Partial<SystemEvaluationResult> = {},
): SystemEvaluationResult {
  return {
    id: "20000000-0000-4000-8000-000000000001",
    version: 1,
    source: "automatic",
    fields: { kills: 12, win: true },
    confidences: { kills: 0.98, win: 1 },
    warnings: [{ key: "image_edge", severity: "warning", summary: "画面边缘略有遮挡" }],
    components: [{ key: "combat", score: 99, weight: 1, summary: "分项仅供解释" }],
    missing_fields: [],
    score: 41,
    grade: "B",
    advice: "继续保持稳定训练节奏",
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "rule-1",
    result_schema_version: "schema-1",
    review_decision: "approved",
    reviewed_at: "2026-08-08T12:00:00Z",
    rejection_reason: null,
    published_at: "2026-08-08T12:00:00Z",
    previous_version_id: null,
    ...overrides,
  }
}

function task(
  overrides: Partial<SystemEvaluationTask> = {},
): SystemEvaluationTask {
  const published = result()
  const current = result({
    id: "20000000-0000-4000-8000-000000000002",
    version: 2,
    source: "revision",
    review_decision: "pending",
    reviewed_at: null,
    published_at: null,
    previous_version_id: published.id,
  })
  return {
    id: taskId,
    public_number: "EV-2026-011",
    status: "revision_draft",
    lock_version: 4,
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
    current_result: current,
    published_result: published,
    result_versions: [published, current],
    allowed_actions: {
      match: false,
      save_manual_draft: false,
      submit_review: false,
      create_correction: false,
      create_revision: true,
      review: false,
      archive: false,
      reopen: false,
      view_preview: true,
    },
    allowed_reopen_modes: [],
    ...overrides,
  }
}

function workspace(
  overrides: Partial<SystemEvaluationTaskWorkspace> = {},
): SystemEvaluationTaskWorkspace {
  return {
    api_version: "v2",
    task_id: taskId,
    task_version: 4,
    profile: {
      mode: "automatic",
      adapter_id: "lol",
      adapter_version: "1.0.0",
      scoring_rule_version: "rule-1",
      result_schema_version: "schema-1",
      required_fields: ["kills", "win"],
      media_slots: [{ key: "profile", label: "个人数据", position: 1, required: true }],
    },
    media: [
      {
        id: mediaId,
        slot_key: "profile",
        position: 1,
        mime_type: "image/png",
        size_bytes: 100,
        width_pixels: 10,
        height_pixels: 10,
      },
    ],
    draft: null,
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderDetail(
  taskValue = task(),
  workspaceValue = workspace(),
  options: {
    queryClient?: QueryClient
    privateNotFound?: boolean
    operationDenied?: boolean
    projection?: "account" | "admin"
  } = {},
) {
  const projection = options.projection ?? "account"
  const taskPath = projection === "admin"
    ? `/evaluations/admin/system-tasks/${taskId}`
    : `/evaluations/system/tasks/${taskId}`
  const fetchMock = vi.fn<typeof fetch>(async (request, init) => {
    const url = String(request)
    if (
      url.endsWith(`/evaluations/admin/system-tasks/${taskId}/archive`) &&
      init?.method === "POST"
    ) {
      return jsonResponse({
        api_version: "v2",
        task_id: taskId,
        task_status: "archived",
        task_version: taskValue.lock_version + 1,
      })
    }
    if (url.endsWith(taskPath)) {
      if (options.operationDenied) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "没有执行此评价操作的权限",
            code: "evaluation_operation_denied",
          },
          403,
        )
      }
      if (options.privateNotFound) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Not Found",
            status: 404,
            detail: "评价资源不存在",
            code: "evaluation_resource_not_found",
          },
          404,
        )
      }
      return jsonResponse({ api_version: "v2", task: taskValue })
    }
    if (url.endsWith(`${taskPath}/workspace`)) {
      if (options.operationDenied) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "没有执行此评价操作的权限",
            code: "evaluation_operation_denied",
          },
          403,
        )
      }
      return jsonResponse(workspaceValue)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient =
    options.queryClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter initialEntries={[`/evaluations/system/tasks/${taskId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/evaluations/system/tasks/:taskId"
            element={<EvaluationTaskDetail projection={projection} />}
          />
          <Route path="/evaluations/system/tasks" element={<p>任务名单落点</p>} />
          <Route path="/evaluations" element={<p>评价中心落点</p>} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { fetchMock, queryClient }
}

afterEach(() => {
  vi.unstubAllGlobals()
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  })
})

it("renders metadata and the server result without recalculating its total score", async () => {
  renderDetail()

  const heading = await screen.findByRole("heading", { name: "EV-2026-011" })
  expect(heading).toBeVisible()
  expect(screen.getByText("任务版本 4")).toBeVisible()
  expect(heading.closest("header")).toHaveTextContent("学* · **0001")
  const resultRegion = screen.getByRole("region", { name: "评价结果" })
  expect(within(resultRegion).getByRole("group", { name: "总分" })).toHaveTextContent("41")
  expect(within(resultRegion).getByRole("group", { name: "等级" })).toHaveTextContent("B")
  const kills = within(resultRegion).getByText("kills")
  expect(kills).toBeVisible()
  expect(kills.parentElement).toHaveTextContent("98%")
  expect(within(resultRegion).getByText("画面边缘略有遮挡")).toBeVisible()
  expect(within(resultRegion).getByText("分项仅供解释")).toBeVisible()
  expect(within(resultRegion).getByText("继续保持稳定训练节奏")).toBeVisible()
  expect(resultRegion.querySelector("footer")).toHaveTextContent(
    "lol · 1.0.0 · rule-1 · schema-1",
  )
})

it("renders immutable current and published versions as separate history", async () => {
  renderDetail()

  const history = await screen.findByRole("region", { name: "结果版本历史" })
  expect(within(history).getByRole("article", { name: "结果版本 V1" })).toBeVisible()
  expect(within(history).getByRole("article", { name: "结果版本 V2" })).toHaveTextContent(
    "基于 V1",
  )
  expect(within(history).getByText("当前工作版本 V2")).toBeVisible()
  expect(within(history).getByText("当前发布版本 V1")).toBeVisible()
})

it("uses protected same-origin previews only when the latest server actions allow them", async () => {
  renderDetail()

  const preview = await screen.findByRole("region", { name: "截图预览" })
  const image = within(preview).getByRole("img", { name: "个人数据预览" })
  expect(image).toHaveAttribute(
    "src",
    `/api/v1/evaluations/system/tasks/${taskId}/media/${mediaId}/preview`,
  )
  expect(image).toHaveAttribute("referrerpolicy", "same-origin")
  expect(image.getAttribute("src")).not.toMatch(/^(data|blob):/)
  expect(within(preview).getByText("正在加载个人数据预览")).toBeVisible()
  fireEvent.error(image)
  expect(within(preview).getByRole("alert")).toHaveTextContent("个人数据预览加载失败")
})

it("omits previews when the server removes view_preview even if workspace media remains", async () => {
  renderDetail(
    task({
      allowed_actions: {
        ...task().allowed_actions,
        view_preview: false,
      },
    }),
  )

  expect(await screen.findByRole("heading", { name: "EV-2026-011" })).toBeVisible()
  expect(screen.queryByRole("region", { name: "截图预览" })).not.toBeInTheDocument()
  expect(screen.queryByRole("img")).not.toBeInTheDocument()
})

it("renders the read-only review panel only from the latest server action", async () => {
  const reviewTask = task()
  renderDetail({
    ...reviewTask,
    status: "needs_review",
    allowed_actions: {
      ...reviewTask.allowed_actions,
      create_revision: false,
      review: true,
    },
  })

  const review = await screen.findByRole("region", { name: "审核评价" })
  expect(review).toBeVisible()
  expect(within(review).queryByLabelText(/总分|等级|建议/)).not.toBeInTheDocument()
})

it("opens the current working result inside the mobile full-screen review", async () => {
  const user = userEvent.setup()
  installMatchMedia(768)
  const reviewTask = task()
  renderDetail({
    ...reviewTask,
    status: "needs_review",
    allowed_actions: {
      ...reviewTask.allowed_actions,
      create_revision: false,
      review: true,
    },
  })

  await user.click(await screen.findByRole("button", { name: "打开全屏审核" }))

  const dialog = screen.getByRole("dialog", { name: "审核评价" })
  const resultRegion = within(dialog).getByRole("region", { name: "评价结果" })
  expect(within(resultRegion).getByText("评价结果 V2")).toBeVisible()
  expect(within(resultRegion).getByText("kills").parentElement).toHaveTextContent(
    "98%",
  )
  expect(within(resultRegion).getByText("画面边缘略有遮挡")).toBeVisible()
  expect(within(resultRegion).getByText("分项仅供解释")).toBeVisible()
  expect(within(resultRegion).getByText("继续保持稳定训练节奏")).toBeVisible()
  expect(resultRegion.querySelector("input, select, textarea")).toBeNull()
  const renderedIds = Array.from(document.querySelectorAll<HTMLElement>("[id]"))
    .map((element) => element.id)
  expect(new Set(renderedIds).size).toBe(renderedIds.length)
})

it("renders the latest server-authorized lifecycle actions inside task detail", async () => {
  renderDetail()

  const actions = await screen.findByRole("region", { name: "任务操作" })
  expect(within(actions).getByRole("button", { name: "创建修订" })).toBeVisible()
  expect(within(actions).queryByRole("button", { name: "归档任务" })).not.toBeInTheDocument()
})

it("removes private task caches and returns to the list after an equivalent 404", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const detailKey = systemEvaluationKeys.detail(accountId, "account", taskId)
  const workspaceKey = systemEvaluationKeys.workspace(accountId, "account", taskId)
  const privateDetailKey = [...detailKey, "cached-private"] as const
  const privateWorkspaceKey = [...workspaceKey, "cached-private"] as const
  queryClient.setQueryData(privateDetailKey, { private: "working-result" })
  queryClient.setQueryData(privateWorkspaceKey, { private: "manual-draft" })

  renderDetail(task(), workspace(), { queryClient, privateNotFound: true })

  expect(await screen.findByText("任务名单落点")).toBeVisible()
  expect(queryClient.getQueryData(privateDetailKey)).toBeUndefined()
  expect(queryClient.getQueryData(privateWorkspaceKey)).toBeUndefined()
})

it("purges admin task caches and returns to the list after operation capability loss", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const detailKey = systemEvaluationKeys.detail(accountId, "admin", taskId)
  const workspaceKey = systemEvaluationKeys.workspace(accountId, "admin", taskId)
  const privateDetailKey = [...detailKey, "cached-private"] as const
  const privateWorkspaceKey = [...workspaceKey, "cached-private"] as const
  queryClient.setQueryData(privateDetailKey, { private: "working-result" })
  queryClient.setQueryData(privateWorkspaceKey, { private: "manual-draft" })

  renderDetail(task(), workspace(), {
    queryClient,
    operationDenied: true,
    projection: "admin",
  })

  expect(await screen.findByText("评价中心落点")).toBeVisible()
  expect(queryClient.getQueryData(privateDetailKey)).toBeUndefined()
  expect(queryClient.getQueryData(privateWorkspaceKey)).toBeUndefined()
})

it("invalidates list, dashboard and published history after a lifecycle success", async () => {
  const user = userEvent.setup()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const listKey = systemEvaluationKeys.list(accountId, "account", {
    page: 1,
    pageSize: 20,
  })
  const dashboardKey = systemEvaluationKeys.dashboard(accountId)
  const publishedKey = systemEvaluationKeys.published(
    accountId,
    "40000000-0000-4000-8000-000000000001",
    1,
  )
  queryClient.setQueryData(listKey, { cached: "list" })
  queryClient.setQueryData(dashboardKey, { cached: "dashboard" })
  queryClient.setQueryData(publishedKey, { cached: "published" })
  const value = task({
    status: "failed",
    allowed_actions: {
      ...task().allowed_actions,
      create_revision: false,
      archive: true,
    },
  })
  renderDetail(value, workspace(), { queryClient })

  await user.click(await screen.findByRole("button", { name: "归档任务" }))
  await user.type(screen.getByLabelText("操作原因"), "无法恢复的处理任务")
  await user.click(screen.getByRole("button", { name: "确认归档" }))

  expect(queryClient.getQueryState(listKey)?.isInvalidated).toBe(true)
  expect(queryClient.getQueryState(dashboardKey)?.isInvalidated).toBe(true)
  expect(queryClient.getQueryState(publishedKey)?.isInvalidated).toBe(true)
})

it("renders only the published result when the server omits a working result", async () => {
  const published = result()
  renderDetail(
    task({
      status: "published",
      current_result: null,
      published_result: published,
      result_versions: [published],
    }),
  )

  expect(await screen.findByRole("region", { name: "评价结果" })).toHaveTextContent("V1")
  expect(screen.queryByText(/当前工作版本/)).not.toBeInTheDocument()
  expect(screen.getByText("当前发布版本 V1")).toBeVisible()
})

it("keeps preview before result content in the compact reading order", async () => {
  renderDetail()
  const preview = await screen.findByRole("region", { name: "截图预览" })
  const resultRegion = screen.getByRole("region", { name: "评价结果" })
  expect(preview.compareDocumentPosition(resultRegion) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
})

it("polls every non-terminal detail and stops for terminal or hidden pages", () => {
  const active = task({ status: "processing" })
  expect(systemEvaluationTaskRefetchInterval({ api_version: "v2", task: active }, "visible"))
    .toBe(5_000)
  expect(
    systemEvaluationTaskRefetchInterval(
      { api_version: "v2", task: task({ status: "published" }) },
      "visible",
    ),
  ).toBe(false)
  expect(
    systemEvaluationTaskRefetchInterval(
      { api_version: "v2", task: task({ status: "archived" }) },
      "visible",
    ),
  ).toBe(false)
  expect(
    systemEvaluationTaskRefetchInterval(
      { api_version: "v2", task: active },
      "hidden",
    ),
  ).toBe(false)
})
