import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"
import type { APIRequestContext, Page } from "@playwright/test"
import type { ClaimDetail } from "../src/profileClaims/adminApi"
import type { AssistedReadiness } from "../src/assistedPeople/editorApi"
import type { HousingRoomDetail } from "../src/housing/api"
import { E2E_CREDENTIALS } from "./e2eCredentials"
import {
  createAdminRequest, expectNoHorizontalOverflow, loginAsAdmin, loginWithIdentifier,
  logout, makeCnId, readRegistrationTestCode, refocus,
} from "./helpers"

// Fixed independent launcher/reseed unit: ten claims, not another addition to
// profile-claims.spec.ts's 18/20 hourly source budget. No production quota edits.
const INTAKE = "/api/v1/personnel-intake"
const CLAIMS = `${INTAKE}/profile-claims`
const PUBLIC = "/api/v1/auth/profile-claims"
const ACTIVATION = "/api/v1/auth/profile-activation"
const PASSWORD = E2E_CREDENTIALS.admin.password
const REVIEW = "/settings/registrations/claims"
type Person = { id: string; full_name: string; account_status: string; identity_number: string;
  date_of_birth: string | null; sex: string | null; document_type: string;
  document_issuing_country: string | null; team_allocation: { status: string } }
type Profile = { person_id: string; nickname: string | null; profile_notes: string | null }
type SeedCase = { kind: "student" | "player" | "both" | "staff"; minor: boolean; sequence: number }
const CASES: SeedCase[] = [
  { kind: "student", minor: false, sequence: 51 }, { kind: "student", minor: true, sequence: 52 },
  { kind: "player", minor: false, sequence: 53 }, { kind: "player", minor: true, sequence: 54 },
  { kind: "both", minor: false, sequence: 55 }, { kind: "both", minor: true, sequence: 56 },
  { kind: "staff", minor: false, sequence: 57 }, { kind: "staff", minor: false, sequence: 58 },
]
test.use({ trace: "off", screenshot: "off", video: "off" })
test.describe.configure({ mode: "serial" })
test.beforeEach(async ({ baseURL }) => {
  for (const origin of [baseURL, process.env.E2E_API_URL]) {
    expect(origin, "explicit disposable loopback origins required").toBeTruthy()
    const url = new URL(origin!)
    expect(url.protocol).toBe("http:")
    expect(["127.0.0.1", "localhost"]).toContain(url.hostname)
  }
})
function responseFor(page: Page, path: string, method = "POST") {
  return page.waitForResponse((response) => response.request().method() === method
    && new URL(response.url()).pathname === path)
}
async function get<T>(api: APIRequestContext, path: string): Promise<T> {
  const response = await api.get(path)
  expect(response.status()).toBe(200)
  return await response.json() as T
}
async function privateOnly(page: Page, values: string[]) {
  const leaked = await page.evaluate((secrets) => {
    const sources = [decodeURIComponent(location.href), document.cookie,
      ...Object.keys(localStorage), ...Object.values(localStorage),
      ...Object.keys(sessionStorage), ...Object.values(sessionStorage)]
    return secrets.some((secret) => secret.length > 0 && sources.some((source) => source.includes(secret)))
  }, values)
  expect(leaked, "applicant facts/codes/passwords stay out of persistent browser state").toBe(false)
  await expectNoHorizontalOverflow(page)
}
function facts(row: SeedCase) {
  const passport = row.sequence === 58
  return {
    full_name: `E2E待补种子${row.sequence}`,
    identity_number: passport ? "E2e-Pending-P8" : makeCnId(row.minor ? "20150102" : "20000102", 601 + row.sequence * 2),
    email: row.sequence % 2 ? `assisted-seed-${row.sequence}@example.com` : `pending-claim-${row.sequence}@example.com`,
    document_type: passport ? "passport" as const : "cn_id" as const,
    issuing_country: passport ? "US" : null,
    phone: `19900007${row.sequence.toString().padStart(3, "0")}`,
    sex: "male" as const, date_of_birth: row.minor ? "2015-01-02" : "2000-01-02",
    guardians: row.minor ? [{ full_name: `E2E待补监护${row.sequence}`,
      identity_number: makeCnId("19800102", 751 + row.sequence * 2), phone: "19900007777",
      email: null, relationship: "father", sex: "male" as const, date_of_birth: "1980-01-02", is_primary: true }] : [],
  }
}
async function openClaim(page: Page, email: string, status = "pending") {
  await page.goto(REVIEW)
  await page.getByLabel("认领状态", { exact: true }).selectOption(status)
  const list = await get<{ items: { id: string }[] }>(page.request, `${CLAIMS}?page=1&page_size=20&status=${status}`)
  for (let index = 0; index < list.items.length; index += 1) {
    const detail = await get<ClaimDetail>(page.request, `${CLAIMS}/${list.items[index].id}`)
    if (detail.applicant_email !== email) continue
    const response = responseFor(page, `${CLAIMS}/${detail.id}`, "GET")
    await page.getByRole("button", { name: "查看认领详情", exact: true }).nth(index).click()
    expect((await response).status()).toBe(200)
    await expect(page.getByRole("region", { name: "认领申请详情", exact: true })).toBeVisible()
    await expectNoHorizontalOverflow(page)
    return detail
  }
  throw new Error("Synthetic pending claim absent from authorized queue")
}
async function startClaim(page: Page, input: ReturnType<typeof facts>, email = input.email) {
  await page.goto("/claim-profile")
  await page.getByLabel("证件类型", { exact: true }).selectOption(input.document_type)
  await page.getByLabel("姓名", { exact: true }).fill(input.full_name)
  await page.getByLabel(input.document_type === "passport" ? "护照号码" : "身份证", { exact: true }).fill(input.identity_number)
  if (input.issuing_country) await page.getByLabel("签发国家（两位代码，可暂缺）", { exact: true }).fill(input.issuing_country)
  await page.getByLabel("本人邮箱", { exact: true }).fill(email)
  // These are independently self-entered, never prefilled from DB readiness.
  await expect(page.getByLabel("联系电话（可暂缺）", { exact: true })).toHaveValue("")
  await expect(page.getByLabel("出生日期（可暂缺）", { exact: true })).toHaveValue("")
  await page.getByLabel("联系电话（可暂缺）", { exact: true }).fill(input.phone)
  await page.getByLabel("性别（可暂缺）", { exact: true }).selectOption(input.sex)
  await page.getByLabel("出生日期（可暂缺）", { exact: true }).fill(input.date_of_birth)
  for (const guardian of input.guardians) {
    await page.getByRole("button", { name: "添加监护人（最多 10 位）", exact: true }).click()
    for (const [label, value] of [["姓名", guardian.full_name], ["身份证", guardian.identity_number],
      ["手机号", guardian.phone], ["与本人关系", guardian.relationship], ["出生日期", guardian.date_of_birth]]) {
      await page.getByLabel(`监护人1${label}`, { exact: true }).fill(value)
    }
    await page.getByLabel("监护人1性别", { exact: true }).selectOption("male")
    await page.getByRole("checkbox", { name: "监护人1为主要监护人", exact: true }).check()
  }
  const starting = responseFor(page, `${PUBLIC}/start`)
  await page.getByRole("button", { name: "请求邮箱验证码", exact: true }).click()
  const response = await starting
  expect(response.status()).toBe(200)
  const body = await response.json() as { accepted: boolean; resend_available_at: string }
  expect(Object.keys(body).sort()).toEqual(["accepted", "expires_at", "resend_available_at"])
  expect(body.accepted).toBe(true)
  const code = await readRegistrationTestCode({ destination: email, purpose: "profile_claim" })
  await page.getByLabel("验证码", { exact: true }).fill(code)
  await page.keyboard.press("Tab")
  await refocus(page)
  expect(await page.getByLabel("验证码", { exact: true }).inputValue() === code).toBe(true)
  await privateOnly(page, [input.identity_number, email, code, PASSWORD, input.phone])
  const verifying = responseFor(page, `${PUBLIC}/verify`)
  await page.getByRole("button", { name: "验证邮箱并申请核对", exact: true }).click()
  const verified = await verifying
  expect(verified.status()).toBe(200)
  expect(await verified.json()).toEqual({ status: "waiting_review", reason: null })
  return { code, resendAt: Date.parse(body.resend_available_at) }
}
async function approve(page: Page, detail: ClaimDetail) {
  expect(detail.can_approve).toBe(true)
  const button = page.getByRole("button", { name: "批准认领", exact: true })
  await expect(button).toBeDisabled()
  await page.getByRole("checkbox", { name: "已人工核对申请人与原档案为同一人", exact: true }).check()
  const approving = responseFor(page, `${CLAIMS}/${detail.id}/approve`)
  await button.click()
  expect((await approving).status()).toBe(200)
}
async function browserPost(page: Page, path: string, data: object) {
  return page.evaluate(async ({ path, data }) => (await fetch(path, {
    method: "POST", credentials: "include", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  })).status, { path, data })
}
async function activationProof(page: Page, email: string, previous: { code: string; resendAt: number }) {
  await page.goto("/activate-profile")
  expect(await browserPost(page, `${ACTIVATION}/complete`, { code: previous.code, password: PASSWORD })).toBe(401)
  const remaining = previous.resendAt - Date.now()
  expect(remaining).toBeLessThanOrEqual(65_000)
  await expect.poll(() => Date.now(), { timeout: Math.max(5_000, remaining + 6_000),
    intervals: [250, 500, 1_000] }).toBeGreaterThan(previous.resendAt + 500)
  await page.getByLabel("本人邮箱", { exact: true }).fill(email)
  const starting = responseFor(page, `${ACTIVATION}/start`)
  await page.getByRole("button", { name: "请求邮箱验证码", exact: true }).click()
  expect((await starting).status()).toBe(200)
  const code = await readRegistrationTestCode({ destination: email, purpose: "assisted_activation" })
  expect(await browserPost(page, `${ACTIVATION}/complete`, { code, password: PASSWORD })).toBe(400)
  await page.getByLabel("验证码", { exact: true }).fill(code)
  const verifying = responseFor(page, `${ACTIVATION}/verify`)
  await page.getByRole("button", { name: "验证邮箱", exact: true }).click()
  expect((await verifying).status()).toBe(200)
  await page.getByLabel("新密码", { exact: true }).fill(PASSWORD)
  await privateOnly(page, [email, code, PASSWORD])
  return code
}

