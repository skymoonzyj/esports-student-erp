import { createHash, createHmac, randomUUID } from "node:crypto"

import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"

import { expectNoHorizontalOverflow, loginAsAdmin } from "./helpers"

const SERVICE_PUBLIC_ID = "pay_e2e_seeded_credential_0001"
const SERVICE_SECRET = "e2e-payment-secret-2026-fixed"
const IMPORT_PATH = "/api/v1/payment-imports"
const COHORT_NAME = "E2E招生暑期营"
const STANDARD_CLASS_NAME = "E2E英雄联盟普通班"
const ELITE_CLASS_NAME = "E2E英雄联盟菁英班"

const STUDENTS_BY_VIEWPORT = new Map([
  [
    1440,
    {
      id: "e1000000-0000-4000-8000-000000000007",
      name: "E2E自助学员",
    },
  ],
  [
    1024,
    {
      id: "e2000000-0000-4000-8000-000000000020",
      name: "E2E招生学员1024",
    },
  ],
  [
    768,
    {
      id: "e2000000-0000-4000-8000-000000000030",
      name: "E2E招生学员768",
    },
  ],
  [
    360,
    {
      id: "e2000000-0000-4000-8000-000000000040",
      name: "E2E招生学员360",
    },
  ],
])

type PaymentResponse = {
  id: string
  admission_case_id: string
}

type EnrollmentResponse = {
  id: string
  class_name: string
  status: "active" | "cancelled" | "completed"
}

type PaymentImportResponse = {
  import_event_id: string
  payment_id: string
  admission_case_id: string
  processing_status: "processed" | "pending_match"
  created: boolean
}

type PageResponse<T> = {
  items: T[]
}

type PendingAdmissionCase = {
  id: string
  student_person_id: string | null
  student_name: string | null
  current_enrollment_id: string | null
  status: string
}

type PaymentConflict = {
  original_import_event_id: string
  conflict_type: string
  status: string
}

type ImportedPayment = {
  id: string
  amount_cents: number | null
}

function studentForPage(page: Page): { id: string; name: string } {
  const width = page.viewportSize()?.width ?? 1440
  const student = STUDENTS_BY_VIEWPORT.get(width)
  if (!student) throw new Error(`No admissions E2E student seeded for viewport ${width}`)
  return student
}

async function expectResponsiveTable(
  page: Page,
  accessibleName: string,
): Promise<void> {
  const table = page.getByRole("table", { name: accessibleName })
  await expect(table).toBeVisible()
  const header = table.locator(".admissionsTableHeader")
  if ((page.viewportSize()?.width ?? 1440) <= 900) {
    await expect(header).toBeHidden()
  } else {
    await expect(header).toBeVisible()
  }
  await expectNoHorizontalOverflow(page)
}

function machineHeaders(
  body: string,
  nonce: string,
  timestamp = Math.floor(Date.now() / 1000).toString(),
): Record<string, string> {
  const bodyDigest = createHash("sha256").update(body).digest("hex")
  const canonical = ["POST", IMPORT_PATH, timestamp, nonce, bodyDigest].join("\n")
  const signature = createHmac("sha256", SERVICE_SECRET)
    .update(canonical)
    .digest("hex")
  return {
    "Content-Type": "application/json",
    "X-Service-Account": SERVICE_PUBLIC_ID,
    "X-Timestamp": timestamp,
    "X-Nonce": nonce,
    "X-Signature": signature,
  }
}

