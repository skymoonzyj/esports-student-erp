import { execFile } from "node:child_process"
import { promisify } from "node:util"

import { expect, test } from "@playwright/test"
import type { APIRequestContext, Download, Locator, Page, Request } from "@playwright/test"

import { e2eCredentialTuple } from "./e2eCredentials"
import { createRequestSession, expectNoHorizontalOverflow, loginAsAdmin, loginWithIdentifier } from "./helpers"

const execFileAsync = promisify(execFile)
const PYTHON = process.env.E2E_PYTHON_BIN
const SELF_PERSON = "e1000000-0000-4000-8000-000000000007"
const mutationSessions = new Map<Page, APIRequestContext>()
type Stay = { id: string; person_id: string; person_name: string; version: number; status: string }
type Room = { id: string; building_id: string; floor_id: string; version: number; verification_status: string; service_status: string; cleaning_status: string; cleaning_frequency: string; notes: string | null; current_occupants: number; is_assignable: boolean }
type Detail = { room: Room; current_stays: Stay[]; photos: { id: string; url: string; thumbnail_url: string }[] }

test.use({ trace: "off", screenshot: "off", video: "off" })
test.describe.configure({ mode: "serial" })

async function signIn(page: Page): Promise<string> {
  await loginAsAdmin(page)
  // Full browser navigation rotates that browser session's CSRF. Keep API
  // fixture/assertion writes in their own session, like the older housing E2Es.
  const session = await createRequestSession(...e2eCredentialTuple("admin"))
  mutationSessions.set(page, session.api)
  return session.csrfToken
}

test.afterEach(async ({ page }) => {
  await mutationSessions.get(page)?.dispose()
  mutationSessions.delete(page)
})

async function room(page: Page, number: string): Promise<Room> {
  const response = await page.request.get(`/api/v1/housing/overview?query=${number}`)
  expect(response.status()).toBe(200)
  const body = await response.json() as { rooms: Room[] }
  expect(body.rooms).toHaveLength(1)
  return body.rooms[0]
}

// A tall mobile panel need not fit vertically all at once: its heading and
// controls must be reachable by normal scrolling without horizontal clipping.
async function expectPanelLayout(page: Page, panel: Locator) {
  await expect(panel).toBeVisible()
  const heading = panel.getByRole("heading").first()
  await heading.scrollIntoViewIfNeeded()
  await expect(heading).toBeInViewport()
  const bounds = await panel.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(-1)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual((page.viewportSize()?.width ?? 0) + 1)
  const lastControl = panel.locator("button, input, select, textarea").last()
  await lastControl.scrollIntoViewIfNeeded()
  await expect(lastControl).toBeInViewport()
  await expectNoHorizontalOverflow(page)
}

async function detail(page: Page, id: string): Promise<Detail> {
  const response = await page.request.get(`/api/v1/housing/rooms/${id}/detail`)
  expect(response.status()).toBe(200)
  return await response.json() as Detail
}

async function post(page: Page, csrf: string, path: string, data: unknown, status = 200) {
  const api = mutationSessions.get(page)
  if (!api) throw new Error("Missing independent mutation session")
  const response = await api.post(`/api/v1/housing/${path}`, {
    headers: { "x-csrf-token": csrf }, data,
  })
  const problemCode = response.status() === status ? "" :
    String((await response.json() as { code?: string }).code ?? "unknown_problem")
  expect(response.status(), problemCode).toBe(status)
  return response
}

async function complete(page: Page, id: string, number: string) {
  await page.goto(`/housing/rooms/${id}`)
  await page.getByRole("button", { name: "完成保洁", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: `完成保洁 ${number}` })
  await expect(dialog).toBeVisible()
  await dialog.getByRole("button", { name: "确认完成保洁" }).click()
  await expect(dialog).toHaveCount(0)
  await expect.poll(async () => (await detail(page, id)).room.cleaning_status).toBe("ready")
  await expectNoHorizontalOverflow(page)
}

async function checkout(page: Page, csrf: string, stay: Stay) {
  const requested = await post(page, csrf, `stays/${stay.id}/request-checkout`, {
    version: stay.version, actual_check_out_at: new Date().toISOString(),
  })
  const pending = await requested.json() as Stay
  await post(page, csrf, `stays/${stay.id}/confirm-checkout`, { version: pending.version })
}

async function expectPendingFiltersExcludeReadyRoom(page: Page) {
  expect((await room(page, "4-204")).cleaning_status).toBe("ready")
  await page.goto("/housing")
  const trigger = page.getByRole("button", { name: "打开筛选", exact: true })
  const mobile = (page.viewportSize()?.width ?? 1440) < 720
  async function searchSyntheticRooms() {
    if (mobile) await trigger.click()
    await page.getByRole("textbox", { name: "搜索房间", exact: true }).fill("4-20")
    await expect(page.getByRole("heading", { name: "4-204", exact: true })).toBeVisible()
    if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
  }
  await searchSyntheticRooms()
  const ready = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "4-204", exact: true }) })
  const pending = page.getByRole("article").filter({ has: page.getByRole("heading", { name: "4-201", exact: true }) })
  await expect(ready).toBeVisible()
  await expect(pending).toBeVisible()
  async function applyPending(action: () => Promise<unknown>) {
    const [response] = await Promise.all([
      page.waitForResponse((response) => {
        const url = new URL(response.url())
        return url.pathname === "/api/v1/housing/overview" && (url.searchParams.getAll("cleaning_statuses").includes("pending") || url.searchParams.get("quick_filter") === "pending_cleaning")
      }),
      action(),
    ])
    expect(response.status()).toBe(200)
    const body = await response.json() as { rooms: Room[] }
    expect(body.rooms.length).toBeGreaterThan(0)
    expect(body.rooms.every((room) => room.cleaning_status === "pending")).toBe(true)
    await expect(pending).toBeVisible()
    await expect(ready).toHaveCount(0)
  }
  if (mobile) await trigger.click()
  await applyPending(() => page.getByRole("checkbox", { name: "退房待保洁", exact: true }).check())
  if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
  if (mobile) await trigger.click()
  await page.getByRole("button", { name: "重置筛选", exact: true }).click()
  await searchSyntheticRooms()
  await expect(ready).toBeVisible()
  await applyPending(() => page.getByRole("button", { name: "按退房待保洁筛选房间", exact: true }).click())
}

