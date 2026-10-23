import { expect, test } from "@playwright/test"
import type { APIRequestContext, APIResponse, Page } from "@playwright/test"

import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  logout,
  openNavigationItem,
} from "./helpers"
import { E2E_CREDENTIALS } from "./e2eCredentials"

const REPORT_PERMISSION_ID = "20000000-0000-4000-8000-000000000043"
const BUILDING_ACCOUNT_ID = "e3110000-0000-4000-8000-000000000001"
const BUILDING_OVERRIDE_ID = "e3150000-0000-4000-8000-000000000001"
const BUILDING_ID = "53011fd7-1aae-5380-b028-23ad5a2aacdf"
const ALLOWED_FLOOR_ID = "ff0b41d6-d1be-578f-9186-acd45d3081ed"
const DENIED_FLOOR_ID = "ad6edff8-f377-5987-8ee4-bd16dab53d75"
const OTHER_FLOOR_ID = "7c993534-98cc-5afa-b3c8-b0f59e2b97a6"
const MISSING_BATCH_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
const REPORT_RANGE = {
  range_start: "2026-08-01T00:00:00+08:00",
  range_end: "2026-08-22T00:00:00+08:00",
}

type BatchSummary = {
  id: string
  batch_number: string
  status: string
  version: number
}

type Problem = {
  type: string
  title: string
  status: number
  detail: string
  code: string
  request_id?: string
}

const REPORT_USERS = {
  building: E2E_CREDENTIALS.housing_report_building,
  floor: E2E_CREDENTIALS.housing_report_floor,
  none: E2E_CREDENTIALS.housing_report_none,
} as const

function viewportWidth(page: Page): number {
  return page.viewportSize()?.width ?? 1440
}

async function login(
  page: Page,
  credentials: (typeof REPORT_USERS)[keyof typeof REPORT_USERS],
): Promise<string> {
  await page.goto("/login")
  await page.getByLabel("手机号").fill(credentials.identifier)
  await page.getByLabel("密码", { exact: true }).fill(credentials.password)
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v1/auth/login"
  ))
  await page.getByRole("button", { name: "登录" }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  return body.csrf_token
}

function generationInput(floorIds: string[], purpose: string): Record<string, unknown> {
  return {
    ...REPORT_RANGE,
    scope_mode: "floors",
    building_ids: [],
    floor_ids: floorIds,
    cohort_ids: [],
    person_types: [],
    purpose,
    corrects_batch_id: null,
  }
}

async function createBatch(
  admin: APIRequestContext,
  csrf: string,
  floorId: string,
  purpose: string,
): Promise<BatchSummary> {
  const response = await admin.post("/api/v1/housing/report-batches", {
    headers: { "X-CSRF-Token": csrf },
    data: generationInput([floorId], purpose),
  })
  expect(response.status()).toBe(200)
  return (await response.json()) as BatchSummary
}

async function adminBatchTotal(admin: APIRequestContext): Promise<number> {
  const response = await admin.get("/api/v1/housing/report-batches?page_size=100")
  expect(response.status()).toBe(200)
  const body = (await response.json()) as { total: number }
  return body.total
}

async function expectAtomicScopeDenial(
  page: Page,
  admin: APIRequestContext,
  csrf: string,
  floorIds: string[],
  purpose: string,
): Promise<void> {
  const before = await adminBatchTotal(admin)
  const response = await page.request.post("/api/v1/housing/report-batches", {
    headers: { "X-CSRF-Token": csrf },
    data: generationInput(floorIds, purpose),
  })
  expect(response.status()).toBe(403)
  const problem = (await response.json()) as Problem
  expect(problem.code).toBe("housing_report_scope_denied")
  expect(await adminBatchTotal(admin)).toBe(before)
}

function privateProblem(response: APIResponse, body: Problem): Record<string, unknown> {
  return {
    http_status: response.status(),
    type: body.type,
    title: body.title,
    status: body.status,
    detail: body.detail,
    code: body.code,
  }
}

async function expectPrivate404Equivalent(
  page: Page,
  deniedBatchId: string,
): Promise<void> {
  const deniedResponse = await page.request.get(
    `/api/v1/housing/report-batches/${deniedBatchId}`,
  )
  const missingResponse = await page.request.get(
    `/api/v1/housing/report-batches/${MISSING_BATCH_ID}`,
  )
  const deniedBody = (await deniedResponse.json()) as Problem
  const missingBody = (await missingResponse.json()) as Problem
  expect(privateProblem(deniedResponse, deniedBody)).toEqual(
    privateProblem(missingResponse, missingBody),
  )
  expect(deniedResponse.status()).toBe(404)
}

async function expectVisibleBatches(
  page: Page,
  visible: readonly BatchSummary[],
  hidden: readonly BatchSummary[],
): Promise<void> {
  await openNavigationItem(page, "实名报送")
  await expect(page.getByRole("heading", { name: "实名入住报送" })).toBeVisible()
  for (const batch of visible) {
    await expect(page.getByText(batch.batch_number, { exact: true }).first()).toBeVisible()
  }
  for (const batch of hidden) {
    await expect(page.getByText(batch.batch_number, { exact: true })).toHaveCount(0)
  }
  const pageText = await page.locator(".housingReportsPage").innerText()
  expect(pageText).not.toMatch(/\b1[3-9]\d{9}\b/)
  expect(pageText).not.toMatch(/\b\d{17}[\dX]\b/i)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".housingReportsPage")
  await expectAboveMobileNavigation(page, ".housingReportsPage")
  if (viewportWidth(page) < 720) {
    await expect(page.getByRole("region", { name: "实名报送批次卡片" })).toBeVisible()
  } else {
    await expect(page.getByRole("table", { name: "实名报送批次" })).toBeVisible()
  }
}

