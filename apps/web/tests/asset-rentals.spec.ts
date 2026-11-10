import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"
import type { Asset } from "../src/assets/api"

// Entirely synthetic acceptance: install before navigation, never forward API
// traffic, and fail the test for every missing fixture. No backend or seed needed.
test.use({ trace: "off", screenshot: "off", video: "off" })
const accountId = "11111111-2222-4333-8444-555555555555"
const ids = ["aaaaaaaa-1111-4111-8111-111111111111", "aaaaaaaa-2222-4222-8222-222222222222"]
const unexpected = new Map<Page, string[]>()
test.afterEach(async ({ page }) => {
  expect(unexpected.get(page) ?? [], "Every API request must use a synthetic fixture").toEqual([])
  unexpected.delete(page)
})

function asset(index: number): Asset {
  return { id: ids[index], asset_code: `SYN-RENT-${index + 1}`, name: `模拟租赁设备${index + 1}`, category: "模拟设备", brand: null, model: null, serial_number: null, status: "available", location: "模拟设备室", purchase_date: null, warranty_expires_on: null, source_purchase_id: null, note: null, created_at: "2026-10-25T00:00:00Z", updated_at: "2026-10-25T00:00:00Z", current_assignment: null, photo: null, ownership_type: "rented", rental_started_on: "2026-01-01", rental_expires_on: index ? "2026-10-24" : "2026-10-31", rental_returned_on: null, rental_revision: index + 2 }
}
type Renewal = { operation_key: string; period: string; assets: { asset_id: string; expected_rental_revision: number }[] }
type AssetListFilter = { query: string | null; status: string | null; category: string | null; ownership_type: string | null; page: number; page_size: number }
async function mockApi(page: Page, permissions = ["assets.read", "assets.manage"], theme: "clear" | "command" = "clear") {
  const control = { items: [asset(0), asset(1)], creates: [] as Record<string, unknown>[], updates: [] as Record<string, unknown>[], exports: [] as Record<string, unknown>[], listCategories: [] as (string | null)[], listFilters: [] as AssetListFilter[], renewals: [] as Renewal[], listReads: 0, reminderReads: 0, stale: false, denied: false }
  unexpected.set(page, [])
  await page.route("**/api/v1/**", async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace("/api/v1", "")
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify(value) })
    if (path === "/appearance") return json({ theme, revision: 1 })
    if (path === "/auth/me") return json({ account_id: accountId, person_id: "bbbbbbbb-1111-4111-8111-111111111111", full_name: "模拟资产老师", email: null, force_password_change: false, is_super_admin: false, permissions, scoped_permissions: [] })
    if (path === "/auth/csrf") return json({ csrf_token: "synthetic-csrf" })
    if (path === "/portal/context") return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path === "/team-management/context") return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (path === "/personnel-intake/context") return json({ can_submit: false, can_review: false, can_direct_create: false, can_read_managed: false, can_direct_create_pending: false, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] })
    if (path === "/portal/departure-reminders") return json({ available: false, items: [], total: 0, page: 1, page_size: 20 })
    if (path === "/portal/rental-reminders") { control.reminderReads++; return json({ available: !control.denied && permissions.includes("assets.read"), can_manage: !control.denied && permissions.includes("assets.manage"), observed_at: "2026-10-25T00:00:00Z", business_date: "2026-10-25", next_refresh_at: "2099-01-01T00:00:00Z", items: control.denied || !permissions.includes("assets.read") ? [] : control.items.map((item, index) => ({ asset_id: item.id, asset_code: item.asset_code, name: item.name, location: item.location, person_name: index ? "模拟使用人" : null, rental_expires_on: item.rental_expires_on, days_remaining: index ? -1 : 6, rental_revision: item.rental_revision })), total: control.denied || !permissions.includes("assets.read") ? 0 : 2, page: 1, page_size: 20 }) }
    if (path === "/assets/categories" && request.method() === "GET") return json({ groups: [{ key: "computers", label: "电脑与电竞设备", options: ["台式电脑", "显示器"] }], legacy_categories: permissions.includes("assets.read") ? ["模拟设备", "旧训练器材"] : [] })
    if (path === "/assets" && request.method() === "GET") {
      const params = new URL(request.url()).searchParams
      const filters: AssetListFilter = { query: params.get("query"), status: params.get("status"), category: params.get("category"), ownership_type: params.get("ownership_type"), page: Number(params.get("page") ?? 1), page_size: Number(params.get("page_size") ?? 50) }
      control.listReads++; control.listCategories.push(filters.category); control.listFilters.push(filters)
      const items = control.items.filter(item => (!filters.category || item.category === filters.category)
        && (!filters.status || item.status === filters.status)
        && (!filters.query || [item.name, item.asset_code, item.serial_number].some(value => value?.includes(filters.query!)))
        && (!filters.ownership_type || (filters.ownership_type === "unconfirmed" ? item.ownership_type == null : item.ownership_type === filters.ownership_type)))
      const start = (filters.page - 1) * filters.page_size
      return json({ items: items.slice(start, start + filters.page_size), total: items.length, page: filters.page, page_size: filters.page_size })
    }
    if (path === "/assets" && request.method() === "POST") { const input = request.postDataJSON(); control.creates.push(input); return json({ ...asset(0), ...input }, 201) }
    if (path === "/assets/exports" && request.method() === "POST") { control.exports.push(request.postDataJSON()); return route.fulfill({ status: 200, contentType: "text/csv", headers: { "Content-Disposition": 'attachment; filename="synthetic-assets.csv"' }, body: "name,category\nsynthetic,legacy\n" }) }
    if (path === `/assets/${ids[0]}` && request.method() === "PATCH") { const input = request.postDataJSON(); control.updates.push(input); control.items[0] = { ...control.items[0], ...input }; return json(control.items[0]) }
    if (path === "/assets/rentals/renew" && request.method() === "POST") {
      const input = request.postDataJSON() as Renewal
      control.renewals.push(input)
      if (control.stale) { control.items[0] = { ...control.items[0], rental_expires_on: "2026-11-07", rental_revision: 8 }; return json({ type: "about:blank", title: "Conflict", status: 409, detail: "模拟租期已更新，请重新选择资产", code: "conflict", request_id: "synthetic-conflict" }, 409) }
      // Independently hand-derived per-asset responses, never production date helpers.
      const results = [
        { asset_id: ids[0], period: "month", expected_rental_revision: 2, previous_expires_on: "2026-10-31", rental_expires_on: "2026-11-30", rental_revision: 3 },
        { asset_id: ids[0], period: "week", expected_rental_revision: 3, previous_expires_on: "2026-11-30", rental_expires_on: "2026-12-07", rental_revision: 4 },
        { asset_id: ids[1], period: "week", expected_rental_revision: 3, previous_expires_on: "2026-10-24", rental_expires_on: "2026-10-31", rental_revision: 4 },
      ]
      const items = input.assets.map(selected => {
        const item = control.items.find(value => value.id === selected.asset_id)!
        const result = results.find(value => value.asset_id === selected.asset_id && value.period === input.period && value.expected_rental_revision === selected.expected_rental_revision)
        expect(result, "renewal request must match an explicit per-ID fixture").toBeDefined()
        expect(item.rental_expires_on).toBe(result!.previous_expires_on)
        item.rental_expires_on = result!.rental_expires_on
        item.rental_revision = result!.rental_revision
        return { asset_id: item.id, previous_expires_on: result!.previous_expires_on, rental_expires_on: result!.rental_expires_on, rental_revision: result!.rental_revision }
      })
      return json({ operation_key: input.operation_key, period: input.period, replayed: false, items })
    }
    if (/^\/assets\/[^/]+\/rental-renewals$/.test(path)) return json({ items: [], total: 0, page: 1, page_size: 20 })
    unexpected.get(page)?.push(`${request.method()} ${path}`)
    return json({ detail: `Unmocked synthetic API: ${path}`, code: "unmocked_test_route" }, 599)
  })
  return control
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
}
async function inventory(page: Page) {
  await page.goto("/assets")
  await expect(page.getByRole("heading", { name: "资产管理", exact: true })).toBeVisible()
  await expect(page.getByText("模拟租赁设备1", { exact: true })).toBeVisible()
}