test("actual checkout blocks admission until explicit cleaning; transfer dirties only the source", async ({ page }) => {
  const csrf = await signIn(page)
  const source = await room(page, "4-201")
  await page.goto(`/housing/rooms/${source.id}`)
  const requestCheckout = page.getByRole("region", { name: "当前入住" }).getByRole("button", {
    name: /^申请退房 /,
  })
  await requestCheckout.click()
  const dialog = page.getByRole("dialog", { name: /^申请退房 ·/ })
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date())
  await dialog.getByLabel("实际离开日期").fill(today)
  await dialog.getByRole("button", { name: "申请退房", exact: true }).click()
  await page.getByRole("dialog", { name: /^二次确认退房 ·/ }).getByRole("button", {
    name: "二次确认退房", exact: true,
  }).click()
  await expect.poll(async () => (await detail(page, source.id)).room.cleaning_status).toBe("pending")
  const pending = await detail(page, source.id)
  expect(pending.room.current_occupants).toBe(0)
  expect(pending.room.is_assignable).toBe(false)
  await expectPendingFiltersExcludeReadyRoom(page)
  const admission = {
    person_id: SELF_PERSON, stay_purpose: "permanent", duration_mode: "long_term",
    planned_check_in_at: new Date().toISOString(),
  }
  await post(page, csrf, `rooms/${source.id}/stays`, admission, 409)
  await complete(page, source.id, "4-201")
  const admitted = await (await post(page, csrf, `rooms/${source.id}/stays`, admission, 201)).json() as Stay
  // The existing create contract immediately checks in starts in the past/now.
  expect(admitted.status).toBe("checked_in")
  const checkedIn = (await detail(page, source.id)).current_stays[0]
  const target = await room(page, "4-203")
  await post(page, csrf, `stays/${checkedIn.id}/transfer`, {
    version: checkedIn.version, target_room_id: target.id,
    transferred_at: new Date().toISOString(), reason: "Synthetic care browser transfer",
  })
  expect((await detail(page, source.id)).room.cleaning_status).toBe("pending")
  expect((await detail(page, target.id)).room.cleaning_status).toBe("ready")
  expect((await detail(page, target.id)).room.current_occupants).toBe(1)
})

test("each departure from a double room increments the room epoch while preserving the remaining resident", async ({ page }) => {
  const csrf = await signIn(page)
  const double = await room(page, "4-202")
  const before = await detail(page, double.id)
  expect(before.current_stays).toHaveLength(2)
  await checkout(page, csrf, before.current_stays[0])
  const first = await detail(page, double.id)
  expect(first.room.cleaning_status).toBe("pending")
  expect(first.current_stays.map((stay) => stay.id)).toEqual([before.current_stays[1].id])
  expect(first.room.version).toBeGreaterThan(before.room.version)
  await page.goto(`/housing/rooms/${double.id}`)
  await expect(page.getByText("退房待保洁", { exact: true }).first()).toBeVisible()
  await checkout(page, csrf, first.current_stays[0])
  const second = await detail(page, double.id)
  expect(second.room.cleaning_status).toBe("pending")
  expect(second.room.version).toBeGreaterThan(first.room.version)
  expect(second.current_stays).toHaveLength(0)
  await complete(page, double.id, "4-202")
})

async function png(color: string): Promise<Buffer> {
  if (!PYTHON) throw new Error("E2E_PYTHON_BIN is required")
  const { stdout } = await execFileAsync(PYTHON, ["-c",
    "import sys; from PIL import Image; Image.new('RGB',(640,480),sys.argv[1]).save(sys.stdout.buffer,format='PNG')", color,
  ], { encoding: "buffer" })
  return stdout
}

