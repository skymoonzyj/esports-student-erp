import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"
import type { APIRequestContext, Locator, Page, TestInfo } from "@playwright/test"

import { E2E_CREDENTIALS } from "./e2eCredentials"
import {
  createAdminRequest, createRequestSession, expectNoHorizontalOverflow,
  loginAsAdmin, loginWithIdentifier, logout, makeCnId, readRegistrationTestCode,
} from "./helpers"

// Fixed isolated launcher only. No production users, external mail or traces.
const INTAKE = "/api/v1/personnel-intake"
const ACTIVATION = "/api/v1/auth/profile-activation"
const PASSWORD = E2E_CREDENTIALS.admin.password
const COACH = "e9000000-0000-4000-8000-000000000001"
type Approval = { person_id: string; request_id: string | null; approval_id: string }
type RolePlan = { person_id: string; version: number; role_keys: string[]; role_names: string[];
  can_edit: boolean; account_activated: boolean }
type Assignments = { business_kind: string | null; source_intake_kind: string | null;
  student_project_ids: string[]; coach_person_ids: string[] }

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
  return page.waitForResponse(response => response.request().method() === method
    && new URL(response.url()).pathname === path)
}
async function get<T>(api: APIRequestContext, path: string): Promise<T> {
  const response = await api.get(path)
  expect(response.status()).toBe(200)
  return await response.json() as T
}
function synthetic(sequence: number) {
  return { full_name: `E2E激活角色${sequence}`, identity_number: makeCnId("20000102", sequence),
    email: `intake-roles-${sequence}@example.com`, phone: `19900006${String(sequence).slice(-3)}`,
    date_of_birth: "2000-01-02", sex: "male" as const }
}
async function noStoredSecrets(page: Page, values: string[]) {
  expect(await page.evaluate(secrets => {
    const persisted = [decodeURIComponent(location.href), document.cookie,
      ...Object.keys(localStorage), ...Object.values(localStorage),
      ...Object.keys(sessionStorage), ...Object.values(sessionStorage)].join("\n")
    return secrets.some(value => value.length > 0 && persisted.includes(value))
  }, values)).toBe(false)
}
async function themeScreenshot(page: Page, locator: Locator, theme: "clear" | "command", name: string, info: TestInfo) {
  // Theme tokens only: no preference write and no mocked product/API behavior.
  await page.evaluate(value => { document.documentElement.dataset.uiTheme = value }, theme)
  await expectNoHorizontalOverflow(page)
  const bounds = await locator.boundingBox()
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual((page.viewportSize()?.width ?? 1440) + 1)
  // Crop excludes identity/contact/password/OTP inputs and account navigation.
  await locator.screenshot({ path: info.outputPath(`${name}-${theme}.png`), animations: "disabled" })
}
async function fillStrict(page: Page, kind: "staff" | "student", sequence: number) {
  const input = synthetic(sequence)
  await page.getByLabel("主要身份", { exact: true }).selectOption(kind === "staff" ? "registered_user" : "student")
  if (await page.getByLabel("主要工作人员岗位", { exact: true }).count()) {
    await page.getByLabel("主要工作人员岗位", { exact: true }).selectOption("property")
  }
  for (const [label, value] of [["姓名", input.full_name], ["身份证", input.identity_number],
    ["手机号", input.phone], ["邮箱", input.email], ["出生日期", input.date_of_birth]]) {
    await page.getByLabel(label, { exact: true }).fill(value)
  }
  await page.getByLabel("性别", { exact: true }).selectOption(input.sex)
  if (kind === "student") await page.getByRole("checkbox", { name: "E2E协助独立教练", exact: true }).check()
  await page.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案", exact: true }).check()
  return input
}
async function savePlan(page: Page, person: string, previous: number, keys: string[]) {
  const saving = responseFor(page, `${INTAKE}/people/${person}/activation-roles`, "PUT")
  await clickUnobstructed(page.getByRole("button", { name: "保存并重新确认激活角色", exact: true }))
  const response = await saving
  expect(response.status()).toBe(200)
  const plan = await response.json() as RolePlan
  expect(plan.version).toBe(previous + 1)
  expect([...plan.role_keys].sort()).toEqual([...keys].sort())
  expect(plan.account_activated).toBe(false)
  return plan
}
async function clickUnobstructed(target: Locator) {
  await target.evaluate(element => element.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" }))
  await expect(target).toBeVisible()
  await expect(target).toBeEnabled()
  expect(await target.evaluate(element => {
    const box = element.getBoundingClientRect()
    const hit = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)
    return hit === element || element.contains(hit)
  }), "control remains normally clickable after scrolling, including above mobile tabs").toBe(true)
  await target.click() // Normal actionability checks, never forced/DOM click.
}
async function affiliation(api: APIRequestContext, person: string) {
  const value = await get<Assignments>(api, `/api/v1/people/${person}/assignments`)
  return { business_kind: value.business_kind, source_intake_kind: value.source_intake_kind,
    student_project_ids: value.student_project_ids, coach_person_ids: value.coach_person_ids }
}
async function approvedGuardians(api: APIRequestContext, person: string) {
  // Direct-create intentionally returns request_id=null, while the authorized
  // review queue retains the approval source. Resolve by the created person.
  const queue = await get<{ total: number; items: { id: string; approved_person_id: string | null }[] }>(
    api, `${INTAKE}/requests?view=review&status=approved&kind=staff&page=1&page_size=100`)
  expect(queue.total).toBeLessThanOrEqual(100)
  const requests = queue.items.filter(item => item.approved_person_id === person)
  expect(requests).toHaveLength(1)
  const detail = await get<{ status: string; approved_person_id: string;
    scoped_payload: { guardians: unknown[] } | null }>(api, `${INTAKE}/requests/${requests[0].id}`)
  expect(detail.status).toBe("approved")
  expect(detail.approved_person_id).toBe(person)
  expect(detail.scoped_payload).not.toBeNull()
  return detail.scoped_payload!.guardians
}
async function correctStrictStaff(page: Page, person: string, input: ReturnType<typeof synthetic>) {
  const context = await get<{ kind: string; profile_version: number; can_edit_activation_email: boolean }>(
    page.request, `${INTAKE}/people/${person}/editor-context`)
  expect(context.kind).toBe("staff")
  const region = page.getByRole("region", { name: "协助档案资料更正", exact: true })
  await region.getByRole("button", { name: "更正本人及监护资料", exact: true }).click()
  const name = `${input.full_name}复核`
  for (const [label, value] of [["姓名", name], ["身份证", input.identity_number],
    ["手机号", input.phone], ["出生日期", input.date_of_birth]]) {
    await region.getByLabel(label, { exact: true }).fill(value)
  }
  await region.getByLabel("性别", { exact: true }).selectOption(input.sex)
  if (context.can_edit_activation_email) await region.getByLabel("邮箱", { exact: true }).fill(input.email)
  await region.getByLabel("资料更正原因", { exact: true }).fill("隔离验收核对工作人员资料")
  await expect(region.getByLabel("人员身份", { exact: true })).toHaveCount(0)
  await expect(region.getByRole("group", { name: "账号激活角色（激活后生效）", exact: true })).toHaveCount(0)
  const correcting = responseFor(page, `${INTAKE}/people/${person}/corrections`)
  await clickUnobstructed(region.getByRole("button", { name: "提交更正", exact: true }))
  const response = await correcting
  expect(response.status()).toBe(200)
  const body = response.request().postDataJSON() as Record<string, unknown>
  expect(body.expected_version).toBe(context.profile_version)
  const allowed = ["full_name", "identity_number", "document_type", "document_issuing_country", "phone",
    "email", "sex", "date_of_birth", "guardians", "expected_version", "reason", "idempotency_key"]
  expect(Object.keys(body).every(key => allowed.includes(key))).toBe(true)
  for (const field of ["kind", "coach_person_ids", "team_id", "activation_role_keys", "role_keys"]) {
    expect(Object.hasOwn(body, field)).toBe(false)
  }
  await expect(region.getByText("资料更正成功。", { exact: true })).toBeVisible()
  await noStoredSecrets(page, [input.identity_number, input.email, input.phone, PASSWORD])
  await page.reload()
  await expect(page.getByRole("heading", { name, exact: true })).toBeVisible()
  const after = await get<{ full_name: string; account_status: string }>(page.request, `/api/v1/people/${person}`)
  expect(after.full_name).toBe(name)
  expect(after.account_status).toBe("not_activated")
}
async function activate(page: Page, input: ReturnType<typeof synthetic>, admin: APIRequestContext, roles: string[]) {
  await logout(page)
  await page.goto("/activate-profile")
  await page.getByLabel("本人邮箱", { exact: true }).fill(input.email)
  const starting = responseFor(page, `${ACTIVATION}/start`)
  await page.getByRole("button", { name: "请求邮箱验证码", exact: true }).click()
  const started = await starting
  expect(started.status()).toBe(200)
  expect(Object.keys(await started.json()).sort()).toEqual(["accepted", "expires_at", "resend_available_at"])
  const code = await readRegistrationTestCode({ destination: input.email, purpose: "assisted_activation" })
  await page.getByLabel("验证码", { exact: true }).fill(code)
  const verifying = responseFor(page, `${ACTIVATION}/verify`)
  await page.getByRole("button", { name: "验证邮箱", exact: true }).click()
  expect((await verifying).status()).toBe(200)
  await page.getByLabel("新密码", { exact: true }).fill(PASSWORD)
  await noStoredSecrets(page, [code, input.email, input.identity_number, PASSWORD])
  const completing = responseFor(page, `${ACTIVATION}/complete`)
  await page.getByRole("button", { name: "完成激活", exact: true }).click()
  expect((await completing).status()).toBe(200)
  await expect(page).toHaveURL(/\/login$/)
  expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401)
  await loginWithIdentifier(page, input.email, PASSWORD)
  const me = await get<{ person_id: string; account_id: string }>(page.request, "/api/v1/auth/me")
  const access = await get<{ roles: { key: string }[] }>(admin, `/api/v1/accounts/${me.account_id}/access`)
  expect(access.roles.map(role => role.key).sort()).toEqual([...roles].sort())
  return me.person_id
}

