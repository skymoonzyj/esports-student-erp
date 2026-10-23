import { mkdir, writeFile } from "node:fs/promises"
import { join } from "node:path"

import { expect, test } from "@playwright/test"
import type { APIRequestContext, Locator, Page, TestInfo } from "@playwright/test"

import { E2E_CREDENTIALS } from "./e2eCredentials"
import {
  createBrowserEvidence,
  recordConsoleCategory,
  recordPageError,
  recordRequestFailure,
  serializeBrowserEvidence,
  type BrowserEvidence,
} from "../src/testSupport/browserEvidence"
import { createRequestSession, logout } from "./helpers"

const STUDENT_PERSON_ID = "e1000000-0000-4000-8000-000000000007"
const ORDINARY_ADMIN_ACCOUNT_ID = "e1100000-0000-4000-8000-000000000002"
const TARGET_CLASS_ID = "e2120000-0000-4000-8000-000000000004"
const GUARDIAN_RELATIONSHIP_ID = "e2400000-0000-4000-8000-000000000005"
const TALENT_READ_PERMISSION_ID = "20000000-0000-4000-8000-000000000079"
const CATALOG_NAMES = [
  "反应速度",
  "瞄准速度",
  "顺序记忆",
  "数字记忆",
  "中文词语记忆",
  "黑猩猩测试",
  "视觉记忆",
  "动态视力",
] as const

const TEST_NAMES: Record<TestId, string> = {
  reactiontime: "反应速度",
  aim: "瞄准速度",
  sequence: "顺序记忆",
  "number-memory": "数字记忆",
  "verbal-memory": "中文词语记忆",
  chimp: "黑猩猩测试",
  memory: "视觉记忆",
  "dynamic-vision": "动态视力",
}

type TestId =
  | "reactiontime"
  | "aim"
  | "sequence"
  | "number-memory"
  | "verbal-memory"
  | "chimp"
  | "memory"
  | "dynamic-vision"

type PageResponse<T> = {
  items: T[]
  total: number
  page: number
  page_size: number
}

type ReportSummary = { id: string; session_id: string }

const evidenceByPage = new WeakMap<Page, BrowserEvidence>()

function artifactPath(name: string): string | null {
  const directory = process.env.TALENT_E2E_ARTIFACT_DIR
  return directory ? join(directory, name) : null
}

test.beforeEach(async ({ page }) => {
  const evidence = createBrowserEvidence()
  evidenceByPage.set(page, evidence)
  page.on("console", (message) => recordConsoleCategory(evidence, message.type()))
  page.on("pageerror", () => recordPageError(evidence))
  page.on("requestfailed", (request) => recordRequestFailure(evidence, request.url()))
})

test.use({ trace: "off", screenshot: "off", video: "off" })

test.afterEach(async ({ page }, testInfo: TestInfo) => {
  const directory = process.env.TALENT_E2E_ARTIFACT_DIR
  if (!directory) return
  await mkdir(directory, { recursive: true })
  await writeFile(
    join(directory, `${testInfo.project.name}-desktop-browser-events.json`),
    serializeBrowserEvidence(
      testInfo.status,
      testInfo.expectedStatus,
      evidenceByPage.get(page) ?? createBrowserEvidence(),
    ),
    "utf8",
  )
  // Keep only source line numbers, never assertion messages or tested values.
  const failureLines = testInfo.errors.flatMap((error) =>
    Array.from(error.stack?.matchAll(/talent-tests\.spec\.ts:(\d+):\d+/g) ?? [],
      (match) => Number(match[1])),
  )
  await writeFile(
    join(directory, `${testInfo.project.name}-desktop-safe-location.json`),
    `${JSON.stringify({ status: testInfo.status, failureLines: [...new Set(failureLines)] })}\n`,
    "utf8",
  )
})

async function capture(page: Page, name: string): Promise<void> {
  const path = artifactPath(name)
  if (!path) return
  await mkdir(process.env.TALENT_E2E_ARTIFACT_DIR!, { recursive: true })
  await page.screenshot({ path, fullPage: true })
}

async function press(locator: Locator): Promise<void> {
  await locator.click()
}