test("private room photos upload, replace, enlarge and delete through the actual detail UI", async ({ page }) => {
  await signIn(page)
  const current = await room(page, "4-206")
  await page.goto(`/housing/rooms/${current.id}`)
  const input = page.getByLabel("选择第 1 张房间照片")
  await input.setInputFiles({ name: "synthetic-blue.png", mimeType: "image/png", buffer: await png("blue") })
  await page.getByRole("button", { name: "上传第 1 张照片" }).click()
  await expect.poll(async () => (await detail(page, current.id)).photos.length).toBe(1)
  const first = (await detail(page, current.id)).photos[0]
  const firstContent = await page.request.get(first.url)
  expect(firstContent.status()).toBe(200)
  expect(firstContent.headers()["cache-control"]).toContain("no-store")
  const firstBytes = await firstContent.body()
  await input.setInputFiles({ name: "synthetic-red.png", mimeType: "image/png", buffer: await png("red") })
  await page.getByRole("button", { name: "上传第 1 张照片" }).click()
  await expect.poll(async () => (await detail(page, current.id)).room.version).toBeGreaterThan(current.version + 1)
  const changed = (await detail(page, current.id)).photos[0]
  expect(await (await page.request.get(changed.url)).body()).not.toEqual(firstBytes)
  await page.getByRole("button", { name: "放大房间照片 1" }).click()
  const lightbox = page.getByRole("dialog", { name: "房间照片 1", exact: true })
  await expect(lightbox).toBeVisible()
  await expect(lightbox.getByRole("img")).toBeVisible()
  await page.getByRole("button", { name: "关闭房间照片 1" }).click()
  await expect(lightbox).toHaveCount(0)
  await page.getByRole("button", { name: "删除第 1 张照片" }).click()
  await expect.poll(async () => (await detail(page, current.id)).photos.length).toBe(0)
  expect((await page.request.get(changed.url)).status()).toBe(404)
  await expectNoHorizontalOverflow(page)
})

async function parseDownload(download: Download, format: "xlsx" | "csv"): Promise<Record<string, unknown>[]> {
  if (!PYTHON) throw new Error("E2E_PYTHON_BIN is required")
  const path = await download.path()
  if (!path) throw new Error("Download was not retained")
  const code = `import csv,io,json,sys,zipfile
from openpyxl import load_workbook
if sys.argv[2]=='xlsx':
    book=load_workbook(io.BytesIO(open(sys.argv[1],'rb').read()),read_only=True,data_only=True)
    tables=[list(sheet.values) for sheet in book.worksheets]
    rows=next(table for table in tables if table and '房号' in table[0])
else:
    raw=open(sys.argv[1],'rb').read()
    if raw[:2]==b'PK':
        archive=zipfile.ZipFile(io.BytesIO(raw)); raw=archive.read(next(name for name in archive.namelist() if name.endswith('.csv')))
    rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'))))
print(json.dumps([dict(zip(rows[0],row)) for row in rows[1:]],ensure_ascii=False,default=str))`
  const { stdout, stderr } = await execFileAsync(PYTHON, ["-c", code, path, format], { encoding: "utf8" })
  expect(stderr).toBe("")
  return JSON.parse(stdout) as Record<string, unknown>[]
}

async function exportRows(page: Page, type: "snapshot" | "history", format: "xlsx" | "csv") {
  await page.goto("/housing")
  await page.getByRole("button", { name: "导出", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: "导出房态" })
  await expect(dialog.getByLabel("导出类型")).toHaveValue("snapshot")
  await dialog.getByLabel("导出类型").selectOption(type)
  await dialog.getByLabel("导出用途（必填）").fill("Synthetic housing-care browser validation")
  await dialog.getByLabel("文件格式").selectOption(format)
  const [download] = await Promise.all([
    page.waitForEvent("download"), dialog.getByRole("button", { name: "开始导出" }).click(),
  ])
  return await parseDownload(download, format)
}

test("downloaded current snapshot contains empty rooms and only current people while historical export stays compatible", async ({ page }) => {
  await signIn(page)
  const rows = await exportRows(page, "snapshot", "xlsx")
  const empty = rows.filter((row) => row["房号"] === "4-202")
  expect(empty).toHaveLength(1)
  expect(empty[0]["入住人"] ?? "").toBe("")
  const occupied = rows.filter((row) => row["房号"] === "4-206")
  expect(occupied).toHaveLength(1)
  expect(occupied[0]["入住人"]).toBeTruthy()
  expect(occupied[0]["计划离开日期"] ?? "").toBe("")
  expect(rows.filter((row) => row["房号"] === "4-201")[0]["入住人"] ?? "").toBe("")
  expect(rows.some((row) => row["房号"] === "4-203" && row["入住人"])).toBe(true)
  const csv = await exportRows(page, "snapshot", "csv")
  expect(csv.map((row) => [row["房号"], row["入住人"] ?? ""]).sort()).toEqual(
    rows.map((row) => [row["房号"], row["入住人"] ?? ""]).sort(),
  )
  const history = await exportRows(page, "history", "xlsx")
  expect(history.some((row) => row["房号"] === "4-201" && row["入住人"])).toBe(true)
  expect(history.filter((row) => row["房号"] === "4-202")).toHaveLength(2)
  expect(Object.keys(history[0])).toEqual([
    "房号", "入住人", "性别", "身份证号", "手机号", "入住时间", "离开时间", "人员类型", "账号状态", "档案来源",
  ])
  await expectNoHorizontalOverflow(page)
})

test("actual care roles remain scoped and coach is not elevated by room permissions", async ({ page }) => {
  await signIn(page)
  const target = await room(page, "4-204")
  for (const role of ["customer_service", "life_teacher", "coach", "ready_male_student"] as const) {
    const session = await createRequestSession(...e2eCredentialTuple(role))
    try {
      const response = await session.api.patch(`/api/v1/housing/rooms/${target.id}`, {
        headers: { "x-csrf-token": session.csrfToken },
        data: { version: (await room(page, "4-204")).version, daily_cleaning_enabled: true },
      })
      // Existing owner_only isolation hides complete room details from coaches,
      // even with global permission overrides; both denied actors get private404.
      expect(response.status()).toBe(role === "coach" || role === "ready_male_student" ? 404 : 200)
      if (role === "coach" || role === "ready_male_student") {
        const deniedRead = await session.api.get(`/api/v1/housing/rooms/${target.id}/detail`)
        expect(deniedRead.status()).toBe(404)
      }
    } finally {
      await session.api.dispose()
    }
  }
})

