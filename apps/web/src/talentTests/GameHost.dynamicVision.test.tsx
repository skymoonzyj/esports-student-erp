import { act, fireEvent, render as renderBase, renderHook, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useLayoutEffect, type ReactElement } from "react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { GameHost } from "./GameHost"
import type { DynamicVisionRule, ResultView, SessionView, Submission } from "./contracts"
import { putPending, readPending } from "./pending"
import { useTalentSession } from "./useTalentSession"

function render(element: ReactElement) {
  return renderBase(element, { wrapper: MemoryRouter })
}

const accountId = "f2000000-0000-4000-8000-000000000001"
const personId = "f2000000-0000-4000-8000-000000000002"
const sessionId = "f2000000-0000-4000-8000-000000000003"
const rule: DynamicVisionRule = {
  kind: "dynamic_vision", engine_version: "dynamic-vision-v1",
  source_label: "内部试行训练规则", unit: "count", base_points_per_correct: 10,
  levels: [
    { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
    { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
    { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
    { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
    { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
  ],
}
function session(overrides: Partial<SessionView> = {}): SessionView {
  return {
    id: sessionId, person_id: personId, status: "in_progress",
    created_at: "2026-09-21T00:00:00Z", completed_at: null, abandoned_at: null,
    report_id: null, results: [],
    rules: {
      catalog_version: "talent-training-v2", scoring_version: "talent-score-v2",
      report_version: "talent-report-v2", test_ids: ["dynamic-vision"],
      tests: { "dynamic-vision": rule },
    },
    ...overrides,
  }
}
function pending(): Submission {
  return {
    test_id: "dynamic-vision", engine_version: "dynamic-vision-v1", locale: "zh-CN",
    submission_id: "f2000000-0000-4000-8000-000000000004",
    raw: {
      selected_level: 4, outcomes: ["correct", "correct", "correct", "skipped", "skipped"],
      measured_display_ms: [36, 36, 36, 36, 36], font_size_px: 56,
    },
    device: { input_kind: "mouse", viewport_width: 1440, viewport_height: 900,
      geometry: { width: 800, height: 420, target_diameter: null } },
    duration_ms: 13000, background_interrupted: false,
  }
}
function response(value: unknown) {
  return new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })
}
function host(account = accountId, round = sessionId, person = personId) {
  return <GameHost accountId={account} personId={person} sessionId={round} testId="dynamic-vision" />
}
function assertTrial() {
  expect(screen.getByText("第 4 级，答对 3/5 题")).toBeVisible()
  expect(screen.getByText("基础分 30 × 难度系数 1.8")).toBeVisible()
  expect(screen.getByText("试算分：54 分（尚未保存，不计入完成进度）")).toBeVisible()
  expect(screen.queryByText(/服务器成绩/)).not.toBeInTheDocument()
  expect(screen.queryByRole("radio")).not.toBeInTheDocument()
}
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Object.defineProperty(document, "hidden", { configurable: true, value: false })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" })
  sessionStorage.clear()
})

it.each(["pointer", "keyboard"])("retains interruption during %s level selection and clears it only after successful start", async (gesture) => {
  let resize: (() => void) | undefined
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => response(session())))
  vi.stubGlobal("ResizeObserver", class {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(target: Element) {
      resize = () => this.callback([
        { target, contentRect: { width: 799, height: 420 } } as ResizeObserverEntry,
      ], this)
    }
    unobserve() {}
    disconnect() {}
  })
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 800, height: 420, x: 0, y: 0, top: 0, right: 800, bottom: 420, left: 0, toJSON: () => ({}),
  })
  const computedStyle = window.getComputedStyle.bind(window)
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => {
    const style = computedStyle(element)
    Object.defineProperty(style, "fontSize", { configurable: true, value: "56px" })
    return style
  })
  render(host())
  fireEvent.click(await screen.findByRole("button", { name: "开始测试" }))
  act(() => resize?.())
  const warning = screen.getByText("游戏区域尺寸已改变，本项已中止，请重新开始")
  const levelFive = screen.getByRole("radio", { name: /第 5 级/ })
  const user = userEvent.setup()
  if (gesture === "pointer") {
    await user.pointer({ target: levelFive, keys: "[MouseLeft>]" })
  } else {
    levelFive.focus()
    await user.keyboard("[Space>]")
  }
  // jsdom cannot measure the 62px browser shift: keeping the same visible
  // status node until activation prevents its layout space being removed.
  expect(warning).toBeVisible()
  expect(screen.getByRole("radio", { name: /第 1 级/ })).toBeChecked()
  if (gesture === "pointer") {
    await user.pointer({ target: levelFive, keys: "[/MouseLeft]" })
  } else {
    await user.keyboard("[/Space]")
  }
  expect(levelFive).toBeChecked()
  expect(warning).toBeVisible()
  fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
  expect(warning).not.toBeInTheDocument()
  const lockedLevel = screen.getByRole("radio", { name: /第 5 级/ })
  expect(lockedLevel).toBeChecked()
  expect(lockedLevel).toBeDisabled()
})

