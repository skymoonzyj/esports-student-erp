import { execFile } from "node:child_process"
import path from "node:path"
import { promisify } from "node:util"

import { expect, test } from "@playwright/test"
import type { APIRequestContext, Download, Locator, Page, TestInfo } from "@playwright/test"

import type { HousingRoomDetail } from "../src/housing/api"
import { e2eCredentialTuple } from "./e2eCredentials"
import {
  createAdminRequest,
  createRequestSession,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
  loginAsAdmin,
  loginAsCustomerService,
  loginWithIdentifier,
  refocus,
} from "./helpers"

const execFileAsync = promisify(execFile)
const SOURCE_STUDENT = "e2900000-0000-4000-8000-000000000001"
const SOURCE_COACH = "e2900000-0000-4000-8000-000000000003"
const MUTABLE_ACCOUNT = "e1100000-0000-4000-8000-000000000002"
const REASON = "E2E虚构历史重复登记核对"
const STUDENT = "E2E自助学员"
const COACH = "E2E教学教练"
type ParsedReport = Record<string, Array<Record<string, string>>>
type Batch = { id: string; batch_number: string; status: string; version: number }

test.beforeEach(async ({ page }) => {
  page.setDefaultTimeout(15_000)
})

async function roomId(api: APIRequestContext, number: string): Promise<string> {
  const response = await api.get(`/api/v1/housing/overview?query=${number}`)
  expect(response.status()).toBe(200)
  const body = await response.json() as { rooms: Array<{ id: string; room_number: string }> }
  expect(body.rooms).toHaveLength(1)
  expect(body.rooms[0].room_number).toBe(number)
  return body.rooms[0].id
}

async function detail(api: APIRequestContext, id: string, voided = false): Promise<HousingRoomDetail> {
  const response = await api.get(`/api/v1/housing/rooms/${id}/detail${voided ? "?include_voided=true" : ""}`)
  expect(response.status()).toBe(200)
  return await response.json() as HousingRoomDetail
}

async function openRoom(page: Page, id: string, number: string): Promise<void> {
  await page.goto(`/housing/rooms/${id}`)
  await expect(page.getByRole("heading", { name: `房间 ${number}`, exact: true })).toBeVisible()
}

async function dialogLayout(page: Page, dialog: Locator): Promise<void> {
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute("aria-modal", "true")
  await expectNoHorizontalOverflow(page)
  await expectNoInternalHorizontalOverflow(dialog)
  await expectHorizontallyInsideViewport(page, ".housingDialog")
  await expectAboveMobileNavigation(page, ".housingDialog")
}

async function prepareVoid(page: Page, name: string, reason: string): Promise<Locator> {
  await page.getByRole("button", { name: `作废历史住宿 ${name}`, exact: true }).click()
  const dialog = page.getByRole("dialog", { name: `作废历史住宿 · ${name}` })
  await dialogLayout(page, dialog)
  const next = dialog.getByRole("button", { name: "下一步：核对历史住宿" })
  await expect(next).toBeDisabled()
  const input = dialog.getByRole("textbox", { name: "作废原因", exact: true })
  await input.fill(reason)
  await expect(input).toHaveValue(reason)
  await expect(input).toBeInViewport()
  await next.click()
  await expect(dialog.getByText("只作废此条历史，不删除人员、账号和其他住宿", { exact: true })).toBeVisible()
  await expect(dialog.getByText("已生成报送不会自动改变", { exact: true })).toBeVisible()
  await expect(dialog.getByText(name, { exact: true })).toBeVisible()
  await expect(dialog.locator("dl").getByText("2026-08-01", { exact: true })).toBeVisible()
  await expect(dialog.locator("dl").getByText("2026-08-10", { exact: true })).toBeVisible()
  await expect(dialog.getByText(/来源住宿/)).toBeVisible()
  const confirm = dialog.getByRole("button", { name: "确认作废历史住宿", exact: true })
  await expect(confirm).toBeDisabled()
  await dialog.getByRole("checkbox", { name: "我已核对目标历史住宿并确认作废" }).check()
  await dialogLayout(page, dialog)
  await confirm.scrollIntoViewIfNeeded()
  await expect(confirm).toBeInViewport()
  return dialog
}

