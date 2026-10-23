import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"
import type { APIRequestContext, Locator, Page } from "@playwright/test"

import type { HousingRoomDetail, HousingStay } from "../src/housing/api"
import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
  loginAsAdmin,
  loginAsCoach,
  makeCnId,
} from "./helpers"

// Login and personnel records must not be retained in browser artifacts.
test.use({ trace: "off", screenshot: "off", video: "off", actionTimeout: 15_000 })

const STUDENT_ID = "e1000000-0000-4000-8000-000000000007"
const COACH_ID = "e1000000-0000-4000-8000-000000000006"
const COACH_ACCOUNT_ID = "e1100000-0000-4000-8000-000000000006"
const ROOMMATE_ID = "e1000000-0000-4000-8000-000000000010"
const PEOPLE_READ_PERMISSION_ID = "20000000-0000-4000-8000-000000000002"
const ROOM_PAIRS: Record<number, [string, string]> = {
  1440: ["4-201", "4-202"],
  1024: ["4-205", "4-206"],
  768: ["4-207", "4-208"],
  360: ["4-209", "4-211"],
}

test.beforeEach(async ({ baseURL }) => {
  for (const origin of [baseURL, process.env.E2E_API_URL]) {
    expect(origin, "explicit disposable loopback origins are required").toBeTruthy()
    const url = new URL(origin!)
    expect(["127.0.0.1", "localhost"]).toContain(url.hostname)
    expect(url.protocol).toBe("http:")
  }
})

function roomsFor(page: Page): [string, string] {
  const pair = ROOM_PAIRS[page.viewportSize()?.width ?? 1440]
  if (!pair) throw new Error("Missing canonical room pair for this viewport")
  return pair
}

function dateInShanghai(asOf: string, offset: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(Date.parse(asOf) + offset * 86_400_000))
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

async function readRoom(api: APIRequestContext, roomId: string): Promise<HousingRoomDetail> {
  const response = await api.get(`/api/v1/housing/rooms/${roomId}/detail`)
  expect(response.status()).toBe(200)
  return await response.json() as HousingRoomDetail
}

async function verifiedRoom(api: APIRequestContext, csrfToken: string, roomNumber: string): Promise<HousingRoomDetail> {
  const response = await api.get(`/api/v1/housing/overview?query=${encodeURIComponent(roomNumber)}`)
  expect(response.status()).toBe(200)
  const overview = await response.json() as { rooms: Array<{ id: string; room_number: string; version: number; verification_status: string }> }
  const room = overview.rooms.find(value => value.room_number === roomNumber)
  if (!room) throw new Error("Canonical disposable room fixture is missing")
  if (room.verification_status === "pending_verification") {
    const verified = await api.post(`/api/v1/housing/rooms/${room.id}/verify`, {
      headers: { "X-CSRF-Token": csrfToken }, data: { version: room.version },
    })
    expect(verified.status()).toBe(200)
  }
  return await readRoom(api, room.id)
}

async function expectLayout(page: Page, region: Locator): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectNoInternalHorizontalOverflow(region)
  const box = await region.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual(page.viewportSize()!.width + 1)
}

async function expectDialogLayout(page: Page, dialog: Locator): Promise<void> {
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute("aria-modal", "true")
  await expectLayout(page, dialog)
  await expectHorizontallyInsideViewport(page, ".housingDialog")
  await expectAboveMobileNavigation(page, ".housingDialog")
}