function statisticFilterAssets(): Asset[] {
  // A real 50-row response boundary makes page 2 meaningful without lying about page_size.
  const rentals = Array.from({ length: 52 }, (_, index): Asset => ({
    ...asset(0),
    id: index < 2 ? ids[index] : `a0000000-aaaa-4aaa-8aaa-${String(index + 1).padStart(12, "0")}`,
    asset_code: `SYN-RENT-${index + 1}`,
    name: `模拟租赁设备${index + 1}`,
    status: index === 50 ? "maintenance" : "available",
  }))
  const available: Asset = { ...asset(0), id: "a0000000-aaaa-4aaa-8aaa-000000000101", asset_code: "SYN-TARGET-AVAILABLE", name: "模拟目标可用", category: "显示器", ownership_type: "owned", acquisition_method: "other", acquisition_note: "模拟捐赠", rental_started_on: null, rental_expires_on: null, rental_returned_on: null, rental_revision: 0 }
  const assigned: Asset = { ...available, id: "a0000000-aaaa-4aaa-8aaa-000000000102", asset_code: "SYN-TARGET-ASSIGNED", name: "模拟目标使用中", status: "assigned", current_assignment: { id: "a0000000-aaaa-4aaa-8aaa-000000000103", asset_id: "a0000000-aaaa-4aaa-8aaa-000000000102", person_id: "bbbbbbbb-1111-4111-8111-111111111111", person_name: "模拟使用人", assigned_at: "2026-10-25T00:00:00Z", expected_return_at: null, returned_at: null, condition_on_assign: null, condition_on_return: null, note: null } }
  return [...rentals, available, assigned]
}

