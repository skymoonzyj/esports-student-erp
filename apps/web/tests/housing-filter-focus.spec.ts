import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"
import type { HousingCatalog, HousingOverview, HousingRoom } from "../src/housing/api"

// This file's tab-return regression uses synthetic API data only. Actual-data
// instant-filter acceptance lives in housing-room-status-foundation.spec.ts.
test.use({ trace: "off", screenshot: "off", video: "off" })
test.beforeEach(async ({ page }, info) => {
  void page
  test.skip(!["desktop-1440", "mobile-360"].includes(info.project.name))
})

const timestamp = "2026-10-10T08:00:00+08:00"
const catalog: HousingCatalog = {
  access_revision: "fictional-access-v1",
  can_create_building: false,
  buildings: [{ id: "building-focus", code: "F", name: "虚构回焦楼", display_order: 1, is_active: true, notes: null, allowed_actions: [], created_at: timestamp, updated_at: timestamp }],
  floors: [{ id: "floor-focus", building_id: "building-focus", building_name: "虚构回焦楼", number: 2, name: "虚构二层", display_order: 1, is_active: true, notes: null, allowed_actions: [], created_at: timestamp, updated_at: timestamp }],
}

function room(index: number): HousingRoom {
  return {
    id: `fictional-room-${index}`, building_id: "building-focus", floor_id: "floor-focus",
    building_code: "F", building_name: "虚构回焦楼", building_is_active: true,
    floor_number: 2, floor_name: "虚构二层", floor_is_active: true,
    room_number: `回焦-${index}`, room_type: "double", capacity: 2, area_square_meters: "20",
    verification_status: "verified", service_status: "available", notes: "合成房间数据",
    version: 1, current_occupants: 0, remaining_capacity: 2, current_gender: null,
    has_future_reservation: false, occupancy_status: "unoccupied", is_assignable: true,
    status_text: "可入住", allowed_actions: { edit: false, delete: false, verify: false, create_stay: false, report_issue: false, view_detail: true, manage_care: false, complete_cleaning: false, manage_photos: false },
    daily_cleaning_enabled: false, cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null, photo_count: 0,
    occupants: [], future_reservation_count: 0, open_issue_summary: null, pending_transfer_count: 0,
    created_at: timestamp, updated_at: timestamp,
  }
}

async function fixtureApi(page: Page) {
  const control = { holdContext: false, contextPending: false, releaseContext: () => {}, overviewRequests: [] as URLSearchParams[] }
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url())
    const json = (value: unknown) => route.fulfill({ status: 200, contentType: "application/json", headers: { "Cache-Control": "no-store" }, body: JSON.stringify(value) })
    if (url.pathname.endsWith("/auth/me")) return json({ account_id: "fictional-focus-account", person_id: "fictional-focus-person", full_name: "虚构房态管理员", email: null, force_password_change: false, is_super_admin: false, permissions: ["housing.read"], scoped_permissions: [] })
    if (url.pathname.endsWith("/auth/csrf")) return json({ csrf_token: "fictional-focus-csrf" })
    if (url.pathname.endsWith("/portal/context")) {
      if (control.holdContext) {
        control.contextPending = true
        await new Promise<void>(resolve => { control.releaseContext = resolve })
      }
      return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: true, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    }
    if (url.pathname.endsWith("/team-management/context")) return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (url.pathname.endsWith("/housing/catalog")) return json(catalog)
    if (url.pathname.endsWith("/housing/overview")) {
      control.overviewRequests.push(url.searchParams)
      const pageNumber = Number(url.searchParams.get("page") ?? 1)
      const response: HousingOverview = {
        access_revision: "fictional-access-v1", as_of: timestamp, page: pageNumber, page_size: 20, total: 25,
        summary: { total_rooms: 25, occupied_rooms: 0, unoccupied_rooms: 25, assignable_rooms: 25, unassignable_rooms: 0, full_rooms: 0, problem_rooms: 0, pending_verification_rooms: 0, pending_cleaning_rooms: 0, total_capacity: 50, current_occupants: 0, remaining_capacity: 50, occupancy_rate: "0", single_rooms: 0, double_rooms: 25, triple_rooms: 0, custom_rooms: 0, functional_rooms: 0 },
        rooms: Array.from({ length: pageNumber === 2 ? 5 : 20 }, (_, offset) => room((pageNumber - 1) * 20 + offset + 1)),
      }
      return json(response)
    }
    // Unexpected network calls fail closed instead of reaching real services.
    return route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "No synthetic fixture for this endpoint" }) })
  })
  return control
}

