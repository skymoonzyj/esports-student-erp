import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"
import type { APIRequestContext, Browser, Page, Response } from "@playwright/test"

import type { IntakeInput, PersonKind } from "../src/assistedPeople/types"
import type { ClaimDetail } from "../src/profileClaims/adminApi"
import { E2E_CREDENTIALS } from "./e2eCredentials"
import {
  createAdminRequest, expectNoHorizontalOverflow, loginAsAdmin, loginWithIdentifier,
  logout, makeCnId, readRegistrationTestCode, refocus,
} from "./helpers"

// Existing synthetic seed_assisted_personnel_e2e aliases, not new fixture promises.
const TEAM = "e9100000-0000-4000-8000-000000000001"
const COACH = "e9000000-0000-4000-8000-000000000001"
const ACTORS = { coach: "19900009001", manager: "19900009002", administration: "19900009005", reviewBothAdmin: "19900009044" }
const PASSWORD = E2E_CREDENTIALS.admin.password
const INTAKE = "/api/v1/personnel-intake"
const CLAIMS = `${INTAKE}/profile-claims`
const PUBLIC = "/api/v1/auth/profile-claims"
const ACTIVATION = "/api/v1/auth/profile-activation"
const REVIEW = "/settings/registrations/claims"
const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:18080"
const WAITING = "邮箱已验证，申请等待管理员核对。邮箱验证不代表档案匹配或账号已激活。"

type RequestView = { id: string; status: string; version: number; approved_person_id: string | null; scoped_payload: IntakeInput | null }
type Person = { id: string; account_status: string; assisted_kind: PersonKind; student_number: string | null }
type Profile = { person_id: string; nickname: string | null; profile_notes: string | null }
type Member = { person_id: string; account_status: string; [key: string]: unknown }
type Room = { current_stays: Array<{ id: string; person_id: string; version: number }>; history: unknown[]; transfers: unknown[] }
type PublicStatus = { status: string; reason: string | null }

// No trace/HAR/video/screenshot may persist identity, mailbox codes or passwords.
test.use({ trace: "off", screenshot: "off", video: "off" })
test.describe.configure({ mode: "serial" })

function responseFor(page: Page, path: string, method = "POST"): Promise<Response> {
  return page.waitForResponse((response) => response.request().method() === method
    && new URL(response.url()).pathname === path)
}

async function browserPostStatus(page: Page, path: string, data: { code: string; password: string }): Promise<number> {
  // APIRequestContext does not model Chromium's loopback Secure-cookie rule.
  // Exercise the actual same-origin browser cookie jar for negative probes.
  return page.evaluate(async ({ path, data }) => (await fetch(path, {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })).status, { path, data })
}

async function get<T>(api: APIRequestContext, path: string): Promise<T> {
  const response = await api.get(path)
  expect(response.status()).toBe(200)
  return await response.json() as T
}

async function privateOnly(page: Page, values: string[]) {
  // Assert only a boolean so failing assertions never print the sensitive value.
  const leaked = await page.evaluate((secrets) => {
    const sources = [decodeURIComponent(location.href), ...Object.keys(localStorage), ...Object.values(localStorage),
      ...Object.keys(sessionStorage), ...Object.values(sessionStorage), document.cookie]
    return secrets.some((secret) => secret.length > 0 && sources.some((source) => source.includes(secret)))
  }, values)
  expect(leaked, "No applicant PII, OTP or password in URL or script-readable persistent state").toBe(false)
  await expectNoHorizontalOverflow(page)
}

function facts(kind: PersonKind, minor: boolean, sequence: number, name?: string): IntakeInput {
  return {
    kind, full_name: name ?? `E2E认领${minor ? "未成年" : "成年"}${kind}`,
    identity_number: makeCnId(minor ? "20150103" : "20000103", sequence),
    phone: "19900009888", email: null, sex: "male", date_of_birth: minor ? "2015-01-03" : "2000-01-03",
    coach_person_ids: kind === "player" ? [] : [COACH], team_id: kind === "student" ? null : TEAM,
    guardians: minor ? [{ full_name: `E2E认领监护${sequence}`, identity_number: makeCnId("19800103", sequence),
      phone: "19900009889", email: null, sex: "male", date_of_birth: "1980-01-03", relationship: "father", is_primary: true }] : [],
  }
}

