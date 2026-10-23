import { act, createEvent, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { VerbalGame } from "./VerbalGame"

function pointerDown(element: Element, pointerType: "mouse" | "touch" | "pen") {
  const event = createEvent.pointerDown(element)
  Object.defineProperties(event, {
    button: { value: 0 },
    isPrimary: { value: true },
    pointerType: { value: pointerType },
  })
  fireEvent(element, event)
}

describe("VerbalGame", () => {
  let now = 100

  beforeEach(() => {
    now = 100
    vi.spyOn(performance, "now").mockImplementation(() => now)
    vi.spyOn(Math, "random").mockReturnValue(0)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows the Chinese title, cumulative metric and internal pilot label", () => {
    render(<VerbalGame onComplete={vi.fn()} onInterrupted={vi.fn()} />)

    expect(screen.getByRole("heading", { name: "中文词语记忆" })).toBeInTheDocument()
    expect(screen.getByText(/累计正确判断/)).toBeInTheDocument()
    expect(screen.getByText(/内部试行训练目标/)).toBeInTheDocument()
  })

  it("handles one turn once, reports only counts, and ends on the third error", () => {
    const onComplete = vi.fn()
    render(<VerbalGame onComplete={onComplete} onInterrupted={vi.fn()} />)

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    expect(screen.getByText("山川")).toBeInTheDocument()

    const seenButton = screen.getByRole("button", { name: "见过" })
    pointerDown(seenButton, "mouse")
    act(() => {
      seenButton.dispatchEvent(new MouseEvent("click", { bubbles: true }))
      seenButton.dispatchEvent(new MouseEvent("click", { bubbles: true }))
    })
    expect(screen.getByText("剩余机会 2")).toBeInTheDocument()

    const newButton = screen.getByRole("button", { name: "新词" })
    pointerDown(newButton, "touch")
    fireEvent.click(newButton)
    expect(screen.getByText("剩余机会 1")).toBeInTheDocument()

    now = 425
    pointerDown(newButton, "pen")
    fireEvent.click(newButton)

    expect(onComplete).toHaveBeenCalledTimes(1)
    expect(onComplete).toHaveBeenCalledWith({
      test_id: "verbal-memory",
      raw: {
        correct_count: 0,
        error_count: 3,
        total_count: 3,
        word_bank_version: "zh-192-v1",
      },
      device: {
        input_kind: "mixed",
        viewport_width: window.innerWidth,
        viewport_height: window.innerHeight,
        geometry: null,
      },
      duration_ms: 325,
      background_interrupted: false,
    })
    expect(JSON.stringify(onComplete.mock.calls[0]?.[0])).not.toContain("山川")
    expect(screen.getByText("累计正确判断 0 次")).toBeInTheDocument()

    fireEvent.click(newButton)
    expect(onComplete).toHaveBeenCalledTimes(1)
  })

  it("interrupts an active game when the page becomes hidden", () => {
    const onComplete = vi.fn()
    const onInterrupted = vi.fn()
    render(<VerbalGame onComplete={onComplete} onInterrupted={onInterrupted} />)

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
    fireEvent(document, new Event("visibilitychange"))
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).toHaveBeenCalledTimes(1)
    expect(onInterrupted).toHaveBeenCalledWith("hidden")
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "开始测试" })).toBeVisible()
  })

  it("keeps an active game interruptible when the host replaces its callback", () => {
    const onComplete = vi.fn()
    const firstOnInterrupted = vi.fn()
    const latestOnInterrupted = vi.fn()
    const view = render(
      <VerbalGame
        onComplete={onComplete}
        onInterrupted={firstOnInterrupted}
      />,
    )

    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    view.rerender(
      <VerbalGame
        onComplete={onComplete}
        onInterrupted={latestOnInterrupted}
      />,
    )
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
    fireEvent(document, new Event("visibilitychange"))
    fireEvent(document, new Event("visibilitychange"))

    expect(firstOnInterrupted).not.toHaveBeenCalled()
    expect(latestOnInterrupted).toHaveBeenCalledTimes(1)
    expect(latestOnInterrupted).toHaveBeenCalledWith("hidden")
    expect(onComplete).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "开始测试" })).toBeVisible()
  })

  it("removes visibility handling when unmounted", () => {
    const onInterrupted = vi.fn()
    const view = render(<VerbalGame onComplete={vi.fn()} onInterrupted={onInterrupted} />)
    fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")

    view.unmount()
    fireEvent(document, new Event("visibilitychange"))

    expect(onInterrupted).not.toHaveBeenCalled()
  })
})
