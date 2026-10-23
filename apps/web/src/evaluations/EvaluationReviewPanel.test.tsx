import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import { EvaluationReviewPanel } from "./EvaluationReviewPanel"
import type { SystemEvaluationResult, SystemEvaluationTask } from "./systemEvaluationApi"

const taskId = "10000000-0000-4000-8000-000000000011"
const resultId = "20000000-0000-4000-8000-000000000001"
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

function result(): SystemEvaluationResult {
  return {
    id: resultId,
    version: 1,
    source: "automatic",
    fields: { kills: 12 },
    confidences: { kills: 0.98 },
    warnings: [],
    components: [{ key: "combat", score: 88, weight: 1, summary: "发挥稳定" }],
    missing_fields: [],
    score: 88,
    grade: "A",
    advice: "继续保持训练节奏",
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "rule-1",
    result_schema_version: "schema-1",
    review_decision: "pending",
    reviewed_at: null,
    rejection_reason: null,
    published_at: null,
    previous_version_id: null,
  }
}

function task(review = true): SystemEvaluationTask {
  const current = result()
  return {
    id: taskId,
    public_number: "EV-2026-011",
    status: "needs_review",
    lock_version: 3,
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
    published_result: null,
    result_versions: [current],
    allowed_actions: {
      match: false,
      save_manual_draft: false,
      submit_review: false,
      create_correction: false,
      create_revision: false,
      review,
      archive: false,
      reopen: false,
      view_preview: true,
    },
    allowed_reopen_modes: [],
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPanel(input: {
  task?: SystemEvaluationTask
  response?: Response
  fetch?: typeof fetch
  onRefresh?: () => void | Promise<void>
  idempotencyKeyFactory?: () => string
  width?: number
  readOnlyResult?: ReactNode
} = {}) {
  installMatchMedia(input.width ?? 1024)
  const successResponse = () =>
    input.response ??
    jsonResponse({
        api_version: "v2",
        task_id: taskId,
        result_id: resultId,
        result_version: 1,
        task_status: "published",
        task_version: 4,
        review_decision: "approved",
        reviewed_at: "2026-08-08T12:00:00Z",
        published_at: "2026-08-08T12:00:00Z",
      })
  const fetchMock = vi.fn<typeof fetch>(
    input.fetch ?? (async () => successResponse()),
  )
  vi.stubGlobal("fetch", fetchMock)
  const onRefresh = vi.fn(input.onRefresh ?? (() => undefined))
  const onReviewed = vi.fn()
  const rendered = render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
    >
      <EvaluationReviewPanel
        task={input.task ?? task()}
        onRefresh={onRefresh}
        onReviewed={onReviewed}
        idempotencyKeyFactory={input.idempotencyKeyFactory ?? (() => "review-key-1")}
        readOnlyResult={input.readOnlyResult}
      />
    </QueryClientProvider>,
  )
  return { fetchMock, onRefresh, onReviewed, ...rendered }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: originalMatchMedia,
  })
})

it("renders nothing when the latest server actions do not allow review", () => {
  renderPanel({ task: task(false) })

  expect(screen.queryByRole("region", { name: "审核评价" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "提交审核" })).not.toBeInTheDocument()
})

it("keeps the review inline on desktop but opens one full-screen read-only review on mobile", async () => {
  const user = userEvent.setup()
  const readOnlyResult = (
    <section role="region" aria-label="完整只读评价">
      <p>kills 12 · 98%</p>
      <p>画面边缘略有遮挡</p>
      <p>战斗分项 88</p>
      <p>继续保持训练节奏</p>
    </section>
  )
  const desktop = renderPanel({ width: 769, readOnlyResult })

  expect(screen.getByRole("region", { name: "审核评价" })).toBeVisible()
  expect(screen.getByRole("radio", { name: "通过" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "打开全屏审核" })).not.toBeInTheDocument()
  expect(screen.queryByRole("dialog", { name: "审核评价" })).not.toBeInTheDocument()
  desktop.unmount()

  renderPanel({ width: 768, readOnlyResult })
  const opener = screen.getByRole("button", { name: "打开全屏审核" })
  expect(screen.queryByRole("radio", { name: "通过" })).not.toBeInTheDocument()

  await user.click(opener)

  const dialog = screen.getByRole("dialog", { name: "审核评价" })
  expect(within(dialog).getByRole("region", { name: "完整只读评价" })).toHaveTextContent(
    "kills 12 · 98%",
  )
  expect(within(dialog).getByText("画面边缘略有遮挡")).toBeVisible()
  expect(within(dialog).getByText("战斗分项 88")).toBeVisible()
  expect(within(dialog).getByText("继续保持训练节奏")).toBeVisible()
  expect(within(dialog).queryByLabelText(/总分|等级|建议/)).not.toBeInTheDocument()
  expect(within(dialog).getByRole("radio", { name: "通过" })).toBeVisible()
})

