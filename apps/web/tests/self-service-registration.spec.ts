import { expect, test } from "@playwright/test"
import type { Page, Response } from "@playwright/test"

import {
  createAdminRequest,
  expectNoHorizontalOverflow,
  loginWithIdentifier,
  logout,
  makeCnId,
  openNavigationItem,
  readRegistrationTestCode,
} from "./helpers"
import { E2E_CREDENTIALS } from "./e2eCredentials"

type RegistrationPurpose = "student" | "guardian" | "staff_applicant"
type IdentifierKind = "phone" | "email"

const ADULT_PHONE = "13600009101"
const ADULT_EMAIL = "registration-adult-e2e@example.com"
const EXACT_CLAIM_PHONE = "13600009001"
const EXISTING_ACCOUNT_PHONE = E2E_CREDENTIALS.registration_existing.identifier
const EXISTING_ACCOUNT_EMAIL = "registration-existing-account@example.com"
const MINOR_PHONE = "13600009102"
const GUARDIAN_PHONE = "13600009103"
const EMAIL_ONLY_EMAIL = E2E_CREDENTIALS.registration_email_only.identifier
const EMAIL_ONLY_ADDED_PHONE = "13600009104"
const EMAIL_ONLY_PERSON_ID = "f6100000-0000-4000-8000-000000000005"
const STAFF_PHONE = "13600009105"

const ADULT_PASSWORD = "E2EAdult26Aa"
const EMAIL_PASSWORD = "E2EMail26Aa"
const EXACT_PASSWORD = "E2EExact26Aa"
const MINOR_PASSWORD = "E2EMinor26Aa"
const GUARDIAN_PASSWORD = "E2ERegGuard26Aa"
const STAFF_PASSWORD = "E2EStaff26Aa"
const EXISTING_PASSWORD = E2E_CREDENTIALS.registration_existing.password
const RESET_PASSWORD = "E2EReset26Aa"
const EMAIL_ONLY_PASSWORD = E2E_CREDENTIALS.registration_email_only.password
const COACH_ROLE_ID = "10000000-0000-4000-8000-000000000002"

type ManualPaymentReceipt = {
  id: string
  admission_case_id: string
  order_number: string
  source_kind: "manual"
  payer_person_id: string | null
  amount_cents: number | null
  currency: "CNY"
}

