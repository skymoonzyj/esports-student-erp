import { describe, expect, it } from "vitest"

import {
  dateInputToShanghaiEndExclusiveIso,
  dateInputToShanghaiIso,
  exclusiveEndToDateInput,
  formatAuditDateTime,
  formatDateOnly,
  formatExclusiveEndDate,
  isDateInput,
  nextDateInput,
  parseDateInput,
  todayDateInput,
} from "./date"

describe("business date helpers", () => {
  it("requires a four-digit year and a real calendar date", () => {
    expect(isDateInput("2026-08-21")).toBe(true)
    expect(isDateInput("26-08-21")).toBe(false)
    expect(isDateInput("2026-02-30")).toBe(false)
    expect(() => parseDateInput("2026-2-01")).toThrow("YYYY-MM-DD")
  })

  it("handles leap-day boundaries", () => {
    expect(nextDateInput("2028-02-29")).toBe("2028-03-01")
    expect(dateInputToShanghaiIso("2026-08-21")).toBe("2026-08-21T00:00:00+08:00")
    expect(dateInputToShanghaiEndExclusiveIso("2026-08-21")).toBe("2026-08-22T00:00:00+08:00")
  })

  it("formats timestamps in Shanghai without a time component", () => {
    expect(formatDateOnly("2026-08-20T16:00:00Z")).toBe("2026-08-21")
    expect(formatDateOnly("2026-08-21")).toBe("2026-08-21")
    expect(todayDateInput(new Date("2026-08-20T16:30:00Z"))).toBe("2026-08-21")
  })

  it("formats exclusive midnight bounds as inclusive end dates", () => {
    expect(formatExclusiveEndDate("2026-08-08T00:00:00+08:00")).toBe("2026-08-07")
    expect(formatExclusiveEndDate("2026-08-07T16:00:00Z")).toBe("2026-08-07")
    expect(exclusiveEndToDateInput("2028-03-01T00:00:00+08:00")).toBe("2028-02-29")
    expect(exclusiveEndToDateInput("2030-08-10T18:00:00+08:00")).toBe("2030-08-10")
    expect(exclusiveEndToDateInput(null)).toBe("")
  })

  it("keeps hours and minutes visible for audit timestamps", () => {
    expect(formatAuditDateTime("2026-07-26T02:02:03Z")).toBe("2026-07-26 10:02:03")
  })
})
