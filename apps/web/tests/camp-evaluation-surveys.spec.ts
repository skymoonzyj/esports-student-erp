import { expect, request, test, type APIRequestContext } from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCoach,
  loginAsStudent,
  logout,
  refocus,
} from "./helpers"
import { E2E_CREDENTIALS } from "./e2eCredentials"

const API_BASE_URL = process.env.E2E_API_URL ?? "http://127.0.0.1:18080"
const CYCLE_ID = "e6140000-0000-4000-8000-000000000001"
const COHORT_ID = "e6100000-0000-4000-8000-000000000001"
const STUDENT_ID = "e2000000-0000-4000-8000-000000000011"
const NO_SURVEY_ADMIN_ACCOUNT_ID = "e6180000-0000-4000-8000-000000000001"
const GUARDIAN_PERSON_ID = "e1000000-0000-4000-8000-000000000003"
const OTHER_GUARDIAN_PERSON_ID = "e1000000-0000-4000-8000-000000000008"
const SURVEY_IDENTITY_PERMISSION_ID = "20000000-0000-4000-8000-000000000034"
const SURVEY_PRINT_PERMISSION_ID = "20000000-0000-4000-8000-000000000035"
const ADMIN = E2E_CREDENTIALS.admin
const STUDENT = E2E_CREDENTIALS.second_student
const GUARDIAN = E2E_CREDENTIALS.guardian
const OTHER_GUARDIAN = E2E_CREDENTIALS.other_guardian
const NO_SURVEY_ADMIN = E2E_CREDENTIALS.camp_survey_no_access

type SurveyContext = {
  cycle_id: string
  student_person_id: string
  snapshot_version: number
  can_submit: boolean
  can_revise: boolean
  submission_id: string | null
  latest_version: number | null
  staff: Array<{ id: string; staff_name: string; role_name: string }>
}

type GuardianRelationship = {
  id: string
  relationship_id: string
  status: "scheduled" | "active" | "expired" | "ended"
}

type PermissionOverride = { id: string }

async function loginApi(identifier: string, password: string): Promise<{ api: APIRequestContext; csrf: string }> {
  const api = await request.newContext({ baseURL: API_BASE_URL })
  const response = await api.post("/api/v1/auth/login", {
    data: { identifier, password },
  })
  expect(response.status(), await response.text()).toBe(200)
  const body = (await response.json()) as { csrf_token: string }
  return { api, csrf: body.csrf_token }
}

async function surveyContext(identifier: string, password: string): Promise<SurveyContext> {
  const { api } = await loginApi(identifier, password)
  try {
    return await surveyContextFor(api)
  } finally {
    await api.dispose()
  }
}

async function surveyContextFor(api: APIRequestContext): Promise<SurveyContext> {
  const response = await api.get("/api/v1/evaluations/surveys/context")
  expect(response.status(), await response.text()).toBe(200)
  const items = (await response.json()) as SurveyContext[]
  const context = items.find((item) => item.cycle_id === CYCLE_ID)
  expect(context, `seeded camp survey ${CYCLE_ID} was not returned`).toBeTruthy()
  return context!
}

async function foodData(api: APIRequestContext): Promise<{ cuisine: Record<string, unknown>; foodVersion: number; dish: string }> {
  const catalogResponse = await api.get("/api/v1/food-preferences/cuisines?include_inactive=false")
  expect(catalogResponse.status(), await catalogResponse.text()).toBe(200)
  const catalog = (await catalogResponse.json()) as { items: Array<Record<string, unknown>> }
  const cuisine = catalog.items.find((item) => item.is_other !== true) ?? catalog.items[0]
  expect(cuisine).toBeTruthy()
  const profileResponse = await api.get(`/api/v1/people/${STUDENT_ID}/food-preferences`)
  expect(profileResponse.status(), await profileResponse.text()).toBe(200)
  const profile = (await profileResponse.json()) as { version: number; cuisines: Array<Record<string, unknown>>; dishes: string[] }
  return {
    cuisine: {
      cuisine_option_id: cuisine!.id,
      name: cuisine!.name,
      custom_name: null,
    },
    foodVersion: profile.version,
    dish: profile.dishes[0] ?? "宫保鸡丁",
  }
}