async function fillIntake(page: Page, input: IntakeInput) {
  await page.getByLabel("人员身份", { exact: true }).selectOption(input.kind)
  for (const [label, value] of [["姓名", input.full_name], ["身份证", input.identity_number],
    ["手机号", input.phone], ["出生日期", input.date_of_birth]]) await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByLabel("性别", { exact: true }).selectOption(input.sex)
  await expect(page.getByLabel("邮箱", { exact: true })).toHaveValue("")
  if (input.kind !== "player") await page.getByRole("checkbox", { name: "E2E协助独立教练", exact: true }).check()
  if (input.kind !== "student") await page.getByLabel("所属队伍", { exact: true }).selectOption(TEAM)
  for (const guardian of input.guardians) {
    await page.getByRole("button", { name: "添加监护人", exact: true }).click()
    for (const [label, value] of [["监护人姓名", guardian.full_name], ["监护人身份证", guardian.identity_number],
      ["监护人手机号", guardian.phone], ["监护人出生日期", guardian.date_of_birth], ["与本人关系", guardian.relationship]]) {
      await page.getByLabel(label, { exact: true }).fill(value)
    }
    await page.getByLabel("监护人性别", { exact: true }).selectOption(guardian.sex)
    await expect(page.getByLabel("监护人邮箱", { exact: true })).toHaveValue("")
  }
  await expectNoHorizontalOverflow(page)
}

async function openIntakeReview(page: Page, request: string) {
  await page.goto("/people/assisted-review")
  await expect(page.getByRole("heading", { name: "建档审核", exact: true })).toBeVisible()
  const list = await get<{ items: RequestView[] }>(page.request, `${INTAKE}/requests?view=review&page=1&page_size=20`)
  const index = list.items.findIndex((item) => item.id === request)
  expect(index).toBeGreaterThanOrEqual(0)
  const detailResponse = responseFor(page, `${INTAKE}/requests/${request}`, "GET")
  await page.getByRole("button", { name: `查看申请 ${index + 1}`, exact: true }).click()
  await detailResponse
  await expect(page.getByRole("heading", { name: "申请详情", exact: true })).toBeVisible()
}

async function reviewerPage(browser: Browser, applicant: Page, independentAdmin = false) {
  const context = await browser.newContext({ baseURL: BASE_URL, viewport: applicant.viewportSize() })
  const page = await context.newPage()
  if (independentAdmin) await loginWithIdentifier(page, ACTORS.reviewBothAdmin, PASSWORD)
  else await loginAsAdmin(page)
  return { context, page }
}

async function openClaim(page: Page, email: string, status = "pending"): Promise<ClaimDetail> {
  await page.goto(REVIEW)
  await expect(page.getByRole("heading", { name: "档案认领审核", exact: true })).toBeVisible()
  await page.getByLabel("认领状态", { exact: true }).selectOption(status)
  const list = await get<{ items: Array<{ id: string }> }>(page.request, `${CLAIMS}?page=1&page_size=20&status=${status}`)
  // Authorized detail lookup selects an exact synthetic applicant, not a masked
  // name or ordering assumption. No identity/email is put in a URL.
  for (let index = 0; index < list.items.length; index += 1) {
    const detail = await get<ClaimDetail>(page.request, `${CLAIMS}/${list.items[index].id}`)
    if (detail.applicant_email !== email) continue
    const loaded = responseFor(page, `${CLAIMS}/${detail.id}`, "GET")
    await page.getByRole("button", { name: "查看认领详情", exact: true }).nth(index).click()
    expect((await loaded).status()).toBe(200)
    await expect(page.getByRole("region", { name: "认领申请详情", exact: true })).toBeVisible()
    await expectNoHorizontalOverflow(page)
    return detail
  }
  throw new Error("Synthetic claim missing from authorized review queue")
}

