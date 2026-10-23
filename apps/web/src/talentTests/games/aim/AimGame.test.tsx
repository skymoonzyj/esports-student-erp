import { act, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { AimGame } from "./AimGame"

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
  target: Element,
  pointerType: string,
  button = 0,
  isPrimary = true,
) {
  fireEvent.pointerDown(target, { button, isPrimary, pointerType })
}

describe("AimGame", () => {
  let now = 1000

  beforeEach(() => {
    ResizeObserverDouble.latest = null
    vi.stubGlobal("ResizeObserver", ResizeObserverDouble)
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.getAttribute("aria-label") === "瞄准训练区域"
        ? rectangle(800, 500)
        : rectangle(0, 0)
    })
    vi.spyOn(performance, "now").mockImplementation(() => now)
    vi.spyOn(Math, "random").mockReturnValue(0.5)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("reports 30 engine intervals, two empty-field misses and touch geometry once", () => {
    const onComplete = vi.fn()
    render(<AimGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "touch")
    const surface = screen.getByLabelText("瞄准训练游戏区域")
    pointerDown(surface, "touch")

    for (let hit = 1; hit <= 29; hit += 1) {
      now += 100
      pointerDown(screen.getByRole("button", { name: /击中目标/ }), "touch")
      if (hit === 15) pointerDown(surface, "touch")
    }

    now += 100
    const finalTarget = screen.getByRole("button", { name: /击中目标/ })
    pointerDown(finalTarget, "touch")
    pointerDown(finalTarget, "touch")

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith({
      test_id: "aim",
      raw: {
        intervals_ms: Array.from({ length: 30 }, () => 100),
        miss_count: 2,
        total_elapsed_ms: 3000,
      },
      device: {
        input_kind: "touch",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: { width: 800, height: 500, target_diameter: 100 },
      },
      duration_ms: 3000,
      background_interrupted: false,
    })
  })

  it("does not count intro, target bubbling or non-primary input as misses", () => {
    const onComplete = vi.fn()
    render(<AimGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    const stage = screen.getByLabelText("瞄准训练区域")
    pointerDown(stage, "mouse")
    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "mouse")
    pointerDown(screen.getByLabelText("瞄准训练游戏区域"), "mouse", 1)

    for (let hit = 0; hit < 30; hit += 1) {
      now += 100
      pointerDown(screen.getByRole("button", { name: /击中目标/ }), "mouse")
    }

    expect(onComplete.mock.calls[0]?.[0].raw.miss_count).toBe(0)
  })

  it("keeps the active target in place for a same-geometry observer notification", () => {
    const onInterrupted = vi.fn()
    vi.spyOn(Math, "random").mockReturnValue(0.25)
    render(<AimGame onComplete={vi.fn()} onInterrupted={onInterrupted} />)

    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "mouse")
    const target = screen.getByRole("button", { name: /击中目标/ })
    expect(target).toHaveStyle({ left: "225px", top: "150px" })

    act(() => ResizeObserverDouble.latest?.resize(800, 500))

    expect(screen.getByRole("button", { name: /击中目标/ })).toHaveStyle({
      left: "225px",
      top: "150px",
    })
    expect(onInterrupted).not.toHaveBeenCalled()
  })

  it("rejects non-primary touch at start and secondary touch or pen while playing", () => {
    const onComplete = vi.fn()
    render(<AimGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    const startTarget = screen.getByRole("button", { name: "点击目标开始" })
    pointerDown(startTarget, "touch", 0, false)
    expect(screen.getByRole("button", { name: "点击目标开始" })).toBeVisible()

    pointerDown(startTarget, "touch")
    const surface = screen.getByLabelText("瞄准训练游戏区域")
    pointerDown(surface, "touch", 0, false)
    pointerDown(screen.getByRole("button", { name: /击中目标/ }), "pen", 0, false)
    expect(screen.getByRole("button", { name: "击中目标，剩余 30 个" })).toBeVisible()

    for (let hit = 0; hit < 30; hit += 1) {
      now += 100
      pointerDown(screen.getByRole("button", { name: /击中目标/ }), "touch")
    }

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete.mock.calls[0]?.[0].raw.miss_count).toBe(0)
    expect(onComplete.mock.calls[0]?.[0].device.input_kind).toBe("touch")
  })

  it("keeps duration at least the sum of engine-clamped intervals", () => {
    const onComplete = vi.fn()
    render(<AimGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "mouse")
    for (let hit = 0; hit < 30; hit += 1) {
      pointerDown(screen.getByRole("button", { name: /击中目标/ }), "mouse")
    }

    const result = onComplete.mock.calls[0]?.[0]
    expect(result.raw.intervals_ms).toEqual(Array.from({ length: 30 }, () => 1))
    expect(result.raw.total_elapsed_ms).toBe(30)
    expect(result.duration_ms).toBe(30)
    expect(screen.queryByText("超高")).not.toBeInTheDocument()
  })

  it("restarts cleanly after observed geometry changes", () => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    const { unmount } = render(
      <AimGame onComplete={onComplete} onInterrupted={onInterrupted} />,
    )

    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "pen")
    now += 100
    pointerDown(screen.getByRole("button", { name: /击中目标/ }), "pen")
    pointerDown(screen.getByLabelText("瞄准训练游戏区域"), "mouse")
    act(() => ResizeObserverDouble.latest?.resize(699, 500))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("geometry_changed")
    expect(screen.getByRole("button", { name: "点击目标开始" })).toBeVisible()
    expect(onComplete).not.toHaveBeenCalled()

    now = 5000
    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "touch")
    for (let hit = 0; hit < 30; hit += 1) {
      now += 50
      pointerDown(screen.getByRole("button", { name: /击中目标/ }), "touch")
    }

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith({
      test_id: "aim",
      raw: {
        intervals_ms: Array.from({ length: 30 }, () => 50),
        miss_count: 0,
        total_elapsed_ms: 1500,
      },
      device: {
        input_kind: "touch",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: { width: 699, height: 500, target_diameter: 64 },
      },
      duration_ms: 1500,
      background_interrupted: false,
    })

    const observer = ResizeObserverDouble.latest
    unmount()
    expect(observer?.disconnect).toHaveBeenCalledTimes(1)
  })

  it("terminates the active run when the page becomes hidden", () => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    render(<AimGame onComplete={onComplete} onInterrupted={onInterrupted} />)

    pointerDown(screen.getByRole("button", { name: "点击目标开始" }), "mouse")
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(screen.getByRole("button", { name: "点击目标开始" })).toBeVisible()
    expect(onComplete).not.toHaveBeenCalled()
  })
})
