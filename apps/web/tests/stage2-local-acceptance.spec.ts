import { expect, test } from "@playwright/test"
import type {
  Page,
  Request as BrowserRequest,
  Response as BrowserResponse,
} from "@playwright/test"

import {
  expectAboveMobileNavigation,
  expectHorizontallyInsideViewport,
  expectNoHorizontalOverflow,
  loginWithIdentifier,
  logout,
} from "./helpers"
import { e2eCredentialTuple } from "./e2eCredentials"


const SYSTEM_STUDENT_ID = "e1000000-0000-4000-8000-000000000004"
const REPORT_STUDENT_ID = "e2000000-0000-4000-8000-000000000011"

const credentials = {
  admin: e2eCredentialTuple("admin"),
  coach: e2eCredentialTuple("coach"),
  guardian: e2eCredentialTuple("guardian"),
  reportStudent: e2eCredentialTuple("second_student"),
} as const

type ObservedJsonResponse = {
  url: string
  body: Promise<string>
}

function observeScopedJsonResponses(page: Page): ObservedJsonResponse[] {
  const responses: ObservedJsonResponse[] = []
  const candidates = new Map<BrowserRequest, BrowserResponse>()
  page.on("response", (response) => {
    const pathname = new URL(response.url()).pathname
    const scoped = [
      "/api/v1/evaluations",
      "/api/v1/family",
      "/api/v1/portal",
    ].some((prefix) => pathname === prefix || pathname.startsWith(prefix + "/"))
    const contentType = response.headers()["content-type"] ?? ""
    if (scoped && /\bjson\b/i.test(contentType)) {
      candidates.set(response.request(), response)
    }
  })
  page.on("requestfinished", (request) => {
    const response = candidates.get(request)
    if (!response) return
    candidates.delete(request)
    responses.push({ url: response.url(), body: response.text() })
  })
  page.on("requestfailed", (request) => candidates.delete(request))
  return responses
}

function expectNoSensitiveText(value: string): void {
  expect(value).not.toMatch(/\b1[3-9]\d{9}\b/)
  expect(value).not.toMatch(/\b\d{17}[\dX]\b/i)
  expect(value).not.toMatch(/[\w.+-]+@example\.com/i)
  expect(value).not.toContain("/Users/")
  expect(value).not.toContain("/Volumes/")
  expect(value).not.toContain("stage2-v1-service-token")
  expect(value).not.toContain("stage2-web-service-token")
  expect(value).not.toContain("stage2-worker-service-token")
}

async function expectSafeJsonResponses(
  responses: readonly ObservedJsonResponse[],
): Promise<void> {
  const snapshot = [...responses]
  expect(snapshot.length).toBeGreaterThan(0)
  for (const response of snapshot) {
    expectNoSensitiveText(await response.body)
  }
}

async function expectLayout(page: Page, selector: string): Promise<void> {
  await expectNoHorizontalOverflow(page)
  await expectHorizontallyInsideViewport(page, selector)
  await expectAboveMobileNavigation(page, selector)
}

test("combined workspaces coexist with family projections and private responses", async ({
  page,
}) => {
  const workspacePages: Page[] = []
  const openWorkspace = async (): Promise<Page> => {
    const workspace = await page.context().newPage()
    workspacePages.push(workspace)
    return workspace
  }

  try {
    await loginWithIdentifier(page, ...credentials.coach)
    const reportPage = await openWorkspace()
    const reportResponses = observeScopedJsonResponses(reportPage)
    await reportPage.goto("/evaluations/reports")
    await expect(
      reportPage.getByRole("heading", { name: "教学评语工作台", exact: true }),
    ).toBeVisible()
    await expect(
      reportPage.getByRole("region", { name: "可填写学员名单" }),
    ).toContainText("E2E监护人第二学员")
    await expectLayout(reportPage, ".teachingReportPage")
    await expectSafeJsonResponses(reportResponses)

    const systemPage = await openWorkspace()
    const systemResponses = observeScopedJsonResponses(systemPage)
    await systemPage.goto("/evaluations/system")
    const frame = systemPage.frameLocator('iframe[title="系统评价"]')
    await expect(frame.getByText("已连接竞学 ERP")).toBeVisible()
    await expectLayout(systemPage, ".systemEvaluationPage")
    await expectSafeJsonResponses(systemResponses)
    await logout(page)

    await loginWithIdentifier(page, ...credentials.reportStudent)
    const surveyPage = await openWorkspace()
    const surveyResponses = observeScopedJsonResponses(surveyPage)
    await surveyPage.goto("/evaluations/surveys")
    await expect(
      surveyPage.getByRole("heading", { name: "营期评价", exact: true }),
    ).toBeVisible()
    await expect(
      surveyPage.getByRole("region", { name: "可填写的营期问卷" }),
    ).toContainText("E2E监护人第二学员")
    await expectLayout(surveyPage, ".campSurveyPage")
    await expectSafeJsonResponses(surveyResponses)
    await logout(page)

    await loginWithIdentifier(page, ...credentials.guardian)
    for (const studentId of [REPORT_STUDENT_ID, SYSTEM_STUDENT_ID]) {
      const learningPage = await openWorkspace()
      const learningResponses = observeScopedJsonResponses(learningPage)
      await learningPage.goto("/learning/" + studentId)
      await expect(
        learningPage.getByRole("region", { name: "系统评价历史" }),
      ).toBeVisible()
      await expect(
        learningPage.getByRole("region", { name: "教学评语" }),
      ).toBeVisible()
      await expectLayout(learningPage, ".portalLearningPage")
      await expectSafeJsonResponses(learningResponses)
    }
    await logout(page)

    await loginWithIdentifier(page, ...credentials.admin)
    const printPage = await openWorkspace()
    const printResponses = observeScopedJsonResponses(printPage)
    await printPage.goto("/evaluations/surveys/print")
    await expect(
      printPage.getByRole("heading", { name: "营期评价打印", exact: true }),
    ).toBeVisible()
    await expect(printPage.getByLabel("打印实名")).not.toBeChecked()
    await expectLayout(printPage, ".campSurveyPage")
    await expectSafeJsonResponses(printResponses)
  } finally {
    await Promise.all(workspacePages.map((workspace) => workspace.close()))
  }
})