async function startClaim(page: Page, input: IntakeInput, email: string) {
  await page.goto("/claim-profile")
  await page.getByLabel("姓名", { exact: true }).fill(input.full_name)
  await page.getByLabel("身份证", { exact: true }).fill(input.identity_number)
  await page.getByLabel("本人邮箱", { exact: true }).fill(email)
  await expect(page.getByLabel("姓名", { exact: true })).toHaveValue(input.full_name)
  await expect(page.getByLabel("身份证", { exact: true })).toHaveValue(input.identity_number)
  await expect(page.getByLabel("本人邮箱", { exact: true })).toHaveValue(email)
  const accepted = responseFor(page, `${PUBLIC}/start`)
  await page.getByRole("button", { name: "请求邮箱验证码", exact: true }).click()
  const response = await accepted
  expect(response.status()).toBe(200)
  const body = await response.json() as { accepted: boolean; resend_available_at: string }
  expect(Object.keys(body).sort()).toEqual(["accepted", "expires_at", "resend_available_at"])
  expect(body.accepted).toBe(true)
  const cookies = await page.context().cookies()
  const cookie = cookies.find((item) => item.name === "erp_profile_claim")
  expect(cookie !== undefined).toBe(true)
  expect(cookie && { httpOnly: cookie.httpOnly, secure: cookie.secure, path: cookie.path, sameSite: cookie.sameSite })
    .toEqual({ httpOnly: true, secure: true, path: PUBLIC, sameSite: "Lax" })
  await expect(page.getByRole("status")).toContainText("此结果不表示存在匹配档案")
  const resendAvailableAt = Date.parse(body.resend_available_at)
  expect(Number.isFinite(resendAvailableAt)).toBe(true)
  return {
    code: await readRegistrationTestCode({ destination: email, purpose: "profile_claim" }),
    resendAvailableAt,
  }
}

async function waitForClaimIdentifierCooldown(resendAvailableAt: number) {
  // Verification issuance uses one 60-second last-sent window per identifier,
  // regardless of claim versus activation purpose. A decoy activation start
  // returns 200 without sending, so wait on the claim response's real deadline.
  const remaining = resendAvailableAt - Date.now()
  expect(remaining).toBeLessThanOrEqual(65_000)
  await expect.poll(() => Date.now(), {
    timeout: Math.max(5_000, remaining + 6_000),
    intervals: [250, 500, 1_000],
  }).toBeGreaterThan(resendAvailableAt + 500)
}

async function verifyClaim(page: Page, code: string): Promise<PublicStatus> {
  await page.getByLabel("验证码", { exact: true }).fill(code)
  const verified = responseFor(page, `${PUBLIC}/verify`)
  await page.getByRole("button", { name: "验证邮箱并申请核对", exact: true }).click()
  const response = await verified
  expect(response.status()).toBe(200)
  const status = await response.json() as PublicStatus
  expect(status).toEqual({ status: "waiting_review", reason: null })
  await expect(page.getByRole("status")).toContainText(WAITING)
  return status
}

test("initial delayed auth restore keeps the public claim form closed until it can retain a draft", async ({ page }) => {
  let releaseRestore!: () => void
  const heldRestore = new Promise<void>((resolve) => { releaseRestore = resolve })
  const authPath = "/api/v1/auth/me"
  await page.route(`**${authPath}`, async (route) => {
    await heldRestore
    await route.continue()
  })
  const restored = page.waitForResponse((response) => new URL(response.url()).pathname === authPath)
  try {
    const restoring = page.waitForRequest((request) => new URL(request.url()).pathname === authPath)
    await page.goto("/claim-profile")
    await restoring
    await expect(page.getByRole("status")).toContainText("正在确认当前会话")
    await expect(page.getByLabel("姓名", { exact: true })).toHaveCount(0)
  } finally {
    releaseRestore()
    await restored
    await page.unroute(`**${authPath}`)
  }
  const name = page.getByLabel("姓名", { exact: true })
  await expect(name).toBeVisible()
  await name.fill("E2E延迟恢复本人")
  await page.getByLabel("身份证", { exact: true }).fill(makeCnId("20000102", 991))
  await page.getByLabel("本人邮箱", { exact: true }).fill("claim-restore@example.com")
  await page.keyboard.press("Tab")
  await expect(name).toHaveValue("E2E延迟恢复本人")
  await expect(page.getByRole("button", { name: "请求邮箱验证码", exact: true })).toBeEnabled()
})

