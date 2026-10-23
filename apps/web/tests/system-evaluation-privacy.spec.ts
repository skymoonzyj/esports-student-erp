import { randomUUID } from "node:crypto"
import { readFile } from "node:fs/promises"
import { dirname, isAbsolute } from "node:path"

import { expect, test } from "@playwright/test"
import type {
  APIResponse,
  Browser,
  BrowserContext,
  Page,
  Response as BrowserResponse,
} from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
} from "./helpers"
import { e2eCredentialTuple } from "./e2eCredentials"

const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5180"
const AUTOMATIC_PNG = process.env.E2E_SYSTEM_EVALUATION_AUTOMATIC_PNG
const MANUAL_PNG = process.env.E2E_SYSTEM_EVALUATION_MANUAL_PNG
const CONFIGURED_MEDIA_ROOTS = [AUTOMATIC_PNG, MANUAL_PNG].flatMap((filePath) =>
  filePath && isAbsolute(filePath) ? [dirname(dirname(filePath))] : [],
)

const AUTOMATIC_PROJECT_ID = "30000000-0000-4000-8000-000000000001"
const MANUAL_PROJECT_ID = "30000000-0000-4000-8000-000000000003"
const BOUND_STUDENT_ID = "e1000000-0000-4000-8000-000000000004"
const SELF_STUDENT_ID = "e1000000-0000-4000-8000-000000000007"
const AUTOMATIC_ENROLLMENT_ID = "e4030000-0000-4000-8000-000000000001"
const MANUAL_ENROLLMENT_ID = "e4030000-0000-4000-8000-000000000002"
const COACH_ACCOUNT_ID = "e1100000-0000-4000-8000-000000000006"
const PRIMARY_GUARDIAN_RELATIONSHIP_ID =
  "e1200000-0000-4000-8000-000000000001"
const EVALUATION_MATCH_PERMISSION_ID =
  "20000000-0000-4000-8000-000000000044"

const PRIVATE_NOT_FOUND = {
  type: "about:blank",
  title: "Not Found",
  status: 404,
  detail: "评价资源不存在",
  code: "evaluation_resource_not_found",
} as const

const credentials = {
  admin: e2eCredentialTuple("admin"),
  guardian: e2eCredentialTuple("guardian"),
  otherGuardian: e2eCredentialTuple("other_guardian"),
  student: e2eCredentialTuple("student"),
  coach: e2eCredentialTuple("coach"),
  reviewer: e2eCredentialTuple("life_teacher"),
} as const

const HIDDEN_MATCH_CANDIDATES = [
  "E2E已绑定学员",
  "E2E-S-001",
  "E2E自助学员",
  "E2E-SELF-001",
] as const

type Viewport = { width: number; height: number }
type RoleSession = {
  context: BrowserContext
  csrfToken: string
}
type BoundTarget = {
  projectId: string
  studentId: string
  enrollmentId: string
}
type TaskCreated = {
  id: string
  public_number: string
  status: string
  lock_version: number
}
type ResultProjection = {
  score: number
  grade: string
  advice: string
}
type TaskDetail = {
  api_version: "v2"
  task: {
    id: string
    public_number: string
    status: string
    lock_version: number
    student_person_id: string | null
    identity_summary: string | null
    project_name: string
    current_result: ResultProjection | null
    published_result: ResultProjection | null
  }
}
type TaskWorkspace = {
  api_version: "v2"
  task_id: string
  task_version: number
  media: Array<Record<string, unknown>>
}
type DraftSaved = {
  task_version: number
  draft_revision: number
}
type ResultSubmitted = {
  task_status: string
  task_version: number
}
type Reviewed = {
  task_status: string
  task_version: number
}
type ProblemDetails = {
  status: number
  code: string
  detail: string
}
type PermissionOverride = { id: string }
type PermissionOverrideCreated = PermissionOverride & {
  rawBody: string
  status: number
}

