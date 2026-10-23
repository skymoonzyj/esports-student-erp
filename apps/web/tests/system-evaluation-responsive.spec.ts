import { expect, test, type Locator, type Page } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
} from "./helpers"
import type { PortalContext } from "../src/portal/api"
import type { TeamContext } from "../src/teams/api"

const accountId = "10000000-0000-4000-8000-000000000001"
const taskId = "10000000-0000-4000-8000-000000000011"
const reviewTaskId = "10000000-0000-4000-8000-000000000012"
const studentId = "40000000-0000-4000-8000-000000000001"
const projectId = "70000000-0000-4000-8000-000000000001"
const resultId = "20000000-0000-4000-8000-000000000001"
const foodAccessRevision = "a".repeat(64)

const actions = {
  match: false,
  save_manual_draft: true,
  submit_review: false,
  create_correction: false,
  create_revision: false,
  review: false,
  archive: true,
  reopen: false,
  view_preview: false,
}

const taskItem = {
  id: taskId,
  public_number: "EV-E2E-RESPONSIVE-001",
  status: "manual_input",
  lock_version: 3,
  created_at: "2026-08-08T10:00:00Z",
  student_person_id: "40000000-0000-4000-8000-000000000001",
  enrollment_id: "50000000-0000-4000-8000-000000000001",
  cohort_id: "60000000-0000-4000-8000-000000000001",
  project_id: "70000000-0000-4000-8000-000000000001",
  class_id: "80000000-0000-4000-8000-000000000001",
  identity_summary: "学* · **0001",
  cohort_name: "2026 夏季营",
  project_name: "英雄联盟",
  class_name: "菁英班",
  media_count: 1,
  current_result: null,
  published_result: null,
  result_versions: [],
  allowed_actions: actions,
  allowed_reopen_modes: [],
}

const reviewResult = {
  id: resultId,
  version: 1,
  source: "automatic",
  fields: { kills: 12 },
  confidences: { kills: 0.98 },
  warnings: [{ key: "image_edge", severity: "warning", summary: "画面边缘略有遮挡" }],
  components: [{ key: "combat", score: 88, weight: 1, summary: "发挥稳定" }],
  missing_fields: [],
  score: 88,
  grade: "A",
  advice: "继续保持训练节奏",
  adapter_id: "lol",
  adapter_version: "1.0.0",
  scoring_rule_version: "rule-1",
  result_schema_version: "schema-1",
  review_decision: "pending",
  reviewed_at: null,
  rejection_reason: null,
  published_at: null,
  previous_version_id: null,
}

const reviewTaskItem = {
  ...taskItem,
  id: reviewTaskId,
  public_number: "EV-E2E-RESPONSIVE-REVIEW",
  status: "needs_review",
  current_result: reviewResult,
  result_versions: [reviewResult],
  allowed_actions: {
    ...actions,
    save_manual_draft: false,
    review: true,
    archive: false,
  },
}

const publishedResult = {
  ...reviewResult,
  review_decision: "approved",
  reviewed_at: "2026-08-08T12:00:00Z",
  published_at: "2026-08-08T12:00:00Z",
}

const publishedTaskItem = {
  ...reviewTaskItem,
  status: "published",
  current_result: publishedResult,
  published_result: publishedResult,
  result_versions: [publishedResult],
  allowed_actions: {
    ...reviewTaskItem.allowed_actions,
    review: false,
  },
}

const portalContext = {
  coach_available: false,
  learning_available: true,
  payments_available: false,
  housing_management_available: false,
  housing_available: false,
  adult_guardian_invitations_available: false,
  food_profile_available: false,
  food_preferences_onboarding_required: false,
  learning_mode: "family",
} satisfies PortalContext

const teamContext = {
  can_open_management: false,
  can_read_self: false,
  can_manage_catalog: false,
  can_assign_managers: false,
  can_transfer: false,
} satisfies TeamContext

