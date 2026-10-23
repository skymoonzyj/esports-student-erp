import { expect, test } from "@playwright/test"
import type {
  Browser,
  BrowserContext,
  FrameLocator,
  Page,
} from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
} from "./helpers"
import { e2eCredentialTuple } from "./e2eCredentials"

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5180"
const AUTOMATIC_PNG = process.env.E2E_SYSTEM_EVALUATION_AUTOMATIC_PNG
const MANUAL_PNG = process.env.E2E_SYSTEM_EVALUATION_MANUAL_PNG
const BOUND_STUDENT_ID = "e1000000-0000-4000-8000-000000000004"
const SELF_STUDENT_ID = "e1000000-0000-4000-8000-000000000007"

const credentials = {
  guardian: e2eCredentialTuple("guardian"),
  student: e2eCredentialTuple("student"),
  coach: e2eCredentialTuple("coach"),
  reviewer: e2eCredentialTuple("life_teacher"),
} as const

type Viewport = { width: number; height: number }
type TaskCreated = {
  id: string
  public_number: string
  status: string
  lock_version: number
}
type TaskDetail = {
  task: {
    id: string
    public_number: string
    status: string
    lock_version: number
    current_result: null | { score: number; grade: string; advice: string }
    published_result: null | { score: number; grade: string; advice: string }
  }
}

async function roleContext(
  browser: Browser,
  viewport: Viewport,
  [phone, password]: readonly [string, string],
): Promise<BrowserContext> {
  const bootstrap = await browser.newContext({ baseURL: BASE_URL, viewport })
  const page = await bootstrap.newPage()
  await page.goto("/login")
  await page.getByLabel("手机号").fill(phone)
  await page.getByLabel("密码", { exact: true }).fill(password)
  const loginResponse = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && new URL(response.url()).pathname === "/api/v1/auth/login",
  )
  await page.getByRole("button", { name: "登录" }).click()
  expect((await loginResponse).status()).toBe(200)
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  const storageState = await bootstrap.storageState()
  await bootstrap.close()
  return browser.newContext({ baseURL: BASE_URL, viewport, storageState })
}

async function expectFrameLayout(frame: FrameLocator): Promise<void> {
  const root = frame.locator("html")
  await expect(root).toBeVisible()
  await expect.poll(() => root.evaluate((element) => (
    element.scrollWidth <= element.clientWidth
  ))).toBe(true)
  const app = frame.locator("#system-evaluation-app")
  await expectNoInternalHorizontalOverflow(app)
}

async function openScoreEvaluation(page: Page): Promise<FrameLocator> {
  await page.goto("/evaluations/system")
  const frame = page.frameLocator('iframe[title="系统评价"]')
  await expect(frame.getByText("已连接竞学 ERP")).toBeVisible()
  const uploadLink = frame.getByRole("link", {
    name: "前往系统评价上传",
    exact: true,
  })
  await expect(uploadLink).toBeVisible()
  await uploadLink.click()
  await expect(
    frame.getByRole("heading", { name: "上传游戏截图，创建表现评价" }),
  ).toBeVisible()
  await expectFrameLayout(frame)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".systemEvaluationPage")
  await expectAboveMobileNavigation(page, ".evaluationFrameShell")
  return frame
}

async function createTaskFromScreenshot(
  page: Page,
  projectName: string,
  filePath: string,
): Promise<TaskCreated> {
  const frame = await openScoreEvaluation(page)
  const target = frame.getByLabel("学员、营期与项目")
  const option = target.locator("option").filter({ hasText: projectName })
  await expect(option).toHaveCount(1)
  const value = await option.getAttribute("value")
  if (value === null) throw new Error(`Missing score target for ${projectName}`)
  await target.selectOption(value)
  const screenshot = frame.getByLabel("合成比赛截图")
  await screenshot.setInputFiles(filePath)
  await expect(frame.getByAltText("合成比赛截图本地预览")).toBeVisible()

  const uploadSessionResponse = page.waitForResponse((response) => {
    const path = new URL(response.url()).pathname
    return response.request().method() === "POST"
      && path.endsWith("/api/evaluations/v2/upload-sessions")
  })
  const createdResponse = page.waitForResponse((response) => {
    const path = new URL(response.url()).pathname
    return response.request().method() === "POST"
      && /\/api\/evaluations\/v2\/upload-sessions\/[0-9a-f-]+\/tasks$/.test(path)
  })
  await frame.getByRole("button", { name: "上传截图并创建评价" }).click()
  const uploadSession = await uploadSessionResponse
  expect(uploadSession.status(), await uploadSession.text()).toBe(201)
  const response = await createdResponse
  expect(response.status()).toBe(201)
  const created = (await response.json()) as TaskCreated
  await expect(
    frame.locator(`.evaluation-history-card[data-task-id="${created.id}"]`),
  ).toBeVisible()
  return created
}