test("machine imports are signed, idempotent, replay-safe and conflict-safe", async ({
  page,
}) => {
  const runId = randomUUID()
  const timestamp = Math.floor(Date.now() / 1000)
  const payload = {
    source_system: "e2e-payment",
    external_order_id: `E2E-GUARDIAN-ORDER-${runId}`,
    idempotency_key: `E2E-GUARDIAN-IDEMPOTENCY-${runId}`,
    payer_phone: "19990000005",
    amount: "5000.00",
    currency: "CNY",
    paid_at: "2026-07-29T09:30:00+08:00",
    raw_payload: { channel: "e2e", sequence: 1 },
  }
  const body = JSON.stringify(payload)

  const first = await page.request.post(IMPORT_PATH, {
    data: body,
    headers: machineHeaders(
      body,
      `e2e-machine-${runId}-001`,
      String(timestamp),
    ),
  })
  expect(first.status()).toBe(201)
  const firstResult = (await first.json()) as PaymentImportResponse
  expect(firstResult).toMatchObject({
    created: true,
    processing_status: "pending_match",
  })

  const replayHeaders = machineHeaders(
    body,
    `e2e-machine-${runId}-002`,
    String(timestamp + 1),
  )
  const businessReplay = await page.request.post(IMPORT_PATH, {
    data: body,
    headers: replayHeaders,
  })
  expect(businessReplay.status()).toBe(200)
  const replayResult = (await businessReplay.json()) as PaymentImportResponse
  expect(replayResult).toMatchObject({
    created: false,
    import_event_id: firstResult.import_event_id,
    payment_id: firstResult.payment_id,
    admission_case_id: firstResult.admission_case_id,
    processing_status: "pending_match",
  })

  const literalReplay = await page.request.post(IMPORT_PATH, {
    data: body,
    headers: replayHeaders,
  })
  expect(literalReplay.status()).toBe(409)
  await expect(literalReplay.json()).resolves.toMatchObject({
    code: "payment_replay_rejected",
  })

  const changedBody = JSON.stringify({
    ...payload,
    external_order_id: `${payload.external_order_id}-changed`,
    amount: "5001.00",
  })
  const changed = await page.request.post(IMPORT_PATH, {
    data: changedBody,
    headers: machineHeaders(
      changedBody,
      `e2e-machine-${runId}-003`,
      String(timestamp + 2),
    ),
  })
  expect(changed.status()).toBe(409)
  await expect(changed.json()).resolves.toMatchObject({
    code: "payment_import_conflict",
  })

  const originalAfterConflict = await page.request.post(IMPORT_PATH, {
    data: body,
    headers: machineHeaders(
      body,
      `e2e-machine-${runId}-004`,
      String(timestamp + 3),
    ),
  })
  expect(originalAfterConflict.status()).toBe(200)
  await expect(originalAfterConflict.json()).resolves.toMatchObject({
    created: false,
    import_event_id: firstResult.import_event_id,
    payment_id: firstResult.payment_id,
    admission_case_id: firstResult.admission_case_id,
  })

  await loginAsAdmin(page)
  const pendingCasesResponse = await page.request.get(
    "/api/v1/admission-cases?status=pending_match&page=1&page_size=100",
  )
  expect(pendingCasesResponse.status()).toBe(200)
  const pendingCases =
    (await pendingCasesResponse.json()) as PageResponse<PendingAdmissionCase>
  expect(
    pendingCases.items.find((item) => item.id === firstResult.admission_case_id),
  ).toMatchObject({
    status: "pending_match",
    student_person_id: null,
    student_name: null,
    current_enrollment_id: null,
  })

  const conflictsResponse = await page.request.get(
    "/api/v1/payment-conflicts?status=open&page=1&page_size=100",
  )
  expect(conflictsResponse.status()).toBe(200)
  const conflicts = (await conflictsResponse.json()) as PageResponse<PaymentConflict>
  expect(
    conflicts.items.find(
      (item) => item.original_import_event_id === firstResult.import_event_id,
    ),
  ).toMatchObject({
    conflict_type: "idempotency_payload_mismatch",
    status: "open",
  })

  const paymentsResponse = await page.request.get(
    "/api/v1/payments?source_kind=api&source_system=e2e-payment&page=1&page_size=100",
  )
  expect(paymentsResponse.status()).toBe(200)
  const importedPayments =
    (await paymentsResponse.json()) as PageResponse<ImportedPayment>
  expect(
    importedPayments.items.find((item) => item.id === firstResult.payment_id),
  ).toMatchObject({ amount_cents: 500_000 })
})

