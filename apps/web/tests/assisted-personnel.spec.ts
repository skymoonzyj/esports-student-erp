import { randomUUID } from "node:crypto"
import { expect, test } from "@playwright/test"
import type { APIRequestContext, Page, Response } from "@playwright/test"

import { E2E_CREDENTIALS } from "./e2eCredentials"
import { assistedPrivateConfig } from "./assistedPrivateConfig"
import {
  createAdminRequest, createRequestSession, expectNoHorizontalOverflow,
  loginAsAdmin, loginWithIdentifier, logout, makeCnId, readRegistrationTestCode,
} from "./helpers"

// These aliases are synthetic fixtures, not deployable account credentials.
const PASSWORD = E2E_CREDENTIALS.admin.password
const TEAM = "e9100000-0000-4000-8000-000000000001"
const MANAGER = "e9010000-0000-4000-8000-000000000002"
const COACH = "e9000000-0000-4000-8000-000000000001"
const PERSONNEL = "/api/v1/personnel-intake"
const TEAM_API = "/api/v1/team-management"
const actors = assistedPrivateConfig()?.actors ?? { coach: "19900009001", manager: "19900009002", administration: "19900009005" }
type Kind = "student" | "player"
type RequestView = { id: string; version: number; status: string; approved_person_id: string | null }
type Member = { id: string; person_id: string; account_status: string; [key: string]: unknown }
type RoomDetail = {
  current_stays: Array<{ id: string; person_id: string; version: number }>
  history: unknown[]; transfers: unknown[]
}

function responseFor(page: Page, path: string, method = "POST"): Promise<Response> {
  return page.waitForResponse((response) => response.request().method() === method
    && new URL(response.url()).pathname === path)
}

async function json<T>(api: APIRequestContext, path: string): Promise<T> {
  const response = await api.get(path)
  expect(response.status()).toBe(200)
  return await response.json() as T
}

function day(offset: number): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai",
    year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(new Date(Date.now() + offset * 86_400_000))
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]))
  return `${values.year}-${values.month}-${values.day}`
}

async function fillMinor(page: Page, kind: Kind) {
  const sequence = kind === "player" ? 901 : 903
  const facts = {
    full_name: `E2E实测未成年${kind === "player" ? "队员" : "学员"}`,
    identity_number: makeCnId("20150103", sequence), phone: "19900009888",
    email: `assisted-live-${kind}@example.com`, date_of_birth: "2015-01-03", sex: "male",
  }
  await page.getByLabel("人员身份", { exact: true }).selectOption(kind)
  for (const [label, value] of [["姓名", facts.full_name], ["身份证", facts.identity_number],
    ["手机号", facts.phone], ["邮箱", facts.email], ["出生日期", facts.date_of_birth]]) {
    await page.getByLabel(label, { exact: true }).fill(value)
  }
  await page.getByLabel("性别", { exact: true }).selectOption("male")
  if (kind === "player") await page.getByLabel("所属队伍", { exact: true }).selectOption(TEAM)
  else await page.getByRole("checkbox", { name: "E2E协助独立教练", exact: true }).check()
  await page.getByRole("button", { name: "添加监护人" }).click()
  await page.getByLabel("监护人姓名", { exact: true }).fill(`E2E实测监护${kind}`)
  await page.getByLabel("监护人身份证", { exact: true }).fill(makeCnId("19800103", sequence))
  await page.getByLabel("监护人手机号", { exact: true }).fill("19900009889")
  await page.getByLabel("监护人出生日期", { exact: true }).fill("1980-01-03")
  await page.getByLabel("监护人性别", { exact: true }).selectOption("male")
  await page.getByLabel("与本人关系", { exact: true }).fill("father")
  // No guardian email/account is created or required.
  await expect(page.getByLabel("监护人邮箱", { exact: true })).toHaveValue("")
  await expectNoHorizontalOverflow(page)
  return facts
}

async function openRequest(page: Page, id: string, review: boolean) {
  await page.goto(review ? "/people/assisted-review" : "/people/assisted-requests")
  await expect(page.getByRole("heading", { name: review ? "建档审核" : "我的建档申请", exact: true })).toBeVisible()
  const list = await json<{ items: RequestView[] }>(page.request,
    `${PERSONNEL}/requests?view=${review ? "review" : "mine"}&page=1&page_size=20`)
  const index = list.items.findIndex((item) => item.id === id)
  expect(index).toBeGreaterThanOrEqual(0)
  await page.getByRole("button", { name: `查看申请 ${index + 1}`, exact: true }).click()
  await expect(page.getByRole("heading", { name: "申请详情", exact: true })).toBeVisible()
}

