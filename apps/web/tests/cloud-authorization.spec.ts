import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"
import type { AuthMode, AuthState, CloudAuthSessionView } from "../src/operations/cloudAuthorizationApi"

// Fictional responses only. Keep this private artifact policy if real acceptance is added later.
test.use({ trace: "off", screenshot: "off", video: "off" })
test.beforeEach(async ({ page }, info) => { void page; test.skip(!["desktop-1440", "mobile-360", "touch-mobile-390"].includes(info.project.name)) })
const longName = "中文 100% 专用备份目录 " + "安全完整读回目录".repeat(12)
async function mockApi(page: Page) {
  const control = { state: "awaiting_confirmation" as AuthState, mode: "own_app" as AuthMode, denied: false, expired: false, creates: 0, cancels: 0, directoryPolls: 0, revision: 1, targetRevision: 1, account: "owner", sameAccountLogout: false, missingApp: false, imports: 0, operation: "", operationQueries: [] as string[], loseImportResponse: false, holdQr: false, managed: false, reauthorizationRequired: false, releaseQr: () => {} }
  const target = { enabled: false, configured: true, remote_path: "/旧配置/ERP", username: "managed-backup", credential_present: true, revision: 1, connection_status: "untested", last_tested_at: null, last_success_at: null, last_backup_id: null, last_error_code: null }
  const session = (provider: "baidu" | "115"): CloudAuthSessionView => ({ id: `mock-session-${control.creates}`, provider, mode: control.mode, state: control.state, revision: control.revision, expires_at: new Date(Date.now() + 120000).toISOString(), account_label: ["authorized", "configuring", "test_pending", "ready", "applying", "applied"].includes(control.state) ? "虚构用户***" : null, selected_directory: ["test_pending", "ready", "applying", "applied"].includes(control.state) ? `/${longName}` : null, error_code: control.denied ? "cloud_auth_denied" : null, next_poll_after_seconds: 1, ...(control.mode === "openlist_builtin_import" && ["authorized", "configuring", "test_pending", "ready", "applied"].includes(control.state) ? { verified_at: new Date().toISOString(), token_expires_at: provider === "baidu" ? null : new Date(Date.now() + 3600000).toISOString(), expiry_source: provider === "baidu" ? "unreported" as const : "provider" as const } : {}) })
  await page.route("**/api/v1/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify(value) })
    if (path.endsWith("/auth/me")) return json({ account_id: control.account, person_id: "mock-person", full_name: "模拟超管", email: null, force_password_change: false, is_super_admin: true, permissions: ["operations.monitor.read"], scoped_permissions: [] })
    if (path.endsWith("/auth/csrf")) return json({ csrf_token: "fictional-csrf" })
    if (path.endsWith("/auth/logout")) { if (!control.sameAccountLogout) control.account = "different-owner"; return route.fulfill({ status: 204 }) }
    if (path.endsWith("/auth/login")) return json({ user: { account_id: control.account, person_id: "mock-person-2", full_name: "另一模拟超管", email: null, force_password_change: false, is_super_admin: true, permissions: ["operations.monitor.read"] }, csrf_token: "fictional-new-csrf" })
    if (path.endsWith("/portal/context")) return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path.endsWith("/team-management/context")) return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (path.endsWith("/operations/overview")) return json({ generated_at: new Date().toISOString(), window_minutes: 5, health: { score: 100, status: "healthy" }, traffic: { request_count: 0, client_error_count: 0, client_error_details: [], server_error_count: 0, error_rate_percent: 0, requests_per_second: 0, average_latency_ms: 0, p50_latency_ms: 0, p95_latency_ms: 0, p99_latency_ms: 0, max_latency_ms: 0, series: [], top_routes: [] }, database: { status: "healthy", response_time_ms: 1, active_connections: 1, idle_connections: 1, total_connections: 2, max_connections: 100, utilization_percent: 2 }, runtime: { status: "healthy", cpu_percent: 1, memory_used_mb: 1, memory_limit_mb: 1024, memory_percent: 1, process_memory_mb: 1, uptime_seconds: 60 }, background_tasks: { status: "healthy", scheduler_status: "healthy", scheduler_last_seen_at: null, scheduler_lag_seconds: 0, scheduler_consecutive_failures: 0, scheduler_last_result_count: 0, total_tasks: 0, queued_tasks: 0, processing_tasks: 0, pending_review_tasks: 0, failed_tasks: 0, needs_attention_tasks: 0 } })
    if (path.endsWith("/operations/backup-status")) return json({ status: "unknown", completed_at: null, last_success_at: null, started_at: null, verification_at: null, verification_status: "unverified", size_bytes: null, error_code: null, reason: "等待备份状态", secondary_copy: { configured: false, status: "not_configured", last_success_at: null } })
    if (path.endsWith("/operations/history") || path.endsWith("/operations/alerts")) return json({ items: [], total: 0, page: 1, page_size: 20, retention_days: 30 })
    if (path.endsWith("/operations/cloud-backups")) return json({ targets: ["baidu", "115", "quark"].map(provider => ({ ...target, revision: control.targetRevision, provider, ...(control.managed && provider === "baidu" ? { managed: true, authorization_mode: control.mode, authorization_status: control.reauthorizationRequired ? "reauthorization_required" : "valid", selected_directory: `/${longName}`, remote_path: "/", connection_status: "ok" } : {}) })), jobs: [], worker_last_seen_at: null })
    const auth = path.match(/cloud-backups\/(baidu|115)\/authorization\/(.+)$/)
    if (auth) {
      const provider = auth[1] as "baidu" | "115", action = auth[2]
      if (action === "application") return json({ provider, client_id: control.missingApp ? null : "fictional-own-app", secret_present: !control.missingApp && provider === "baidu", revision: 1, configured: !control.missingApp, prerequisites: control.missingApp ? ["cloud_app_unconfigured"] : [] })
      if (action === "sessions") { control.creates++; control.revision = 1; control.mode = request.postDataJSON().mode ?? "own_app"; control.state = control.mode === "openlist_builtin_import" ? "awaiting_import" : "awaiting_confirmation"; control.denied = false; return json(session(provider), 202) }
      if (action.endsWith("/import")) {
        control.imports++; control.operation = request.postDataJSON().operation_id; control.state = "verifying_import"; control.revision++
        if (control.loseImportResponse) return route.abort("failed")
        return json({ operation_id: control.operation, status: "pending", session: session(provider), error_code: null }, 202)
      }
      if (action.includes("/operations/")) {
        const operation_id = action.split("/").at(-1)!
        control.operationQueries.push(operation_id)
        return json({ operation_id, status: control.state === "verifying_import" ? "pending" : "succeeded", session: session(provider), error_code: null }, control.state === "verifying_import" ? 202 : 200)
      }
      if (action.endsWith("/qr")) {
        if (control.holdQr) await new Promise<void>(resolve => { control.releaseQr = resolve })
        return json({ kind: "payload", content: `https://${provider === "baidu" ? "openapi.baidu.com" : "open.115.com"}/fictional-not-a-real-grant`, official_open_url: null, expires_at: new Date(Date.now() + 120000).toISOString() })
      }
      if (action.endsWith("/cancel")) { control.cancels++; control.state = "cancelled"; control.revision++; return json(session(provider)) }
      if (action.endsWith("/directories")) {
        const operation = request.method() === "POST" ? request.postDataJSON().operation_id : new URL(request.url()).searchParams.get("operation_id")
        if (request.method() === "POST") { control.directoryPolls = 0; return json({ operation_id: operation, status: "pending", entries: [], error_code: null }, 202) }
        control.directoryPolls++
        return json({ operation_id: operation, status: "ready", entries: [{ name: longName, path: `/${longName}`, selectable: true }, { name: "只读目录", path: "/只读目录", selectable: false }], error_code: null })
      }
      if (action.endsWith("/select-directory")) { control.state = "test_pending"; control.revision++; return json(session(provider), 202) }
      if (action.endsWith("/test")) { control.state = "ready"; control.revision++; return json(session(provider), 202) }
      if (action.endsWith("/apply")) { control.state = "applied"; control.revision++; return json(session(provider), 202) }
      if (action.startsWith("sessions/")) return json(session(provider))
    }
    // Never pass through to a real API, account or provider.
    return json({ items: [], total: 0 })
  })
  return control
}
async function open(page: Page, provider = "百度网盘") {
  await page.goto("/settings/operations")
  const card = page.getByRole("group", { name: provider, exact: true })
  await card.getByRole("button", { name: "高级选项：自有应用" }).click()
  await card.getByRole("button", { name: "扫码绑定" }).click()
  await expect(page.getByRole("img", { name: `${provider}授权二维码` })).toBeVisible()
  return page.getByRole("dialog")
}
async function assertLayout(page: Page) {
  expect(await page.getByRole("dialog").evaluate(node => node.scrollWidth <= node.clientWidth)).toBe(true)
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}
test("mock authorization: Chinese long directories, keyboard controls and explicit apply stay separate from backup success", async ({ page }) => {
  const control = await mockApi(page)
  const dialog = await open(page)
  await assertLayout(page)
  control.state = "authorized"; control.revision = 2
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  await dialog.getByRole("button", { name: "确认此账号并读取目录" }).click()
  await expect(dialog.getByRole("button", { name: `选择 ${longName}` })).toBeVisible()
  await assertLayout(page)
  await expect(dialog.getByRole("button", { name: "选择 只读目录" })).toBeDisabled()
  await dialog.getByRole("button", { name: `选择 ${longName}` }).click()
  const verify = dialog.getByRole("button", { name: "验证写入与完整读回" })
  await verify.focus(); await page.keyboard.press("Enter")
  const apply = dialog.getByRole("button", { name: "使用此账号与目录" })
  await expect(apply).toBeDisabled()
  await dialog.getByRole("checkbox", { name: /确认替换/ }).check()
  await apply.focus(); await page.keyboard.press("Enter")
  await expect(dialog.getByText(/已应用账号与目录，尚未验证/)).toBeVisible()
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toMatch(/fictional-not-a-real-grant|mock-session|virtual-token/)
  await assertLayout(page)
})
test("mock authorization: denial, expiry, regeneration and cancellation remove the obsolete QR", async ({ page }) => {
  const control = await mockApi(page)
  const dialog = await open(page, "115 网盘")
  control.state = "failed"; control.denied = true
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  await expect(dialog.getByText("用户拒绝授权", { exact: true })).toBeVisible()
  await expect(dialog.getByRole("img")).toHaveCount(0)
  await dialog.getByRole("button", { name: "重新获取二维码" }).click()
  await expect(dialog.getByRole("img")).toBeVisible()
  control.state = "expired"
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  await expect(dialog.getByText("二维码已过期", { exact: true })).toBeVisible()
  await expect(dialog.getByRole("img")).toHaveCount(0)
  await dialog.getByRole("button", { name: "重新获取二维码" }).click()
  await expect(dialog.getByRole("img")).toBeVisible()
  await dialog.getByRole("button", { name: "取消授权" }).click()
  await expect(page.getByRole("dialog")).toHaveCount(0)
  expect(control.cancels).toBe(1)
  await page.reload()
  await expect(page.getByRole("img", { name: /授权二维码/ })).toHaveCount(0)
  expect(control.creates).toBe(3)
})
test("mock authorization: late QR cancellation then logout and account switch leave no authorization material", async ({ page }) => {
  const control = await mockApi(page)
  control.holdQr = true
  await page.goto("/settings/operations")
  await page.getByRole("group", { name: "百度网盘", exact: true }).getByRole("button", { name: "高级选项：自有应用" }).click()
  await page.getByRole("group", { name: "百度网盘", exact: true }).getByRole("button", { name: "扫码绑定" }).click()
  await expect(page.getByRole("dialog")).toBeVisible()
  await expect.poll(() => control.creates).toBe(1)
  await page.getByRole("dialog").getByRole("button", { name: "关闭" }).click()
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await page.getByRole("button", { name: "退出登录", exact: true }).first().click()
  await expect(page).toHaveURL(/\/login/)
  control.releaseQr()
  await page.getByLabel("手机号").fill("13800000000")
  await page.getByLabel("密码", { exact: true }).fill("fictional-password")
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await page.goto("/settings/operations")
  await expect(page.getByRole("group", { name: "百度网盘", exact: true })).toBeVisible()
  await expect(page.getByRole("dialog")).toHaveCount(0)
  await expect(page.getByRole("img", { name: /授权二维码/ })).toHaveCount(0)
  expect(control.creates).toBe(1)
})
test("mock managed target: awaiting real restore acceptance blocks automatic enable but allows explicit copy", async ({ page }) => {
  const control = await mockApi(page)
  control.managed = true
  await page.goto("/settings/operations")
  const card = page.getByRole("group", { name: "百度网盘", exact: true })
  await expect(card.getByText(/待真实网盘.*隔离恢复验收/)).toBeVisible()
  await expect(card.getByRole("button", { name: "启用自动复制" })).toBeDisabled()
  await expect(card.getByLabel("远端逻辑路径")).toHaveValue(`/${longName}`)
  await expect(card.getByLabel("远端逻辑路径")).toBeDisabled()
  await expect(card.getByRole("button", { name: "手动复制完整备份用于验收" })).toBeEnabled()
  control.reauthorizationRequired = true
  await page.getByRole("button", { name: "刷新云备份状态" }).click()
  await expect(card.getByRole("button", { name: "手动复制完整备份用于验收" })).toBeDisabled()
  await expect(card.getByText(/授权已失效.*本地备份/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})
test("mock builtin import: consent, mobile paste, tab return, target refresh and confirmed acceptance", async ({ page }) => {
  const control = await mockApi(page)
  control.missingApp = true
  await page.goto("/settings/operations")
  await page.getByRole("group", { name: "百度网盘", exact: true }).getByRole("button", { name: "免填应用信息绑定" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByText("等待粘贴授权令牌", { exact: true })).toBeVisible()
  await expect(dialog.getByRole("button", { name: "打开授权页面" })).toBeDisabled()
  await dialog.getByRole("checkbox", { name: /已阅读/ }).check()
  const link = dialog.getByRole("link", { name: "打开授权页面" })
  await expect(link).toHaveAttribute("href", "https://api.oplist.org/")
  await expect(link).toHaveAttribute("rel", "noopener noreferrer")
  const access = dialog.getByLabel("访问令牌（Access Token）")
  const refresh = dialog.getByLabel("刷新令牌（Refresh Token）")
  await access.fill("synthetic-access"); await refresh.fill("synthetic-refresh")
  const node = await access.elementHandle()
  const externalTab = await page.context().newPage()
  await externalTab.goto("about:blank")
  await externalTab.bringToFront()
  await page.bringToFront()
  await externalTab.close()
  await page.evaluate(() => { window.dispatchEvent(new Event("blur")); window.dispatchEvent(new Event("focus")); document.dispatchEvent(new Event("visibilitychange")) })
  control.targetRevision++
  // The panel's background refetch is deliberately triggered while the modal stays mounted.
  await page.getByRole("button", { name: "刷新云备份状态" }).evaluate((button: HTMLButtonElement) => button.click())
  await expect(access).toHaveValue("synthetic-access")
  expect(await node!.evaluate(input => input.isConnected)).toBe(true)
  expect(await access.evaluate((input, original) => input === original, node)).toBe(true)
  await assertLayout(page)
  await dialog.getByRole("button", { name: "验证授权" }).click()
  await expect(access).toHaveValue("")
  await expect(refresh).toHaveValue("")
  control.state = "authorized"; control.revision++
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  await expect(dialog.getByText("授权已验证，尚未启用备份", { exact: true })).toBeVisible()
  await expect(dialog.getByText("令牌到期时间未知", { exact: true })).toBeVisible()
  expect(control.imports).toBe(1)
  await dialog.getByRole("button", { name: "确认此账号并读取目录" }).click()
  await dialog.getByRole("button", { name: `选择 ${longName}` }).click()
  await dialog.getByRole("button", { name: "验证写入与完整读回" }).click()
  await dialog.getByRole("checkbox", { name: /确认替换/ }).check()
  await dialog.getByRole("button", { name: "使用此账号与目录" }).click()
  await expect(dialog.getByText(/已应用账号与目录，尚未验证/)).toBeVisible()
  expect(await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }))).not.toMatch(/synthetic-access|synthetic-refresh/)
  await assertLayout(page)
})
test("mock builtin import: lost response queries original UUID and same-account logout destroys pasted tokens", async ({ page }) => {
  const control = await mockApi(page)
  control.missingApp = true; control.loseImportResponse = true; control.sameAccountLogout = true
  await page.goto("/settings/operations")
  await page.getByRole("group", { name: "115 网盘", exact: true }).getByRole("button", { name: "免填应用信息绑定" }).click()
  const dialog = page.getByRole("dialog")
  await expect(dialog.getByText("等待粘贴授权令牌", { exact: true })).toBeVisible()
  await dialog.getByRole("checkbox", { name: /已阅读/ }).check()
  await dialog.getByLabel("访问令牌（Access Token）").fill("synthetic-access")
  await dialog.getByLabel("刷新令牌（Refresh Token）").fill("synthetic-refresh")
  await dialog.getByRole("button", { name: "验证授权" }).click()
  await expect.poll(() => control.operationQueries.length).toBeGreaterThan(0)
  await expect(dialog.getByLabel("访问令牌（Access Token）")).toHaveValue("")
  await page.evaluate(() => window.dispatchEvent(new Event("focus")))
  expect(control.imports).toBe(1)
  expect(control.operationQueries.every(id => id === control.operation)).toBe(true)
  await dialog.getByRole("button", { name: "关闭" }).click()
  await expect(dialog).toHaveCount(0)
  await page.getByRole("group", { name: "115 网盘", exact: true }).getByRole("button", { name: "免填应用信息绑定" }).click()
  await expect(dialog.getByText("等待粘贴授权令牌", { exact: true })).toBeVisible()
  await dialog.getByLabel("访问令牌（Access Token）").fill("synthetic-unsubmitted")
  const oldNode = await dialog.getByLabel("访问令牌（Access Token）").elementHandle()
  await page.getByRole("button", { name: "退出登录", exact: true }).first().evaluate((button: HTMLButtonElement) => button.click())
  await expect(page).toHaveURL(/\/login/)
  await page.getByLabel("手机号").fill("13800000000")
  await page.getByLabel("密码", { exact: true }).fill("fictional-password")
  await page.getByRole("button", { name: "登录", exact: true }).click()
  await page.goto("/settings/operations")
  await expect(page.getByRole("dialog")).toHaveCount(0)
  expect(await oldNode!.evaluate(input => input.isConnected).catch(() => false)).toBe(false)
  await page.getByRole("group", { name: "115 网盘", exact: true }).getByRole("button", { name: "免填应用信息绑定" }).click()
  await expect(dialog.getByLabel("访问令牌（Access Token）")).toHaveValue("")
  await expect(dialog.getByLabel("刷新令牌（Refresh Token）")).toHaveValue("")
  await assertLayout(page)
})