async function installControlledBrowser(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Math.random = () => 0
  })
  await page.clock.install()
}

async function loginWith(
  page: Page,
  credentials: { identifier: string; password: string },
): Promise<void> {
  await page.goto("/login")
  await page.getByLabel("手机号或邮箱", { exact: true }).fill(credentials.identifier)
  await page.getByLabel("密码", { exact: true }).fill(credentials.password)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
}

async function loginTalentStudent(page: Page): Promise<void> {
  await loginWith(page, E2E_CREDENTIALS.student)
}

async function startTalentSession(
  page: Page,
  firstTest: TestId = "reactiontime",
): Promise<string> {
  await page.goto("/talent-tests")
  await expect(page.getByRole("heading", { name: "天赋测试", exact: true })).toBeVisible()
  await expect(
    page.getByLabel("本人测试轮次").getByRole("heading", { level: 2 }),
  ).toHaveText([...CATALOG_NAMES])
  await expect(page.getByText("0/8", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: /^选择/ })).toHaveCount(8)
  await press(page.getByRole("button", { name: `选择${TEST_NAMES[firstTest]}` }))
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/[0-9a-f-]+/${firstTest}$`),
  )
  const sessionId = page.url().match(/\/sessions\/([0-9a-f-]+)\//)?.[1]
  if (!sessionId) throw new Error(`Talent session id missing from ${page.url()}`)
  return sessionId
}

async function selectGameFromList(
  page: Page,
  sessionId: string,
  testId: TestId,
): Promise<void> {
  await expect(page).toHaveURL(/\/talent-tests$/)
  await press(page.getByRole("link", { name: `选择${TEST_NAMES[testId]}` }))
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/${sessionId}/${testId}$`),
  )
}

