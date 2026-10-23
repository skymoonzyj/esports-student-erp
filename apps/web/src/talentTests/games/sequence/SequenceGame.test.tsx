import { act, createEvent, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { SequenceGame } from "./SequenceGame"

function primaryPointerDown(
  element: Element,
  pointerType: "mouse" | "touch" | "pen" = "mouse",
) {
  const event = createEvent.pointerDown(element)
  Object.defineProperties(event, {
    button: { value: 0 },
    isPrimary: { value: true },
    pointerType: { value: pointerType },
  })
  fireEvent(element, event)
}

function advanceShowing(sequenceLength: number) {
  act(() => {
    vi.advanceTimersByTime(600 + sequenceLength * (400 + 300))
  })
  expect(screen.getByText("按顺序点击方格")).toBeInTheDocument()
}

describe("SequenceGame", () => {
  let now = 100

  beforeEach(() => {
    now = 100
    vi.useFakeTimers()
    vi.spyOn(performance, "now").mockImplementation(() => now)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("keeps accepting rapid repeated tiles while feedback is visible and completes once", () => {
    vi.spyOn(Math, "random").mockReturnValue(0)
    const onComplete = vi.fn()
    render(<SequenceGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    advanceShowing(1)
    primaryPointerDown(screen.getByRole("button", { name: "第 1 格" }), "touch")

    advanceShowing(2)
    const repeatedTile = screen.getByRole("button", { name: "第 1 格" })
    primaryPointerDown(repeatedTile, "touch")
    expect(repeatedTile).toBeEnabled()
    primaryPointerDown(repeatedTile, "touch")

    expect(screen.getByText("关卡：3")).toBeInTheDocument()
    expect(onComplete).not.toHaveBeenCalled()

    advanceShowing(3)
    now = 4_321
    const wrongTile = screen.getByRole("button", { name: "第 9 格" })
    primaryPointerDown(wrongTile, "touch")
    primaryPointerDown(wrongTile, "touch")

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith({
      test_id: "sequence",
      raw: {
        reached_level: 3,
        completed_level: 2,
        failed_step: 1,
        end_reason: "wrong_tile",
      },
      device: {
        input_kind: "touch",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: null,
      },
      duration_ms: 4_221,
      background_interrupted: false,
    })
    expect(screen.getByRole("heading", { name: "到达第 3 关" })).toBeVisible()
    expect(screen.getByText("完整通过 2 关")).toBeVisible()
    expect(screen.queryByText("成功通过 3 关")).not.toBeInTheDocument()
  })

  it("ignores showing input and reports the exact failed response step", () => {
    vi.spyOn(Math, "random").mockReturnValueOnce(0).mockReturnValueOnce(0.99)
    const onComplete = vi.fn()
    render(<SequenceGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    const ninthTile = screen.getByRole("button", { name: "第 9 格" })
    primaryPointerDown(ninthTile, "pen")
    expect(onComplete).not.toHaveBeenCalled()

    advanceShowing(1)
    primaryPointerDown(screen.getByRole("button", { name: "第 1 格" }), "touch")
    advanceShowing(2)
    primaryPointerDown(screen.getByRole("button", { name: "第 1 格" }), "touch")
    now = 3_000
    primaryPointerDown(screen.getByRole("button", { name: "第 8 格" }), "pen")

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0]?.[0]).toMatchObject({
      raw: { reached_level: 2, completed_level: 1, failed_step: 2 },
      device: { input_kind: "mixed" },
    })
  })

  it("interrupts a hidden run and cancels every pending timer", () => {
    vi.spyOn(Math, "random").mockReturnValue(0)
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
    const view = render(
      <SequenceGame onComplete={onComplete} onInterrupted={onInterrupted} />,
    )

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    hidden.mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "开始测试" })).toBeVisible()
    expect(vi.getTimerCount()).toBe(0)

    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("cancels sequence and feedback timers when unmounted", () => {
    vi.spyOn(Math, "random").mockReturnValue(0)
    const view = render(
      <SequenceGame onComplete={vi.fn()} onInterrupted={vi.fn()} />,
    )

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    advanceShowing(1)
    primaryPointerDown(screen.getByRole("button", { name: "第 1 格" }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)

    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })
})