async function submissionPayload(api: APIRequestContext, context: SurveyContext, marker: string) {
  const food = await foodData(api)
  return {
    idempotency_key: `web-e2e-${marker}-${Date.now()}`,
    expected_snapshot_version: context.snapshot_version,
    expected_food_preference_version: food.foodVersion,
    favorite_cuisines: [food.cuisine],
    favorite_dishes: [food.dish],
    course_training_score: 9,
    course_training_reason: `E2E ${marker}`,
    life_service_score: 8,
    life_service_reason: null,
    service_management_score: 9,
    service_management_reason: null,
    overall_suggestion: `E2E ${marker}`,
    staff_ratings: context.staff.map((staff) => ({ staff_snapshot_id: staff.id, score: 8, reason: `E2E ${marker}`, suggestion: null })),
  }
}

async function postSubmission(api: APIRequestContext, csrf: string, payload: Record<string, unknown>) {
  return api.post(`/api/v1/evaluations/surveys/${CYCLE_ID}/submissions`, {
    headers: { "X-CSRF-Token": csrf },
    data: payload,
  })
}

async function ensureSubmission(
  credentials: { identifier: string; password: string },
  respondentPersonId: string,
  marker: string,
): Promise<{ respondentPersonId: string; submissionId: string }> {
  const { api, csrf } = await loginApi(credentials.identifier, credentials.password)
  try {
    let context = await surveyContextFor(api)
    if (!context.submission_id) {
      expect(context.can_submit).toBe(true)
      const response = await postSubmission(api, csrf, await submissionPayload(api, context, marker))
      expect(response.status(), await response.text()).toBe(201)
      const created = (await response.json()) as { submission_id: string }
      context = await surveyContextFor(api)
      expect(context.submission_id).toBe(created.submission_id)
    }
    expect(context.submission_id).toBeTruthy()
    return { respondentPersonId, submissionId: context.submission_id! }
  } finally {
    await api.dispose()
  }
}

async function ensureActiveOtherGuardian(
  api: APIRequestContext,
  csrf: string,
): Promise<string> {
  const listed = await api.get(`/api/v1/people/${STUDENT_ID}/guardians?include_inactive=true`)
  expect(listed.status(), await listed.text()).toBe(200)
  const relationships = (await listed.json()) as GuardianRelationship[]
  const active = relationships.find((item) =>
    item.id === OTHER_GUARDIAN_PERSON_ID &&
    (item.status === "active" || item.status === "scheduled")
  )
  if (active) return active.relationship_id

  const created = await api.post(`/api/v1/people/${STUDENT_ID}/guardians`, {
    headers: { "X-CSRF-Token": csrf },
    data: {
      guardian_id: OTHER_GUARDIAN_PERSON_ID,
      relationship: "母亲",
      is_primary: false,
      valid_from: "2026-01-01",
      valid_until: null,
    },
  })
  expect(created.status(), await created.text()).toBe(201)
  return ((await created.json()) as GuardianRelationship).relationship_id
}

async function addCohortPermissionOverride(
  admin: APIRequestContext,
  csrf: string,
  permissionId: string,
  reason: string,
): Promise<string> {
  const now = Date.now()
  const response = await admin.post(
    `/api/v1/accounts/${NO_SURVEY_ADMIN_ACCOUNT_ID}/permission-overrides`,
    {
      headers: { "X-CSRF-Token": csrf },
      data: {
        permission_id: permissionId,
        effect: "allow",
        scope_kind: "cohort",
        scope_id: COHORT_ID,
        valid_from: new Date(now - 60_000).toISOString(),
        valid_until: new Date(now + 60 * 60_000).toISOString(),
        grant_reason: reason,
      },
    },
  )
  expect(response.status(), await response.text()).toBe(201)
  return ((await response.json()) as PermissionOverride).id
}