test("bulk room status is available without catalog expansion and conflicts roll back every selected room", async ({ page }) => {
  const csrf = await signIn(page)
  const base = await room(page, "4-204")
  const prefix = `BULK-${page.viewportSize()?.width ?? 1440}`
  const numbers = [`${prefix}-1`, `${prefix}-2`]
  const created: Room[] = []
  for (const number of numbers) {
    created.push(await (await post(page, csrf, "rooms", {
      floor_id: base.floor_id, room_number: number, room_type: "single", capacity: 1,
    }, 201)).json() as Room)
  }
  await page.goto("/housing")
  const directory = page.getByRole("button", { name: "目录管理", exact: true })
  await expect(directory).toBeVisible()
  await expect(page.getByRole("heading", { name: "目录管理", exact: true })).toHaveCount(0)
  if ((page.viewportSize()?.width ?? 1440) < 720) {
    await page.getByRole("button", { name: "打开筛选", exact: true }).click()
  }
  await page.getByRole("textbox", { name: "搜索房间", exact: true }).fill(prefix)
  await expect(page.getByText("筛选结果 2 间", { exact: true })).toBeVisible()
  if ((page.viewportSize()?.width ?? 1440) < 720) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
  const operation = page.getByRole("combobox", { name: "批量操作", exact: true })
  await expect(operation).toBeVisible()
  const select = async () => {
    for (const number of numbers) {
      await page.getByRole("checkbox", { name: `选择房间 ${number}`, exact: true }).check()
    }
  }
  const confirm = async (button: string) => {
    await page.getByRole("button", { name: button, exact: true }).click()
    const dialog = page.getByRole("dialog", { name: "确认批量操作" })
    await expect(dialog).toBeVisible()
    await dialog.getByRole("button", { name: "确认执行", exact: true }).click()
  }
  await select()
  await confirm("批量核对 2 间房")
  await expect.poll(async () => Promise.all(created.map(async (value) =>
    (await detail(page, value.id)).room.verification_status))).toEqual(["verified", "verified"])
  await expect(page.getByRole("dialog", { name: "确认批量操作" })).toHaveCount(0)

  // The default action is still verification. Already verified, assignable
  // rooms must nevertheless be selectable before choosing a service action.
  await select()
  await expect(page.getByRole("button", { name: "批量核对 2 间房", exact: true })).toBeDisabled()

  for (const [value, label] of [["maintenance", "维修"], ["disabled", "停用"], ["available", "可用"]] as const) {
    await operation.selectOption(value)
    for (const number of numbers) {
      await expect(page.getByRole("checkbox", { name: `选择房间 ${number}`, exact: true })).toBeChecked()
    }
    await confirm(`批量设为${label} 2 间房`)
    await expect.poll(async () => Promise.all(created.map(async (item) =>
      (await detail(page, item.id)).room.service_status))).toEqual([value, value])
    await expect(page.getByRole("dialog", { name: "确认批量操作" })).toHaveCount(0)
    await select()
  }

  await operation.selectOption("maintenance")
  await page.getByRole("button", { name: "批量设为维修 2 间房", exact: true }).click()
  await page.getByRole("dialog", { name: "确认批量操作" }).getByRole("button", { name: "取消", exact: true }).click()
  expect((await detail(page, created[0].id)).room.service_status).toBe("available")
  const selectedBefore = await Promise.all(created.map(async (item) => (await detail(page, item.id)).room))
  const api = mutationSessions.get(page)!
  const changed = await api.patch(`/api/v1/housing/rooms/${created[1].id}`, {
    headers: { "x-csrf-token": csrf }, data: { version: selectedBefore[1].version, notes: "Synthetic concurrent bulk edit" },
  })
  expect(changed.status()).toBe(200)
  const failure = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/housing/rooms/batch-service-status") && response.request().method() === "POST")
  await confirm("批量设为维修 2 间房")
  expect((await failure).status()).toBe(409)
  await expect(page.getByText("批量修改未执行，请处理以下房间后重试", { exact: true })).toBeVisible()
  await expect(page.getByText(new RegExp(`${numbers[1]}.*(版本|变化|更新)`))).toBeVisible()
  const after = await Promise.all(created.map(async (item) => (await detail(page, item.id)).room))
  expect(after.map((item) => item.service_status)).toEqual(["available", "available"])
  expect(after[0].version).toBe(selectedBefore[0].version)
  expect(after[1].version).toBe(selectedBefore[1].version + 1)
  await expect(page.getByRole("heading", { name: "目录管理", exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
})

test("persisted frequencies, property room-card reminders and reviewed proposals work through the real browser and API", async ({ page, browser, baseURL }, info) => {
  test.setTimeout(180_000)
  expect(baseURL && ["127.0.0.1", "localhost"].includes(new URL(baseURL).hostname)).toBe(true)
  await signIn(page)
  const occupied = await room(page, "4-206")
  const pendingVerification = await room(page, "4-205")
  const resident = (await detail(page, occupied.id)).current_stays[0]
  expect(resident).toBeTruthy()
  const mobile = (page.viewportSize()?.width ?? 1440) < 720

  await test.step("save a new frequency and export the persisted filtered set in both export kinds", async () => {
    await page.goto("/housing")
    if (mobile) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
    await page.getByRole("combobox", { name: "楼栋", exact: true }).selectOption(occupied.building_id)
    await page.getByRole("textbox", { name: "搜索房间", exact: true }).fill("4-206")
    await expect(page.getByText("筛选结果 1 间", { exact: true })).toBeVisible()
    if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
    await page.getByRole("button", { name: "编辑房间 4-206", exact: true }).click()
    const edit = page.getByRole("dialog", { name: "编辑房间 4-206", exact: true })
    await edit.getByRole("combobox", { name: "保洁频率", exact: true }).selectOption("weekly", { timeout: 10_000 })
    await edit.getByRole("button", { name: "保存房间", exact: true }).click()
    await expect(edit).toHaveCount(0)
    await expect.poll(async () => (await detail(page, occupied.id)).room.cleaning_frequency).toBe("weekly")
    expect((await detail(page, occupied.id)).room.cleaning_status).toBe(occupied.cleaning_status)
    await page.goto(`/housing/rooms/${occupied.id}`)
    await page.reload()
    await expect(page.getByText("每周保洁", { exact: true })).toBeVisible()

    await page.goto("/housing")
    if (mobile) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
    await page.getByRole("combobox", { name: "楼栋", exact: true }).selectOption(occupied.building_id)
    await page.getByRole("textbox", { name: "搜索房间", exact: true }).fill("4-206")
    // Wait for the debounced search before closing the mobile filter sheet or
    // selecting a shortcut; neither may cancel or overtake the applied query.
    await expect(page.getByText("筛选结果 1 间", { exact: true })).toBeVisible()
    await Promise.all([
      page.waitForResponse(response => {
        const url = new URL(response.url())
        return url.pathname === "/api/v1/housing/overview" && url.searchParams.get("query") === "4-206" && url.searchParams.getAll("cleaning_frequency").includes("weekly") && !url.searchParams.has("quick_filter")
      }),
      page.getByRole("listbox", { name: "保洁频率", exact: true }).selectOption("weekly"),
    ])
    if (mobile) await page.getByRole("button", { name: "关闭筛选", exact: true }).click()
    const [filtered] = await Promise.all([
      page.waitForResponse(response => {
        const url = new URL(response.url())
        return url.pathname === "/api/v1/housing/overview" && url.searchParams.get("quick_filter") === "cleaning_weekly"
      }),
      page.getByRole("button", { name: "按每周保洁筛选房间", exact: true }).click(),
    ])
    expect(filtered.status()).toBe(200)
    const filteredUrl = new URL(filtered.url())
    expect(filteredUrl.searchParams.get("building_id")).toBe(occupied.building_id)
    expect(filteredUrl.searchParams.getAll("cleaning_frequency")).toEqual(["weekly"])
    const filteredBody = await filtered.json() as { rooms: Room[]; summary: { occupied_rooms: number; current_occupants: number; weekly_cleaning_rooms: number } }
    expect(filteredBody.rooms.map(value => value.id)).toEqual([occupied.id])
    expect(filteredBody.summary).toMatchObject({ occupied_rooms: 1, current_occupants: 1, weekly_cleaning_rooms: 1 })
    for (const [kind, format] of [["snapshot", "xlsx"], ["history", "csv"]] as const) {
      await page.getByRole("button", { name: "导出", exact: true }).click()
      const dialog = page.getByRole("dialog", { name: "导出房态", exact: true })
      await dialog.getByLabel("导出类型").selectOption(kind)
      await expect(dialog.getByLabel(kind === "snapshot" ? "导出保洁频率" : "历史导出保洁频率", { exact: true })).toHaveValues(["weekly"])
      await dialog.getByLabel("导出用途（必填）").fill("Synthetic frequency persistence validation")
      await dialog.getByLabel("文件格式", { exact: true }).selectOption(format)
      const [download, response] = await Promise.all([
        page.waitForEvent("download"),
        page.waitForResponse(value => value.request().method() === "POST" && new URL(value.url()).pathname === (kind === "snapshot" ? "/api/v1/housing/room-status/exports" : "/api/v1/housing/exports")),
        dialog.getByRole("button", { name: "开始导出", exact: true }).click(),
      ])
      expect(response.status()).toBe(200)
      expect(response.request().postDataJSON()).toMatchObject({ cleaning_frequencies: ["weekly"], quick_filter: "cleaning_weekly", query: "4-206", ...(kind === "snapshot" ? { building_ids: [occupied.building_id] } : { building_id: occupied.building_id }) })
      const rows = await parseDownload(download, format)
      expect(rows).toHaveLength(1)
      expect(rows[0]["房号"]).toBe("4-206")
      expect(rows[0]["入住人"]).toBe(resident.person_name)
      if (kind === "snapshot") expect(rows[0]["保洁频率"]).toBe("每周保洁")
      await expect(dialog).toHaveCount(0)
    }
  })

  const browserOptions = { baseURL, viewport: page.viewportSize(), isMobile: info.project.use.isMobile, hasTouch: info.project.use.hasTouch }
  const propertyContext = await browser.newContext(browserOptions)
  const administrationContext = await browser.newContext(browserOptions)
  const superAdminContext = await browser.newContext(browserOptions)
  const coachContext = await browser.newContext(browserOptions)
  const propertySession = await createRequestSession(...e2eCredentialTuple("visitor"))
  const appearanceSession = await createRequestSession(...e2eCredentialTuple("life_teacher"))
  type Appearance = { theme: "clear" | "command"; revision: number }
  const readAppearance = async (): Promise<Appearance> => {
    const response = await appearanceSession.api.get("/api/v1/operations/appearance")
    expect(response.status()).toBe(200)
    return await response.json() as Appearance
  }
  const originalAppearance = await readAppearance()
  const setAppearance = async (theme: Appearance["theme"]) => {
    const current = await readAppearance()
    const response = await appearanceSession.api.put("/api/v1/operations/appearance", {
      headers: { "x-csrf-token": appearanceSession.csrfToken },
      data: { theme, expected_revision: current.revision },
    })
    expect(response.status()).toBe(200)
    expect(await response.json()).toMatchObject({ theme })
  }
  try {
    const propertyPage = await propertyContext.newPage()
    const administrationPage = await administrationContext.newPage()
    const superAdminPage = await superAdminContext.newPage()
    const coachPage = await coachContext.newPage()
    await loginWithIdentifier(propertyPage, ...e2eCredentialTuple("visitor"))
    await loginWithIdentifier(administrationPage, ...e2eCredentialTuple("customer_service"))
    await loginWithIdentifier(superAdminPage, ...e2eCredentialTuple("life_teacher"))
    await loginWithIdentifier(coachPage, ...e2eCredentialTuple("coach"))

    await test.step("real clear and command themes preserve role-only export and workspace-only approval layout", async () => {
      const panelColors: string[] = []
      for (const theme of ["clear", "command"] as const) {
        await setAppearance(theme)
        await propertyPage.goto("/housing")
        await propertyPage.reload()
        await expect(propertyPage.locator("html")).toHaveAttribute("data-ui-theme", theme)
        const exports = propertyPage.getByRole("region", { name: "物业基础导出", exact: true })
        await expectPanelLayout(propertyPage, exports)
        panelColors.push(await exports.evaluate(element => getComputedStyle(element).color))
        await expect(propertyPage.getByRole("region", { name: "物业变更申请", exact: true })).toHaveCount(0)
        await expect(propertyPage.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
        await propertyPage.goto("/")
        await expect(propertyPage.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
        await expect(propertyPage.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
        await propertyPage.goto(`/housing/rooms/${pendingVerification.id}`)
        await expectPanelLayout(propertyPage, propertyPage.getByRole("region", { name: "物业变更申请", exact: true }))
        await expect(propertyPage.getByRole("button", { name: "编辑房间", exact: true })).toHaveCount(0)
        await expect(propertyPage.getByRole("button", { name: "通过申请", exact: true })).toHaveCount(0)

        for (const reviewer of [administrationPage, superAdminPage]) {
          await reviewer.goto("/housing")
          await expect(reviewer.locator("html")).toHaveAttribute("data-ui-theme", theme)
          await expect(reviewer.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
          await expect(reviewer.getByRole("region", { name: "物业基础导出", exact: true })).toHaveCount(0)
          await expect(reviewer.getByRole("region", { name: "物业变更申请", exact: true })).toHaveCount(0)
          await expect(reviewer.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
          await reviewer.goto(`/housing/rooms/${pendingVerification.id}`)
          await expect(reviewer.getByRole("button", { name: "编辑房间", exact: true })).toBeVisible()
          await expect(reviewer.getByRole("button", { name: "提交待审申请", exact: true })).toHaveCount(0)
          await expect(reviewer.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
          await reviewer.goto("/")
          await expectPanelLayout(reviewer, reviewer.getByRole("region", { name: "物业变更审批", exact: true }))
          await expect(reviewer.getByRole("button", { name: "提交待审申请", exact: true })).toHaveCount(0)
        }
        await page.goto("/housing")
        await expect(page.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
        await expect(page.getByRole("region", { name: "物业基础导出", exact: true })).toHaveCount(0)
        await page.goto("/")
        await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
        await expect(page.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
        await coachPage.reload()
        await expect(coachPage.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
        await expect(coachPage.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
      }
      // Confirm the real theme stylesheet changed the panel, not just its DOM label.
      expect(panelColors[0]).not.toBe(panelColors[1])
    })

    await test.step("administration edits room detail immediately without a proposal or automatic review", async () => {
      const target = await room(page, "4-204")
      const proposals = async () => {
        const response = await administrationPage.request.get(`/api/v1/housing/property/changes?room_id=${target.id}&view=review&page_size=100`)
        expect(response.status()).toBe(200)
        return await response.json() as { total: number; items: unknown[] }
      }
      const beforeProposals = await proposals()
      await administrationPage.goto(`/housing/rooms/${target.id}`)
      await expect(administrationPage.getByRole("button", { name: "提交待审申请", exact: true })).toHaveCount(0)
      await expect(administrationPage.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
      const postedProposals: string[] = []
      const trackProposal = (request: Request) => {
        if (request.method() === "POST" && new URL(request.url()).pathname.includes("change-requests")) postedProposals.push(request.url())
      }
      administrationPage.on("request", trackProposal)
      try {
        await administrationPage.getByRole("button", { name: "编辑房间", exact: true }).click()
        const edit = administrationPage.getByRole("dialog", { name: "编辑房间 4-204", exact: true })
        await edit.getByRole("combobox", { name: "服务状态", exact: true }).selectOption("disabled")
        await edit.getByRole("textbox", { name: "备注", exact: true }).fill("Synthetic administration immediate save")
        const [saved] = await Promise.all([
          administrationPage.waitForResponse(response => response.request().method() === "PATCH" && new URL(response.url()).pathname === `/api/v1/housing/rooms/${target.id}`),
          edit.getByRole("button", { name: "保存房间", exact: true }).click(),
        ])
        expect(saved.status()).toBe(200)
        await expect(edit).toHaveCount(0)
        expect((await detail(page, target.id)).room).toMatchObject({ service_status: "disabled", notes: "Synthetic administration immediate save", version: target.version + 1 })
        await administrationPage.reload()
        const persisted = await administrationPage.request.get(`/api/v1/housing/rooms/${target.id}/detail`)
        expect(persisted.status()).toBe(200)
        expect((await persisted.json() as Detail).room).toMatchObject({ service_status: "disabled", notes: "Synthetic administration immediate save" })
        expect(await proposals()).toEqual(beforeProposals)
        expect(postedProposals).toEqual([])
        await expectNoHorizontalOverflow(administrationPage)
      } finally {
        administrationPage.off("request", trackProposal)
      }
    })

    await test.step("property saves real room-card dates and receives its own D+7 reminder", async () => {
      const clockResponse = await propertyPage.request.get("/api/v1/portal/room-card-reminders")
      expect(clockResponse.status()).toBe(200)
      const clock = await clockResponse.json() as { business_date: string }
      const expiry = new Date(`${clock.business_date}T00:00:00Z`)
      expiry.setUTCDate(expiry.getUTCDate() + 7)
      const expiresOn = expiry.toISOString().slice(0, 10)
      await propertyPage.goto(`/housing/rooms/${occupied.id}`)
      const cards = propertyPage.getByRole("region", { name: "住客房卡日期", exact: true })
      await cards.getByLabel(`${resident.person_name}开卡日`, { exact: true }).fill(clock.business_date)
      await cards.getByLabel(`${resident.person_name}到期日`, { exact: true }).fill(expiresOn)
      const [saved] = await Promise.all([
        propertyPage.waitForResponse(response => response.request().method() === "PUT" && new URL(response.url()).pathname === `/api/v1/housing/stays/${resident.id}/room-card`),
        cards.getByRole("button", { name: `保存${resident.person_name}的房卡日期`, exact: true }).click(),
      ])
      expect(saved.status()).toBe(200)
      expect(await saved.json()).toMatchObject({ stay_id: resident.id, issued_on: clock.business_date, expires_on: expiresOn, version: 1 })
      await propertyPage.reload()
      await expect(cards.getByLabel(`${resident.person_name}到期日`, { exact: true })).toHaveValue(expiresOn)
      const persistedCards = await propertyPage.request.get(`/api/v1/housing/rooms/${occupied.id}/room-cards`)
      expect(persistedCards.status()).toBe(200)
      expect((await persistedCards.json() as { items: unknown[] }).items).toEqual([expect.objectContaining({ stay_id: resident.id, issued_on: clock.business_date, expires_on: expiresOn })])
      await propertyPage.goto("/")
      const reminders = propertyPage.getByRole("region", { name: "房卡到期提醒", exact: true })
      await expect(reminders.getByRole("heading", { name: resident.person_name, exact: true })).toBeVisible()
      await expect(reminders.getByText("7 天后到期", { exact: true })).toBeVisible()
      const reminderResponse = await propertyPage.request.get("/api/v1/portal/room-card-reminders")
      expect(reminderResponse.status()).toBe(200)
      expect((await reminderResponse.json() as { items: unknown[] }).items).toEqual([expect.objectContaining({ stay_id: resident.id, expires_on: expiresOn, days_remaining: 7 })])
      await expectNoHorizontalOverflow(propertyPage)
    })

    await test.step("property cannot mutate directly; administration approval alone applies the proposed status and notes", async () => {
      await propertyPage.goto(`/housing/rooms/${pendingVerification.id}`)
      await expect(propertyPage.getByRole("button", { name: "安排入住", exact: true })).toHaveCount(0)
      await expect(propertyPage.getByRole("button", { name: "上报问题", exact: true })).toHaveCount(0)
      await expect(propertyPage.getByRole("button", { name: "完成保洁", exact: true })).toHaveCount(0)
      const before = (await detail(page, pendingVerification.id)).room
      const denied = await propertySession.api.patch(`/api/v1/housing/rooms/${before.id}`, {
        headers: { "x-csrf-token": propertySession.csrfToken }, data: { version: before.version, service_status: "disabled", notes: "Synthetic forbidden bypass" },
      })
      expect(denied.status()).toBe(403)
      expect((await detail(page, before.id)).room).toMatchObject({ version: before.version, service_status: before.service_status, notes: before.notes })
      const changes = propertyPage.getByRole("region", { name: "物业变更申请", exact: true })
      const withdrawnReason = "Synthetic own withdrawal leaves room unchanged"
      await changes.getByRole("checkbox", { name: "变更备注 / 问题说明", exact: true }).check()
      await changes.getByRole("textbox", { name: "建议备注 / 问题说明", exact: true }).fill("Synthetic withdrawn note")
      await changes.getByRole("textbox", { name: "申请原因", exact: true }).fill(withdrawnReason)
      const [withdrawalSubmission] = await Promise.all([
        propertyPage.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/housing/rooms/${before.id}/change-requests`),
        changes.getByRole("button", { name: "提交待审申请", exact: true }).click(),
      ])
      expect(withdrawalSubmission.status()).toBe(201)
      const withdrawal = await withdrawalSubmission.json() as { id: string; status: string }
      expect(withdrawal.status).toBe("pending")
      const [withdrawn] = await Promise.all([
        propertyPage.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/housing/change-requests/${withdrawal.id}/withdraw`),
        changes.getByRole("article").filter({ hasText: withdrawnReason }).getByRole("button", { name: "撤回申请", exact: true }).click(),
      ])
      expect(withdrawn.status()).toBe(200)
      expect(await withdrawn.json()).toMatchObject({ id: withdrawal.id, status: "withdrawn" })
      await changes.getByRole("combobox", { name: "申请状态", exact: true }).selectOption("withdrawn")
      await expect(changes.getByRole("article").filter({ hasText: withdrawnReason }).getByText("已撤回（未生效）", { exact: true })).toBeVisible()
      expect((await detail(page, before.id)).room).toMatchObject({ version: before.version, service_status: before.service_status, notes: before.notes })
      await changes.getByRole("combobox", { name: "申请状态", exact: true }).selectOption("pending")
      const proposedNotes = "Synthetic reviewed property note"
      const reason = `Synthetic property browser review ${page.viewportSize()?.width ?? 1440}`
      await changes.getByRole("checkbox", { name: "变更服务状态", exact: true }).check()
      await changes.getByRole("combobox", { name: "建议服务状态", exact: true }).selectOption("maintenance", { timeout: 10_000 })
      await changes.getByRole("checkbox", { name: "变更备注 / 问题说明", exact: true }).check()
      await changes.getByRole("textbox", { name: "建议备注 / 问题说明", exact: true }).fill(proposedNotes)
      await changes.getByRole("textbox", { name: "申请原因", exact: true }).fill(reason)
      const [submitted] = await Promise.all([
        propertyPage.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/housing/rooms/${before.id}/change-requests`),
        changes.getByRole("button", { name: "提交待审申请", exact: true }).click(),
      ])
      expect(submitted.status()).toBe(201)
      const request = await submitted.json() as { id: string; status: string }
      expect(request.status).toBe("pending")
      expect((await detail(page, before.id)).room).toMatchObject({ version: before.version, service_status: before.service_status, notes: before.notes })
      const ownPending = changes.getByRole("article").filter({ hasText: reason })
      await expect(ownPending.getByText("待审核（未生效）", { exact: true })).toBeVisible()
      await expect(ownPending.getByRole("button", { name: "撤回申请", exact: true })).toBeVisible()
      await expect(changes.getByRole("button", { name: "通过申请", exact: true })).toHaveCount(0)
      await expect(propertyPage.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
      const selfReview = await propertySession.api.post(`/api/v1/housing/change-requests/${request.id}/review`, {
        headers: { "x-csrf-token": propertySession.csrfToken }, data: { decision: "approve" },
      })
      expect(selfReview.status()).toBe(403)
      expect((await detail(page, before.id)).room.version).toBe(before.version)
      await administrationPage.goto("/housing")
      await expect(administrationPage.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
      await expect(administrationPage.getByRole("region", { name: "物业变更审批", exact: true })).toHaveCount(0)
      await administrationPage.goto("/")
      const approvals = administrationPage.getByRole("region", { name: "物业变更审批", exact: true })
      await expectPanelLayout(administrationPage, approvals)
      const application = approvals.getByRole("article").filter({ hasText: reason })
      await expect(application.getByText("待审核（未生效）", { exact: true })).toBeVisible()
      await application.getByRole("button", { name: "通过申请", exact: true }).click()
      const [approved] = await Promise.all([
        administrationPage.waitForResponse(response => response.request().method() === "POST" && new URL(response.url()).pathname === `/api/v1/housing/change-requests/${request.id}/review`),
        application.getByRole("button", { name: "确认通过并生效", exact: true }).click(),
      ])
      expect(approved.status()).toBe(200)
      expect(await approved.json()).toMatchObject({ id: request.id, status: "approved" })
      await expect(administrationPage).toHaveURL(baseURL + "/")
      await expect.poll(async () => (await detail(page, before.id)).room.service_status).toBe("maintenance")
      expect((await detail(page, before.id)).room).toMatchObject({ notes: proposedNotes, verification_status: "verified", version: before.version + 1, cleaning_frequency: before.cleaning_frequency, cleaning_status: before.cleaning_status })
      await propertyPage.reload()
      const ownChanges = propertyPage.getByRole("region", { name: "物业变更申请", exact: true })
      await ownChanges.getByRole("combobox", { name: "申请状态", exact: true }).selectOption("approved", { timeout: 10_000 })
      await expect(ownChanges.getByRole("article").filter({ hasText: reason }).getByText("已通过（已生效）", { exact: true })).toBeVisible()
      await expectNoHorizontalOverflow(propertyPage)
      await expectNoHorizontalOverflow(administrationPage)
    })
  } finally {
    try {
      await setAppearance(originalAppearance.theme)
      expect((await readAppearance()).theme).toBe(originalAppearance.theme)
    } finally {
      await Promise.all([
        appearanceSession.api.dispose(), propertySession.api.dispose(),
        propertyContext.close(), administrationContext.close(),
        superAdminContext.close(), coachContext.close(),
      ])
    }
  }
})