it.each([
  ["reactiontime", "pointer"], ["reactiontime", "keyboard"],
  ["aim", "pointer"], ["aim", "keyboard"],
] as const)("preserves %s custom %s press-start dismissal without requiring a native click", async (testId, gesture) => {
  const current = session()
  current.rules.test_ids = [testId]
  current.rules.tests[testId] = {
    engine_version: `${testId}-clone-v1`, anchors: [1000, 550, 450, 350, 300, 250],
    source_label: "训练", unit: "ms",
  }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => response(current)))
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  })
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 800, height: 420, x: 0, y: 0, top: 0, right: 800, bottom: 420, left: 0, toJSON: () => ({}),
  })
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId={testId} />)
  const target = async () => testId === "reactiontime"
    ? screen.findByTestId("reaction-game")
    : screen.findByRole("button", { name: "点击目标开始" })
  const activate = (element: HTMLElement) => {
    if (gesture === "pointer") {
      fireEvent.pointerDown(element, { isPrimary: true, button: 0, pointerType: "mouse" })
    } else {
      fireEvent.keyDown(element, { key: "Enter" })
    }
  }
  activate(await target())
  Object.defineProperty(document, "hidden", { configurable: true, value: true })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" })
  act(() => document.dispatchEvent(new Event("visibilitychange")))
  Object.defineProperty(document, "hidden", { configurable: true, value: false })
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" })
  const warning = screen.getByText("页面切换已中止本项，请重新开始")
  activate(await target())
  expect(warning).not.toBeInTheDocument()
  if (testId === "reactiontime") {
    expect(screen.getByRole("heading", { name: "等待变绿" })).toBeVisible()
  } else {
    expect(screen.getByLabelText("瞄准训练游戏区域")).toBeVisible()
    expect(screen.queryByRole("button", { name: "点击目标开始" })).not.toBeInTheDocument()
  }
})

