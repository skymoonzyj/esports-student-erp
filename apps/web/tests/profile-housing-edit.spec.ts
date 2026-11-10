import { expect, test } from "@playwright/test"
import type { APIRequestContext, Locator, Page } from "@playwright/test"

import type { HousingRoomDetail, HousingStay } from "../src/housing/api"
import {
  createAdminRequest,
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCoach,
  loginAsStudent,
  logout,
} from "./helpers"

// Login and personnel fixtures must never be retained in browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off" })

const SELF_STUDENT_ID = "e1000000-0000-4000-8000-000000000007"
const ROOM_BY_WIDTH: Record<number, string> = {
  1440: "4-201", 1024: "4-205", 768: "4-207", 360: "4-209", 390: "4-211",
}

test.beforeEach(async ({ baseURL }) => {
  for (const origin of [baseURL, process.env.E2E_API_URL]) {
    expect(origin, "explicit disposable loopback origins are required").toBeTruthy()
    const url = new URL(origin!)
    expect(["127.0.0.1", "localhost"]).toContain(url.hostname)
    expect(url.protocol).toBe("http:")
  }
})

function shanghaiDate(asOf: string, offset: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(Date.parse(asOf) + offset * 86_400_000))
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

function shanghaiMidnight(date: string): string {
  return `${date}T00:00:00+08:00`
}

async function readRoom(api: APIRequestContext, roomId: string): Promise<HousingRoomDetail> {
  const response = await api.get(`/api/v1/housing/rooms/${roomId}/detail`)
  expect(response.status()).toBe(200)
  return await response.json() as HousingRoomDetail
}

function stableStay(stay: HousingStay) {
  return {
    id: stay.id, room_id: stay.room_id, person_id: stay.person_id,
    status: stay.status, planned_check_in_at: Date.parse(stay.planned_check_in_at),
    actual_check_in_at: stay.actual_check_in_at === null ? null : Date.parse(stay.actual_check_in_at),
    actual_check_out_at: stay.actual_check_out_at === null ? null : Date.parse(stay.actual_check_out_at),
    notes: stay.notes,
  }
}

async function openDeparture(page: Page): Promise<Locator> {
  await page.getByRole("button", { name: "修改计划离开 E2E自助学员", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "修改计划离开 · E2E自助学员", exact: true })
  await expect(dialog).toBeVisible()
  await expect(dialog.locator('input[type="datetime-local"]')).toHaveCount(0)
  await expect(dialog.getByLabel("住宿期限")).toHaveValue("fixed_term")
  await expect(dialog.getByLabel("计划离开日期", { exact: true })).toBeVisible()
  return dialog
}

async function saveDeparture(page: Page, dialog: Locator, stayId: string): Promise<void> {
  const responsePromise = page.waitForResponse(response =>
    response.request().method() === "PATCH"
    && new URL(response.url()).pathname === `/api/v1/housing/stays/${stayId}/planned-departure`,
  )
  await dialog.getByRole("button", { name: "保存计划离开", exact: true }).click()
  expect((await responsePromise).status()).toBe(200)
  await expect(dialog).toHaveCount(0)
}