for (const theme of ["clear", "command"] as const) {
  test(`${theme}: statistic cards preserve current filters, reset paging, support keyboard and export applied status`, async ({ page }, testInfo) => {
    const control = await mockApi(page, ["assets.read", "assets.manage", "assets.export"], theme)
    control.items = statisticFilterAssets()
    await inventory(page)
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
    const stats = page.getByRole("region", { name: "资产数量统计", exact: true })
    const all = stats.getByRole("button", { name: "资产总数", exact: true })
    const available = stats.getByRole("button", { name: "当前可用", exact: true })
    const assigned = stats.getByRole("button", { name: "使用中", exact: true })
    const query = page.getByRole("textbox", { name: "搜索资产", exact: true })
    const category = page.getByRole("combobox", { name: "分类", exact: true })
    const ownership = page.getByRole("combobox", { name: "归属筛选", exact: true })
    const status = page.getByRole("combobox", { name: "状态", exact: true })
    await expect(all).toHaveAttribute("aria-pressed", "true")
    await expect(available).toContainText("当前页数量")
    await expect(assigned).toContainText("当前页数量")

    await query.fill("模拟租赁")
    await category.selectOption("模拟设备")
    await ownership.selectOption("rented")
    await page.getByRole("button", { name: "查询", exact: true }).click()
    await expect.poll(() => control.listFilters.at(-1)).toEqual({ query: "模拟租赁", status: null, category: "模拟设备", ownership_type: "rented", page: 1, page_size: 50 })
    await page.getByRole("button", { name: "下一页资产", exact: true }).click()
    await expect.poll(() => control.listFilters.at(-1)?.page).toBe(2)
    await expect(page.locator(".assetTableRow")).toHaveCount(2)
    await expect(assigned.getByText("0", { exact: true })).toBeVisible()
    await noOverflow(page)

    // These are current UI drafts, deliberately different from the applied page-2 filters.
    await query.fill("  模拟目标  ")
    await category.selectOption("显示器")
    await ownership.selectOption("owned")
    await assigned.focus()
    await expect(assigned).toBeFocused()
    await assigned.press("Enter")
    await expect.poll(() => control.listFilters.at(-1)).toEqual({ query: "模拟目标", status: "assigned", category: "显示器", ownership_type: "owned", page: 1, page_size: 50 })
    await expect(status).toHaveValue("assigned")
    await expect(assigned).toHaveAttribute("aria-pressed", "true")
    await expect(all).toHaveAttribute("aria-pressed", "false")
    await expect(page.getByText("模拟目标使用中", { exact: true })).toBeVisible()
    await expect(available.getByText("0", { exact: true })).toBeVisible()
    await expect(available).toBeEnabled()

    await available.focus()
    await expect(available).toBeFocused()
    await available.press("Space")
    await expect.poll(() => control.listFilters.at(-1)).toEqual({ query: "模拟目标", status: "available", category: "显示器", ownership_type: "owned", page: 1, page_size: 50 })
    await expect(status).toHaveValue("available")
    await expect(available).toHaveAttribute("aria-pressed", "true")
    await expect(assigned).toHaveAttribute("aria-pressed", "false")
    await expect(category).toHaveValue("显示器")
    await expect(ownership).toHaveValue("owned")
    await expect(page.getByText("模拟目标可用", { exact: true })).toBeVisible()
    await noOverflow(page)
    await page.screenshot({ path: testInfo.outputPath(`asset-stat-${theme}.png`), fullPage: true })

    // Unapplied dropdown edits must not change the pressed card or the exported applied filters.
    await status.selectOption("maintenance")
    await expect(available).toHaveAttribute("aria-pressed", "true")
    await page.getByRole("button", { name: "导出", exact: true }).click()
    const dialog = page.getByRole("dialog", { name: "导出资产台账", exact: true })
    await dialog.getByLabel("导出用途").fill("模拟统计筛选验收")
    await dialog.getByLabel("文件格式").selectOption("csv")
    await noOverflow(page)
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      dialog.getByRole("button", { name: "开始导出", exact: true }).click(),
    ])
    expect(download.suggestedFilename()).toBe("synthetic-assets.csv")
    await expect.poll(() => control.exports.length).toBe(1)
    expect(control.exports[0]).toMatchObject({ query: "模拟目标", category: "显示器", ownership_type: "owned", status: "available", format: "csv", purpose: "模拟统计筛选验收" })
    await expect(dialog).toHaveCount(0)

    await all.click()
    await expect.poll(() => control.listFilters.at(-1)).toEqual({ query: "模拟目标", status: null, category: "显示器", ownership_type: "owned", page: 1, page_size: 50 })
    await expect(status).toHaveValue("")
    await expect(all).toHaveAttribute("aria-pressed", "true")
    await expect(available).toHaveAttribute("aria-pressed", "false")
    await expect(assigned).toHaveAttribute("aria-pressed", "false")
    await expect(page.locator(".assetTableRow")).toHaveCount(2)
    await noOverflow(page)
    expect(control.creates).toEqual([])
    expect(control.updates).toEqual([])
    expect(control.renewals).toEqual([])
  })

  test(`${theme}: read-only statistic cards remain usable at zero without management controls or mobile overflow`, async ({ page }) => {
    const control = await mockApi(page, ["assets.read"], theme)
    control.items = [statisticFilterAssets().at(-1)!, { ...asset(1), status: "retired" }]
    await page.goto("/assets")
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
    await expect(page.getByText("模拟目标使用中", { exact: true })).toBeVisible()
    await expect(page.getByText("模拟租赁设备2", { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: /^(新增资产|批量新增租用资产|编辑|分配|归还|续期|退租|批量续期（\d+）|导出)$/ })).toHaveCount(0)
    await expect(page.getByRole("checkbox", { name: /选择续期/ })).toHaveCount(0)
    const stats = page.getByRole("region", { name: "资产数量统计", exact: true })
    const available = stats.getByRole("button", { name: "当前可用", exact: true })
    const assigned = stats.getByRole("button", { name: "使用中", exact: true })
    const all = stats.getByRole("button", { name: "资产总数", exact: true })
    await expect(available.getByText("0", { exact: true })).toBeVisible()
    await expect(available).toBeEnabled()
    await available.click()
    await expect.poll(() => control.listFilters.at(-1)).toEqual({ query: null, status: "available", category: null, ownership_type: null, page: 1, page_size: 50 })
    await expect(available).toHaveAttribute("aria-pressed", "true")
    await expect(page.getByRole("combobox", { name: "状态", exact: true })).toHaveValue("available")
    await expect(page.locator(".assetTableRow")).toHaveCount(0)
    await expect(assigned.getByText("0", { exact: true })).toBeVisible()
    await expect(assigned).toBeEnabled()
    await noOverflow(page)

    await assigned.press("Enter")
    await expect(page.getByText("模拟目标使用中", { exact: true })).toBeVisible()
    await expect(assigned).toHaveAttribute("aria-pressed", "true")
    await expect(page.getByRole("combobox", { name: "状态", exact: true })).toHaveValue("assigned")
    await expect(page.locator(".assetTableRow")).toHaveCount(1)
    await expect(page.getByText("模拟租赁设备2", { exact: true })).toHaveCount(0)
    await all.click()
    // The original all-status query can still be fresh in the 30-second app cache.
    // Assert the restored applied UI and both status rows, not a redundant network request.
    await expect(all).toHaveAttribute("aria-pressed", "true")
    await expect(available).toHaveAttribute("aria-pressed", "false")
    await expect(assigned).toHaveAttribute("aria-pressed", "false")
    await expect(page.getByRole("combobox", { name: "状态", exact: true })).toHaveValue("")
    await expect(page.locator(".assetTableRow")).toHaveCount(2)
    await expect(page.getByText("模拟目标使用中", { exact: true })).toBeVisible()
    await expect(page.getByText("模拟租赁设备2", { exact: true })).toBeVisible()
    await noOverflow(page)
    expect(control.creates).toEqual([])
    expect(control.updates).toEqual([])
    expect(control.renewals).toEqual([])
    expect(control.exports).toEqual([])
  })
}

