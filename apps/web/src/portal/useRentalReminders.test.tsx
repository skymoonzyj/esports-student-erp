import { focusManager, QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import { type PropsWithChildren } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { portalKeys } from "./api"
import { rentalReminderKeys } from "./rentalRemindersApi"
import { useRentalReminders } from "./useRentalReminders"

const ready = { accountId: "account-a", contextReady: true, contextEpoch: 1 }
function payload(page = 1, total = 1) {
  return { available: true, can_manage: false, observed_at: "2026-10-24T15:59:50Z", business_date: "2026-10-24", next_refresh_at: "2026-10-24T16:00:00Z", page, page_size: 20, total,
    items: total ? [{ asset_id: "10000000-0000-4000-8000-000000000001", asset_code: "RENT-1", name: "租用电脑", location: null, person_name: null, rental_expires_on: "2026-10-24", days_remaining: 0, rental_revision: 1 }] : [] }
}
const clients: QueryClient[] = []
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={client}>{children}</QueryClientProvider> }
  return { client, wrapper }
}
afterEach(() => { clients.forEach((client) => client.clear()); clients.length = 0; vi.useRealTimers(); vi.unstubAllGlobals(); vi.restoreAllMocks(); focusManager.setFocused(undefined) })

it("hides and purges data on authorization changes and resets pagination", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => Response.json(payload(Number(new URL(String(input), "http://localhost").searchParams.get("page"))))))
  const { client, wrapper } = setup()
  const { result, rerender } = renderHook(useRentalReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(result.current.query.data?.total).toBe(1))
  act(() => result.current.setPage(2))
  await waitFor(() => expect(result.current.query.data?.page).toBe(2))
  rerender({ ...ready, contextReady: false })
  expect(result.current.query.data).toBeUndefined()
  expect(client.getQueriesData({ queryKey: rentalReminderKeys.root("account-a") })).toEqual([])
  rerender({ ...ready, contextEpoch: 2 })
  expect(result.current.page).toBe(1)
  await waitFor(() => expect(result.current.query.data?.page).toBe(1))
})

it("aborts old identities and prevents late private responses from repopulating cache", async () => {
  let finish!: (value: Response) => void
  let signal: AbortSignal | null | undefined
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_input, init) => {
    if (!signal) { signal = init?.signal; return new Promise((resolve) => { finish = resolve }) }
    return Response.json({ ...payload(), items: [{ ...payload().items[0], name: "新账号设备" }] })
  }))
  const { client, wrapper } = setup()
  const { result, rerender } = renderHook(useRentalReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(signal).toBeTruthy())
  rerender({ ...ready, accountId: "account-b" })
  expect(result.current.query.data).toBeUndefined()
  await waitFor(() => expect(result.current.query.data?.items[0].name).toBe("新账号设备"))
  expect(signal?.aborted).toBe(true)
  await act(async () => { finish(Response.json(payload())) })
  expect(client.getQueriesData({ queryKey: rentalReminderKeys.root("account-a") })).toEqual([])
})

it("reauthorizes on focus before fetching another private page", async () => {
  const order: string[] = []
  let finish!: (value: string) => void
  let calls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => { order.push("rentals"); return Response.json(payload()) }))
  const { wrapper } = setup()
  const { result } = renderHook(() => {
    const context = useQuery({ queryKey: portalKeys.context("account-a"), queryFn: async () => { order.push("context"); if (++calls === 1) return "allowed"; return new Promise<string>((resolve) => { finish = resolve }) }, staleTime: 0, refetchOnWindowFocus: true })
    return useRentalReminders({ accountId: "account-a", contextReady: context.isSuccess && !context.isFetching, contextEpoch: context.dataUpdatedAt })
  }, { wrapper })
  await waitFor(() => expect(result.current.query.data?.total).toBe(1))
  order.length = 0
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(order).toEqual(["context"]))
  expect(result.current.query.data).toBeUndefined()
  await act(async () => { finish("allowed") })
  await waitFor(() => expect(order).toEqual(["context", "rentals"]))
})

