import { expect, test } from "@playwright/test"
import type { APIResponse, Page, Response } from "@playwright/test"

import {
  createAdminRequest,
  expectNoHorizontalOverflow,
  loginWithIdentifier,
  makeCnId,
  readRegistrationTestCode,
} from "./helpers"
import { E2E_CREDENTIALS } from "./e2eCredentials"

const SPLIT_PHONE = "13600009003"
const PRIVACY_PASSWORD = "E2EPrivacy26Aa"
const REVIEWER_PHONE = E2E_CREDENTIALS.registration_reviewer.identifier
const REVIEWER_PASSWORD = E2E_CREDENTIALS.registration_reviewer.password
const REVIEWER_ACCOUNT_ID = "f6110000-0000-4000-8000-000000000006"
const REVIEW_OVERRIDE_ID = "f6130000-0000-4000-8000-000000000006"
const CANDIDATE_IDS = [
  "f6100000-0000-4000-8000-000000000003",
  "f6100000-0000-4000-8000-000000000004",
]

function installPrivacyMonitor(page: Page, initialForbidden: string[]) {
  const forbidden = new Set(initialForbidden)
  const pending: Promise<void>[] = []
  const failures: string[] = []

  async function inspectValue(pathname: string, value: unknown): Promise<void> {
      const scrubCsrf = (input: unknown): unknown => {
        if (Array.isArray(input)) return input.map(scrubCsrf)
        if (!input || typeof input !== "object") return input
        return Object.fromEntries(Object.entries(input as Record<string, unknown>).map(
          ([key, item]) => {
            if (key === "csrf_token" && typeof item === "string") {
              forbidden.add(item)
              return [key, "[EPHEMERAL_CSRF]"]
            }
            return [key, scrubCsrf(item)]
          },
        ))
      }
      const rendered = JSON.stringify(scrubCsrf(value))
      for (const secret of forbidden) {
        if (secret && rendered.includes(secret)) {
          failures.push(`forbidden response value at ${pathname}`)
        }
      }
      if (/\b1[3-9]\d{9}\b/.test(rendered)) failures.push(`phone at ${pathname}`)
      if (/\b\d{17}[\dX]\b/i.test(rendered)) failures.push(`identity at ${pathname}`)
      if (/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/.test(rendered)) failures.push(`email at ${pathname}`)
      if (/postgres(?:ql)?(?:\+psycopg)?:\/\//i.test(rendered)) failures.push(`database URL at ${pathname}`)
      if (/(?:\/Users\/|\/private\/|\/tmp\/|[A-Za-z]:\\)/.test(rendered)) failures.push(`absolute path at ${pathname}`)
      if (/erp_(?:registration_)?session/i.test(rendered)) failures.push(`cookie name at ${pathname}`)
  }

  function inspect(response: Response): void {
    const url = new URL(response.url())
    if (!url.pathname.startsWith("/api/v1/")) return
    const contentType = response.headers()["content-type"] ?? ""
    if (!contentType.includes("application/json")) return
    const job = response.finished()
      .then((failure) => {
        if (failure) throw failure
        return response.json()
      })
      .then((value: unknown) => inspectValue(url.pathname, value))
      .catch(() => failures.push(`unreadable JSON at ${url.pathname}`))
    pending.push(job)
  }

  page.on("response", inspect)
  return {
    forbid(value: string) {
      forbidden.add(value)
    },
    async inspectApiResponse(response: APIResponse) {
      const url = new URL(response.url())
      const contentType = response.headers()["content-type"] ?? ""
      if (!url.pathname.startsWith("/api/v1/") || !contentType.includes("application/json")) {
        return
      }
      const job = response.json()
        .then((value: unknown) => inspectValue(url.pathname, value))
        .catch(() => failures.push(`unreadable JSON at ${url.pathname}`))
      pending.push(job)
      await job
    },
    async assertClean() {
      let observed = -1
      while (observed !== pending.length) {
        observed = pending.length
        await Promise.all(pending)
      }
      expect(failures).toEqual([])
    },
  }
}

test("keeps registration JSON private and clears reviewer state after live permission revocation", async ({ page }) => {
  test.setTimeout(90_000)
  const identity = makeCnId("19980102", 213)
  const monitor = installPrivacyMonitor(page, [
    SPLIT_PHONE,
    REVIEWER_PHONE,
    PRIVACY_PASSWORD,
    REVIEWER_PASSWORD,
    identity,
    "E2E注册身份冲突档案",
    "E2E注册联系方式冲突档案",
    "实名冲突内部审核备注",
    ...CANDIDATE_IDS,
  ])

  await page.goto("/register")
  await expect(page.getByRole("heading", { name: "创建账号" })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByRole("radio", { name: /^学员/ }).click()
  await page.getByRole("radio", { name: "手机号", exact: true }).click()
  await page.locator("#registration-identifier").fill(SPLIT_PHONE)
  const challengeResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /\/registration\/applications\/[0-9a-f-]+\/challenge$/.test(
        new URL(response.url()).pathname,
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送验证码" }).click()
  const challenge = await challengeResponse
  const challengeBody = await challenge.json() as { challenge_id: string }
  await expect(page.getByRole("heading", { name: "验证联系方式" })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  const code = await readRegistrationTestCode({
    challengeId: challengeBody.challenge_id,
    destination: SPLIT_PHONE,
    purpose: "registration",
  })
  monitor.forbid(code)
  await page.getByLabel("验证码").fill(code)
  await page.getByRole("button", { name: "验证并继续" }).click()
  await expect(page.getByRole("heading", { name: "填写实名资料" })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await page.getByLabel("姓名").fill("E2E注册身份冲突档案")
  await page.getByLabel("身份证").fill(identity)
  await page.getByLabel("密码", { exact: true }).fill(PRIVACY_PASSWORD)
  await page.getByLabel("确认密码").fill(PRIVACY_PASSWORD)
  await page.getByLabel(/我已阅读并同意实名资料处理条款/).check()
  await page.getByRole("button", { name: "提交实名资料" }).click()
  await expect(page.getByText("资料已提交审核", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)

  const portalReady = page.waitForResponse(
    (response) => new URL(response.url()).pathname === "/api/v1/portal/context"
      && response.status() === 200,
    { timeout: 15_000 },
  )
  await loginWithIdentifier(page, REVIEWER_PHONE, REVIEWER_PASSWORD)
  await portalReady
  await page.goto("/settings/registrations")
  await expect(page.getByRole("heading", { name: "注册处理" })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  const application = page.locator(".registrationAdminList article")
  await expect(application).toHaveCount(1)
  const detailResponsePromise = page.waitForResponse(
    (response) => response.request().method() === "GET"
      && /\/api\/v1\/registration-admin\/applications\/[0-9a-f-]+$/.test(
        new URL(response.url()).pathname,
      ),
    { timeout: 15_000 },
  )
  await application.getByRole("button", { name: "处理申请" }).click()
  const detailResponse = await detailResponsePromise
  expect(detailResponse.status()).toBe(200)
  const detailPath = new URL(detailResponse.url()).pathname
  await expect(page.getByRole("heading", { name: "审核注册申请" })).toBeVisible()
  await expect(page.getByText("E2E注册身份冲突档案", { exact: true })).toHaveCount(0)
  await expect(page.getByText(identity, { exact: true })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)

  const admin = await createAdminRequest()
  const removal = await admin.admin.delete(
    `/api/v1/accounts/${REVIEWER_ACCOUNT_ID}/permission-overrides/${REVIEW_OVERRIDE_ID}`,
    { headers: { "X-CSRF-Token": admin.csrfToken } },
  )
  expect(removal.status()).toBe(204)
  await monitor.inspectApiResponse(removal)
  await admin.admin.dispose()

  const revokedDetailResponse = page.waitForResponse(
    (response) => response.request().method() === "GET"
      && new URL(response.url()).pathname === detailPath,
    { timeout: 15_000 },
  )
  const revokedDetail = await page.evaluate(async (path) => {
    const response = await fetch(path, { credentials: "include" })
    return {
      status: response.status,
      cacheControl: response.headers.get("Cache-Control"),
      body: await response.json() as unknown,
    }
  }, detailPath)
  expect((await revokedDetailResponse).status()).toBe(404)
  expect(revokedDetail).toMatchObject({
    status: 404,
    cacheControl: "no-store, private",
    body: {
      status: 404,
      code: "registration_application_not_found",
    },
  })

  await page.getByRole("button", { name: "刷新" }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "审核注册申请" })).toHaveCount(0)
  await page.goto("/settings/registrations")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await monitor.assertClean()
})