async function registerGuardianWithoutFamilyAccess(page: Page, input: ReturnType<typeof facts>, child: string) {
  const guardian = input.guardians[0]
  const email = "pending-guardian-registration@example.com"
  await expect(page.locator(".shellUserName")).toHaveText(input.full_name)
  await logout(page)
  await page.goto("/register")
  await page.getByRole("radio", { name: /^监护人/ }).click()
  await page.getByRole("radio", { name: "邮箱", exact: true }).click()
  await page.locator("#registration-identifier").fill(email)
  const challenging = page.waitForResponse((response) => response.request().method() === "POST"
    && /\/registration\/applications\/[0-9a-f-]+\/challenge$/.test(new URL(response.url()).pathname))
  await page.getByRole("button", { name: "发送验证码", exact: true }).click()
  const challenged = await challenging
  expect(challenged.status()).toBe(201)
  const challenge = await challenged.json() as { challenge_id: string }
  const code = await readRegistrationTestCode({ challengeId: challenge.challenge_id,
    destination: email, purpose: "registration" })
  await page.getByLabel("验证码", { exact: true }).fill(code)
  const verifying = page.waitForResponse((response) => response.request().method() === "POST"
    && /\/registration\/applications\/[0-9a-f-]+\/verify$/.test(new URL(response.url()).pathname))
  await page.getByRole("button", { name: "验证并继续", exact: true }).click()
  expect((await verifying).status()).toBe(200)
  await page.getByLabel("姓名", { exact: true }).fill(guardian.full_name)
  await page.getByLabel("身份证", { exact: true }).fill(guardian.identity_number)
  await page.getByLabel("密码", { exact: true }).fill(PASSWORD)
  await page.getByLabel("确认密码", { exact: true }).fill(PASSWORD)
  await page.getByLabel(/我已阅读并同意实名资料处理条款/).check()
  const finalizing = responseFor(page, "/api/v1/registration/me/finalize")
  await page.getByRole("button", { name: "提交实名资料", exact: true }).click()
  expect((await finalizing).status()).toBe(200)
  await loginWithIdentifier(page, email, PASSWORD)
  const family = await page.request.get("/api/v1/family/students")
  if (family.status() === 200) {
    const children = await family.json() as { items?: unknown[] } | unknown[]
    expect(Array.isArray(children) ? children : children.items).toEqual([])
  } else expect([403, 404]).toContain(family.status())
  expect([403, 404]).toContain((await page.request.get(`/api/v1/family/students/${child}/teaching`)).status())
  await privateOnly(page, [guardian.identity_number, email, code, PASSWORD])
}

