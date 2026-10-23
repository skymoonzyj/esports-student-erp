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
import { containsKnownPrivateValue } from "../src/testSupport/talentStoragePrivacy"
import { E2E_CREDENTIALS } from "./e2eCredentials"

type DynamicRaw = {
  selected_level: number
  outcomes: string[]
  measured_display_ms: number[]
  font_size_px: number
}

type SessionResult = {
  test_id: string
  engine_version: string
  submission_id: string
  score: number
  raw: DynamicRaw
  device: { input_kind: string; geometry: { width: number; height: number } | null }
}

type SessionView = {
  id: string
  status: string
  results: SessionResult[]
  report_id: string | null
  rules: { catalog_version: string; test_ids: string[] }
}

type ContextView = {
  active_session_id: string | null
  catalog: { catalog_version: string; test_ids: string[] }
}

const evidenceByPage = new WeakMap<Page, BrowserEvidence>()
const phaseByPage = new WeakMap<Page, string>()
const keypadStateByPage = new WeakMap<Page, {
  count: number
  visible: boolean
  enabled: boolean
  selectedLevel: string | null
  maxLength: number
  valueLength: number
  box: { width: number; height: number } | null
}>()
const KNOWN_SYNTHETIC_PRIVATE_VALUES = new Set(["00000", "000000"])

test.use({ trace: "off", screenshot: "off", video: "off" })

test.beforeEach(async ({ page }) => {
  phaseByPage.set(page, "before_each")
  const evidence = createBrowserEvidence()
  evidenceByPage.set(page, evidence)
  page.on("console", (message) => recordConsoleCategory(evidence, message.type()))
  page.on("pageerror", () => recordPageError(evidence))
  page.on("requestfailed", (request) => recordRequestFailure(evidence, request.url()))
  await page.addInitScript(() => {
    Math.random = () => 0
  })
  await page.clock.install()
})

test.afterEach(async ({ page }, testInfo: TestInfo) => {
  const directory = process.env.TALENT_E2E_ARTIFACT_DIR
  if (!directory) return
  const radios = page.locator('input[name="dynamic-vision-level"]')
  const selectedLevel = await page.evaluate(() =>
    document.querySelector<HTMLInputElement>(
      'input[name="dynamic-vision-level"]:checked',
    )?.value ?? null
  ).catch(() => null)
  const controlState = {
    radioCount: await radios.count().catch(() => 0),
    selectedLevel,
    enabledRadioCount: await radios.evaluateAll((items) =>
      items.filter((item) => !(item as HTMLInputElement).disabled).length
    ).catch(() => 0),
    startVisible: await page.getByRole("button", { name: "开始测试" }).isVisible().catch(() => false),
  }
  await mkdir(directory, { recursive: true })
  await writeFile(
    join(directory, `${testInfo.project.name}-dynamic-browser-events.json`),
    serializeBrowserEvidence(
      testInfo.status,
      testInfo.expectedStatus,
      evidenceByPage.get(page) ?? createBrowserEvidence(),
    ),
    "utf8",
  )
  await writeFile(
    join(directory, `${testInfo.project.name}-dynamic-safe-phase.json`),
    `${JSON.stringify({
      status: testInfo.status,
      phase: phaseByPage.get(page) ?? "unknown",
      keypad: keypadStateByPage.get(page) ?? null,
      controls: controlState,
    }, null, 2)}\n`,
    "utf8",
  )
})

async function activate(target: Locator, touch: boolean, timeout?: number): Promise<void> {
  if (touch) {
    await target.tap({ timeout })
  } else {
    await target.click({ timeout })
  }
}

async function login(page: Page): Promise<void> {
  await page.goto("/login")
  await page.getByLabel("手机号或邮箱", { exact: true })
    .fill(E2E_CREDENTIALS.student.identifier)
  await page.getByLabel("密码", { exact: true }).fill(E2E_CREDENTIALS.student.password)
  await page.getByRole("button", { name: "登录" }).click()
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
}

