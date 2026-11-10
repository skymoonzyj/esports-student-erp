import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"
import type { APIRequestContext, Locator, Page, TestInfo } from "@playwright/test"
import { E2E_CREDENTIALS } from "./e2eCredentials"
import { createAdminRequest, createRequestSession, expectNoHorizontalOverflow, loginAsAdmin as baseLoginAsAdmin,
  loginWithIdentifier as baseLoginWithIdentifier, logout as baseLogout, makeCnId, readRegistrationTestCode,
  refocus as baseRefocus } from "./helpers"
import { installIdentityTheme } from "./support/intakeIdentityTheme"

// Fixed full-suite isolated runner only; no mocked API, external mail, real users,
// retained credentials, full-page screenshots, traces or videos.
const INTAKE = "/api/v1/personnel-intake"
const TEAMS = "/api/v1/team-management"
const ACTIVATION = "/api/v1/auth/profile-activation"
const TEAM_A = "e9100000-0000-4000-8000-000000000001"
const TEAM_B = "e9100000-0000-4000-8000-000000000002"
const TEAM_OUTSIDE = "e9100000-0000-4000-8000-000000000003"
const TEAM_NAMES = { [TEAM_A]: "E2E协助目标队", [TEAM_B]: "E2E协助无关队" }
const COACH = "e9000000-0000-4000-8000-000000000001"
const PASSWORD = E2E_CREDENTIALS.admin.password
type Theme = "clear" | "command"
type Approval = { person_id: string; approval_id: string }
type Position = { row_key: string; source_kind: string; person_id: string; person_position_id: string;
  account_id: string | null; account_assignment_id: string | null; staff_role: string;
  account_status: string; authorization_status: string; version: number; ended_on: string | null }
type RolePlan = { version: number; role_keys: string[]; account_activated: boolean }
type Matrix = { primary: string; concurrent: string[]; playerTeam?: string; coaches?: boolean;
  staff: Partial<Record<"manager" | "coach" | "assistant_coach", string[]>> }

test.use({ trace: "off", screenshot: "off", video: "off" })
// Each case owns distinct synthetic identities and a fresh page. Keep the fixed
// workers=1/retries=0 config, but collect all independent failures instead of
// serial-mode fail-fast skips; the runner still requires every case to pass.
test.describe.configure({ mode: "default" })
test.beforeEach(async ({ baseURL, page }, info) => {
  test.setTimeout(180_000)
  page.setDefaultTimeout(15_000)
  for (const value of [baseURL, process.env.E2E_API_URL]) {
    expect(value, "explicit disposable loopback origin required").toBeTruthy()
    const origin = new URL(value!)
    expect(origin.protocol).toBe("http:")
    expect(["127.0.0.1", "localhost"]).toContain(origin.hostname)
  }
  // Register before the first document loads. Login, full navigation/reload,
  // polling/focus refresh and all six workflows keep the requested CSS theme.
  await page.addInitScript(installIdentityTheme, caseTheme(info.title))
})

function caseTheme(title = test.info().title): Theme {
  const match = /^IDT[1-6] (clear|command)$/.exec(title)
  expect(match, "fixed case title must identify its actual A/B theme").not.toBeNull()
  return match![1] as Theme
}
async function assertTheme(page: Page, refresh = false) {
  const theme = caseTheme()
  await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
  if (refresh) {
    // Read the real authoritative appearance endpoint through the real store.
    // Do not call accept(), invent revisions or mock any application response.
    const authoritative = await page.evaluate(async () => {
      const path = "/src/appearance/store.ts"
      const { appearanceStore } = await import(/* @vite-ignore */ path)
      await appearanceStore.refresh()
      return appearanceStore.getAuthoritativeSnapshot()
    }) as { theme: Theme; revision: number } | null
    expect(authoritative, "real authoritative appearance refresh must finish").not.toBeNull()
    expect(["clear", "command"]).toContain(authoritative!.theme)
    expect(authoritative!.revision).toBeGreaterThanOrEqual(1)
    await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
  }
}
async function navigate(page: Page, path: string) {
  await page.goto(path)
  await assertTheme(page, true)
}
async function reload(page: Page) {
  await page.reload()
  await assertTheme(page, true)
}
async function loginAsAdmin(page: Page) {
  await baseLoginAsAdmin(page)
  await assertTheme(page, true)
}
async function loginWithIdentifier(page: Page, identifier: string, password: string) {
  await baseLoginWithIdentifier(page, identifier, password)
  await assertTheme(page, true)
}
async function logout(page: Page) {
  await assertTheme(page)
  // A document reload finishes before React restores its authenticated shell.
  // Wait for the real logout control; the shared helper's immediate count is
  // intentionally not itself a navigation/readiness wait.
  await expect(page.getByRole("button", { name: "退出登录" }).first()).toBeVisible()
  await baseLogout(page)
  await assertTheme(page, true)
}
async function refocus(page: Page) {
  await baseRefocus(page)
  await assertTheme(page, true)
}