async function installApi(page: Page): Promise<void> {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url())
    const path = url.pathname
    const json = (body: unknown, status = 200) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) })

    if (path === "/api/v1/auth/me") {
      await json({
        account_id: accountId,
        person_id: "20000000-0000-4000-8000-000000000001",
        full_name: "响应式评价管理员",
        email: "responsive-evaluation@example.test",
        force_password_change: false,
        permissions: ["evaluation_center.access"],
        scoped_permissions: [],
      })
      return
    }
    if (path === "/api/v1/auth/csrf") {
      await json({ csrf_token: "responsive-evaluation-csrf" })
      return
    }
    if (path === "/api/v1/portal/context") {
      await json(portalContext)
      return
    }
    if (path === "/api/v1/team-management/context" && route.request().method() === "GET") {
      await json(teamContext)
      return
    }
    if (path === "/api/v1/evaluations/context") {
      await json({
        accessible: true,
        system_evaluation_available: true,
        embedded_start_url: "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
        standalone_start_url:
          "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
        upload_available: false,
        video_available: false,
        reports_available: false,
        surveys_available: false,
        survey_sections: {
          respondent: false,
          staff_feedback: false,
          administration: false,
        },
        game_accounts_available: false,
        admin_available: true,
        admin_sections: {
          cohort_staff: false,
          student_matching: false,
          analysis_profiles: false,
          system_tasks: true,
          adapter_certifications: true,
        },
        integration_api_version: "v1",
      })
      return
    }
    if (path === "/api/v1/family/students") {
      await json({
        items: [
          {
            student_person_id: studentId,
            student_name: "响应式学员",
            student_number: "S-E2E-01",
            age_status: "adult",
            access_source: "family",
            current_enrollment: {
              enrollment_id: "50000000-0000-4000-8000-000000000001",
              cohort_id: "60000000-0000-4000-8000-000000000001",
              cohort_name: "2026 夏季营",
              starts_at: "2026-08-01T09:00:00+08:00",
              ends_at: "2026-08-21T18:00:00+08:00",
              project_name: "英雄联盟",
              class_id: "80000000-0000-4000-8000-000000000001",
              class_name: "菁英班",
              class_type: "elite",
              professional_team_name: "星河职业队",
              status: "active",
              confirmed_at: "2026-07-01T09:00:00+08:00",
              cancelled_at: null,
              completed_at: null,
            },
          },
        ],
        total: 1,
      })
      return
    }
    if (path === `/api/v1/family/students/${studentId}/teaching`) {
      const enrollment = {
        enrollment_id: "50000000-0000-4000-8000-000000000001",
        cohort_id: "60000000-0000-4000-8000-000000000001",
        cohort_name: "2026 夏季营",
        starts_at: "2026-08-01T09:00:00+08:00",
        ends_at: "2026-08-21T18:00:00+08:00",
        project_name: "英雄联盟",
        class_id: "80000000-0000-4000-8000-000000000001",
        class_name: "菁英班",
        class_type: "elite",
        professional_team_name: "星河职业队",
        status: "active",
        confirmed_at: "2026-07-01T09:00:00+08:00",
        cancelled_at: null,
        completed_at: null,
      }
      await json({
        student: {
          student_person_id: studentId,
          student_name: "响应式学员",
          student_number: "S-E2E-01",
          age_status: "adult",
          access_source: "family",
          current_enrollment: enrollment,
        },
        items: [enrollment],
        total: 1,
        page: 1,
        page_size: 20,
      })
      return
    }
    if (path === "/api/v1/food-preferences/cuisines") {
      await json({
        items: [],
        allowed_actions: [],
        access_revision: foodAccessRevision,
      })
      return
    }
    if (path === `/api/v1/people/${studentId}/food-preferences`) {
      await json({
        person_id: studentId,
        version: 0,
        cuisines: [],
        dishes: [],
        updated_at: null,
        allowed_actions: [],
        access_revision: foodAccessRevision,
      })
      return
    }
    if (path === `/api/v1/evaluations/students/${studentId}/published-reports`) {
      await json({ items: [], total: 0, page: 1, page_size: 10 })
      return
    }
    if (path === "/api/v1/evaluations/system/task-filter-options") {
      await json({
        api_version: "v2",
        cohorts: [{ id: taskItem.cohort_id, label: taskItem.cohort_name }],
        projects: [{ id: taskItem.project_id, label: taskItem.project_name }],
        classes: [{ id: taskItem.class_id, label: taskItem.class_name }],
        students: [{ id: taskItem.student_person_id, label: taskItem.identity_summary }],
      })
      return
    }
    if (path === "/api/v1/evaluations/system/tasks") {
      if (url.searchParams.get("published_only") === "true") {
        await json({
          api_version: "v2",
          items: [publishedTaskItem],
          total: 11,
          page: Number(url.searchParams.get("page") ?? "1"),
          page_size: 10,
        })
      } else {
        await json({ api_version: "v2", items: [taskItem], total: 1, page: 1, page_size: 20 })
      }
      return
    }
    if (
      path === `/api/v1/evaluations/system/tasks/${taskId}` ||
      path === `/api/v1/evaluations/system/tasks/${reviewTaskId}`
    ) {
      await json({
        api_version: "v2",
        task: path.includes(reviewTaskId) ? reviewTaskItem : taskItem,
      })
      return
    }
    if (
      path === `/api/v1/evaluations/system/tasks/${taskId}/workspace` ||
      path === `/api/v1/evaluations/system/tasks/${reviewTaskId}/workspace`
    ) {
      await json({
        api_version: "v2",
        task_id: path.includes(reviewTaskId) ? reviewTaskId : taskId,
        task_version: 3,
        profile: {
          mode: "manual_only",
          adapter_id: null,
          adapter_version: null,
          scoring_rule_version: null,
          result_schema_version: "1",
          required_fields: ["kills", "assists"],
          media_slots: [],
        },
        media: [],
        draft: null,
      })
      return
    }
    if (
      path === `/api/v1/evaluations/system/tasks/${reviewTaskId}/review` &&
      route.request().method() === "POST"
    ) {
      await json({
        api_version: "v2",
        task_id: reviewTaskId,
        result_id: resultId,
        result_version: 1,
        task_status: "published",
        task_version: 4,
        review_decision: "approved",
        reviewed_at: "2026-08-08T12:00:00Z",
        published_at: "2026-08-08T12:00:00Z",
      })
      return
    }
    if (path === "/api/v1/evaluations/admin/adapter-certification-projects") {
      await json({
        items: [{ id: projectId, name: "英雄联盟", is_active: true }],
        total: 1,
        page: 1,
        page_size: 100,
      })
      return
    }
    if (path === "/api/v1/evaluations/admin/system-task-filter-options") {
      await json({
        api_version: "v2",
        cohorts: [],
        projects: [],
        classes: [],
        students: [],
      })
      return
    }
    if (path === "/api/v1/evaluations/admin/system-tasks") {
      await json({
        api_version: "v2",
        items: [],
        total: 0,
        page: 1,
        page_size: 20,
      })
      return
    }
    if (path === "/api/v1/evaluations/admin/adapter-certifications") {
      await json({
        api_version: "v2",
        items: [
          {
            id: "90000000-0000-4000-8000-000000000001",
            project_id: projectId,
            adapter_id: "lol",
            adapter_version: "1.0.0",
            scoring_rule_version: "rule-1",
            result_schema_version: "schema-1",
            manifest_digest: "a".repeat(64),
            certified_by_user_id: "91000000-0000-4000-8000-000000000001",
            certified_at: "2026-08-08T12:00:00Z",
          },
        ],
      })
      return
    }
    throw new Error(`Unexpected system-evaluation request: ${route.request().method()} ${path}`)
  })
}