async function getSession(page: Page, sessionId: string): Promise<SessionView> {
  const response = await page.request.get(`/api/v1/talent-tests/sessions/${sessionId}`)
  expect(response.status()).toBe(200)
  expect(response.headers()["cache-control"]).toBe("no-store, private")
  return response.json() as Promise<SessionView>
}

async function expectNoDynamicResult(page: Page, sessionId: string): Promise<void> {
  const session = await getSession(page, sessionId)
  expect(session.results.filter((result) => result.test_id === "dynamic-vision")).toHaveLength(0)
  expect(session.report_id).toBeNull()
}

async function enterZeroAnswer(page: Page, digitCount: number, touch: boolean): Promise<void> {
  const input = page.getByRole("textbox", { name: "输入数字" })
  phaseByPage.set(page, "answer_input_visibility")
  await expect(input).toBeVisible()
  const activeElementKind = async (): Promise<string> => page.evaluate(() => {
    if (document.activeElement instanceof HTMLInputElement) return "input"
    if (document.activeElement instanceof HTMLButtonElement) return "button"
    if (document.activeElement === document.body) return "body"
    return "other"
  })
  phaseByPage.set(page, `answer_initial_focus_${await activeElementKind()}`)
  if (touch) {
    phaseByPage.set(page, "answer_tap_input")
    await input.tap()
  } else {
    phaseByPage.set(page, "answer_shift_tab")
    await page.keyboard.press("Shift+Tab")
    phaseByPage.set(page, `answer_after_shift_tab_${await activeElementKind()}`)
    phaseByPage.set(page, "answer_tab")
    await page.keyboard.press("Tab")
  }
  phaseByPage.set(page, `answer_focus_assertion_${await activeElementKind()}`)
  await expect(input).toBeFocused()
  await expect(input).toHaveAttribute("inputmode", "numeric")
  phaseByPage.set(page, "answer_focus_verified")
  for (let index = 0; index < digitCount; index += 1) {
    const key = page.getByRole("button", { name: "数字 0", exact: true })
    const box = await key.boundingBox()
    const selectedLevel = await page.locator(
      'input[name="dynamic-vision-level"]:checked',
    ).getAttribute("value")
    keypadStateByPage.set(page, {
      count: await key.count(),
      visible: await key.isVisible(),
      enabled: await key.isEnabled(),
      selectedLevel,
      maxLength: await input.getAttribute("maxlength").then((value) => Number(value)),
      valueLength: await input.inputValue().then((value) => value.length),
      box: box ? { width: box.width, height: box.height } : null,
    })
    phaseByPage.set(page, `answer_keypad_${index + 1}_before`)
    await activate(key, touch, 5_000)
    phaseByPage.set(page, `answer_keypad_${index + 1}_after`)
  }
  phaseByPage.set(page, "answer_value_assertion")
  await expect(input).toHaveValue("0".repeat(digitCount))
  phaseByPage.set(page, "answer_verified")
}

function containsForbiddenAnswerData(value: unknown): boolean {
  const forbidden = /^(answer|answers|digit|digits|stimulus|target_value|entered_value)$/i
  const visit = (current: unknown): boolean => {
    if (Array.isArray(current)) return current.some(visit)
    if (current === null || typeof current !== "object") return false
    return Object.entries(current).some(([key, nested]) => forbidden.test(key) || visit(nested))
  }
  return visit(value)
}

function containsForbiddenPendingData(value: unknown): boolean {
  if (containsForbiddenAnswerData(value)) return true
  const forbidden = /^(score|result_payload)$/i
  const visit = (current: unknown): boolean => {
    if (Array.isArray(current)) return current.some(visit)
    if (current === null || typeof current !== "object") return false
    return Object.entries(current).some(([key, nested]) => forbidden.test(key) || visit(nested))
  }
  return visit(value)
}