// Catches dropped selections, premature UserRole writes, stale detail saves,
// missing authorizer reconfirmation and activation losing the staff baseline.
for (const [theme, sequence] of [["clear", 811], ["command", 813]] as const) {
  test(`${theme}: strict staff plans survive reopen/reconfirmation and grant only after self activation`, async ({ page }, info) => {
    test.setTimeout(180_000)
    const administrator = await createAdminRequest()
    try {
      await loginAsAdmin(page)
      await page.goto("/people/assisted-create")
      const picker = page.getByRole("group", { name: "人员身份", exact: true })
      await expect(picker).toBeVisible()
      await page.getByLabel("主要身份", { exact: true }).selectOption("registered_user")
      await picker.getByText("兼任身份", { exact: true }).click()
      await expect(picker.getByRole("checkbox", { name: "管理员", exact: true })).toHaveCount(0)
      await expect(picker.getByRole("checkbox", { name: "超级管理员", exact: true })).toHaveCount(0)
      await picker.getByRole("checkbox", { name: "物业", exact: true }).check()
      await picker.getByRole("checkbox", { name: "助教", exact: true }).check()
      const input = await fillStrict(page, "staff", sequence)
      await themeScreenshot(page, picker, theme, "role-picker", info)
      const creating = responseFor(page, `${INTAKE}/direct-create`)
      await page.getByRole("button", { name: "提交申请", exact: true }).click()
      const created = await creating
      expect(created.status()).toBe(201)
      const { person_id: person } = await created.json() as Approval
      await expect(page.getByText("建档不等于账号激活。", { exact: true })).toBeVisible()
      const detail = await get<{ account_status: string }>(administrator.admin, `/api/v1/people/${person}`)
      expect(detail.account_status).toBe("not_activated")
      let plan = await get<RolePlan>(administrator.admin, `${INTAKE}/people/${person}/activation-roles`)
      expect(plan.role_keys.sort()).toEqual(["assistant_coach", "property"])
      expect(plan.can_edit).toBe(true)
      expect(plan.account_activated).toBe(false)
      const before = await affiliation(administrator.admin, person)
      expect(before).toMatchObject({ business_kind: null, source_intake_kind: "staff", coach_person_ids: [] })
      // The existing /people/{id}/guardians API is student-only, not a staff
      // relationship query. Staff approved facts plus the activated guardian's
      // actual family portal prove that choosing the role creates no relation.
      const guardiansBefore = await approvedGuardians(administrator.admin, person)
      expect(guardiansBefore).toEqual([])
      await page.goto(`/people/${person}`)
      const panel = page.getByRole("region", { name: "激活角色计划", exact: true })
      await expect(panel.getByRole("checkbox", { name: "物业", exact: true })).toBeChecked()
      await expect(panel.getByRole("checkbox", { name: "助教", exact: true })).toBeChecked()
      // A full-element crop can include the fixed mobile tabbar. Exercise the
      // actual last option and save after ordinary scrolling, not the crop.
      const last = panel.getByRole("checkbox", { name: "队员", exact: true })
      await expect(last).not.toBeChecked()
      await clickUnobstructed(last)
      await expect(last).toBeChecked()
      await clickUnobstructed(last)
      await expect(last).not.toBeChecked()
      plan = await savePlan(page, person, plan.version, ["assistant_coach", "property"])
      // A guardian role is a role only; no family relationship should appear.
      await panel.getByRole("checkbox", { name: "监护人", exact: true }).check()
      plan = await savePlan(page, person, plan.version, ["assistant_coach", "guardian", "property"])
      await page.reload()
      await expect(panel.getByRole("checkbox", { name: "监护人", exact: true })).toBeChecked()
      await themeScreenshot(page, panel, theme, "role-plan", info)
      expect(await approvedGuardians(administrator.admin, person)).toEqual(guardiansBefore)
      const beforeCorrection = await get<RolePlan>(administrator.admin, `${INTAKE}/people/${person}/activation-roles`)
      await correctStrictStaff(page, person, input)
      expect(await get<RolePlan>(administrator.admin, `${INTAKE}/people/${person}/activation-roles`)).toEqual(beforeCorrection)
      expect(await affiliation(administrator.admin, person)).toEqual(before)
      expect(await approvedGuardians(administrator.admin, person)).toEqual(guardiansBefore)
      expect(await activate(page, input, administrator.admin,
        ["assistant_coach", "guardian", "property", "registered_user"])).toBe(person)
      expect(await affiliation(administrator.admin, person)).toEqual(before)
      expect(await approvedGuardians(administrator.admin, person)).toEqual(guardiansBefore)
      expect((await get<{ items: unknown[] }>(page.request, "/api/v1/family/students")).items).toEqual([])
      await logout(page)
      await loginAsAdmin(page)
      await page.goto(`/people/${person}`)
      await expect(panel).toContainText(/账号已激活.*已有权限管理/)
      await expect(panel.getByRole("button", { name: "保存并重新确认激活角色", exact: true })).toHaveCount(0)
      const frozen = await get<RolePlan>(administrator.admin, `${INTAKE}/people/${person}/activation-roles`)
      expect(frozen.account_activated).toBe(true)
      expect(frozen.can_edit).toBe(false)
      const denied = await administrator.admin.put(`${INTAKE}/people/${person}/activation-roles`, {
        headers: { "X-CSRF-Token": administrator.csrfToken },
        data: { role_keys: [], expected_version: frozen.version, idempotency_key: randomUUID() },
      })
      expect(denied.status()).toBe(409)
    } finally { await administrator.admin.dispose() }
  })
}