async function removePermissionOverride(
  admin: APIRequestContext,
  csrf: string,
  overrideId: string,
): Promise<void> {
  const response = await admin.delete(
    `/api/v1/accounts/${NO_SURVEY_ADMIN_ACCOUNT_ID}/permission-overrides/${overrideId}`,
    { headers: { "X-CSRF-Token": csrf } },
  )
  expect(response.status(), await response.text()).toBe(204)
}

test("student and both guardians own distinct camp survey submissions", async ({ page }) => {
  const respondents = [
    { credentials: STUDENT, label: "学生", personId: STUDENT_ID, marker: "independent-student" },
    { credentials: GUARDIAN, label: "监护人", personId: GUARDIAN_PERSON_ID, marker: "independent-guardian-a" },
    { credentials: OTHER_GUARDIAN, label: "监护人", personId: OTHER_GUARDIAN_PERSON_ID, marker: "independent-guardian-b" },
  ] as const
  const submissions = []

  for (const respondent of respondents) {
    submissions.push(await ensureSubmission(
      respondent.credentials,
      respondent.personId,
      respondent.marker,
    ))
    await page.goto("/login")
    await page.getByLabel("手机号").fill(respondent.credentials.identifier)
    await page.getByLabel("密码", { exact: true }).fill(respondent.credentials.password)
    await page.getByRole("button", { name: "登录" }).click()
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await page.goto("/evaluations/surveys")
    await expect(page.getByRole("heading", { name: "营期评价", exact: true })).toBeVisible()
    const card = page.locator(".campSurveyCard").filter({ hasText: respondent.label })
    await expect(card).toHaveCount(1)
    await expect(card).toContainText(/已提交第 \d+ 版/)
    await expect(card.getByRole("button", { name: "修改问卷" })).toBeVisible()
    await logout(page)
  }

  expect(new Set(submissions.map((item) => item.respondentPersonId)).size).toBe(3)
  expect(new Set(submissions.map((item) => item.submissionId)).size).toBe(3)

  const { api } = await loginApi(ADMIN.identifier, ADMIN.password)
  try {
    const response = await api.get(
      `/api/v1/evaluations/admin/surveys/print-projection?identity=real_name&cohort_id=${COHORT_ID}`,
    )
    expect(response.status(), await response.text()).toBe(200)
    const body = (await response.json()) as {
      items: Array<{ details: Array<{ respondent_person_id: string | null }> }>
    }
    const projectedRespondents = new Set(
      body.items.flatMap((item) => item.details.map((detail) => detail.respondent_person_id)).filter(Boolean),
    )
    expect(projectedRespondents).toEqual(new Set(respondents.map((item) => item.personId)))
  } finally {
    await api.dispose()
  }
})

test("an open guardian survey clears private state after the real relationship is ended", async ({ page }) => {
  const adminSession = await loginApi(ADMIN.identifier, ADMIN.password)
  const guardianSession = await loginApi(
    OTHER_GUARDIAN.identifier,
    OTHER_GUARDIAN.password,
  )
  try {
    const relationshipId = await ensureActiveOtherGuardian(adminSession.api, adminSession.csrf)

    await page.goto("/login")
    await page.getByLabel("手机号").fill(OTHER_GUARDIAN.identifier)
    await page.getByLabel("密码", { exact: true }).fill(OTHER_GUARDIAN.password)
    await page.getByRole("button", { name: "登录" }).click()
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await page.goto("/evaluations/surveys")
    await expect(page.getByRole("heading", { name: "营期评价", exact: true })).toBeVisible()
    await page.getByRole("button", { name: "修改问卷" }).click()
    const privateDraft = page.getByLabel("总体建议")
    await expect(privateDraft).toBeVisible()
    await privateDraft.fill("监护关系撤销后不得回显的私密草稿")

    const ended = await adminSession.api.post(
      `/api/v1/people/${STUDENT_ID}/guardians/${relationshipId}/end`,
      {
        headers: { "X-CSRF-Token": adminSession.csrf },
        data: { reason: "E2E 验证营期评价撤权" },
      },
    )
    expect(ended.status(), await ended.text()).toBe(200)

    const privateRead = await guardianSession.api.get(`/api/v1/evaluations/surveys/${CYCLE_ID}`)
    expect(privateRead.status(), await privateRead.text()).toBe(404)

    await refocus(page)
    await expect(page).toHaveURL(/\/evaluations$/)
    await expect(page.getByRole("heading", { name: "评价中心", exact: true })).toBeVisible()
    await expect(page.getByLabel("总体建议")).toHaveCount(0)

    await refocus(page)
    await expect(page).toHaveURL(/\/evaluations$/)
    await expect(page.getByText("监护关系撤销后不得回显的私密草稿")).toHaveCount(0)
  } finally {
    await guardianSession.api.dispose()
    await adminSession.api.dispose()
  }
})

