import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"

// Development-module and production-chunk failure; all business requests are intercepted.
test.use({ trace: "off", screenshot: "off", video: "off" })
const personId = "aaaaaaaa-1111-4111-8111-111111111111"
const reportModule = /\/(?:assets\/AssessmentReportsPanel-[^/?]+\.js|src\/esportsAssessments\/AssessmentReportsPanel\.tsx)(?:\?.*)?$/
const unexpected = new Map<Page, string[]>()
test.afterEach(async ({ page }) => {
  expect(unexpected.get(page) ?? [], "No API may escape the synthetic fixtures").toEqual([])
  unexpected.delete(page)
})

async function fixtures(page: Page, reports: boolean) {
  unexpected.set(page, [])
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname.replace("/api/v1", "")
    const json = (value: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) })
    if (path === "/auth/me") return json({ account_id: "11111111-2222-4333-8444-555555555555", person_id: "bbbbbbbb-1111-4111-8111-111111111111", full_name: "合成档案查看员", email: null, force_password_change: false, is_super_admin: false, permissions: ["people.read", "people.identity.reveal", ...(reports ? ["esports_assessments.read"] : [])], scoped_permissions: [] })
    if (path === "/auth/csrf") return json({ csrf_token: "synthetic-only" })
    if (path === "/portal/context") return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path === "/team-management/context") return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (path === "/personnel-intake/context") return json({ can_submit: false, can_review: false, can_direct_create: false, can_read_managed: false, can_direct_create_pending: false, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] })
    if (path === `/people/${personId}`) return json({ id: personId, full_name: "合成档案人员", nickname: "合成昵称", person_types: ["visitor"], identity_number: "110105********002X", phone: "138****8000", email: "synthetic@example.invalid", sex: "female", date_of_birth: "2000-01-02", created_at: "2026-10-25T00:00:00Z", age_status: "adult", guardian_status: "not_applicable", student_number: null, student_status: null, staff_type: null, visit_reason: "合成验收", linked_student_id: null })
    unexpected.get(page)?.push(`${route.request().method()} ${path}`)
    return json({ detail: "Unmocked synthetic request" }, 599)
  })
}

test("failed report chunk preserves personnel detail and an unsaved dialog", async ({ page, baseURL }) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1")
  await fixtures(page, true)
  let release!: () => void
  const gate = new Promise<void>(resolve => { release = resolve })
  let chunks = 0
  await page.route(reportModule, async route => {
    chunks++
    await gate
    await route.abort("failed")
  })
  try {
    await page.goto(`/people/${personId}`, { waitUntil: "domcontentloaded" })
    await expect(page.getByRole("heading", { name: "合成档案人员", exact: true })).toBeVisible()
    await expect.poll(() => chunks).toBe(1)
    await page.getByRole("button", { name: "查看完整证件号码" }).click()
    await page.getByLabel("查看用途").fill("合成未提交草稿")
    release()
    await expect(page.getByText(/报告.*暂时无法加载/)).toBeVisible()
    await expect(page.getByRole("heading", { name: "合成档案人员", exact: true })).toBeVisible()
    await expect(page.getByLabel("查看用途")).toHaveValue("合成未提交草稿")
    await expect(page.getByText("Unexpected Application Error!")).toHaveCount(0)
    await expect(page.getByText(/TypeError: Failed to fetch/)).toHaveCount(0)
    expect(new URL(page.url()).pathname).toBe(`/people/${personId}`)
  } finally { release() }
})

test("personnel viewer without report permission never downloads the report chunk", async ({ page, baseURL }) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1")
  await fixtures(page, false)
  const chunks: string[] = []
  page.on("request", request => { if (reportModule.test(request.url())) chunks.push(request.url()) })
  await page.goto(`/people/${personId}`)
  await expect(page.getByRole("heading", { name: "合成档案人员", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "查看完整证件号码" }).click()
  await expect(page.getByLabel("查看用途")).toBeVisible()
  await expect(page.getByText(/报告.*暂时无法加载/)).toHaveCount(0)
  expect(chunks).toEqual([])
})