async function inspect(download: Download, format: "xlsx" | "csv"): Promise<ParsedReport> {
  const python = process.env.E2E_PYTHON_BIN
  if (!python) throw new Error("E2E_PYTHON_BIN is required for report inspection")
  const file = await download.path()
  if (!file) throw new Error("Browser download was not retained")
  const result = await execFileAsync(python, [
    path.resolve("tests/support/inspect-housing-report.py"), "--format", format, file,
  ])
  expect(result.stderr).toBe("")
  const report = JSON.parse(result.stdout) as ParsedReport
  expect(Object.keys(report).sort()).toEqual(["入住名单", "未成年监护人"].sort())
  return report
}

async function reportDownload(page: Page, batch: Batch, format: "xlsx" | "csv"): Promise<ParsedReport> {
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", {
      name: `下载 ${batch.batch_number} ${format === "xlsx" ? "Excel" : "CSV"}`,
    }).click(),
  ])
  return inspect(download, format)
}

// This must exercise the browser's native segmented editor, not fill() with an
// invalid whole string or a synthetic React onChange event.
async function typeNativeYear(
  page: Page, dialog: Locator, input: Locator, testInfo: TestInfo, name: string,
): Promise<void> {
  const baseline = "2026-09-20"
  await input.fill(baseline)
  await input.focus()
  let yearSelected = false
  const selectionProbes: string[] = []
  const values: string[] = []
  for (let index = 0; index < 4; index += 1) {
    await input.press("ArrowUp")
    const probe = await input.inputValue()
    selectionProbes.push(probe)
    await input.press("ArrowDown")
    expect(await input.inputValue()).toBe(baseline)
    if (probe === "2027-09-20") { yearSelected = true; break }
    await input.press("ArrowRight")
  }
  if (!yearSelected) {
    await testInfo.attach(`native-year-${name}`, {
      body: JSON.stringify({ baseline, selectionProbes, values }), contentType: "application/json",
    })
    throw new Error("Native year segment could not be selected; no acceptance claimed")
  }
  try {
    for (const digit of ["2", "0", "2", "6"]) {
      await input.press(digit)
      values.push(await input.inputValue())
      await expect(dialog).toBeVisible()
    }
    // At least one real intermediate year must differ from a completed 2026 date.
    expect(values.some((value) => /^00\d{2}-09-20$/.test(value))).toBe(true)
  } finally {
    await testInfo.attach(`native-year-${name}`, {
      body: JSON.stringify({ baseline, selectionProbes, values }), contentType: "application/json",
    })
  }
  await input.fill(baseline)
  await expect(input).toHaveValue(baseline)
  await expect(dialog).toBeVisible()
  await page.keyboard.press("Tab")
}

