import { execFile } from "node:child_process"
import path from "node:path"
import { promisify } from "node:util"

import { expect, test } from "@playwright/test"
import type { APIRequestContext, Download, Locator, Page } from "@playwright/test"

import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
  loginAsAdmin,
  openNavigationItem,
} from "./helpers"

const execFileAsync = promisify(execFile)
const PYTHON_BIN = process.env.E2E_PYTHON_BIN
const INSPECTOR = path.resolve("tests/support/inspect-housing-report.py")
const REPORT_RANGE_START = "2026-08-01T00:00"
const REPORT_RANGE_END = "2026-08-22T00:00"
const SNAPSHOT_PERSON_ID = "e1000000-0000-4000-8000-000000000007"
const REPORT_TABLES = ["入住名单", "未成年监护人"] as const

type CanonicalReport = Record<
  (typeof REPORT_TABLES)[number],
  Array<Record<string, string>>
>

type BatchSummary = {
  id: string
  batch_number: string
  status: "generated" | "marked_submitted" | "voided" | "failed"
  version: number
}

type AuditEvent = {
  action: string
  outcome: "allowed" | "denied" | "failed"
  metadata: Record<string, unknown>
}

function viewportWidth(page: Page): number {
  return page.viewportSize()?.width ?? 1440
}

