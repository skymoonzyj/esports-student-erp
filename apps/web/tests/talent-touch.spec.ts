import { mkdir, writeFile } from "node:fs/promises"
import { join } from "node:path"

import { expect, test } from "@playwright/test"
import type { Locator, Page, TestInfo } from "@playwright/test"

import {
  createBrowserEvidence,
  recordConsoleCategory,
  recordPageError,
  recordRequestFailure,
  serializeBrowserEvidence,
  type BrowserEvidence,
} from "../src/testSupport/browserEvidence"
import { E2E_CREDENTIALS } from "./e2eCredentials"

type TouchTestId =
  | "reactiontime"
  | "aim"
  | "sequence"
  | "number-memory"
  | "verbal-memory"
  | "chimp"
  | "memory"
  | "dynamic-vision"

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

const TEST_NAMES: Record<TouchTestId, string> = {
  reactiontime: "反应速度",
  aim: "瞄准速度",
  sequence: "顺序记忆",
  "number-memory": "数字记忆",
  "verbal-memory": "中文词语记忆",
  chimp: "黑猩猩测试",
  memory: "视觉记忆",
  "dynamic-vision": "动态视力",
}

const evidenceByPage = new WeakMap<Page, BrowserEvidence>()

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
    join(directory, `${testInfo.project.name}-touch-browser-events.json`),
    serializeBrowserEvidence(
      testInfo.status,
      testInfo.expectedStatus,
      evidenceByPage.get(page) ?? createBrowserEvidence(),
    ),
    "utf8",
  )
})

async function capture(page: Page, name: string): Promise<void> {
  const directory = process.env.TALENT_E2E_ARTIFACT_DIR
  if (!directory) return
  await mkdir(directory, { recursive: true })
  await page.screenshot({ path: join(directory, name), fullPage: true })
}

async function tap(target: Locator): Promise<void> {
  await target.tap()
}

async function loginTouchStudent(page: Page): Promise<void> {
  await page.goto("/login")
  await page.getByLabel("手机号或邮箱", { exact: true })
    .fill(E2E_CREDENTIALS.student.identifier)
  await page.getByLabel("密码", { exact: true }).fill(E2E_CREDENTIALS.student.password)
  await page.getByRole("button", { name: "登录" }).tap()
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
}

async function startTouchSession(
  page: Page,
  firstTest: TouchTestId,
): Promise<string> {
  await expect(page.getByRole("button", { name: /^选择/ })).toHaveCount(8)
  await tap(page.getByRole("button", { name: `选择${TEST_NAMES[firstTest]}` }))
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/[0-9a-f-]+/${firstTest}$`),
  )
  const sessionId = page.url().match(/\/sessions\/([0-9a-f-]+)\//)?.[1]
  if (!sessionId) throw new Error(`Talent session id missing from ${page.url()}`)
  return sessionId
}

async function selectTouchGameFromList(
  page: Page,
  sessionId: string,
  testId: TouchTestId,
): Promise<void> {
  await expect(page).toHaveURL(/\/talent-tests$/)
  await tap(page.getByRole("link", { name: `选择${TEST_NAMES[testId]}` }))
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/${sessionId}/${testId}$`),
  )
}

