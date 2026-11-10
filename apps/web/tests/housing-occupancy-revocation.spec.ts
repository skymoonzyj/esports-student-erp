import { expect, test } from "@playwright/test"
import type { APIRequestContext, Locator, Page } from "@playwright/test"

import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
  loginAsAdmin,
  loginAsCustomerService,
  logout,
  openNavigationItem,
  refocus,
} from "./helpers"

const CUSTOMER_SERVICE_ACCOUNT_ID = "e1100000-0000-4000-8000-000000000002"
const HOUSING_READ_PERMISSION_ID = "20000000-0000-4000-8000-000000000037"
const HOUSING_STAYS_MANAGE_PERMISSION_ID = "20000000-0000-4000-8000-000000000039"
const SOURCE_ROOMS = {
  1440: "4-201",
  1024: "4-205",
  768: "4-207",
  360: "4-209",
} as const

function viewportWidth(page: Page): number {
  return page.viewportSize()?.width ?? 1440
}

function sourceRoom(page: Page): string {
  const width = viewportWidth(page)
  const room = SOURCE_ROOMS[width as keyof typeof SOURCE_ROOMS]
  if (!room) throw new Error(`Missing source room for viewport ${width}`)
  return room
}

function shanghaiDateInput(offsetDays: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date(Date.now() + offsetDays * 86_400_000))
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

async function openHousingFilters(page: Page): Promise<Locator> {
  if (viewportWidth(page) < 720) {
    const trigger = page.getByRole("button", { name: "打开筛选" })
    await expect(trigger).toBeVisible()
    await trigger.click()
  }
  const filters = page.locator(".housingFilters")
  await expect(filters).toBeVisible()
  return filters
}

async function findRoomCard(page: Page, roomNumber: string): Promise<Locator> {
  const filters = await openHousingFilters(page)
  await filters.getByLabel("搜索房间").fill(roomNumber)
  await expect(page.getByText("筛选结果 1 间", { exact: true })).toBeVisible()
  const card = page.locator(".housingRoomCard").filter({ hasText: roomNumber })
  await expect(card).toHaveCount(1)
  if (viewportWidth(page) < 720) await filters.getByRole("button", { name: "关闭筛选" }).click()
  await expect(card).toBeVisible()
  return card
}

async function verifyRoomAndGetId(page: Page, roomNumber: string): Promise<string> {
  const card = await findRoomCard(page, roomNumber)
  const verify = card.getByRole("button", { name: `核对房间 ${roomNumber}` })
  await expect(verify).toBeVisible()
  await verify.click()
  await expect(verify).toHaveCount(0)
  await card.getByRole("button", { name: `查看房间 ${roomNumber} 详情` }).click()
  await expect(page.getByRole("heading", { name: `房间 ${roomNumber}` })).toBeVisible()
  const match = page.url().match(/\/housing\/rooms\/([0-9a-f-]+)$/)
  if (!match) throw new Error(`Room detail URL missing id: ${page.url()}`)
  return match[1]
}

async function addFloorOverride(
  admin: APIRequestContext,
  csrfToken: string,
  permissionId: string,
  floorId: string,
  reason: string,
): Promise<string> {
  const response = await admin.post(
    `/api/v1/accounts/${CUSTOMER_SERVICE_ACCOUNT_ID}/permission-overrides`,
    {
      headers: { "X-CSRF-Token": csrfToken },
      data: {
        permission_id: permissionId,
        effect: "allow",
        scope_kind: "floor",
        scope_id: floorId,
        grant_reason: reason,
      },
    },
  )
  expect(response.status()).toBe(201)
  const body = (await response.json()) as { id: string }
  return body.id
}

async function removeOverride(
  admin: APIRequestContext,
  csrfToken: string,
  overrideId: string,
): Promise<void> {
  const response = await admin.delete(
    `/api/v1/accounts/${CUSTOMER_SERVICE_ACCOUNT_ID}/permission-overrides/${overrideId}`,
    { headers: { "X-CSRF-Token": csrfToken } },
  )
  expect(response.status()).toBe(204)
}

