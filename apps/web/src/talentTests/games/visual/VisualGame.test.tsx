import { act, createEvent, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { VisualGame } from "./VisualGame"
import { createRound } from "./engine"

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

function pointerDown(
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

function tile(index: number) {
  return screen.getByRole("button", { name: `第 ${index + 1} 格` })
}

function beginRound() {
  act(() => {
    vi.advanceTimersByTime(500 + 1000 + 300)
  })
  expect(screen.getByText("请选择刚才亮起的方格")).toBeVisible()
}

function wrongTiles(level: number) {
  const round = createRound(level, 3, () => 0)
  return Array.from({ length: round.gridSize ** 2 }, (_, index) => index).filter(
    (index) => !round.targets.includes(index),
  )
}

function loseLife(level: number, pointerType: "mouse" | "touch" | "pen" = "mouse") {
  for (const index of wrongTiles(level).slice(0, 3)) pointerDown(tile(index), pointerType)
}

function advanceFeedbackAndRound() {
  act(() => {
    vi.advanceTimersByTime(449)
  })
  expect(screen.getByText(/本关完成|扣除一条生命/)).toBeVisible()
  act(() => {
    vi.advanceTimersByTime(1)
  })
  expect(screen.getByText("请观察亮起的方格")).toBeVisible()
  beginRound()
}

describe("VisualGame", () => {
  let now = 100
  let boardSize = { width: 300, height: 300 }

  beforeEach(() => {
    now = 100
    boardSize = { width: 300, height: 300 }
    ResizeObserverDouble.latest = null
    vi.useFakeTimers()
    vi.stubGlobal("ResizeObserver", ResizeObserverDouble)
    vi.spyOn(performance, "now").mockImplementation(() => now)
    vi.spyOn(Math, "random").mockReturnValue(0)
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this.getAttribute("aria-label")?.includes("方格棋盘")) {
        return rectangle(boardSize.width, boardSize.height)
      }
      if (this.getAttribute("data-testid") === "visual-memory-tile") {
        return rectangle(96, 96)
      }
      return rectangle(0, 0)
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("shows the ERP title and internal-pilot scoring label", () => {
    render(<VisualGame onComplete={vi.fn()} onInterrupted={vi.fn()} />)

    expect(screen.getByRole("heading", { name: "视觉记忆" })).toBeInTheDocument()
    expect(screen.getByText(/内部试行训练目标/)).toBeInTheDocument()
  })

  it("preserves the 500/1000/300ms reveal timing and ignores showing input", () => {
    render(<VisualGame onComplete={vi.fn()} onInterrupted={vi.fn()} />)
    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))

    const targets = createRound(1, 3, () => 0).targets
    pointerDown(tile(targets[0]), "touch")
    expect(tile(targets[0])).toBeDisabled()

    act(() => vi.advanceTimersByTime(499))
    expect(screen.queryAllByTestId("visual-memory-tile-active")).toHaveLength(0)
    act(() => vi.advanceTimersByTime(1))
    expect(screen.getAllByTestId("visual-memory-tile-active")).toHaveLength(3)
    act(() => vi.advanceTimersByTime(999))
    expect(screen.getAllByTestId("visual-memory-tile-active")).toHaveLength(3)
    act(() => vi.advanceTimersByTime(1))
    expect(screen.queryAllByTestId("visual-memory-tile-active")).toHaveLength(0)
    expect(tile(targets[0])).toBeDisabled()
    act(() => vi.advanceTimersByTime(299))
    expect(tile(targets[0])).toBeDisabled()
    act(() => vi.advanceTimersByTime(1))
    expect(tile(targets[0])).toBeEnabled()
  })

  it("counts only accepted wrong tiles, permits the same tile next round and reports geometry", () => {
    const onComplete = vi.fn()
    render(<VisualGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))
    beginRound()
    const [sameWrong] = wrongTiles(1)
    pointerDown(tile(sameWrong), "touch")
    pointerDown(tile(sameWrong), "touch")
    for (const index of wrongTiles(1).slice(1, 3)) pointerDown(tile(index), "touch")

    advanceFeedbackAndRound()
    pointerDown(tile(sameWrong), "touch")
    for (const index of wrongTiles(1).slice(1, 3)) pointerDown(tile(index), "touch")

    advanceFeedbackAndRound()
    now = 2_000
    const terminalWrong = wrongTiles(1)
    pointerDown(tile(terminalWrong[0]), "touch")
    pointerDown(tile(terminalWrong[1]), "touch")
    const terminalTile = tile(terminalWrong[2])
    pointerDown(terminalTile, "touch")
    pointerDown(terminalTile, "touch")

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith({
      test_id: "memory",
      raw: {
        reached_level: 1,
        completed_level: 0,
        accepted_misses: 9,
        lives_used: 3,
        end_reason: "lives",
      },
      device: {
        input_kind: "touch",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: { width: 300, height: 300, target_diameter: 96 },
      },
      duration_ms: 1900,
      background_interrupted: false,
    })
    expect(screen.getByRole("heading", { name: "到达第 1 关" })).toBeVisible()
    expect(screen.getByText("完整通过 0 关")).toBeVisible()

    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("keeps accepted misses from a successful round and reports reached versus passed", () => {
    const onComplete = vi.fn()
    render(<VisualGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))
    beginRound()
    pointerDown(tile(wrongTiles(1)[0]), "mouse")
    for (const target of createRound(1, 3, () => 0).targets) pointerDown(tile(target), "mouse")

    advanceFeedbackAndRound()
    loseLife(2, "mouse")
    advanceFeedbackAndRound()
    loseLife(2, "mouse")
    advanceFeedbackAndRound()
    now = 5_000
    loseLife(2, "mouse")

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0]?.[0].raw).toEqual({
      reached_level: 2,
      completed_level: 1,
      accepted_misses: 10,
      lives_used: 3,
      end_reason: "lives",
    })
    expect(screen.getByRole("heading", { name: "到达第 2 关" })).toBeVisible()
    expect(screen.getByText("完整通过 1 关")).toBeVisible()
  })

  it("interrupts a hidden run once, cancels timers and resets", () => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
    render(<VisualGame onComplete={onComplete} onInterrupted={onInterrupted} />)

    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    hidden.mockReturnValue(true)
    fireEvent(document, new Event("visibilitychange"))
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(onComplete).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
    expect(screen.getByRole("button", { name: "开始视觉记忆" })).toBeVisible()
  })

  it.each([
    [396, 396.015625],
    [336, 336.015625],
    [324, 324.0625],
  ])("continues through level 8 after grid rounding at board width %i", (width, level8Height) => {
    boardSize = { width, height: width }
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    render(<VisualGame onComplete={onComplete} onInterrupted={onInterrupted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))

    for (let level = 1; level <= 8; level += 1) {
      expect(screen.getByText(`关卡：${level}`)).toBeVisible()
      if (level === 8) {
        expect(screen.getAllByTestId("visual-memory-tile")).toHaveLength(25)
        act(() => ResizeObserverDouble.latest?.resize(width, level8Height))
        expect(onInterrupted).not.toHaveBeenCalled()
      }
      act(() => vi.advanceTimersByTime(500))
      const targets = screen.getAllByTestId("visual-memory-tile-active").map(
        (marker) => marker.closest("button")!,
      )
      expect(targets).toHaveLength(level + 2)
      act(() => vi.advanceTimersByTime(1300))
      for (const target of targets) pointerDown(target)
      expect(screen.getByText("本关完成")).toBeVisible()
      act(() => vi.advanceTimersByTime(450))
    }

    expect(screen.getByText("关卡：9")).toBeVisible()
    expect(onInterrupted).not.toHaveBeenCalled()
    expect(onComplete).not.toHaveBeenCalled()
    beginRound()
    loseLife(9)
    advanceFeedbackAndRound()
    loseLife(9)
    advanceFeedbackAndRound()
    loseLife(9)
    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0]?.[0].raw).toEqual({
      reached_level: 9,
      completed_level: 8,
      accepted_misses: 9,
      lives_used: 3,
      end_reason: "lives",
    })
    expect(onComplete.mock.calls[0]?.[0].device.geometry).toEqual({
      width, height: width, target_diameter: 96,
    })
  })

  it.each(["width", "height"] as const)("compares %s drift with the initial board, not the previous measurement", (dimension) => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    render(<VisualGame onComplete={onComplete} onInterrupted={onInterrupted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))

    for (const delta of [-0.25, 0.25, 0.5]) {
      const size = { width: 300, height: 300, [dimension]: 300 + delta }
      act(() => ResizeObserverDouble.latest?.resize(size.width, size.height))
      expect(onInterrupted).not.toHaveBeenCalled()
      expect(screen.getByText("请观察亮起的方格")).toBeVisible()
    }
    const resized = { width: 300, height: 300, [dimension]: 300.75 }
    act(() => ResizeObserverDouble.latest?.resize(resized.width, resized.height))
    expect(onInterrupted).toHaveBeenCalledExactlyOnceWith("geometry_changed")
    expect(onComplete).not.toHaveBeenCalled()
    expect(vi.getTimerCount()).toBe(0)
    expect(screen.getByRole("button", { name: "开始视觉记忆" })).toBeVisible()
  })

  it.each([[299, 300], [300, 299]])("interrupts when the recorded board geometry changes to %i x %i", (width, height) => {
    const onInterrupted = vi.fn()
    render(<VisualGame onComplete={vi.fn()} onInterrupted={onInterrupted} />)

    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))
    act(() => ResizeObserverDouble.latest?.resize(width, height))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("geometry_changed")
    expect(screen.getByRole("button", { name: "开始视觉记忆" })).toBeVisible()
  })

  it("cancels reveal and feedback timers when unmounted", () => {
    const view = render(<VisualGame onComplete={vi.fn()} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "开始视觉记忆" }))
    expect(vi.getTimerCount()).toBeGreaterThan(0)
    const observer = ResizeObserverDouble.latest
    view.unmount()

    expect(vi.getTimerCount()).toBe(0)
    expect(observer?.disconnect).toHaveBeenCalledTimes(1)
  })
})