test("new ownership form requires choice and preserves distinct owned acquisition and rented dates", async ({ page }) => {
  const control = await mockApi(page)
  await inventory(page)
  await page.getByRole("button", { name: "新增资产", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "新增资产" })
  await expect(dialog.getByLabel("资产归属", { exact: true })).toHaveValue("")
  await dialog.getByLabel("资产名称", { exact: true }).fill("模拟新设备")
  await dialog.getByRole("button", { name: "保存资产" }).click()
  expect(control.creates).toHaveLength(0)
  await dialog.getByLabel("资产归属", { exact: true }).selectOption("owned")
  await dialog.getByLabel("取得方式", { exact: true }).selectOption("other")
  await dialog.getByLabel("获得方式说明").fill("模拟捐赠")
  await expect(dialog.getByLabel("租赁开始日期")).toHaveCount(0)
  await dialog.getByLabel("取得方式", { exact: true }).selectOption("purchase")
  await dialog.getByLabel("采购渠道", { exact: true }).selectOption("offline")
  await dialog.getByRole("button", { name: "保存资产" }).click()
  await expect.poll(() => control.creates.length).toBe(1)
  expect(control.creates[0]).toMatchObject({ ownership_type: "owned", acquisition_method: "purchase", acquisition_channel: "offline" })
  await page.getByRole("button", { name: "新增资产", exact: true }).click()
  await dialog.getByLabel("资产名称", { exact: true }).fill("模拟租用设备")
  await dialog.getByLabel("资产归属", { exact: true }).selectOption("rented")
  await dialog.getByLabel("租赁开始日期").fill("2026-01-01")
  await dialog.getByLabel("租赁到期日期（含当天）").fill("2026-10-31")
  await expect(dialog.getByLabel("取得方式", { exact: true })).toHaveCount(0)
  await noOverflow(page)
  await dialog.getByRole("button", { name: "保存资产" }).click()
  await expect.poll(() => control.creates.length).toBe(2)
  expect(control.creates[1]).toMatchObject({ ownership_type: "rented", rental_started_on: "2026-01-01", rental_expires_on: "2026-10-31" })
})