test("native year-segment typing keeps both housing date dialogs mounted without submitting", async ({ page }, testInfo) => {
  test.setTimeout(180_000)
  const pageErrors: string[] = []
  const housingPosts: string[] = []
  page.on("pageerror", (error) => pageErrors.push(error.name))
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname.startsWith("/api/v1/housing/")) {
      housingPosts.push(new URL(request.url()).pathname)
    }
  })
  const { admin } = await createAdminRequest()
  try {
    const source = await roomId(admin, "4-201")
    const target = await roomId(admin, "4-202")
    await loginAsAdmin(page)
    await openRoom(page, source, "4-201")
    await page.getByRole("button", { name: "安排入住", exact: true }).click()
    let dialog = page.getByRole("dialog", { name: "安排入住 · 4-201" })
    // The regression is in date draft rendering, and requires no resident mutation.
    await dialog.getByLabel("住宿类型").selectOption("permanent")
    await dialog.getByLabel("时长类型").selectOption("long_term")
    await typeNativeYear(page, dialog, dialog.getByRole("textbox", { name: "计划入住日期", exact: true }), testInfo, "move-in")
    expect(pageErrors).toEqual([])
    expect(housingPosts).toEqual([])
    await dialog.getByRole("button", { name: "取消", exact: true }).click()
    await openRoom(page, target, "4-202")
    await page.getByRole("button", { name: `修改计划离开 ${STUDENT}`, exact: true }).click()
    dialog = page.getByRole("dialog", { name: `修改计划离开 · ${STUDENT}` })
    await dialog.getByLabel("住宿期限").selectOption("fixed_term")
    await typeNativeYear(page, dialog, dialog.getByRole("textbox", { name: "计划离开日期", exact: true }), testInfo, "planned-departure")
    await expect(dialog.getByRole("button", { name: "保存计划离开" })).toBeEnabled()
    expect(pageErrors).toEqual([])
    expect(housingPosts).toEqual([])
    await dialog.getByRole("button", { name: "取消", exact: true }).click()
  } finally { await admin.dispose() }
})

test("operational administrator has no history-void UI and the real action is denied", async ({ page }) => {
  const { admin } = await createAdminRequest()
  const operator = await createRequestSession(...e2eCredentialTuple("life_teacher"))
  try {
    const source = await roomId(admin, "4-201")
    await loginWithIdentifier(page, ...e2eCredentialTuple("life_teacher"))
    await openRoom(page, source, "4-201")
    await expect(page.getByRole("region", { name: "住宿历史", exact: true }).getByText(STUDENT, { exact: true })).toBeVisible()
    await expect(page.getByRole("button", { name: /^作废历史住宿 / })).toHaveCount(0)
    await expect(page.getByRole("checkbox", { name: "查看已作废历史" })).toHaveCount(0)
    const response = await operator.api.post(`/api/v1/housing/stays/${SOURCE_STUDENT}/void-history`, {
      headers: { "X-CSRF-Token": operator.csrfToken },
      data: { expected_version: 1, reason: REASON, confirmed: true, idempotency_key: crypto.randomUUID() },
    })
    expect(response.status()).toBe(403)
    expect((await detail(admin, source)).history.some((stay) => stay.id === SOURCE_STUDENT)).toBe(true)
  } finally { await operator.api.dispose(); await admin.dispose() }
})

