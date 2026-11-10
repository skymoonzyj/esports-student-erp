import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"

// Only fictional API responses are allowed here. Never let an unhandled API request
// reach a configured backend, and do not retain traces of account/IP fixtures.
test.use({ trace: "off", screenshot: "off", video: "off" })

const accountId = "11111111-2222-4333-8444-555555555555"
const anonymousId = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
const error = {
  id: "00000000-1111-4222-8333-444444444444",
  occurred_at: "2026-10-25T04:05:06Z",
  account_id: accountId,
  account_name: "模拟值班老师",
  source_ip: "198.51.100.24",
  request_id: "synthetic-request-1",
  method: "PATCH",
  route: "/api/v1/housing/rooms/{room_id}",
  status_code: 403,
  reason: "无权执行此操作",
}
const anonymousError = {
  ...error,
  id: anonymousId,
  account_id: null,
  account_name: null,
  source_ip: null,
  request_id: null,
  method: "POST",
  route: "/api/v1/auth/login",
  status_code: 401,
  reason: "身份验证失败",
}

type RequestControl = { denied: boolean; queries: URLSearchParams[] }

async function mockApi(page: Page): Promise<RequestControl> {
  const control: RequestControl = { denied: false, queries: [] }
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const json = (value: unknown, status = 200) => route.fulfill({
      status,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify(value),
    })
    if (path.endsWith("/auth/me")) return json({ account_id: accountId, person_id: "synthetic-person", full_name: "模拟超管", email: null, force_password_change: false, is_super_admin: true, permissions: ["operations.monitor.read"], scoped_permissions: [] })
    if (path.endsWith("/auth/csrf")) return json({ csrf_token: "synthetic-csrf" })
    if (path.endsWith("/portal/context")) return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path.endsWith("/team-management/context")) return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (path.endsWith("/operations/overview")) return json({ generated_at: "2026-10-25T04:06:00Z", window_minutes: 60, health: { score: 100, status: "healthy" }, traffic: { request_count: 0, client_error_count: 0, client_error_details: [], server_error_count: 0, error_rate_percent: 0, requests_per_second: 0, average_latency_ms: 0, p50_latency_ms: 0, p95_latency_ms: 0, p99_latency_ms: 0, max_latency_ms: 0, series: [], top_routes: [] }, database: { status: "healthy", response_time_ms: 1, active_connections: 1, idle_connections: 1, total_connections: 2, max_connections: 100, utilization_percent: 2 }, runtime: { status: "healthy", cpu_percent: 1, memory_used_mb: 1, memory_limit_mb: 1024, memory_percent: 1, process_memory_mb: 1, uptime_seconds: 60 }, background_tasks: { status: "healthy", scheduler_status: "healthy", scheduler_last_seen_at: null, scheduler_lag_seconds: 0, scheduler_consecutive_failures: 0, scheduler_last_result_count: 0, total_tasks: 0, queued_tasks: 0, processing_tasks: 0, pending_review_tasks: 0, failed_tasks: 0, needs_attention_tasks: 0 } })
    if (path.endsWith("/operations/backup-status")) return json({ status: "unknown", completed_at: null, last_success_at: null, started_at: null, verification_at: null, verification_status: "unverified", size_bytes: null, error_code: null, reason: "等待备份状态", secondary_copy: { configured: false, status: "not_configured", last_success_at: null } })
    if (path.endsWith("/operations/history") || path.endsWith("/operations/alerts")) return json({ items: [], total: 0, page: 1, page_size: 20, retention_days: 30 })
    if (path.endsWith("/operations/cloud-backups")) return json({ targets: [], jobs: [], worker_last_seen_at: null })
    if (path.endsWith("/operations/request-errors")) {
      control.queries.push(new URLSearchParams(url.search))
      if (control.denied) return json({ type: "about:blank", title: "Forbidden", status: 403, detail: "无权查看请求错误明细", code: "forbidden", request_id: "synthetic-denial" }, 403)
      const pageNumber = Number(url.searchParams.get("page") ?? "1")
      const filtered = Boolean(url.searchParams.get("account_id") || url.searchParams.get("source_ip") || url.searchParams.get("status_code"))
      return json({ items: pageNumber === 2 ? [{ ...error, id: "00000000-1111-4222-8333-999999999999", request_id: "synthetic-request-2" }] : filtered ? [error] : [error, anonymousError], total: filtered ? 21 : 2, page: pageNumber, page_size: 20, retention_days: 30 })
    }
    // A missing fixture is a failed test, never a passthrough to a real API.
    return json({ type: "about:blank", title: "Unexpected synthetic route", status: 599, detail: `Unmocked API path: ${path}`, code: "unmocked_test_route", request_id: "synthetic" }, 599)
  })
  return control
}

