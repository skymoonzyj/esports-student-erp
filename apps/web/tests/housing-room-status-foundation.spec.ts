import { expect, test } from "@playwright/test"
import type { Locator, Page } from "@playwright/test"

import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCustomerService,
  logout,
  openNavigationItem,
} from "./helpers"
import type { HousingOverview, HousingRoom } from "../src/housing/api"

function viewportWidth(page: Page): number {
  return page.viewportSize()?.width ?? 1440
}

async function expectHousingLayout(page: Page, selector = ".housingPage"): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, selector)
  await expectAboveMobileNavigation(page, ".housingPagination")
}

async function expectDialogLayout(page: Page): Promise<Locator> {
  const dialog = page.getByRole("dialog")
  await expect(dialog).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".housingDialog")
  await expectAboveMobileNavigation(page, ".housingDialog")
  return dialog
}

async function openFilters(page: Page): Promise<Locator> {
  if (viewportWidth(page) < 720 && !await page.locator(".housingFilters").isVisible()) {
    await page.getByRole("button", { name: "打开筛选" }).click()
  }
  const filters = page.locator(".housingFilters")
  await expect(filters).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".housingFilters")
  await expectAboveMobileNavigation(page, ".housingFilters")
  return filters
}

async function exerciseDialogDismissals(
  page: Page,
  trigger: Locator,
  dialogName: string,
): Promise<void> {
  await trigger.click()
  let dialog = await expectDialogLayout(page)
  await dialog.getByRole("button", { name: "取消" }).click()
  await expect(page.getByRole("dialog", { name: dialogName })).toHaveCount(0)
  await expect(trigger).toBeFocused()

  await trigger.click()
  dialog = await expectDialogLayout(page)
  await page.keyboard.press("Escape")
  await expect(dialog).toHaveCount(0)
  await expect(trigger).toBeFocused()
}

async function applySearch(page: Page, query: string, expectedRoom: string): Promise<void> {
  const filters = await openFilters(page)
  await filters.getByLabel("搜索房间").fill(query)
  await expect(page.getByText("筛选结果 1 间", { exact: true })).toBeVisible()
  await expect(page.locator(".housingRoomCard").filter({ hasText: expectedRoom })).toHaveCount(1)
  if (viewportWidth(page) < 720) await filters.getByRole("button", { name: "关闭筛选" }).click()
  await expectHousingLayout(page, ".housingRoomGrid")
}

async function resetFilters(page: Page, expectedRooms = 188): Promise<void> {
  const filters = await openFilters(page)
  await filters.getByRole("button", { name: "重置筛选" }).click()
  await expect(page.getByText(`筛选结果 ${expectedRooms} 间`, { exact: true })).toBeVisible()
  await expectHousingLayout(page, ".housingRoomGrid")
}