async function verifyRoom(page: Page, room: string): Promise<string> {
  await page.goto("/housing")
  await expect(page.getByRole("heading", { name: "住宿房态", exact: true })).toBeVisible()
  if ((page.viewportSize()?.width ?? 1440) < 720) {
    await page.getByRole("button", { name: "打开筛选" }).click()
  }
  const filters = page.locator(".housingFilters")
  await filters.getByLabel("搜索房间").fill(room)
  await filters.getByRole("button", { name: "应用筛选" }).click()
  const card = page.locator(".housingRoomCard").filter({ hasText: room })
  await expect(card).toHaveCount(1)
  await card.getByRole("button", { name: `核对房间 ${room}`, exact: true }).click()
  await expect(card.getByText("空闲，可入住", { exact: true })).toBeVisible()
  await card.getByRole("button", { name: `查看房间 ${room} 详情`, exact: true }).click()
  await expect(page.getByRole("heading", { name: `房间 ${room}`, exact: true })).toBeVisible()
  const id = new URL(page.url()).pathname.split("/").at(-1)!
  expect(id).toMatch(/^[0-9a-f-]{36}$/)
  return id
}

async function housingSnapshot(api: APIRequestContext, rooms: string[]) {
  return Promise.all(rooms.map(async (room) => {
    const detail = await json<RoomDetail>(api, `/api/v1/housing/rooms/${room}/detail`)
    return { current_stays: detail.current_stays, history: detail.history, transfers: detail.transfers }
  }))
}

async function memberships(api: APIRequestContext, person: string) {
  const result = await json<{ items: Member[] }>(api, `${TEAM_API}/teams/${TEAM}/memberships?status=all&page_size=100`)
  return result.items.filter((item) => item.person_id === person).map((item) => {
    const { account_status, ...facts } = item
    expect(["not_activated", "active"]).toContain(account_status)
    return facts
  })
}

async function approvedRequestGuardians(api: APIRequestContext, request: string, person: string) {
  const detail = await json<RequestView & { scoped_payload: { guardians: unknown[] } | null }>(
    api, `${PERSONNEL}/requests/${request}`)
  expect(detail.status).toBe("approved")
  expect(detail.approved_person_id).toBe(person)
  expect(detail.scoped_payload).not.toBeNull()
  return detail.scoped_payload!.guardians
}

async function expectNoStoredSecrets(page: Page, secrets: string[]) {
  const leaked = await page.evaluate((values) => {
    const stored = [window.location.href, ...Object.values(localStorage), ...Object.values(sessionStorage)].join("\n")
    return values.some((value) => stored.includes(value))
  }, secrets)
  expect(leaked).toBe(false)
}

