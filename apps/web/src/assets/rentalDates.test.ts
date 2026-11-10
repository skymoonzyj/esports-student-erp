import { expect, it, vi } from "vitest"
import { rentalEligible, renewalDate, validRentalDate } from "./rentalDates"

it("uses China today to exclude rentals that have not started", () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-24T16:00:00Z"))
  try {
    const asset = { ownership_type: "rented" as const, status: "available", rental_expires_on: "2026-12-31", rental_revision: 0 }
    expect(rentalEligible({ ...asset, rental_started_on: "2026-10-25" })).toBe(true)
    expect(rentalEligible({ ...asset, rental_started_on: "2026-10-26" })).toBe(false)
  } finally { vi.useRealTimers() }
})

it.each([
  ["2026-01-31", "month", "2026-02-28"],
  ["2024-01-31", "month", "2024-02-29"],
  ["2024-11-30", "quarter", "2025-02-28"],
  ["0001-01-01", "week", "0001-01-08"],
  ["0099-12-31", "month", "0100-01-31"],
  ["9999-12-24", "week", "9999-12-31"],
] as const)("previews %s plus %s as %s", (date, period, expected) => expect(renewalDate(date, period)).toBe(expected))

it.each(["2026-02-29", "0000-01-01", "10000-01-01", "2026-1-01", "2026-04-31", "2026-10-25T00:00:00Z"])("rejects invalid date-only input %s", (date) => expect(validRentalDate(date)).toBe(false))
it.each(["week", "month", "quarter"] as const)("rejects year overflow for %s", (period) => expect(() => renewalDate("9999-12-31", period)).toThrow())

it.each([
  { rental_started_on: null },
  { rental_started_on: "2026-02-30" },
  { rental_started_on: "2026-02-01" },
])("does not select a malformed lease with %j", (change) => {
  expect(rentalEligible({ ownership_type: "rented", status: "available", rental_expires_on: "2026-01-31", rental_returned_on: null, rental_revision: 0, ...change })).toBe(false)
})
