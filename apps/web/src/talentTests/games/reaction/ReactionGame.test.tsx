import { StrictMode } from "react"
import { act, createEvent, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ReactionGame } from "./ReactionGame"

let clock = 0

function primaryPointerDown(
  element: Element,
  pointerType: "mouse" | "touch" | "pen",
  button = 0,
) {
  const event = createEvent.pointerDown(element)
  Object.defineProperties(event, {
    button: { value: button },
    isPrimary: { value: true },
    pointerType: { value: pointerType },
  })
  fireEvent(element, event)
}

function primaryPointerUp(
  element: Element,
  pointerType: "mouse" | "touch" | "pen",
) {
  const event = createEvent.pointerUp(element)
  Object.defineProperties(event, {
    button: { value: 0 },
    isPrimary: { value: true },
    pointerType: { value: pointerType },
  })
  fireEvent(element, event)
}

function stage() {
  return screen.getByTestId("reaction-game")
}

function advanceToGreen(greenAt: number) {
  clock = greenAt
  act(() => {
    vi.advanceTimersByTime(2_500)
    vi.advanceTimersByTime(16)
  })
  expect(screen.getByRole("heading", { name: "点击！" })).toBeInTheDocument()
}

beforeEach(() => {
  clock = 0
  vi.useFakeTimers()
  vi.spyOn(Math, "random").mockReturnValue(0)
  vi.spyOn(performance, "now").mockImplementation(() => clock)
  vi.stubGlobal(
    "requestAnimationFrame",
    (callback: FrameRequestCallback) => window.setTimeout(() => callback(clock), 16),
  )
  vi.stubGlobal("cancelAnimationFrame", (handle: number) => window.clearTimeout(handle))
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it("waits for green, ignores a duplicate retry press, and completes with one early click", () => {
  const onComplete = vi.fn()
  clock = 100
  render(<ReactionGame onComplete={onComplete} onInterrupted={vi.fn()} />)

  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "等待变绿" })).toBeInTheDocument()

  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "太早了！" })).toBeInTheDocument()
  primaryPointerDown(stage(), "mouse")
  primaryPointerDown(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "等待变绿" })).toBeInTheDocument()
  primaryPointerUp(stage(), "mouse")

  advanceToGreen(1_000)
  primaryPointerDown(stage(), "mouse")
  expect(screen.getByText("0 毫秒")).toBeInTheDocument()

  const remaining = [
    { greenAt: 4_000, elapsed: 200, pointerType: "touch" as const },
    { greenAt: 7_000, elapsed: 220, pointerType: "pen" as const },
    { greenAt: 10_000, elapsed: 240, pointerType: "mouse" as const },
    { greenAt: 13_000, elapsed: 260, pointerType: "touch" as const },
  ]
  for (const trial of remaining) {
    primaryPointerDown(stage(), trial.pointerType)
    advanceToGreen(trial.greenAt)
    clock = trial.greenAt + trial.elapsed
    primaryPointerDown(stage(), trial.pointerType)
  }

  expect(onComplete).toHaveBeenCalledTimes(1)
  expect(onComplete).toHaveBeenCalledWith({
    test_id: "reactiontime",
    raw: {
      samples_ms: [0, 200, 220, 240, 260],
      too_soon_count: 1,
    },
    device: {
      input_kind: "mixed",
      viewport_width: window.innerWidth,
      viewport_height: window.innerHeight,
      geometry: null,
    },
    duration_ms: 13_160,
    background_interrupted: false,
  })

  primaryPointerDown(stage(), "mouse")
  expect(onComplete).toHaveBeenCalledTimes(1)
})

it("counts a released new press during a genuine waiting phase", () => {
  const onComplete = vi.fn()
  render(<ReactionGame onComplete={onComplete} onInterrupted={vi.fn()} />)

  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "太早了！" })).toBeInTheDocument()

  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "等待变绿" })).toBeInTheDocument()
  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "太早了！" })).toBeInTheDocument()

  primaryPointerDown(stage(), "mouse")
  primaryPointerUp(stage(), "mouse")
  for (let index = 0; index < 5; index += 1) {
    const greenAt = 3_000 + index * 3_000
    advanceToGreen(greenAt)
    clock = greenAt + 200
    primaryPointerDown(stage(), "mouse")
    primaryPointerUp(stage(), "mouse")
    if (index < 4) {
      primaryPointerDown(stage(), "mouse")
      primaryPointerUp(stage(), "mouse")
    }
  }

  expect(onComplete).toHaveBeenCalledTimes(1)
  expect(onComplete.mock.calls[0]?.[0].raw.too_soon_count).toBe(2)
})

it("arms a genuine waiting press when the start press is released outside the game", () => {
  render(
    <>
      <div data-testid="outside-reaction-game" />
      <ReactionGame onComplete={vi.fn()} onInterrupted={vi.fn()} />
    </>,
  )

  primaryPointerDown(stage(), "mouse")
  expect(screen.getByRole("heading", { name: "等待变绿" })).toBeInTheDocument()
  primaryPointerUp(screen.getByTestId("outside-reaction-game"), "mouse")

  primaryPointerDown(stage(), "mouse")

  expect(screen.getByRole("heading", { name: "太早了！" })).toBeInTheDocument()
})

it("keeps waiting through a callback-only rerender and uses the latest interruption callback", () => {
  const firstInterrupted = vi.fn()
  const latestInterrupted = vi.fn()
  const view = render(
    <ReactionGame onComplete={vi.fn()} onInterrupted={firstInterrupted} />,
  )

  primaryPointerDown(stage(), "touch")
  primaryPointerUp(stage(), "touch")
  view.rerender(
    <ReactionGame onComplete={vi.fn()} onInterrupted={latestInterrupted} />,
  )

  advanceToGreen(3_000)
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true)
  fireEvent(document, new Event("visibilitychange"))

  expect(firstInterrupted).not.toHaveBeenCalled()
  expect(latestInterrupted).toHaveBeenCalledTimes(1)
  expect(latestInterrupted).toHaveBeenCalledWith("hidden")
  hidden.mockRestore()
})

it("reports hidden interruption and cancels the pending timer", () => {
  const onComplete = vi.fn()
  const onInterrupted = vi.fn()
  const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(false)
  const view = render(<ReactionGame onComplete={onComplete} onInterrupted={onInterrupted} />)

  primaryPointerDown(stage(), "touch")
  expect(vi.getTimerCount()).toBeGreaterThan(0)
  hidden.mockReturnValue(true)
  fireEvent(document, new Event("visibilitychange"))

  expect(onInterrupted).toHaveBeenCalledTimes(1)
  expect(onInterrupted).toHaveBeenCalledWith("hidden")
  expect(onComplete).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)

  view.unmount()
  expect(vi.getTimerCount()).toBe(0)
})

it("cancels its timer when the host unmounts it", () => {
  const view = render(<ReactionGame onComplete={vi.fn()} onInterrupted={vi.fn()} />)
  primaryPointerDown(stage(), "mouse")
  expect(vi.getTimerCount()).toBeGreaterThan(0)

  view.unmount()

  expect(vi.getTimerCount()).toBe(0)
})

it("remains interactive through the StrictMode effect replay", () => {
  render(
    <StrictMode>
      <ReactionGame onComplete={vi.fn()} onInterrupted={vi.fn()} />
    </StrictMode>,
  )

  primaryPointerDown(stage(), "mouse")

  expect(screen.getByRole("heading", { name: "等待变绿" })).toBeInTheDocument()
  expect(vi.getTimerCount()).toBeGreaterThan(0)
})