async function approveClaim(page: Page, detail: ClaimDetail) {
  const approve = page.getByRole("button", { name: "批准认领", exact: true })
  await expect(approve).toBeDisabled()
  await page.getByRole("checkbox", { name: "已人工核对申请人与原档案为同一人", exact: true }).check()
  await page.getByLabel("批准备注（可选）", { exact: true }).fill("E2E合成人工核对")
  const approved = responseFor(page, `${CLAIMS}/${detail.id}/approve`)
  await approve.click()
  const response = await approved
  expect(response.status()).toBe(200)
  expect(await response.json()).toMatchObject({ id: detail.id, person_id: detail.person_id, status: "approved" })
}

async function refreshClaim(page: Page, expected: string) {
  const status = responseFor(page, `${PUBLIC}/status`)
  await page.getByRole("button", { name: "查询审核状态", exact: true }).click()
  const response = await status
  expect(response.status()).toBe(200)
  expect((await response.json() as PublicStatus).status).toBe(expected)
}

async function teamSnapshot(api: APIRequestContext, person: string) {
  const all = await get<{ items: Member[] }>(api, `/api/v1/team-management/teams/${TEAM}/memberships?status=all&page_size=100`)
  return all.items.filter((item) => item.person_id === person).map((item) => {
    const { account_status, ...membership } = item
    expect(["not_activated", "active"]).toContain(account_status)
    return membership
  })
}

async function arrangeStay(page: Page, name: string, room: string): Promise<string> {
  await page.goto("/housing")
  await expect(page.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
  if ((page.viewportSize()?.width ?? 1440) < 720) await page.getByRole("button", { name: "打开筛选", exact: true }).click()
  const filters = page.locator(".housingFilters")
  await filters.getByLabel("搜索房间").fill(room)
  await filters.getByRole("button", { name: "应用筛选", exact: true }).click()
  const card = page.locator(".housingRoomCard").filter({ hasText: room })
  await expect(card).toHaveCount(1)
  await card.getByRole("button", { name: `核对房间 ${room}`, exact: true }).click()
  await card.getByRole("button", { name: `查看房间 ${room} 详情`, exact: true }).click()
  await expect(page.getByRole("heading", { name: `房间 ${room}`, exact: true })).toBeVisible()
  const roomId = new URL(page.url()).pathname.split("/").at(-1)!
  await page.getByRole("button", { name: "安排入住", exact: true }).click()
  const dialog = page.getByRole("dialog", { name: `安排入住 · ${room}`, exact: true })
  await dialog.getByLabel("搜索入住人员").fill(name)
  await dialog.getByRole("radio", { name: `选择 ${name}`, exact: true }).check()
  await dialog.getByLabel("住宿类型").selectOption("permanent")
  await dialog.getByLabel("时长类型").selectOption("long_term")
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date())
  await dialog.getByLabel("计划入住日期").fill(today)
  const created = responseFor(page, `/api/v1/housing/rooms/${roomId}/stays`)
  await dialog.getByRole("button", { name: "确认入住", exact: true }).click()
  expect((await created).status()).toBe(201)
  await expect(dialog).toBeHidden()
  return roomId
}

