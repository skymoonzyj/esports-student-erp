import { expect, test } from "@playwright/test"
import type {
  Browser,
  BrowserContext,
  Locator,
  Page,
} from "@playwright/test"

import {
  createAdminRequest,
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  expectNoInternalHorizontalOverflow,
  refocus,
} from "./helpers"
import { e2eCredentialTuple } from "./e2eCredentials"


const BASE_URL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:5181"
const STUDENT_PERSON_ID = "e2000000-0000-4000-8000-000000000011"
const AUTHORIZED_ASSISTANT_ACCOUNT_ID = "e5010000-0000-4000-8000-000000000001"
const AUTHORIZED_ASSISTANT_OVERRIDE_ID = "e5150000-0000-4000-8000-000000000001"
const SECOND_GUARDIAN_RELATIONSHIP_ID = "e5020000-0000-4000-8000-000000000001"
const STUDENT_NAME = "E2E监护人第二学员"
const COHORT_21 = "E2E教学评语21日营"
const COHORT_10 = "E2E教学评语10日营"
const COHORT_UTC = "E2E教学评语跨UTC日界营"

const credentials = {
  coach: e2eCredentialTuple("coach"),
  authorizedAssistant: e2eCredentialTuple("teaching_authorized_assistant"),
  unauthorizedAssistant: e2eCredentialTuple("teaching_unauthorized_assistant"),
  reviewer: e2eCredentialTuple("life_teacher"),
  student: e2eCredentialTuple("second_student"),
  guardianOne: e2eCredentialTuple("guardian"),
  guardianTwo: e2eCredentialTuple("other_guardian"),
} as const

type Viewport = { width: number; height: number }
type ReportMutationResponse = {
  report_id: string
  status: string
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

async function expectTeachingLayout(page: Page): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".teachingReportPage")
  await expectAboveMobileNavigation(page, ".teachingReportPage")
}

async function openAuthorTarget(page: Page, cohortName: string): Promise<void> {
  await page.goto("/evaluations/reports")
  await expect(
    page.getByRole("heading", { name: "教学评语工作台", exact: true }),
  ).toBeVisible()
  const cohort = page.getByLabel("营期")
  await expect(cohort.locator("option").filter({ hasText: cohortName })).toHaveCount(1)
  await cohort.selectOption({ label: cohortName })
  const target = page
    .getByRole("article")
    .filter({ hasText: `${cohortName} · 英雄联盟` })
  await expect(target).toHaveCount(1)
  await target.getByRole("button", { name: `选择${STUDENT_NAME}` }).click()
  await expect(page.getByRole("region", { name: "评语任务" })).toBeVisible()
  await expectTeachingLayout(page)
}

function slot(page: Page, kind: "日报" | "周报", sequence: number): Locator {
  return page.getByRole("article", { name: `${kind} 第 ${sequence} 期` })
}

async function selectKind(page: Page, kind: "daily" | "weekly"): Promise<void> {
  await page.getByLabel("评语类型").selectOption(kind)
}

async function periodLengthDays(item: Locator): Promise<number> {
  const text = await item.innerText()
  const match = text.match(/(\d{4}-\d{2}-\d{2}) 至 (\d{4}-\d{2}-\d{2})/)
  if (!match?.[1] || !match[2]) throw new Error(`Missing report period in ${text}`)
  const startsAt = Date.parse(`${match[1]}T00:00:00Z`)
  const endsAt = Date.parse(`${match[2]}T00:00:00Z`)
  return Math.round((endsAt - startsAt) / 86_400_000) + 1
}

function mutationResponse(
  page: Page,
  method: "PUT" | "POST",
  suffix: string,
) {
  return page.waitForResponse((response) => {
    const path = new URL(response.url()).pathname
    return response.request().method() === method
      && path.startsWith("/api/v1/evaluations/reports/")
      && path.endsWith(suffix)
  })
}

async function openEditor(
  page: Page,
  kind: "日报" | "周报",
  sequence: number,
): Promise<Locator> {
  await slot(page, kind, sequence)
    .getByRole("button", { name: `编辑${kind}第 ${sequence} 期` })
    .click()
  const dialog = page.getByRole("dialog", { name: "编辑教学评语" })
  await expect(dialog).toBeVisible()
  await expectNoInternalHorizontalOverflow(dialog)
  await expectAboveMobileNavigation(page, ".systemEvaluationDialogBackdrop")
  return dialog
}