test("super administrator voids one transfer source, refreshes tombstone, and downloads the unchanged sealed report", async ({ page }) => {
  test.setTimeout(180_000)
  const { admin, csrfToken } = await createAdminRequest()
  try {
    const source = await roomId(admin, "4-201")
    const target = await roomId(admin, "4-202")
    const beforeTarget = await detail(admin, target)
    const beforeSource = await detail(admin, source)
    const response = await admin.get("/api/v1/housing/report-batches")
    expect(response.status()).toBe(200)
    const reports = await response.json() as { items: Batch[] }
    expect(reports.items).toHaveLength(1)
    const batch = reports.items[0]
    expect(batch.status).toBe("generated")
    await loginAsAdmin(page)
    await page.goto("/housing/reports")
    const xlsx = await reportDownload(page, batch, "xlsx")
    const csv = await reportDownload(page, batch, "csv")
    expect(xlsx).toEqual(csv)
    expect(xlsx["入住名单"]).toHaveLength(2)
    expect(xlsx["入住名单"].map((row) => row["住宿记录ID"]).sort()).toEqual([SOURCE_STUDENT, SOURCE_COACH].sort())
    expect(JSON.stringify(xlsx)).toContain(STUDENT)
    await openRoom(page, source, "4-201")
    const dialog = await prepareVoid(page, STUDENT, REASON)
    const resultPromise = page.waitForResponse((item) => item.request().method() === "POST"
      && new URL(item.url()).pathname === `/api/v1/housing/stays/${SOURCE_STUDENT}/void-history`)
    await dialog.getByRole("button", { name: "确认作废历史住宿", exact: true }).click()
    expect((await resultPromise).status()).toBe(200)
    await expect(dialog).toHaveCount(0)
    await expect(page.getByRole("region", { name: "住宿历史", exact: true }).getByText(STUDENT, { exact: true })).toHaveCount(0)
    await page.reload()
    const toggle = page.getByRole("checkbox", { name: "查看已作废历史" })
    await expect(toggle).not.toBeChecked()
    await toggle.check()
    let tombstones = page.getByRole("region", { name: "已作废住宿历史", exact: true })
    await expect(tombstones.getByText(STUDENT, { exact: true })).toBeVisible()
    await expect(tombstones.getByText(REASON)).toBeVisible()
    expect(page.url()).not.toContain(REASON)
    expect(await page.evaluate((reason) => Object.values(localStorage).some(
      (value: unknown) => typeof value === "string" && value.includes(reason),
    ), REASON)).toBe(false)
    await page.reload()
    await expect(page.getByRole("region", { name: "已作废住宿历史", exact: true })).toHaveCount(0)
    await page.getByRole("checkbox", { name: "查看已作废历史" }).check()
    tombstones = page.getByRole("region", { name: "已作废住宿历史", exact: true })
    await expect(tombstones.getByText(STUDENT, { exact: true })).toBeVisible()
    const sourceAfter = await detail(admin, source, true)
    expect(sourceAfter.history.map((stay) => stay.id)).toEqual([SOURCE_COACH])
    expect(sourceAfter.voided_history?.map((stay) => stay.id)).toEqual([SOURCE_STUDENT])
    expect(sourceAfter.transfers.map((transfer) => [transfer.id, transfer.from_stay_id, transfer.to_stay_id]))
      .toEqual(beforeSource.transfers.map((transfer) => [transfer.id, transfer.from_stay_id, transfer.to_stay_id]))
    await openRoom(page, target, "4-202")
    await expect(page.getByRole("region", { name: "当前入住", exact: true }).getByText(STUDENT, { exact: true })).toBeVisible()
    await expect(page.getByText("来源历史已作废", { exact: true })).toBeVisible()
    expect((await detail(admin, target)).current_stays).toEqual(beforeTarget.current_stays)
    await page.goto("/housing/reports")
    expect(await reportDownload(page, batch, "xlsx")).toEqual(xlsx)
    expect(await reportDownload(page, batch, "csv")).toEqual(csv)
    const persisted = await admin.get(`/api/v1/housing/report-batches/${batch.id}`)
    expect(persisted.status()).toBe(200)
    const body = await persisted.json() as Batch
    expect(body.status).toBe("generated")
    expect(body.version).toBe(batch.version)
    const generated = await admin.post("/api/v1/housing/report-batches", {
      headers: { "X-CSRF-Token": csrfToken },
      data: {
        range_start: "2026-08-01T00:00:00+08:00",
        range_end: "2026-08-10T00:00:00+08:00",
        scope_mode: "authorized",
        purpose: "E2E虚构作废后重新生成报送",
      },
    })
    expect(generated.status()).toBe(200)
    const corrected = await generated.json() as Batch
    await page.reload()
    const newReport = await reportDownload(page, corrected, "xlsx")
    expect(newReport["入住名单"].map((row) => row["住宿记录ID"])).toEqual([SOURCE_COACH])
    expect(JSON.stringify(newReport)).not.toContain(STUDENT)
  } finally { await admin.dispose() }
})