for (const [index, [kind, minor]] of ([
  ["student", false], ["student", true], ["player", false],
  ["player", true], ["both", false], ["both", true],
] as const).entries()) {
  // Catches activation creating/replacing a person, losing guardian/team/stay
  // data, or treating claim verification/approval as account activation.
  test(`${minor ? "minor" : "adult"} ${kind}: missing email approval → housing → claim → fresh activation preserves person`, async ({ page, browser }) => {
    test.setTimeout(300_000)
    const input = facts(kind, minor, 811 + index * 2)
    const email = `claim-live-${kind}-${minor ? "minor" : "adult"}@example.com`
    const administrator = await createAdminRequest()
    const review = await reviewerPage(browser, page, kind === "both")
    try {
      // No synthetic combined-owner role is promised by the current seed.
      // The existing administrator can lawfully submit both affiliations, but
      // a different seeded administrator must review because self-review is forbidden.
      if (kind === "both") await loginAsAdmin(page)
      else await loginWithIdentifier(page, kind === "player" ? ACTORS.manager : ACTORS.coach, PASSWORD)
      const submitter = await get<{ account_id: string }>(page.request, "/api/v1/auth/me")
      const reviewer = await get<{ account_id: string }>(review.page.request, "/api/v1/auth/me")
      expect(submitter.account_id === reviewer.account_id).toBe(false)
      await page.goto("/people/assisted-requests")
      await page.getByRole("button", { name: "新建申请", exact: true }).click()
      await fillIntake(page, input)
      const submitted = responseFor(page, `${INTAKE}/requests`)
      await page.getByRole("button", { name: "提交申请", exact: true }).click()
      const submission = await submitted
      expect(submission.status()).toBe(201)
      const request = await submission.json() as RequestView
      expect(request.status).toBe("pending")
      expect(request.approved_person_id).toBeNull()
      await logout(page)
      await openIntakeReview(review.page, request.id)
      await expect(review.page.getByRole("region", { name: "授权建档资料", exact: true }).locator("dt")
        .filter({ hasText: /^邮箱$/ }).first().locator("+ dd")).toHaveText("未提供")
      const approving = responseFor(review.page, `${INTAKE}/requests/${request.id}/approve`)
      await review.page.getByRole("button", { name: "审核通过", exact: true }).click()
      const approval = await approving
      expect(approval.status()).toBe(200)
      const { person_id: person } = await approval.json() as { person_id: string }
      const before = await get<Person>(administrator.admin, `/api/v1/people/${person}`)
      expect(before.account_status).toBe("not_activated")
      expect(before.assisted_kind).toBe(kind)
      if (kind === "player") expect(before.student_number).toBeNull()
      const patch = await administrator.admin.patch(`/api/v1/people/${person}`, {
        headers: { "X-CSRF-Token": administrator.csrfToken },
        data: { nickname: `E2E认领昵称${index}`, profile_notes: `E2E认领保留备注${index}` },
      })
      expect(patch.status()).toBe(200)
      const profileBefore = await get<Profile>(administrator.admin, `/api/v1/people/${person}/staff-profile`)
      expect(profileBefore.nickname).toBe(`E2E认领昵称${index}`)
      expect(profileBefore.profile_notes).toBe(`E2E认领保留备注${index}`)
      const requestBefore = await get<RequestView>(administrator.admin, `${INTAKE}/requests/${request.id}`)
      expect(requestBefore.scoped_payload?.email).toBeNull()
      expect(requestBefore.scoped_payload?.guardians).toEqual(input.guardians)
      const liveGuardiansBefore = kind === "player" ? null
        : await get<unknown[]>(administrator.admin, `/api/v1/people/${person}/guardians`)
      if (liveGuardiansBefore) expect(liveGuardiansBefore).toHaveLength(minor ? 1 : 0)
      const teamBefore = await teamSnapshot(administrator.admin, person)
      expect(teamBefore).toHaveLength(kind === "student" ? 0 : 1)
      await loginWithIdentifier(page, ACTORS.administration, PASSWORD)
      const room = await arrangeStay(page, input.full_name, `A-${201 + index}`)
      const housingBefore = await get<Room>(administrator.admin, `/api/v1/housing/rooms/${room}/detail`)
      const stayBefore = housingBefore.current_stays.find((stay) => stay.person_id === person)
      expect(stayBefore).toBeDefined()
      await logout(page)

      const { code, resendAvailableAt } = await startClaim(page, input, email)
      // Actual input focus/blur + tab switch, not a fake business response.
      await page.getByLabel("验证码", { exact: true }).focus()
      await page.getByLabel("验证码", { exact: true }).fill(code)
      await page.keyboard.press("Tab")
      await refocus(page)
      expect(await page.getByLabel("验证码", { exact: true }).inputValue() === code).toBe(true)
      expect(await page.getByLabel("姓名", { exact: true }).inputValue() === input.full_name).toBe(true)
      expect(await page.getByLabel("身份证", { exact: true }).inputValue() === input.identity_number).toBe(true)
      expect(await page.getByLabel("本人邮箱", { exact: true }).inputValue() === email).toBe(true)
      await privateOnly(page, [input.full_name, input.identity_number, input.phone, email, code, PASSWORD])
      await verifyClaim(page, code)
      const claim = await openClaim(review.page, email)
      expect(claim.person_id).toBe(person)
      expect(claim.can_approve).toBe(true)
      await privateOnly(review.page, [input.identity_number, email, code, PASSWORD])
      await approveClaim(review.page, claim)
      expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
      await refreshClaim(page, "approved")
      await expect(page.getByRole("status")).toContainText("尚未创建账号")
      await page.getByRole("link", { name: "前往本人激活", exact: true }).click()
      // A verified claim cookie/code cannot substitute for an activation session.
      const claimOnly = await browserPostStatus(page, `${ACTIVATION}/complete`, { code, password: PASSWORD })
      expect(claimOnly).toBe(401)
      await waitForClaimIdentifierCooldown(resendAvailableAt)
      await page.getByLabel("本人邮箱", { exact: true }).fill(email)
      const starting = responseFor(page, `${ACTIVATION}/start`)
      await page.getByRole("button", { name: "请求邮箱验证码", exact: true }).click()
      expect((await starting).status()).toBe(200)
      const activationCode = await readRegistrationTestCode({ destination: email, purpose: "assisted_activation" })
      const unverified = await browserPostStatus(page, `${ACTIVATION}/complete`, { code, password: PASSWORD })
      expect(unverified).toBe(400)
      // Independent six-digit challenges may coincidentally have equal digits.
      // If different, exercise purpose isolation against this fresh session too.
      if (activationCode !== code) {
        const rejected = responseFor(page, `${ACTIVATION}/verify`)
        await page.getByLabel("验证码", { exact: true }).fill(code)
        await page.getByRole("button", { name: "验证邮箱", exact: true }).click()
        expect((await rejected).status()).toBe(400)
        await expect(page.getByLabel("新密码", { exact: true })).toHaveCount(0)
      }
      await page.getByLabel("验证码", { exact: true }).fill(activationCode)
      const verified = responseFor(page, `${ACTIVATION}/verify`)
      await page.getByRole("button", { name: "验证邮箱", exact: true }).click()
      expect((await verified).status()).toBe(200)
      await page.getByLabel("新密码", { exact: true }).fill(PASSWORD)
      await privateOnly(page, [input.identity_number, email, code, activationCode, PASSWORD])
      const completing = responseFor(page, `${ACTIVATION}/complete`)
      await page.getByRole("button", { name: "完成激活", exact: true }).click()
      expect((await completing).status()).toBe(200)
      await expect(page).toHaveURL(/\/login$/)
      expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401)
      await loginWithIdentifier(page, email, PASSWORD)
      const me = await get<{ person_id: string; account_id: string }>(page.request, "/api/v1/auth/me")
      expect(me.person_id).toBe(person)
      expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("active")
      const access = await get<{ roles: Array<{ key: string }> }>(administrator.admin, `/api/v1/accounts/${me.account_id}/access`)
      expect(access.roles.map((role) => role.key).sort()).toEqual(kind === "both" ? ["student", "team_member"] : [kind === "student" ? "student" : "team_member"])
      await page.reload()
      expect((await get<{ person_id: string }>(page.request, "/api/v1/auth/me")).person_id).toBe(person)
      expect(await get<Profile>(administrator.admin, `/api/v1/people/${person}/staff-profile`)).toEqual(profileBefore)
      expect(await teamSnapshot(administrator.admin, person)).toEqual(teamBefore)
      const requestAfter = await get<RequestView>(administrator.admin, `${INTAKE}/requests/${request.id}`)
      expect(requestAfter.approved_person_id).toBe(person)
      expect(requestAfter.scoped_payload?.guardians).toEqual(requestBefore.scoped_payload?.guardians)
      if (liveGuardiansBefore) expect(await get<unknown[]>(administrator.admin,
        `/api/v1/people/${person}/guardians`)).toEqual(liveGuardiansBefore)
      const housingAfter = await get<Room>(administrator.admin, `/api/v1/housing/rooms/${room}/detail`)
      expect(housingAfter.current_stays.find((stay) => stay.person_id === person)).toEqual(stayBefore)
      expect(housingAfter.history).toEqual(housingBefore.history)
      expect(housingAfter.transfers).toEqual(housingBefore.transfers)
      expect((await get<ClaimDetail>(administrator.admin, `${CLAIMS}/${claim.id}`)).status).toBe("completed")
    } finally {
      await review.context.close()
      await administrator.admin.dispose()
    }
  })
}

