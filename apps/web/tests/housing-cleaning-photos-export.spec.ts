import { execFile } from "node:child_process"
import { promisify } from "node:util"

import { expect, test } from "@playwright/test"
import type { APIRequestContext, Download, Page } from "@playwright/test"

import { e2eCredentialTuple } from "./e2eCredentials"
import { createRequestSession, expectNoHorizontalOverflow, loginAsAdmin } from "./helpers"

const execFileAsync = promisify(execFile)
const PYTHON = process.env.E2E_PYTHON_BIN
const SELF_PERSON = "e1000000-0000-4000-8000-000000000007"
const mutationSessions = new Map<Page, APIRequestContext>()
type Stay = { id: string; person_id: string; person_name: string; version: number; status: string }
type Room = { id: string; version: number; cleaning_status: string; current_occupants: number; is_assignable: boolean }
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
    await page.getByRole("button", { name: "应用筛选", exact: true }).click()
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
        return url.pathname === "/api/v1/housing/overview" && url.searchParams.getAll("cleaning_statuses").includes("pending")
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
  await page.getByRole("checkbox", { name: "待保洁", exact: true }).check()
  await applyPending(() => page.getByRole("button", { name: "应用筛选", exact: true }).click())
  if (mobile) await trigger.click()
  await page.getByRole("button", { name: "重置筛选", exact: true }).click()
  await searchSyntheticRooms()
  await expect(ready).toBeVisible()
  await applyPending(() => page.getByRole("button", { name: "按待保洁筛选房间", exact: true }).click())
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
  await expect(page.getByText("待保洁", { exact: true }).first()).toBeVisible()
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
