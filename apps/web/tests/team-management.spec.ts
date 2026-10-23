import { randomUUID } from "node:crypto"

import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"

import { E2E_CREDENTIALS } from "./e2eCredentials"
import {
  createAdminRequest,
  expectNoHorizontalOverflow,
  logout,
} from "./helpers"

const API = "/api/v1/team-management"
const TEAM_A = "e8100000-0000-4000-8000-000000000001"
const TEAM_B = "e8100000-0000-4000-8000-000000000002"
const TEAM_C = "e8100000-0000-4000-8000-000000000003"
const PURE_MEMBER_PERSON = "e8000000-0000-4000-8000-000000000006"
const STUDENT_PERSON = "e1000000-0000-4000-8000-000000000007"
const STUDENT_MEMBERSHIP = "e8120000-0000-4000-8000-000000000001"
const SHARED_ASSIGNMENT = "e8110000-0000-4000-8000-000000000005"
const HISTORY_DISABLED = "e8120000-0000-4000-8000-000000000002"
const HISTORY_DELETED = "e8120000-0000-4000-8000-000000000003"

type TeamPage = { items: { id: string; name: string; member_count: number }[]; total: number }
type MembershipPage = { items: { id: string; person_id: string; account_status: string }[]; total: number }
type MutationResult = { operation_id: string; membership_id: string | null }