async function continueToGame(
  page: Page,
  sessionId: string,
  testId: TestId,
): Promise<void> {
  await press(page.getByRole("link", { name: `继续下一项：${TEST_NAMES[testId]}` }))
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/${sessionId}/${testId}$`),
  )
}

async function returnToGameList(page: Page): Promise<void> {
  await press(page.getByRole("link", { name: "返回项目列表" }))
  await expect(page).toHaveURL(/\/talent-tests$/)
}

async function expectServerScore(page: Page): Promise<void> {
  await expect(
    page.getByText(/服务器成绩：\d+ 分/)
      .or(page.getByRole("heading", { name: "训练参考报告" })),
  ).toBeVisible()
}

async function expectReactionSavedSummary(page: Page, sessionId: string): Promise<void> {
  const response = await page.request.get(`/api/v1/talent-tests/sessions/${sessionId}`)
  expect(response.status()).toBe(200)
  const session = (await response.json()) as {
    results: Array<{ test_id: string; raw_value: number; score: number }>
  }
  const reaction = session.results.find((result) => result.test_id === "reactiontime")
  expect(reaction).toBeTruthy()
  const mean = page.getByText(`5 次平均响应：${reaction!.raw_value} 毫秒`, { exact: true })
  const score = page.getByText(`服务器成绩：${reaction!.score} 分`, { exact: true })
  await expect(mean).toBeVisible()
  await expect(score).toBeVisible()
  const scoreElement = await score.elementHandle()
  if (!scoreElement) throw new Error("Reaction score element missing")
  expect(await mean.evaluate((element, scoreNode) => Boolean(
    element.compareDocumentPosition(scoreNode as Node) & Node.DOCUMENT_POSITION_FOLLOWING
  ), scoreElement)).toBe(true)
  await scoreElement.dispose()
}

async function completeReactionThroughUI(
  page: Page,
  sessionId: string,
): Promise<void> {
  const stage = page.getByLabel("反应时间测试区域")
  await press(stage)
  for (let sample = 0; sample < 5; sample += 1) {
    await page.clock.runFor(2_600)
    await expect(page.getByRole("heading", { name: "点击！" })).toBeVisible()
    await page.clock.runFor(200)
    await press(stage)
    if (sample < 4) await press(stage)
  }

  await expectServerScore(page)
  await expectReactionSavedSummary(page, sessionId)
}

async function completeAimThroughUI(page: Page): Promise<void> {
  await press(page.getByRole("button", { name: "点击目标开始" }))
  for (let remaining = 30; remaining > 0; remaining -= 1) {
    await page.clock.runFor(200)
    await press(page.getByRole("button", {
      name: `击中目标，剩余 ${remaining} 个`,
    }))
  }
  await expectServerScore(page)
}

async function completeSequenceThroughUI(
  page: Page,
  options: { loseResponse?: boolean } = {},
): Promise<void> {
  let responseLost = false
  if (options.loseResponse) {
    await page.route("**/api/v1/talent-tests/sessions/*/results", async (route) => {
      if (responseLost || route.request().method() !== "POST") {
        await route.continue()
        return
      }
      responseLost = true
      const upstream = await route.fetch()
      expect(upstream.status()).toBe(201)
      await route.abort("connectionfailed")
    })
  }
  await press(page.getByRole("button", { name: "开始测试" }))
  await page.clock.runFor(1_500)
  await press(page.getByRole("button", { name: "第 2 格" }))
  if (options.loseResponse) {
    await expect(page.getByText(/保存失败，已保留原提交编号/)).toBeVisible()
    const replay = page.waitForResponse((response) =>
      response.request().method() === "POST"
      && /\/api\/v1\/talent-tests\/sessions\/[0-9a-f-]+\/results$/.test(
        new URL(response.url()).pathname,
      ),
    )
    await page.getByRole("button", { name: "手动重试" }).click()
    expect((await replay).status()).toBe(200)
    await page.unroute("**/api/v1/talent-tests/sessions/*/results")
  }
  await expectServerScore(page)
}

async function completeNumberThroughUI(page: Page): Promise<void> {
  await press(page.getByRole("button", { name: "开始数字记忆" }))
  await page.clock.runFor(1_900)
  await page.getByLabel("输入刚才显示的数字").fill("0")
  await press(page.getByRole("button", { name: "提交答案" }))
  await expectServerScore(page)
}

async function completeVerbalThroughUI(page: Page): Promise<void> {
  await press(page.getByRole("button", { name: "开始测试" }))
  await page.clock.runFor(10)
  await press(page.getByRole("button", { name: "见过", exact: true }))
  await press(page.getByRole("button", { name: "新词", exact: true }))
  await press(page.getByRole("button", { name: "新词", exact: true }))
  await expectServerScore(page)
}

async function completeChimpThroughUI(page: Page): Promise<void> {
  await press(page.getByRole("button", { name: "开始黑猩猩测试" }))
  await page.clock.runFor(10)
  for (let strike = 0; strike < 3; strike += 1) {
    await press(page.getByRole("button", { name: "数字 2", exact: true }))
    if (strike < 2) await press(page.getByRole("button", { name: "继续" }))
  }
  await expectServerScore(page)
}

async function visibleTargetIndexes(tiles: Locator): Promise<number[]> {
  return tiles.evaluateAll((elements) =>
    elements.flatMap((element, index) =>
      element.querySelector("[data-testid='visual-memory-tile-active']")
        ? [index]
        : [],
    ),
  )
}

async function completeVisualThroughUI(page: Page): Promise<void> {
  await press(page.getByRole("button", { name: "开始视觉记忆" }))
  const tiles = page.getByTestId("visual-memory-tile")
  for (let life = 3; life > 0; life -= 1) {
    await page.clock.runFor(life === 3 ? 600 : 1_050)
    const targets = new Set(await visibleTargetIndexes(tiles))
    expect(targets.size).toBe(3)
    await page.clock.runFor(life === 3 ? 1_300 : 1_200)
    const wrong = Array.from({ length: 9 }, (_, index) => index)
      .filter((index) => !targets.has(index))
      .slice(0, 3)
    for (const index of wrong) await press(tiles.nth(index))
  }
  await expectServerScore(page)
}

async function completeDynamicVisionThroughUI(page: Page): Promise<void> {
  await page.getByRole("radio", { name: /第 1 级/ }).check()
  await press(page.getByRole("button", { name: "开始测试" }))
  for (let question = 1; question <= 5; question += 1) {
    await page.clock.runFor(2_600)
    await press(page.getByRole("button", { name: "跳过本题" }))
    if (question < 5) await press(page.getByRole("button", { name: "下一题" }))
  }
  await expectServerScore(page)
}

async function completeGame(
  page: Page,
  sessionId: string,
  testId: Exclude<TestId, "reactiontime">,
  options: { loseResponse?: boolean } = {},
): Promise<void> {
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/${sessionId}/${testId}$`),
  )
  switch (testId) {
    case "aim":
      await completeAimThroughUI(page)
      break
    case "sequence":
      await completeSequenceThroughUI(page, options)
      break
    case "number-memory":
      await completeNumberThroughUI(page)
      break
    case "verbal-memory":
      await completeVerbalThroughUI(page)
      break
    case "chimp":
      await completeChimpThroughUI(page)
      break
    case "memory":
      await completeVisualThroughUI(page)
      break
    case "dynamic-vision":
      await completeDynamicVisionThroughUI(page)
      break
  }
}