it("closes the mobile review with Escape or cancel and restores focus", async () => {
  const user = userEvent.setup()
  renderPanel({ width: 360 })
  const opener = screen.getByRole("button", { name: "打开全屏审核" })

  await user.click(opener)
  await user.keyboard("{Escape}")
  expect(screen.queryByRole("dialog", { name: "审核评价" })).not.toBeInTheDocument()
  await waitFor(() => expect(opener).toHaveFocus())

  await user.click(opener)
  await user.click(
    within(screen.getByRole("dialog", { name: "审核评价" })).getByRole(
      "button",
      { name: "取消" },
    ),
  )
  expect(screen.queryByRole("dialog", { name: "审核评价" })).not.toBeInTheDocument()
  await waitFor(() => expect(opener).toHaveFocus())
})

it("locks every mobile dismissal path while a review request is pending", async () => {
  const user = userEvent.setup()
  let resolveRequest!: (response: Response) => void
  const pendingResponse = new Promise<Response>((resolve) => {
    resolveRequest = resolve
  })
  renderPanel({
    width: 360,
    fetch: vi.fn<typeof fetch>(() => pendingResponse),
  })
  await user.click(screen.getByRole("button", { name: "打开全屏审核" }))
  const dialog = screen.getByRole("dialog", { name: "审核评价" })
  await user.click(within(dialog).getByRole("radio", { name: "通过" }))
  await user.click(within(dialog).getByRole("button", { name: "提交审核" }))

  expect(within(dialog).getByRole("button", { name: "关闭审核评价" })).toBeDisabled()
  expect(within(dialog).getByRole("button", { name: "取消" })).toBeDisabled()
  expect(within(dialog).getByRole("radio", { name: "通过" })).toBeDisabled()
  await user.keyboard("{Escape}")
  expect(screen.getByRole("dialog", { name: "审核评价" })).toBeVisible()

  resolveRequest(
    jsonResponse({
      api_version: "v2",
      task_id: taskId,
      result_id: resultId,
      result_version: 1,
      task_status: "published",
      task_version: 4,
      review_decision: "approved",
      reviewed_at: "2026-08-08T12:00:00Z",
      published_at: "2026-08-08T12:00:00Z",
    }),
  )
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "审核评价" })).not.toBeInTheDocument(),
  )
})

it("keeps result inputs read-only and requires a rejection reason", async () => {
  const user = userEvent.setup()
  const view = renderPanel()
  const panel = screen.getByRole("region", { name: "审核评价" })

  expect(within(panel).getByText("88 分 · A")).toBeVisible()
  expect(within(panel).getByText("继续保持训练节奏")).toBeVisible()
  expect(within(panel).queryByLabelText(/总分|等级|建议/)).not.toBeInTheDocument()
  await user.click(within(panel).getByRole("radio", { name: "驳回" }))
  const reason = within(panel).getByLabelText("驳回原因")
  expect(reason).toBeRequired()
  expect(within(panel).getByRole("button", { name: "提交审核" })).toBeDisabled()

  await user.type(reason, "需要补充清晰截图")
  await user.click(within(panel).getByRole("button", { name: "提交审核" }))

  expect(view.fetchMock).toHaveBeenCalledTimes(1)
  const [, init] = view.fetchMock.mock.calls[0] ?? []
  expect(init?.method).toBe("POST")
  expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("review-key-1")
  expect(JSON.parse(String(init?.body))).toEqual({
    expected_lock_version: 3,
    decision: "rejected",
    rejection_reason: "需要补充清晰截图",
  })
  expect(view.onReviewed).toHaveBeenCalledTimes(1)
})