test("student can submit once, revise once, and the third revision is rejected", async ({ page }) => {
  const { api, csrf } = await loginApi(STUDENT.identifier, STUDENT.password)
  try {
    let context = await surveyContext(STUDENT.identifier, STUDENT.password)
    if (!context.submission_id && context.can_submit) {
      const first = await postSubmission(api, csrf, await submissionPayload(api, context, "first"))
      expect(first.status(), await first.text()).toBe(201)
      context = await surveyContext(STUDENT.identifier, STUDENT.password)
    }
    expect(context.submission_id).toBeTruthy()
    if (context.can_revise) {
      const revision = await api.post(`/api/v1/evaluations/surveys/${CYCLE_ID}/submissions/${context.submission_id}/revisions`, {
        headers: { "X-CSRF-Token": csrf },
        data: await submissionPayload(api, context, "revision"),
      })
      expect(revision.status(), await revision.text()).toBe(200)
      context = await surveyContext(STUDENT.identifier, STUDENT.password)
    }
    expect(context.latest_version).toBeGreaterThanOrEqual(2)
    const third = await api.post(`/api/v1/evaluations/surveys/${CYCLE_ID}/submissions/${context.submission_id}/revisions`, {
      headers: { "X-CSRF-Token": csrf },
      data: await submissionPayload(api, context, "third"),
    })
    expect(third.status()).toBe(409)
    expect(((await third.json()) as { code?: string }).code).toBe("evaluation_survey_revision_conflict")
  } finally {
    await api.dispose()
  }
  await loginAsStudent(page)
  await page.goto("/evaluations/surveys")
  await expect(page.getByRole("heading", { name: "营期评价" })).toBeVisible()
})

test("staff receives anonymous feedback and cannot see respondent identity", async ({ page }) => {
  await loginAsCoach(page)
  await page.goto("/evaluations/surveys/feedback")
  await expect(page.getByRole("heading", { name: "收到的匿名反馈", exact: true })).toBeVisible()
  await expect(page.locator(".campSurveyTable")).toBeVisible()
  await expect(page.locator(".campSurveyTable")).not.toContainText("E2E已绑定学员")
})

test("admin defaults to anonymous, only identity-capable account can switch real name, and print fits every viewport", async ({ page }) => {
  await loginAsAdmin(page)
  await page.goto("/evaluations/surveys/admin")
  await expect(page.getByRole("heading", { name: "营期评价管理", exact: true })).toBeVisible()
  const identityToggle = page.getByLabel("查看实名")
  await expect(identityToggle).toBeVisible()
  await expect(identityToggle).not.toBeChecked()
  await identityToggle.check()
  await expect(page.getByText("当前模式：实名")).toBeVisible()
  await page.goto("/evaluations/surveys/print")
  await expect(page.getByRole("heading", { name: "营期评价打印", exact: true })).toBeVisible()
  await expect(page.getByLabel("打印实名")).toBeVisible()
  await expect(page.getByLabel("打印实名")).not.toBeChecked()
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".printProjection")
  await expectAboveMobileNavigation(page, ".printProjection")
})