async function taskDetail(
  context: BrowserContext,
  taskId: string,
  projection: "account" | "admin" = "account",
): Promise<TaskDetail> {
  const prefix = projection === "admin"
    ? "/api/v1/evaluations/admin/system-tasks"
    : "/api/v1/evaluations/system/tasks"
  const response = await context.request.get(
    `${prefix}/${taskId}`,
  )
  expect(response.status()).toBe(200)
  return (await response.json()) as TaskDetail
}

async function waitForTaskStatus(
  context: BrowserContext,
  taskId: string,
  expected: string,
): Promise<TaskDetail> {
  let latest: TaskDetail | null = null
  await expect.poll(async () => {
    latest = await taskDetail(context, taskId)
    return latest.task.status
  }, { timeout: 60_000 }).toBe(expected)
  if (!latest) throw new Error(`Task ${taskId} did not load`)
  return latest
}

async function reviewTask(
  page: Page,
  taskId: string,
  decision: "approved" | "rejected",
  reason?: string,
): Promise<Record<string, unknown>> {
  await page.goto(`/evaluations/system/tasks/${taskId}`)
  await expect(page.getByRole("region", { name: "评价结果" })).toBeVisible()
  let scope = page.getByRole("region", { name: "审核评价" })
  if ((page.viewportSize()?.width ?? 1440) <= 768) {
    await scope.getByRole("button", { name: "打开全屏审核" }).click()
    scope = page.getByRole("dialog", { name: "审核评价" })
    await expect(scope).toBeVisible()
    await expectNoInternalHorizontalOverflow(scope)
    await expectAboveMobileNavigation(page, ".systemEvaluationDialogBackdrop")
  }
  const label = decision === "approved" ? "通过" : "驳回"
  await scope.getByRole("radio", { name: label }).check()
  if (decision === "rejected") {
    await scope.getByLabel("驳回原因").fill(reason ?? "E2E 需要整改")
  }
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && new URL(response.url()).pathname
      === `/api/v1/evaluations/admin/system-tasks/${taskId}/reviews`,
  )
  await scope.getByRole("button", { name: "提交审核" }).click()
  const response = await responsePromise
  expect(response.status()).toBe(200)
  await expectNoHorizontalOverflow(page)
  return (await response.json()) as Record<string, unknown>
}

async function saveManualDraft(
  page: Page,
  taskId: string,
  values: { score: string; grade: string; summary: string; advice: string },
): Promise<void> {
  await page.goto(`/evaluations/system/tasks/${taskId}`)
  await page.getByRole("button", { name: "人工录入" }).click()
  const dialog = page.getByRole("dialog", { name: "人工录入系统评价" })
  await expect(dialog).toBeVisible()
  await dialog.getByLabel("字段 kills").fill("12")
  await dialog.getByLabel("字段 deaths").fill("3")
  await dialog.getByLabel("字段 assists").fill("9")
  await dialog.getByLabel("总分").fill(values.score)
  await dialog.getByLabel("等级").fill(values.grade)
  await dialog.getByLabel("分项说明").fill(values.summary)
  await dialog.getByLabel("训练建议").fill(values.advice)
  const savedResponse = page.waitForResponse((response) =>
    response.request().method() === "PUT"
    && new URL(response.url()).pathname.endsWith(`/${taskId}/manual-draft`),
  )
  await dialog.getByRole("button", { name: "保存草稿" }).click()
  expect((await savedResponse).status()).toBe(200)
  await expect(dialog).toHaveCount(0)

  await expect(page.getByRole("button", { name: "提交审核" })).toBeVisible()
  await page.getByRole("button", { name: "提交审核" }).click()
  const submitDialog = page.getByRole("dialog", { name: "提交人工评价审核" })
  await expect(submitDialog).toBeVisible()
  const submittedResponse = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith(`/${taskId}/submit-review`),
  )
  await submitDialog.getByRole("button", { name: "确认提交审核" }).click()
  expect((await submittedResponse).status()).toBe(201)
  await expect(submitDialog).toHaveCount(0)
}

async function startCorrection(page: Page, taskId: string): Promise<void> {
  await page.goto(`/evaluations/system/tasks/${taskId}`)
  await page.getByRole("button", { name: "开始整改" }).click()
  const dialog = page.getByRole("dialog", { name: "开始评价整改" })
  await dialog.getByRole("radio", { name: "人工处理" }).check()
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && new URL(response.url()).pathname.endsWith(`/${taskId}/corrections`),
  )
  await dialog.getByRole("button", { name: "确认开始" }).click()
  expect((await responsePromise).status()).toBe(201)
  await expect(dialog).toHaveCount(0)
}

