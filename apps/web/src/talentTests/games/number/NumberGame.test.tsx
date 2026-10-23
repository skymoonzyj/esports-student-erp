import { act, createEvent, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import type { GameProps } from "../../contracts"
import { NumberGame } from "./NumberGame"

function tap(element: Element, pointerType: "mouse" | "touch" = "mouse") {
  const event = createEvent.pointerDown(element)
  Object.defineProperties(event, {
    button: { value: 0 },
    isPrimary: { value: true },
    pointerType: { value: pointerType },
  })
  fireEvent(element, event)
  fireEvent.click(element)
}

function revealAnswer() {
  act(() => {
    vi.advanceTimersByTime(1800)
  })
  return screen.getByRole("textbox", { name: "输入刚才显示的数字" })
}

describe("NumberGame", () => {
  let now = 0

  beforeEach(() => {
    vi.useFakeTimers()
    vi.spyOn(performance, "now").mockImplementation(() => now)
    vi.spyOn(Math, "random").mockReturnValue(0)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it("reports zero successful digits on a first-level failure without answer text", () => {
    const form = { current: null as HTMLFormElement | null }
    const onComplete = vi.fn<GameProps<"number-memory">["onComplete"]>(() => {
      if (form.current) fireEvent.submit(form.current)
    })
    render(<NumberGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    tap(screen.getByRole("button", { name: "开始数字记忆" }), "touch")
    expect(screen.getByText("1")).toBeInTheDocument()
    const input = revealAnswer()
    expect(input).toHaveAttribute("inputmode", "numeric")
    expect(input).toHaveAttribute("pattern", "[0-9]*")

    form.current = input.closest("form")
    fireEvent.change(input, { target: { value: "01" } })
    now = 2000
    fireEvent.keyDown(input, { key: "Enter", code: "Enter" })

    expect(onComplete).toHaveBeenCalledTimes(1)
    const payload = onComplete.mock.calls[0]?.[0]
    expect(payload).toBeDefined()
    if (!payload) throw new Error("expected a number-memory result")
    expect(payload).toEqual({
      test_id: "number-memory",
      raw: {
        correct_digits: 0,
        failed_digits: 1,
        display_duration_ms: 1800,
      },
      device: {
        input_kind: "touch",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: null,
      },
      duration_ms: 2000,
      background_interrupted: false,
    })
    expect(payload).not.toHaveProperty("answer")
    expect(payload).not.toHaveProperty("expected")
    expect(payload.raw).not.toHaveProperty("answer")
    expect(payload.raw).not.toHaveProperty("expected")

    fireEvent.keyDown(input, { key: "Enter", code: "Enter" })
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("advances after a correct answer and reports only the successful level", () => {
    const onComplete = vi.fn<GameProps<"number-memory">["onComplete"]>()
    render(<NumberGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    tap(screen.getByRole("button", { name: "开始数字记忆" }))
    let input = revealAnswer()
    fireEvent.change(input, { target: { value: "1" } })
    fireEvent.submit(input.closest("form")!)

    expect(screen.getByRole("button", { name: "下一题" })).toBeVisible()
    tap(screen.getByRole("button", { name: "下一题" }))
    expect(screen.getByText("10")).toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(2600)
    })

    input = screen.getByRole("textbox", { name: "输入刚才显示的数字" })
    fireEvent.change(input, { target: { value: "11" } })
    now = 5000
    const submit = screen.getByRole("button", { name: "提交答案" })
    tap(submit)
    tap(submit)

    expect(onComplete).toHaveBeenCalledTimes(1)
    const payload = onComplete.mock.calls[0]?.[0]
    expect(payload).toBeDefined()
    if (!payload) throw new Error("expected a number-memory result")
    expect(payload.raw).toEqual({
      correct_digits: 1,
      failed_digits: 2,
      display_duration_ms: 2600,
    })
  })

  it("interrupts a memorizing round when hidden and cancels its timer", () => {
    const onComplete = vi.fn<GameProps<"number-memory">["onComplete"]>()
    const onInterrupted = vi.fn<GameProps<"number-memory">["onInterrupted"]>()
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
    const view = render(
      <NumberGame onComplete={onComplete} onInterrupted={onInterrupted} />,
    )

    tap(screen.getByRole("button", { name: "开始数字记忆" }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    hidden.mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(onComplete).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
    expect(screen.getByRole("button", { name: "开始数字记忆" })).toBeVisible()

    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
  })

  it("keeps memorizing across callback rerenders and uses the latest interruption callback", () => {
    const onComplete = vi.fn<GameProps<"number-memory">["onComplete"]>()
    const firstInterrupted = vi.fn<GameProps<"number-memory">["onInterrupted"]>()
    const latestInterrupted = vi.fn<GameProps<"number-memory">["onInterrupted"]>()
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
    const view = render(
      <NumberGame onComplete={onComplete} onInterrupted={firstInterrupted} />,
    )

    tap(screen.getByRole("button", { name: "开始数字记忆" }))
    view.rerender(
      <NumberGame onComplete={onComplete} onInterrupted={latestInterrupted} />,
    )
    act(() => {
      vi.advanceTimersByTime(1800)
    })
    expect(
      screen.getByRole("textbox", { name: "输入刚才显示的数字" }),
    ).toBeVisible()

    hidden.mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))

    expect(firstInterrupted).not.toHaveBeenCalled()
    expect(latestInterrupted).toHaveBeenCalledTimes(1)
    expect(latestInterrupted).toHaveBeenCalledWith("hidden")
  })
})