test("open real-name admin and print pages clear after a live permission revocation", async ({ page }) => {
  const adminSession = await loginApi(ADMIN.identifier, ADMIN.password)
  const printPage = await page.context().newPage()
  let identityOverrideId: string | null = null
  let printOverrideId: string | null = null
  let delegatedSession: Awaited<ReturnType<typeof loginApi>> | null = null
  try {
    identityOverrideId = await addCohortPermissionOverride(
      adminSession.api,
      adminSession.csrf,
      SURVEY_IDENTITY_PERMISSION_ID,
      "E2E 营期评价实名临时授权",
    )
    printOverrideId = await addCohortPermissionOverride(
      adminSession.api,
      adminSession.csrf,
      SURVEY_PRINT_PERMISSION_ID,
      "E2E 营期评价打印临时授权",
    )
    delegatedSession = await loginApi(
      NO_SURVEY_ADMIN.identifier,
      NO_SURVEY_ADMIN.password,
    )

    await page.goto("/login")
    await page.getByLabel("手机号").fill(NO_SURVEY_ADMIN.identifier)
    await page.getByLabel("密码", { exact: true }).fill(NO_SURVEY_ADMIN.password)
    await page.getByRole("button", { name: "登录" }).click()
    await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
    await page.goto("/evaluations/surveys/admin")
    await page.getByLabel("查看实名").check()
    await expect(page.getByText("当前模式：实名")).toBeVisible()
    await expect(page.getByText(/E2E另一付款家长/).first()).toBeVisible()

    await printPage.goto("/evaluations/surveys/print?identity=real_name")
    await expect(printPage.getByLabel("打印实名")).toBeChecked()
    await expect(printPage.getByText(/评价者：E2E另一付款家长/).first()).toBeVisible()

    await removePermissionOverride(
      adminSession.api,
      adminSession.csrf,
      identityOverrideId,
    )
    identityOverrideId = null

    await refocus(page)
    await refocus(printPage)

    await expect(page).toHaveURL(/\/evaluations$/)
    await expect(printPage).toHaveURL(/\/evaluations$/)
    await expect(page.getByRole("heading", { name: "评价中心", exact: true })).toBeVisible()
    await expect(printPage.getByRole("heading", { name: "评价中心", exact: true })).toBeVisible()
    await expect(page.getByLabel("查看实名")).toHaveCount(0)
    await expect(printPage.getByLabel("打印实名")).toHaveCount(0)
    await expect(page.getByText(/E2E另一付款家长/)).toHaveCount(0)
    await expect(printPage.getByText(/评价者：E2E另一付款家长/)).toHaveCount(0)

    const adminDenied = await delegatedSession.api.get(
      "/api/v1/evaluations/admin/surveys?identity=real_name",
    )
    expect([403, 404]).toContain(adminDenied.status())
    const printDenied = await delegatedSession.api.get(
      "/api/v1/evaluations/admin/surveys/print-projection?identity=real_name",
    )
    expect([403, 404]).toContain(printDenied.status())

    await refocus(page)
    await refocus(printPage)
    await expect(page).toHaveURL(/\/evaluations$/)
    await expect(printPage).toHaveURL(/\/evaluations$/)
  } finally {
    await delegatedSession?.api.dispose()
    if (identityOverrideId) {
      await removePermissionOverride(
        adminSession.api,
        adminSession.csrf,
        identityOverrideId,
      )
    }
    if (printOverrideId) {
      await removePermissionOverride(
        adminSession.api,
        adminSession.csrf,
        printOverrideId,
      )
    }
    await printPage.close()
    await adminSession.api.dispose()
  }
})

test("an account without survey permissions has no identity switch", async ({ page }) => {
  await page.goto("/login")
  await page.getByLabel("手机号").fill(NO_SURVEY_ADMIN.identifier)
  await page.getByLabel("密码", { exact: true }).fill(NO_SURVEY_ADMIN.password)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
  await page.goto("/evaluations/surveys/admin")
  await expect(page).toHaveURL(/\/evaluations$/)
  await expect(page.getByLabel("查看实名")).toHaveCount(0)
  await page.goto("/evaluations/surveys/print")
  await expect(page).toHaveURL(/\/evaluations$/)
  await expect(page.getByLabel("打印实名")).toHaveCount(0)
})