for (const row of CASES) {
  // Catches lost original rows/stays, inferred unknown facts, automatic account
  // creation on verification/approval, and treating unassigned teams as a blocker.
  test(`pending ${row.minor ? "minor" : "adult"} ${row.kind} ${row.sequence}: completion and fresh activation preserve original person/housing`, async ({ page, browser, baseURL }) => {
    test.setTimeout(240_000)
    const input = facts(row)
    const administrator = await createAdminRequest()
    const reviewContext = await browser.newContext({ baseURL, viewport: page.viewportSize() })
    const review = await reviewContext.newPage()
    try {
      const people = await get<{ items: Person[] }>(administrator.admin, "/api/v1/people?page=1&page_size=100")
      const original = people.items.find((person) => person.full_name === input.full_name)
      expect(original).toBeDefined()
      const person = original!.id
      expect(original!.account_status).toBe("not_activated")
      const readiness = await get<AssistedReadiness>(administrator.admin, `${INTAKE}/people/${person}/readiness`)
      expect(readiness.approval_valid).toBe(true)
      expect(readiness.activation_profile_ready).toBe(false)
      expect(readiness.profile_missing).toContain("phone")
      if (row.minor) expect(readiness.profile_missing).toContain("guardian")
      if (row.sequence === 58) {
        expect(original!.sex).toBeNull()
        expect(original!.date_of_birth).toBeNull()
        expect(readiness.housing_ready).toBe(false)
        expect(readiness.housing_missing).toEqual(["sex"])
        const denied = await administrator.admin.get(`/api/v1/people/${person}/housing/check-in-options`)
        expect(denied.status()).toBe(403)
        const overview = await get<{ rooms: { id: string; room_number: string }[] }>(administrator.admin, "/api/v1/housing/overview?query=A-308")
        const room = overview.rooms.find((value) => value.room_number === "A-308")
        expect(room).toBeDefined()
        const refused = await administrator.admin.post(`/api/v1/housing/rooms/${room!.id}/stays/backfill`, {
          headers: { "X-CSRF-Token": administrator.csrfToken }, data: { person_id: person,
            stay_purpose: "permanent", duration_mode: "long_term", planned_check_in_at: new Date(Date.now() - 86_400_000).toISOString() },
        })
        expect(refused.status()).toBe(409)
        expect((await refused.json() as { code: string }).code).toBe("housing_profile_incomplete")
        const context = await get<{ profile_version: number }>(administrator.admin, `${INTAKE}/people/${person}/editor-context`)
        const corrected = await administrator.admin.post(`${INTAKE}/people/${person}/pending-corrections`, {
          headers: { "X-CSRF-Token": administrator.csrfToken }, data: {
            full_name: input.full_name, identity_number: input.identity_number, document_type: "passport",
            document_issuing_country: "US", sex: "male", date_of_birth: null, phone: null, guardians: [],
            expected_version: context.profile_version, reason: "合成护照事实核对", idempotency_key: randomUUID(),
          },
        })
        expect(corrected.status()).toBe(200)
        const created = await administrator.admin.post(`/api/v1/housing/rooms/${room!.id}/stays/backfill`, {
          headers: { "X-CSRF-Token": administrator.csrfToken }, data: { person_id: person,
            stay_purpose: "permanent", duration_mode: "long_term", planned_check_in_at: new Date(Date.now() - 86_400_000).toISOString(), notes: "E2E护照真实更正后入住" },
        })
        expect(created.status()).toBe(201)
      } else expect(readiness.housing_ready).toBe(true)
      const housing = await get<{ management_stay: { id: string; room_id: string; status: string } }>(administrator.admin, `/api/v1/people/${person}/housing`)
      expect(housing.management_stay.status).toBe("checked_in")
      const roomBefore = await get<HousingRoomDetail>(administrator.admin, `/api/v1/housing/rooms/${housing.management_stay.room_id}/detail`)
      if (row.sequence === 51) {
        expect(roomBefore.history).toHaveLength(1)
        expect(roomBefore.history[0].status).toBe("checked_out")
      }
      const profileBefore = await get<Profile>(administrator.admin, `/api/v1/people/${person}/staff-profile`)
      expect(profileBefore.nickname).toBe(`E2E待补昵称${row.sequence}`)
      expect(profileBefore.profile_notes).toBe(`E2E待补保留备注${row.sequence}`)
      const allocationBefore = (await get<Person>(administrator.admin, `/api/v1/people/${person}`)).team_allocation
      expect(allocationBefore.status).toBe("pending")
      const proof = await startClaim(page, input)
      await loginAsAdmin(review)
      const detail = await openClaim(review, input.email)
      expect(detail.person_id).toBe(person)
      expect(detail.applicant_document_type).toBe(input.document_type)
      expect(detail.applicant_completion?.phone).toBe(input.phone)
      expect(detail.applicant_completion?.guardians).toEqual(input.guardians)
      expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
      await approve(review, detail)
      expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
      await activationProof(page, input.email, proof)
      const completing = responseFor(page, `${ACTIVATION}/complete`)
      await page.getByRole("button", { name: "完成激活", exact: true }).click()
      expect((await completing).status()).toBe(200)
      await expect(page).toHaveURL(/\/login$/)
      await loginWithIdentifier(page, input.email, PASSWORD)
      const me = await get<{ person_id: string; account_id: string }>(page.request, "/api/v1/auth/me")
      expect(me.person_id).toBe(person)
      const completedPerson = await get<Person>(administrator.admin, `/api/v1/people/${person}`)
      expect(completedPerson.identity_number === input.identity_number).toBe(false)
      if (input.document_type === "passport") expect(completedPerson.identity_number).toBe("****P8")
      else expect(completedPerson.identity_number).toMatch(/^110105\*{8}\d{3}[\dX]$/)
      const revealed = await administrator.admin.post(`/api/v1/people/${person}/sensitive-reveal`, {
        headers: { "X-CSRF-Token": administrator.csrfToken },
        data: { field: "identity_number", purpose: "合成档案生命周期证件保留核对" },
      })
      expect(revealed.status()).toBe(200)
      expect(revealed.headers()["cache-control"]).toContain("no-store")
      expect((await revealed.json() as { value: string }).value === input.identity_number).toBe(true)
      expect(completedPerson.document_type).toBe(input.document_type)
      expect(completedPerson.date_of_birth).toBe(input.date_of_birth)
      if (row.sequence === 58) expect(completedPerson.document_issuing_country).toBe("US")
      const access = await get<{ roles: { key: string }[] }>(administrator.admin, `/api/v1/accounts/${me.account_id}/access`)
      const expectedRoles = { student: ["student"], player: ["team_member"],
        both: ["student", "team_member"], staff: ["registered_user"] }
      expect(access.roles.map((role) => role.key).sort()).toEqual(expectedRoles[row.kind])
      expect(await get<Profile>(administrator.admin, `/api/v1/people/${person}/staff-profile`)).toEqual(profileBefore)
      expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).team_allocation).toEqual(allocationBefore)
      const roomAfter = await get<HousingRoomDetail>(administrator.admin, `/api/v1/housing/rooms/${housing.management_stay.room_id}/detail`)
      expect(roomAfter.current_stays.find((stay) => stay.person_id === person)).toEqual(roomBefore.current_stays.find((stay) => stay.person_id === person))
      expect(roomAfter.history).toEqual(roomBefore.history)
      expect(roomAfter.transfers).toEqual(roomBefore.transfers)
      expect((await get<ClaimDetail>(administrator.admin, `${CLAIMS}/${detail.id}`)).status).toBe("completed")
      await page.reload()
      expect((await get<{ person_id: string }>(page.request, "/api/v1/auth/me")).person_id).toBe(person)
      if (row.sequence === 52) await registerGuardianWithoutFamilyAccess(page, input, person)
    } finally {
      await reviewContext.close()
      await administrator.admin.dispose()
    }
  })
}