test("revocation denies a new action and drops a late successful void response with sensitive tombstone state", async ({ page }) => {
  test.setTimeout(180_000)
  const { admin, csrfToken } = await createAdminRequest()
  const mutable = await createRequestSession(...e2eCredentialTuple("customer_service"))
  const routePattern = `**/api/v1/housing/stays/${SOURCE_COACH}/void-history`
  let release = () => {}
  let ready = () => {}
  let settled = () => {}
  const held = new Promise<void>((resolve) => { release = resolve })
  const received = new Promise<void>((resolve) => { ready = resolve })
  const finished = new Promise<void>((resolve) => { settled = resolve })
  let intercepted = false
  let routeError: unknown
  const draft = "E2E虚构撤权前历史原因草稿"
  await page.route(routePattern, async (route) => {
    intercepted = true
    try {
      const response = await route.fetch()
      expect(response.status()).toBe(200)
      ready()
      await held
      try { await route.fulfill({ response }) } catch { /* Revocation can abort the request. */ }
    } catch (error) { routeError = error; ready() }
    finally { settled() }
  })
  try {
    const source = await roomId(admin, "4-201")
    // The late-response scenario can also be run alone after the opt-in seed.
    const fixture = await detail(admin, source, true)
    if (!fixture.voided_history?.some((stay) => stay.id === SOURCE_STUDENT)) {
      const stay = fixture.history.find((item) => item.id === SOURCE_STUDENT)
      if (!stay) throw new Error("Synthetic source history was not found")
      const tombstone = await admin.post(`/api/v1/housing/stays/${SOURCE_STUDENT}/void-history`, {
        headers: { "X-CSRF-Token": csrfToken },
        data: { expected_version: stay.version, reason: REASON, confirmed: true, idempotency_key: crypto.randomUUID() },
      })
      expect(tombstone.status()).toBe(200)
    }
    const rolesResponse = await admin.get("/api/v1/roles")
    expect(rolesResponse.status()).toBe(200)
    const roles = await rolesResponse.json() as Array<{ id: string; key: string }>
    const superAdminRole = roles.find((role) => role.key === "super_admin")
    if (!superAdminRole) throw new Error("Canonical super_admin role was not found")
    await loginAsCustomerService(page)
    await openRoom(page, source, "4-201")
    await page.getByRole("checkbox", { name: "查看已作废历史" }).check()
    await expect(page.getByRole("region", { name: "已作废住宿历史", exact: true }).getByText(REASON)).toBeVisible()
    const dialog = await prepareVoid(page, COACH, draft)
    await dialog.getByRole("button", { name: "确认作废历史住宿", exact: true }).click()
    await received
    expect(routeError).toBeUndefined()
    const revoke = await admin.delete(`/api/v1/accounts/${MUTABLE_ACCOUNT}/roles/${superAdminRole.id}`, {
      headers: { "X-CSRF-Token": csrfToken },
    })
    expect(revoke.status()).toBe(204)
    const denied = await mutable.api.post(`/api/v1/housing/stays/${SOURCE_COACH}/void-history`, {
      headers: { "X-CSRF-Token": mutable.csrfToken },
      data: { expected_version: 1, reason: draft, confirmed: true, idempotency_key: crypto.randomUUID() },
    })
    expect(denied.status()).toBe(403)
    await refocus(page)
    await expect(dialog).toHaveCount(0)
    await expect(page.getByRole("checkbox", { name: "查看已作废历史" })).toHaveCount(0)
    await expect(page.getByRole("region", { name: "已作废住宿历史", exact: true })).toHaveCount(0)
    release()
    await finished
    expect(routeError).toBeUndefined()
    await expect(page.getByRole("dialog")).toHaveCount(0)
    await expect(page.getByRole("button", { name: /^作废历史住宿 / })).toHaveCount(0)
    await expect(page.getByText(REASON)).toHaveCount(0)
    await expect.poll(() => page.locator("input, textarea").evaluateAll(
      (elements, value) => elements.filter((element) => (element as HTMLInputElement).value === value).length, draft,
    )).toBe(0)
    await expectNoHorizontalOverflow(page)
  } finally {
    release()
    if (intercepted) await finished
    await page.unroute(routePattern)
    await mutable.api.dispose()
    await admin.dispose()
  }
})