async function completeRemainingSixThroughUI(
  page: Page,
  sessionId: string,
  options: { loseFinalResponse?: boolean } = {},
): Promise<void> {
  await selectGameFromList(page, sessionId, "memory")
  await completeGame(page, sessionId, "memory")
  await continueToGame(page, sessionId, "aim")
  await completeGame(page, sessionId, "aim")
  await returnToGameList(page)

  await selectGameFromList(page, sessionId, "verbal-memory")
  await completeGame(page, sessionId, "verbal-memory")
  await continueToGame(page, sessionId, "sequence")
  await completeGame(page, sessionId, "sequence", {
    loseResponse: options.loseFinalResponse,
  })
  await returnToGameList(page)

  await selectGameFromList(page, sessionId, "chimp")
  await completeGame(page, sessionId, "chimp")
  await continueToGame(page, sessionId, "number-memory")
  await completeGame(page, sessionId, "number-memory")
  await returnToGameList(page)
}

async function completeWholeSession(page: Page): Promise<string> {
  const sessionId = await startTalentSession(page, "memory")
  await completeGame(page, sessionId, "memory")
  await continueToGame(page, sessionId, "reactiontime")
  await completeReactionThroughUI(page, sessionId)
  for (const testId of [
    "aim",
    "sequence",
    "number-memory",
    "verbal-memory",
    "chimp",
    "dynamic-vision",
  ] as const) {
    await continueToGame(page, sessionId, testId)
    await completeGame(page, sessionId, testId)
  }
  await expect(page.getByRole("heading", { name: "训练参考报告" })).toBeVisible()
  return sessionId
}

async function apiJson<T>(
  api: APIRequestContext,
  path: string,
  status = 200,
): Promise<T> {
  const response = await api.get(path)
  expect(response.status()).toBe(status)
  expect(response.headers()["cache-control"]).toContain("no-store")
  return response.json() as Promise<T>
}

async function expectPrivateNotFound(
  api: APIRequestContext,
  path: string,
): Promise<void> {
  const response = await api.get(path)
  expect(response.status()).toBe(404)
  expect(response.headers()["cache-control"]).toBe("no-store, private")
  const body = (await response.json()) as { code: string }
  expect(body.code).toBe("talent_not_found")
}