test("administrator persists another person's nickname and internal notes while student self views stay private", async ({ page }, testInfo) => {
  test.setTimeout(90_000)
  const nickname = `浏览器昵称-${testInfo.project.name}`
  const note = `仅后台可见-${testInfo.project.name}`
  await loginAsAdmin(page)
  await page.goto(`/people/${SELF_STUDENT_ID}`)
  await expect(page.getByRole("heading", { name: "E2E自助学员", level: 1, exact: true })).toBeVisible()
  await page.getByRole("button", { name: "编辑昵称与内部备注", exact: true }).click()
  await page.getByRole("textbox", { name: "昵称", exact: true }).fill(nickname)
  await page.getByRole("textbox", { name: "内部备注", exact: true }).fill(note)
  await page.getByRole("button", { name: "保存昵称与备注", exact: true }).click()
  await expect(page.locator(".staffProfileNotes")).toHaveText(note)
  await page.reload()
  await expect(page.locator(".staffProfileNotes")).toHaveText(note)
  await expect(page.locator(".personDetailNickname")).toHaveText(`昵称：${nickname}`)
  await expectNoHorizontalOverflow(page)

  const general = await page.request.get(`/api/v1/people/${SELF_STUDENT_ID}`)
  expect(general.status()).toBe(200)
  expect(await general.text()).not.toContain("profile_notes")
  await expect(page.getByRole("button", { name: "退出登录", exact: true }).filter({ visible: true }).first()).toBeVisible()
  await logout(page)
  await loginAsStudent(page)
  await page.goto("/me/profile")
  await expect(page.getByRole("heading", { name: "基本信息", exact: true })).toBeVisible()
  await expect(page.locator(".selfProfilePanel").getByText(nickname, { exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "内部备注", exact: true })).toHaveCount(0)
  await expect(page.getByText(note, { exact: true })).toHaveCount(0)
  const own = await page.request.get("/api/v1/people/me/profile")
  expect(own.status()).toBe(200)
  expect(await own.text()).not.toContain("profile_notes")
  expect((await page.request.get(`/api/v1/people/${SELF_STUDENT_ID}/staff-profile`)).status()).toBe(403)
})