// Catches treating extra account roles as a replacement for learner identity or
// silently creating/removing business affiliations while granting a role.
test("student baseline and real coach affiliation remain independent of extra activation roles", async ({ page }) => {
  test.setTimeout(180_000)
  const administrator = await createAdminRequest()
  try {
    await loginAsAdmin(page)
    await page.goto("/people/assisted-create")
    const input = await fillStrict(page, "student", 815)
    await page.getByRole("group", { name: "人员身份", exact: true }).getByText("兼任身份", { exact: true }).click()
    await page.getByRole("checkbox", { name: "物业", exact: true }).check()
    // An identity change invalidates the explicit management confirmation.
    await page.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案", exact: true }).check()
    const creating = responseFor(page, `${INTAKE}/direct-create`)
    await page.getByRole("button", { name: "提交申请", exact: true }).click()
    const response = await creating
    expect(response.status()).toBe(201)
    const { person_id: person } = await response.json() as Approval
    const before = await affiliation(administrator.admin, person)
    expect(before).toMatchObject({ business_kind: "student", source_intake_kind: "student", coach_person_ids: [COACH] })
    expect(await activate(page, input, administrator.admin, ["property", "student"])).toBe(person)
    expect(await affiliation(administrator.admin, person)).toEqual(before)
  } finally { await administrator.admin.dispose() }
})

