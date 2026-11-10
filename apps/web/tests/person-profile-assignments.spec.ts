import { expect, test } from "@playwright/test"
import { installUiThemeFixtures } from "./helpers/uiThemeFixtures"

// Every business request stays inside these synthetic fixtures. No real people.
test.use({ trace: "off", screenshot: "off", video: "off" })
test("personnel profile saves project/coach separately from team and keeps mobile controls usable", async ({ page, baseURL }) => {
  expect(new URL(baseURL!).hostname).toBe("127.0.0.1")
  const personId = "aaaaaaaa-1111-4111-8111-111111111111"
  const projectId = "cccccccc-1111-4111-8111-111111111111"
  const coachId = "dddddddd-1111-4111-8111-111111111111"
  const teamId = "eeeeeeee-1111-4111-8111-111111111111"
  let assigned = false
  let joined = false
  const writes: Array<{ path: string; body: Record<string, unknown> }> = []
  const unexpected: string[] = []
  const person = () => ({ id: personId, full_name: "合成待分配人员", nickname: null, person_types: assigned ? ["staff", "student"] : ["staff"], identity_number: "********", phone: null, email: null, sex: "male", date_of_birth: "2000-01-02", created_at: "2026-10-25T00:00:00Z", age_status: "adult", guardian_status: "not_applicable", student_number: assigned ? "SYNTHETIC-ONLY" : null, student_status: assigned ? "draft" : null, staff_type: "other", assisted_kind: null, account_status: "not_activated", visit_reason: null, linked_student_id: null, team: { visibility: "allowed", current: joined ? { membership_id: "ffffffff-1111-4111-8111-111111111111", project_id: projectId, project_name: "合成分部", team_id: teamId, team_name: "合成青训队", joined_on: "2026-10-25" } : null } })
  const assignments = () => ({ person_id: personId, version: assigned ? "v2" : "v1", business_kind: assigned ? "both" : null, source_intake_kind: null, student_project_ids: assigned ? [projectId] : [], coach_person_ids: assigned ? [coachId] : [], capabilities: { can_edit: true, can_manage_account_roles: false, can_remove_identity: false }, eligible_projects: [{ id: projectId, name: "合成项目" }], eligible_coaches: [{ id: coachId, full_name: "合成教练", project_ids: [projectId] }], current_projects: [], current_coaches: [], account_id: null, account_status: "not_activated" })
  await page.route("**/api/v1/**", async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname.replace("/api/v1", "")
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })
    if (request.method() !== "GET") writes.push({ path, body: request.postDataJSON() as Record<string, unknown> })
    if (path === "/appearance") return json({ theme: "clear", revision: 1 })
    if (path === "/auth/me") return json({ account_id: "11111111-2222-4333-8444-555555555555", person_id: "bbbbbbbb-1111-4111-8111-111111111111", full_name: "合成管理员", email: null, force_password_change: false, is_super_admin: false, permissions: ["people.read", "people.update"], scoped_permissions: [] })
    if (path === "/auth/csrf") return json({ csrf_token: "synthetic-only" })
    if (path === "/portal/context") return json({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, invitations_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null })
    if (path === "/team-management/context") return json({ can_open_management: true, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
    if (path === "/personnel-intake/context") return json({ can_submit: false, can_review: false, can_direct_create: false, can_read_managed: false, can_direct_create_pending: false, allowed_team_ids: [], eligible_coaches: [], eligible_teams: [] })
    if (path === `/people/${personId}`) return json(person())
    if (path === `/people/${personId}/staff-profile`) return json({ person_id: personId, nickname: null, profile_notes: null })
    if (path === `/people/${personId}/assignments`) {
      if (request.method() === "PUT") {
        expect(request.postDataJSON()).toMatchObject({ business_kind: "both", student_project_ids: [projectId], coach_person_ids: [coachId], expected_version: "v1", reason: "合成归属验证" })
        expect(request.postDataJSON().idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
        assigned = true
      }
      return json(assignments())
    }
    if (path === `/people/${personId}/guardians`) return json([])
    if (path === `/people/${personId}/readiness`) return json({ age_status: "adult", guardian_status: "not_applicable", ready: true, missing: [], guardian_issues: [] })
    if (path === "/team-management/teams") return json({ items: [{ id: teamId, project_id: projectId, project_name: "合成分部", name: "合成青训队", is_active: true, member_count: joined ? 1 : 0, allowed_actions: ["read", "manage"] }], page: 1, page_size: 100, total: 1 })
    if (path === `/team-management/teams/${teamId}/memberships` && request.method() === "POST") {
      expect(request.postDataJSON().person_id).toBe(personId)
      joined = true
      return json({ operation_id: "synthetic-operation", membership_id: "ffffffff-1111-4111-8111-111111111111", closed_membership_id: null, assignment_id: null })
    }
    unexpected.push(`${request.method()} ${path}`)
    return json({ detail: "Unexpected synthetic request" }, 599)
  })
  await page.goto(`/people/${personId}`)
  await expect(page.getByRole("heading", { name: "合成待分配人员", exact: true })).toBeVisible()
  await page.getByRole("button", { name: "身份与归属", exact: true }).click()
  await page.getByLabel("学员身份", { exact: true }).check()
  await page.getByLabel("队员身份", { exact: true }).check()
  await page.getByLabel("合成项目", { exact: true }).check()
  await page.getByLabel("合成教练", { exact: true }).check()
  await page.getByLabel("身份与归属变更原因").fill("合成归属验证")
  await page.getByRole("button", { name: "保存身份与归属", exact: true }).click()
  await expect(page.getByText("身份与归属已保存", { exact: true })).toBeVisible()
  expect(writes).toHaveLength(1)
  await page.getByRole("combobox", { name: "分部", exact: true }).selectOption(projectId)
  await page.getByRole("combobox", { name: "队伍", exact: true }).selectOption(teamId)
  await page.getByRole("button", { name: "加入队伍", exact: true }).click()
  await expect(page.getByRole("region", { name: "队伍归属", exact: true }).getByText("合成分部 · 合成青训队", { exact: true })).toBeVisible()
  expect(writes).toHaveLength(2)
  expect(unexpected).toEqual([])
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
  const alignment = await page.locator('[aria-label="身份与归属"] label:has(input[type="checkbox"])').evaluateAll(labels => labels.map(label => {
    const input = label.querySelector("input")!.getBoundingClientRect()
    const text = [...label.childNodes].find(node => node.nodeType === Node.TEXT_NODE && node.textContent?.trim())!
    const range = document.createRange()
    range.selectNode(text)
    const rect = range.getBoundingClientRect()
    return Math.abs(input.top + input.height / 2 - rect.top - rect.height / 2)
  }))
  expect(alignment.length).toBeGreaterThan(0)
  expect(Math.max(...alignment), "Checkboxes and their names must remain on the same line").toBeLessThan(6)
  await page.screenshot({ path: test.info().outputPath("synthetic-assignments.png"), fullPage: true })
})