async function revokeBuildingPermission(
  admin: APIRequestContext,
  csrf: string,
): Promise<void> {
  const response = await admin.delete(
    `/api/v1/accounts/${BUILDING_ACCOUNT_ID}/permission-overrides/${BUILDING_OVERRIDE_ID}`,
    { headers: { "X-CSRF-Token": csrf } },
  )
  expect(response.status()).toBe(204)
}

async function restoreBuildingPermission(
  admin: APIRequestContext,
  csrf: string,
): Promise<void> {
  const response = await admin.post(
    `/api/v1/accounts/${BUILDING_ACCOUNT_ID}/permission-overrides`,
    {
      headers: { "X-CSRF-Token": csrf },
      data: {
        permission_id: REPORT_PERMISSION_ID,
        effect: "allow",
        scope_kind: "building",
        scope_id: BUILDING_ID,
        grant_reason: "恢复实名报送 E2E 楼栋范围",
      },
    },
  )
  expect(response.status()).toBe(201)
}

async function expectRevokedRequestsDenied(
  page: Page,
  batch: BatchSummary,
  csrf: string,
): Promise<void> {
  const [listResponse, downloadResponse, actionResponse] = await Promise.all([
    page.request.get("/api/v1/housing/report-batches"),
    page.request.get(
      `/api/v1/housing/report-batches/${batch.id}/download?format=xlsx`,
    ),
    page.request.post(`/api/v1/housing/report-batches/${batch.id}/void`, {
      headers: { "X-CSRF-Token": csrf },
      data: { reason: "撤权后动作必须拒绝", version: batch.version },
    }),
  ])
  for (const response of [listResponse, downloadResponse, actionResponse]) {
    expect(response.status()).toBe(403)
    const problem = (await response.json()) as Problem
    expect(problem.code).toBe("permission_denied")
  }
}

async function expectSafeAuditMetadata(admin: APIRequestContext): Promise<void> {
  const response = await admin.get(
    "/api/v1/audit-events?resource_type=housing_report_batch&page_size=100",
  )
  expect(response.status()).toBe(200)
  const body = (await response.json()) as {
    items: Array<{ metadata: Record<string, unknown> }>
  }
  const metadata = JSON.stringify(body.items.map((item) => item.metadata))
  expect(metadata).not.toMatch(/\b1[3-9]\d{9}\b/)
  expect(metadata).not.toMatch(/\b\d{17}[\dX]\b/i)
  expect(metadata).not.toContain("E2E自助学员")
  expect(metadata).not.toContain("E2E绑定学员")
}

test("scoped users obey floor denial, private 404, and live revocation", async ({
  page,
}, testInfo) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(15_000)
  const { admin, csrfToken: adminCsrf } = await createAdminRequest()
  let buildingPermissionRevoked = false

  try {
    const allowedBatch = await createBatch(
      admin,
      adminCsrf,
      ALLOWED_FLOOR_ID,
      `4幢二楼实名批次-${testInfo.project.name}`,
    )
    const deniedFloorBatch = await createBatch(
      admin,
      adminCsrf,
      DENIED_FLOOR_ID,
      `4幢三楼实名批次-${testInfo.project.name}`,
    )
    const otherBuildingBatch = await createBatch(
      admin,
      adminCsrf,
      OTHER_FLOOR_ID,
      `A幢二楼实名批次-${testInfo.project.name}`,
    )
    expect(await adminBatchTotal(admin)).toBe(3)

    await login(page, REPORT_USERS.none)
    await expect(page.getByRole("link", { name: "实名报送" })).toHaveCount(0)
    await page.goto("/housing/reports")
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await logout(page)

    const firstBuildingCsrf = await login(page, REPORT_USERS.building)
    await expectVisibleBatches(
      page,
      [allowedBatch, deniedFloorBatch],
      [otherBuildingBatch],
    )
    await expectPrivate404Equivalent(page, otherBuildingBatch.id)
    await expectAtomicScopeDenial(
      page,
      admin,
      firstBuildingCsrf,
      [ALLOWED_FLOOR_ID, OTHER_FLOOR_ID],
      `楼栋跨范围拒绝-${testInfo.project.name}`,
    )
    await logout(page)

    const floorCsrf = await login(page, REPORT_USERS.floor)
    await expectVisibleBatches(
      page,
      [allowedBatch],
      [deniedFloorBatch, otherBuildingBatch],
    )
    await expectPrivate404Equivalent(page, deniedFloorBatch.id)
    await expectAtomicScopeDenial(
      page,
      admin,
      floorCsrf,
      [ALLOWED_FLOOR_ID, DENIED_FLOOR_ID],
      `楼层显式拒绝-${testInfo.project.name}`,
    )
    await logout(page)

    const revokedBuildingCsrf = await login(page, REPORT_USERS.building)
    await expectVisibleBatches(
      page,
      [allowedBatch, deniedFloorBatch],
      [otherBuildingBatch],
    )
    await revokeBuildingPermission(admin, adminCsrf)
    buildingPermissionRevoked = true
    await expectRevokedRequestsDenied(page, allowedBatch, revokedBuildingCsrf)

    await page.reload()
    await expect(page).toHaveURL(/\/$/)
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await expect(page.getByRole("link", { name: "实名报送" })).toHaveCount(0)
    await page.goto("/housing/reports")
    await expect(page).toHaveURL(/\/$/)
    await expect(page.locator(".housingReportsPage")).toHaveCount(0)

    await expectSafeAuditMetadata(admin)
  } finally {
    if (buildingPermissionRevoked) {
      await restoreBuildingPermission(admin, adminCsrf)
    }
    await admin.dispose()
  }
})