test.describe.configure({ mode: "serial" })
for (const kind of ["player", "student"] as const) {
  test(`reviewed accountless ${kind} can stay then activate original profile`, async ({ page }) => {
    test.setTimeout(240_000)
    page.setDefaultTimeout(15_000)
    const ownerPhone = actors[kind === "player" ? "manager" : "coach"]
    const administrator = await createAdminRequest()
    const owner = await createRequestSession(ownerPhone, PASSWORD)
    try {
      await loginWithIdentifier(page, ownerPhone, PASSWORD)
      await page.goto("/people/assisted-requests")
      await page.getByRole("button", { name: "新建申请", exact: true }).click()
      const facts = await fillMinor(page, kind)
      const submit = responseFor(page, `${PERSONNEL}/requests`)
      await page.getByRole("button", { name: "提交申请", exact: true }).click()
      const submittedResponse = await submit
      expect(submittedResponse.status()).toBe(201)
      const submitted = await submittedResponse.json() as RequestView
      expect(submitted.status).toBe("pending")
      expect(submitted.approved_person_id).toBeNull()
      await expectNoStoredSecrets(page, [facts.identity_number, facts.email, PASSWORD])
      await logout(page)

      await loginAsAdmin(page)
      await openRequest(page, submitted.id, true)
      await page.getByLabel("退回原因", { exact: true }).fill("请补充核实姓名")
      const returned = responseFor(page, `${PERSONNEL}/requests/${submitted.id}/return`)
      await page.getByRole("button", { name: "退回修改", exact: true }).click()
      expect((await returned).status()).toBe(200)
      await logout(page)

      await loginWithIdentifier(page, ownerPhone, PASSWORD)
      await openRequest(page, submitted.id, false)
      const name = `${facts.full_name}复核`
      await page.getByLabel("姓名", { exact: true }).fill(name)
      const resubmitted = responseFor(page, `${PERSONNEL}/requests/${submitted.id}/resubmit`)
      await page.getByRole("button", { name: "提交申请", exact: true }).click()
      const revisionResponse = await resubmitted
      expect(revisionResponse.status()).toBe(200)
      const revision = await revisionResponse.json() as RequestView
      expect(revision.version).toBe(submitted.version + 1)
      expect(revision.status).toBe("pending")
      // No stale-version approval can materialize a different person.
      const stale = await administrator.admin.post(`${PERSONNEL}/requests/${submitted.id}/approve`, {
        headers: { "X-CSRF-Token": administrator.csrfToken }, data: { expected_version: submitted.version },
      })
      expect(stale.status()).toBe(409)
      await logout(page)
      await loginAsAdmin(page)
      await openRequest(page, submitted.id, true)
      await expect(page.getByText(name, { exact: true })).toBeVisible()
      const approval = responseFor(page, `${PERSONNEL}/requests/${submitted.id}/approve`)
      await page.getByRole("button", { name: "审核通过", exact: true }).click()
      const approvedResponse = await approval
      expect(approvedResponse.status()).toBe(200)
      const approved = await approvedResponse.json() as { person_id: string }
      const before = await json<{ id: string; account_status: string; student_number: string | null }>(
        administrator.admin, `/api/v1/people/${approved.person_id}`)
      expect(before.account_status).toBe("not_activated")
      if (kind === "player") expect(before.student_number).toBeNull()
      // Legacy live guardian API is student-only. Independent players use the
      // authorized approval snapshot here; full live relationship preservation
      // is separately checked across every DB column for student/player/both.
      const guardianBefore = kind === "player"
        ? await approvedRequestGuardians(administrator.admin, submitted.id, approved.person_id)
        : await json<unknown[]>(administrator.admin, `/api/v1/people/${approved.person_id}/guardians`)
      expect(guardianBefore).toHaveLength(1)
      if (kind === "player") expect(guardianBefore).toEqual([{
        full_name: "E2E实测监护player", identity_number: makeCnId("19800103", 901),
        phone: "19900009889", email: null, relationship: "father", sex: "male",
        date_of_birth: "1980-01-03", is_primary: true,
      }])
      const teamBefore = await memberships(administrator.admin, approved.person_id)
      expect(teamBefore).toHaveLength(kind === "player" ? 1 : 0)
      await logout(page)

      await loginWithIdentifier(page, actors.administration, PASSWORD)
      const source = kind === "player" ? "A-201" : "A-203"
      const target = kind === "player" ? "A-202" : "A-204"
      const sourceId = await verifyRoom(page, source)
      const targetId = await verifyRoom(page, target)
      await page.goto(`/housing/rooms/${sourceId}`)
      await page.getByRole("button", { name: "安排入住", exact: true }).click()
      const arrange = page.getByRole("dialog", { name: `安排入住 · ${source}`, exact: true })
      await arrange.getByLabel("搜索入住人员").fill(name)
      await arrange.getByRole("radio", { name: `选择 ${name}`, exact: true }).check()
      await arrange.getByLabel("住宿类型").selectOption("permanent")
      await arrange.getByLabel("时长类型").selectOption("long_term")
      // Newly approved guardian relationships start today. The server resolves
      // today's date-only transfer/checkout actions to their operation times.
      await arrange.getByLabel("计划入住日期").fill(day(0))
      const stayCreated = responseFor(page, `/api/v1/housing/rooms/${sourceId}/stays`)
      await arrange.getByRole("button", { name: "确认入住", exact: true }).click()
      expect((await stayCreated).status()).toBe(201)
      await expect(arrange).toBeHidden()
      await page.getByRole("button", { name: `调房 ${name}`, exact: true }).click()
      const transfer = page.getByRole("dialog", { name: `调房 · ${name}`, exact: true })
      await transfer.getByRole("radio", { name: new RegExp(`${target}$`) }).check()
      await transfer.getByLabel("调房日期").fill(day(0))
      await transfer.getByLabel("调房原因").fill("测试范围内调房")
      await transfer.getByRole("button", { name: "确认调房", exact: true }).click()
      await expect(transfer).toBeHidden()
      const housingBefore = await housingSnapshot(administrator.admin, [sourceId, targetId])
      expect(housingBefore[0].history).toHaveLength(1)
      expect(housingBefore[1].current_stays).toHaveLength(1)
      await logout(page)

      await loginWithIdentifier(page, ownerPhone, PASSWORD)
      await page.goto("/my-people")
      await page.getByLabel("姓名或昵称", { exact: true }).fill(name)
      const card = page.getByRole("article").filter({ has: page.getByRole("heading", { name, exact: true }) })
      await expect(card.getByText("已入住", { exact: true })).toBeVisible()
      await expect(card.getByText(new RegExp(target))).toBeVisible()
      await expect(card.getByRole("button")).toHaveCount(0)
      await expectNoHorizontalOverflow(page)
      const managed = await json<{ items: Array<Record<string, unknown>> }>(owner.api,
        `${PERSONNEL}/managed-people?query=${encodeURIComponent(name)}`)
      expect(managed.items).toHaveLength(1)
      expect(Object.keys(managed.items[0]).sort()).toEqual([
        "authorized_affiliations", "full_name", "housing", "kinds", "nickname", "person_id",
      ])
      const housing = managed.items[0].housing as { stay: Record<string, unknown> }
      expect(Object.keys(housing).sort()).toEqual(["next_planned_stay", "status", "stay"])
      expect(Object.keys(housing.stay).sort()).toEqual([
        "actual_check_in_at", "actual_check_out_at", "building_name", "duration_mode",
        "floor_name", "planned_check_in_at", "planned_check_out_at", "room_name",
      ])
      for (const unrelated of ["E2E协助种子10", "E2E协助种子11"]) {
        const invisible = await json<{ items: unknown[]; total: number }>(owner.api,
          `${PERSONNEL}/managed-people?query=${encodeURIComponent(unrelated)}`)
        expect(invisible).toMatchObject({ items: [], total: 0 })
      }
      for (const path of ["/api/v1/housing/catalog", `/api/v1/housing/rooms/${targetId}/detail`,
        "/api/v1/housing/report-batches"]) {
        const denied = await owner.api.get(path)
        expect([403, 404]).toContain(denied.status())
      }
      const deniedWrite = await owner.api.post(`/api/v1/housing/rooms/${targetId}/stays`, {
        headers: { "X-CSRF-Token": owner.csrfToken }, data: { person_id: approved.person_id,
          stay_purpose: "permanent", duration_mode: "long_term", planned_check_in_at: `${day(1)}T00:00:00+08:00` },
      })
      expect([403, 404]).toContain(deniedWrite.status())
      const deniedCoaches = await owner.api.put(`${PERSONNEL}/people/${approved.person_id}/coaches`, {
        headers: { "X-CSRF-Token": owner.csrfToken }, data: { coach_person_ids: [COACH],
          expected_version: 1, reason: "无权修改测试" },
      })
      expect([403, 404]).toContain(deniedCoaches.status())
      await logout(page)

      await page.goto("/activate-profile")
      await page.getByLabel("本人邮箱", { exact: true }).fill(facts.email)
      const start = responseFor(page, "/api/v1/auth/profile-activation/start")
      await page.getByRole("button", { name: "请求邮箱验证码", exact: true }).click()
      expect((await start).status()).toBe(200)
      const code = await readRegistrationTestCode({ destination: facts.email, purpose: "assisted_activation" })
      await page.getByLabel("验证码", { exact: true }).fill(code)
      await page.getByRole("button", { name: "验证邮箱", exact: true }).click()
      await page.getByLabel("新密码", { exact: true }).fill(PASSWORD)
      await expectNoStoredSecrets(page, [code, facts.email, facts.identity_number, PASSWORD])
      await page.getByRole("button", { name: "完成激活", exact: true }).click()
      await expect(page).toHaveURL(/\/login$/)
      // Completion is not login; first login remains explicit.
      expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401)
      await loginWithIdentifier(page, facts.email, PASSWORD)
      const me = await json<{ person_id: string; account_id: string }>(page.request, "/api/v1/auth/me")
      expect(me.person_id).toBe(before.id)
      const access = await json<{ roles: Array<{ key: string }> }>(administrator.admin,
        `/api/v1/accounts/${me.account_id}/access`)
      expect(access.roles.map((role) => role.key).sort()).toEqual(kind === "player" ? ["team_member"] : ["student"])
      await page.reload()
      await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
      expect((await json<{ person_id: string }>(page.request, "/api/v1/auth/me")).person_id).toBe(before.id)
      expect(await memberships(administrator.admin, approved.person_id)).toEqual(teamBefore)
      const guardianAfter = kind === "player"
        ? await approvedRequestGuardians(administrator.admin, submitted.id, approved.person_id)
        : await json(administrator.admin, `/api/v1/people/${approved.person_id}/guardians`)
      expect(guardianAfter).toEqual(guardianBefore)
      expect(await housingSnapshot(administrator.admin, [sourceId, targetId])).toEqual(housingBefore)
      await logout(page)
      expect((await page.request.get("/api/v1/auth/me")).status()).toBe(401)

      await loginWithIdentifier(page, actors.administration, PASSWORD)
      await page.goto(`/housing/rooms/${targetId}`)
      await page.getByRole("button", { name: `申请退房 ${name}`, exact: true }).click()
      const checkout = page.getByRole("dialog", { name: `申请退房 · ${name}`, exact: true })
      await checkout.getByLabel("实际离开日期").fill(day(0))
      await checkout.getByRole("button", { name: "申请退房", exact: true }).click()
      const confirm = page.getByRole("dialog", { name: `二次确认退房 · ${name}`, exact: true })
      await confirm.getByRole("button", { name: "二次确认退房", exact: true }).click()
      await expect(confirm).toBeHidden()
      const final = await housingSnapshot(administrator.admin, [sourceId, targetId])
      expect(final[1].current_stays).toHaveLength(0)
      expect(final[1].history).toHaveLength(1)
    } finally {
      await owner.api.dispose()
      await administrator.admin.dispose()
    }
  })
}