async function saveDraft(
  page: Page,
  kind: "日报" | "周报",
  sequence: number,
  body: string,
): Promise<ReportMutationResponse> {
  const dialog = await openEditor(page, kind, sequence)
  await dialog.getByLabel("评语正文").fill(body)
  const responsePromise = mutationResponse(page, "PUT", "/draft")
  await dialog.getByRole("button", { name: "保存草稿" }).click()
  const response = await responsePromise
  expect(response.status(), await response.text()).toBe(200)
  const payload = (await response.json()) as ReportMutationResponse
  await expect(dialog).toHaveCount(0)
  return payload
}

async function submitDraft(
  page: Page,
  kind: "日报" | "周报",
  sequence: number,
): Promise<ReportMutationResponse> {
  const dialog = await openEditor(page, kind, sequence)
  const responsePromise = mutationResponse(page, "POST", "/submit")
  await dialog.getByRole("button", { name: "提交审核" }).click()
  const response = await responsePromise
  expect(response.status(), await response.text()).toBe(201)
  const payload = (await response.json()) as ReportMutationResponse
  await expect(dialog).toHaveCount(0)
  return payload
}

async function startDraft(
  page: Page,
  kind: "日报" | "周报",
  sequence: number,
  action: "开始整改" | "发起修订",
): Promise<void> {
  const dialog = await openEditor(page, kind, sequence)
  const suffix = action === "开始整改" ? "/correction-drafts" : "/revisions"
  const responsePromise = mutationResponse(page, "POST", suffix)
  await dialog.getByRole("button", { name: action }).click()
  const response = await responsePromise
  expect(response.status(), await response.text()).toBe(201)
  await expect(dialog).toHaveCount(0)
}

async function reviewReport(
  page: Page,
  reportId: string,
  decision: "approved" | "rejected",
  reason?: string,
): Promise<void> {
  await page.goto("/evaluations/reports/review")
  await expect(
    page.getByRole("heading", { name: "教学评语审核", exact: true }),
  ).toBeVisible()
  const cohort = page.getByLabel("营期")
  await expect(cohort.locator("option").filter({ hasText: COHORT_21 })).toHaveCount(1)
  await cohort.selectOption({ label: COHORT_21 })
  const reviewButton = page.getByRole("button", { name: "审核E2E教学教练的日报" })
  await expect(reviewButton).toHaveCount(1)
  await reviewButton.click()
  const dialog = page.getByRole("dialog", { name: "审核教学评语" })
  await expect(dialog).toBeVisible()
  await expectNoInternalHorizontalOverflow(dialog)
  const label = decision === "approved" ? "通过" : "驳回"
  await dialog.getByRole("radio", { name: label }).check()
  if (decision === "rejected") {
    await dialog.getByLabel("驳回原因").fill(reason ?? "请补充训练结论。")
  }
  const responsePromise = page.waitForResponse((response) =>
    response.request().method() === "POST"
    && new URL(response.url()).pathname
      === `/api/v1/evaluations/reports/${reportId}/reviews`,
  )
  await dialog.getByRole("button", { name: "确认审核" }).click()
  const response = await responsePromise
  expect(response.status(), await response.text()).toBe(200)
  await expect(dialog).toHaveCount(0)
  await expectTeachingLayout(page)
}

async function expectPublished(
  page: Page,
  body: string,
  version: number,
): Promise<Locator> {
  await page.goto(`/learning/${STUDENT_PERSON_ID}`)
  const panel = page.getByRole("region", { name: "教学评语" })
  await expect(panel).toBeVisible()
  const item = panel.getByRole("article").filter({ hasText: body })
  await expect(item).toHaveCount(1)
  await expect(item).toContainText(`版本 ${version}`)
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, ".portalLearningPage")
  await expectAboveMobileNavigation(page, ".publishedReportsPanel")
  return item
}