test("single and explicit bulk renewal show original/new dates and send selected versions only", async ({ page }) => {
  const control = await mockApi(page)
  await inventory(page)
  await page.locator(".assetTableRow").filter({ hasText: "SYN-RENT-1" }).getByRole("button", { name: "续期", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "续期租赁资产" })
  await dialog.getByLabel("续期周期").selectOption("month")
  await expect(dialog.getByText("原到期日：2026-10-31", { exact: true })).toBeVisible()
  await expect(dialog.getByText("2026-11-30", { exact: true })).toBeVisible()
  await expect(dialog.getByRole("button", { name: "确认续期", exact: true })).toBeDisabled()
  await dialog.getByLabel("我已核对原到期日和新到期日，确认续期").check()
  await noOverflow(page)
  await dialog.getByRole("button", { name: "确认续期", exact: true }).click()
  await expect(dialog).toHaveCount(0)
  expect(control.renewals[0]).toMatchObject({ period: "month", assets: [{ asset_id: ids[0], expected_rental_revision: 2 }] })
  expect(control.renewals[0].operation_key).toMatch(/^[0-9a-f-]{36}$/)
  await page.getByLabel("选择续期 SYN-RENT-1", { exact: true }).check()
  await page.getByLabel("选择续期 SYN-RENT-2", { exact: true }).check()
  await page.getByRole("button", { name: "批量续期（2）" }).click()
  await dialog.getByLabel("续期周期").selectOption("week")
  await expect(dialog.getByText("SYN-RENT-2 · 模拟租赁设备2", { exact: true })).toBeVisible()
  await expect(dialog.getByText("原到期日：2026-10-24", { exact: true })).toBeVisible()
  await expect(dialog.getByText("2026-10-31", { exact: true })).toBeVisible()
  await dialog.getByLabel("我已核对原到期日和新到期日，确认续期").check()
  await dialog.getByRole("button", { name: "确认续期", exact: true }).click()
  await expect.poll(() => control.renewals.length).toBe(2)
  expect(control.renewals[1].assets).toEqual([{ asset_id: ids[0], expected_rental_revision: 3 }, { asset_id: ids[1], expected_rental_revision: 3 }])
  expect(control.renewals[1].operation_key).not.toBe(control.renewals[0].operation_key)
  await expect(dialog).toHaveCount(0)
  await expect(page.locator(".assetTableRow").filter({ hasText: "SYN-RENT-1" })).toContainText("2026-12-07")
  await expect(page.locator(".assetTableRow").filter({ hasText: "SYN-RENT-2" })).toContainText("2026-10-31")
})