async function assertPage(page: Page, heading: string): Promise<void> {
  await expect(page.getByRole("heading", { name: heading, exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
}

async function beginRegistration(
  page: Page,
  input: {
    purpose: RegistrationPurpose
    purposeLabel: RegExp
    identifierKind: IdentifierKind
    identifier: string
  },
): Promise<void> {
  await page.goto("/register")
  await assertPage(page, "创建账号")
  await page.getByRole("radio", { name: input.purposeLabel }).click()
  await page.getByRole("radio", {
    name: input.identifierKind === "phone" ? "手机号" : "邮箱",
    exact: true,
  }).click()
  await page.locator("#registration-identifier").fill(input.identifier)
  const challengeResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /\/registration\/applications\/[0-9a-f-]+\/challenge$/.test(
        new URL(response.url()).pathname,
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送验证码" }).click()
  const challenge = await challengeResponse
  expect(challenge.status()).toBe(201)
  const body = await challenge.json() as { challenge_id: string }
  await assertPage(page, "验证联系方式")
  const code = await readRegistrationTestCode({
    challengeId: body.challenge_id,
    destination: input.identifier,
    purpose: "registration",
  })
  await page.getByLabel("验证码").fill(code)
  const verifyResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /\/registration\/applications\/[0-9a-f-]+\/verify$/.test(
        new URL(response.url()).pathname,
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "验证并继续" }).click()
  expect((await verifyResponse).status()).toBe(200)
  await assertPage(page, "填写实名资料")
}

async function submitProfile(
  page: Page,
  input: {
    fullName: string
    identityNumber: string
    password: string
    completion: "账号注册完成" | "实名资料已保存" | "资料已提交审核"
    staffType?: "coach"
    staffReason?: string
  },
): Promise<void> {
  await page.getByLabel("姓名").fill(input.fullName)
  await page.getByLabel("身份证").fill(input.identityNumber)
  await page.getByLabel("密码", { exact: true }).fill(input.password)
  await page.getByLabel("确认密码").fill(input.password)
  if (input.staffType) {
    await page.getByLabel("申请岗位").selectOption(input.staffType)
    await page.getByLabel("申请说明").fill(input.staffReason ?? "申请加入教学团队")
  }
  await page.getByLabel(/我已阅读并同意实名资料处理条款/).check()
  const finalization = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/registration/me/finalize"),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "提交实名资料" }).click()
  expect((await finalization).status()).toBe(200)
  await expect(page.getByText(input.completion, { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
}

async function openGuardianInvitation(
  page: Page,
  invitationId: string,
  destination: string,
): Promise<void> {
  const token = await readRegistrationTestCode({
    challengeId: invitationId,
    destination,
    purpose: "guardian_invitation",
  })
  await page.goto(`/guardian-invitation#${token}`)
  await expect.poll(() => new URL(page.url()).hash).toBe("")
  await assertPage(page, "确认监护邀请")
  await expect(page.locator("body")).not.toContainText(token)
  expect(await page.evaluate(() => ({
    local: Object.values(localStorage),
    session: Object.values(sessionStorage),
  }))).toEqual({ local: [], session: [] })
}

async function loginFromGuardianInvitation(
  page: Page,
  identifier: string,
  password: string,
): Promise<void> {
  await page.getByRole("link", { name: "已有账号登录" }).click()
  await assertPage(page, "登录工作台")
  await page.getByLabel("手机号或邮箱").fill(identifier)
  await page.getByLabel("密码", { exact: true }).fill(password)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page).toHaveURL(/\/guardian-invitation$/)
  await assertPage(page, "确认监护邀请")
}

async function acceptHandoffInvitation(
  page: Page,
  expectedStudentFinalized: boolean,
): Promise<void> {
  await page.getByLabel(/本人确认接受监护关系/).check()
  const acceptanceResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(
        "/registration/guardian-invitations/accept",
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "确认建立监护关系" }).click()
  const acceptance = await acceptanceResponse
  expect(acceptance.status()).toBe(200)
  expect((await acceptance.json() as { student_finalized: boolean }).student_finalized)
    .toBe(expectedStudentFinalized)
  await expect(page.getByText("监护关系已建立", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
}

async function submitEmailOnlyManualPayment(
  page: Page,
  note: string,
): Promise<Response> {
  await page.goto("/teaching/payments")
  await assertPage(page, "付款与退款")
  await page.getByRole("button", { name: "登记付款" }).click()
  const dialog = page.getByRole("dialog", { name: "登记付款" })
  await dialog.getByLabel("学员 ID").fill(EMAIL_ONLY_PERSON_ID)
  await dialog.getByLabel("付款金额（元）").fill("5000.00")
  await dialog.getByLabel("备注").fill(note)
  await expectNoHorizontalOverflow(page)
  const response = page.waitForResponse(
    (candidate) => candidate.request().method() === "POST"
      && new URL(candidate.url()).pathname === "/api/v1/payments",
    { timeout: 15_000 },
  )
  await dialog.getByRole("button", { name: "确认登记" }).click()
  return response
}

test("completes self-service registration, guardian, review, recovery, and phone-readiness journeys", async ({ page }) => {
  test.setTimeout(180_000)

  await beginRegistration(page, {
    purpose: "student",
    purposeLabel: /^学员/,
    identifierKind: "phone",
    identifier: ADULT_PHONE,
  })
  await submitProfile(page, {
    fullName: "E2E自助注册成年学员",
    identityNumber: makeCnId("20010102", 221),
    password: ADULT_PASSWORD,
    completion: "账号注册完成",
  })
  await loginWithIdentifier(page, ADULT_PHONE, ADULT_PASSWORD)
  await expect(page.getByText("餐饮偏好待完善", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await logout(page)

  await beginRegistration(page, {
    purpose: "student",
    purposeLabel: /^学员/,
    identifierKind: "email",
    identifier: ADULT_EMAIL,
  })
  await submitProfile(page, {
    fullName: "E2E邮箱自助注册学员",
    identityNumber: makeCnId("20020102", 222),
    password: EMAIL_PASSWORD,
    completion: "账号注册完成",
  })
  await loginWithIdentifier(page, ADULT_EMAIL, EMAIL_PASSWORD)
  await expect(page.locator(".shellUserName")).toHaveText("E2E邮箱自助注册学员")
  await logout(page)

  await beginRegistration(page, {
    purpose: "student",
    purposeLabel: /^学员/,
    identifierKind: "phone",
    identifier: EXACT_CLAIM_PHONE,
  })
  await submitProfile(page, {
    fullName: "E2E注册精确认领学员",
    identityNumber: makeCnId("20000102", 211),
    password: EXACT_PASSWORD,
    completion: "账号注册完成",
  })
  await loginWithIdentifier(page, EXACT_CLAIM_PHONE, EXACT_PASSWORD)
  await expect(page.locator(".shellUserName")).toHaveText("E2E注册精确认领学员")
  await logout(page)

  await beginRegistration(page, {
    purpose: "student",
    purposeLabel: /^学员/,
    identifierKind: "phone",
    identifier: MINOR_PHONE,
  })
  await submitProfile(page, {
    fullName: "E2E未成年自助注册学员",
    identityNumber: makeCnId("20120102", 223),
    password: MINOR_PASSWORD,
    completion: "实名资料已保存",
  })
  await page.getByRole("link", { name: "管理监护人邀请" }).click()
  await assertPage(page, "监护人完善区")
  await page.getByLabel("监护人手机号或邮箱").fill(GUARDIAN_PHONE)
  await page.getByLabel("与学员关系").fill("母亲")
  await page.getByLabel("设为主要监护人").check()
  const invitationResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/registration/me/guardian-invitations"),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送监护人邀请" }).click()
  const invitation = await invitationResponse
  expect(invitation.status()).toBe(201)
  const firstMinorInvitationId = ((await invitation.json()) as { id: string }).id
  await expect(page.getByText("等待监护人确认", { exact: true })).toBeVisible()

  const resendResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith(
        `/registration/me/guardian-invitations/${firstMinorInvitationId}/resend`,
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: /重新发送给 136\*\*\*\*9103/ }).click()
  const resentInvitation = await resendResponse
  expect(resentInvitation.status()).toBe(201)
  const resentMinorInvitationId = (
    await resentInvitation.json() as { id: string }
  ).id
  expect(resentMinorInvitationId).not.toBe(firstMinorInvitationId)

  const revokeResponse = page.waitForResponse(
    (response) => response.request().method() === "DELETE"
      && new URL(response.url()).pathname.endsWith(
        `/registration/me/guardian-invitations/${resentMinorInvitationId}`,
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: /撤销 136\*\*\*\*9103/ }).click()
  expect((await revokeResponse).status()).toBe(200)
  await expect(page.getByText("邀请已撤销", { exact: true })).toBeVisible()

  await page.getByLabel("监护人手机号或邮箱").fill(GUARDIAN_PHONE)
  await page.getByLabel("与学员关系").fill("母亲")
  await page.getByLabel("设为主要监护人").check()
  const replacementResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/registration/me/guardian-invitations"),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送监护人邀请" }).click()
  const replacement = await replacementResponse
  expect(replacement.status()).toBe(201)
  const minorInvitationId = ((await replacement.json()) as { id: string }).id

  await openGuardianInvitation(page, minorInvitationId, GUARDIAN_PHONE)
  await page.getByRole("link", { name: "注册监护人账号" }).click()
  await assertPage(page, "创建账号")
  await expect(page.getByText("账号用途已锁定为监护人", { exact: true })).toBeVisible()
  const guardianChallengeResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && /\/registration\/applications\/[0-9a-f-]+\/challenge$/.test(
        new URL(response.url()).pathname,
      ),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "向受邀联系方式发送验证码" }).click()
  const guardianChallenge = await guardianChallengeResponse
  expect(guardianChallenge.status()).toBe(201)
  const guardianChallengeId = (
    await guardianChallenge.json() as { challenge_id: string }
  ).challenge_id
  const guardianCode = await readRegistrationTestCode({
    challengeId: guardianChallengeId,
    destination: GUARDIAN_PHONE,
    purpose: "registration",
  })
  await assertPage(page, "验证联系方式")
  await page.getByLabel("验证码").fill(guardianCode)
  await page.getByRole("button", { name: "验证并继续" }).click()
  await assertPage(page, "填写实名资料")
  await submitProfile(page, {
    fullName: "E2E自助注册监护人",
    identityNumber: makeCnId("19850102", 224),
    password: GUARDIAN_PASSWORD,
    completion: "账号注册完成",
  })
  await page.getByRole("link", { name: "登录并确认邀请" }).click()
  await assertPage(page, "登录工作台")
  await page.getByLabel("手机号或邮箱").fill(GUARDIAN_PHONE)
  await page.getByLabel("密码", { exact: true }).fill(GUARDIAN_PASSWORD)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page).toHaveURL(/\/guardian-invitation$/)
  await assertPage(page, "确认监护邀请")
  await acceptHandoffInvitation(page, true)
  await page.getByRole("link", { name: "返回工作台" }).click()
  await assertPage(page, "工作台")
  await expect(page.getByText("餐饮偏好待完善", { exact: true })).toHaveCount(0)
  await logout(page)

  await loginWithIdentifier(page, MINOR_PHONE, MINOR_PASSWORD)
  await expect(page.getByText("餐饮偏好待完善", { exact: true })).toBeVisible()
  await logout(page)

  await loginWithIdentifier(page, ADULT_PHONE, ADULT_PASSWORD)
  await openNavigationItem(page, "监护邀请")
  await assertPage(page, "成年学员监护邀请")
  await page.getByLabel("监护人手机号或邮箱").fill(GUARDIAN_PHONE)
  await page.getByLabel("与学员关系").fill("家长")
  const adultInvitationResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/registration/guardian-invitations"),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送监护人邀请" }).click()
  const adultInvitation = await adultInvitationResponse
  expect(adultInvitation.status()).toBe(201)
  const optionalInvitationId = (
    await adultInvitation.json() as { id: string }
  ).id
  await expect(page.getByText("136****9103", { exact: true })).toBeVisible()
  await logout(page)

  await openGuardianInvitation(page, optionalInvitationId, GUARDIAN_PHONE)
  await loginFromGuardianInvitation(page, GUARDIAN_PHONE, GUARDIAN_PASSWORD)
  await acceptHandoffInvitation(page, false)
  await page.getByRole("link", { name: "返回工作台" }).click()
  await assertPage(page, "工作台")
  await logout(page)

  await beginRegistration(page, {
    purpose: "staff_applicant",
    purposeLabel: /^工作人员申请/,
    identifierKind: "phone",
    identifier: STAFF_PHONE,
  })
  await submitProfile(page, {
    fullName: "E2E自助注册教练申请人",
    identityNumber: makeCnId("19930102", 225),
    password: STAFF_PASSWORD,
    completion: "账号注册完成",
    staffType: "coach",
    staffReason: "申请承担英雄联盟项目教学工作",
  })

  await loginWithIdentifier(
    page,
    E2E_CREDENTIALS.admin.identifier,
    E2E_CREDENTIALS.admin.password,
  )
  await page.goto("/settings/staff-applications")
  await assertPage(page, "身份申请")
  const staffApplication = page.locator(".registrationAdminList article").filter({
    hasText: "教练",
  })
  await expect(staffApplication).toHaveCount(1)
  await staffApplication.getByRole("button", { name: "查看完整资料" }).click()
  await expect(page.getByText("E2E自助注册教练申请人", { exact: true })).toBeVisible()
  await expect(
    page.getByLabel("审核备注（批准选填，驳回必填）"),
  ).toHaveValue("")
  await page.getByRole("button", { name: "批准申请" }).click()
  await expect(staffApplication).toHaveCount(0)
  await logout(page)

  await loginWithIdentifier(page, STAFF_PHONE, STAFF_PASSWORD)
  await page.goto("/register/status")
  await assertPage(page, "注册状态")
  await expect(page.getByText("工作人员申请已通过", { exact: true })).toBeVisible()

  const staffAccount = await page.evaluate(async () => {
    const response = await fetch("/api/v1/auth/me", { credentials: "include" })
    if (!response.ok) throw new Error(`staff account lookup failed: ${response.status}`)
    return await response.json() as { account_id: string }
  })
  const evaluationContextResponse = page.waitForResponse(
    (response) => response.request().method() === "GET"
      && new URL(response.url()).pathname === "/api/v1/evaluations/context",
    { timeout: 15_000 },
  )
  await page.goto("/evaluations")
  expect((await evaluationContextResponse).status()).toBe(200)
  await assertPage(page, "评价中心")

  const roleAdministrator = await createAdminRequest()
  try {
    const removed = await roleAdministrator.admin.delete(
      `/api/v1/accounts/${staffAccount.account_id}/roles/${COACH_ROLE_ID}`,
      { headers: { "X-CSRF-Token": roleAdministrator.csrfToken } },
    )
    expect(removed.status(), await removed.text()).toBe(204)
  } finally {
    await roleAdministrator.admin.dispose()
  }

  const revokedEvaluationContext = await page.evaluate(async () => {
    const response = await fetch("/api/v1/evaluations/context", {
      credentials: "include",
    })
    return {
      status: response.status,
      body: await response.json() as { code?: string },
    }
  })
  expect(revokedEvaluationContext).toMatchObject({
    status: 403,
    body: { code: "evaluation_access_denied" },
  })
  await page.reload()
  await expect(page).toHaveURL(/\/$/)
  await assertPage(page, "工作台")
  await logout(page)

  await beginRegistration(page, {
    purpose: "student",
    purposeLabel: /^学员/,
    identifierKind: "phone",
    identifier: EXISTING_ACCOUNT_PHONE,
  })
  await submitProfile(page, {
    fullName: "E2E注册既有账号学员",
    identityNumber: makeCnId("19990102", 212),
    password: "E2EAttempt26Aa",
    completion: "账号注册完成",
  })
  await loginWithIdentifier(page, EXISTING_ACCOUNT_PHONE, EXISTING_PASSWORD)
  await expect(page.locator(".shellUserName")).toHaveText("E2E注册既有账号学员")
  await logout(page)

  await page.goto("/forgot-password")
  await assertPage(page, "找回密码")
  await page.getByLabel("手机号或邮箱").fill(EXISTING_ACCOUNT_EMAIL)
  const resetChallengeResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/auth/password-reset/challenges"),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送验证码" }).click()
  const resetChallenge = await resetChallengeResponse
  expect(resetChallenge.status()).toBe(201)
  const resetBody = await resetChallenge.json() as { challenge_id: string }
  const resetCode = await readRegistrationTestCode({
    challengeId: resetBody.challenge_id,
    destination: EXISTING_ACCOUNT_EMAIL,
    purpose: "password_reset",
  })
  await page.getByLabel("验证码").fill(resetCode)
  await page.getByLabel("新密码", { exact: true }).fill(RESET_PASSWORD)
  await page.getByLabel("确认新密码").fill(RESET_PASSWORD)
  await page.getByRole("button", { name: "重置密码" }).click()
  await expect(page.getByText("如果账号信息有效，密码已完成更新", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await loginWithIdentifier(page, EXISTING_ACCOUNT_PHONE, RESET_PASSWORD)
  await logout(page)

  await loginWithIdentifier(page, EMAIL_ONLY_EMAIL, EMAIL_ONLY_PASSWORD)
  await expect(page.locator(".shellUserName")).toHaveText("E2E注册仅邮箱学员")
  await logout(page)

  await loginWithIdentifier(
    page,
    E2E_CREDENTIALS.admin.identifier,
    E2E_CREDENTIALS.admin.password,
  )
  const emailVerifiedPayment = await submitEmailOnlyManualPayment(
    page,
    "邮箱验证后付款",
  )
  expect(emailVerifiedPayment.status()).toBe(201)
  const firstPayment = await emailVerifiedPayment.json() as ManualPaymentReceipt
  expect(firstPayment).toMatchObject({
    source_kind: "manual",
    payer_person_id: EMAIL_ONLY_PERSON_ID,
    amount_cents: 500_000,
    currency: "CNY",
  })
  expect(firstPayment.id).toMatch(/^[0-9a-f-]{36}$/)
  await expect(page.getByRole("dialog", { name: "登记付款" })).toHaveCount(0)
  await expect(
    page.getByRole("table", { name: "付款记录" })
      .getByRole("row")
      .filter({ hasText: firstPayment.order_number }),
  ).toContainText("¥5,000.00")
  await logout(page)

  await loginWithIdentifier(page, EMAIL_ONLY_EMAIL, EMAIL_ONLY_PASSWORD)
  await page.goto("/me/profile")
  await assertPage(page, "个人资料")
  await expect(page.getByRole("button", { name: "添加手机号" })).toBeVisible()
  await page.getByRole("button", { name: "添加手机号" }).click()
  await page.getByLabel("新手机号").fill(EMAIL_ONLY_ADDED_PHONE)
  await page.getByLabel("当前密码").fill(EMAIL_ONLY_PASSWORD)
  const phoneChallengeResponse = page.waitForResponse(
    (response) => response.request().method() === "POST"
      && new URL(response.url()).pathname.endsWith("/auth/identifiers/challenges"),
    { timeout: 15_000 },
  )
  await page.getByRole("button", { name: "发送验证码" }).click()
  const phoneChallenge = await phoneChallengeResponse
  expect(phoneChallenge.status()).toBe(201)
  const phoneBody = await phoneChallenge.json() as { challenge_id: string }
  const phoneCode = await readRegistrationTestCode({
    challengeId: phoneBody.challenge_id,
    destination: EMAIL_ONLY_ADDED_PHONE,
    purpose: "add_login_identifier",
  })
  await page.getByLabel("验证码").fill(phoneCode)
  await page.getByRole("button", { name: "确认绑定" }).click()
  await expect(page.getByText("136****9104 · 已验证", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "添加手机号" })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)
  await logout(page)

  await loginWithIdentifier(
    page,
    E2E_CREDENTIALS.admin.identifier,
    E2E_CREDENTIALS.admin.password,
  )
  const phoneAddedPayment = await submitEmailOnlyManualPayment(
    page,
    "补绑手机号后的新付款操作",
  )
  expect(phoneAddedPayment.status()).toBe(201)
  const secondPayment = await phoneAddedPayment.json() as ManualPaymentReceipt
  expect(secondPayment).toMatchObject({
    source_kind: "manual",
    payer_person_id: EMAIL_ONLY_PERSON_ID,
    amount_cents: 500_000,
    currency: "CNY",
  })
  expect(secondPayment.id).not.toBe(firstPayment.id)
  const firstOperationKey = (emailVerifiedPayment.request().postDataJSON() as { idempotency_key: string }).idempotency_key
  const secondOperationKey = (phoneAddedPayment.request().postDataJSON() as { idempotency_key: string }).idempotency_key
  expect(secondOperationKey).not.toBe(firstOperationKey)
  await expect(page.getByRole("dialog", { name: "登记付款" })).toHaveCount(0)
  await logout(page)

})
