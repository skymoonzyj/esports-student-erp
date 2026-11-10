import { afterEach, expect, it, vi } from "vitest"

import { departureKeys, getDepartureReminders } from "./departureApi"

const id = "10000000-0000-4000-8000-000000000001"
function payload() {
  return {
    available: true, observed_at: "2026-10-24T15:59:50Z",
    business_date: "2026-10-24", timezone: "Asia/Shanghai",
    next_refresh_at: "2026-10-24T16:00:00Z", window: "three_days",
    today_count: 1, three_day_count: 1, page: 1, page_size: 20, total: 1,
    items: [{ person_id: id, full_name: "测试学员", nickname: null, events: [
      { kind: "planned_checkout", source_id: id, date: "2026-10-24", building_name: "一号楼", room_number: "101", detail_path: `/housing/rooms/${id}` },
      { kind: "enrollment_end", source_id: id, date: "2026-10-25", project_name: "电竞", cohort_name: "秋季营", enrollment_status: "completed", detail_path: null },
    ] }],
  }
}
afterEach(() => vi.unstubAllGlobals())

it("defaults to three_days and 20, preserves both source contracts and forwards cancellation", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(payload()))
  vi.stubGlobal("fetch", fetcher)
  const signal = new AbortController().signal
  const result = await getDepartureReminders(undefined, signal)
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/portal/departure-reminders?window=three_days&page=1&page_size=20")
  expect(fetcher.mock.calls[0][1]?.signal).toBe(signal)
  expect(result.items[0].events.map((event) => event.kind)).toEqual(["planned_checkout", "enrollment_end"])
  expect(departureKeys.page("a", 42, "today", 2, 20)).toEqual(["portal", "a", "departures", 42, "today", 2, 20])
})

it.each(["invalid union", "invalid date", "external link", "foreign source fields", "unavailable with people"])("rejects %s without retaining response data", async (kind) => {
  const body = payload()
  const event = body.items[0].events[0] as Record<string, unknown>
  if (kind === "invalid union") event.kind = "secret-id-123456"
  if (kind === "invalid date") event.date = "2026-02-30"
  if (kind === "external link") event.detail_path = "https://secret-id-123456.example.com"
  if (kind === "foreign source fields") event.project_name = "secret-id-123456"
  if (kind === "unavailable with people") body.available = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => Response.json(body)))
  const error = await getDepartureReminders().catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(Error)
  expect(String(error)).not.toContain("secret-id-123456")
  expect(error).not.toHaveProperty("cause")
  expect(error).not.toHaveProperty("issues")
})

it.each(["problem", "invalid json", "network"])("sanitizes %s errors including own properties", async (kind) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => {
    if (kind === "network") throw new Error("secret-id-123456")
    if (kind === "invalid json") return new Response('{"secret-id-123456":')
    return Response.json({ status: 403, detail: "身份证 secret-id-123456", title: "secret-id-123456", code: "secret-id-123456", request_id: "secret-id-123456", errors: [{ field: "身份证", message: "secret-id-123456" }] }, { status: 403 })
  }))
  const error = await getDepartureReminders().catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(Error)
  const ownProperties = Object.fromEntries(Object.getOwnPropertyNames(error).map((key) => [key, (error as Record<string, unknown>)[key]]))
  expect(JSON.stringify(ownProperties)).not.toContain("secret-id-123456")
  if (kind === "problem") expect(error).toMatchObject({ problem: { status: 403 } })
  expect(error).not.toHaveProperty("cause")
})

it("preserves AbortError without a response cause", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => { throw new DOMException("request details", "AbortError") }))
  await expect(getDepartureReminders()).rejects.toMatchObject({ name: "AbortError" })
})

it.each(["window", "page", "page_size"])("rejects a response for the wrong %s", async (field) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => Response.json({ ...payload(), [field]: field === "window" ? "today" : 2 })))
  await expect(getDepartureReminders()).rejects.toBeInstanceOf(Error)
})