async function continueToTouchGame(
  page: Page,
  sessionId: string,
  testId: TouchTestId,
): Promise<void> {
  await tap(page.getByRole("link", { name: `继续下一项：${TEST_NAMES[testId]}` }))
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/${sessionId}/${testId}$`),
  )
}

async function returnToTouchGameList(page: Page): Promise<void> {
  await tap(page.getByRole("link", { name: "返回项目列表" }))
  await expect(page).toHaveURL(/\/talent-tests$/)
}

async function expectTouchSaved(page: Page): Promise<void> {
  await expect(
    page.getByText(/服务器成绩：\d+ 分/)
      .or(page.getByRole("heading", { name: "训练参考报告" })),
  ).toBeVisible()
}

async function expectTouchReactionSavedSummary(page: Page, sessionId: string): Promise<void> {
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

async function finishReactionByTap(page: Page, sessionId: string): Promise<void> {
  const stage = page.getByLabel("反应时间测试区域")
  await tap(stage)
  for (let sample = 0; sample < 5; sample += 1) {
    await page.clock.runFor(2_600)
    await page.clock.runFor(200)
    await tap(stage)
    if (sample < 4) await tap(stage)
  }
  await expectTouchSaved(page)
  await expectTouchReactionSavedSummary(page, sessionId)
}

async function finishAimByTap(page: Page): Promise<void> {
  await tap(page.getByRole("button", { name: "点击目标开始" }))
  for (let remaining = 30; remaining > 0; remaining -= 1) {
    await page.clock.runFor(200)
    await tap(page.getByRole("button", { name: `击中目标，剩余 ${remaining} 个` }))
  }
  await expectTouchSaved(page)
}

async function finishSequenceByTap(page: Page): Promise<void> {
  await tap(page.getByRole("button", { name: "开始测试" }))
  await page.clock.runFor(1_500)
  await tap(page.getByRole("button", { name: "第 2 格" }))
  await expectTouchSaved(page)
}

async function finishNumberByTap(page: Page): Promise<void> {
  await tap(page.getByRole("button", { name: "开始数字记忆" }))
  await page.clock.runFor(1_900)
  await page.getByLabel("输入刚才显示的数字").fill("0")
  await tap(page.getByRole("button", { name: "提交答案" }))
  await expectTouchSaved(page)
}

async function finishVerbalByTap(page: Page): Promise<void> {
  await tap(page.getByRole("button", { name: "开始测试" }))
  await page.clock.runFor(10)
  await tap(page.getByRole("button", { name: "见过", exact: true }))
  await tap(page.getByRole("button", { name: "新词", exact: true }))
  await tap(page.getByRole("button", { name: "新词", exact: true }))
  await expectTouchSaved(page)
}

async function finishChimpByTap(page: Page): Promise<void> {
  await tap(page.getByRole("button", { name: "开始黑猩猩测试" }))
  await page.clock.runFor(10)
  for (let strike = 0; strike < 3; strike += 1) {
    await tap(page.getByRole("button", { name: "数字 2", exact: true }))
    if (strike < 2) await tap(page.getByRole("button", { name: "继续" }))
  }
  await expectTouchSaved(page)
}

async function touchTargets(tiles: Locator): Promise<Set<number>> {
  const indexes = await tiles.evaluateAll((elements) =>
    elements.flatMap((element, index) =>
      element.querySelector("[data-testid='visual-memory-tile-active']")
        ? [index]
        : [],
    ),
  )
  return new Set(indexes)
}

async function finishVisualByTap(page: Page): Promise<void> {
  await tap(page.getByRole("button", { name: "开始视觉记忆" }))
  const tiles = page.getByTestId("visual-memory-tile")
  for (let life = 3; life > 0; life -= 1) {
    await page.clock.runFor(life === 3 ? 600 : 1_050)
    const targets = await touchTargets(tiles)
    expect(targets.size).toBe(3)
    await page.clock.runFor(life === 3 ? 1_300 : 1_200)
    const misses = Array.from({ length: 9 }, (_, index) => index)
      .filter((index) => !targets.has(index))
      .slice(0, 3)
    for (const index of misses) await tap(tiles.nth(index))
  }
  await expectTouchSaved(page)
}

async function finishDynamicVisionByTap(page: Page): Promise<void> {
  await tap(page.getByRole("radio", { name: /第 1 级/ }))
  await tap(page.getByRole("button", { name: "开始测试" }))
  for (let question = 1; question <= 5; question += 1) {
    await page.clock.runFor(2_600)
    await tap(page.getByRole("button", { name: "跳过本题" }))
    if (question < 5) await tap(page.getByRole("button", { name: "下一题" }))
  }
  await expectTouchSaved(page)
}

async function finishTouchGame(
  page: Page,
  sessionId: string,
  testId: TouchTestId,
): Promise<void> {
  await expect(page).toHaveURL(
    new RegExp(`/talent-tests/sessions/${sessionId}/${testId}$`),
  )
  switch (testId) {
    case "reactiontime":
      await finishReactionByTap(page, sessionId)
      break
    case "aim":
      await finishAimByTap(page)
      break
    case "sequence":
      await finishSequenceByTap(page)
      break
    case "number-memory":
      await finishNumberByTap(page)
      break
    case "verbal-memory":
      await finishVerbalByTap(page)
      break
    case "chimp":
      await finishChimpByTap(page)
      break
    case "memory":
      await finishVisualByTap(page)
      break
    case "dynamic-vision":
      await finishDynamicVisionByTap(page)
      break
  }
}

test("真实触屏点击完成八项并显示设备适用限制", async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  test.skip(testInfo.project.name !== "touch-mobile-390", "requires the real touch project")
  await page.addInitScript(() => {
    Math.random = () => 0
  })
  await page.clock.install()
  await loginTouchStudent(page)
  const touchCapability = await page.evaluate(() => ({
    coarse: matchMedia("(pointer: coarse)").matches,
    maxTouchPoints: navigator.maxTouchPoints,
  }))
  expect(touchCapability.maxTouchPoints).toBeGreaterThan(0)
  expect(touchCapability.coarse).toBe(true)

  await page.goto("/talent-tests")
  await expect(page.getByRole("heading", { name: "天赋测试", exact: true })).toBeVisible()
  await expect(
    page.getByLabel("本人测试轮次").getByRole("heading", { level: 2 }),
  ).toHaveText([...CATALOG_NAMES])
  await expect(page.getByText("0/8", { exact: true })).toBeVisible()
  await capture(page, "touch-landing.png")
  const sessionId = await startTouchSession(page, "memory")
  await finishTouchGame(page, sessionId, "memory")
  await continueToTouchGame(page, sessionId, "reactiontime")
  await finishTouchGame(page, sessionId, "reactiontime")
  await capture(page, "touch-reaction-navigation.png")
  await continueToTouchGame(page, sessionId, "aim")
  await finishTouchGame(page, sessionId, "aim")
  await returnToTouchGameList(page)

  await selectTouchGameFromList(page, sessionId, "chimp")
  await finishTouchGame(page, sessionId, "chimp")
  for (const testId of [
    "sequence",
    "number-memory",
    "verbal-memory",
  ] as const) {
    await continueToTouchGame(page, sessionId, testId)
    await finishTouchGame(page, sessionId, testId)
  }
  await returnToTouchGameList(page)
  await expect(page.getByText("7/8", { exact: true })).toBeVisible()
  await expect(page.getByRole("link", { name: "查看E2E自助学员的报告" })).toHaveCount(0)

  await selectTouchGameFromList(page, sessionId, "dynamic-vision")
  await finishTouchGame(page, sessionId, "dynamic-vision")
  await expect(page.getByRole("heading", { name: "训练参考报告" })).toBeVisible()
  await capture(page, "touch-report.png")
  await expect(
    page.getByText("触屏输入，和鼠标结果的适用条件不同", { exact: true }),
  ).toHaveCount(8)
  await expect(page.getByText("需复测", { exact: true })).toHaveCount(0)
  await expect(page.getByText("中文打字", { exact: true })).toHaveCount(0)

  const reportId = page.url().match(/\/talent-tests\/reports\/([0-9a-f-]+)$/)?.[1]
  if (!reportId) throw new Error(`Talent report id missing from ${page.url()}`)
  const response = await page.request.get(`/api/v1/talent-tests/reports/${reportId}`)
  expect(response.status()).toBe(200)
  expect(response.headers()["cache-control"]).toBe("no-store, private")
  const report = (await response.json()) as {
    mixed_devices: boolean
    results: Array<{
      device: { input_kind: string }
      quality_flags: string[]
    }>
  }
  expect(report.mixed_devices).toBe(false)
  expect(report.results).toHaveLength(8)
  expect(report.results.map((result) => result.device.input_kind)).toEqual(
    Array.from({ length: 8 }, () => "touch"),
  )
  expect(report.results.flatMap((result) => result.quality_flags)).toEqual([])
})