async function expectMobileDialogControls(page: Page): Promise<void> {
  if (viewportWidth(page) !== 360) return
  const boxes = await page.locator([
    ".housingDialog button:visible",
    ".housingDialog input:not([type='radio']):visible",
    ".housingDialog select:visible",
    ".housingDialog textarea:visible",
    ".housingDialog .housingResidentOptions label:visible",
  ].join(", ")).evaluateAll((controls) =>
    controls.map((control) => {
      const box = control.getBoundingClientRect()
      return { width: box.width, height: box.height }
    }),
  )
  expect(boxes.length).toBeGreaterThan(0)
  for (const box of boxes) {
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
}

async function draftValueCount(page: Page, expected: string): Promise<number> {
  return page.locator("input, textarea").evaluateAll(
    (elements, value) => elements.filter(
      (element) => (element as HTMLInputElement | HTMLTextAreaElement).value === value,
    ).length,
    expected,
  )
}

test("revoking floor housing.read purges scoped detail, drafts, and late resident data", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(15_000)
  const roomNumber = sourceRoom(page)
  const draft = `撤权草稿-${testInfo.project.name}`
  const residentRoute = /\/api\/v1\/housing\/rooms\/[^/]+\/resident-options\?/
  const remainingOverrideIds = new Set<string>()
  const { admin, csrfToken } = await createAdminRequest()
  let delayNextResidentResponse = false
  let lateResponseStarted = false
  let lateResponseBody = ""
  let lateResponseError: unknown
  let releaseLateResponse = () => {}
  let markLateResponseReady = () => {}
  let markLateResponseSettled = () => {}
  const lateResponseRelease = new Promise<void>((resolve) => {
    releaseLateResponse = resolve
  })
  const lateResponseReady = new Promise<void>((resolve) => {
    markLateResponseReady = resolve
  })
  const lateResponseSettled = new Promise<void>((resolve) => {
    markLateResponseSettled = resolve
  })

  await page.route(residentRoute, async (route) => {
    if (!delayNextResidentResponse) {
      await route.continue()
      return
    }
    delayNextResidentResponse = false
    lateResponseStarted = true
    try {
      const response = await route.fetch()
      lateResponseBody = await response.text()
      markLateResponseReady()
      await lateResponseRelease
      try {
        await route.fulfill({ response, body: lateResponseBody })
      } catch {
        // Revocation may abort the in-flight browser request before settlement.
      }
    } catch (caught) {
      lateResponseError = caught
      markLateResponseReady()
    } finally {
      markLateResponseSettled()
    }
  })

  try {
    await loginAsAdmin(page)
    await openNavigationItem(page, "房态管理")
    const roomId = await verifyRoomAndGetId(page, roomNumber)
    const detailResponse = await admin.get(`/api/v1/housing/rooms/${roomId}/detail`)
    expect(detailResponse.status()).toBe(200)
    const detail = (await detailResponse.json()) as { room: { floor_id: string } }
    const floorId = detail.room.floor_id

    const readOverrideId = await addFloorOverride(
      admin,
      csrfToken,
      HOUSING_READ_PERMISSION_ID,
      floorId,
      `住宿读取撤权 E2E ${testInfo.project.name}`,
    )
    remainingOverrideIds.add(readOverrideId)
    const manageOverrideId = await addFloorOverride(
      admin,
      csrfToken,
      HOUSING_STAYS_MANAGE_PERMISSION_ID,
      floorId,
      `住宿入住撤权 E2E ${testInfo.project.name}`,
    )
    remainingOverrideIds.add(manageOverrideId)

    await logout(page)
    await loginAsCustomerService(page)
    await openNavigationItem(page, "房态管理")
    const scopedCard = await findRoomCard(page, roomNumber)
    await scopedCard
      .getByRole("button", { name: `查看房间 ${roomNumber} 详情` })
      .click()
    await expect(page.getByRole("heading", { name: `房间 ${roomNumber}` })).toBeVisible()
    await expectNoHorizontalOverflow(page)
    await expectHorizontallyInsideViewport(page, ".housingRoomDetail")

    const arrange = page.getByRole("button", { name: "安排入住" })
    await expect(arrange).toBeVisible()
    await arrange.click()
    const dialog = page.getByRole("dialog", { name: `安排入住 · ${roomNumber}` })
    await expect(dialog).toBeVisible()
    await expectHorizontallyInsideViewport(page, ".housingDialog")
    await expectNoInternalHorizontalOverflow(dialog)
    await expectAboveMobileNavigation(page, ".housingDialog")
    await expectMobileDialogControls(page)

    const residentSearch = dialog.getByLabel("搜索入住人员")
    await residentSearch.fill("E2E自助")
    const resident = dialog.getByRole("radio", { name: "选择 E2E自助学员" })
    await expect(resident).toBeVisible()
    await resident.check()
    await dialog.getByLabel("住宿类型").selectOption("permanent")
    await dialog.getByLabel("时长类型").selectOption("fixed_term")
    await dialog.getByLabel("计划入住日期").fill(shanghaiDateInput(1))
    await dialog.getByLabel("计划离开日期").fill(shanghaiDateInput(2))
    await dialog.getByLabel("备注").fill(draft)
    await expect(dialog.getByLabel("备注")).toHaveValue(draft)
    await expect(dialog.getByText("E2E自助学员", { exact: true })).toBeVisible()

    delayNextResidentResponse = true
    await residentSearch.fill("E2E自助学员")
    await lateResponseReady
    expect(lateResponseError).toBeUndefined()
    expect(lateResponseBody).toContain("E2E自助学员")
    await expect(dialog.getByLabel("备注")).toHaveValue(draft)

    await removeOverride(admin, csrfToken, readOverrideId)
    remainingOverrideIds.delete(readOverrideId)
    await refocus(page)

    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await expect(page.getByRole("link", { name: "房态管理" })).toHaveCount(0)
    await expect(page.getByText("E2E自助学员", { exact: true })).toHaveCount(0)
    await expect.poll(() => draftValueCount(page, draft)).toBe(0)
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await expect(page.getByRole("button", { name: "安排入住" })).toHaveCount(0)
    await expect(page.locator(".housingRoomDetail")).toHaveCount(0)
    await expect(page.getByText(roomNumber, { exact: true })).toHaveCount(0)
    await expectNoHorizontalOverflow(page)

    releaseLateResponse()
    await lateResponseSettled
    await expect(page.getByText("E2E自助学员", { exact: true })).toHaveCount(0)
    await expect.poll(() => draftValueCount(page, draft)).toBe(0)
    await expect(page.getByText(roomNumber, { exact: true })).toHaveCount(0)
  } finally {
    releaseLateResponse()
    if (lateResponseStarted) await lateResponseSettled
    await page.unroute(residentRoute)
    try {
      for (const overrideId of remainingOverrideIds) {
        await removeOverride(admin, csrfToken, overrideId)
      }
    } finally {
      await admin.dispose()
    }
  }
})
