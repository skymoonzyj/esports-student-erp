import { act, fireEvent, render as renderBase, screen } from "@testing-library/react"
import type { ReactElement } from "react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import type { GameProps, RuleSnapshot } from "./contracts"
import { GameHost } from "./GameHost"

function render(element: ReactElement) {
  return renderBase(element, { wrapper: MemoryRouter })
}

vi.mock("./games/dynamicVision/DynamicVisionGame", () => ({
  DynamicVisionGame: ({ onInterrupted }: GameProps<"dynamic-vision">) => (
    <button type="button" onClick={() => onInterrupted("geometry_changed")}>
      detect geometry change
    </button>
  ),
}))

vi.mock("./games/reaction/ReactionGame", () => ({
  ReactionGame: ({ onComplete, onInterrupted }: GameProps<"reactiontime">) => (
    <div>
      <button type="button" onClick={() => onInterrupted("hidden")}>interrupt</button>
      <button
        type="button"
        onClick={() => onComplete({
          test_id: "reactiontime",
          raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
          device: {
            input_kind: "mouse",
            viewport_width: 1440,
            viewport_height: 900,
            geometry: null,
          },
          duration_ms: 10_000,
          background_interrupted: false,
        })}
      >
        finish
      </button>
    </div>
  ),
}))

const accountId = "f1000000-0000-4000-8000-000000000001"
const sessionId = "f1000000-0000-4000-8000-000000000002"
const personId = "f1000000-0000-4000-8000-000000000003"

const rules: RuleSnapshot = {
  catalog_version: "talent-training-v1",
  scoring_version: "talent-score-v1",
  report_version: "talent-report-v1",
  test_ids: ["reactiontime", "aim", "sequence", "number-memory", "verbal-memory", "chimp", "memory"],
  tests: {
    reactiontime: { engine_version: "reactiontime-clone-v1", anchors: [500, 275, 225, 175, 150, 100], source_label: "训练", unit: "ms" },
    aim: { engine_version: "aim-clone-v1", anchors: [1000, 550, 450, 350, 300, 250], source_label: "训练", unit: "ms" },
    sequence: { engine_version: "sequence-clone-v1", anchors: [0, 8, 10, 18, 25, 35], source_label: "训练", unit: "level" },
    "number-memory": { engine_version: "number-memory-clone-v1", anchors: [0, 7, 9, 13, 16, 20], source_label: "训练", unit: "digits" },
    "verbal-memory": { engine_version: "verbal-memory-clone-v1", anchors: [0, 20, 50, 100, 200, 300], source_label: "训练", unit: "count" },
    chimp: { engine_version: "chimp-clone-v1", anchors: [0, 9, 11, 15, 20, 25], source_label: "训练", unit: "count" },
    memory: { engine_version: "memory-clone-v1", anchors: [0, 6, 10, 18, 25, 35], source_label: "训练", unit: "level" },
  },
}

function sessionResponse(): Response {
  return new Response(JSON.stringify({
    id: sessionId,
    person_id: personId,
    status: "in_progress",
    created_at: "2026-09-20T00:00:00Z",
    completed_at: null,
    abandoned_at: null,
    rules,
    results: [],
    report_id: null,
  }), { headers: { "Content-Type": "application/json" } })
}

function problemResponse(status: number): Response {
  return new Response(JSON.stringify({
    type: "about:blank",
    title: "Request failed",
    status,
    detail: "请求失败",
    code: status === 422 ? "validation_error" : "unavailable",
    request_id: `request-${status}`,
  }), { status, headers: { "Content-Type": "application/json" } })
}

afterEach(() => {
  vi.unstubAllGlobals()
  sessionStorage.clear()
})

it("shows saving, pending, and rejected recovery status ahead of a stale interruption", async () => {
  let finishFirstPost: ((response: Response) => void) | undefined
  let postCount = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    if (!String(input).endsWith("/results")) return sessionResponse()
    postCount += 1
    if (postCount === 1) {
      return new Promise<Response>((resolve) => { finishFirstPost = resolve })
    }
    return problemResponse(422)
  }))
  render(
    <GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="reactiontime" />,
  )

  fireEvent.click(await screen.findByRole("button", { name: "interrupt" }))
  expect(screen.getByText("页面切换已中止本项，请重新开始")).toBeVisible()
  fireEvent.click(screen.getByRole("button", { name: "finish" }))
  expect(await screen.findByText("正在保存结果…")).toBeVisible()

  await act(async () => finishFirstPost?.(problemResponse(503)))
  expect(await screen.findByText(/保存失败，已保留原提交编号/)).toBeVisible()

  fireEvent.click(screen.getByRole("button", { name: "手动重试" }))
  expect(await screen.findByText("结果无效，请重新完成本项。")).toBeVisible()
})

it("preserves a fresh interruption raised by the native control during the same click", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => {
    const loaded = await sessionResponse().json()
    loaded.rules.catalog_version = "talent-training-v2"
    loaded.rules.scoring_version = "talent-score-v2"
    loaded.rules.report_version = "talent-report-v2"
    loaded.rules.test_ids.push("dynamic-vision")
    loaded.rules.tests["dynamic-vision"] = {
      engine_version: "dynamic-vision-v1", kind: "dynamic_vision",
      source_label: "内部试行训练规则", unit: "count", base_points_per_correct: 10,
      levels: [
        { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
        { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
        { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
        { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
        { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
      ],
    }
    return new Response(JSON.stringify(loaded), { headers: { "Content-Type": "application/json" } })
  }))
  render(<GameHost accountId={accountId} personId={personId} sessionId={sessionId} testId="dynamic-vision" />)

  fireEvent.click(await screen.findByRole("button", { name: "detect geometry change" }))

  expect(screen.getByText("游戏区域尺寸已改变，本项已中止，请重新开始")).toBeVisible()
})