async function expectMobileTouchTargets(page: Page): Promise<void> {
  if (viewportWidth(page) !== 360) return
  const boxes = await page.locator(".housingPage button:visible").evaluateAll((buttons) =>
    buttons.map((button) => {
      const box = button.getBoundingClientRect()
      return { width: box.width, height: box.height }
    }),
  )
  expect(boxes.length).toBeGreaterThan(0)
  for (const box of boxes) {
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
}

test("admin verifies the housing room-status foundation and customer service is denied", async ({ page }) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(10_000)
  const width = viewportWidth(page)
  const buildingName = `测试楼-${width}`
  const floorName = `测试层-${width}`
  const tripleRoom = `T-${width}`
  const customRoom = `C-${width}`

  await loginAsAdmin(page)
  await openNavigationItem(page, "房态管理")
  await expect(page.getByRole("heading", { name: "住宿房态" })).toBeVisible()

  const summary = page.getByRole("region", { name: "住宿汇总" })
  for (const item of [
    "房间总数 188",
    "待核对 171",
    "单人间 119",
    "双人间 52",
    "功能间 17",
    "总床位 223",
  ]) {
    await expect(summary.getByText(item, { exact: true })).toBeVisible()
  }
  await expectHousingLayout(page, ".housingRoomGrid")

  for (const [query, room] of [
    ["4-409", "4-409"],
    ["9-305", "9-305"],
    ["5-311", "5-311"],
    ["Ａ－２０５", "A-205"],
  ] as const) {
    await applySearch(page, query, room)
  }
  await resetFilters(page)

  await page.getByRole("button", { name: "按空房筛选房间", exact: true }).click()
  let filters = await openFilters(page)
  await filters.locator("label").filter({ hasText: /^可入住状态/ }).locator("select").selectOption("full")
  // All seed rooms are vacant: sidebar 满房 means full, not the
  // legacy broad unassignable set of pending-verification/functional rooms.
  await expect(page.getByText("筛选结果 0 间", { exact: true })).toBeVisible()
  if (width < 720) await filters.getByRole("button", { name: "关闭筛选" }).click()
  await expect(page.getByRole("heading", { name: "没有匹配的房间", exact: true })).toBeVisible()
  await expectHousingLayout(page, ".housingState--empty")
  await resetFilters(page)

  filters = await openFilters(page)
  await filters.getByLabel("待核对").check()
  await expect(page.getByText("筛选结果 171 间", { exact: true })).toBeVisible()
  await expect(summary.getByText("待核对 171", { exact: true })).toBeVisible()
  if (width < 720) await filters.getByRole("button", { name: "关闭筛选" }).click()
  await expectHousingLayout(page, ".housingRoomGrid")
  await resetFilters(page)

  await applySearch(page, "4-409", "4-409")
  let targetCard = page.locator(".housingRoomCard").filter({ hasText: "4-409" })
  await expect(targetCard).toHaveClass(/housingRoomCard--unavailable/)
  await targetCard.getByRole("button", { name: "核对房间 4-409" }).click()
  await expect(targetCard).toHaveClass(/housingRoomCard--available/)
  await expect(targetCard.getByText("空闲，可入住", { exact: true })).toBeVisible()
  await resetFilters(page)
  await expect(summary.getByText("待核对 170", { exact: true })).toBeVisible()

  const managementTrigger = page.getByRole("button", { name: "目录管理" })
  await managementTrigger.click()
  await expect(page.getByRole("heading", { name: "目录管理" })).toBeVisible()
  const pendingChoices = page.locator(".housingRoomGrid .housingRoomCard")
    .filter({ has: page.getByRole("button", { name: /^核对房间 / }) })
    .getByRole("checkbox", { name: /^选择房间 / })
  await pendingChoices.nth(0).check()
  await pendingChoices.nth(1).check()
  await expect(page.getByLabel("跨页已选总数 2 间")).toBeVisible()
  await page.getByRole("button", { name: "批量核对 2 间房" }).click()
  await page.getByRole("dialog", { name: "确认批量操作" }).getByRole("button", { name: "确认执行" }).click()
  await expect(summary.getByText("待核对 168", { exact: true })).toBeVisible()
  await expect(page.getByLabel("跨页已选总数 2 间")).toHaveCount(0)
  await expectHousingLayout(page, ".housingRoomGrid")

  const createBuilding = page.getByRole("button", { name: "新建楼栋" })
  await exerciseDialogDismissals(page, createBuilding, "新建楼栋")
  await expectHousingLayout(page, ".housingRoomGrid")

  await createBuilding.click()
  let dialog = await expectDialogLayout(page)
  await dialog.getByLabel("楼栋代码").fill(`T${width}`)
  await dialog.getByLabel("楼栋名称").fill(buildingName)
  await dialog.getByLabel("显示顺序").fill(String(width))
  await dialog.getByRole("button", { name: "保存楼栋" }).click()
  const building = page.getByRole("article", { name: `楼栋 ${buildingName}` })
  await expect(building).toBeVisible()
  await expectHousingLayout(page, ".housingCatalog")

  const editBuilding = building.getByRole("button", { name: `编辑楼栋 ${buildingName}` })
  await exerciseDialogDismissals(page, editBuilding, `编辑楼栋 ${buildingName}`)
  await editBuilding.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByLabel("备注").fill(`E2E 楼栋 ${width}`)
  await dialog.getByRole("button", { name: "保存楼栋" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(building).toBeVisible()

  const createFloor = building.getByRole("button", { name: `在${buildingName}新建楼层` })
  await exerciseDialogDismissals(page, createFloor, `在${buildingName}新建楼层`)
  await createFloor.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByLabel("楼层编号").fill("1")
  await dialog.getByLabel("楼层名称").fill(floorName)
  await dialog.getByLabel("显示顺序").fill("1")
  await dialog.getByRole("button", { name: "保存楼层" }).click()
  const floor = page.getByRole("article", { name: `楼层 ${floorName}` })
  await expect(floor).toBeVisible()
  await expectHousingLayout(page, ".housingCatalog")

  const editFloor = floor.getByRole("button", { name: `编辑楼层 ${floorName}` })
  await exerciseDialogDismissals(page, editFloor, `编辑楼层 ${floorName}`)
  await editFloor.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByLabel("备注").fill(`E2E 楼层 ${width}`)
  await dialog.getByRole("button", { name: "保存楼层" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(floor).toBeVisible()

  const createRoom = floor.getByRole("button", { name: `在${floorName}新建房间` })
  await exerciseDialogDismissals(page, createRoom, `在${floorName}新建房间`)
  await createRoom.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByLabel("房间号").fill(tripleRoom)
  await dialog.getByLabel("房型").selectOption("triple")
  await dialog.getByLabel("面积（平方米）").fill("30")
  await dialog.getByRole("button", { name: "保存房间" }).click()
  await expect(summary.getByText("三人间 1", { exact: true })).toBeVisible()
  await expectHousingLayout(page, ".housingCatalog")

  await floor.getByRole("button", { name: `在${floorName}新建房间` }).click()
  dialog = await expectDialogLayout(page)
  await dialog.getByLabel("房间号").fill(customRoom)
  await dialog.getByLabel("房型").selectOption("custom")
  await dialog.getByLabel("自定义容量").fill("6")
  await dialog.getByLabel("面积（平方米）").fill("36")
  await dialog.getByRole("button", { name: "保存房间" }).click()
  await expect(summary.getByText("房间总数 190", { exact: true })).toBeVisible()
  await expect(summary.getByText("自定义房型 1", { exact: true })).toBeVisible()
  await expect(summary.getByText("总床位 232", { exact: true })).toBeVisible()
  await expectHousingLayout(page, ".housingCatalog")

  await applySearch(page, tripleRoom, tripleRoom)
  const createdTripleCard = page.locator(".housingRoomCard").filter({ hasText: tripleRoom })
  const deleteTripleRoom = createdTripleCard.getByRole("button", { name: `删除房间 ${tripleRoom}` })
  await exerciseDialogDismissals(page, deleteTripleRoom, `删除房间 ${tripleRoom}`)
  await deleteTripleRoom.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByRole("button", { name: `确认删除房间 ${tripleRoom}` }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText("筛选结果 0 间", { exact: true })).toBeVisible()
  await resetFilters(page, 189)
  await expect(summary.getByText("房间总数 189", { exact: true })).toBeVisible()
  await expect(summary.getByText("自定义房型 1", { exact: true })).toBeVisible()
  await expect(summary.getByText("总床位 229", { exact: true })).toBeVisible()

  await applySearch(page, "4-409", "4-409")
  targetCard = page.locator(".housingRoomCard").filter({ hasText: "4-409" })
  const editRoom = targetCard.getByRole("button", { name: "编辑房间 4-409" })
  await exerciseDialogDismissals(page, editRoom, "编辑房间 4-409")
  await editRoom.click()
  dialog = await expectDialogLayout(page)
  await expect(dialog.getByText("结构变更未设置服务状态时会将房间重置为待核对。", { exact: true })).toBeVisible()
  await dialog.getByLabel("面积（平方米）").fill("31")
  await dialog.getByRole("button", { name: "保存房间" }).click()
  await expect(targetCard).toHaveClass(/housingRoomCard--unavailable/)
  await expect(targetCard.getByText("待核对", { exact: true })).toBeVisible()
  await expectHousingLayout(page, ".housingRoomGrid")
  await editRoom.click()
  dialog = await expectDialogLayout(page)
  await expect(dialog.getByLabel("服务状态")).toHaveValue("")
  await dialog.getByLabel("服务状态").selectOption("available")
  await dialog.getByRole("button", { name: "保存房间" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(targetCard).toHaveClass(/housingRoomCard--available/)
  await expect(targetCard.getByText("空闲，可入住", { exact: true })).toBeVisible()
  // A later structural-only edit must still require verification.
  await editRoom.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByLabel("面积（平方米）").fill("32")
  await dialog.getByRole("button", { name: "保存房间" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(targetCard.getByText("待核对", { exact: true })).toBeVisible()
  await targetCard.getByRole("button", { name: "核对房间 4-409" }).click()
  await expect(targetCard).toHaveClass(/housingRoomCard--available/)
  await expect(targetCard.getByText("空闲，可入住", { exact: true })).toBeVisible()

  await applySearch(page, customRoom, customRoom)
  const customRoomCard = page.locator(".housingRoomCard").filter({ hasText: customRoom })
  const deleteCustomRoom = customRoomCard.getByRole("button", { name: `删除房间 ${customRoom}` })
  await deleteCustomRoom.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByRole("button", { name: `确认删除房间 ${customRoom}` }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText("筛选结果 0 间", { exact: true })).toBeVisible()
  await resetFilters(page)

  const deleteFloor = floor.getByRole("button", { name: `删除楼层 ${floorName}` })
  await exerciseDialogDismissals(page, deleteFloor, `删除楼层 ${floorName}`)
  await deleteFloor.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByRole("button", { name: `确认删除楼层 ${floorName}` }).click()
  await expect(dialog).toHaveCount(0)
  await expect(floor).toHaveCount(0)

  const deleteBuilding = building.getByRole("button", { name: `删除楼栋 ${buildingName}` })
  await exerciseDialogDismissals(page, deleteBuilding, `删除楼栋 ${buildingName}`)
  await deleteBuilding.click()
  dialog = await expectDialogLayout(page)
  await dialog.getByRole("button", { name: `确认删除楼栋 ${buildingName}` }).click()
  await expect(dialog).toHaveCount(0)
  await expect(building).toHaveCount(0)

  await expectMobileTouchTargets(page)
  await expectHousingLayout(page, ".housingRoomGrid")

  await logout(page)
  await loginAsCustomerService(page)
  await expect(page.getByRole("link", { name: "房态管理" })).toHaveCount(0)
  await page.goto("/housing")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
})

// Catches accumulated vacant+assignable predicates (which omit the partly
// occupied room), counters accidentally computed from the quick-filtered list,
// and controls losing their input/focus while real requests resolve.
test("real housing instant filters keep base counters, replace shortcuts and preserve mobile input", async ({ page, baseURL }) => {
  test.setTimeout(90_000)
  expect(baseURL && ["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname)).toBe(true)
  await loginAsAdmin(page)
  const { admin, csrfToken } = await createAdminRequest()
  const suffix = viewportWidth(page)
  const prefix = `INSTANT-${suffix}`
  const headers = { "x-csrf-token": csrfToken }
  try {
    const create = async <T,>(path: string, data: unknown, status = 201): Promise<T> => {
      const response = await admin.post(`/api/v1/housing/${path}`, { headers, data })
      expect(response.status(), `create isolated ${path}`).toBe(status)
      return await response.json() as T
    }
    const building = await create<{ id: string }>("buildings", { code: `I${suffix}`, name: prefix, display_order: suffix })
    const floor = await create<{ id: string }>("floors", { building_id: building.id, number: 1, name: `${prefix}-F`, display_order: 1 })
    const rooms: HousingRoom[] = []
    for (const [label, room_type, capacity] of [["EMPTY", "double", 2], ["PART", "double", 2], ["FULL", "double", 2], ["FUNCTION", "functional", 0]] as const) {
      const created = await create<HousingRoom>("rooms", { floor_id: floor.id, room_number: `${prefix}-${label}`, room_type, capacity })
      const verified = await create<HousingRoom>(`rooms/${created.id}/verify`, { version: created.version }, 200)
      rooms.push(verified)
    }
    for (const [index, person_id] of [[1, "e2000000-0000-4000-8000-000000000011"], [2, "e1000000-0000-4000-8000-000000000007"], [2, "e1000000-0000-4000-8000-000000000010"]] as const) {
      const stay = await create<{ status: string }>(`rooms/${rooms[index].id}/stays`, {
        person_id, stay_purpose: "permanent", duration_mode: "long_term", planned_check_in_at: new Date(Date.now() - 60_000).toISOString(),
      })
      expect(stay.status).toBe("checked_in")
    }
    // Independent persisted-data read, not a route mock or UI-derived oracle.
    const saved = await admin.get(`/api/v1/housing/overview?building_id=${building.id}`)
    expect(saved.status()).toBe(200)
    const persisted = await saved.json() as HousingOverview
    expect(persisted.summary).toMatchObject({ total_rooms: 4, occupied_rooms: 2, current_occupants: 3, unoccupied_rooms: 2, assignable_rooms: 2, full_rooms: 1, total_capacity: 6, remaining_capacity: 3, functional_rooms: 1 })
    expect(persisted.rooms.find(room => room.room_number.endsWith("-PART"))).toMatchObject({ current_occupants: 1, is_assignable: true })

    await page.goto("/housing")
    const waitOverview = async (action: () => Promise<unknown>, matches: (url: URL) => boolean): Promise<HousingOverview> => {
      const [response] = await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === "/api/v1/housing/overview" && matches(new URL(response.url()))),
        action(),
      ])
      expect(response.status()).toBe(200)
      return await response.json() as HousingOverview
    }
    let filters = await openFilters(page)
    await waitOverview(() => filters.getByRole("combobox", { name: "楼栋", exact: true }).selectOption(building.id), url => url.searchParams.get("building_id") === building.id)
    await expect(page.getByText("筛选结果 4 间", { exact: true })).toBeVisible()
    if (suffix < 720) await filters.getByRole("button", { name: "关闭筛选", exact: true }).click()
    const summary = page.getByRole("region", { name: "住宿汇总" })
    for (const counter of ["房间总数 4", "已用房间数 2", "空房 2", "可入住 2", "满房 1", "功能间 1", "总床位 6", "剩余床位 3"]) {
      await expect(summary.getByText(counter, { exact: true })).toBeVisible()
    }
    const baseCounters = await summary.innerText()
    for (const [name, quick, expected] of [["空房", "vacant", ["EMPTY", "FUNCTION"]], ["可入住", "assignable", ["EMPTY", "PART"]], ["满房", "full", ["FULL"]]] as const) {
      const body = await waitOverview(() => page.getByRole("button", { name: `按${name}筛选房间`, exact: true }).click(), url => url.searchParams.get("building_id") === building.id && url.searchParams.get("quick_filter") === quick)
      expect(body.rooms.map(room => room.room_number).sort()).toEqual(expected.map(label => `${prefix}-${label}`).sort())
      expect(body.summary).toEqual(persisted.summary)
      await expect(page.getByText(`筛选结果 ${expected.length} 间`, { exact: true })).toBeVisible()
      await expect.poll(() => summary.innerText()).toBe(baseCounters)
      for (const label of expected) await expect(page.getByRole("heading", { name: `${prefix}-${label}`, exact: true })).toBeVisible()
    }
    filters = await openFilters(page)
    await expect(filters.getByRole("combobox", { name: "楼栋", exact: true })).toHaveValue(building.id)
    await expect(filters.getByRole("combobox", { name: "可入住状态", exact: true })).toHaveValue("")
    const input = filters.getByRole("textbox", { name: "搜索房间", exact: true })
    const raw = `  ${prefix}-PART  `
    const typed = await waitOverview(async () => {
      await input.focus()
      await input.pressSequentially(raw, { delay: 25 })
    }, url => url.searchParams.get("query")?.trim() === raw.trim() && !url.searchParams.has("quick_filter"))
    expect(typed.rooms.map(room => room.room_number)).toEqual([`${prefix}-PART`])
    expect(typed.summary).toMatchObject({ total_rooms: 1, current_occupants: 1, assignable_rooms: 1 })
    for (const counter of ["房间总数 1", "已用房间数 1", "可入住 1"]) {
      await expect(summary.getByText(counter, { exact: true })).toBeVisible()
    }
    await expect(input).toHaveValue(raw)
    await expect(input).toBeFocused()
    await expect(page.getByText("筛选结果 1 间", { exact: true })).toBeVisible()
    if (suffix < 720) {
      await filters.getByRole("button", { name: "关闭筛选", exact: true }).click()
      await expect(page.getByRole("heading", { name: `${prefix}-PART`, exact: true })).toBeVisible()
      filters = await openFilters(page)
      await expect(filters.getByRole("textbox", { name: "搜索房间", exact: true })).toHaveValue(raw)
      await expect(filters.getByRole("combobox", { name: "楼栋", exact: true })).toHaveValue(building.id)
      await filters.getByRole("button", { name: "关闭筛选", exact: true }).click()
    }
    await expectHousingLayout(page, ".housingRoomGrid")
  } finally {
    await admin.dispose()
  }
})