// Catches UI-only finance restrictions, admin/root escalation and accidental
// acceptance of activation grants on ordinary submitted intake requests.
test("restricted administrator cannot plan finance and no actor can intake-grant admin/root", async ({ page }) => {
  const administrator = await createAdminRequest()
  const limited = await createRequestSession("19900009044", PASSWORD)
  const coach = await createRequestSession("19900009001", PASSWORD)
  try {
    await loginWithIdentifier(page, "19900009044", PASSWORD)
    await page.goto("/people/assisted-create")
    const picker = page.getByRole("group", { name: "人员身份", exact: true })
    await page.getByLabel("主要身份", { exact: true }).selectOption("registered_user")
    await picker.getByText("兼任身份", { exact: true }).click()
    await expect(picker.getByRole("checkbox", { name: "财务", exact: true })).toBeDisabled()
    await expect(picker.getByRole("checkbox", { name: "管理员", exact: true })).toHaveCount(0)
    await expect(picker.getByRole("checkbox", { name: "超级管理员", exact: true })).toHaveCount(0)
    await expectNoHorizontalOverflow(page)
    const before = await get<{ total: number }>(administrator.admin, "/api/v1/people?page=1&page_size=1")
    for (const [role, actor, status] of [["finance", limited, 403], ["admin", { api: administrator.admin, csrfToken: administrator.csrfToken }, 422],
      ["super_admin", { api: administrator.admin, csrfToken: administrator.csrfToken }, 422]] as const) {
      const response = await actor.api.post(`${INTAKE}/direct-create-pending`, {
        headers: { "X-CSRF-Token": actor.csrfToken },
        data: { ...synthetic(817), kind: "staff", guardians: [], coach_person_ids: [], team_id: null,
          team_allocation_status: "not_required", activation_role_keys: [role], idempotency_key: randomUUID() },
      })
      expect(response.status()).toBe(status)
    }
    const ordinary = await coach.api.post(`${INTAKE}/requests`, {
      headers: { "X-CSRF-Token": coach.csrfToken },
      data: { ...synthetic(819), kind: "student", guardians: [], coach_person_ids: [COACH], team_id: null,
        activation_role_keys: ["property"] },
    })
    expect(ordinary.status()).toBe(422)
    expect((await get<{ total: number }>(administrator.admin, "/api/v1/people?page=1&page_size=1")).total).toBe(before.total)
  } finally { await Promise.all([administrator.admin.dispose(), limited.api.dispose(), coach.api.dispose()]) }
})