// This simulates the hidden -> visible events listened to by focusManager.
// It is an automated browser regression, not proof of physical tab/window switching.
async function simulateTabReturn(page: Page) {
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" })
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }))
  })
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" })
    document.dispatchEvent(new Event("visibilitychange", { bubbles: true }))
  })
}

// Removing route-level filter retention must fail these real control/page assertions.
test("housing tab-return reauthorization hides rooms while pending and preserves applied filters and page", async ({ page }) => {
  const control = await fixtureApi(page)
  const mobile = page.viewportSize()?.width === 360
  await page.goto("/housing")
  await expect(page.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
  await expect(page.getByRole("article").first()).toBeVisible()
  await page.getByRole("button", { name: "按空房筛选房间", exact: true }).click()
  await expect(page.getByRole("article").first()).toBeVisible()
  if (mobile) await page.getByRole("button", { name: "打开筛选" }).click()
  await page.getByRole("combobox", { name: "楼栋", exact: true }).selectOption("building-focus")
  await page.getByRole("combobox", { name: "楼层", exact: true }).selectOption("floor-focus")
  await page.getByRole("textbox", { name: "搜索房间", exact: true }).fill("回焦")
  await page.getByRole("checkbox", { name: "双人间", exact: true }).check()
  await page.getByRole("checkbox", { name: "已核对", exact: true }).check()
  await page.getByRole("checkbox", { name: "正常", exact: true }).check()
  await page.getByRole("combobox", { name: "可入住状态", exact: true }).selectOption("assignable")
  await expect.poll(() => control.overviewRequests.at(-1)?.get("assignability")).toBe("assignable")
  // A pending search debounce is itself a new base-filter change and resets
  // pagination; finish that real request before testing page retention.
  await expect.poll(() => control.overviewRequests.at(-1)?.get("query")).toBe("回焦")
  if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
  await page.getByRole("navigation", { name: "房态分页" }).getByRole("button", { name: "下一页" }).click()
  const pagination = page.getByRole("navigation", { name: "房态分页" })
  await expect(pagination).toContainText("第 2 / 2 页")
  await expect(page.getByRole("heading", { name: "回焦-21", exact: true })).toBeVisible()

  control.holdContext = true
  await simulateTabReturn(page)
  await expect.poll(() => control.contextPending).toBe(true)
  await expect(page.locator(".housingRoomCard")).toHaveCount(0)
  await expect(page.getByRole("heading", { name: "回焦-21", exact: true })).toHaveCount(0)
  control.holdContext = false
  control.releaseContext()

  await expect(page.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
  await expect(pagination).toContainText("第 2 / 2 页")
  await expect(page.getByRole("heading", { name: "回焦-21", exact: true })).toBeVisible()
  if (mobile) await page.getByRole("button", { name: "打开筛选" }).click()
  await expect(page.getByRole("combobox", { name: "楼栋", exact: true })).toHaveValue("building-focus")
  await expect(page.getByRole("combobox", { name: "楼层", exact: true })).toHaveValue("floor-focus")
  await expect(page.getByRole("textbox", { name: "搜索房间", exact: true })).toHaveValue("回焦")
  await expect(page.getByRole("checkbox", { name: "双人间", exact: true })).toBeChecked()
  await expect(page.getByRole("checkbox", { name: "已核对", exact: true })).toBeChecked()
  await expect(page.getByRole("checkbox", { name: "正常", exact: true })).toBeChecked()
  // Changing any sidebar condition clears the previous vacant shortcut.
  await expect.poll(() => control.overviewRequests.at(-1)?.get("quick_filter")).toBeNull()
  await expect.poll(() => control.overviewRequests.at(-1)?.get("occupancy")).toBeNull()
  await expect(page.getByRole("combobox", { name: "可入住状态", exact: true })).toHaveValue("assignable")
})