test("pending browser creation → rejection/reapply → revoked binding denies old verified activation", async ({ page, browser, baseURL }) => {
  test.setTimeout(240_000)
  const input = { ...facts({ kind: "staff", minor: false, sequence: 59 }), full_name: "E2E待补浏览器建档", email: "pending-reapply@example.com" }
  const administrator = await createAdminRequest()
  const reviewContext = await browser.newContext({ baseURL, viewport: page.viewportSize() })
  const review = await reviewContext.newPage()
  try {
    await loginAsAdmin(page)
    await page.goto("/people/assisted-create")
    await page.getByRole("button", { name: "资料待补建档", exact: true }).click()
    await page.getByLabel("人员身份", { exact: true }).selectOption("staff")
    await page.getByLabel("姓名", { exact: true }).fill(input.full_name)
    await page.getByLabel("身份证", { exact: true }).fill(input.identity_number)
    await page.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案", exact: true }).check()
    const creating = responseFor(page, `${INTAKE}/direct-create-pending`)
    await page.getByRole("button", { name: "建立待补档案", exact: true }).click()
    const created = await creating
    expect(created.status()).toBe(201)
    const person = (await created.json() as { person_id: string }).person_id
    await expectNoHorizontalOverflow(page)
    await logout(page)
    await startClaim(page, input)
    await loginAsAdmin(review)
    const rejected = await openClaim(review, input.email)
    expect(rejected.person_id).toBe(person)
    await review.getByLabel("拒绝／撤销原因", { exact: true }).fill("合成资料请重新核对")
    const rejecting = responseFor(review, `${CLAIMS}/${rejected.id}/reject`)
    await review.getByRole("button", { name: "拒绝申请", exact: true }).click()
    expect((await rejecting).status()).toBe(200)
    const status = responseFor(page, `${PUBLIC}/status`)
    await page.getByRole("button", { name: "查询审核状态", exact: true }).click()
    expect((await (await status).json() as { status: string }).status).toBe("rejected")
    // A different synthetic mailbox avoids reusing the rejected proof or waiting
    // on its identifier cooldown, without raising any server quota.
    const email = "pending-reapply-fresh@example.com"
    const proof = await startClaim(page, input, email)
    const detail = await openClaim(review, email)
    expect(detail.id).not.toBe(rejected.id)
    await approve(review, detail)
    const activationCode = await activationProof(page, email, proof)
    await openClaim(review, email, "approved")
    await review.getByLabel("拒绝／撤销原因", { exact: true }).fill("合成绑定撤销")
    await review.getByRole("checkbox", { name: "确认撤销尚未开户的认领绑定", exact: true }).check()
    const revoking = responseFor(review, `${CLAIMS}/${detail.id}/revoke`)
    await review.getByRole("button", { name: "撤销绑定", exact: true }).click()
    expect((await revoking).status()).toBe(200)
    expect(await browserPost(page, `${ACTIVATION}/complete`, { code: activationCode, password: PASSWORD })).toBe(400)
    expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401)
    expect((await get<Person>(administrator.admin, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
  } finally {
    await reviewContext.close()
    await administrator.admin.dispose()
  }
})
