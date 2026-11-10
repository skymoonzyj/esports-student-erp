import { focusManager, QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { act, renderHook, waitFor } from "@testing-library/react"
import { type PropsWithChildren } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { portalKeys } from "./api"
import { departureKeys } from "./departureApi"
import { useDepartureReminders } from "./useDepartureReminders"

const id = "10000000-0000-4000-8000-000000000001"
function payload(page = 1, total = 1) {
  return { available: true, observed_at: "2026-10-24T15:59:50Z", business_date: "2026-10-24", timezone: "Asia/Shanghai", next_refresh_at: "2026-10-24T16:00:00Z", window: "three_days", today_count: total, three_day_count: total, page, page_size: 20, total,
    items: total ? [{ person_id: id, full_name: "测试学员", nickname: null, events: [{ kind: "planned_checkout", source_id: id, date: "2026-10-24", detail_path: null, building_name: "一号楼", room_number: "101" }] }] : [] }
}
const clients: QueryClient[] = []
function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  function wrapper({ children }: PropsWithChildren) { return <QueryClientProvider client={client}>{children}</QueryClientProvider> }
  return { client, wrapper }
}
const ready = { accountId: "account-a", contextReady: true, contextEpoch: 1 }
afterEach(() => { clients.forEach((client) => client.clear()); clients.length = 0; vi.useRealTimers(); vi.unstubAllGlobals(); focusManager.setFocused(undefined) })

it("test_old_account_response_cannot_repopulate", async () => {
  let finish!: (value: Response) => void
  let signal: AbortSignal | null | undefined
  let calls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_input, init) => {
    if (++calls === 1) { signal = init?.signal; return new Promise((resolve) => { finish = resolve }) }
    return Response.json({ ...payload(), items: [{ ...payload().items[0], full_name: "新账号学员" }] })
  }))
  const { client, wrapper } = setup()
  const { result, rerender } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(calls).toBe(1))
  rerender({ ...ready, accountId: "account-b" })
  expect(result.current.query.data).toBeUndefined()
  await waitFor(() => expect(result.current.query.data?.items[0].full_name).toBe("新账号学员"))
  expect(signal?.aborted).toBe(true)
  await act(async () => { finish(Response.json(payload())); await Promise.resolve() })
  expect(client.getQueriesData({ queryKey: departureKeys.root("account-a") })).toEqual([])
  expect(result.current.query.data?.items[0].full_name).toBe("新账号学员")
})

it("test_revoked_context_hides_and_cancels_previous_data", async () => {
  let signal: AbortSignal | null | undefined
  let calls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_input, init) => { signal = init?.signal; calls++; return Response.json(payload()) }))
  const { client, wrapper } = setup()
  const { result, rerender } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(result.current.query.data?.total).toBe(1))
  rerender({ ...ready, contextReady: false })
  expect(result.current.query.data).toBeUndefined()
  expect(client.getQueryData(departureKeys.page("account-a", 1, "three_days", 1, 20))).toBeUndefined()
  rerender({ ...ready, contextEpoch: 2 })
  await waitFor(() => expect(calls).toBe(2))
  expect(signal).toBeTruthy()
})

it("cancels an in-flight request when context is being rechecked", async () => {
  let signal: AbortSignal | null | undefined
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_input, init) => { signal = init?.signal; return new Promise(() => {}) }))
  const { wrapper } = setup()
  const { result, rerender } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(signal).toBeTruthy())
  rerender({ ...ready, contextReady: false })
  expect(result.current.query.data).toBeUndefined()
  await waitFor(() => expect(signal?.aborted).toBe(true))
})

it("persists window on workbench reentry, isolates it per account, and resets page on authorization epoch", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => { const url = new URL(String(input), "http://localhost"); return Response.json({ ...payload(Number(url.searchParams.get("page"))), window: url.searchParams.get("window") }) }))
  const { wrapper } = setup()
  const first = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(first.result.current.query.isSuccess).toBe(true))
  act(() => { first.result.current.setWindow("today"); first.result.current.setPage(2) })
  first.unmount()
  const next = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  expect(next.result.current.window).toBe("today")
  expect(next.result.current.page).toBe(1)
  act(() => next.result.current.setPage(2))
  next.rerender({ ...ready, contextEpoch: 2 })
  expect(next.result.current.page).toBe(1)
  expect(next.result.current.window).toBe("today")
  next.rerender({ ...ready, accountId: "account-b" })
  expect(next.result.current.window).toBe("three_days")
})