test("八项保存可续接且最终报告没有中文打字", async ({ page }) => {
  test.setTimeout(120_000)
  await installControlledBrowser(page)
  await loginTalentStudent(page)

  const abandonedSessionId = await startTalentSession(page)
  const reaction = page.getByLabel("反应时间测试区域")
  await press(reaction)
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true })
    document.dispatchEvent(new Event("visibilitychange"))
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false })
  })
  await expect(page.getByText("页面切换已中止本项，请重新开始")).toBeVisible()
  await page.goto("/talent-tests")
  await page.getByRole("button", { name: "放弃本轮" }).click()
  const abandonDialog = page.getByRole("dialog", { name: "确认放弃本轮" })
  await abandonDialog.getByRole("button", { name: "确认放弃" }).click()
  await expect(page.getByRole("button", { name: "开始新一轮" })).toBeVisible()
  const abandoned = await page.request.get(
    `/api/v1/talent-tests/sessions/${abandonedSessionId}`,
  )
  expect(abandoned.status()).toBe(200)
  expect(((await abandoned.json()) as { status: string }).status).toBe("abandoned")

  const sessionId = await startTalentSession(page)
  await completeReactionThroughUI(page, sessionId)
  await capture(page, "desktop-reaction-navigation.png")
  await page.reload()
  await expectServerScore(page)
  await expectReactionSavedSummary(page, sessionId)
  await returnToGameList(page)
  await expect(page.getByText("1/8", { exact: true })).toBeVisible()
  await capture(page, "desktop-landing-progress.png")

  await logout(page)
  await loginWith(page, E2E_CREDENTIALS.guardian)
  await expectPrivateNotFound(
    page.request,
    `/api/v1/talent-tests/sessions/${sessionId}`,
  )
  await page.goto("/talent-tests")
  await expect(page.getByRole("heading", { name: "天赋测试", exact: true })).toBeVisible()
  await expect(page.getByText("服务器成绩", { exact: false })).toHaveCount(0)
  await expect(page.getByText("1/8", { exact: true })).toHaveCount(0)
  await logout(page)
  await loginTalentStudent(page)
  await page.goto("/talent-tests")
  await expect(page.getByText("1/8", { exact: true })).toBeVisible()

  await completeRemainingSixThroughUI(page, sessionId, { loseFinalResponse: true })
  await page.goto("/talent-tests")
  await expect(page.getByText("7/8", { exact: true })).toBeVisible()
  await expect(page.getByRole("link", { name: "查看E2E自助学员的报告" })).toHaveCount(0)
  const partialReports = await apiJson<PageResponse<ReportSummary>>(
    page.request,
    `/api/v1/talent-tests/reports?person_id=${STUDENT_PERSON_ID}&page=1&page_size=20`,
  )
  expect(partialReports.total).toBe(0)

  await selectGameFromList(page, sessionId, "dynamic-vision")
  await completeGame(page, sessionId, "dynamic-vision")
  await expect(page.getByRole("heading", { name: "训练参考报告" })).toBeVisible()
  await capture(page, "desktop-report.png")
  await expect(page.getByText("中文打字", { exact: true })).toHaveCount(0)
  const reportId = page.url().match(/\/talent-tests\/reports\/([0-9a-f-]+)$/)?.[1]
  if (!reportId) throw new Error(`Talent report id missing from ${page.url()}`)
  const oneReport = await apiJson<PageResponse<ReportSummary>>(
    page.request,
    `/api/v1/talent-tests/reports?person_id=${STUDENT_PERSON_ID}&page=1&page_size=20`,
  )
  expect(oneReport.total).toBe(1)
  expect(oneReport.items).toHaveLength(1)
  expect(oneReport.items[0].session_id).toBe(sessionId)
  const originalReport = await apiJson<Record<string, unknown>>(
    page.request,
    `/api/v1/talent-tests/reports/${reportId}`,
  )

  const sessions = await Promise.all([
    createRequestSession(
      E2E_CREDENTIALS.admin.identifier,
      E2E_CREDENTIALS.admin.password,
    ),
    createRequestSession(
      E2E_CREDENTIALS.guardian.identifier,
      E2E_CREDENTIALS.guardian.password,
    ),
    createRequestSession(
      E2E_CREDENTIALS.coach.identifier,
      E2E_CREDENTIALS.coach.password,
    ),
    createRequestSession(
      E2E_CREDENTIALS.life_teacher.identifier,
      E2E_CREDENTIALS.life_teacher.password,
    ),
    createRequestSession(
      E2E_CREDENTIALS.customer_service.identifier,
      E2E_CREDENTIALS.customer_service.password,
    ),
  ])
  const [admin, guardian, assignedCoach, unrelatedCoach, ordinaryAdmin] = sessions
  try {
    await apiJson(guardian.api, `/api/v1/talent-tests/reports/${reportId}`)
    await apiJson(assignedCoach.api, `/api/v1/talent-tests/reports/${reportId}`)
    await expectPrivateNotFound(
      unrelatedCoach.api,
      `/api/v1/talent-tests/reports/${reportId}`,
    )
    await expectPrivateNotFound(
      ordinaryAdmin.api,
      `/api/v1/talent-tests/reports/${reportId}`,
    )

    const granted = await admin.api.post(
      `/api/v1/accounts/${ORDINARY_ADMIN_ACCOUNT_ID}/permission-overrides`,
      {
        headers: { "X-CSRF-Token": admin.csrfToken },
        data: {
          permission_id: TALENT_READ_PERMISSION_ID,
          effect: "allow",
          scope_kind: "global",
          scope_id: null,
          grant_reason: "天赋测试 E2E 授权",
        },
      },
    )
    expect(granted.status()).toBe(201)
    await apiJson(ordinaryAdmin.api, `/api/v1/talent-tests/reports/${reportId}`)

    const forbiddenDelegation = await ordinaryAdmin.api.post(
      `/api/v1/accounts/${ORDINARY_ADMIN_ACCOUNT_ID}/permission-overrides/preview`,
      {
        headers: { "X-CSRF-Token": ordinaryAdmin.csrfToken },
        data: {
          permission_id: TALENT_READ_PERMISSION_ID,
          effect: "allow",
          scope_kind: "global",
          scope_id: null,
        },
      },
    )
    expect(forbiddenDelegation.status()).toBe(403)

    const denied = await admin.api.post(
      `/api/v1/accounts/${ORDINARY_ADMIN_ACCOUNT_ID}/permission-overrides`,
      {
        headers: { "X-CSRF-Token": admin.csrfToken },
        data: {
          permission_id: TALENT_READ_PERMISSION_ID,
          effect: "deny",
          scope_kind: "global",
          scope_id: null,
          grant_reason: "天赋测试 E2E 撤权",
        },
      },
    )
    expect(denied.status()).toBe(201)
    await expectPrivateNotFound(
      ordinaryAdmin.api,
      `/api/v1/talent-tests/reports/${reportId}`,
    )

    const endedGuardian = await admin.api.post(
      `/api/v1/people/${STUDENT_PERSON_ID}/guardians/${GUARDIAN_RELATIONSHIP_ID}/end`,
      {
        headers: { "X-CSRF-Token": admin.csrfToken },
        data: { reason: "天赋测试 E2E 监护撤权" },
      },
    )
    expect(endedGuardian.status()).toBe(200)
    await expectPrivateNotFound(
      guardian.api,
      `/api/v1/talent-tests/reports/${reportId}`,
    )

    const assignments = await admin.api.get(
      `/api/v1/classes/${TARGET_CLASS_ID}/coach-assignments`,
    )
    expect(assignments.status()).toBe(200)
    const assignment = ((await assignments.json()) as {
      items: Array<{ id: string; coach_person_id: string; ended_at: string | null }>
    }).items.find((item) =>
      item.coach_person_id === "e1000000-0000-4000-8000-000000000006"
      && item.ended_at === null,
    )
    expect(assignment).toBeTruthy()
    const endedCoach = await admin.api.post(
      `/api/v1/classes/${TARGET_CLASS_ID}/coach-assignments/${assignment!.id}/end`,
      {
        headers: { "X-CSRF-Token": admin.csrfToken },
        data: { reason: "天赋测试 E2E 教练撤任" },
      },
    )
    expect(endedCoach.status()).toBe(200)
    await expectPrivateNotFound(
      assignedCoach.api,
      `/api/v1/talent-tests/reports/${reportId}`,
    )
  } finally {
    await Promise.all(sessions.map((session) => session.api.dispose()))
  }

  await page.goto("/talent-tests")
  await completeWholeSession(page)
  const twoReports = await apiJson<PageResponse<ReportSummary>>(
    page.request,
    `/api/v1/talent-tests/reports?person_id=${STUDENT_PERSON_ID}&page=1&page_size=20`,
  )
  expect(twoReports.total).toBe(2)
  const unchangedReport = await apiJson<Record<string, unknown>>(
    page.request,
    `/api/v1/talent-tests/reports/${reportId}`,
  )
  expect(unchangedReport).toEqual(originalReport)
  expect(JSON.stringify(unchangedReport)).not.toContain("中文打字")
})