test("409 stale renewal refreshes inventory and blocks resubmitting obsolete selection", async ({ page }) => {
  const control = await mockApi(page)
  control.stale = true
  await inventory(page)
  await page.getByLabel("选择续期 SYN-RENT-1", { exact: true }).check()
  await page.getByRole("button", { name: "批量续期（1）" }).click()
  const dialog = page.getByRole("dialog", { name: "续期租赁资产" })
  await dialog.getByLabel("我已核对原到期日和新到期日，确认续期").check()
  const reads = control.listReads
  await dialog.getByRole("button", { name: "确认续期", exact: true }).click()
  await expect(dialog.getByRole("alert")).toContainText("请关闭并重新选择资产")
  await expect.poll(() => control.listReads).toBeGreaterThan(reads)
  await expect(dialog.getByRole("button", { name: "确认续期", exact: true })).toBeDisabled()
  expect(control.renewals).toHaveLength(1)
})

test("workbench shows independent due and overdue metadata without page overflow", async ({ page }) => {
  await mockApi(page)
  await page.goto("/")
  const panel = page.getByRole("region", { name: "租用资产到期提醒", exact: true })
  await expect(panel.getByText("6 天后到期")).toBeVisible()
  await expect(panel.getByText("已逾期 1 天")).toBeVisible()
  await expect(panel.getByText("模拟使用人", { exact: true })).toBeVisible()
  await expect(panel.getByText("2026-10-24", { exact: true })).toBeVisible()
  await expect(panel.getByRole("link", { name: "管理资产与续期" })).toHaveAttribute("href", "/assets")
  await noOverflow(page)
})

test("read-only permission exposes dates but no renewal selection or write controls", async ({ page }) => {
  await mockApi(page, ["assets.read"])
  await inventory(page)
  await expect(page.getByRole("button", { name: /^(新增资产|续期|退租|批量续期（\d+）)$/ })).toHaveCount(0)
  await expect(page.getByRole("checkbox", { name: /选择续期/ })).toHaveCount(0)
  await page.goto("/")
  const panel = page.getByRole("region", { name: "租用资产到期提醒", exact: true })
  await expect(panel.getByRole("link", { name: "查看资产台账" })).toBeVisible()
  await expect(panel.getByRole("link", { name: "管理资产与续期" })).toHaveCount(0)
  await noOverflow(page)
})

test("unavailable capability does not leak rental reminder records", async ({ page }) => {
  const control = await mockApi(page, [])
  await page.goto("/")
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  await expect.poll(() => control.reminderReads).toBeGreaterThan(0)
  await expect(page.getByRole("region", { name: "租用资产到期提醒", exact: true })).toHaveCount(0)
  await expect(page.getByText("模拟租赁设备1", { exact: true })).toHaveCount(0)
})

test("workbench reauthorization clears previously visible rental data after capability loss", async ({ page }) => {
  const control = await mockApi(page)
  await page.goto("/")
  const panel = page.getByRole("region", { name: "租用资产到期提醒", exact: true })
  await expect(panel.getByText("模拟租赁设备1", { exact: true })).toBeVisible()
  const reads = control.reminderReads
  control.denied = true
  await page.evaluate(() => document.dispatchEvent(new Event("visibilitychange")))
  await expect.poll(() => control.reminderReads).toBeGreaterThan(reads)
  await expect(panel).toHaveCount(0)
  await expect(page.getByText("模拟租赁设备1", { exact: true })).toHaveCount(0)
})