test("revoked manager cannot revisit owner or old housing pages", async ({ page }) => {
  const administrator = await createAdminRequest()
  const owner = await createRequestSession(actors.manager, PASSWORD)
  try {
    await loginWithIdentifier(page, actors.manager, PASSWORD)
    await page.goto("/my-people")
    await expect(page.getByRole("heading", { name: "E2E协助种子3", exact: true })).toBeVisible()
    const assignments = await json<{ items: Array<{ id: string; account_id: string }> }>(administrator.admin,
      `${TEAM_API}/teams/${TEAM}/manager-assignments`)
    const assignment = assignments.items.find((item) => item.account_id === MANAGER)
    expect(assignment).toBeDefined()
    const removed = await administrator.admin.post(`${TEAM_API}/manager-assignments/${assignment!.id}/end`, {
      headers: { "X-CSRF-Token": administrator.csrfToken }, data: { idempotency_key: randomUUID() },
    })
    expect(removed.status()).toBe(200)
    const now = await owner.api.get(`${PERSONNEL}/managed-people`)
    // Relationship revocation may remove the capability entirely or return an
    // empty authorized collection; neither branch may retain a former subject.
    if (now.status() === 200) {
      expect(await now.json()).toMatchObject({ items: [], total: 0, next_cursor: null })
    } else expect(now.status()).toBe(403)
    await page.reload()
    await expect(page.getByRole("heading", { name: "E2E协助种子3", exact: true })).toHaveCount(0)
    for (const path of ["/housing", "/housing/reports"]) {
      await page.goto(path)
      await expect(page.locator(".housingRoomCard")).toHaveCount(0)
    }
    expect([403, 404]).toContain((await owner.api.get("/api/v1/housing/catalog")).status())
    // Also change the actual role while this same authenticated session is live.
    const roles = await json<Array<{ id: string; key: string }>>(administrator.admin, "/api/v1/roles")
    const role = roles.find((item) => item.key === "team_manager")!
    const revoked = await administrator.admin.delete(`/api/v1/accounts/${MANAGER}/roles/${role.id}`, {
      headers: { "X-CSRF-Token": administrator.csrfToken },
    })
    expect(revoked.status()).toBe(204)
    expect((await owner.api.get(`${PERSONNEL}/managed-people`)).status()).toBe(403)
  } finally {
    await owner.api.dispose()
    await administrator.admin.dispose()
  }
})