async function sessionStorageSnapshot(page: Page): Promise<Record<string, unknown>> {
  return page.evaluate(() => Object.fromEntries(
    Object.entries(sessionStorage).map(([key, raw]) => {
      try {
        return [key, JSON.parse(raw) as unknown]
      } catch {
        return [key, raw]
      }
    }),
  ))
}

async function expectNoDynamicPendingStorage(
  page: Page,
  sessionId: string,
): Promise<void> {
  const stored = await sessionStorageSnapshot(page)
  expect(
    Object.keys(stored).filter((key) =>
      key.includes(":pending:") && key.endsWith(`:${sessionId}:dynamic-vision`)
    ),
  ).toEqual([])
  expect(containsForbiddenPendingData(stored)).toBe(false)
  expect(containsKnownPrivateValue(stored, KNOWN_SYNTHETIC_PRIVATE_VALUES)).toBe(false)
}

async function readExactDynamicPendingStorage(
  page: Page,
  sessionId: string,
): Promise<Record<string, unknown>> {
  const stored = await sessionStorageSnapshot(page)
  const matches = Object.entries(stored).filter(([key]) =>
    key.includes(":pending:") && key.endsWith(`:${sessionId}:dynamic-vision`)
  )
  expect(matches).toHaveLength(1)
  const match = matches[0]
  if (!match) throw new Error("dynamic pending storage missing")
  expect(match[0]).toMatch(
    /^erp:talent-tests:pending:[0-9a-f-]+:[0-9a-f-]+:dynamic-vision$/i,
  )
  const envelope = match[1]
  if (envelope === null || typeof envelope !== "object" || Array.isArray(envelope)) {
    throw new Error("dynamic pending envelope invalid")
  }
  expect(Object.keys(envelope).sort()).toEqual(["savedAt", "value"])
  expect(typeof (envelope as Record<string, unknown>).savedAt).toBe("number")
  const value = (envelope as Record<string, unknown>).value
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("dynamic pending value invalid")
  }
  const submission = value as Record<string, unknown>
  expect(Object.keys(submission).sort()).toEqual([
    "background_interrupted",
    "device",
    "duration_ms",
    "engine_version",
    "locale",
    "raw",
    "submission_id",
    "test_id",
  ])
  expect(Object.keys(submission.raw as Record<string, unknown>).sort()).toEqual([
    "font_size_px",
    "measured_display_ms",
    "outcomes",
    "selected_level",
  ])
  expect(Object.keys(submission.device as Record<string, unknown>).sort()).toEqual([
    "geometry",
    "input_kind",
    "viewport_height",
    "viewport_width",
  ])
  const geometry = (submission.device as Record<string, unknown>).geometry
  expect(Object.keys(geometry as Record<string, unknown>).sort()).toEqual([
    "height",
    "target_diameter",
    "width",
  ])
  expect(containsForbiddenPendingData(stored)).toBe(false)
  expect(containsKnownPrivateValue(stored, KNOWN_SYNTHETIC_PRIVATE_VALUES)).toBe(false)
  return submission
}

