import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import * as operationsApi from "./api"
import type { OperationsOverview } from "./api"
import { OperationsMonitorPage } from "./OperationsMonitorPage"

beforeEach(() => {
  vi.spyOn(operationsApi, "getOperationsBackupStatus").mockResolvedValue({ status: "unknown", completed_at: null, last_success_at: null, started_at: null, verification_at: null, verification_status: "unverified", size_bytes: null, error_code: null, reason: "等待备份状态", secondary_copy: { configured: false, status: "not_configured", last_success_at: null } })
  vi.spyOn(operationsApi, "getOperationsHistory").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, retention_days: 30 })
  vi.spyOn(operationsApi, "getOperationsAlerts").mockResolvedValue({ items: [], total: 0, page: 1, page_size: 10 })
})

const sampleOverview: OperationsOverview = {
  generated_at: "2026-09-01T08:00:00Z",
  window_minutes: 60,
  health: { score: 96, status: "healthy" },
  traffic: {
    request_count: 273,
    client_error_count: 4,
    client_error_details: [
      { status_code: 404, count: 3, reason: "请求资源不存在" },
      { status_code: 401, count: 1, reason: "未登录或登录已失效" },
    ],
    server_error_count: 2,
    error_rate_percent: 0.73,
    requests_per_second: 0.076,
    average_latency_ms: 38.2,
    p50_latency_ms: 21,
    p95_latency_ms: 90,
    p99_latency_ms: 180,
    max_latency_ms: 260,
    series: Array.from({ length: 24 }, (_, index) => ({
      starts_at: `2026-09-01T${String(index).padStart(2, "0")}:00:00Z`,
      request_count: index + 1,
      error_count: index === 20 ? 1 : 0,
      average_latency_ms: 30 + index,
    })),
    top_routes: [
      {
        method: "GET",
        route: "/api/v1/people",
        request_count: 82,
        error_count: 0,
        average_latency_ms: 24,
      },
    ],
  },
  database: {
    status: "healthy",
    response_time_ms: 4.2,
    active_connections: 3,
    idle_connections: 4,
    total_connections: 7,
    max_connections: 100,
    utilization_percent: 7,
  },
  runtime: {
    status: "healthy",
    cpu_percent: 15.7,
    memory_used_mb: 451,
    memory_limit_mb: 4096,
    memory_percent: 11,
    process_memory_mb: 220,
    uptime_seconds: 90_000,
  },
  background_tasks: {
    status: "healthy",
    scheduler_status: "healthy",
    scheduler_last_seen_at: "2026-09-01T07:59:50Z",
    scheduler_lag_seconds: 10,
    scheduler_consecutive_failures: 0,
    scheduler_last_result_count: 1,
    total_tasks: 12,
    queued_tasks: 3,
    processing_tasks: 2,
    pending_review_tasks: 4,
    failed_tasks: 0,
    needs_attention_tasks: 0,
  },
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <OperationsMonitorPage />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

it("renders operational metrics and refreshes on demand", async () => {
  const request = vi.spyOn(operationsApi, "getOperationsOverview").mockResolvedValue(sampleOverview)
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByRole("heading", { name: "运维监控" })).toBeVisible()
  expect(screen.getByLabelText("服务健康评分 96 分")).toBeVisible()
  expect(screen.getByText("273")).toBeVisible()
  expect(screen.getByText("请求资源不存在")).toBeVisible()
  expect(screen.getByText("3 次")).toBeVisible()
  expect(screen.getByText("401")).toBeVisible()
  expect(screen.getByText("/api/v1/people")).toBeVisible()
  expect(screen.getByText("7", { selector: ".operationsServicePanel > strong" })).toBeVisible()
  expect(screen.getByLabelText("请求数刻度：0 到 24 次")).toHaveTextContent("24")
  expect(screen.getByLabelText("请求数刻度：0 到 24 次")).toHaveTextContent("12")
  expect(screen.getByLabelText("时间刻度").children).toHaveLength(3)

  await user.click(screen.getByRole("button", { name: "立即刷新运维数据" }))
  await waitFor(() => expect(request).toHaveBeenCalledTimes(2))
})

it("switches the monitoring window and supports pausing auto refresh", async () => {
  const request = vi.spyOn(operationsApi, "getOperationsOverview").mockResolvedValue(sampleOverview)
  const user = userEvent.setup()
  renderPage()

  await screen.findByRole("heading", { name: "运维监控" })
  await user.selectOptions(screen.getByLabelText("时间范围"), "5")
  await waitFor(() => expect(request).toHaveBeenCalledWith(5))
  const toggle = screen.getByRole("checkbox", { name: "自动刷新" })
  expect(toggle).toBeChecked()
  await user.click(toggle)
  expect(toggle).not.toBeChecked()
})

it("shows a retryable error state", async () => {
  vi.spyOn(operationsApi, "getOperationsOverview").mockRejectedValue(
    new Error("监控服务暂时不可用"),
  )
  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent("监控服务暂时不可用")
  expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
})