function shanghaiDateTimeInput(offsetMinutes: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(Date.now() + offsetMinutes * 60_000))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`
}

async function expectTouchTargets(page: Page): Promise<void> {
  if (viewportWidth(page) !== 360) return
  const boxes = await page.locator([
    ".housingReportsPage button:visible",
    ".housingDialog input:not([type='radio']):not([type='checkbox']):visible",
    ".housingDialog textarea:visible",
    ".housingDialog label:has(input):visible",
  ].join(", ")).evaluateAll((controls) => controls.map((control) => {
    const box = control.getBoundingClientRect()
    return { width: box.width, height: box.height }
  }))
  expect(boxes.length).toBeGreaterThan(0)
  for (const box of boxes) {
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
}

async function expectReportLayout(page: Page): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".housingReportsPage")
  await expectAboveMobileNavigation(page, ".housingReportsPage")
  if (viewportWidth(page) < 720) {
    await expect(page.getByRole("region", { name: "实名报送批次卡片" })).toBeVisible()
    await expect(page.getByRole("table", { name: "实名报送批次" })).toHaveCount(0)
  } else {
    await expect(page.getByRole("table", { name: "实名报送批次" })).toBeVisible()
  }
  await expectTouchTargets(page)
}

async function expectReportDialog(page: Page, name: string): Promise<Locator> {
  const dialog = page.getByRole("dialog", { name })
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute("aria-modal", "true")
  await expect.poll(() => dialog.evaluate((element) => (
    element.contains(document.activeElement)
  ))).toBe(true)
  await expectNoHorizontalOverflow(page)
  await expectNoInternalHorizontalOverflow(dialog)
  await expectHorizontallyInsideViewport(page, ".housingDialog")
  await expectAboveMobileNavigation(page, ".housingDialog")
  await expectTouchTargets(page)
  return dialog
}

async function inspectDownload(
  download: Download,
  format: "xlsx" | "csv",
): Promise<CanonicalReport> {
  if (!PYTHON_BIN) throw new Error("E2E_PYTHON_BIN is required to inspect report downloads")
  const downloadedPath = await download.path()
  if (!downloadedPath) throw new Error(`Playwright did not retain ${format} download`)
  const { stdout, stderr } = await execFileAsync(
    PYTHON_BIN,
    [INSPECTOR, "--format", format, downloadedPath],
    { encoding: "utf8" },
  )
  expect(stderr).toBe("")
  const parsed = JSON.parse(stdout) as CanonicalReport
  expect(Object.keys(parsed).sort()).toEqual([...REPORT_TABLES].sort())
  return parsed
}

async function captureDownload(
  page: Page,
  batchNumber: string,
  format: "xlsx" | "csv",
): Promise<CanonicalReport> {
  const accessibleFormat = format === "xlsx" ? "Excel" : "CSV"
  const expectedExtension = format === "xlsx" ? ".xlsx" : ".zip"
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    page.getByRole("button", {
      name: `下载 ${batchNumber} ${accessibleFormat}`,
    }).click(),
  ])
  expect(download.suggestedFilename()).toBe(
    `housing-report-${batchNumber}${expectedExtension}`,
  )
  return inspectDownload(download, format)
}

async function updateLivePerson(
  admin: APIRequestContext,
  csrfToken: string,
  replacementName: string,
): Promise<void> {
  const response = await admin.patch(`/api/v1/people/${SNAPSHOT_PERSON_ID}`, {
    headers: { "X-CSRF-Token": csrfToken },
    data: { full_name: replacementName },
  })
  expect(response.status()).toBe(200)
}

async function expectAllowedAuditEvents(admin: APIRequestContext): Promise<void> {
  const response = await admin.get(
    "/api/v1/audit-events?resource_type=housing_report_batch&page_size=100",
  )
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { items: AuditEvent[] }
  const allowedActions = new Set(
    body.items
      .filter((event) => event.outcome === "allowed")
      .map((event) => event.action),
  )
  for (const action of [
    "housing.report_batch.generate",
    "housing.report_batch.download.xlsx",
    "housing.report_batch.download.csv",
    "housing.report_batch.mark_submitted",
    "housing.report_batch.void",
    "housing.report_batch.correct",
  ]) {
    expect(allowedActions).toContain(action)
  }
  const metadata = JSON.stringify(body.items.map((event) => event.metadata))
  expect(metadata).not.toMatch(/\b1[3-9]\d{9}\b/)
  expect(metadata).not.toMatch(/\b\d{17}[\dX]\b/i)
  expect(metadata).not.toContain("E2E自助学员")
  expect(metadata).not.toContain("E2E实名报送快照已变更")
}

test("administrator completes the immutable reporting lifecycle", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(15_000)
  const { admin, csrfToken } = await createAdminRequest()
  const purpose = `派出所人工报送-${testInfo.project.name}`
  const changedName = `E2E实名报送快照已变更-${testInfo.project.name}`

  try {
    await loginAsAdmin(page)
    await openNavigationItem(page, "实名报送")
    await expect(page.getByRole("heading", { name: "实名入住报送" })).toBeVisible()
    await expect(page.getByRole("heading", { name: "暂无实名报送批次" })).toBeVisible()
    await expectNoHorizontalOverflow(page)

    const generate = page.getByRole("button", { name: "生成实名名单" })
    await generate.click()
    let dialog = await expectReportDialog(page, "生成实名名单")
    await page.keyboard.press("Escape")
    await expect(dialog).toHaveCount(0)
    await expect(generate).toBeFocused()

    await generate.click()
    dialog = await expectReportDialog(page, "生成实名名单")
    await dialog.getByLabel("开始时间").fill(REPORT_RANGE_START)
    await dialog.getByLabel("结束时间").fill(REPORT_RANGE_END)
    await dialog.getByLabel("导出用途").fill(purpose)
    const generatedResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/v1/housing/report-batches"
    ))
    await dialog.getByRole("button", { name: "确认生成" }).click()
    const generatedResponse = await generatedResponsePromise
    expect(generatedResponse.status()).toBe(200)
    const generated = (await generatedResponse.json()) as BatchSummary
    expect(generated.status).toBe("generated")
    await expect(dialog).toHaveCount(0)
    await expect(page.getByText(`已生成批次 ${generated.batch_number}`)).toBeVisible()
    await expect(page.getByText(generated.batch_number, { exact: true }).first()).toBeVisible()
    await expectReportLayout(page)

    const xlsx = await captureDownload(page, generated.batch_number, "xlsx")
    const csv = await captureDownload(page, generated.batch_number, "csv")
    expect(xlsx).toEqual(csv)
    expect(xlsx["入住名单"]).toHaveLength(6)
    expect(xlsx["未成年监护人"]).toHaveLength(2)

    await updateLivePerson(admin, csrfToken, changedName)
    const immutableRedownload = await captureDownload(
      page,
      generated.batch_number,
      "xlsx",
    )
    expect(immutableRedownload).toEqual(xlsx)
    expect(JSON.stringify(immutableRedownload)).not.toContain(changedName)

    await page.getByRole("button", {
      name: `标记 ${generated.batch_number} 已报送`,
    }).click()
    dialog = await expectReportDialog(page, "标记人工报送")
    await dialog.getByLabel("报送时间").fill(shanghaiDateTimeInput(-2))
    await dialog.getByLabel("报送备注（选填）").fill(`窗口报送-${testInfo.project.name}`)
    const submittedResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/${generated.id}/mark-submitted`)
    ))
    await dialog.getByRole("button", { name: "确认已报送" }).click()
    expect((await submittedResponsePromise).status()).toBe(200)
    await expect(dialog).toHaveCount(0)
    await expect(page.getByText(`批次 ${generated.batch_number} 已标记报送`)).toBeVisible()

    await page.getByRole("button", { name: `作废 ${generated.batch_number}` }).click()
    dialog = await expectReportDialog(page, "作废实名批次")
    await expect(dialog.getByText(
      "作废后原批次不能再次下载，只能创建关联更正批次。",
    )).toBeVisible()
    await dialog.getByLabel("作废原因").fill(`窗口数据有误-${testInfo.project.name}`)
    const voidedResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(`/${generated.id}/void`)
    ))
    await dialog.getByRole("button", { name: "确认作废" }).click()
    expect((await voidedResponsePromise).status()).toBe(200)
    await expect(dialog).toHaveCount(0)
    await expect(page.getByText(`批次 ${generated.batch_number} 已作废`)).toBeVisible()
    await expect(page.getByRole("button", {
      name: `下载 ${generated.batch_number} Excel`,
    })).toHaveCount(0)

    await page.getByRole("button", { name: `更正 ${generated.batch_number}` }).click()
    dialog = await expectReportDialog(page, "创建更正实名名单")
    await expect(dialog.getByLabel("指定楼层")).toBeChecked()
    const correctedResponsePromise = page.waitForResponse((response) => (
      response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/v1/housing/report-batches"
    ))
    await dialog.getByRole("button", { name: "确认生成" }).click()
    const correctedResponse = await correctedResponsePromise
    expect(correctedResponse.status()).toBe(200)
    const corrected = (await correctedResponse.json()) as BatchSummary
    expect(corrected.id).not.toBe(generated.id)
    await expect(dialog).toHaveCount(0)

    await page.getByRole("button", { name: `查看 ${corrected.batch_number} 详情` }).click()
    dialog = await expectReportDialog(page, "实名批次详情")
    const chain = dialog.getByText(new RegExp(
      `${generated.batch_number}.*${corrected.batch_number}`,
    ))
    await expect(chain).toBeVisible()
    await dialog.getByRole("button", { name: "关闭", exact: true }).click()
    await expect(dialog).toHaveCount(0)

    await expectAllowedAuditEvents(admin)
    await expectReportLayout(page)
  } finally {
    await admin.dispose()
  }
})
