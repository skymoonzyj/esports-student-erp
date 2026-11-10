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

  it.each([
    ["2026-10-24T00:00:00+08:00", "2026-10-24", "2026-10-23"],
    ["2026-10-24T18:00:00+08:00", "2026-10-24", "2026-10-24"],
    ["2026-10-23T16:00:00Z", "2026-10-24", "2026-10-23"],
    ["2026-11-01T00:00:00+08:00", "2026-11-01", "2026-10-31"],
    ["2027-01-01T00:00:00+08:00", "2027-01-01", "2026-12-31"],
    ["2028-03-01T00:00:00+08:00", "2028-03-01", "2028-02-29"],
    ["2028-02-29T00:00:00+08:00", "2028-02-29", "2028-02-28"],
    ["2026-10-24T00:00:01+08:00", "2026-10-24", "2026-10-24"],
    ["2026-10-24T00:01:00+08:00", "2026-10-24", "2026-10-24"],
    ["2026-10-24T01:00:00+08:00", "2026-10-24", "2026-10-24"],
    ["2026-10-24T00:00:00.001+08:00", "2026-10-24", "2026-10-23"],
    ["2026-10-23T16:00:00.999Z", "2026-10-24", "2026-10-23"],
    ["2026-10-24T00:00:00.999999+08:00", "2026-10-24", "2026-10-23"],
    ["2026-10-23T15:59:59.999Z", "2026-10-23", "2026-10-23"],
  ])("matches departure reminder boundaries for %s", (timestamp, housingDate, teachingDate) => {
    expect(formatDateOnly(timestamp)).toBe(housingDate)
    expect(exclusiveEndToDateInput(timestamp)).toBe(teachingDate)
  })
})