async function login(page: Page, key: keyof typeof E2E_CREDENTIALS): Promise<string> {
  const credential = E2E_CREDENTIALS[key]
  await page.goto("/login")
  await page.getByLabel("手机号").fill(credential.identifier)
  await page.getByLabel("密码", { exact: true }).fill(credential.password)
  const responsePromise = page.waitForResponse((response) => (
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v1/auth/login"
  ))
  await page.getByRole("button", { name: "登录" }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  return ((await response.json()) as { csrf_token: string }).csrf_token
}

async function openManagement(page: Page): Promise<void> {
  await page.goto("/people/teams")
  await expect(page.getByRole("heading", { name: "队伍管理", exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
}

async function teamPage(page: Page): Promise<TeamPage> {
  const response = await page.request.get(`${API}/teams`)
  expect(response.status()).toBe(200)
  return (await response.json()) as TeamPage
}

async function membershipPage(page: Page, teamId: string, status = "current"): Promise<MembershipPage> {
  const response = await page.request.get(`${API}/teams/${teamId}/memberships?status=${status}`)
  expect(response.status()).toBe(200)
  return (await response.json()) as MembershipPage
}

test.describe.configure({ mode: "serial" })

test("manager A/B and unbound manager have isolated menus, lists, and history", async ({ page }) => {
  await login(page, "team_manager_a")
  await openManagement(page)
  await expect(page.getByRole("button", { name: "查看 E2E王者一队" })).toBeVisible()
  await expect(page.getByRole("button", { name: "查看 E2E和平二队" })).toHaveCount(0)
  expect((await teamPage(page)).items.map((item) => item.id)).toEqual([TEAM_A])
  const forbiddenB = await page.request.get(`${API}/teams/${TEAM_B}/memberships`)
  expect([403, 404]).toContain(forbiddenB.status())
  await logout(page)

  await login(page, "team_manager_b")
  await openManagement(page)
  expect((await teamPage(page)).items.map((item) => item.id)).toEqual([TEAM_B])
  const historical = await membershipPage(page, TEAM_B, "left")
  expect(historical.items.map((item) => item.id)).toEqual(expect.arrayContaining([
    HISTORY_DISABLED, HISTORY_DELETED,
  ]))
  expect(historical.items.find((item) => item.id === HISTORY_DISABLED)?.account_status).toBe("disabled")
  expect(historical.items.find((item) => item.id === HISTORY_DELETED)?.account_status).toBe("deleted")
  await logout(page)

  await login(page, "team_manager_unbound")
  const unboundContext = await page.request.get(`${API}/context`)
  expect(unboundContext.status()).toBe(200)
  expect(((await unboundContext.json()) as { can_open_management: boolean }).can_open_management).toBe(false)
  await page.goto("/people/teams")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible()
  await expect(page.getByRole("button", { name: /查看 E2E/ })).toHaveCount(0)
  await logout(page)

  await login(page, "team_ordinary_admin")
  const ordinaryContext = await page.request.get(`${API}/context`)
  expect(ordinaryContext.status()).toBe(200)
  expect(((await ordinaryContext.json()) as { can_open_management: boolean }).can_open_management).toBe(false)
  await page.goto("/people/teams")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible()
  await expect(page.getByRole("button", { name: /查看 E2E/ })).toHaveCount(0)
})

test("manager can add then remove a qualified member without losing history", async ({ page }) => {
  await login(page, "team_manager_a")
  await openManagement(page)
  await page.getByRole("button", { name: "查看 E2E王者一队" }).click()
  await page.getByRole("button", { name: "添加队员" }).click()
  const dialog = page.getByRole("dialog", { name: "添加队员" })
  await dialog.getByLabel("搜索队员").fill("E2E纯队员")
  await dialog.getByRole("button", { name: /选择 E2E纯队员/ }).click()
  await dialog.getByRole("button", { name: "确认添加" }).click()
  await expect(dialog).toHaveCount(0)
  const current = await membershipPage(page, TEAM_A)
  expect(current.items.filter((item) => item.person_id === PURE_MEMBER_PERSON)).toHaveLength(1)

  await page.getByRole("button", { name: "移出 E2E纯队员" }).click()
  const leaveDialog = page.getByRole("dialog", { name: "移出队员" })
  await expect(leaveDialog.getByText("仅结束队伍归属，不删除账号、学员身份或历史")).toBeVisible()
  await leaveDialog.getByRole("button", { name: "确认移出" }).click()
  await expect(leaveDialog).toHaveCount(0)
  expect((await membershipPage(page, TEAM_A)).items.some((item) => item.person_id === PURE_MEMBER_PERSON)).toBe(false)
  expect((await membershipPage(page, TEAM_A, "left")).items.some((item) => item.person_id === PURE_MEMBER_PERSON)).toBe(true)
})

test("one manager binding can be revoked while another manager retains access", async ({ page }) => {
  await login(page, "team_manager_shared")
  await openManagement(page)
  await expect(page.getByRole("button", { name: "查看 E2E王者一队" })).toBeVisible()
  await page.getByRole("button", { name: "查看 E2E王者一队" }).click()
  await page.getByRole("button", { name: "移出 E2E自助学员" }).click()
  const staleDialog = page.getByRole("dialog", { name: "移出队员" })
  await expect(staleDialog.getByRole("button", { name: "确认移出" })).toBeVisible()

  const { admin, csrfToken } = await createAdminRequest()
  try {
    const before = await admin.get(`${API}/teams/${TEAM_A}/memberships`)
    expect(before.status()).toBe(200)
    expect(((await before.json()) as MembershipPage).items.some((item) => item.id === STUDENT_MEMBERSHIP)).toBe(true)
    const response = await admin.post(`${API}/manager-assignments/${SHARED_ASSIGNMENT}/end`, {
      headers: { "X-CSRF-Token": csrfToken },
      data: { idempotency_key: randomUUID() },
    })
    expect(response.status()).toBe(200)
    const attemptedWrite = page.waitForResponse((reply) => (
      reply.request().method() === "POST"
      && new URL(reply.url()).pathname === `${API}/memberships/${STUDENT_MEMBERSHIP}/leave`
    ))
    await staleDialog.getByRole("button", { name: "确认移出" }).click()
    expect([403, 404]).toContain((await attemptedWrite).status())
    await expect(staleDialog).toHaveCount(0)
    await expect(page.getByRole("region", { name: "E2E王者一队 名单" })).toHaveCount(0)
    await expect(page.getByRole("button", { name: "查看 E2E王者一队" })).toHaveCount(0)
    const after = await admin.get(`${API}/teams/${TEAM_A}/memberships`)
    expect(after.status()).toBe(200)
    expect(((await after.json()) as MembershipPage).items.some((item) => item.id === STUDENT_MEMBERSHIP)).toBe(true)
  } finally {
    await admin.dispose()
  }
  await page.reload()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible()
  const revokedContext = await page.request.get(`${API}/context`)
  expect(revokedContext.status()).toBe(200)
  expect(((await revokedContext.json()) as { can_open_management: boolean }).can_open_management).toBe(false)
  await expect(page.getByRole("button", { name: "查看 E2E王者一队" })).toHaveCount(0)
  const oldPage = await page.request.get(`${API}/teams/${TEAM_A}/memberships`)
  expect([403, 404]).toContain(oldPage.status())
  await logout(page)

  await login(page, "team_manager_multi")
  await openManagement(page)
  expect((await teamPage(page)).items.map((item) => item.id).sort()).toEqual([TEAM_A, TEAM_B])
})

test("lost join response keeps one operation and pure member retains self-only access", async ({ page }) => {
  await login(page, "team_manager_a")
  await openManagement(page)
  await page.getByRole("button", { name: "查看 E2E王者一队" }).click()
  await page.getByRole("button", { name: "添加队员" }).click()
  const dialog = page.getByRole("dialog", { name: "添加队员" })
  await dialog.getByLabel("搜索队员").fill("E2E纯队员")
  await dialog.getByRole("button", { name: /选择 E2E纯队员/ }).click()

  const operationKeys: string[] = []
  let responseLost = false
  const rosterUrl = (url: URL) => url.pathname === `${API}/teams/${TEAM_A}/memberships`
  await page.route(rosterUrl, async (route) => {
    if (route.request().method() !== "POST") {
      if (responseLost) await route.abort("failed")
      else await route.continue()
      return
    }
    operationKeys.push((route.request().postDataJSON() as { idempotency_key: string }).idempotency_key)
    if (!responseLost) {
      const completed = await route.fetch()
      expect(completed.status()).toBe(200)
      responseLost = true
      await route.abort("failed")
    } else {
      responseLost = false
      await route.continue()
    }
  })
  await dialog.getByRole("button", { name: "确认添加" }).click()
  await expect(dialog.getByRole("button", { name: "用原操作键重试" })).toBeVisible()
  await dialog.getByRole("button", { name: "用原操作键重试" }).click()
  await expect(dialog).toHaveCount(0)
  await page.unroute(rosterUrl)
  expect(operationKeys).toHaveLength(2)
  expect(operationKeys[1]).toBe(operationKeys[0])
  const current = await membershipPage(page, TEAM_A)
  expect(current.items.filter((item) => item.person_id === PURE_MEMBER_PERSON)).toHaveLength(1)
  await logout(page)

  await login(page, "team_member_only")
  const memberSelf = await page.request.get(`${API}/me`)
  expect(memberSelf.status()).toBe(200)
  expect(((await memberSelf.json()) as { current: { team_id: string } | null }).current?.team_id).toBe(TEAM_A)
  const teammateRoster = await page.request.get(`${API}/teams/${TEAM_A}/memberships`)
  expect([403, 404]).toContain(teammateRoster.status())
})

test("superadmin transfer and student self view stay in sync", async ({ page }) => {
  await login(page, "admin")
  await openManagement(page)
  const { admin, csrfToken } = await createAdminRequest()
  let transferred: MutationResult
  try {
    const result = await admin.post(`${API}/memberships/${STUDENT_MEMBERSHIP}/transfer`, {
      headers: { "X-CSRF-Token": csrfToken },
      data: { target_team_id: TEAM_C, reason: "E2E 验证跨分部调队", idempotency_key: randomUUID() },
    })
    expect(result.status()).toBe(200)
    transferred = (await result.json()) as MutationResult
  } finally {
    await admin.dispose()
  }
  expect(transferred.membership_id).toBeTruthy()
  expect((await membershipPage(page, TEAM_A, "left")).items.some((item) => item.id === STUDENT_MEMBERSHIP)).toBe(true)
  expect((await membershipPage(page, TEAM_C)).items.some((item) => item.id === transferred.membership_id)).toBe(true)
  await page.goto(`/people/${STUDENT_PERSON}`)
  await expect(page.getByText("队伍归属").locator("..")).toContainText("三角洲行动 · E2E三角洲三队")
  await logout(page)

  await login(page, "student")
  const self = await page.request.get(`${API}/me`)
  expect(self.status()).toBe(200)
  const body = (await self.json()) as { current: { team_id: string } | null }
  expect(body.current?.team_id).toBe(TEAM_C)
  await page.goto("/me/profile")
  await expect(page.getByRole("region", { name: "我的队伍" })).toContainText("E2E三角洲三队")
  const roster = await page.request.get(`${API}/teams/${TEAM_C}/memberships`)
  expect([403, 404]).toContain(roster.status())
})

test("mobile roster dialog stays inside viewport with focused search", async ({ page }, testInfo) => {
  await login(page, "team_manager_a")
  await openManagement(page)
  await page.getByRole("button", { name: "查看 E2E王者一队" }).click()
  await page.getByRole("button", { name: "添加队员" }).click()
  const search = page.getByRole("dialog", { name: "添加队员" }).getByLabel("搜索队员")
  await search.focus()
  await expect(search).toBeFocused()
  await search.fill("E2E")
  await expectNoHorizontalOverflow(page)
  const bounds = await search.boundingBox()
  const width = page.viewportSize()?.width ?? 1440
  expect(bounds).not.toBeNull()
  expect(bounds!.x).toBeGreaterThanOrEqual(0)
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(width + 1)
  await page.screenshot({ path: testInfo.outputPath("team-roster-dialog.png"), fullPage: true })
})
