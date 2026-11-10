import { afterEach, expect, it, vi } from "vitest"

import { getRentalReminders, rentalReminderKeys } from "./rentalRemindersApi"

function payload() {
  return { available: true, can_manage: false, observed_at: "2026-10-24T15:59:50Z", business_date: "2026-10-24", next_refresh_at: "2026-10-24T16:00:00Z", page: 1, page_size: 20, total: 1,
    items: [{ asset_id: "10000000-0000-4000-8000-000000000001", asset_code: "RENT-1", name: "租用电脑", location: null, person_name: null, rental_expires_on: "2026-10-24", days_remaining: 0, rental_revision: 1 }] }
}
afterEach(() => vi.unstubAllGlobals())

it("requests bounded independent pagination and forwards cancellation under logout-cleaned query keys", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(payload()))
  vi.stubGlobal("fetch", fetcher)
  const signal = new AbortController().signal
  const result = await getRentalReminders(1, signal)
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/portal/rental-reminders?page=1&page_size=20")
  expect(fetcher.mock.calls[0][1]?.signal).toBe(signal)
  expect(result.items[0].days_remaining).toBe(0)
  expect(rentalReminderKeys.page("a", 42, 2)).toEqual(["portal", "a", "rental-reminders", 42, 2, 20])
})

it.each(["date", "foreign payload", "unavailable", "page", "page_size", "revision"])("rejects invalid %s without retaining private raw details", async (kind) => {
  const body = payload()
  if (kind === "date") body.items[0].rental_expires_on = "2026-02-30"
  if (kind === "foreign payload") Object.assign(body.items[0], { photos: "secret-photo" })
  if (kind === "unavailable") body.available = false
  if (kind === "page") body.page = 2
  if (kind === "page_size") body.page_size = 50
  if (kind === "revision") body.items[0].rental_revision = -1
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => Response.json(body)))
  const error = await getRentalReminders().catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(Error)
  expect(String(error)).not.toContain("secret-photo")
  expect(error).not.toHaveProperty("cause")
  expect(error).not.toHaveProperty("issues")
})

it.each(["problem", "invalid json", "network"])("sanitizes %s without leaking raw rental data", async (kind) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => {
    if (kind === "network") throw new Error("secret-rental")
    if (kind === "invalid json") return new Response('{"secret-rental":')
    return Response.json({ detail: "secret-rental", code: "secret-rental", errors: [{ message: "secret-rental" }] }, { status: 403 })
  }))
  const error = await getRentalReminders().catch((caught: unknown) => caught)
  expect(error).toBeInstanceOf(Error)
  const own = Object.fromEntries(Object.getOwnPropertyNames(error).map((key) => [key, (error as Record<string, unknown>)[key]]))
  expect(JSON.stringify(own)).not.toContain("secret-rental")
  expect(error).not.toHaveProperty("cause")
})
