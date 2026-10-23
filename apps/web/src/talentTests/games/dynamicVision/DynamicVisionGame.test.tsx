import { act, fireEvent, render, screen } from "@testing-library/react"
import { StrictMode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { DynamicVisionGame } from "./DynamicVisionGame"

function rectangle(width: number, height: number): DOMRect {
  return {
    x: 0,
    y: 0,
    width,
    height,
    top: 0,
    right: width,
    bottom: height,
    left: 0,
    toJSON: () => ({}),
  }
}

class ResizeObserverDouble implements ResizeObserver {
  static latest: ResizeObserverDouble | null = null

  private observed: Element | null = null

  constructor(private readonly callback: ResizeObserverCallback) {
    ResizeObserverDouble.latest = this
  }

  observe(target: Element) {
    this.observed = target
  }

  unobserve() {
    this.observed = null
  }

  disconnect = vi.fn(() => {
    this.observed = null
  })

  resize(width: number, height: number) {
    if (!this.observed) throw new Error("ResizeObserver has no observed element")
    this.callback(
      [{ target: this.observed, contentRect: rectangle(width, height) } as ResizeObserverEntry],
      this,
    )
  }
}

async function advance(milliseconds: number): Promise<void> {
  await act(async () => {
    vi.advanceTimersByTime(milliseconds)
  })
}

async function advanceToAnswer(displayMilliseconds: number): Promise<void> {
  await advance(600)
  await advance(600)
  await advance(1300)
  await advance(displayMilliseconds)
}

describe("DynamicVisionGame", () => {
  let fontSize = 56

  beforeEach(() => {
    vi.useFakeTimers()
    fontSize = 56
    ResizeObserverDouble.latest = null
    vi.stubGlobal("ResizeObserver", ResizeObserverDouble)
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.getAttribute("data-testid") === "dynamic-vision-stage"
        ? rectangle(800, 420)
        : rectangle(0, 0)
    })
    const getComputedStyle = window.getComputedStyle.bind(window)
    vi.spyOn(window, "getComputedStyle").mockImplementation((element, pseudoElement) => {
      const style = getComputedStyle(element, pseudoElement)
      Object.defineProperty(style, "fontSize", {
        configurable: true,
        value: `${fontSize}px`,
      })
      return style
    })
    vi.spyOn(Math, "random").mockReturnValue(0)
    Object.defineProperty(document, "hidden", { configurable: true, value: false })
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("announces a successful start exactly once per attempt after locking the selected level", () => {
    const onAttemptStarted = vi.fn()
    render(<DynamicVisionGame onComplete={vi.fn()} onInterrupted={vi.fn()} onAttemptStarted={onAttemptStarted} />)
    fireEvent.click(screen.getByRole("radio", { name: /第 5 级/ }))
    expect(onAttemptStarted).not.toHaveBeenCalled()
    const start = screen.getByRole("button", { name: "开始测试" })
    act(() => {
      fireEvent.click(start)
      fireEvent.click(start)
    })
    expect(onAttemptStarted).toHaveBeenCalledTimes(1)
    expect(screen.getByRole("radio", { name: /第 5 级/ })).toBeChecked()
    expect(screen.getByRole("radio", { name: /第 5 级/ })).toBeDisabled()

    act(() => ResizeObserverDouble.latest?.resize(799, 420))
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    expect(onAttemptStarted).toHaveBeenCalledTimes(2)
  })

  it("does not announce a start when the display geometry is invalid", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(rectangle(0, 0))
    const onAttemptStarted = vi.fn()
    const onComplete = vi.fn()
    render(<DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} onAttemptStarted={onAttemptStarted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))

    expect(screen.getByRole("alert")).toHaveTextContent("无法可靠测量测试区域")
    expect(screen.getByRole("button", { name: "重新开始" })).toBeVisible()
    expect(onAttemptStarted).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
  })

  it("locks one level, hides before input, and keeps leading zeroes", async () => {
    const onComplete = vi.fn()
    render(<DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("radio", { name: /第 4 级/ }))
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    expect(screen.getByRole("radio", { name: /第 1 级/ })).toBeDisabled()
    await advance(600)
    expect(screen.getByTestId("dynamic-vision-stage")).toHaveTextContent("1")
    await advance(600)
    expect(screen.getByTestId("dynamic-vision-stage")).toBeEmptyDOMElement()
    await advance(1300)
    expect(screen.getByTestId("dynamic-vision-stimulus")).toHaveAttribute("aria-hidden", "true")
    expect(screen.queryByRole("textbox", { name: "输入数字" })).not.toBeInTheDocument()
    await advance(36)
    expect(screen.queryByTestId("dynamic-vision-stimulus")).not.toBeInTheDocument()
    fireEvent.change(screen.getByRole("textbox", { name: "输入数字" }), {
      target: { value: "00000" },
    })
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }))
    expect(screen.getByText("回答正确")).toBeInTheDocument()
    expect(onComplete).not.toHaveBeenCalled()
  })

  it("measures the committed visible DOM interval instead of copying the nominal duration", async () => {
    let measuredNow = 100
    vi.spyOn(performance, "now").mockImplementation(() => measuredNow)
    const onComplete = vi.fn()
    render(<DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("radio", { name: /第 4 级/ }))
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    await advance(600)
    await advance(600)
    measuredNow = 5000
    await advance(1300)
    measuredNow = 5050
    await advance(36)
    fireEvent.click(screen.getByRole("button", { name: "跳过本题" }))
    expect(screen.getByText("本题已跳过")).toBeVisible()

    for (let question = 2; question <= 5; question += 1) {
      fireEvent.click(screen.getByRole("button", { name: "下一题" }))
      await advance(600)
      await advance(600)
      measuredNow += 3000
      await advance(1300)
      measuredNow += 48
      await advance(36)
      fireEvent.click(screen.getByRole("button", { name: "跳过本题" }))
    }

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0]?.[0].raw.measured_display_ms).toEqual([
      50, 48, 48, 48, 48,
    ])
  })

  it("completes exactly five questions once and never persists digits or answers", async () => {
    const onComplete = vi.fn()
    render(<DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} />)
    fireEvent.click(screen.getByRole("radio", { name: /第 4 级/ }))
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))

    await advanceToAnswer(36)
    fireEvent.change(screen.getByRole("textbox", { name: "输入数字" }), { target: { value: "00000" } })
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }))

    fireEvent.click(screen.getByRole("button", { name: "下一题" }))
    await advanceToAnswer(36)
    fireEvent.change(screen.getByRole("textbox", { name: "输入数字" }), { target: { value: "99999" } })
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }))

    fireEvent.click(screen.getByRole("button", { name: "下一题" }))
    await advanceToAnswer(36)
    fireEvent.click(screen.getByRole("button", { name: "跳过本题" }))

    fireEvent.click(screen.getByRole("button", { name: "下一题" }))
    await advanceToAnswer(36)
    fireEvent.change(screen.getByRole("textbox", { name: "输入数字" }), { target: { value: "00000" } })
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }))

    fireEvent.click(screen.getByRole("button", { name: "下一题" }))
    await advanceToAnswer(36)
    const input = screen.getByRole("textbox", { name: "输入数字" })
    const submit = screen.getByRole("button", { name: "提交答案" })
    fireEvent.change(input, { target: { value: "00000" } })
    act(() => {
      fireEvent.click(submit)
      fireEvent.keyDown(input, { key: "Enter" })
      fireEvent.click(submit)
    })

    expect(onComplete).toHaveBeenCalledTimes(1)
    const result = onComplete.mock.calls[0]?.[0]
    expect(result.raw).toEqual({
      selected_level: 4,
      outcomes: ["correct", "incorrect", "skipped", "correct", "correct"],
      measured_display_ms: [36, 36, 36, 36, 36],
      font_size_px: 56,
    })
    expect(result.device.geometry).toEqual({ width: 800, height: 420, target_diameter: null })
    expect(result.duration_ms).toBeGreaterThanOrEqual(180)
    expect(JSON.stringify(result)).not.toMatch(/digits|answer|answers/)
    expect(JSON.stringify(sessionStorage)).not.toMatch(/00000|99999|digits|answer/)
    expect(screen.getByRole("heading", { name: "本项已完成" })).toBeVisible()
    expect(screen.getByText(/第 4 级，答对 3\/5 题/)).toBeVisible()
    expect(screen.queryByRole("button", { name: "下一题" })).not.toBeInTheDocument()
    expect(screen.queryByRole("radio")).not.toBeInTheDocument()
  })

  it("keeps an active attempt for equal geometry and interrupts once for real geometry change", () => {
    const onInterrupted = vi.fn()
    render(<DynamicVisionGame onComplete={vi.fn()} onInterrupted={onInterrupted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))

    act(() => ResizeObserverDouble.latest?.resize(800, 420))
    expect(onInterrupted).not.toHaveBeenCalled()
    expect(screen.getByRole("radio", { name: /第 1 级/ })).toBeDisabled()

    act(() => ResizeObserverDouble.latest?.resize(799, 420))
    act(() => ResizeObserverDouble.latest?.resize(798, 420))
    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("geometry_changed")
  })

  it("independently detects a font-only change without a ResizeObserver notification", async () => {
    const onInterrupted = vi.fn()
    render(<DynamicVisionGame onComplete={vi.fn()} onInterrupted={onInterrupted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))

    fontSize = 40
    await advance(16)

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("geometry_changed")
  })

  it("interrupts a backgrounded attempt once without completing it", () => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    render(<DynamicVisionGame onComplete={onComplete} onInterrupted={onInterrupted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))

    Object.defineProperty(document, "hidden", { configurable: true, value: true })
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" })
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"))
      document.dispatchEvent(new Event("visibilitychange"))
    })

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(onComplete).not.toHaveBeenCalled()
  })

  it("survives StrictMode effect replay and clears every timer on unmount", async () => {
    const onComplete = vi.fn()
    const view = render(
      <StrictMode>
        <DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} />
      </StrictMode>,
    )
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    await advance(600)
    await advance(600)
    await advance(1300)

    expect(screen.getByTestId("dynamic-vision-stimulus")).toBeInTheDocument()
    expect(onComplete).not.toHaveBeenCalled()
    const activeTimerCount = vi.getTimerCount()
    expect(activeTimerCount).toBeGreaterThan(0)
    view.unmount()
    expect(vi.getTimerCount()).toBe(0)
    await advance(1000)
    expect(onComplete).not.toHaveBeenCalled()
  })

  it("aborts an invalid zero-length visibility measurement and allows a fresh restart", async () => {
    vi.spyOn(performance, "now").mockReturnValue(1000)
    const onComplete = vi.fn()
    render(<DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    await advanceToAnswer(100)

    expect(screen.getByRole("alert")).toHaveTextContent("无法可靠测量显示时长")
    expect(screen.getByRole("button", { name: "重新开始" })).toBeVisible()
    expect(onComplete).not.toHaveBeenCalled()
  })
})
