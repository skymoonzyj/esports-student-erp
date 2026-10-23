import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"
import { createAdminRequest, expectAboveMobileNavigation, expectNoHorizontalOverflow, loginWithIdentifier, refocus } from "./helpers"
import { e2eCredentialTuple } from "./e2eCredentials"
import { resolve } from "node:path"

test.use({ trace: "off", screenshot: "off", video: "off" })
test.beforeEach(async ({ page }, info) => { void page; test.skip(!["desktop-1440", "mobile-360"].includes(info.project.name)) })
const orgA = "e2900000-0000-4000-8000-000000000001"
const orgB = "e2900000-0000-4000-8000-000000000002"
const source = (id: string) => ({ source_kind: "organization", organization_id: id, name: id === orgA ? "合成组织甲" : "合成组织乙" })
async function syntheticApi(page: Page) {
  const control = {
    hold: false,
    pending: false,
    release: () => {},
    revoked: false,
    late: false,
    latePending: false,
    releaseLate: () => {},
    financialRequests: 0,
  }
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url())
    const json = (body: unknown) => route.fulfill({
      status: 200,
      contentType: "application/json",
      headers: { "Cache-Control": "no-store, private" },
      body: JSON.stringify(body),
    })
    if (url.pathname.endsWith("/auth/me")) {
      return json({
        account_id: "synthetic-invitations",
        person_id: "synthetic-person",
        full_name: "合成负责人",
        email: null,
        force_password_change: false,
        permissions: [],
        scoped_permissions: [],
      })
    }
    if (url.pathname.endsWith("/auth/csrf")) {
      return json({ csrf_token: "synthetic-csrf" })
    }
    if (url.pathname.endsWith("/portal/context")) {
      return json({
        coach_available: false,
        learning_available: false,
        payments_available: false,
        housing_available: false,
        housing_management_available: false,
        invitations_available: true,
        adult_guardian_invitations_available: false,
        food_profile_available: false,
        food_preferences_onboarding_required: false,
        learning_mode: null,
      })
    }
    if (url.pathname.endsWith("/team-management/context")) {
      return json({
        can_open_management: false,
        can_read_self: false,
        can_manage_catalog: false,
        can_assign_managers: false,
        can_transfer: false,
      })
    }
    if (url.pathname.endsWith("/referrals/me/sources")) {
      if (control.hold) {
        control.pending = true
        await new Promise<void>((resolve) => { control.release = resolve })
      }
      return json({
        sources: control.revoked ? [source(orgA)] : [source(orgA), source(orgB)],
      })
    }
    if (url.pathname.endsWith("/referrals/me/housing")) {
      const b = url.searchParams.get("organization_id") === orgB
      if (b && control.late) {
        control.latePending = true
        await new Promise<void>((resolve) => { control.releaseLate = resolve })
      }
      return json({
        as_of: "2026-10-11T01:00:00Z",
        total: 1,
        page: 1,
        page_size: 20,
        items: [{
          person_id: "e2000000-0000-4000-8000-000000000010",
          full_name: b ? "合成受邀乙" : "合成受邀甲",
          nickname: "测试昵称",
          source: source(b ? orgB : orgA),
          housing: {
            status: "checked_in",
            next_planned_stay: {
              building_name: "合成楼栋",
              floor_name: "四层",
              room_name: "4-203",
              duration_mode: "long_term",
              planned_check_in_at: "2026-11-01T00:00:00Z",
              planned_check_out_at: null,
              actual_check_in_at: null,
              actual_check_out_at: null,
            },
            stay: {
              building_name: "合成楼栋",
              floor_name: "四层",
              room_name: "4-202",
              duration_mode: "fixed_term",
              planned_check_in_at: "2026-10-10T18:00:00Z",
              planned_check_out_at: "2026-10-13T00:00:00Z",
              actual_check_in_at: "2026-10-12T00:00:00Z",
              actual_check_out_at: null,
            },
          },
        }],
      })
    }
    if (/revenue-share|payments/.test(url.pathname)) control.financialRequests += 1
    return route.fulfill({
      status: 404,
      contentType: "application/json",
      body: "{}",
    })
  })
  return control
}
async function simulateFocus(page: Page) {
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" }); document.dispatchEvent(new Event("visibilitychange", { bubbles: true })) })
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" }); document.dispatchEvent(new Event("visibilitychange", { bubbles: true })) })
}
test("IH-SYNTH desktop/mobile date, focus, periodic authorization and late source isolation", async ({ page }) => {
  const control = await syntheticApi(page)
  await page.clock.install()
  await page.goto("/me/invitations")
  await expect(page.getByText("合成受邀甲", { exact: true })).toBeVisible()
  await expect(page.getByText("2026-10-11", { exact: true })).toBeVisible()
  await expect(page.getByText("2026-10-13", { exact: true })).toBeVisible()
  await expect(page.getByText("实际入住", { exact: true }).first()).toBeVisible()
  await expect(page.getByText("下一次计划", { exact: true })).toBeVisible()
  await expect(page.getByText("长期入住", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectAboveMobileNavigation(page, ".invitationPage nav")
  await page.screenshot({ path: resolve(process.env.INVITED_HOUSING_E2E_ARTIFACT_DIR ?? "../../.superpowers/sdd/2026-10-10-invited-housing-visibility/task-4-artifacts", `synthetic-${page.viewportSize()!.width}.png`), fullPage: true })
  expect(await page.getByRole("button", { name: /调房|退房|安排入住|导出/ }).count()).toBe(0)
  control.late = true
  await page.getByLabel("邀请来源").selectOption(orgB)
  await expect.poll(() => control.latePending).toBe(true)
  await page.getByLabel("邀请来源").selectOption(orgA)
  await expect(page.getByText("合成受邀甲", { exact: true })).toBeVisible()
  control.releaseLate()
  await expect(page.getByText("合成受邀乙", { exact: true })).toHaveCount(0)
  control.hold = true
  await simulateFocus(page)
  await expect.poll(() => control.pending).toBe(true)
  await expect(page.getByText("合成受邀甲", { exact: true })).toHaveCount(0)
  await expect(page.getByLabel("邀请来源")).toHaveCount(0)
  control.revoked = true; control.hold = false; control.release()
  await expect(page.getByText("合成受邀甲", { exact: true })).toBeVisible()
  await expect(page.getByRole("option", { name: "合成组织乙" })).toHaveCount(0)
  control.hold = true; control.pending = false
  await page.clock.fastForward(30000)
  await expect.poll(() => control.pending).toBe(true)
  await expect(page.getByText("合成受邀甲", { exact: true })).toHaveCount(0)
  control.hold = false; control.release()
  await expect(page.getByText("合成受邀甲", { exact: true })).toBeVisible()
  expect(control.financialRequests).toBe(0)
})

test("IH-REAL grant, read, revoke, old-session private 404 and UI clearing", async ({ page }) => {
  test.skip(process.env.INVITED_HOUSING_REAL_E2E !== "1", "Requires explicitly seeded disposable database")
  const width = page.viewportSize()!.width
  const index = width === 1440 ? 1 : 4
  const organizationId = `e2900000-0000-4000-8000-${String(index).padStart(12, "0")}`
  const visitorId = "e1100000-0000-4000-8000-000000000012"
  const admin = await createAdminRequest()
  let granted = false
  try {
    const before = await admin.admin.get(`/api/v1/revenue-share/organizations/${organizationId}/managers`)
    expect(await before.json()).toEqual([])
    const grant = await admin.admin.put(`/api/v1/revenue-share/organizations/${organizationId}/managers/${visitorId}`, { headers: { "X-CSRF-Token": admin.csrfToken } })
    expect(grant.status()).toBe(200); granted = true
    await loginWithIdentifier(page, ...e2eCredentialTuple("visitor"))
    await page.goto("/me/invitations")
    await expect(page.getByText(`E2E招生学员${width}`, { exact: true })).toBeVisible()
    await expect(page.getByText(width === 1440 ? "4-202" : "4-206", { exact: false })).toBeVisible()
    await expect(page.getByText("4-201", { exact: false })).toHaveCount(0)
    await expectNoHorizontalOverflow(page)
    await expectAboveMobileNavigation(page, ".invitationPage nav")
    const housing = await page.request.get(`/api/v1/referrals/me/housing?source_kind=organization&organization_id=${organizationId}`)
    expect(housing.status()).toBe(200); expect(housing.headers()["cache-control"]).toContain("no-store")
    const body = await housing.json() as { items: Record<string, unknown>[]; total: number }
    expect(body.total).toBe(1)
    expect(Object.keys(body.items[0]).sort()).toEqual(["full_name", "housing", "nickname", "person_id", "source"])
    const finance = await page.request.get("/api/v1/revenue-share/me")
    expect([403, 404]).toContain(finance.status())
    const revoke = await admin.admin.delete(`/api/v1/revenue-share/organizations/${organizationId}/managers/${visitorId}`, { headers: { "X-CSRF-Token": admin.csrfToken } })
    expect(revoke.status()).toBe(204); granted = false
    const denied = await page.request.get(`/api/v1/referrals/me/housing?source_kind=organization&organization_id=${organizationId}`)
    expect(denied.status()).toBe(404); expect(denied.headers()["cache-control"]).toContain("no-store")
    await refocus(page); await simulateFocus(page)
    await expect(page.getByText(`E2E招生学员${width}`, { exact: true })).toHaveCount(0)
    await expect(page.getByLabel("邀请来源")).toHaveCount(0)
  } finally {
    if (granted) await admin.admin.delete(`/api/v1/revenue-share/organizations/${organizationId}/managers/${visitorId}`, { headers: { "X-CSRF-Token": admin.csrfToken } })
    await admin.admin.dispose()
  }
})