async function directCreate(api: APIRequestContext, csrf: string, input: IntakeInput) {
  const response = await api.post(`${INTAKE}/direct-create`, {
    headers: { "X-CSRF-Token": csrf }, data: { ...input, idempotency_key: randomUUID() },
  })
  expect(response.status()).toBe(201)
  return (await response.json() as { person_id: string }).person_id
}

test("no matching identity remains indistinguishable publicly and cannot be approved", async ({ page, browser }) => {
  const input = facts("student", false, 841, "E2E认领无匹配")
  const email = "claim-no-match@example.com"
  const { code } = await startClaim(page, input, email)
  await verifyClaim(page, code)
  const review = await reviewerPage(browser, page)
  try {
    const detail = await openClaim(review.page, email)
    expect(detail.person_id).toBeNull()
    expect(detail.can_approve).toBe(false)
    await expect(review.page.getByRole("button", { name: "批准认领", exact: true })).toHaveCount(0)
    await privateOnly(page, [input.full_name, input.identity_number, email, code])
    await refreshClaim(page, "waiting_review")
  } finally { await review.context.close() }
})

test("same name and shared phone do not select a different identity; two mailbox sessions compete for one person", async ({ page, browser }) => {
  test.setTimeout(180_000)
  const administrator = await createAdminRequest()
  const first = facts("player", false, 845, "E2E认领同名")
  const other = facts("player", false, 847, first.full_name)
  const review = await reviewerPage(browser, page)
  const competing = await browser.newContext({ baseURL: BASE_URL, viewport: page.viewportSize() })
  try {
    const person = await directCreate(administrator.admin, administrator.csrfToken, first)
    const otherPerson = await directCreate(administrator.admin, administrator.csrfToken, other)
    expect(otherPerson).not.toBe(person)
    const secondPage = await competing.newPage()
    const firstEmail = "claim-compete-first@example.com"
    const secondEmail = "claim-compete-second@example.com"
    const { code: firstCode } = await startClaim(page, first, firstEmail)
    await verifyClaim(page, firstCode)
    const { code: secondCode } = await startClaim(secondPage, first, secondEmail)
    await verifyClaim(secondPage, secondCode)
    const claim = await openClaim(review.page, firstEmail)
    expect(claim.person_id).toBe(person)
    expect(claim.same_target_pending_count).toBe(2)
    const secondClaim = await openClaim(review.page, secondEmail)
    expect(secondClaim.person_id).toBe(person)
    await openClaim(review.page, firstEmail)
    await approveClaim(review.page, claim)
    await refreshClaim(page, "approved")
    await refreshClaim(secondPage, "closed")
    const invalidated = await get<ClaimDetail>(administrator.admin, `${CLAIMS}/${secondClaim.id}`)
    expect(invalidated.status).toBe("invalidated")
    expect(invalidated.allowed_actions).not.toContain("approve")
    expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
    const otherAfter = await get<Person & { email: string | null }>(administrator.admin, `/api/v1/people/${otherPerson}`)
    expect(otherAfter.account_status).toBe("not_activated")
    expect(otherAfter.email).toBeNull()
    await privateOnly(page, [first.identity_number, firstEmail, firstCode])
    await privateOnly(secondPage, [first.identity_number, secondEmail, secondCode])
  } finally {
    await competing.close()
    await review.context.close()
    await administrator.admin.dispose()
  }
})

