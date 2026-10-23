import { expect, test } from "@playwright/test"
import type { Locator, Page } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
  expectPrivateHousingProjection,
  loginAsAdmin,
  loginAsCoach,
  loginAsStudent,
  logout,
  openNavigationItem,
} from "./helpers"

const ROOM_PAIRS = {
  1440: { source: "4-201", target: "4-202" },
  1024: { source: "4-205", target: "4-206" },
  768: { source: "4-207", target: "4-208" },
  360: { source: "4-209", target: "4-211" },
} as const

function viewportWidth(page: Page): number {
  return page.viewportSize()?.width ?? 1440
}

function roomPair(page: Page): { source: string; target: string } {
  const width = viewportWidth(page)
  const pair = ROOM_PAIRS[width as keyof typeof ROOM_PAIRS]
  if (!pair) throw new Error(`Missing room pair for viewport ${width}`)
  return pair
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function shanghaiDateTimeInput(asOf: string, offsetMinutes: number): string {
  const asOfTimestamp = Date.parse(asOf)
  if (!Number.isFinite(asOfTimestamp)) {
    throw new Error(`Room detail as_of is not an ISO timestamp: ${asOf}`)
  }
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(asOfTimestamp + offsetMinutes * 60_000))
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}T${value.hour}:${value.minute}`
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
  await filters.getByRole("button", { name: "应用筛选" }).click()
  await expect(page.getByText("筛选结果 1 间", { exact: true })).toBeVisible()
  const card = page.locator(".housingRoomCard").filter({ hasText: roomNumber })
  await expect(card).toHaveCount(1)
  await expect(card).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".housingRoomCard")
  return card
}

type RoomDetailReference = { roomId: string; asOf: string }

async function verifyRoomAndGetId(
  page: Page,
  roomNumber: string,
): Promise<RoomDetailReference> {
  const card = await findRoomCard(page, roomNumber)
  const verify = card.getByRole("button", { name: `核对房间 ${roomNumber}` })
  await expect(verify).toBeVisible()
  await verify.click()
  await expect(verify).toHaveCount(0)
  await expect(card.getByText("空闲，可入住", { exact: true })).toBeVisible()
  const detailResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET"
      && new URL(response.url()).pathname.endsWith("/detail"),
  )
  await card.getByRole("button", { name: `查看房间 ${roomNumber} 详情` }).click()
  await expect(page.getByRole("heading", { name: `房间 ${roomNumber}` })).toBeVisible()
  const detailResponse = await detailResponsePromise
  expect(detailResponse.status()).toBe(200)
  const detail = (await detailResponse.json()) as { as_of?: unknown }
  if (typeof detail.as_of !== "string") {
    throw new Error("Room detail response is missing as_of")
  }
  const roomMatch = page.url().match(/\/housing\/rooms\/([0-9a-f-]+)$/)
  if (!roomMatch) throw new Error(`Room detail URL missing id: ${page.url()}`)
  return { roomId: roomMatch[1], asOf: detail.as_of }
}

async function openRoomDetailAndReadAsOf(page: Page, roomId: string): Promise<string> {
  const detailResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "GET"
      && new URL(response.url()).pathname === `/api/v1/housing/rooms/${roomId}/detail`,
  )
  await page.goto(`/housing/rooms/${roomId}`)
  const detailResponse = await detailResponsePromise
  expect(detailResponse.status()).toBe(200)
  const detail = (await detailResponse.json()) as { as_of?: unknown }
  if (typeof detail.as_of !== "string") {
    throw new Error(`Room detail ${roomId} response is missing as_of`)
  }
  return detail.as_of
}

async function expectMobileTouchTargets(page: Page): Promise<void> {
  if (viewportWidth(page) !== 360) return
  const boxes = await page
    .locator([
      ".housingRoomDetail button:visible",
      ".housingDialog button:visible",
      ".housingDialog input:not([type='radio']):visible",
      ".housingDialog select:visible",
      ".housingDialog textarea:visible",
      ".housingDialog .housingResidentOptions label:visible",
    ].join(", "))
    .evaluateAll((buttons) =>
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

async function expectRoomLayout(page: Page): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".housingRoomDetail")
  await expectAboveMobileNavigation(page, ".housingRoomDetail__grid")
  await expectMobileTouchTargets(page)
}

async function expectHousingDialog(page: Page, name: string): Promise<Locator> {
  const dialog = page.getByRole("dialog", { name })
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute("aria-modal", "true")
  await expect
    .poll(() => dialog.evaluate((element) => element.contains(document.activeElement)))
    .toBe(true)
  await expectNoHorizontalOverflow(page)
  await expectNoInternalHorizontalOverflow(dialog)
  await expectHorizontallyInsideViewport(page, ".housingDialog")
  await expectAboveMobileNavigation(page, ".housingDialog")
  await expectMobileTouchTargets(page)
  return dialog
}

async function expectDialogFocusRestored(page: Page, dialog: Locator): Promise<void> {
  await expect(dialog).toHaveCount(0)
  if (viewportWidth(page) !== 360) return
  await expect
    .poll(() =>
      page.evaluate(() => {
        const active = document.activeElement
        return active instanceof HTMLElement
          && active.closest('[data-housing-dialog-owner="true"]') !== null
      }),
    )
    .toBe(true)
}

async function roomFact(page: Page, label: string): Promise<Locator> {
  const facts = page.locator(".housingRoomDetail__facts")
  const row = facts.locator("div").filter({ has: page.getByText(label, { exact: true }) })
  await expect(row).toHaveCount(1)
  return row.locator("dd")
}

async function expectRoomFacts(
  page: Page,
  values: { occupancy: string; remaining: string; gender: string },
): Promise<void> {
  await expect(await roomFact(page, "当前入住")).toHaveText(values.occupancy)
  await expect(await roomFact(page, "剩余容量")).toHaveText(values.remaining)
  await expect(await roomFact(page, "入住性别")).toHaveText(values.gender)
}

async function chooseResident(dialog: Locator, name: string): Promise<void> {
  await dialog.getByLabel("搜索入住人员").fill(name)
  const choice = dialog.getByRole("radio", { name: `选择 ${name}` })
  await expect(choice).toBeVisible()
  await choice.check()
  await expect(choice).toBeChecked()
}

async function fillPermanentStay(
  dialog: Locator,
  name: string,
  plannedCheckIn: string,
  notes: string,
): Promise<void> {
  await chooseResident(dialog, name)
  await dialog.getByLabel("住宿类型").selectOption("permanent")
  await dialog.getByLabel("时长类型").selectOption("long_term")
  await dialog.getByLabel("计划入住").fill(plannedCheckIn)
  await dialog.getByLabel("备注").fill(notes)
}

async function expectCurrentRoommate(
  page: Page,
  roomNumber: string,
  roommateName: string,
): Promise<void> {
  const stay = page.locator(".myHousingCurrent .myHousingStayCard")
  await expect(stay.getByText(roomNumber, { exact: true })).toBeVisible()
  await expect(stay.getByText(roommateName, { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".myHousingStayCard")
  await expectAboveMobileNavigation(page, ".myHousingStayCard")
}

test("administrator completes the occupancy lifecycle while portals track roommates", async ({
  page,
}, testInfo) => {
  test.setTimeout(240_000)
  page.setDefaultTimeout(15_000)
  const { source, target } = roomPair(page)
  const issueDescription = `E2E源房问题-${testInfo.project.name}`

  await loginAsAdmin(page)
  await openNavigationItem(page, "房态管理")
  await expect(page.getByRole("heading", { name: "住宿房态" })).toBeVisible()

  const sourceRoom = await verifyRoomAndGetId(page, source)
  await page.goto("/housing")
  const targetRoom = await verifyRoomAndGetId(page, target)
  const sourceRoomId = sourceRoom.roomId
  const targetRoomId = targetRoom.roomId
  const timelineAsOf = targetRoom.asOf
  const pastCheckIn = shanghaiDateTimeInput(timelineAsOf, -60)
  const futureCheckIn = shanghaiDateTimeInput(timelineAsOf, 60)
  const futureCheckOut = shanghaiDateTimeInput(timelineAsOf, 24 * 60)
  const transferAt = shanghaiDateTimeInput(timelineAsOf, -10)
  await page.goto(`/housing/rooms/${sourceRoomId}`)
  await expect(page.getByRole("heading", { name: `房间 ${source}` })).toBeVisible()
  await expectRoomFacts(page, { occupancy: "0 / 2", remaining: "2", gender: "未指定" })
  await expectRoomLayout(page)

  const arrangeCoach = page.getByRole("button", { name: "安排入住" })
  await arrangeCoach.click()
  let dialog = await expectHousingDialog(page, `安排入住 · ${source}`)
  await page.keyboard.press("Escape")
  await expectDialogFocusRestored(page, dialog)
  if (viewportWidth(page) === 360) await expect(arrangeCoach).toBeFocused()

  await arrangeCoach.click()
  dialog = await expectHousingDialog(page, `安排入住 · ${source}`)
  await fillPermanentStay(
    dialog,
    "E2E教学教练",
    pastCheckIn,
    `教练常住-${testInfo.project.name}`,
  )
  await expect(dialog.getByRole("button", { name: "确认入住" })).toBeEnabled()
  await dialog.getByRole("button", { name: "确认入住", exact: true }).click()
  await expectDialogFocusRestored(page, dialog)
  await expectRoomFacts(page, { occupancy: "1 / 2", remaining: "1", gender: "男住" })
  const current = page.getByRole("region", { name: "当前入住" })
  await expect(current.getByText("E2E教学教练", { exact: true })).toBeVisible()

  const arrangeLifeTeacher = page.getByRole("button", { name: "安排入住" })
  await arrangeLifeTeacher.click()
  dialog = await expectHousingDialog(page, `安排入住 · ${source}`)
  await fillPermanentStay(
    dialog,
    "E2E生活老师",
    pastCheckIn,
    `异性冲突-${testInfo.project.name}`,
  )
  const conflictResponsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && response.url().endsWith(`/api/v1/housing/rooms/${sourceRoomId}/stays`),
  )
  await dialog.getByRole("button", { name: "确认入住" }).click()
  const conflictResponse = await conflictResponsePromise
  expect(conflictResponse.status()).toBe(409)
  const conflict = (await conflictResponse.json()) as { code: string }
  expect(conflict.code).toBe("housing_gender_conflict")
  await expect(dialog.getByRole("alert")).toBeVisible()
  await expectRoomFacts(page, { occupancy: "1 / 2", remaining: "1", gender: "男住" })
  await dialog.getByRole("button", { name: "取消" }).click()
  await expectDialogFocusRestored(page, dialog)

  const arrangeStudent = page.getByRole("button", { name: "安排入住" })
  await arrangeStudent.click()
  dialog = await expectHousingDialog(page, `安排入住 · ${source}`)
  await chooseResident(dialog, "E2E自助学员")
  await dialog.getByLabel("住宿类型").selectOption("permanent")
  await dialog.getByLabel("时长类型").selectOption("fixed_term")
  await dialog.getByLabel("计划入住").fill(futureCheckIn)
  await dialog.getByLabel("计划离开").fill(futureCheckOut)
  await dialog.getByLabel("备注").fill(`学员预约-${testInfo.project.name}`)
  await expect(dialog.getByRole("button", { name: "创建预约" })).toBeEnabled()
  await dialog.getByRole("button", { name: "创建预约" }).click()
  await expectDialogFocusRestored(page, dialog)
  const planned = page.getByRole("region", { name: "未来预约" })
  await expect(planned.getByText("E2E自助学员", { exact: true })).toBeVisible()
  const confirmStudent = planned.getByRole("button", {
    name: "确认入住 E2E自助学员",
  })
  await expect(confirmStudent).toBeVisible()
  await confirmStudent.click()
  dialog = await expectHousingDialog(page, "确认入住 · E2E自助学员")
  await dialog.getByRole("button", { name: "确认入住", exact: true }).click()
  await expectDialogFocusRestored(page, dialog)
  await expectRoomFacts(page, { occupancy: "2 / 2", remaining: "0", gender: "男住" })
  await expect(current.getByText("E2E教学教练", { exact: true })).toBeVisible()
  await expect(current.getByText("E2E自助学员", { exact: true })).toBeVisible()
  await expectRoomLayout(page)

  await logout(page)
  await loginAsStudent(page)
  await page.goto("/me/housing")
  await expect(page.getByRole("heading", { name: "E2E自助学员" })).toBeVisible()
  await expectCurrentRoommate(page, source, "E2E教学教练")
  await expectPrivateHousingProjection(page)

  await logout(page)
  await loginAsCoach(page)
  await page.goto("/me/housing")
  await expect(page.getByRole("heading", { name: "E2E自助学员" })).toBeVisible()
  await expectCurrentRoommate(page, source, "E2E教学教练")
  await expectPrivateHousingProjection(page)

  await logout(page)
  await loginAsAdmin(page)
  await page.goto(`/housing/rooms/${sourceRoomId}`)
  await expect(page.getByRole("heading", { name: `房间 ${source}` })).toBeVisible()

  await page.getByRole("button", { name: "上报问题" }).click()
  dialog = await expectHousingDialog(page, `上报问题 · 房间 ${source}`)
  await dialog.getByLabel("问题描述").fill(issueDescription)
  await dialog.getByRole("button", { name: "确认上报" }).click()
  await expectDialogFocusRestored(page, dialog)
  const issues = page.getByRole("region", { name: "问题记录" })
  await expect(issues.getByText(issueDescription, { exact: true })).toBeVisible()
  await expect(issues.getByText("待处理", { exact: true })).toBeVisible()
  await expect(page.getByText("该房间当前为问题房，新增入住已暂停。")).toBeVisible()

  await page.goto("/housing")
  const issueRoomCard = await findRoomCard(page, source)
  await expect(issueRoomCard.getByText("待调房 2 人", { exact: true })).toBeVisible()
  await page.goto(`/housing/rooms/${sourceRoomId}`)
  await expect(page.getByRole("heading", { name: `房间 ${source}` })).toBeVisible()

  const transferCoach = current.getByRole("button", { name: "调房 E2E教学教练" })
  await transferCoach.click()
  dialog = await expectHousingDialog(page, "调房 · E2E教学教练")
  const targetChoice = dialog.getByRole("radio", {
    name: new RegExp(`${escapeRegExp(target)}$`),
  })
  await expect(targetChoice).toBeEnabled()
  await targetChoice.check()
  await dialog.getByLabel("调房时间").fill(transferAt)
  await dialog.getByLabel("调房原因").fill(`问题房原子调房-${testInfo.project.name}`)
  await dialog.getByRole("button", { name: "确认调房" }).click()
  await expectDialogFocusRestored(page, dialog)
  await expect(current.getByText("E2E教学教练", { exact: true })).toHaveCount(0)
  await expect(current.getByText("E2E自助学员", { exact: true })).toBeVisible()
  await expectRoomFacts(page, { occupancy: "1 / 2", remaining: "1", gender: "男住" })

  await page.goto("/housing")
  const partiallyClearedCard = await findRoomCard(page, source)
  await expect(partiallyClearedCard.getByText("待调房 1 人", { exact: true })).toBeVisible()
  await page.goto(`/housing/rooms/${sourceRoomId}`)
  await expect(page.getByRole("heading", { name: `房间 ${source}` })).toBeVisible()

  await issues.getByRole("button", { name: `解决问题 ${issueDescription}` }).click()
  dialog = await expectHousingDialog(page, `解决问题 · 房间 ${source}`)
  await dialog.getByLabel("处理说明").fill(`源房问题已处理-${testInfo.project.name}`)
  await dialog.getByRole("button", { name: "确认解决" }).click()
  await expectDialogFocusRestored(page, dialog)
  await expect(issues.getByText("已解决", { exact: true })).toBeVisible()
  await expect(page.getByText("该房间当前为问题房，新增入住已暂停。")).toHaveCount(0)

  const checkoutAsOf = await openRoomDetailAndReadAsOf(page, targetRoomId)
  await expect(page.getByRole("heading", { name: `房间 ${target}` })).toBeVisible()
  await expectRoomFacts(page, { occupancy: "1 / 2", remaining: "1", gender: "男住" })
  const targetCurrent = page.getByRole("region", { name: "当前入住" })
  const checkout = targetCurrent.getByRole("button", {
    name: "申请退房 E2E教学教练",
  })
  await checkout.click()
  dialog = await expectHousingDialog(page, "申请退房 · E2E教学教练")
  const checkoutAt = shanghaiDateTimeInput(checkoutAsOf, -1)
  await dialog.getByLabel("实际离开时间").fill(checkoutAt)
  await dialog.getByRole("button", { name: "申请退房", exact: true }).click()
  const confirmCheckout = await expectHousingDialog(
    page,
    "二次确认退房 · E2E教学教练",
  )
  await expectRoomFacts(page, { occupancy: "1 / 2", remaining: "1", gender: "男住" })
  await expect(targetCurrent.getByText("待确认退房", { exact: true })).toBeVisible()
  await confirmCheckout
    .getByRole("button", { name: "二次确认退房", exact: true })
    .click()
  await expectDialogFocusRestored(page, confirmCheckout)
  await expectRoomFacts(page, { occupancy: "0 / 2", remaining: "2", gender: "未指定" })
  await expect(targetCurrent.getByText("E2E教学教练", { exact: true })).toHaveCount(0)
  await expectRoomLayout(page)

  await logout(page)
  await loginAsStudent(page)
  await page.goto("/me/housing")
  const studentStay = page.locator(".myHousingCurrent .myHousingStayCard")
  await expect(studentStay.getByText(source, { exact: true })).toBeVisible()
  await expect(studentStay.getByText("暂无室友", { exact: true })).toBeVisible()
  await expect(studentStay.getByText("E2E教学教练", { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".myHousingStayCard")
  await expectAboveMobileNavigation(page, ".myHousingStayCard")
  await expectPrivateHousingProjection(page)
})
