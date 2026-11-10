import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter, useLocation } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { setInvalidSessionHandler } from "../api/client"
import { AppearanceProvider } from "./AppearanceProvider"
import { createAppearanceStore } from "./store"

function response(theme: string, revision: number) {
  return new Response(JSON.stringify({ theme, revision }), { status: 200 })
}

beforeEach(() => {
  localStorage.clear()
  document.documentElement.removeAttribute("data-ui-theme")
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  setInvalidSessionHandler(null)
})

it("changes the root and browser chrome without remounting an entered form or router", async () => {
  let resolve!: (value: Response) => void
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done })))
  const store = createAppearanceStore()
  function Form() {
    const location = useLocation()
    return <><input aria-label="未保存姓名" defaultValue="" /><output>{location.pathname}</output></>
  }
  render(<AppearanceProvider store={store}><MemoryRouter initialEntries={["/people/new"]}><Form /></MemoryRouter></AppearanceProvider>)
  const input = screen.getByRole("textbox")
  fireEvent.change(input, { target: { value: "保留输入" } })
  await act(async () => resolve(response("command", 2)))
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "command")
  expect(document.querySelector('meta[name="theme-color"]')).toHaveAttribute("content", "#14253d")
  expect(screen.getByRole("textbox")).toBe(input)
  expect(input).toHaveValue("保留输入")
  expect(screen.getByText("/people/new")).toBeInTheDocument()
})

it.each([
  "not json",
  JSON.stringify({ version: 1, theme: "javascript:bad", revision: 2 }),
  JSON.stringify({ version: 2, theme: "command", revision: 2 }),
  JSON.stringify({ version: 1, theme: "command", revision: -1 }),
])("safely defaults on malformed or unsupported cached configuration %s", (cache) => {
  localStorage.setItem("erp.appearance.v1", cache)
  const store = createAppearanceStore()
  expect(store.getSnapshot()).toEqual({ theme: "clear", revision: 1 })
})

it("uses a validated cache immediately and preserves it when public fetch fails without invalidating auth", async () => {
  localStorage.setItem("erp.appearance.v1", JSON.stringify({ version: 1, theme: "command", revision: 4 }))
  const invalidate = vi.fn()
  setInvalidSessionHandler(invalidate)
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code: "invalid_session" }), { status: 401 })))
  const store = createAppearanceStore()
  render(<AppearanceProvider store={store}><p>登录仍可使用</p></AppearanceProvider>)
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "command")
  await act(async () => { await store.refresh() })
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 4 })
  expect(invalidate).not.toHaveBeenCalled()
  expect(screen.getByText("登录仍可使用")).toBeInTheDocument()
})

it("ignores older in-flight and invalid public data after a successful save", async () => {
  let resolve!: (value: Response) => void
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { resolve = done })))
  const store = createAppearanceStore()
  render(<AppearanceProvider store={store} />)
  act(() => store.accept({ theme: "command", revision: 8 }))
  await act(async () => resolve(response("clear", 2)))
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 8 })
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response("external", 9)))
  await act(async () => { await store.refresh() })
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "command")
})

it("polls visible pages at 60 seconds, refreshes on focus, and cleans up single-flight work", async () => {
  vi.useFakeTimers()
  let visibility = "visible"
  vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility as DocumentVisibilityState)
  const fetcher = vi.fn().mockResolvedValue(response("clear", 1))
  vi.stubGlobal("fetch", fetcher)
  const store = createAppearanceStore()
  const view = render(<AppearanceProvider store={store} />)
  await act(async () => {})
  expect(fetcher).toHaveBeenCalledTimes(1)
  visibility = "hidden"
  act(() => document.dispatchEvent(new Event("visibilitychange")))
  await act(async () => vi.advanceTimersByTime(120_000))
  expect(fetcher).toHaveBeenCalledTimes(1)
  visibility = "visible"
  fetcher.mockImplementation(() => new Promise(() => {}))
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"))
    window.dispatchEvent(new Event("focus"))
  })
  expect(fetcher).toHaveBeenCalledTimes(2)
  await act(async () => vi.advanceTimersByTime(1_000))
  expect(fetcher).toHaveBeenCalledTimes(2)
  const signal = fetcher.mock.calls[1][1].signal as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
  expect(vi.getTimerCount()).toBe(0)
  act(() => window.dispatchEvent(new Event("focus")))
  expect(fetcher).toHaveBeenCalledTimes(2)
})