async function expectPublishedHistory(
  page: Page,
  studentId: string,
  project: string,
  score: number,
  advice: string,
): Promise<void> {
  await page.goto(`/learning/${studentId}`)
  const history = page.getByRole("region", { name: "系统评价历史" })
  await expect(history).toBeVisible()
  const item = history.getByRole("article").filter({ hasText: project })
  await expect(item).toContainText(`${score} 分`)
  await expect(item).toContainText(advice)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalLearningPage")
  await expectAboveMobileNavigation(page, ".publishedEvaluationPanel")
}

test("automatic and manual system evaluations publish immutable role-scoped history", async ({
  browser,
}, testInfo) => {
  test.setTimeout(240_000)
  if (!AUTOMATIC_PNG || !MANUAL_PNG) {
    throw new Error("Task 8 synthetic PNG paths are required")
  }
  const viewport = testInfo.project.use.viewport as Viewport
  const guardian = await roleContext(browser, viewport, credentials.guardian)
  const student = await roleContext(browser, viewport, credentials.student)
  const coach = await roleContext(browser, viewport, credentials.coach)
  const reviewer = await roleContext(browser, viewport, credentials.reviewer)

  try {
    const guardianPage = await guardian.newPage()
    const automaticTask = await createTaskFromScreenshot(
      guardianPage,
      "英雄联盟",
      AUTOMATIC_PNG,
    )
    const guardianPending = await waitForTaskStatus(
      guardian,
      automaticTask.id,
      "needs_review",
    )
    expect(guardianPending.task.current_result).toBeNull()
    expect(guardianPending.task.published_result).toBeNull()
    const automaticPending = await taskDetail(
      reviewer,
      automaticTask.id,
      "admin",
    )
    expect(automaticPending.task.current_result?.score).toBe(88)
    expect(automaticPending.task.current_result?.grade).toBe("A")
    expect(automaticPending.task.published_result).toBeNull()

    const reviewerPage = await reviewer.newPage()
    const automaticReview = await reviewTask(
      reviewerPage,
      automaticTask.id,
      "approved",
    )
    expect(automaticReview.task_status).toBe("published")
    const automaticPublished = await waitForTaskStatus(
      guardian,
      automaticTask.id,
      "published",
    )
    expect(automaticPublished.task.published_result?.score).toBe(88)
    await expectPublishedHistory(
      guardianPage,
      BOUND_STUDENT_ID,
      "英雄联盟",
      88,
      "仅用于本地测试，不得用于适配器认证。",
    )

    const studentPage = await student.newPage()
    const manualTask = await createTaskFromScreenshot(
      studentPage,
      "三角洲行动",
      MANUAL_PNG,
    )
    await waitForTaskStatus(student, manualTask.id, "manual_input")

    const coachPage = await coach.newPage()
    await saveManualDraft(coachPage, manualTask.id, {
      score: "84",
      grade: "B+",
      summary: "首次人工评价分项",
      advice: "需要复盘首次路线选择。",
    })
    await waitForTaskStatus(coach, manualTask.id, "needs_review")

    const rejected = await reviewTask(
      reviewerPage,
      manualTask.id,
      "rejected",
      "请补充整改后的路线说明。",
    )
    expect(rejected.task_status).toBe("rejected")
    await waitForTaskStatus(coach, manualTask.id, "rejected")

    await startCorrection(coachPage, manualTask.id)
    await waitForTaskStatus(coach, manualTask.id, "correction_pending")
    await saveManualDraft(coachPage, manualTask.id, {
      score: "92",
      grade: "A",
      summary: "整改后人工评价分项",
      advice: "整改完成，继续保持路线沟通。",
    })
    await waitForTaskStatus(coach, manualTask.id, "needs_review")

    const approved = await reviewTask(
      reviewerPage,
      manualTask.id,
      "approved",
    )
    expect(approved.task_status).toBe("published")
    const manualPublished = await waitForTaskStatus(
      student,
      manualTask.id,
      "published",
    )
    expect(manualPublished.task.published_result?.score).toBe(92)
    await expectPublishedHistory(
      studentPage,
      SELF_STUDENT_ID,
      "三角洲行动",
      92,
      "整改完成，继续保持路线沟通。",
    )

    for (const page of [guardianPage, studentPage, coachPage, reviewerPage]) {
      const text = await page.locator("body").innerText()
      expect(text).not.toMatch(/\b1[3-9]\d{9}\b/)
      expect(text).not.toMatch(/\b\d{17}[\dX]\b/i)
      expect(text).not.toContain("/.system-evaluation-e2e/")
    }
  } finally {
    await Promise.all([
      guardian.close(),
      student.close(),
      coach.close(),
      reviewer.close(),
    ])
  }
})