test("动态视力在五视口安全完成、真实中断并以原提交键恢复", async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  const touch = testInfo.project.name === "touch-mobile-390"
  if (touch) {
    const capability = await page.evaluate(() => ({
      coarse: matchMedia("(pointer: coarse)").matches,
      maxTouchPoints: navigator.maxTouchPoints,
    }))
    expect(capability.maxTouchPoints).toBeGreaterThan(0)
    expect(capability.coarse).toBe(true)
  }

  await login(page)
  phaseByPage.set(page, "logged_in")
  const contextResponse = await page.request.get("/api/v1/talent-tests/context")
  expect(contextResponse.status()).toBe(200)
  const seededContext = (await contextResponse.json()) as ContextView
  expect(seededContext.catalog.catalog_version).toBe("talent-training-v2")
  expect(seededContext.catalog.test_ids).toHaveLength(8)
  if (!seededContext.active_session_id) throw new Error("legacy E2E session was not seeded")

  const legacySessionId = seededContext.active_session_id
  const legacy = await getSession(page, legacySessionId)
  expect(legacy.rules.catalog_version).toBe("talent-training-v1")
  expect(legacy.rules.test_ids).toHaveLength(7)
  await page.goto(`/talent-tests/sessions/${legacySessionId}/dynamic-vision`)
  await expect(page.getByRole("alert")).toHaveText("本轮不包含该测试项目。")
  await expectNoDynamicResult(page, legacySessionId)
  phaseByPage.set(page, "legacy_rejected")

  await page.goto("/talent-tests")
  await expect(page.getByText("0/7", { exact: true })).toBeVisible()
  await activate(page.getByRole("button", { name: "放弃本轮" }), touch)
  const abandonDialog = page.getByRole("dialog", { name: "确认放弃本轮" })
  await activate(abandonDialog.getByRole("button", { name: "确认放弃" }), touch)
  await expect(page.getByRole("button", { name: "开始新一轮" })).toBeVisible()
  await activate(page.getByRole("button", { name: "开始新一轮" }), touch)
  await expect(page).toHaveURL(/\/talent-tests\/sessions\/[0-9a-f-]+\/reactiontime$/)
  const sessionId = page.url().match(/\/sessions\/([0-9a-f-]+)\/reactiontime$/)?.[1]
  if (!sessionId) throw new Error("current E2E session id missing")
  phaseByPage.set(page, "v2_started")

  await page.goto("/talent-tests")
  await activate(page.getByRole("link", { name: /动态视力/ }), touch)
  await expect(page.getByRole("heading", { name: "动态视力", exact: true })).toBeVisible()
  const levelFive = page.getByRole("radio", { name: /第 5 级/ })
  await activate(levelFive, touch)
  await expect(levelFive).toBeChecked()
  await expect(page.getByText(/最高.*100/)).toBeVisible()
  await activate(page.getByRole("button", { name: "开始测试" }), touch)
  await expect(page.getByRole("radio", { name: /第 4 级/ })).toBeDisabled()
  phaseByPage.set(page, "level_five_countdown")
  await page.clock.runFor(2_500)

  const stage = page.getByTestId("dynamic-vision-stage")
  const stimulus = page.getByTestId("dynamic-vision-stimulus")
  await expect(stimulus).toBeVisible()
  await expect(stimulus).toHaveAttribute("aria-hidden", "true")
  await expect(stimulus).toHaveText(/^\d{6}$/)
  const geometry = await stage.evaluate((element) => {
    const stageRect = element.getBoundingClientRect()
    const stimulusElement = element.querySelector<HTMLElement>("[data-testid='dynamic-vision-stimulus']")
    const stimulusRect = stimulusElement?.getBoundingClientRect()
    return {
      stageOverflow: element.scrollWidth > element.clientWidth,
      stimulusInside: stimulusRect !== undefined
        && stimulusRect.left >= stageRect.left
        && stimulusRect.right <= stageRect.right,
    }
  })
  expect(geometry).toEqual({ stageOverflow: false, stimulusInside: true })
  await page.clock.runFor(36)
  await enterZeroAnswer(page, 6, touch)
  phaseByPage.set(page, "level_five_answered")

  const originalViewport = page.viewportSize()
  if (!originalViewport) throw new Error("viewport missing")
  const keyboardViewport = {
    width: originalViewport.width,
    height: Math.max(520, originalViewport.height - 180),
  }
  await page.setViewportSize(keyboardViewport)
  await expect(page.getByRole("textbox", { name: "输入数字" })).toBeVisible()
  await expect(page.getByText("游戏区域尺寸已改变，本项已中止，请重新开始")).toHaveCount(0)

  const narrowerWidth = originalViewport.width >= 600
    ? 520
    : Math.max(320, originalViewport.width - 30)
  await page.setViewportSize({ width: narrowerWidth, height: keyboardViewport.height })
  phaseByPage.set(page, "geometry_interruption_wait")
  await expect(page.getByText("游戏区域尺寸已改变，本项已中止，请重新开始")).toBeVisible()
  await expectNoDynamicResult(page, sessionId)
  await expectNoDynamicPendingStorage(page, sessionId)
  phaseByPage.set(page, "geometry_interruption_verified")

  await page.setViewportSize(originalViewport)
  const selectedBeforeReselect = await page.locator(
    'input[name="dynamic-vision-level"]:checked',
  ).getAttribute("value")
  phaseByPage.set(page, `level_five_reselect_before_${selectedBeforeReselect ?? "none"}`)
  await activate(levelFive, touch)
  phaseByPage.set(
    page,
    `level_five_reselect_after_${await levelFive.isChecked() ? "checked" : "unchecked"}`,
  )
  await expect(levelFive).toBeChecked()
  phaseByPage.set(page, "level_five_reselect_verified")
  await activate(page.getByRole("button", { name: "开始测试" }), touch)
  phaseByPage.set(page, "hidden_interruption_dispatch")
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true })
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" })
    document.dispatchEvent(new Event("visibilitychange"))
    Object.defineProperty(document, "hidden", { configurable: true, get: () => false })
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" })
  })
  await expect(page.getByText("页面切换已中止本项，请重新开始")).toBeVisible()
  await expectNoDynamicResult(page, sessionId)
  await expectNoDynamicPendingStorage(page, sessionId)
  phaseByPage.set(page, "hidden_interruption_verified")

  let resultPosts = 0
  const submittedPayloads: Record<string, unknown>[] = []
  let responseLost = false
  await page.route("**/api/v1/talent-tests/sessions/*/results", async (route) => {
    if (route.request().method() !== "POST") {
      await route.continue()
      return
    }
    resultPosts += 1
    submittedPayloads.push(route.request().postDataJSON() as Record<string, unknown>)
    if (!responseLost) {
      responseLost = true
      const upstream = await route.fetch()
      expect(upstream.status()).toBe(201)
      await route.abort("connectionfailed")
      return
    }
    await route.continue()
  })
  phaseByPage.set(page, "lost_response_route_ready")

  const levelFour = page.getByRole("radio", { name: /第 4 级/ })
  phaseByPage.set(page, "level_four_select_click")
  await activate(levelFour, touch)
  phaseByPage.set(page, "level_four_select_checked_assertion")
  await expect(levelFour).toBeChecked()
  phaseByPage.set(page, "level_four_select_verified")
  await expect(page.getByText(/最高.*90/)).toBeVisible()
  await activate(page.getByRole("button", { name: "开始测试" }), touch)
  await expect(page.getByRole("radio", { name: /第 5 级/ })).toBeDisabled()
  phaseByPage.set(page, "level_four_started")

  for (let question = 1; question <= 5; question += 1) {
    phaseByPage.set(page, `question_${question}_countdown`)
    await page.clock.runFor(2_536)
    phaseByPage.set(page, `question_${question}_answer`)
    await enterZeroAnswer(page, 5, touch)
    phaseByPage.set(page, `question_${question}_submit`)
    await activate(page.getByRole("button", { name: "提交答案" }), touch)
    phaseByPage.set(page, `question_${question}_submitted`)
    if (question < 5) {
      await expect(page.getByText("回答正确")).toBeVisible()
      await activate(page.getByRole("button", { name: "下一题" }), touch)
      phaseByPage.set(page, `question_${question}_advanced`)
    }
    if (question === 4) await expectNoDynamicResult(page, sessionId)
  }

  phaseByPage.set(page, "pending_wait")
  await expect(page.getByText(/已保留原提交编号/)).toBeVisible()
  await expect(page.getByRole("button", { name: "手动重试" })).toBeVisible()
  const pendingSummary = page.getByLabel("动态视力结果摘要")
  await expect(pendingSummary).toBeVisible()
  await expect(pendingSummary.getByText("第 4 级，答对 5/5 题", { exact: true })).toBeVisible()
  await expect(
    pendingSummary.getByText("基础分 50 × 难度系数 1.8", { exact: true }),
  ).toBeVisible()
  await expect(
    pendingSummary.getByText("试算分：90 分（尚未保存，不计入完成进度）", { exact: true }),
  ).toBeVisible()
  await expect(page.getByRole("radio")).toHaveCount(0)
  await expect(page.getByRole("button", { name: "开始测试" })).toHaveCount(0)
  expect(resultPosts).toBe(1)
  expect(submittedPayloads).toHaveLength(1)
  const submittedPayload = submittedPayloads[0]
  if (!submittedPayload) throw new Error("dynamic submission payload missing")
  expect(containsForbiddenPendingData(submittedPayload)).toBe(false)
  expect(Object.keys(submittedPayload.raw as Record<string, unknown>).sort()).toEqual([
    "font_size_px",
    "measured_display_ms",
    "outcomes",
    "selected_level",
  ])
  const pendingPayload = await readExactDynamicPendingStorage(page, sessionId)
  expect(pendingPayload).toEqual(submittedPayload)
  expect((pendingPayload.raw as Record<string, unknown>).selected_level).toBe(4)

  const savedBeforeRetry = await getSession(page, sessionId)
  const dynamicBeforeRetry = savedBeforeRetry.results.filter(
    (result) => result.test_id === "dynamic-vision",
  )
  expect(dynamicBeforeRetry).toHaveLength(1)
  expect(dynamicBeforeRetry[0]).toMatchObject({
    engine_version: "dynamic-vision-v1",
    score: 90,
    raw: {
      selected_level: 4,
      outcomes: ["correct", "correct", "correct", "correct", "correct"],
    },
    device: { input_kind: touch ? "touch" : "mouse" },
  })
  expect(Object.keys(dynamicBeforeRetry[0]!.raw).sort()).toEqual([
    "font_size_px",
    "measured_display_ms",
    "outcomes",
    "selected_level",
  ])
  expect(containsForbiddenAnswerData(savedBeforeRetry)).toBe(false)
  expect(savedBeforeRetry.report_id).toBeNull()
  phaseByPage.set(page, "pending_verified")

  const replay = page.waitForResponse((response) =>
    response.request().method() === "POST"
      && new URL(response.url()).pathname === `/api/v1/talent-tests/sessions/${sessionId}/results`,
  )
  await activate(page.getByRole("button", { name: "手动重试" }), touch)
  phaseByPage.set(page, "retry_wait")
  expect((await replay).status()).toBe(200)
  await page.unroute("**/api/v1/talent-tests/sessions/*/results")
  await expect(page.getByText("服务器成绩：90 分", { exact: true })).toBeVisible()
  const savedSummary = page.getByLabel("动态视力结果摘要")
  await expect(savedSummary.getByText("第 4 级，答对 5/5 题", { exact: true })).toBeVisible()
  await expect(
    savedSummary.getByText("基础分 50 × 难度系数 1.8", { exact: true }),
  ).toBeVisible()
  await expect(
    savedSummary.getByText("服务器成绩：90 分", { exact: true }),
  ).toBeVisible()
  await expect(savedSummary.getByText(/试算分/)).toHaveCount(0)
  expect(resultPosts).toBe(2)
  expect(submittedPayloads).toHaveLength(2)
  expect(submittedPayloads[1]).toEqual(submittedPayloads[0])

  const savedAfterRetry = await getSession(page, sessionId)
  expect(savedAfterRetry.results.filter((result) => result.test_id === "dynamic-vision")).toHaveLength(1)
  expect(savedAfterRetry.report_id).toBeNull()
  expect(savedAfterRetry.results[0]?.submission_id).toBe(submittedPayload.submission_id)
  await expectNoDynamicPendingStorage(page, sessionId)
  phaseByPage.set(page, "saved_verified")
})
