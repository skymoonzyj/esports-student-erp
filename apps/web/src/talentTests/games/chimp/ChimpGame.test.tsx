import { act, createEvent, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ChimpGame } from "./ChimpGame"

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

function press(
  element: Element,
  pointerType: "mouse" | "touch" | "pen" = "mouse",
) {
  const pointer = createEvent.pointerDown(element)
  Object.defineProperties(pointer, {
    button: { value: 0 },
    isPrimary: { value: true },
    pointerType: { value: pointerType },
  })
  fireEvent(element, pointer)
  fireEvent.click(element)
}

describe("ChimpGame", () => {
  let now = 100

  beforeEach(() => {
    now = 100
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 640 })
    ResizeObserverDouble.latest = null
    vi.stubGlobal("ResizeObserver", ResizeObserverDouble)
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.getAttribute("aria-label") === "八列五行数字方格"
        ? rectangle(320, 200)
        : rectangle(0, 0)
    })
    vi.spyOn(performance, "now").mockImplementation(() => now)
    vi.spyOn(Math, "random").mockReturnValue(0)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("reports a first-level third strike once without exposing the board", () => {
    const onComplete = vi.fn()
    render(<ChimpGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "touch")
    for (let strike = 1; strike <= 3; strike += 1) {
      if (strike === 3) now = 2_100
      press(screen.getByRole("button", { name: "数字 2" }), "touch")
      if (strike < 3) {
        expect(screen.getByText(`失误 ${strike} / 3`)).toBeVisible()
        press(screen.getByRole("button", { name: "继续" }), "touch")
      }
    }

    expect(onComplete).toHaveBeenCalledTimes(1)
    const payload = onComplete.mock.calls[0]?.[0]
    expect(payload).toEqual({
      test_id: "chimp",
      raw: {
        reached_count: 4,
        completed_count: 0,
        strikes: 3,
        end_reason: "strikes",
      },
      device: {
        input_kind: "touch",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: { width: 320, height: 200, target_diameter: 36 },
      },
      duration_ms: 2000,
      background_interrupted: false,
    })
    expect(payload).not.toHaveProperty("board")
    expect(payload.raw).not.toHaveProperty("board")

    fireEvent.click(screen.getByRole("heading", { name: "到达 4 个数字" }))
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("keeps level four visible and masks level five after its first correct cell", () => {
    render(<ChimpGame onComplete={vi.fn()} onInterrupted={vi.fn()} />)

    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "mouse")
    press(screen.getByRole("button", { name: "数字 1" }))
    expect(screen.getByRole("button", { name: "数字 2" })).toHaveTextContent("2")
    press(screen.getByRole("button", { name: "数字 2" }))
    press(screen.getByRole("button", { name: "数字 3" }))
    press(screen.getByRole("button", { name: "数字 4" }))

    expect(screen.getByText("数字 5")).toBeVisible()
    press(screen.getByRole("button", { name: "继续" }))
    press(screen.getByRole("button", { name: "数字 1" }))

    expect(screen.queryByRole("button", { name: "数字 2" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "方块 第1行第2列" })).toHaveTextContent("")
  })

  it("reports the reached level separately from the last completed level and mixed input", () => {
    const onComplete = vi.fn()
    render(<ChimpGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "mouse")
    for (let number = 1; number <= 4; number += 1) {
      press(screen.getByRole("button", { name: `数字 ${number}` }), "mouse")
    }
    press(screen.getByRole("button", { name: "继续" }), "touch")

    for (let strike = 0; strike < 3; strike += 1) {
      press(screen.getByRole("button", { name: "数字 2" }), "touch")
      if (strike < 2) press(screen.getByRole("button", { name: "继续" }), "touch")
    }

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0]?.[0]).toMatchObject({
      raw: {
        reached_count: 5,
        completed_count: 4,
        strikes: 3,
        end_reason: "strikes",
      },
      device: { input_kind: "mixed" },
    })
  })

  it("preserves source padding in touch and desktop target geometry", () => {
    const onComplete = vi.fn()
    const view = render(<ChimpGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "pen")
    for (let strike = 0; strike < 3; strike += 1) {
      press(screen.getByRole("button", { name: "数字 2" }), "pen")
      if (strike < 2) press(screen.getByRole("button", { name: "继续" }), "pen")
    }
    expect(onComplete.mock.calls[0]?.[0].device.geometry.target_diameter).toBe(36)

    view.unmount()
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.getAttribute("aria-label") === "八列五行数字方格"
        ? rectangle(720, 450)
        : rectangle(0, 0)
    })
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 })
    const desktopComplete = vi.fn()
    render(<ChimpGame onComplete={desktopComplete} onInterrupted={vi.fn()} />)
    press(screen.getByRole("button", { name: "开始黑猩猩测试" }))
    for (let strike = 0; strike < 3; strike += 1) {
      press(screen.getByRole("button", { name: "数字 2" }))
      if (strike < 2) press(screen.getByRole("button", { name: "继续" }))
    }

    expect(desktopComplete.mock.calls[0]?.[0].device.geometry).toEqual({
      width: 720,
      height: 450,
      target_diameter: 80,
    })
  })

  it("interrupts an active run on geometry change and disconnects on unmount", () => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    const view = render(
      <ChimpGame onComplete={onComplete} onInterrupted={onInterrupted} />,
    )

    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "mouse")
    const observer = ResizeObserverDouble.latest
    act(() => observer?.resize(319, 200))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("geometry_changed")
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "开始黑猩猩测试" })).toBeVisible()

    view.unmount()
    expect(observer?.disconnect).toHaveBeenCalledTimes(1)
  })

  it("interrupts when responsive padding changes and removes the resize listener", () => {
    const onInterrupted = vi.fn()
    const view = render(
      <ChimpGame onComplete={vi.fn()} onInterrupted={onInterrupted} />,
    )

    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "touch")
    Object.defineProperty(window, "innerWidth", { configurable: true, value: 900 })
    fireEvent(window, new Event("resize"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("geometry_changed")

    view.unmount()
    fireEvent(window, new Event("resize"))
    expect(onInterrupted).toHaveBeenCalledTimes(1)
  })

  it("interrupts an active run when hidden but leaves an idle game alone", () => {
    const onInterrupted = vi.fn()
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
    render(<ChimpGame onComplete={vi.fn()} onInterrupted={onInterrupted} />)

    hidden.mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))
    expect(onInterrupted).not.toHaveBeenCalled()

    hidden.mockReturnValue(false)
    press(screen.getByRole("button", { name: "开始黑猩猩测试" }), "mouse")
    hidden.mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(screen.getByRole("button", { name: "开始黑猩猩测试" })).toBeVisible()
  })
})