// Removing the host-level summary must fail on the real fifth answer, not a mocked completion.
it("keeps an answer-free summary from fifth answer through saving, lost response, reload and server confirmation", async () => {
  let rejectSave: ((error: Error) => void) | undefined
  const posted: Submission[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    if (!String(url).endsWith("/results")) return response(session())
    const value = JSON.parse(String(init?.body)) as Submission
    posted.push(value)
    if (posted.length === 1) return new Promise<Response>((_resolve, reject) => { rejectSave = reject })
    const result: ResultView = {
      ...value, id: "f2000000-0000-4000-8000-000000000005", raw_value: 3,
      unit: "count", score: 53, grade: "medium", quality_flags: [],
      scoring_version: "talent-score-v2", created_at: "2026-09-21T00:01:00Z",
    }
    return response({ session: session({ results: [result] }), result })
  }))
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  })
  vi.spyOn(Math, "random").mockReturnValue(0)
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
    width: 800, height: 420, x: 0, y: 0, top: 0, right: 800, bottom: 420, left: 0, toJSON: () => ({}),
  })
  const computedStyle = window.getComputedStyle.bind(window)
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => {
    const style = computedStyle(element)
    Object.defineProperty(style, "fontSize", { configurable: true, value: "56px" })
    return style
  })
  const view = render(host())
  await screen.findByRole("button", { name: "开始测试" })
  vi.useFakeTimers()
  // Advancing the game clock must not create a future-dated cache on reload.
  vi.setSystemTime(Date.now() - 30_000)
  fireEvent.click(screen.getByRole("radio", { name: /第 4 级/ }))
  fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
  for (let question = 0; question < 5; question += 1) {
    for (const milliseconds of [600, 600, 1300, 36]) {
      await act(async () => { vi.advanceTimersByTime(milliseconds) })
    }
    if (question < 3) {
      fireEvent.change(screen.getByRole("textbox", { name: "输入数字" }), { target: { value: "00000" } })
      fireEvent.click(screen.getByRole("button", { name: "提交答案" }))
    } else {
      fireEvent.click(screen.getByRole("button", { name: "跳过本题" }))
    }
    if (question < 4) fireEvent.click(screen.getByRole("button", { name: "下一题" }))
  }
  assertTrial()
  expect(screen.getByText("正在保存结果…")).toBeVisible()
  expect(screen.queryByText(/正确数字/)).not.toBeInTheDocument()
  expect(Object.keys(posted[0].raw).sort()).toEqual([
    "font_size_px", "measured_display_ms", "outcomes", "selected_level",
  ])
  const cached = readPending(accountId, sessionId, "dynamic-vision", Date.now())
  expect(cached).toEqual(posted[0])
  expect(JSON.stringify(cached?.raw)).not.toMatch(/00000|digits|answer/)
  await act(async () => { rejectSave?.(new TypeError("lost response")) })
  assertTrial()
  expect(screen.getByRole("button", { name: "手动重试" })).toBeVisible()
  vi.useRealTimers()
  view.unmount()
  render(host())
  await screen.findByRole("button", { name: "手动重试" })
  assertTrial()
  fireEvent.click(screen.getByRole("button", { name: "手动重试" }))
  expect(await screen.findByText("服务器成绩：53 分")).toBeVisible()
  expect(screen.getByText("第 4 级，答对 3/5 题")).toBeVisible()
  expect(screen.getByText("基础分 30 × 难度系数 1.8")).toBeVisible()
  expect(screen.queryByText(/试算分/)).not.toBeInTheDocument()
  expect(posted).toEqual([posted[0], posted[0]])
  expect(sessionStorage.length).toBe(0)
})

it("derives a recovered trial from the frozen rule instead of the engine defaults", async () => {
  putPending(accountId, sessionId, pending(), Date.now())
  const current = session()
  current.rules.tests["dynamic-vision"] = {
    ...rule, levels: rule.levels.map((level) => ({ ...level, coefficient_tenths: 20 })) as DynamicVisionRule["levels"],
  }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => response(current)))
  render(host())
  expect(await screen.findByText("试算分：60 分（尚未保存，不计入完成进度）")).toBeVisible()
  expect(screen.getByText("基础分 30 × 难度系数 2.0")).toBeVisible()
})

it.each(["account", "session", "person"])("never exposes the prior pending result in even the first render after a %s change", async (changed) => {
  putPending(accountId, sessionId, pending(), Date.now())
  let reads = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => {
    if (++reads === 1) return response(session())
    return new Promise<Response>(() => {})
  }))
  const observations: ReturnType<typeof useTalentSession>[] = []
  const hook = renderHook(({ account, round, person }) => {
    const value = useTalentSession(account, person, round)
    useLayoutEffect(() => { observations.push(value) })
    return value
  }, { initialProps: { account: accountId, round: sessionId, person: personId } })
  await waitFor(() => expect(hook.result.current.saveState).toBe("pending"))
  expect(hook.result.current.pendingResult?.test_id).toBe("dynamic-vision")
  observations.length = 0
  hook.rerender({ account: changed === "account" ? "other-account" : accountId,
    round: changed === "session" ? "other-session" : sessionId,
    person: changed === "person" ? "other-person" : personId })
  expect(observations.length).toBeGreaterThan(0)
  for (const value of observations) {
    expect(value.session).toBeNull()
    expect(value.pendingResult).toBeNull()
  }
})