it("retains the rejection reason and refreshes detail after a stale 409", async () => {
  const user = userEvent.setup()
  const view = renderPanel({
    response: jsonResponse(
      {
        type: "about:blank",
        title: "Conflict",
        status: 409,
        detail: "任务版本已变化",
        code: "evaluation_stale_version",
        request_id: "review-stale",
      },
      409,
    ),
  })
  await user.click(screen.getByRole("radio", { name: "驳回" }))
  await user.type(screen.getByLabelText("驳回原因"), "保留我的复核意见")
  await user.click(screen.getByRole("button", { name: "提交审核" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "任务状态已变化，请刷新详情并比较后重试。",
  )
  expect(screen.getByLabelText("驳回原因")).toHaveValue("保留我的复核意见")
  expect(view.onRefresh).toHaveBeenCalledTimes(1)
  expect(view.onReviewed).not.toHaveBeenCalled()
})

it("reuses the same idempotency key after an uncertain network failure", async () => {
  const user = userEvent.setup()
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError("network disconnected"))
    .mockResolvedValueOnce(
      jsonResponse({
        api_version: "v2",
        task_id: taskId,
        result_id: resultId,
        result_version: 1,
        task_status: "published",
        task_version: 4,
        review_decision: "approved",
        reviewed_at: "2026-08-08T12:00:00Z",
        published_at: "2026-08-08T12:00:00Z",
      }),
    )
  const keyFactory = vi
    .fn<() => string>()
    .mockReturnValueOnce("review-retry-key-1")
    .mockReturnValueOnce("review-retry-key-2")
  renderPanel({ fetch: fetchMock, idempotencyKeyFactory: keyFactory })

  await user.click(screen.getByRole("radio", { name: "驳回" }))
  await user.type(screen.getByLabelText("驳回原因"), "复用同一审核请求")
  await user.click(screen.getByRole("button", { name: "提交审核" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("审核暂时无法提交，请重试。")

  await user.click(screen.getByRole("button", { name: "提交审核" }))

  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(keyFactory).toHaveBeenCalledTimes(1)
  expect(
    fetchMock.mock.calls.map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    ),
  ).toEqual(["review-retry-key-1", "review-retry-key-1"])
})

it("clears the rejection form and refreshes after private access is lost", async () => {
  const user = userEvent.setup()
  const view = renderPanel({
    width: 360,
    response: jsonResponse(
      {
        type: "about:blank",
        title: "Not Found",
        status: 404,
        detail: "评价资源不存在",
        code: "evaluation_resource_not_found",
      },
      404,
    ),
  })

  await user.click(screen.getByRole("button", { name: "打开全屏审核" }))
  const dialog = screen.getByRole("dialog", { name: "审核评价" })
  await user.click(within(dialog).getByRole("radio", { name: "驳回" }))
  await user.type(
    within(dialog).getByLabelText("驳回原因"),
    "不得在撤权后保留的原因",
  )
  await user.click(within(dialog).getByRole("button", { name: "提交审核" }))

  await vi.waitFor(() => expect(view.onRefresh).toHaveBeenCalledTimes(1))
  expect(screen.queryByRole("dialog", { name: "审核评价" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("驳回原因")).not.toBeInTheDocument()
  expect(screen.queryByText("不得在撤权后保留的原因")).not.toBeInTheDocument()
})

it("shows the server self-review conflict without exposing editable result controls", async () => {
  const user = userEvent.setup()
  renderPanel({
    response: jsonResponse(
      {
        type: "about:blank",
        title: "Conflict",
        status: 409,
        detail: "不能审核自己的评价版本",
        code: "evaluation_review_conflict",
        request_id: "review-self",
      },
      409,
    ),
  })
  await user.click(screen.getByRole("radio", { name: "通过" }))
  await user.click(screen.getByRole("button", { name: "提交审核" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "不能审核自己创建的评价版本。",
  )
  expect(screen.queryByLabelText(/总分|等级|建议/)).not.toBeInTheDocument()
})
