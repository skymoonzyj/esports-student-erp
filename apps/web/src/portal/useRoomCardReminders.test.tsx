import { focusManager, QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import type { PropsWithChildren } from "react"
import { afterEach, expect, it, vi } from "vitest"
import { portalKeys } from "./api"
import { roomCardReminderKeys } from "./roomCardRemindersApi"
import { useRoomCardReminders } from "./useRoomCardReminders"

const payload = { available: true, observed_at: "2026-11-05T15:59:50Z", business_date: "2026-11-05", next_refresh_at: "2026-11-05T16:00:00Z", items: [], total: 0, page: 1, page_size: 20 }
const ready = { accountId: "account-a", contextReady: true, contextEpoch: 1 }
const clients: QueryClient[] = []
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={client}>{children}</QueryClientProvider> }
  return { client, wrapper }
}
afterEach(() => { clients.forEach((client) => client.clear()); clients.length = 0; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); focusManager.setFocused(undefined) })

it("cancels and purges old account responses and hides data during authorization changes", async () => {
  let finish!: (response: Response) => void
  let firstSignal: AbortSignal | null | undefined
  let calls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (++calls === 1) { firstSignal = init?.signal; return new Promise((resolve) => { finish = resolve }) }
    return Response.json(payload)
  }))
  const { client, wrapper } = setup()
  const { result, rerender } = renderHook(useRoomCardReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(calls).toBe(1))
  rerender({ ...ready, accountId: "account-b" })
  expect(result.current.query.data).toBeUndefined()
  await waitFor(() => expect(result.current.query.data?.available).toBe(true))
  expect(firstSignal?.aborted).toBe(true)
  await act(async () => finish(Response.json(payload)))
  expect(client.getQueriesData({ queryKey: roomCardReminderKeys.root("account-a") })).toEqual([])
  rerender({ ...ready, accountId: "account-b", contextReady: false })
  expect(result.current.query.data).toBeUndefined()
})
it("reauthorizes before fetching on focus", async () => {
  const order: string[] = []
  let resolve!: (value: string) => void
  let contextCalls = 0
  vi.stubGlobal("fetch", vi.fn(async () => { order.push("reminders"); return Response.json(payload) }))
  const { wrapper } = setup()
  const { result } = renderHook(() => {
    const context = useQuery({ queryKey: portalKeys.context("account-a"), queryFn: async () => { order.push("context"); if (++contextCalls === 1) return "allowed"; return new Promise<string>((done) => { resolve = done }) }, staleTime: 0, refetchOnWindowFocus: true })
    return useRoomCardReminders({ ...ready, contextReady: context.isSuccess && !context.isFetching, contextEpoch: context.dataUpdatedAt })
  }, { wrapper })
  await waitFor(() => expect(result.current.query.data?.available).toBe(true))
  order.length = 0
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(order).toEqual(["context"]))
  expect(result.current.query.data).toBeUndefined()
  await act(async () => resolve("allowed"))
  await waitFor(() => expect(order).toEqual(["context", "reminders"]))
})
it.each([false, true])("refreshes using the server midnight, including hidden-tab resume=%s", async (hidden) => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] })
  vi.setSystemTime(new Date("2040-01-01T00:00:00Z"))
  const { client, wrapper } = setup()
  client.setQueryData(portalKeys.context("account-a"), "allowed")
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(payload)))
  const { result, rerender } = renderHook(useRoomCardReminders, { wrapper, initialProps: ready })
  await act(async () => { await vi.advanceTimersByTimeAsync(10) })
  expect(result.current.query.data?.business_date).toBe("2026-11-05")
  if (hidden) vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  if (hidden) {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
    act(() => document.dispatchEvent(new Event("visibilitychange")))
  }
  expect(result.current.query.data).toBeUndefined()
  expect(client.getQueryState(portalKeys.context("account-a"))?.isInvalidated).toBe(true)
  rerender({ ...ready, contextEpoch: 2 })
  await act(async () => { await vi.advanceTimersByTimeAsync(10) })
  expect(result.current.query.data?.available).toBe(true)
})

it("resets an out-of-range page and resets pagination on a permission revision", async () => {
  const pages: number[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url) => {
    const page = Number(new URL(String(url), "http://localhost").searchParams.get("page")); pages.push(page)
    return Response.json({ ...payload, page })
  }))
  const { wrapper } = setup()
  const { result, rerender } = renderHook(useRoomCardReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(result.current.query.isSuccess).toBe(true))
  act(() => result.current.setPage(3))
  await waitFor(() => expect(result.current.page).toBe(1))
  await waitFor(() => expect(pages).toEqual([1, 3, 1]))
  act(() => result.current.setPage(2))
  rerender({ ...ready, contextEpoch: 2 })
  expect(result.current.page).toBe(1)
})

it("re-arms a conservative pre-midnight refresh even when the deadline is unchanged", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] })
  const { client, wrapper } = setup()
  client.setQueryData(portalKeys.context("account-a"), "allowed")
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(payload)))
  const { result, rerender } = renderHook(useRoomCardReminders, { wrapper, initialProps: ready })
  await act(async () => { await vi.advanceTimersByTimeAsync(10) })
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(result.current.query.data).toBeUndefined()
  rerender({ ...ready, contextEpoch: 2 })
  await act(async () => { await vi.advanceTimersByTimeAsync(10) })
  expect(result.current.query.data?.available).toBe(true)
  await act(async () => { await vi.advanceTimersByTimeAsync(10000) })
  expect(result.current.query.data).toBeUndefined()
})