test("returning from mailbox preserves draft; leaving the claim page clears it", async ({ page }) => {
  const input = facts("student", false, 849, "E2E认领内存草稿")
  const email = "claim-draft@example.com"
  const { code } = await startClaim(page, input, email)
  await page.getByLabel("验证码", { exact: true }).fill(code)
  await refocus(page)
  expect(await page.getByLabel("身份证", { exact: true }).inputValue() === input.identity_number).toBe(true)
  await page.getByLabel("本人邮箱", { exact: true }).fill("claim-draft-switched@example.com")
  expect(await page.getByLabel("姓名", { exact: true }).inputValue() === input.full_name).toBe(true)
  await expect(page.getByLabel("验证码", { exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "验证邮箱并申请核对", exact: true })).toHaveCount(0)
  await privateOnly(page, [input.full_name, input.identity_number, email, "claim-draft-switched@example.com", code])
  await page.getByRole("link", { name: "返回登录", exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole("heading", { name: "登录工作台", exact: true })).toBeVisible()
  await page.goBack()
  await expect(page).toHaveURL(/\/claim-profile$/)
  await expect(page.getByRole("heading", { name: "已有档案，申请激活", exact: true })).toBeVisible()
  await expect(page.getByLabel("姓名", { exact: true })).toHaveValue("")
  await expect(page.getByLabel("身份证", { exact: true })).toHaveValue("")
  await expect(page.getByLabel("本人邮箱", { exact: true })).toHaveValue("")
})

for (const action of ["reject", "revoke"] as const) {
  test(`${action} requires a reason and never activates or replaces the person`, async ({ page, browser }) => {
    const administrator = await createAdminRequest()
    const input = facts("player", false, action === "reject" ? 851 : 853, `E2E认领${action}`)
    const email = `claim-${action}@example.com`
    const review = await reviewerPage(browser, page)
    try {
      const person = await directCreate(administrator.admin, administrator.csrfToken, input)
      const teamBefore = await teamSnapshot(administrator.admin, person)
      const { code } = await startClaim(page, input, email)
      await verifyClaim(page, code)
      let detail = await openClaim(review.page, email)
      if (action === "revoke") {
        await approveClaim(review.page, detail)
        await refreshClaim(page, "approved")
        detail = await openClaim(review.page, email, "approved")
      }
      const button = review.page.getByRole("button", { name: action === "reject" ? "拒绝申请" : "撤销绑定", exact: true })
      await expect(button).toBeDisabled()
      const reason = "E2E合成核对未通过"
      await review.page.getByLabel("拒绝／撤销原因", { exact: true }).fill(reason)
      if (action === "revoke") {
        await expect(button).toBeDisabled()
        await review.page.getByRole("checkbox", { name: "确认撤销尚未开户的认领绑定", exact: true }).check()
      }
      const decided = responseFor(review.page, `${CLAIMS}/${detail.id}/${action}`)
      await button.click()
      const response = await decided
      expect(response.status()).toBe(200)
      expect(await response.json()).toMatchObject({ id: detail.id, person_id: person, status: action === "reject" ? "rejected" : "revoked" })
      await refreshClaim(page, action === "reject" ? "rejected" : "closed")
      if (action === "reject") await expect(page.getByRole("status")).toContainText(reason)
      await expect(page.getByRole("link", { name: "前往本人激活", exact: true })).toHaveCount(0)
      expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
      expect(await teamSnapshot(administrator.admin, person)).toEqual(teamBefore)
      await privateOnly(page, [input.identity_number, email, code])
    } finally {
      await review.context.close()
      await administrator.admin.dispose()
    }
  })
}

for (const actor of ["coach", "manager", "administration"] as const) {
  test(`${actor} cannot enter the private claim queue using unrelated existing authority`, async ({ page }) => {
    await loginWithIdentifier(page, ACTORS[actor], PASSWORD)
    const context = await page.request.get(`${CLAIMS}/context`)
    if (context.status() === 200) expect(await context.json()).toEqual({ can_enter: false })
    else expect([403, 404]).toContain(context.status())
    expect([403, 404]).toContain((await page.request.get(`${CLAIMS}?page=1&page_size=20`)).status())
    await page.goto(REVIEW)
    await expect(page.getByRole("region", { name: "认领申请队列", exact: true })).toHaveCount(0)
    await expect(page.getByRole("region", { name: "认领申请详情", exact: true })).toHaveCount(0)
  })
}