// Catches losing pending-mode selections, granting an account during creation,
// and clearing extra roles accidentally deleting the minimum activation role.
test("pending staff stores selected roles and explicit clearing returns to baseline without activating", async ({ page }) => {
  await loginAsAdmin(page)
  await page.goto("/people/assisted-create")
  await page.getByRole("button", { name: "资料待补建档", exact: true }).click()
  await page.getByLabel("主要身份", { exact: true }).selectOption("registered_user")
  await page.getByRole("group", { name: "人员身份", exact: true }).getByText("兼任身份", { exact: true }).click()
  const input = synthetic(821)
  await page.getByLabel("姓名", { exact: true }).fill(input.full_name)
  await page.getByLabel("身份证", { exact: true }).fill(input.identity_number)
  await page.getByRole("checkbox", { name: "物业", exact: true }).check()
  await page.getByRole("checkbox", { name: "助教", exact: true }).check()
  await page.getByLabel("主要工作人员岗位", { exact: true }).selectOption("property")
  await page.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案", exact: true }).check()
  const creating = responseFor(page, `${INTAKE}/direct-create-pending`)
  await page.getByRole("button", { name: "建立待补档案", exact: true }).click()
  const response = await creating
  expect(response.status()).toBe(201)
  const { person_id: person } = await response.json() as Approval
  await page.goto(`/people/${person}`)
  const panel = page.getByRole("region", { name: "激活角色计划", exact: true })
  const before = await get<RolePlan>(page.request, `${INTAKE}/people/${person}/activation-roles`)
  expect(before.role_keys.sort()).toEqual(["assistant_coach", "property"])
  await panel.getByRole("checkbox", { name: "物业", exact: true }).uncheck()
  await panel.getByRole("checkbox", { name: "助教", exact: true }).uncheck()
  await savePlan(page, person, before.version, [])
  await page.reload()
  await expect(panel.getByRole("checkbox", { name: "物业", exact: true })).not.toBeChecked()
  await expect(panel.getByRole("checkbox", { name: "助教", exact: true })).not.toBeChecked()
  expect((await get<{ account_status: string }>(page.request, `/api/v1/people/${person}`)).account_status).toBe("not_activated")
  await noStoredSecrets(page, [input.identity_number, PASSWORD])
  await expectNoHorizontalOverflow(page)
})