test("admin records payment, enrolls, transfers and withdraws with preview", async ({
  page,
}) => {
  test.setTimeout(90_000)
  const student = studentForPage(page)
  await loginAsAdmin(page)

  await page.goto("/teaching/payments")
  await expect(page.getByRole("heading", { name: "付款与退款" })).toBeVisible()
  await page.getByRole("button", { name: "登记付款" }).click()
  const paymentDialog = page.getByRole("dialog", { name: "登记付款" })
  await paymentDialog.getByLabel("学员 ID").fill(student.id)
  await paymentDialog.getByLabel("付款金额（元）").fill("5000.00")
  await paymentDialog.getByLabel("备注").fill(`四视口报名验收：${student.name}`)
  await expectNoHorizontalOverflow(page)
  const paymentResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === "/api/v1/payments" &&
      response.request().method() === "POST",
  )
  await paymentDialog.getByRole("button", { name: "确认登记" }).click()
  const paymentResponse = await paymentResponsePromise
  expect(paymentResponse.status()).toBe(201)
  const payment = (await paymentResponse.json()) as PaymentResponse
  await expect(paymentDialog).toHaveCount(0)
  const paymentRow = page
    .getByRole("table", { name: "付款记录" })
    .getByRole("row")
    .filter({ hasText: payment.admission_case_id })
  await expect(paymentRow).toContainText("手工登记")
  await expect(paymentRow).toContainText("¥5,000.00")
  await expectResponsiveTable(page, "付款记录")

  await page.goto("/teaching/admission-cases")
  await page.getByLabel("待分配状态").selectOption("ready")
  const caseTable = page.getByRole("table", { name: "待分配名单" })
  const caseRow = caseTable.getByRole("row").filter({ hasText: student.name })
  await expect(caseRow).toContainText("已付清")
  await expect(caseRow).toContainText("¥5,000.00")
  await expectResponsiveTable(page, "待分配名单")
  await caseRow.getByRole("button", { name: "确认报名" }).click()

  const enrollmentDialog = page.getByRole("dialog", { name: "确认报名" })
  await enrollmentDialog
    .getByRole("radio", {
      name: `${COHORT_NAME} · ${STANDARD_CLASS_NAME}`,
    })
    .check()
  await expect(
    enrollmentDialog.locator(".enrollmentOptionSummary").filter({
      hasText: "默认学费",
    }),
  ).toContainText("¥12,800.00")
  await expectNoHorizontalOverflow(page)
  const enrollmentResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/v1/admission-cases/${payment.admission_case_id}/confirm-enrollment` &&
      response.request().method() === "POST",
  )
  await enrollmentDialog
    .getByRole("button", { name: "最终确认报名" })
    .click()
  const enrollmentResponse = await enrollmentResponsePromise
  expect(enrollmentResponse.status()).toBe(201)
  const enrollment = (await enrollmentResponse.json()) as EnrollmentResponse
  await expect(enrollmentDialog).toHaveCount(0)

  await page.goto(`/teaching/enrollments/${enrollment.id}`)
  await expect(page.getByRole("heading", { name: student.name })).toBeVisible()
  await expect(page.getByText(STANDARD_CLASS_NAME, { exact: true })).toBeVisible()
  await expect(page.getByText("欠款 ¥7,800.00", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)

  await page.getByRole("button", { name: "转班", exact: true }).click()
  const transferDialog = page.getByRole("dialog", { name: "转班" })
  await transferDialog.getByRole("radio", { name: ELITE_CLASS_NAME }).check()
  await transferDialog.getByLabel("转班原因").fill("E2E 验证普通班转入菁英班")
  await expectNoHorizontalOverflow(page)
  const transferResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/v1/enrollments/${enrollment.id}/transfer` &&
      response.request().method() === "POST",
  )
  await transferDialog.getByRole("button", { name: "确认转班" }).click()
  const transferResponse = await transferResponsePromise
  expect(transferResponse.status()).toBe(200)
  await expect(transferDialog).toHaveCount(0)
  await expect(page.getByText(ELITE_CLASS_NAME, { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)

  await page.getByRole("button", { name: "退营并退款" }).click()
  const withdrawalDialog = page.getByRole("dialog", { name: "退营并退款" })
  await withdrawalDialog.getByLabel("退营日期").fill("2030-08-07")
  await withdrawalDialog
    .getByRole("radio", { name: "按剩余日期退款" })
    .check()
  const previewResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/v1/enrollments/${enrollment.id}/withdrawal-refund-preview` &&
      response.request().method() === "POST",
  )
  await withdrawalDialog.getByRole("button", { name: "计算退款" }).click()
  const previewResponse = await previewResponsePromise
  expect(previewResponse.status()).toBe(200)
  const preview = page.getByRole("region", { name: "退款计算预览" })
  await expect(preview).toContainText("共 10 天")
  await expect(preview).toContainText("剩余 3 天")
  await expect(preview).toContainText("¥3,840.00")
  await expectNoHorizontalOverflow(page)
  await withdrawalDialog.getByLabel("退营原因").fill("E2E 验证按剩余日期退营退款")
  const withdrawalResponsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname ===
        `/api/v1/enrollments/${enrollment.id}/withdrawal-refund-confirm` &&
      response.request().method() === "POST",
  )
  await withdrawalDialog
    .getByRole("button", { name: "二次确认退营并退款" })
    .click()
  const withdrawalResponse = await withdrawalResponsePromise
  expect(withdrawalResponse.status()).toBe(200)
  const withdrawn = (await withdrawalResponse.json()) as EnrollmentResponse
  expect(withdrawn.status).toBe("cancelled")
  await expect(withdrawalDialog).toHaveCount(0)
  await expect(page.getByText("已取消", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
})