// Catches missing person binding, lost create/transfer persistence and displaying a
// closed stay as current. Both mutations are observed through independent room APIs.
test("administrator arranges and transfers the fixed person from personnel detail with persisted current housing", async ({ page }) => {
  test.setTimeout(120_000)
  const { admin, csrfToken } = await createAdminRequest()
  try {
    const [sourceNumber, targetNumber] = roomsFor(page)
    const source = await verifiedRoom(admin, csrfToken, sourceNumber)
    const target = await verifiedRoom(admin, csrfToken, targetNumber)
    expect(source.current_stays).toHaveLength(0)
    expect(target.current_stays).toHaveLength(0)
    await loginAsAdmin(page)
    await page.goto(`/people/${STUDENT_ID}`)
    await expect(page.getByRole("heading", { name: "E2E自助学员", exact: true, level: 1 })).toBeVisible()
    const housing = page.getByRole("region", { name: "住宿信息", exact: true })
    await expect(housing.getByText("当前未入住", { exact: true })).toBeVisible()
    await housing.getByRole("button", { name: "安排入住", exact: true }).click()
    await expect(page.getByLabel("选择房间")).toBeVisible()
    await page.getByLabel("选择房间").selectOption(source.room.id)
    await page.getByRole("button", { name: "下一步", exact: true }).click()
    const create = page.getByRole("dialog", { name: `安排入住 · ${sourceNumber}`, exact: true })
    await expectDialogLayout(page, create)
    await expect(create.getByText("E2E自助学员", { exact: true })).toBeVisible()
    await expect(create.getByLabel("搜索入住人员")).toHaveCount(0)
    await create.getByLabel("住宿类型").selectOption("permanent")
    await create.getByLabel("时长类型").selectOption("long_term")
    await create.getByLabel("计划入住日期").fill(dateInShanghai(source.as_of, -1))
    const createdResponse = page.waitForResponse(response => response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/v1/housing/rooms/${source.room.id}/stays`)
    await create.getByRole("button", { name: "确认入住", exact: true }).click()
    const response = await createdResponse
    expect(response.status()).toBe(201)
    const original = await response.json() as HousingStay
    expect(original.person_id).toBe(STUDENT_ID)
    expect(original.status).toBe("checked_in")
    await expect(create).toHaveCount(0)
    const current = housing.getByRole("region", { name: "当前住宿", exact: true })
    await expect(current).toContainText(sourceNumber)
    await expect(current).toContainText("已入住")
    await expectLayout(page, housing)
    await housing.getByRole("button", { name: "修改房间", exact: true }).click()
    const transfer = page.getByRole("dialog", { name: "调房 · E2E自助学员", exact: true })
    await expectDialogLayout(page, transfer)
    await transfer.getByRole("radio", { name: new RegExp(` · ${targetNumber}$`) }).check()
    await transfer.getByLabel("调房日期").fill(dateInShanghai(source.as_of, 0))
    await transfer.getByLabel("调房原因").fill("人员详情浏览器验收调房")
    const transferredResponse = page.waitForResponse(value => value.request().method() === "POST"
      && new URL(value.url()).pathname === `/api/v1/housing/stays/${original.id}/transfer`)
    await transfer.getByRole("button", { name: "确认调房", exact: true }).click()
    const transferred = await transferredResponse
    expect(transferred.status()).toBe(200)
    const replacement = await transferred.json() as HousingStay
    expect(replacement.id).not.toBe(original.id)
    expect(replacement.person_id).toBe(STUDENT_ID)
    expect(replacement.room_id).toBe(target.room.id)
    await expect(transfer).toHaveCount(0)
    await expect(current).toContainText(targetNumber)
    await expect(housing).not.toContainText(sourceNumber)
    await page.reload()
    await expect(current).toContainText(targetNumber)
    await expect(current).toContainText("已入住")
    await expect(housing.getByRole("button", { name: "修改房间", exact: true })).toBeVisible()
    await expect(housing.getByText(/历史|同住人员/)).toHaveCount(0)
    await expectLayout(page, housing)
    const oldRoom = await readRoom(admin, source.room.id)
    const newRoom = await readRoom(admin, target.room.id)
    expect(oldRoom.current_stays).toHaveLength(0)
    expect(oldRoom.history.map(stay => ({ id: stay.id, status: stay.status }))).toEqual([{ id: original.id, status: "checked_out" }])
    expect(oldRoom.history[0].actual_check_out_at).not.toBeNull()
    expect(newRoom.current_stays.map(stay => ({ id: stay.id, person_id: stay.person_id }))).toEqual([{ id: replacement.id, person_id: STUDENT_ID }])
    expect(newRoom.room.current_occupants).toBe(1)
    expect(oldRoom.transfers).toEqual(expect.arrayContaining([expect.objectContaining({ from_stay_id: original.id, to_stay_id: replacement.id, person_id: STUDENT_ID })]))
  } finally {
    await admin.dispose()
  }
})

// Catches widening managed-own access into room/personnel data or edit authority.
test("coach with exact managed-person access sees read-only housing without roommate or room-detail data", async ({ page }) => {
  test.setTimeout(90_000)
  const { admin, csrfToken } = await createAdminRequest()
  let overrideId: string | undefined
  try {
    // Independent of the administrator test, including when its transfer fails.
    const sourceNumber = "4-203"
    const source = await verifiedRoom(admin, csrfToken, sourceNumber)
    expect(source.current_stays).toHaveLength(0)
    const createdPerson = await admin.post("/api/v1/personnel-intake/direct-create", {
      headers: { "X-CSRF-Token": csrfToken },
      data: {
        idempotency_key: randomUUID(), kind: "student", full_name: "E2E人员住房只读学员",
        identity_number: makeCnId("20000102", 881), phone: "19900008881", email: null,
        sex: "male", date_of_birth: "2000-01-02", guardians: [], coach_person_ids: [COACH_ID],
      },
    })
    expect(createdPerson.status()).toBe(201)
    const managedPersonId = (await createdPerson.json() as { person_id: string }).person_id
    for (const personId of [managedPersonId, ROOMMATE_ID]) {
      const response = await admin.post(`/api/v1/housing/rooms/${source.room.id}/stays/backfill`, {
        headers: { "X-CSRF-Token": csrfToken },
        data: { person_id: personId, stay_purpose: "permanent", duration_mode: "long_term", planned_check_in_at: `${dateInShanghai(source.as_of, -1)}T00:00:00+08:00`, notes: "不能泄露的同住房间备注" },
      })
      expect(response.status()).toBe(201)
    }
    const grant = await admin.post(`/api/v1/accounts/${COACH_ACCOUNT_ID}/permission-overrides`, {
      headers: { "X-CSRF-Token": csrfToken },
      data: { permission_id: PEOPLE_READ_PERMISSION_ID, effect: "allow", scope_kind: "person", scope_id: managedPersonId, grant_reason: "人员详情只读住宿浏览器验收" },
    })
    expect(grant.status()).toBe(201)
    overrideId = (await grant.json() as { id: string }).id
    await loginAsCoach(page)
    await page.goto(`/people/${managedPersonId}`)
    await expect(page.getByRole("heading", { name: "E2E人员住房只读学员", exact: true, level: 1 })).toBeVisible()
    const housing = page.getByRole("region", { name: "住宿信息", exact: true })
    await expect(housing.getByRole("region", { name: "当前住宿", exact: true })).toContainText(sourceNumber)
    await expect(housing.getByRole("button", { name: /安排入住|修改房间/ })).toHaveCount(0)
    await expect(housing).not.toContainText("E2E监护就绪男学员")
    await expect(housing).not.toContainText("不能泄露的同住房间备注")
    await expectLayout(page, housing)
    const ownResponse = await page.request.get(`/api/v1/people/${managedPersonId}/housing`)
    expect(ownResponse.status()).toBe(200)
    const own = await ownResponse.json() as Record<string, unknown>
    expect(own.management_stay).toBeNull()
    expect(own.allowed_actions).toEqual({ create_stay: false })
    for (const key of ["history", "current_stays", "transfers", "roommates", "issues"]) expect(own).not.toHaveProperty(key)
    expect(JSON.stringify(own)).not.toContain(ROOMMATE_ID)
    expect(JSON.stringify(own)).not.toContain("不能泄露的同住房间备注")
    expect((await page.request.get(`/api/v1/housing/rooms/${source.room.id}/detail`)).status()).toBe(404)
    expect([403, 404]).toContain((await page.request.get(`/api/v1/people/${ROOMMATE_ID}`)).status())
    expect([403, 404]).toContain((await page.request.get(`/api/v1/people/${ROOMMATE_ID}/housing`)).status())
    expect([403, 404]).toContain((await page.request.get(`/api/v1/people/${managedPersonId}/housing/check-in-options`)).status())
    await page.reload()
    await expect(housing.getByRole("region", { name: "当前住宿", exact: true })).toContainText(sourceNumber)
    await expect(housing.getByRole("button", { name: /安排入住|修改房间/ })).toHaveCount(0)
  } finally {
    if (overrideId) {
      const removed = await admin.delete(`/api/v1/accounts/${COACH_ACCOUNT_ID}/permission-overrides/${overrideId}`, { headers: { "X-CSRF-Token": csrfToken } })
      expect(removed.status()).toBe(204)
    }
    await admin.dispose()
  }
})