function synthetic(sequence: number) {
  return { full_name: `E2E统一身份${sequence}`, identity_number: makeCnId("20000102", sequence),
    phone: `19900005${String(sequence).slice(-3)}`, email: `identity-teams-${sequence}@example.com`,
    sex: "male", date_of_birth: "2000-01-02" }
}
function responseFor(page: Page, path: string, method = "POST") {
  return page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname === path)
}
async function get<T>(api: APIRequestContext, path: string): Promise<T> {
  const response = await api.get(path)
  expect(response.status()).toBe(200)
  return await response.json() as T
}
async function ready(target: Locator) {
  await assertTheme(target.page())
  await expect(target).toBeVisible()
  await expect(target).toBeEnabled()
  return target
}
async function fill(target: Locator, value: string) {
  await (await ready(target)).fill(value)
}
async function choose(target: Locator, value: string) {
  await (await ready(target)).selectOption(value)
}
async function check(target: Locator, checked = true) {
  await (await ready(target)).setChecked(checked)
}
async function click(target: Locator) {
  await ready(target)
  await target.evaluate(element => element.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" }))
  await target.click()
  await assertTheme(target.page())
}
async function noStoredSecrets(page: Page, values: string[]) {
  expect(await page.evaluate(secrets => {
    const data = [decodeURIComponent(location.href), document.cookie, ...Object.keys(localStorage),
      ...Object.values(localStorage), ...Object.keys(sessionStorage), ...Object.values(sessionStorage)].join("\n")
    return secrets.some(value => value && data.includes(value))
  }, values)).toBe(false)
}
async function crop(page: Page, target: Locator, theme: Theme, name: string, info: TestInfo) {
  expect(caseTheme()).toBe(theme)
  await assertTheme(page, true)
  await expectNoHorizontalOverflow(page)
  await expect(target).toBeVisible()
  const box = await target.boundingBox()
  expect(box).not.toBeNull()
  expect(box!.x).toBeGreaterThanOrEqual(0)
  expect(box!.x + box!.width).toBeLessThanOrEqual((page.viewportSize()?.width ?? 1440) + 1)
  // Only synthetic identity controls / staff list survive launcher cleanup.
  await target.screenshot({ path: info.outputPath(`${name}-${theme}.png`), animations: "disabled" })
}
async function selectIdentity(page: Page, selection: Matrix) {
  await assertTheme(page)
  const identity = page.getByRole("group", { name: "人员身份", exact: true })
  await choose(page.getByLabel("主要身份", { exact: true }), selection.primary)
  if (selection.concurrent.length) {
    await click(identity.getByText("兼任身份", { exact: true }))
    for (const name of selection.concurrent) await check(identity.getByRole("checkbox", { name, exact: true }))
  }
  if (selection.coaches) await check(identity.getByRole("checkbox", { name: "E2E协助独立教练", exact: true }))
  if (selection.playerTeam) await choose(page.getByLabel(/^队员所属队伍/), selection.playerTeam)
  const labels = { manager: "队伍经理", coach: "教练", assistant_coach: "助教" }
  for (const role of Object.keys(selection.staff) as (keyof typeof labels)[]) {
    const group = identity.getByRole("group", { name: `${labels[role]}负责队伍（可暂不分配）`, exact: true })
    for (const team of selection.staff[role]!) await check(group.getByRole("checkbox", { name: `和平精英 / ${TEAM_NAMES[team]}`, exact: true }))
  }
  await expect(page.getByRole("group", { name: "账号激活角色（激活后生效）", exact: true })).toHaveCount(0)
  await expect(identity.getByRole("option", { name: "管理员", exact: true })).toHaveCount(0)
  await expect(identity.getByRole("option", { name: "超级管理员", exact: true })).toHaveCount(0)
  return identity
}
async function fillFacts(page: Page, input: ReturnType<typeof synthetic>, complete = true, within?: Locator) {
  await assertTheme(page)
  const region = within ?? page.locator("main")
  await fill(region.getByLabel("姓名", { exact: true }), input.full_name)
  await fill(region.getByLabel("身份证", { exact: true }), input.identity_number)
  if (complete) {
    await fill(region.getByLabel(/^(手机号|联系电话（可暂缺）)$/), input.phone)
    await fill(region.getByLabel("邮箱", { exact: true }), input.email)
    await choose(region.getByLabel("性别", { exact: true }), input.sex)
    await fill(region.getByLabel("出生日期", { exact: true }), input.date_of_birth)
  }
}
async function createFromForm(page: Page, pending = false): Promise<Approval> {
  await assertTheme(page)
  await check(page.getByRole("checkbox", { name: "我已核对资料，确认直接创建已通过档案", exact: true }))
  const saving = responseFor(page, `${INTAKE}/${pending ? "direct-create-pending" : "direct-create"}`)
  await click(page.getByRole("button", { name: pending ? "建立待补档案" : "提交申请", exact: true }))
  const response = await saving
  expect(response.status()).toBe(201)
  const body = response.request().postDataJSON() as Record<string, unknown>
  expect(body).toHaveProperty("identity_selection")
  expect(body).toHaveProperty("staff_teams")
  expect(Object.hasOwn(body, "kind")).toBe(false)
  expect(Object.hasOwn(body, "activation_role_keys")).toBe(false)
  await expect(page.getByText("建档不等于账号激活。", { exact: true })).toBeVisible()
  return await response.json() as Approval
}
async function positions(api: APIRequestContext, team: string, person: string, status = "current") {
  const result = await get<{ items: Position[]; total: number }>(api, `${TEAMS}/teams/${team}/staff-positions?status=${status}&page_size=100`)
  expect(result.total).toBeLessThanOrEqual(100)
  return result.items.filter(row => row.person_id === person)
}
async function openRoster(page: Page, team: string) {
  await navigate(page, "/people/teams")
  return await selectRoster(page, team)
}
async function selectRoster(page: Page, team: string) {
  await click(page.getByRole("button", { name: `查看 ${TEAM_NAMES[team]}`, exact: true }))
  const roster = page.getByRole("region", { name: "队内工作人员（只读）", exact: true })
  await expect(roster).toBeVisible()
  return roster
}
async function activate(page: Page, input: ReturnType<typeof synthetic>, admin: APIRequestContext, roles: string[]) {
  const current = await page.request.get("/api/v1/auth/me")
  if (current.status() === 200) await logout(page)
  else expect(current.status()).toBe(401)
  await navigate(page, "/activate-profile")
  await fill(page.getByLabel("本人邮箱", { exact: true }), input.email)
  const starting = responseFor(page, `${ACTIVATION}/start`)
  await click(page.getByRole("button", { name: "请求邮箱验证码", exact: true }))
  expect((await starting).status()).toBe(200)
  const code = await readRegistrationTestCode({ destination: input.email, purpose: "assisted_activation" })
  await fill(page.getByLabel("验证码", { exact: true }), code)
  const verifying = responseFor(page, `${ACTIVATION}/verify`)
  await click(page.getByRole("button", { name: "验证邮箱", exact: true }))
  expect((await verifying).status()).toBe(200)
  await fill(page.getByLabel("新密码", { exact: true }), PASSWORD)
  await noStoredSecrets(page, [code, input.email, input.identity_number, PASSWORD])
  const finishing = responseFor(page, `${ACTIVATION}/complete`)
  await click(page.getByRole("button", { name: "完成激活", exact: true }))
  expect((await finishing).status()).toBe(200)
  await expect(page).toHaveURL(/\/login$/)
  await loginWithIdentifier(page, input.email, PASSWORD)
  const me = await get<{ person_id: string; account_id: string }>(page.request, "/api/v1/auth/me")
  const access = await get<{ roles: { key: string }[] }>(admin, `/api/v1/accounts/${me.account_id}/access`)
  expect(access.roles.map(row => row.key).sort()).toEqual([...roles].sort())
  const context = await get<{ can_assign_managers: boolean; can_manage_catalog: boolean; can_transfer: boolean }>(page.request, `${TEAMS}/context`)
  expect(context).toMatchObject({ can_assign_managers: false, can_manage_catalog: false, can_transfer: false })
  return me
}
async function claimPending(page: Page, input: ReturnType<typeof synthetic>, person: string,
  admin: { admin: APIRequestContext; csrfToken: string }) {
  await logout(page)
  await navigate(page, "/claim-profile")
  await fill(page.getByLabel("姓名", { exact: true }), input.full_name)
  await fill(page.getByLabel("身份证", { exact: true }), input.identity_number)
  await fill(page.getByLabel("本人邮箱", { exact: true }), input.email)
  await fill(page.getByLabel("联系电话（可暂缺）", { exact: true }), input.phone)
  await choose(page.getByLabel("性别（可暂缺）", { exact: true }), input.sex)
  await fill(page.getByLabel("出生日期（可暂缺）", { exact: true }), input.date_of_birth)
  const starting = responseFor(page, "/api/v1/auth/profile-claims/start")
  await click(page.getByRole("button", { name: "请求邮箱验证码", exact: true }))
  const started = await starting
  expect(started.status()).toBe(200)
  const { resend_available_at: resendAt } = await started.json() as { resend_available_at: string }
  const code = await readRegistrationTestCode({ destination: input.email, purpose: "profile_claim" })
  await fill(page.getByLabel("验证码", { exact: true }), code)
  const verifying = responseFor(page, "/api/v1/auth/profile-claims/verify")
  await click(page.getByRole("button", { name: "验证邮箱并申请核对", exact: true }))
  expect((await verifying).status()).toBe(200)
  await noStoredSecrets(page, [code, input.identity_number, input.email, PASSWORD])
  const queue = await get<{ items: { id: string }[]; total: number }>(admin.admin, `${INTAKE}/profile-claims?status=pending&page_size=100`)
  expect(queue.total).toBeLessThanOrEqual(100)
  const matches: { id: string; version: number; person_id: string | null; can_approve: boolean }[] = []
  for (const item of queue.items) {
    const detail = await get<{ id: string; version: number; person_id: string | null; can_approve: boolean }>(admin.admin, `${INTAKE}/profile-claims/${item.id}`)
    if (detail.person_id === person) matches.push(detail)
  }
  expect(matches).toHaveLength(1)
  expect(matches[0].can_approve).toBe(true)
  const approved = await admin.admin.post(`${INTAKE}/profile-claims/${matches[0].id}/approve`, {
    headers: { "X-CSRF-Token": admin.csrfToken },
    data: { expected_version: matches[0].version, idempotency_key: randomUUID(), identity_confirmed: true } })
  expect(approved.status()).toBe(200)
  // Preserve the cross-purpose real email cooldown, never modify its quota.
  const remaining = Date.parse(resendAt) - Date.now()
  expect(remaining).toBeLessThanOrEqual(65_000)
  await expect.poll(() => Date.now(), { timeout: Math.max(5_000, remaining + 6_000),
    intervals: [250, 500, 1_000] }).toBeGreaterThan(Date.parse(resendAt) + 500)
}
async function pendingStaff(page: Page, sequence: number, selection: Matrix) {
  await loginAsAdmin(page)
  await navigate(page, "/people/assisted-create")
  await click(page.getByRole("button", { name: "资料待补建档", exact: true }))
  await selectIdentity(page, selection)
  const input = synthetic(sequence)
  await fillFacts(page, input, false)
  return { ...await createFromForm(page, true), input }
}

for (const [theme, base] of [["clear", 901], ["command", 921]] as const) {
  // Strict coach + manager multi-team: immediate business roster, then exactly
  // one linked row per role and only read access after real self activation.
  test(`IDT1 ${theme}`, async ({ page }, info) => {
    const admin = await createAdminRequest()
    try {
      await loginAsAdmin(page)
      await navigate(page, "/people/assisted-create")
      const identity = await selectIdentity(page, { primary: "coach", concurrent: ["队伍经理"],
        staff: { coach: [TEAM_A, TEAM_B], manager: [TEAM_A] } })
      const input = synthetic(base)
      await fillFacts(page, input)
      await (await ready(page.getByLabel("姓名", { exact: true }))).focus()
      await page.keyboard.press("Tab")
      await refocus(page)
      await expect(page.getByLabel("姓名", { exact: true })).toHaveValue(input.full_name)
      await expect(page.getByLabel("身份证", { exact: true })).toHaveValue(input.identity_number)
      await expect(identity.getByRole("checkbox", { name: "队伍经理", exact: true })).toBeChecked()
      await crop(page, identity, theme, "identity-selection", info)
      const created = await createFromForm(page)
      const before = await positions(admin.admin, TEAM_A, created.person_id)
      expect(before).toHaveLength(2)
      expect(before.every(row => row.account_id === null && row.account_assignment_id === null && row.account_status === "not_activated")).toBe(true)
      expect((await positions(admin.admin, TEAM_B, created.person_id)).map(row => row.staff_role)).toEqual(["coach"])
      const unauthenticated = await createRequestSession("19900009003", PASSWORD)
      try { expect([403, 404]).toContain((await unauthenticated.api.get(`${TEAMS}/teams/${TEAM_A}/staff-positions`)).status()) }
      finally { await unauthenticated.api.dispose() }
      const roster = await openRoster(page, TEAM_A)
      await expect(roster.getByRole("listitem").filter({ hasText: input.full_name })).toHaveCount(2)
      await expect(roster.getByRole("listitem").filter({ hasText: input.full_name }).first()).toContainText("未激活")
      await crop(page, roster, theme, "staff-roster", info)
      const me = await activate(page, input, admin.admin, ["registered_user", "coach", "team_manager"])
      expect(me.person_id).toBe(created.person_id)
      const visible = await get<{ items: { id: string; allowed_actions: string[] }[] }>(page.request, `${TEAMS}/teams?page_size=100`)
      expect(visible.items.map(row => row.id).sort()).toEqual([TEAM_A, TEAM_B].sort())
      expect(visible.items.every(row => row.allowed_actions.join(",") === "read")).toBe(true)
      expect([403, 404]).toContain((await page.request.get(`${TEAMS}/teams/${TEAM_OUTSIDE}/memberships`)).status())
      const csrf = await get<{ csrf_token: string }>(page.request, "/api/v1/auth/csrf")
      const membersBefore = await get<{ items: { person_id: string }[] }>(admin.admin, `${TEAMS}/teams/${TEAM_A}/memberships?page_size=100`)
      expect(membersBefore.items.filter(row => row.person_id === created.person_id)).toEqual([])
      const write = await page.request.post(`${TEAMS}/teams/${TEAM_A}/memberships`, {
        headers: { "X-CSRF-Token": csrf.csrf_token }, data: { person_id: created.person_id, idempotency_key: randomUUID() } })
      // Existing write-resource policy deliberately conceals denied teams as404;
      // require that exact business denial, not CSRF403 or schema rejection.
      expect(write.status()).toBe(404)
      expect((await write.json() as { code: string }).code).toBe("team_not_found")
      const membersAfter = await get<{ items: { person_id: string }[] }>(admin.admin, `${TEAMS}/teams/${TEAM_A}/memberships?page_size=100`)
      expect(membersAfter.items.filter(row => row.person_id === created.person_id)).toEqual([])
      const after = await positions(admin.admin, TEAM_A, created.person_id)
      expect(after.map(row => row.row_key).sort()).toEqual(before.map(row => row.row_key).sort())
      expect(after).toHaveLength(2)
      expect(after.every(row => row.source_kind === "person" && row.account_id === me.account_id && row.account_assignment_id && row.account_status === "active")).toBe(true)
      await logout(page)
      await loginAsAdmin(page)
      const reopened = await openRoster(page, TEAM_A)
      await expect(reopened.getByRole("listitem").filter({ hasText: input.full_name })).toHaveCount(2)
      await expect(reopened.getByRole("listitem").filter({ hasText: input.full_name }).first()).toContainText("已激活")
    } finally { await admin.admin.dispose() }
  })

  // Missing facts stay missing; correction cannot silently change identities or
  // replace the real player and assistant relationships before activation.
  test(`IDT2 ${theme}`, async ({ page }) => {
    const admin = await createAdminRequest()
    try {
      const created = await pendingStaff(page, base + 2, { primary: "student", concurrent: ["队员", "助教"],
        playerTeam: TEAM_A, coaches: true, staff: { assistant_coach: [TEAM_B] } })
      const before = await positions(admin.admin, TEAM_B, created.person_id)
      expect(before).toHaveLength(1)
      expect(before[0]).toMatchObject({ account_id: null, account_status: "not_activated", staff_role: "assistant_coach" })
      await navigate(page, `/people/${created.person_id}`)
      const correction = page.getByRole("region", { name: "协助档案资料更正", exact: true })
      await click(correction.getByRole("button", { name: "更正本人及监护资料", exact: true }))
      await fillFacts(page, created.input, true, correction)
      await fill(correction.getByLabel("资料更正原因", { exact: true }), "隔离验收补齐本人资料")
      await check(correction.getByRole("checkbox", { name: "已通过可信渠道核实本人及邮箱更正", exact: true }))
      await expect(correction.getByLabel("主要身份", { exact: true })).toHaveCount(0)
      const correcting = responseFor(page, `${INTAKE}/people/${created.person_id}/pending-corrections`)
      await click(correction.getByRole("button", { name: "提交待补更正", exact: true }))
      expect((await correcting).status()).toBe(200)
      await expect(correction.getByText("待补资料更正成功；账号仍未激活。", { exact: true })).toBeVisible()
      await reload(page)
      await claimPending(page, created.input, created.person_id, admin)
      const me = await activate(page, created.input, admin.admin, ["student", "team_member", "assistant_coach"])
      expect(me.person_id).toBe(created.person_id)
      const after = await positions(admin.admin, TEAM_B, created.person_id)
      expect(after).toHaveLength(1)
      expect(after[0].row_key).toBe(before[0].row_key)
      expect(after[0].account_assignment_id).toBeTruthy()
      const members = await get<{ items: { person_id: string }[] }>(admin.admin, `${TEAMS}/teams/${TEAM_A}/memberships?page_size=100`)
      expect(members.items.filter(row => row.person_id === created.person_id)).toHaveLength(1)
      expect((await get<{ coach_person_ids: string[] }>(admin.admin, `/api/v1/people/${created.person_id}/assignments`)).coach_person_ids).toEqual([COACH])
    } finally { await admin.admin.dispose() }
  })

  test(`IDT3 ${theme}`, async ({ page }) => {
    const created = await pendingStaff(page, base + 4, { primary: "coach", concurrent: ["队伍经理"],
      staff: { coach: [TEAM_A, TEAM_B], manager: [TEAM_A] } })
    const before = await positions(page.request, TEAM_A, created.person_id)
    const coach = before.find(row => row.staff_role === "coach")!
    const roster = await openRoster(page, TEAM_A)
    const row = roster.getByRole("listitem").filter({ hasText: created.input.full_name }).filter({ hasText: "教练 ·" })
    await click(row.getByRole("button", { name: "重新确认授权", exact: true }))
    let dialog = page.getByRole("dialog", { name: "重新确认队内岗位授权", exact: true })
    await fill(dialog.getByLabel("操作原因", { exact: true }), "隔离验收重新核实岗位")
    const reconfirming = responseFor(page, `${TEAMS}/person-staff-positions/${coach.person_position_id}/reconfirm`)
    await click(dialog.getByRole("button", { name: "确认重新授权", exact: true }))
    expect((await reconfirming).status()).toBe(200)
    await reload(page)
    // Selected-team detail is local UI state, not a persisted deep route.
    await selectRoster(page, TEAM_A)
    const refreshed = await positions(page.request, TEAM_A, created.person_id)
    expect(refreshed.find(value => value.staff_role === "coach")!.version).toBe(coach.version + 1)
    await click(page.getByRole("button", { name: `解绑 ${created.input.full_name} 的教练岗位`, exact: true }))
    dialog = page.getByRole("dialog", { name: "解绑队内岗位", exact: true })
    await fill(dialog.getByLabel("操作原因", { exact: true }), "隔离验收结束单个岗位")
    const ending = responseFor(page, `${TEAMS}/person-staff-positions/${coach.person_position_id}/end`)
    await click(dialog.getByRole("button", { name: "确认解绑", exact: true }))
    expect((await ending).status()).toBe(200)
    await reload(page)
    await selectRoster(page, TEAM_A)
    expect((await positions(page.request, TEAM_A, created.person_id)).map(value => value.staff_role)).toEqual(["manager"])
    expect(await positions(page.request, TEAM_B, created.person_id)).toHaveLength(1)
    const ended = await positions(page.request, TEAM_A, created.person_id, "ended")
    expect(ended).toHaveLength(1)
    expect(ended[0].person_position_id).toBe(coach.person_position_id)
    const reopenedRoster = page.getByRole("region", { name: "队内工作人员（只读）", exact: true })
    await choose(reopenedRoster.getByRole("combobox", { name: "岗位绑定状态", exact: true }), "ended")
    await expect(reopenedRoster.getByRole("list", { name: "队内岗位绑定", exact: true }).getByRole("listitem").filter({ hasText: created.input.full_name })).toHaveCount(1)
  })

  test(`IDT4 ${theme}`, async ({ page }) => {
    const created = await pendingStaff(page, base + 6, { primary: "coach", concurrent: ["队伍经理"],
      staff: { coach: [TEAM_A], manager: [TEAM_A, TEAM_B] } })
    await navigate(page, `/people/${created.person_id}`)
    const panel = page.getByRole("region", { name: "激活角色计划", exact: true })
    const before = await get<RolePlan>(page.request, `${INTAKE}/people/${created.person_id}/activation-roles`)
    await check(panel.getByRole("checkbox", { name: "队伍管理", exact: true }), false)
    const firstSave = responseFor(page, `${INTAKE}/people/${created.person_id}/activation-roles`, "PUT")
    await click(panel.getByRole("button", { name: "保存并重新确认激活角色", exact: true }))
    const rejected = await firstSave
    expect(rejected.status()).toBe(409)
    const firstKey = (rejected.request().postDataJSON() as { idempotency_key: string }).idempotency_key
    const dialog = page.getByRole("dialog", { name: "确认结束队内岗位", exact: true })
    await expect(dialog).toBeVisible()
    await click(dialog.getByRole("button", { name: "取消移除角色", exact: true }))
    expect((await get<RolePlan>(page.request, `${INTAKE}/people/${created.person_id}/activation-roles`)).version).toBe(before.version)
    expect(await positions(page.request, TEAM_B, created.person_id)).toHaveLength(1)
    const again = responseFor(page, `${INTAKE}/people/${created.person_id}/activation-roles`, "PUT")
    await click(panel.getByRole("button", { name: "保存并重新确认激活角色", exact: true }))
    expect((await again).status()).toBe(409)
    const saving = responseFor(page, `${INTAKE}/people/${created.person_id}/activation-roles`, "PUT")
    await click(dialog.getByRole("button", { name: "确认移除角色并结束岗位", exact: true }))
    const confirmed = await saving
    expect(confirmed.status()).toBe(200)
    const body = confirmed.request().postDataJSON() as { confirm_end_staff_positions: boolean; idempotency_key: string }
    expect(body.confirm_end_staff_positions).toBe(true)
    expect(body.idempotency_key).not.toBe(firstKey)
    await reload(page)
    expect((await positions(page.request, TEAM_A, created.person_id)).map(row => row.staff_role)).toEqual(["coach"])
    expect(await positions(page.request, TEAM_B, created.person_id)).toEqual([])
    await check(panel.getByRole("checkbox", { name: "队伍管理", exact: true }))
    const regranting = responseFor(page, `${INTAKE}/people/${created.person_id}/activation-roles`, "PUT")
    await click(panel.getByRole("button", { name: "保存并重新确认激活角色", exact: true }))
    expect((await regranting).status()).toBe(200)
    expect(await positions(page.request, TEAM_B, created.person_id)).toEqual([])
    expect(await positions(page.request, TEAM_B, created.person_id, "ended")).toHaveLength(1)
  })

  test(`IDT5 ${theme}`, async ({ page }) => {
    const admin = await createAdminRequest()
    const limited = await createRequestSession("19900009045", PASSWORD)
    try {
      await loginWithIdentifier(page, "19900009045", PASSWORD)
      await navigate(page, "/people/assisted-create")
      await click(page.getByRole("button", { name: "资料待补建档", exact: true }))
      await selectIdentity(page, { primary: "assistant_coach", concurrent: [], staff: {} })
      await expect(page.getByRole("group", { name: "助教负责队伍（可暂不分配）", exact: true }).getByRole("checkbox")).toHaveCount(0)
      const input = synthetic(base + 8)
      await fillFacts(page, input, false)
      const created = await createFromForm(page, true)
      expect(await positions(admin.admin, TEAM_A, created.person_id)).toEqual([])
      const before = await get<{ total: number }>(admin.admin, "/api/v1/people?page_size=1")
      const staffBefore = await get<{ total: number }>(admin.admin, `${TEAMS}/teams/${TEAM_A}/staff-positions?page_size=1`)
      const forbidden = await limited.api.post(`${INTAKE}/direct-create-pending`, {
        headers: { "X-CSRF-Token": limited.csrfToken }, data: {
          ...synthetic(base + 10), guardians: [], coach_person_ids: [], team_id: null, team_allocation_status: "pending",
          identity_selection: { primary_identity: "assistant_coach", concurrent_identities: [], primary_staff_identity: null },
          staff_teams: [{ staff_role: "assistant_coach", team_ids: [TEAM_A] }], idempotency_key: randomUUID() } })
      expect(forbidden.status()).toBe(409)
      expect((await forbidden.json() as { code: string }).code).toBe("personnel_intake_conflict")
      expect((await get<{ total: number }>(admin.admin, "/api/v1/people?page_size=1")).total).toBe(before.total)
      expect((await get<{ total: number }>(admin.admin, `${TEAMS}/teams/${TEAM_A}/staff-positions?page_size=1`)).total).toBe(staffBefore.total)
    } finally { await Promise.all([admin.admin.dispose(), limited.api.dispose()]) }
  })

  test(`IDT6 ${theme}`, async ({ page }) => {
    await loginAsAdmin(page)
    const me = await get<{ account_id: string }>(page.request, "/api/v1/auth/me")
    const input = synthetic(base + 12)
    // Seed an old *in-memory* QueryClient record, exactly where the old page
    // saved drafts. No production hook, API response mock or persistence added.
    await page.evaluate(async ({ account, facts, coach, team }) => {
      const path = "/src/app/providers.tsx"
      const providers = await import(/* @vite-ignore */ path)
      const client = providers.AppProviders({ children: null }).props.client
      client.setQueryData(["assisted-people", account, "pending-create-draft"], {
        draft: { kind: "both", ...facts, document_type: "cn_id", document_issuing_country: null,
          guardians: [], coach_person_ids: [coach], team_id: team, team_allocation_status: "pending" },
        roleKeys: ["coach"], attempt: { fingerprint: "synthetic-legacy-intent", key: crypto.randomUUID() } })
    }, { account: me.account_id, facts: input, coach: COACH, team: TEAM_A })
    // SPA navigation is essential: a document reload intentionally drops cache.
    const mobile = (page.viewportSize()?.width ?? 1440) < 768
    if (mobile) {
      await click(page.getByRole("button", { name: "更多", exact: true }))
    }
    const navigation = mobile ? page.getByRole("dialog", { name: "更多导航", exact: true })
      : page.getByRole("navigation", { name: "主导航", exact: true })
    // This actor has people.list: AppShell renders the parent as a real link,
    // not the button used for child-only/no-directory actors in other tests.
    const group = await ready(navigation.getByRole("link", { name: "人员中心", exact: true }))
    if (await group.getAttribute("aria-expanded") === "false") await click(group)
    await click(navigation.getByRole("link", { name: "管理建档", exact: true }))
    await assertTheme(page, true)
    await expect(page.getByText("旧草稿的身份、队伍及负责人已保留，请明确确认主要身份。", { exact: true })).toBeVisible()
    await expect(page.getByLabel("主要身份", { exact: true })).toHaveValue("")
    await expect(page.getByLabel("姓名", { exact: true })).toHaveValue(input.full_name)
    await expect(page.getByLabel("主要身份", { exact: true })).toBeEnabled()
    await choose(page.getByLabel("主要身份", { exact: true }), "student")
    await expect(page.getByLabel(/^队员所属队伍/)).toHaveValue(TEAM_A)
    await expect(page.getByRole("checkbox", { name: "E2E协助独立教练", exact: true })).toBeChecked()
    await expect(page.getByRole("group", { name: "人员身份", exact: true }).getByRole("checkbox", { name: "教练", exact: true })).toBeChecked()
    await refocus(page)
    await (await ready(page.getByLabel("姓名", { exact: true }))).focus()
    await page.keyboard.press("End")
    await page.keyboard.type("复核")
    await page.keyboard.press("Tab")
    await expect(page.getByLabel("姓名", { exact: true })).toHaveValue(`${input.full_name}复核`)
    await expectNoHorizontalOverflow(page)
    const created = await createFromForm(page, true)
    const plan = await get<RolePlan>(page.request, `${INTAKE}/people/${created.person_id}/activation-roles`)
    expect(plan.role_keys).toEqual(["coach"])
    expect(plan.account_activated).toBe(false)
    await noStoredSecrets(page, [input.email, input.identity_number, PASSWORD])
  })
}