it.each([false, true])("uses literal server midnight despite clock skew and hidden=%s", async (hidden) => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] })
  vi.setSystemTime(new Date("2040-01-01T00:00:00Z"))
  const { client, wrapper } = setup()
  client.setQueryData(portalKeys.context("account-a"), "allowed")
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => { await new Promise((resolve) => setTimeout(resolve, 2000)); return Response.json(payload()) }))
  const { result, rerender } = renderHook(useRentalReminders, { wrapper, initialProps: ready })
  await act(async () => { await vi.advanceTimersByTimeAsync(2001) })
  expect(result.current.query.data?.business_date).toBe("2026-10-24")
  if (hidden) vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
  await act(async () => { await vi.advanceTimersByTimeAsync(7999) })
  if (hidden) {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
    act(() => document.dispatchEvent(new Event("visibilitychange")))
  }
  expect(result.current.query.data).toBeUndefined()
  expect(client.getQueryState(portalKeys.context("account-a"))?.isInvalidated).toBe(true)
  rerender({ ...ready, contextEpoch: 2 })
  await act(async () => { await vi.advanceTimersByTimeAsync(2001) })
  expect(result.current.query.data?.total).toBe(1)
})

it("re-arms an early asymmetric-latency refresh when the server returns the same midnight deadline", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] })
  vi.setSystemTime(new Date("2040-01-01T00:00:00Z"))
  const { client, wrapper } = setup()
  client.setQueryData(portalKeys.context("account-a"), "allowed")
  let calls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => {
    const call = ++calls
    // Request 1 spends 4s before the server observes its clock, then 100ms
    // returning. Later requests observe immediately and return after 100ms.
    await new Promise((resolve) => setTimeout(resolve, call === 1 ? 4100 : 100))
    return Response.json({
      ...payload(),
      observed_at: call === 1 ? "2026-10-24T15:59:54Z" : call === 2 ? "2026-10-24T15:59:56Z" : "2026-10-24T16:00:00Z",
      business_date: call < 3 ? "2026-10-24" : "2026-10-25",
      next_refresh_at: call < 3 ? "2026-10-24T16:00:00Z" : "2026-10-25T16:00:00Z",
    })
  }))
  const { result, rerender } = renderHook(useRentalReminders, { wrapper, initialProps: ready })
  await act(async () => { await vi.advanceTimersByTimeAsync(4101) })
  expect(result.current.query.data?.business_date).toBe("2026-10-24")
  await act(async () => { await vi.advanceTimersByTimeAsync(1899) })
  // The elapsed request bound can conservatively fire before actual midnight.
  expect(result.current.query.data).toBeUndefined()
  act(() => client.setQueryData(portalKeys.context("account-a"), "allowed"))
  rerender({ ...ready, contextEpoch: 2 })
  await act(async () => { await vi.advanceTimersByTimeAsync(101) })
  expect(result.current.query.data?.business_date).toBe("2026-10-24")
  expect(result.current.query.data?.next_refresh_at).toBe("2026-10-24T16:00:00Z")
  expect(calls).toBe(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(3898) })
  expect(result.current.query.data?.business_date).toBe("2026-10-24")
  expect(calls).toBe(2)
  await act(async () => { await vi.advanceTimersByTimeAsync(1) })
  expect(result.current.query.data).toBeUndefined()
  act(() => client.setQueryData(portalKeys.context("account-a"), "allowed"))
  rerender({ ...ready, contextEpoch: 3 })
  await act(async () => { await vi.advanceTimersByTimeAsync(101) })
  expect(result.current.query.data?.business_date).toBe("2026-10-25")
  expect(calls).toBe(3)
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000) })
  expect(result.current.query.data?.business_date).toBe("2026-10-25")
  expect(calls).toBe(3)
})

it("resets an out-of-range page after a renewal removes its last row", async () => {
  const pages: number[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => { const page = Number(new URL(String(input), "http://localhost").searchParams.get("page")); pages.push(page); return Response.json(payload(page, 0)) }))
  const { wrapper } = setup()
  const { result } = renderHook(useRentalReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(result.current.query.isSuccess).toBe(true))
  act(() => result.current.setPage(3))
  await waitFor(() => expect(result.current.page).toBe(1))
  await waitFor(() => expect(result.current.query.isSuccess).toBe(true))
  expect(pages).toEqual([1, 3, 1])
})

it("does not fetch without identity or ready context and sanitizes access errors", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ detail: "secret-rental" }, { status: 403 }))
  vi.stubGlobal("fetch", fetcher)
  const { wrapper } = setup()
  const { result, rerender } = renderHook(useRentalReminders, { wrapper, initialProps: { ...ready, contextReady: false } })
  expect(result.current.query.data).toBeUndefined()
  expect(fetcher).not.toHaveBeenCalled()
  rerender(ready)
  await waitFor(() => expect(result.current.query.isError).toBe(true))
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(JSON.stringify(result.current.query.error)).not.toContain("secret-rental")
})