test("daily and weekly teaching reports preserve reviewed family history and revoke immediately", async ({
  browser,
}, testInfo) => {
  test.setTimeout(300_000)
  const viewport = testInfo.project.use.viewport as Viewport
  const suffix = testInfo.project.name
  const initialBody = `E2E ${suffix} 首次补录日报`
  const rejectionReason = `E2E ${suffix} 请补充训练结论`
  const correctedBody = `E2E ${suffix} 整改日报已补充训练结论`
  const revisionBody = `E2E ${suffix} 修订日报最终版本`
  const assistantBody = `E2E ${suffix} 助教私有草稿`

  const coach = await roleContext(browser, viewport, credentials.coach)
  const reviewer = await roleContext(browser, viewport, credentials.reviewer)
  const authorizedAssistant = await roleContext(
    browser,
    viewport,
    credentials.authorizedAssistant,
  )
  const unauthorizedAssistant = await roleContext(
    browser,
    viewport,
    credentials.unauthorizedAssistant,
  )
  const student = await roleContext(browser, viewport, credentials.student)
  const guardianOne = await roleContext(browser, viewport, credentials.guardianOne)
  const guardianTwo = await roleContext(browser, viewport, credentials.guardianTwo)
  const { admin, csrfToken } = await createAdminRequest()

  try {
    const coachPage = await coach.newPage()

    await openAuthorTarget(coachPage, COHORT_21)
    await selectKind(coachPage, "weekly")
    for (const sequence of [1, 2, 3]) {
      const item = slot(coachPage, "周报", sequence)
      await expect(item).toBeVisible()
      expect(await periodLengthDays(item)).toBe(7)
    }

    await openAuthorTarget(coachPage, COHORT_10)
    await selectKind(coachPage, "weekly")
    await expect(slot(coachPage, "周报", 1)).toBeVisible()
    await expect(slot(coachPage, "周报", 2)).toBeVisible()
    expect(await periodLengthDays(slot(coachPage, "周报", 1))).toBe(7)
    expect(await periodLengthDays(slot(coachPage, "周报", 2))).toBe(3)
    const shortWeekDialog = await openEditor(coachPage, "周报", 2)
    await expect(shortWeekDialog.getByRole("button", { name: "保存草稿" })).toBeVisible()
    await expect(shortWeekDialog.getByRole("button", { name: "提交审核" })).toHaveCount(0)
    await shortWeekDialog.getByRole("button", { name: "取消" }).click()

    await openAuthorTarget(coachPage, COHORT_UTC)
    await selectKind(coachPage, "daily")
    await expect(slot(coachPage, "日报", 1)).toBeVisible()
    expect(await periodLengthDays(slot(coachPage, "日报", 1))).toBe(1)

    await openAuthorTarget(coachPage, COHORT_21)
    await selectKind(coachPage, "daily")
    const draft = await saveDraft(coachPage, "日报", 1, initialBody)
    expect(draft.status).toBe("draft")
    const submitted = await submitDraft(coachPage, "日报", 1)
    expect(submitted.report_id).toBe(draft.report_id)
    expect(submitted.status).toBe("pending_review")

    const reviewerPage = await reviewer.newPage()
    await reviewReport(
      reviewerPage,
      draft.report_id,
      "rejected",
      rejectionReason,
    )

    await openAuthorTarget(coachPage, COHORT_21)
    await selectKind(coachPage, "daily")
    await expect(slot(coachPage, "日报", 1)).toContainText("已驳回")
    await startDraft(coachPage, "日报", 1, "开始整改")
    const correctedDraft = await saveDraft(coachPage, "日报", 1, correctedBody)
    expect(correctedDraft.report_id).toBe(draft.report_id)
    const correctedSubmit = await submitDraft(coachPage, "日报", 1)
    expect(correctedSubmit.status).toBe("pending_review")
    await reviewReport(reviewerPage, draft.report_id, "approved")

    const studentPage = await student.newPage()
    const guardianOnePage = await guardianOne.newPage()
    const guardianTwoPage = await guardianTwo.newPage()
    for (const page of [studentPage, guardianOnePage, guardianTwoPage]) {
      await expectPublished(page, correctedBody, 2)
      await expect(page.getByText(initialBody)).toHaveCount(0)
      await expect(page.getByText(rejectionReason)).toHaveCount(0)
    }

    await openAuthorTarget(coachPage, COHORT_21)
    await selectKind(coachPage, "daily")
    await startDraft(coachPage, "日报", 1, "发起修订")
    await saveDraft(coachPage, "日报", 1, revisionBody)
    const revisionSubmit = await submitDraft(coachPage, "日报", 1)
    expect(revisionSubmit.status).toBe("revision_pending_review")

    for (const page of [studentPage, guardianOnePage, guardianTwoPage]) {
      await expectPublished(page, correctedBody, 2)
      await expect(page.getByText(revisionBody)).toHaveCount(0)
    }
    await reviewReport(reviewerPage, draft.report_id, "approved")
    for (const page of [studentPage, guardianOnePage, guardianTwoPage]) {
      await expectPublished(page, revisionBody, 3)
      await expect(page.getByText(correctedBody)).toHaveCount(0)
    }

    await openAuthorTarget(coachPage, COHORT_21)
    await selectKind(coachPage, "daily")
    await slot(coachPage, "日报", 1)
      .getByRole("button", { name: "查看日报第 1 期版本" })
      .click()
    await expect(
      coachPage.getByRole("region", { name: "版本 3 与版本 2 对比" }),
    ).toBeVisible()
    await expect(
      coachPage.getByRole("article", { name: "教学评语版本 1" }),
    ).toContainText(rejectionReason)

    const authorizedPage = await authorizedAssistant.newPage()
    await openAuthorTarget(authorizedPage, COHORT_21)
    await selectKind(authorizedPage, "daily")
    const assistantDraft = await saveDraft(
      authorizedPage,
      "日报",
      2,
      assistantBody,
    )
    const loadedPrivateDialog = await openEditor(authorizedPage, "日报", 2)
    await expect(loadedPrivateDialog.getByLabel("评语正文")).toHaveValue(assistantBody)

    const assistantRevoked = await admin.delete(
      `/api/v1/accounts/${AUTHORIZED_ASSISTANT_ACCOUNT_ID}/permission-overrides/${AUTHORIZED_ASSISTANT_OVERRIDE_ID}`,
      { headers: { "X-CSRF-Token": csrfToken } },
    )
    expect(assistantRevoked.status(), await assistantRevoked.text()).toBe(204)
    const assistantPrivateRead = await authorizedAssistant.request.get(
      `/api/v1/evaluations/reports/${assistantDraft.report_id}`,
    )
    expect(assistantPrivateRead.status()).toBe(403)
    expect((await assistantPrivateRead.json()).code).toBe("evaluation_operation_denied")
    await refocus(authorizedPage)
    await expect(authorizedPage).toHaveURL(/\/evaluations\/?$/)
    await expect(loadedPrivateDialog).toHaveCount(0)
    await expect(authorizedPage.getByText(assistantBody)).toHaveCount(0)

    const unauthorizedPage = await unauthorizedAssistant.newPage()
    await unauthorizedPage.goto("/evaluations/reports")
    await expect(unauthorizedPage).toHaveURL(/\/evaluations\/?$/)
    await expect(
      unauthorizedPage.getByRole("heading", { name: "教学评语工作台" }),
    ).toHaveCount(0)

    const guardianEnded = await admin.post(
      `/api/v1/people/${STUDENT_PERSON_ID}/guardians/${SECOND_GUARDIAN_RELATIONSHIP_ID}/end`,
      {
        headers: { "X-CSRF-Token": csrfToken },
        data: { reason: "E2E 教学评语即时撤权" },
      },
    )
    expect(guardianEnded.status(), await guardianEnded.text()).toBe(200)
    const guardianPrivateRead = await guardianTwo.request.get(
      `/api/v1/evaluations/students/${STUDENT_PERSON_ID}/published-reports`,
    )
    expect(guardianPrivateRead.status()).toBe(404)
    expect((await guardianPrivateRead.json()).code).toBe(
      "evaluation_resource_not_found",
    )
    await refocus(guardianTwoPage)
    await expect(guardianTwoPage.getByText(revisionBody)).toHaveCount(0)
    await expect(guardianTwoPage).toHaveURL(/\/learning\/?$/)
    await expect(
      guardianTwoPage.getByRole("region", { name: "暂无可查看学员" }),
    ).toBeVisible()
    await expectPublished(studentPage, revisionBody, 3)
    await expectPublished(guardianOnePage, revisionBody, 3)

    for (const page of [
      coachPage,
      reviewerPage,
      authorizedPage,
      unauthorizedPage,
      studentPage,
      guardianOnePage,
      guardianTwoPage,
    ]) {
      const text = await page.locator("body").innerText()
      expect(text).not.toMatch(/\b1[3-9]\d{9}\b/)
      expect(text).not.toMatch(/\b\d{17}[\dX]\b/i)
      expect(text).not.toContain(credentials.authorizedAssistant[1])
      expect(text).not.toContain(credentials.unauthorizedAssistant[1])
    }
  } finally {
    await admin.dispose()
    await Promise.all([
      coach.close(),
      reviewer.close(),
      authorizedAssistant.close(),
      unauthorizedAssistant.close(),
      student.close(),
      guardianOne.close(),
      guardianTwo.close(),
    ])
  }
})