test("category preset selection creates an asset without typing a classification", async ({ page }) => {
  const control = await mockApi(page)
  await inventory(page)
  await page.getByRole("button", { name: "新增资产", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "新增资产" })
  const category = dialog.getByRole("combobox", { name: "资产分类", exact: true })
  await expect(category.locator('optgroup[label="电脑与电竞设备"]')).toHaveCount(1)
  await expect(category.locator('option[value="模拟设备"]')).toHaveCount(0)
  await category.selectOption("台式电脑")
  await dialog.getByLabel("资产名称", { exact: true }).fill("模拟训练主机")
  await dialog.getByLabel("CPU 型号", { exact: true }).fill("模拟 CPU")
  await dialog.getByLabel("内存容量", { exact: true }).fill("32GB")
  await dialog.getByLabel("GPU 型号", { exact: true }).fill("核显")
  await dialog.getByLabel("硬盘容量", { exact: true }).fill("1TB SSD")
  await dialog.getByLabel("资产归属", { exact: true }).selectOption("owned")
  await dialog.getByLabel("取得方式", { exact: true }).selectOption("other")
  await dialog.getByLabel("获得方式说明").fill("模拟捐赠")
  await noOverflow(page)
  await dialog.getByRole("button", { name: "保存资产" }).click()
  await expect.poll(() => control.creates.length).toBe(1)
  expect(control.creates[0]).toMatchObject({ name: "模拟训练主机", category: "台式电脑", cpu_model: "模拟 CPU", memory_capacity: "32GB", gpu_model: "核显", disk_capacity: "1TB SSD" })
})

test("category editor preserves a historical classification during unrelated changes", async ({ page }) => {
  const control = await mockApi(page)
  control.items[0].category = "  旧教学器材  "
  await inventory(page)
  await page.locator(".assetTableRow").filter({ hasText: "SYN-RENT-1" }).getByRole("button", { name: "编辑", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "编辑资产" })
  const category = dialog.getByRole("combobox", { name: "资产分类", exact: true })
  await expect(category).toHaveValue("  旧教学器材  ")
  await dialog.getByLabel("资产名称", { exact: true }).fill("模拟更新设备")
  await category.selectOption("台式电脑")
  await category.selectOption("  旧教学器材  ")
  await category.selectOption("")
  await category.selectOption("  旧教学器材  ")
  await expect(dialog.getByLabel("资产名称", { exact: true })).toHaveValue("模拟更新设备")
  await noOverflow(page)
  await dialog.getByRole("button", { name: "保存资产" }).click()
  await expect.poll(() => control.updates.length).toBe(1)
  expect(control.updates[0]).toMatchObject({ name: "模拟更新设备" })
  expect(control.updates[0]).not.toHaveProperty("category")
  expect(control.items[0].category).toBe("  旧教学器材  ")
  await expect(page.locator(".assetTableRow").filter({ hasText: "SYN-RENT-1" })).toContainText("旧教学器材")
})

test("category filter includes historical choices beyond current rows and exports applied selection", async ({ page }) => {
  const control = await mockApi(page, ["assets.read", "assets.manage", "assets.export"])
  await inventory(page)
  const select = page.getByRole("combobox", { name: "分类", exact: true })
  await select.selectOption("旧训练器材")
  expect(control.listCategories.at(-1)).toBeNull()
  await page.getByRole("button", { name: "查询", exact: true }).click()
  await expect.poll(() => control.listCategories.at(-1)).toBe("旧训练器材")
  await select.selectOption("显示器")
  await page.getByRole("button", { name: "导出", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "导出资产台账" })
  await dialog.getByLabel("导出用途").fill("模拟分类验收")
  await dialog.getByLabel("文件格式").selectOption("csv")
  await noOverflow(page)
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    dialog.getByRole("button", { name: "开始导出" }).click(),
  ])
  expect(download.suggestedFilename()).toBe("synthetic-assets.csv")
  await expect.poll(() => control.exports.length).toBe(1)
  expect(control.exports[0]).toMatchObject({ category: "旧训练器材", format: "csv", purpose: "模拟分类验收" })
  await expect(dialog).toHaveCount(0)
})