async function expectKeyActionsAtLeast44(page: Page, names: string[]): Promise<void> {
  for (const name of names) {
    const control = page.getByRole("button", { name })
    await expect(control).toBeVisible()
    const box = await control.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
}

async function expectInteractiveTargetsAtLeast44(scope: Locator): Promise<void> {
  const controls = scope.locator(
    'a[href], button, input:not([type="hidden"]):not([type="radio"]):not([type="checkbox"]), select, textarea',
  )
  for (let index = 0; index < (await controls.count()); index += 1) {
    const control = controls.nth(index)
    if (!(await control.isVisible())) continue
    const box = await control.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
}

async function expectReviewDecisionTargetsAtLeast44(scope: Locator): Promise<void> {
  for (const name of ["通过", "驳回"]) {
    const input = scope.getByRole("radio", { name })
    const label = input.locator("xpath=ancestor::label[1]")
    const box = await label.boundingBox()
    expect(box?.width ?? 0).toBeGreaterThanOrEqual(44)
    expect(box?.height ?? 0).toBeGreaterThanOrEqual(44)
  }
}

test.beforeEach(async ({ page }) => {
  await installApi(page)
})

test("task list switches once between desktop table and compact cards", async ({ page }) => {
  await page.goto("/evaluations/system/tasks")
  await expect(page.getByRole("heading", { name: "系统评价任务" })).toBeVisible()

  const width = page.viewportSize()?.width ?? 1440
  if (width <= 768) {
    await expect(page.getByRole("region", { name: "系统评价任务卡片" })).toBeVisible()
    await expect(page.getByRole("table", { name: "系统评价任务" })).toHaveCount(0)
  } else {
    await expect(page.getByRole("table", { name: "系统评价任务" })).toBeVisible()
    await expect(page.getByRole("region", { name: "系统评价任务卡片" })).toHaveCount(0)
  }

  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".systemEvaluationTasksPage")
  await expectKeyActionsAtLeast44(page, ["刷新", "上一页", "下一页"])
})

test("manual task dialog stays usable and inside every viewport", async ({ page }) => {
  await page.goto(`/evaluations/system/tasks/${taskId}`)
  await expect(page.getByRole("heading", { name: "EV-E2E-RESPONSIVE-001" })).toBeVisible()
  await expectKeyActionsAtLeast44(page, ["人工录入", "归档任务"])
  await page.getByRole("button", { name: "人工录入" }).click()

  const dialog = page.getByRole("dialog", { name: "人工录入系统评价" })
  await expect(dialog).toBeVisible()
  await expectNoHorizontalOverflow(page)
  await expectNoInternalHorizontalOverflow(dialog)
  await expectHorizontallyInsideViewport(page, ".systemEvaluationDialog")
  await expectKeyActionsAtLeast44(page, ["取消", "保存草稿"])

  const viewport = page.viewportSize()
  const box = await dialog.boundingBox()
  if (!viewport || !box) throw new Error("Responsive dialog requires a viewport box")
  if (viewport.width <= 768) {
    expect(Math.round(box.width)).toBe(viewport.width)
    expect(Math.round(box.height)).toBe(viewport.height)
  } else {
    expect(box.width).toBeLessThanOrEqual(720)
    expect(box.height).toBeLessThanOrEqual(viewport.height)
  }
})

test("review stays inline on desktop and becomes a full-screen read-only mobile flow", async ({ page }) => {
  await page.goto(`/evaluations/system/tasks/${reviewTaskId}`)
  await expect(page.getByRole("heading", { name: "EV-E2E-RESPONSIVE-REVIEW" })).toBeVisible()

  const width = page.viewportSize()?.width ?? 1440
  if (width > 768) {
    const review = page.getByRole("region", { name: "审核评价" })
    await expect(review.getByRole("radio", { name: "通过" })).toBeVisible()
    await expect(page.getByRole("button", { name: "打开全屏审核" })).toHaveCount(0)
    await expectInteractiveTargetsAtLeast44(review)
    await expectReviewDecisionTargetsAtLeast44(review)
  } else {
    const opener = page.getByRole("button", { name: "打开全屏审核" })
    await expect(opener).toBeVisible()
    await expectKeyActionsAtLeast44(page, ["打开全屏审核"])
    await opener.click()
    const dialog = page.getByRole("dialog", { name: "审核评价" })
    await expect(dialog).toContainText("kills")
    await expect(dialog).toContainText("98%")
    await expect(dialog).toContainText("画面边缘略有遮挡")
    await expect(dialog).toContainText("发挥稳定")
    await expect(dialog).toContainText("继续保持训练节奏")
    await expectNoInternalHorizontalOverflow(dialog)
    await expectAboveMobileNavigation(page, ".systemEvaluationDialog")
    const viewport = page.viewportSize()
    const box = await dialog.boundingBox()
    if (!viewport || !box) throw new Error("Mobile review requires a viewport box")
    expect(Math.round(box.width)).toBe(viewport.width)
    expect(Math.round(box.height)).toBe(viewport.height)
    await expectInteractiveTargetsAtLeast44(dialog)
    await expectReviewDecisionTargetsAtLeast44(dialog)
    await page.keyboard.press("Escape")
    await expect(dialog).toHaveCount(0)
    await expect(opener).toBeFocused()
  }

  await expectNoHorizontalOverflow(page)
})

test("published history and adapter certification controls keep 44px touch targets", async ({ page }) => {
  await page.goto(`/learning/${studentId}`)
  const history = page.getByRole("region", { name: "系统评价历史" })
  await expect(history).toBeVisible()
  await expect(history.getByRole("link", { name: "查看完整评价" })).toBeVisible()
  await expect(history.getByRole("button", { name: "下一页" })).toBeVisible()
  await expectInteractiveTargetsAtLeast44(history)

  await page.goto("/evaluations/admin")
  await page.getByRole("tab", { name: "适配器认证" }).click()
  const certifications = page.getByRole("region", { name: "适配器认证" })
  await expect(certifications).toBeVisible()
  await expectInteractiveTargetsAtLeast44(certifications)
  const certificationTab = page.getByRole("tab", { name: "适配器认证" })
  const tabBox = await certificationTab.boundingBox()
  expect(tabBox?.height ?? 0).toBeGreaterThanOrEqual(44)
  await expectNoHorizontalOverflow(page)
})