it("expires a hung public read so focus can sync without accepting its late response", async () => {
  vi.useFakeTimers()
  const invalidate = vi.fn()
  setInvalidSessionHandler(invalidate)
  let completeStalled!: (value: Response) => void
  const fetcher = vi.fn<typeof fetch>()
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { completeStalled = resolve }))
    .mockResolvedValueOnce(response("command", 2))
  vi.stubGlobal("fetch", fetcher)
  const store = createAppearanceStore()
  const view = render(<AppearanceProvider store={store}><input aria-label="未保存姓名" defaultValue="保留输入" /></AppearanceProvider>)
  const input = screen.getByRole("textbox")
  let refreshFinished = false
  void store.refresh().then(() => { refreshFinished = true })
  await act(async () => vi.advanceTimersByTime(10_000))
  expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true)
  expect(refreshFinished).toBe(true)
  expect(store.getSnapshot()).toEqual({ theme: "clear", revision: 1 })
  await act(async () => window.dispatchEvent(new Event("focus")))
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 2 })
  await act(async () => completeStalled(response("clear", 99)))
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 2 })
  expect(screen.getByRole("textbox")).toBe(input)
  expect(input).toHaveValue("保留输入")
  expect(invalidate).not.toHaveBeenCalled()
  await act(async () => vi.advanceTimersByTimeAsync(0))
  expect(vi.getTimerCount()).toBe(1)
  view.unmount()
  expect(vi.getTimerCount()).toBe(0)
})

it("recovers on the next visible poll when an earlier response body never finishes", async () => {
  vi.useFakeTimers()
  let completeBody!: (value: unknown) => void
  const stalled = response("clear", 1)
  vi.spyOn(stalled, "json").mockImplementation(() => new Promise((resolve) => { completeBody = resolve }))
  const fetcher = vi.fn<typeof fetch>()
    .mockResolvedValueOnce(stalled)
    .mockResolvedValueOnce(response("command", 2))
  vi.stubGlobal("fetch", fetcher)
  const store = createAppearanceStore()
  render(<AppearanceProvider store={store} />)
  await act(async () => {})
  await act(async () => vi.advanceTimersByTimeAsync(60_000))
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 2 })
  await act(async () => completeBody({ theme: "clear", revision: 99 }))
  expect(document.documentElement).toHaveAttribute("data-ui-theme", "command")
})

it("falls back safely when browser storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("unavailable") })
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("unavailable") })
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response("command", 2)))
  const store = createAppearanceStore()
  render(<AppearanceProvider store={store} />)
  await waitFor(() => expect(document.documentElement).toHaveAttribute("data-ui-theme", "command"))
})

it("treats cached revisions as visual hints and lets the server correct a stale or forged cache", async () => {
  localStorage.setItem("erp.appearance.v1", JSON.stringify({ version: 1, theme: "command", revision: 9999 }))
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response("clear", 3)))
  const store = createAppearanceStore()
  render(<AppearanceProvider store={store} />)
  await waitFor(() => expect(store.getSnapshot()).toEqual({ theme: "clear", revision: 3 }))
  expect(JSON.parse(localStorage.getItem("erp.appearance.v1")!)).toEqual({ version: 1, theme: "clear", revision: 3 })
})

it("does not let a fetch aborted during cleanup overwrite a restarted provider", async () => {
  const completions: Array<(response: Response) => void> = []
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>((done) => { completions.push(done) })))
  const store = createAppearanceStore()
  const first = render(<AppearanceProvider store={store} />)
  first.unmount()
  render(<AppearanceProvider store={store} />)
  await act(async () => completions[1](response("command", 3)))
  await act(async () => completions[0](response("clear", 99)))
  expect(store.getSnapshot()).toEqual({ theme: "command", revision: 3 })
})