for (const theme of ["clear", "command"] as const) {
  for (const keepConcurrent of [true, false]) {
    test(`${theme}: personnel job change retains concurrent roles=${keepConcurrent} with keyboard and responsive confirmation`, async ({ page, baseURL }) => {
      expect(new URL(baseURL!).hostname).toBe("127.0.0.1")
      const fixture = await installUiThemeFixtures(page, theme)
      const personId = "aaaaaaaa-1111-4111-8111-111111111111"
      const accountId = "bbbbbbbb-1111-4111-8111-111111111111"
      const coach = { id: "10000000-1111-4111-8111-111111111111", key: "coach", name: "合成教练" }
      const finance = { id: "20000000-1111-4111-8111-111111111111", key: "finance", name: "合成财务" }
      const service = { id: "30000000-1111-4111-8111-111111111111", key: "customer_service", name: "合成客服" }
      const student = { id: "40000000-1111-4111-8111-111111111111", key: "student", name: "合成学员" }
      let assigned = [coach, finance, student]
      const writes: unknown[] = []
      await page.route("**/api/v1/**", async route => {
        const request = route.request()
        const path = new URL(request.url()).pathname.replace("/api/v1", "")
        const json = (value: unknown) => route.fulfill({ status: 200, json: value })
        if (path === "/auth/me") return json({ account_id: "11111111-2222-4333-8444-555555555555", person_id: "cccccccc-1111-4111-8111-111111111111", full_name: "合成权限管理员", email: null, force_password_change: false, is_super_admin: false, permissions: ["people.read", "permissions.manage"], scoped_permissions: [] })
        if (path === "/team-management/context") return json({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
        if (path === `/people/${personId}`) return json({ id: personId, full_name: "合成岗位人员", nickname: null, person_types: ["staff", "student"], identity_number: "********", phone: null, email: null, sex: "male", date_of_birth: "2000-01-02", created_at: "2026-10-28T00:00:00Z", age_status: "adult", guardian_status: "not_applicable", staff_type: assigned.some(role => role.key === "coach") ? "coach" : "customer_service", assisted_kind: null, account_status: "active", team: { visibility: "allowed", current: null } })
        if (path === `/people/${personId}/staff-profile`) return json({ person_id: personId, nickname: null, profile_notes: null })
        if (path === `/people/${personId}/assignments`) return json({ person_id: personId, version: "synthetic-v1", business_kind: "student", source_intake_kind: null, student_project_ids: [], coach_person_ids: [], capabilities: { can_edit: false, can_manage_account_roles: true, can_remove_identity: false }, eligible_projects: [], eligible_coaches: [], current_projects: [], current_coaches: [], account_id: accountId, account_status: "active" })
        if (path === `/people/${personId}/guardians`) return json([])
        if (path === `/people/${personId}/readiness`) return json({ age_status: "adult", guardian_status: "not_applicable", ready: true, missing: [], guardian_issues: [] })
        if (path === "/roles") return json([coach, finance, service, student])
        if (path === `/accounts/${accountId}/access`) return json({ account_id: accountId, roles: assigned, permission_overrides: [], effective_permissions: [] })
        if (path === `/accounts/${accountId}/job-role-change` && request.method() === "POST") {
          writes.push(request.postDataJSON())
          expect(request.postDataJSON()).toEqual({ from_role_id: coach.id, to_role_id: service.id, keep_concurrent_roles: keepConcurrent, expected_role_ids: [coach.id, finance.id, student.id] })
          assigned = keepConcurrent ? [finance, student, service] : [student, service]
          return json({ account_id: accountId, added_role_ids: [service.id], removed_role_ids: keepConcurrent ? [coach.id] : [coach.id, finance.id], retained_role_ids: keepConcurrent ? [finance.id, student.id] : [student.id], roles: assigned })
        }
        return route.fallback()
      })
      await page.goto(`/people/${personId}`)
      await expect(page.locator("html")).toHaveAttribute("data-ui-theme", theme)
      await expect(page.getByRole("heading", { name: "合成岗位人员", exact: true })).toBeVisible()
      await page.getByRole("button", { name: "身份与归属", exact: true }).click()
      await page.getByRole("combobox", { name: "原岗位", exact: true }).selectOption(coach.id)
      await page.getByRole("combobox", { name: "新岗位", exact: true }).selectOption(service.id)
      await expect(page.getByRole("button", { name: "预览岗位变更", exact: true })).toBeDisabled()
      expect(writes).toEqual([])
      const radio = page.getByRole("radio", { name: keepConcurrent ? "是，保留其他兼任岗位" : "否，撤销其他工作人员岗位", exact: true })
      await radio.focus()
      await page.keyboard.press("Space")
      await expect(radio).toBeChecked()
      const preview = page.getByRole("button", { name: "预览岗位变更", exact: true })
      await preview.focus()
      await page.keyboard.press("Enter")
      const dialog = page.getByRole("dialog", { name: "确认岗位变更", exact: true })
      await expect(dialog).toContainText(keepConcurrent ? "将撤销：合成教练" : "将撤销：合成教练、合成财务")
      await expect(dialog).toContainText(keepConcurrent ? "将保留：合成财务、合成学员" : "将保留：合成学员")
      await expect(dialog).toContainText("单独 allow/deny 授权不变")
      expect(writes).toEqual([])
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true)
      const confirm = dialog.getByRole("button", { name: "确认岗位变更", exact: true })
      await confirm.focus()
      await page.keyboard.press("Enter")
      await expect(page.getByRole("status").filter({ hasText: "岗位已更新" })).toBeVisible()
      await expect(page.getByRole("button", { name: "移除 合成学员", exact: true })).toBeVisible()
      await expect(page.getByRole("button", { name: "移除 合成财务", exact: true })).toHaveCount(keepConcurrent ? 1 : 0)
      expect(writes).toHaveLength(1)
      expect(fixture.unexpected).toEqual([])
      expect(fixture.mutations).toEqual([])
      await page.screenshot({ path: test.info().outputPath(`synthetic-job-${theme}-${keepConcurrent}.png`), fullPage: true })
    })
  }
}