it("test_focus_keeps_window_and_reauthorizes_first", async () => {
  const order: string[] = []
  let finishContext!: (value: string) => void
  let contextCalls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => { order.push("reminders"); const url = new URL(String(input), "http://localhost"); return Response.json({ ...payload(), window: url.searchParams.get("window") }) }))
  const { wrapper } = setup()
  const { result } = renderHook(() => {
    const context = useQuery({ queryKey: portalKeys.context("account-a"), queryFn: async () => { order.push("context"); if (++contextCalls === 1) return "allowed"; return new Promise<string>((resolve) => { finishContext = resolve }) }, staleTime: 0, refetchOnWindowFocus: true })
    return useDepartureReminders({ accountId: "account-a", contextReady: context.isSuccess && !context.isFetching, contextEpoch: context.dataUpdatedAt })
  }, { wrapper })
  await waitFor(() => expect(result.current.query.data?.total).toBe(1))
  act(() => result.current.setWindow("today"))
  await waitFor(() => expect(result.current.query.data?.window).toBe("today"))
  order.length = 0
  act(() => { focusManager.setFocused(false); focusManager.setFocused(true) })
  await waitFor(() => expect(order).toEqual(["context"]))
  expect(result.current.query.data).toBeUndefined()
  await act(async () => { finishContext("allowed") })
  await waitFor(() => expect(order).toEqual(["context", "reminders"]))
  expect(result.current.window).toBe("today")
})

it.each(["test_server_midnight_with_skewed_device_clock", "test_hidden_tab_resume_after_midnight"])("%s", async (name) => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date", "performance"] })
  vi.setSystemTime(new Date("2040-01-01T00:00:00Z"))
  const { client, wrapper } = setup()
  client.setQueryData(portalKeys.context("account-a"), "allowed")
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => { await new Promise((resolve) => setTimeout(resolve, 2000)); return Response.json(payload()) }))
  const { result, rerender } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await act(async () => { await vi.advanceTimersByTimeAsync(2001) })
  expect(result.current.query.data?.business_date).toBe("2026-10-24")
  if (name.includes("hidden")) {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden")
    await act(async () => { await vi.advanceTimersByTimeAsync(20000) })
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible")
    act(() => document.dispatchEvent(new Event("visibilitychange")))
  } else {
    await act(async () => { await vi.advanceTimersByTimeAsync(7999) })
  }
  expect(client.getQueryState(portalKeys.context("account-a"))?.isInvalidated).toBe(true)
  expect(result.current.query.data).toBeUndefined()
  rerender({ ...ready, contextEpoch: 2 })
  await act(async () => { await vi.advanceTimersByTimeAsync(2001) })
  expect(result.current.query.data?.total).toBe(1)
})

it("test_out_of_range_page_resets_once", async () => {
  const pages: number[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => { const page = Number(new URL(String(input), "http://localhost").searchParams.get("page")); pages.push(page); return Response.json(payload(page, 0)) }))
  const { wrapper } = setup()
  const { result } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(result.current.query.isSuccess).toBe(true))
  act(() => result.current.setPage(3))
  await waitFor(() => expect(result.current.page).toBe(1))
  await waitFor(() => expect(result.current.query.isSuccess).toBe(true))
  expect(pages).toEqual([1, 3, 1])
})

it("does not automatically retry access errors or expose their sensitive details", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ detail: "身份证 secret-123456", code: "permission_denied" }, { status: 403 }))
  vi.stubGlobal("fetch", fetcher)
  const { wrapper } = setup()
  const { result } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await waitFor(() => expect(result.current.query.isError).toBe(true))
  expect(fetcher).toHaveBeenCalledTimes(1)
  expect(JSON.stringify(result.current.query.error)).not.toContain("secret-123456")
})

it("bounds transient failure retries to one delayed retry", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] })
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ detail: "服务失败" }, { status: 500 }))
  vi.stubGlobal("fetch", fetcher)
  const { wrapper } = setup()
  const { result } = renderHook(useDepartureReminders, { wrapper, initialProps: ready })
  await act(async () => { await vi.advanceTimersByTimeAsync(999) })
  expect(fetcher).toHaveBeenCalledTimes(1)
  await act(async () => { await vi.advanceTimersByTimeAsync(10001) })
  expect(fetcher).toHaveBeenCalledTimes(2)
  expect(result.current.query.isError).toBe(true)
})