test("checked-in planned departure changes through date-only UI without replacing stay or releasing its bed", async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  const { admin, csrfToken } = await createAdminRequest()
  try {
    const roomNumber = ROOM_BY_WIDTH[page.viewportSize()?.width ?? 1440]
    expect(roomNumber).toBeTruthy()
    const overviewResponse = await admin.get(`/api/v1/housing/overview?query=${encodeURIComponent(roomNumber)}`)
    expect(overviewResponse.status()).toBe(200)
    const overview = await overviewResponse.json() as { rooms: Array<{ id: string; room_number: string; version: number; verification_status: string }> }
    const room = overview.rooms.find(value => value.room_number === roomNumber)
    expect(room).toBeTruthy()
    if (!room) throw new Error("canonical disposable room fixture is missing")
    if (room.verification_status === "pending_verification") {
      const verified = await admin.post(`/api/v1/housing/rooms/${room.id}/verify`, {
        headers: { "X-CSRF-Token": csrfToken }, data: { version: room.version },
      })
      expect(verified.status()).toBe(200)
    }
    const before = await readRoom(admin, room.id)
    expect(before.current_stays).toHaveLength(0)
    const checkIn = shanghaiMidnight(shanghaiDate(before.as_of, -2))
    const firstEnd = shanghaiMidnight(shanghaiDate(before.as_of, 3))
    const nextDate = shanghaiDate(before.as_of, 5)
    const finalDate = shanghaiDate(before.as_of, 7)
    const createdResponse = await admin.post(`/api/v1/housing/rooms/${room.id}/stays/backfill`, {
      headers: { "X-CSRF-Token": csrfToken },
      data: {
        person_id: SELF_STUDENT_ID, stay_purpose: "permanent", duration_mode: "fixed_term",
        planned_check_in_at: checkIn, planned_check_out_at: firstEnd,
        notes: `日历离开验证-${testInfo.project.name}`,
      },
    })
    expect(createdResponse.status()).toBe(201)
    const original = await createdResponse.json() as HousingStay
    expect(original.status).toBe("checked_in")
    expect(original.actual_check_in_at).not.toBeNull()

    await loginAsAdmin(page)
    await page.goto(`/housing/rooms/${room.id}`)
    await expect(page.getByRole("heading", { name: `房间 ${roomNumber}`, exact: true })).toBeVisible()
    let dialog = await openDeparture(page)
    await expect(dialog.getByLabel("计划离开日期", { exact: true })).toHaveAttribute("type", "date")
    await expect(dialog.getByLabel("计划离开日期", { exact: true })).toHaveValue(shanghaiDate(before.as_of, 3))
    await expect(dialog.getByRole("button", { name: "保存计划离开", exact: true })).toBeEnabled()
    await dialog.getByLabel("计划离开日期", { exact: true }).fill(nextDate)
    await saveDeparture(page, dialog, original.id)
    await page.reload()
    await expect(page.getByRole("heading", { name: `房间 ${roomNumber}`, level: 1, exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "修改计划离开 E2E自助学员", exact: true })).toBeVisible()
    let detail = await readRoom(admin, room.id)
    expect(detail.current_stays).toHaveLength(1)
    let updated = detail.current_stays[0]
    expect(stableStay(updated)).toEqual(stableStay(original))
    expect(Date.parse(updated.planned_check_out_at!)).toBe(Date.parse(shanghaiMidnight(nextDate)))
    expect(updated.version).toBe(original.version + 1)
    expect(detail.room.current_occupants).toBe(1)
    expect(detail.room.remaining_capacity).toBe(detail.room.capacity - 1)
    expect(detail.history).toHaveLength(0)
    expect(detail.transfers).toHaveLength(0)

    dialog = await openDeparture(page)
    await dialog.getByLabel("住宿期限").selectOption("long_term")
    await expect(dialog.getByLabel("计划离开日期", { exact: true })).toHaveCount(0)
    await saveDeparture(page, dialog, original.id)
    detail = await readRoom(admin, room.id)
    updated = detail.current_stays[0]
    expect(stableStay(updated)).toEqual(stableStay(original))
    expect(updated.duration_mode).toBe("long_term")
    expect(updated.planned_check_out_at).toBeNull()
    expect(detail.room.current_occupants).toBe(1)
    expect(detail.room.remaining_capacity).toBe(detail.room.capacity - 1)

    dialog = await openDeparture(page)
    await expect(dialog.getByLabel("计划离开日期", { exact: true })).toHaveValue("")
    await expect(dialog.getByRole("button", { name: "保存计划离开", exact: true })).toBeDisabled()
    await dialog.getByLabel("计划离开日期", { exact: true }).fill(finalDate)
    await saveDeparture(page, dialog, original.id)
    await page.reload()
    await expect(page.getByRole("heading", { name: `房间 ${roomNumber}`, level: 1, exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: "修改计划离开 E2E自助学员", exact: true })).toBeVisible()
    detail = await readRoom(admin, room.id)
    updated = detail.current_stays[0]
    expect(stableStay(updated)).toEqual(stableStay(original))
    expect(updated.duration_mode).toBe("fixed_term")
    expect(Date.parse(updated.planned_check_out_at!)).toBe(Date.parse(shanghaiMidnight(finalDate)))
    expect(updated.version).toBe(original.version + 3)
    expect(detail.current_stays).toHaveLength(1)
    expect(detail.history).toHaveLength(0)
    expect(detail.transfers).toHaveLength(0)
    expect(detail.room.current_occupants).toBe(1)
    expect(detail.room.remaining_capacity).toBe(detail.room.capacity - 1)
    await expectNoHorizontalOverflow(page)

    await expect(page.getByRole("button", { name: "退出登录", exact: true }).filter({ visible: true }).first()).toBeVisible()
    await logout(page)
    await loginAsCoach(page)
    await page.goto("/me/housing")
    await expect(page.locator(".myHousingCurrent").getByText(roomNumber, { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: /修改计划离开|保存计划离开/ })).toHaveCount(0)
    expect((await page.request.get(`/api/v1/housing/rooms/${room.id}/detail`)).status()).toBe(404)
    await page.goto(`/housing/rooms/${room.id}`)
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole("button", { name: /修改计划离开|保存计划离开/ })).toHaveCount(0)
  } finally {
    await admin.dispose()
  }
})