async function openPanel(page: Page) {
  await page.goto("/settings/operations")
  const panel = page.getByRole("region", { name: "请求错误明细" })
  await expect(panel).toBeVisible()
  return panel
}

test("synthetic errors identify an authenticated account and an unattributed login failure without page overflow", async ({ page }) => {
  const control = await mockApi(page)
  const panel = await openPanel(page)
  await expect.poll(() => control.queries.length).toBeGreaterThan(0)
  expect(control.queries[0].get("days")).toBe("1")
  expect(control.queries[0].get("page")).toBe("1")
  expect(control.queries[0].get("page_size")).toBe("20")
  const attributed = panel.getByRole("article").filter({ hasText: accountId })
  await expect(attributed.getByText("模拟值班老师")).toBeVisible()
  await expect(attributed.getByText(accountId)).toBeVisible()
  await expect(attributed.getByText("198.51.100.24")).toBeVisible()
  await expect(attributed.getByText("/api/v1/housing/rooms/{room_id}")).toBeVisible()
  await expect(attributed.getByText("PATCH")).toBeVisible()
  await expect(attributed.getByText("403")).toBeVisible()
  await expect(attributed.getByText("无权执行此操作")).toBeVisible()
  await expect(attributed.getByText("synthetic-request-1")).toBeVisible()
  const unattributed = panel.getByRole("article").filter({ hasText: "身份验证失败" })
  await expect(unattributed.getByText("未登录／无法确认账号")).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test("synthetic filters reset paging and issue exact query parameters", async ({ page }) => {
  const control = await mockApi(page)
  const panel = await openPanel(page)
  await panel.getByLabel("错误范围").selectOption("7")
  await panel.getByLabel("账号 UUID").fill(accountId)
  await panel.getByLabel("来源 IP").fill("198.51.100.24")
  await panel.getByLabel("HTTP 状态码").fill("403")
  await panel.getByRole("button", { name: "查询错误" }).click()
  await expect.poll(() => control.queries.some(params => params.get("account_id") === accountId && params.get("source_ip") === "198.51.100.24" && params.get("status_code") === "403" && params.get("days") === "7" && params.get("page") === "1")).toBe(true)
  await panel.getByRole("button", { name: "下一页" }).click()
  await expect.poll(() => control.queries.some(params => params.get("page") === "2" && params.get("days") === "7" && params.get("account_id") === accountId)).toBe(true)
  await expect(panel.getByText("synthetic-request-2")).toBeVisible()
  await panel.getByRole("button", { name: "重置筛选" }).click()
  await expect(panel.getByText("synthetic-request-2")).toHaveCount(0)
  await expect(panel.getByText("synthetic-request-1")).toBeVisible()
  await expect(panel.getByText("共 2 条 · 第 1 / 1 页")).toBeVisible()
  await expect(panel.getByLabel("错误范围")).toHaveValue("1")
  await expect(panel.getByLabel("账号 UUID")).toHaveValue("")
  await expect(panel.getByLabel("来源 IP")).toHaveValue("")
  await expect(panel.getByLabel("HTTP 状态码")).toHaveValue("")
})

test("a 403 on a new request removes details from the previous authorized result", async ({ page }) => {
  const control = await mockApi(page)
  const panel = await openPanel(page)
  await expect(panel.getByText(accountId)).toBeVisible()
  control.denied = true
  await panel.getByLabel("错误范围").selectOption("7")
  await panel.getByRole("button", { name: "查询错误" }).click()
  await expect.poll(() => control.queries.some(params => params.get("days") === "7")).toBe(true)
  await expect(panel.getByText(accountId)).toHaveCount(0)
  await expect(panel.getByText("198.51.100.24")).toHaveCount(0)
  await expect(panel.getByRole("alert")).toBeVisible()
})