function expectNoSensitiveText(value: string): void {
  expect(value).not.toMatch(/\b1[3-9]\d{9}\b/)
  expect(value).not.toMatch(/\b\d{17}[\dX]\b/i)
  expect(value).not.toMatch(/\.?system-evaluation-e2e/i)
  expect(value).not.toMatch(/file:\/\//i)
  expect(value).not.toMatch(
    /["'](?:absolute_path|filesystem_path|media_path|path|physical_path|preview_storage_key|quarantine_storage_key|storage_key)["']\s*:/i,
  )
  expect(value).not.toMatch(/(?:objects|previews|quarantine)\//i)
  for (const root of CONFIGURED_MEDIA_ROOTS) {
    expect(value).not.toContain(root)
  }
}

function expectSafePayload(payload: unknown): void {
  const serialized = JSON.stringify(payload)
  expectNoSensitiveText(serialized)
  expect(serialized).not.toMatch(
    /["'](?:candidate_identity|candidate_identities|candidate_count|candidate_phone|candidate_identity_number)["']/i,
  )
}

function expectPrivateHeaders(response: APIResponse): void {
  const headers = response.headers()
  expect(headers["cache-control"]).toBe("no-store, private")
  expect(headers.pragma).toBe("no-cache")
  expect(headers["x-content-type-options"]).toBe("nosniff")
}

async function safeJson<T>(response: APIResponse, status: number): Promise<T> {
  expect(response.status()).toBe(status)
  expectPrivateHeaders(response)
  const payload = (await response.json()) as T
  expectSafePayload(payload)
  return payload
}

async function roleSession(
  browser: Browser,
  viewport: Viewport,
  [phone, password]: readonly [string, string],
): Promise<RoleSession> {
  const bootstrap = await browser.newContext({ baseURL: BASE_URL, viewport })
  try {
    const page = await bootstrap.newPage()
    await page.goto("/login")
    await page.getByLabel("手机号").fill(phone)
    await page.getByLabel("密码", { exact: true }).fill(password)
    const loginResponse = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && new URL(response.url()).pathname === "/api/v1/auth/login",
    )
    await page.getByRole("button", { name: "登录" }).click()
    const response = await loginResponse
    expect(response.status()).toBe(200)
    const body = (await response.json()) as { csrf_token: string }
    await expect(
      page.getByRole("heading", { name: "工作台", exact: true }),
    ).toBeVisible()
    const storageState = await bootstrap.storageState()
    return {
      context: await browser.newContext({
        baseURL: BASE_URL,
        viewport,
        storageState,
      }),
      csrfToken: body.csrf_token,
    }
  } finally {
    await bootstrap.close()
  }
}

function mutationHeaders(
  role: RoleSession,
  idempotencyKey?: string,
): Record<string, string> {
  return {
    "X-CSRF-Token": role.csrfToken,
    ...(idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {}),
  }
}

async function createTask(
  role: RoleSession,
  filePath: string,
  projectId: string,
  target: BoundTarget | null,
  namespace: string,
): Promise<TaskCreated> {
  const sessionResponse = await role.context.request.post(
    "/api/v1/evaluations/system/upload-sessions",
    {
      headers: mutationHeaders(role),
      data: {
        project_id: projectId,
        ...(target
          ? {
              student_person_id: target.studentId,
              enrollment_id: target.enrollmentId,
            }
          : {}),
      },
    },
  )
  const session = await safeJson<{ id: string }>(sessionResponse, 201)

  const file = await readFile(filePath)
  const uploadResponse = await role.context.request.put(
    `/api/v1/evaluations/system/upload-sessions/${session.id}/slots/match_result`,
    {
      headers: mutationHeaders(role),
      multipart: {
        file: {
          name: `${namespace}.png`,
          mimeType: "image/png",
          buffer: file,
        },
      },
    },
  )
  const uploaded = await safeJson<Record<string, unknown>>(uploadResponse, 200)
  expect(Object.keys(uploaded).sort()).toEqual([
    "height_pixels",
    "id",
    "mime_type",
    "position",
    "session_id",
    "size_bytes",
    "slot_key",
    "width_pixels",
  ])

  const taskResponse = await role.context.request.post(
    `/api/v1/evaluations/system/upload-sessions/${session.id}/tasks`,
    {
      headers: mutationHeaders(role, `${namespace}-${randomUUID()}`),
      data: {},
    },
  )
  return safeJson<TaskCreated>(taskResponse, 201)
}

async function taskDetail(
  role: RoleSession,
  taskId: string,
  projection: "account" | "admin" = "account",
): Promise<TaskDetail> {
  const prefix = projection === "admin"
    ? "/api/v1/evaluations/admin/system-tasks"
    : "/api/v1/evaluations/system/tasks"
  const response = await role.context.request.get(`${prefix}/${taskId}`)
  return safeJson<TaskDetail>(response, 200)
}

async function waitForTaskStatus(
  role: RoleSession,
  taskId: string,
  expected: string,
): Promise<TaskDetail> {
  let latest: TaskDetail | null = null
  await expect.poll(async () => {
    latest = await taskDetail(role, taskId)
    return latest.task.status
  }, { timeout: 60_000 }).toBe(expected)
  if (latest === null) throw new Error(`Task ${taskId} did not load`)
  return latest
}

async function saveAndSubmitManualResult(
  coach: RoleSession,
  task: TaskCreated,
): Promise<ResultSubmitted> {
  const draftResponse = await coach.context.request.put(
    `/api/v1/evaluations/system/tasks/${task.id}/manual-draft`,
    {
      headers: mutationHeaders(coach),
      data: {
        expected_lock_version: task.lock_version,
        draft_revision: null,
        fields: [
          { key: "kills", value: 12 },
          { key: "deaths", value: 3 },
          { key: "assists", value: 9 },
        ],
        confidences: [
          { key: "kills", confidence: 1 },
          { key: "deaths", confidence: 1 },
          { key: "assists", confidence: 1 },
        ],
        missing_fields: [],
        warnings: [],
        components: [
          {
            key: "overall",
            score: 89,
            weight: 1,
            summary: "隐私 E2E 人工评价分项",
          },
        ],
        score: 89,
        grade: "A",
        advice: "隐私 E2E 人工评价建议。",
      },
    },
  )
  const draft = await safeJson<DraftSaved>(draftResponse, 200)
  const submittedResponse = await coach.context.request.post(
    `/api/v1/evaluations/system/tasks/${task.id}/submit-review`,
    {
      headers: mutationHeaders(coach, `manual-submit-${randomUUID()}`),
      data: {
        expected_lock_version: draft.task_version,
        draft_revision: draft.draft_revision,
      },
    },
  )
  const submitted = await safeJson<ResultSubmitted>(submittedResponse, 201)
  expect(submitted.task_status).toBe("needs_review")
  return submitted
}

async function reviewTask(
  role: RoleSession,
  taskId: string,
  expectedLockVersion: number,
  namespace: string,
): Promise<APIResponse> {
  return role.context.request.post(
    `/api/v1/evaluations/admin/system-tasks/${taskId}/reviews`,
    {
      headers: mutationHeaders(role, `${namespace}-${randomUUID()}`),
      data: {
        expected_lock_version: expectedLockVersion,
        decision: "approved",
      },
    },
  )
}

async function addAutomaticProjectMatchDeny(
  admin: RoleSession,
): Promise<PermissionOverrideCreated> {
  const now = Date.now()
  const response = await admin.context.request.post(
    `/api/v1/accounts/${COACH_ACCOUNT_ID}/permission-overrides`,
    {
      headers: mutationHeaders(admin),
      data: {
        permission_id: EVALUATION_MATCH_PERMISSION_ID,
        effect: "deny",
        scope_kind: "project",
        scope_id: AUTOMATIC_PROJECT_ID,
        valid_from: new Date(now - 60_000).toISOString(),
        valid_until: new Date(now + 60 * 60_000).toISOString(),
        grant_reason: "E2E project isolation verification",
      },
    },
  )
  const rawBody = await response.text()
  const payload = JSON.parse(rawBody) as PermissionOverride
  return { id: payload.id, rawBody, status: response.status() }
}

async function removePermissionOverride(
  admin: RoleSession,
  overrideId: string,
): Promise<void> {
  const response = await admin.context.request.delete(
    `/api/v1/accounts/${COACH_ACCOUNT_ID}/permission-overrides/${overrideId}`,
    { headers: mutationHeaders(admin) },
  )
  expect(response.status()).toBe(204)
}

async function setStudentStatus(
  admin: RoleSession,
  studentId: string,
  status: "active" | "inactive",
): Promise<void> {
  const response = await admin.context.request.patch(`/api/v1/people/${studentId}`, {
    headers: mutationHeaders(admin),
    data: { student_status: status },
  })
  expect(response.status()).toBe(200)
  expectNoSensitiveText(await response.text())
}

async function expectPrivateNotFound(response: APIResponse): Promise<ProblemDetails> {
  const payload = await safeJson<ProblemDetails>(response, 404)
  expect(payload).toEqual(PRIVATE_NOT_FOUND)
  return payload
}

async function expectTaskPageLayout(page: Page): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".systemEvaluationTaskDetailPage")
  await expectAboveMobileNavigation(page, ".systemEvaluationTaskDetailPage")
}

async function expectSafePage(
  page: Page,
  forbiddenIdentities: readonly string[] = [],
): Promise<void> {
  const [text, html] = await Promise.all([
    page.locator("body").innerText(),
    page.content(),
  ])
  expectNoSensitiveText(text)
  expectNoSensitiveText(html)
  for (const identity of forbiddenIdentities) {
    expect(text).not.toContain(identity)
    expect(html).not.toContain(identity)
  }
}

function observeScopedJsonResponses(page: Page): BrowserResponse[] {
  const responses: BrowserResponse[] = []
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname
    const isPrivateApi = [
      "/api/v1/evaluations",
      "/api/v1/family",
      "/api/v1/portal",
    ].some((prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`))
    const contentType = response.headers()["content-type"] ?? ""
    if (isPrivateApi && /\bjson\b/i.test(contentType)) responses.push(response)
  })
  return responses
}

async function expectSafeJsonResponses(
  responses: readonly BrowserResponse[],
  forbiddenIdentities: readonly string[] = [],
): Promise<void> {
  expect(responses.length).toBeGreaterThan(0)
  for (const response of responses) {
    const failure = await response.finished()
    if (failure) throw failure
    const body = await response.text()
    expectNoSensitiveText(body)
    expectSafePayload(JSON.parse(body) as unknown)
    for (const identity of forbiddenIdentities) {
      expect(body).not.toContain(identity)
    }
  }
}

test("private system evaluation selectors, conflicts, revocation, and layouts stay indistinguishable", async ({
  browser,
}, testInfo) => {
  test.setTimeout(300_000)
  if (!AUTOMATIC_PNG || !MANUAL_PNG) {
    throw new Error("Task 8 synthetic PNG paths are required")
  }
  const viewport = testInfo.project.use.viewport as Viewport
  const opened: RoleSession[] = []

  try {
    const admin = await roleSession(browser, viewport, credentials.admin)
    opened.push(admin)
    const guardian = await roleSession(browser, viewport, credentials.guardian)
    opened.push(guardian)
    const otherGuardian = await roleSession(
      browser,
      viewport,
      credentials.otherGuardian,
    )
    opened.push(otherGuardian)
    const student = await roleSession(browser, viewport, credentials.student)
    opened.push(student)
    const coach = await roleSession(browser, viewport, credentials.coach)
    opened.push(coach)
    const reviewer = await roleSession(browser, viewport, credentials.reviewer)
    opened.push(reviewer)

    const pendingMatchTask = await createTask(
      coach,
      AUTOMATIC_PNG,
      AUTOMATIC_PROJECT_ID,
      null,
      `pending-match-${testInfo.project.name}`,
    )
    expect(pendingMatchTask.status).toBe("pending_match")
    expectSafePayload(pendingMatchTask)

    const pendingDetail = await taskDetail(coach, pendingMatchTask.id)
    expect(pendingDetail.task.student_person_id).toBeNull()
    expect(pendingDetail.task.identity_summary).toBeNull()
    const pendingWorkspaceResponse = await coach.context.request.get(
      `/api/v1/evaluations/system/tasks/${pendingMatchTask.id}/workspace`,
    )
    const pendingWorkspace = await safeJson<TaskWorkspace>(
      pendingWorkspaceResponse,
      200,
    )
    for (const medium of pendingWorkspace.media) {
      expect(Object.keys(medium).sort()).toEqual([
        "height_pixels",
        "id",
        "mime_type",
        "position",
        "size_bytes",
        "slot_key",
        "width_pixels",
      ])
    }
    for (const candidate of HIDDEN_MATCH_CANDIDATES) {
      expect(JSON.stringify([pendingMatchTask, pendingDetail, pendingWorkspace]))
        .not.toContain(candidate)
    }

    const manualTask = await createTask(
      student,
      MANUAL_PNG,
      MANUAL_PROJECT_ID,
      {
        projectId: MANUAL_PROJECT_ID,
        studentId: SELF_STUDENT_ID,
        enrollmentId: MANUAL_ENROLLMENT_ID,
      },
      `manual-privacy-${testInfo.project.name}`,
    )
    expect(manualTask.status).toBe("manual_input")
    const submitted = await saveAndSubmitManualResult(coach, manualTask)

    const automaticTask = await createTask(
      guardian,
      AUTOMATIC_PNG,
      AUTOMATIC_PROJECT_ID,
      {
        projectId: AUTOMATIC_PROJECT_ID,
        studentId: BOUND_STUDENT_ID,
        enrollmentId: AUTOMATIC_ENROLLMENT_ID,
      },
      `automatic-privacy-${testInfo.project.name}`,
    )
    expect(automaticTask.status).toBe("queued")
    const guardianAutomaticPending = await waitForTaskStatus(
      guardian,
      automaticTask.id,
      "needs_review",
    )
    expect(guardianAutomaticPending.task.current_result).toBeNull()
    expect(guardianAutomaticPending.task.published_result).toBeNull()

    const reviewerAutomaticPending = await taskDetail(
      reviewer,
      automaticTask.id,
      "admin",
    )
    expect(reviewerAutomaticPending.task.current_result).not.toBeNull()
    expect(reviewerAutomaticPending.task.published_result).toBeNull()

    const coachAutomatic = await taskDetail(coach, automaticTask.id, "admin")
    expect(coachAutomatic.task.id).toBe(automaticTask.id)

    const missing = await coach.context.request.get(
      `/api/v1/evaluations/admin/system-tasks/${randomUUID()}`,
    )
    const missingBody = await expectPrivateNotFound(missing)

    const wrongScope = await otherGuardian.context.request.get(
      `/api/v1/evaluations/system/tasks/${automaticTask.id}`,
    )
    const wrongScopeBody = await expectPrivateNotFound(wrongScope)

    const deny = await addAutomaticProjectMatchDeny(admin)
    try {
      expect(deny.status).toBe(201)
      expectNoSensitiveText(deny.rawBody)
      const denied = await coach.context.request.get(
        `/api/v1/evaluations/admin/system-tasks/${automaticTask.id}`,
      )
      const deniedBody = await expectPrivateNotFound(denied)
      expect(deniedBody).toEqual(missingBody)
      const otherProjectStillAllowed = await taskDetail(coach, manualTask.id, "admin")
      expect(otherProjectStillAllowed.task.project_name).toBe("三角洲行动")
    } finally {
      await removePermissionOverride(admin, deny.id)
    }

    let disabledBody: ProblemDetails
    await setStudentStatus(admin, BOUND_STUDENT_ID, "inactive")
    try {
      const disabled = await coach.context.request.get(
        `/api/v1/evaluations/admin/system-tasks/${automaticTask.id}`,
      )
      disabledBody = await expectPrivateNotFound(disabled)
    } finally {
      await setStudentStatus(admin, BOUND_STUDENT_ID, "active")
    }

    expect([wrongScopeBody, disabledBody]).toEqual([missingBody, missingBody])

    const selfReviewResponse = await reviewTask(
      coach,
      manualTask.id,
      submitted.task_version,
      "self-review",
    )
    const selfReview = await safeJson<ProblemDetails>(selfReviewResponse, 409)
    expect(selfReview.code).toBe("evaluation_review_conflict")
    expect(selfReview.detail).toBe("不能审核自己创建的评价")

    expect(submitted.task_version).toBeGreaterThan(1)
    const staleReviewResponse = await reviewTask(
      reviewer,
      manualTask.id,
      submitted.task_version - 1,
      "stale-review",
    )
    const staleReview = await safeJson<ProblemDetails>(staleReviewResponse, 409)
    expect(staleReview.code).toBe("evaluation_stale_version")
    expect(staleReview.detail).toBe("任务版本已变化，请刷新后重试")
    const manualStillPending = await taskDetail(coach, manualTask.id)
    expect(manualStillPending.task.status).toBe("needs_review")

    const coachPage = await coach.context.newPage()
    const coachJsonResponses = observeScopedJsonResponses(coachPage)
    await coachPage.goto(`/evaluations/system/tasks/${pendingMatchTask.id}`)
    await expect(
      coachPage.getByRole("heading", { name: pendingMatchTask.public_number }),
    ).toBeVisible()
    await expect(
      coachPage.getByText(/待匹配 · 英雄联盟 · 待匹配/),
    ).toBeVisible()
    await expectSafePage(coachPage, HIDDEN_MATCH_CANDIDATES)
    await expectTaskPageLayout(coachPage)

    await coachPage.goto(`/evaluations/system/tasks/${manualTask.id}`)
    await expect(
      coachPage.getByRole("heading", { name: manualTask.public_number }),
    ).toBeVisible()
    await expect(coachPage.getByRole("region", { name: "评价结果" }).first())
      .toBeVisible()
    await expectSafePage(coachPage, ["E2E自助学员", "E2E-SELF-001"])
    await expectTaskPageLayout(coachPage)
    await expectSafeJsonResponses(coachJsonResponses, HIDDEN_MATCH_CANDIDATES)

    const approvedResponse = await reviewTask(
      reviewer,
      automaticTask.id,
      reviewerAutomaticPending.task.lock_version,
      "independent-review",
    )
    const approved = await safeJson<Reviewed>(approvedResponse, 200)
    expect(approved.task_status).toBe("published")
    const automaticPublished = await waitForTaskStatus(
      guardian,
      automaticTask.id,
      "published",
    )
    const publishedResult = automaticPublished.task.published_result
    if (publishedResult === null) throw new Error("Published result is missing")

    const guardianPage = await guardian.context.newPage()
    const guardianJsonResponses = observeScopedJsonResponses(guardianPage)
    await guardianPage.goto(`/learning/${BOUND_STUDENT_ID}`)
    const history = guardianPage.getByRole("region", { name: "系统评价历史" })
    const publishedCard = history.getByRole("article", {
      name: `${automaticTask.public_number} 已发布评价`,
    })
    await expect(publishedCard).toContainText(`${publishedResult.score} 分`)
    await expect(publishedCard).toContainText(publishedResult.advice)
    await expectSafePage(guardianPage)
    await expectNoHorizontalOverflow(guardianPage)
    await expectHorizontallyInsideViewport(guardianPage, ".portalLearningPage")
    await expectAboveMobileNavigation(guardianPage, ".publishedEvaluationPanel")

    const guardianTaskPage = await guardian.context.newPage()
    const guardianTaskJsonResponses = observeScopedJsonResponses(guardianTaskPage)
    const guardianTaskRoute = `/evaluations/system/tasks/${automaticTask.id}`
    await guardianTaskPage.goto(guardianTaskRoute)
    await expect(
      guardianTaskPage.getByRole("heading", {
        name: automaticTask.public_number,
      }),
    ).toBeVisible()
    await expect(guardianTaskPage.getByText(publishedResult.advice)).toBeVisible()
    await expectSafePage(guardianTaskPage)
    await expectTaskPageLayout(guardianTaskPage)

    const ended = await admin.context.request.post(
      `/api/v1/people/${BOUND_STUDENT_ID}/guardians/${PRIMARY_GUARDIAN_RELATIONSHIP_ID}/end`,
      {
        headers: mutationHeaders(admin),
        data: { reason: "E2E relationship revocation verification" },
      },
    )
    expect(ended.status()).toBe(200)
    expectNoSensitiveText(await ended.text())

    const revoked = await guardian.context.request.get(
      `/api/v1/evaluations/system/tasks/${automaticTask.id}`,
    )
    await expectPrivateNotFound(revoked)

    await guardianTaskPage.reload()
    await expect(guardianTaskPage).not.toHaveURL(
      new RegExp(`${guardianTaskRoute}$`),
    )
    await expect(
      guardianTaskPage.getByRole("heading", { name: "系统评价任务" }),
    ).toBeVisible()
    await expect(guardianTaskPage.locator("body")).not.toContainText(
      automaticTask.public_number,
    )
    await expect(guardianTaskPage.locator("body")).not.toContainText(
      publishedResult.advice,
    )
    await expectSafePage(guardianTaskPage)
    await expectNoHorizontalOverflow(guardianTaskPage)
    await expectHorizontallyInsideViewport(
      guardianTaskPage,
      ".systemEvaluationTasksPage",
    )
    await expectAboveMobileNavigation(
      guardianTaskPage,
      ".systemEvaluationTasksPage",
    )
    await expectSafeJsonResponses(guardianTaskJsonResponses)

    const revokedRoute = `/learning/${BOUND_STUDENT_ID}`
    await guardianPage.reload()
    await expect(guardianPage).not.toHaveURL(new RegExp(`${revokedRoute}$`))
    await expect(guardianPage.locator("body")).not.toContainText(
      automaticTask.public_number,
    )
    await expect(guardianPage.locator("body")).not.toContainText(
      publishedResult.advice,
    )
    await expectSafePage(guardianPage)
    await expectNoHorizontalOverflow(guardianPage)
    await expectHorizontallyInsideViewport(guardianPage, ".portalLearningPage")
    await expectAboveMobileNavigation(guardianPage, ".portalLearningPage")
    await expectSafeJsonResponses(guardianJsonResponses)
  } finally {
    await Promise.all(opened.map(({ context }) => context.close()))
  }
})
