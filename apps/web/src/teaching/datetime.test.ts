import { expect, it } from "vitest"

import {
  formatShanghaiDateTime,
  initializeCohortDates,
  shanghaiDateEndExclusive,
  shanghaiLocalToIso,
  yuanToCents,
} from "./datetime"

it("initializes inclusive cohort dates without using the host timezone", () => {
  expect(initializeCohortDates("2026-08-01", "2026-08-07")).toEqual({
    startsAt: "2026-08-01T00:00",
    endsAt: "2026-08-08T00:00",
    checkInAt: "2026-08-01T00:00",
    checkOutAt: "2026-08-08T00:00",
  })
  expect(shanghaiDateEndExclusive("2028-02-29")).toBe("2028-03-01T00:00:00+08:00")
})

it("serializes Shanghai wall time with an explicit offset and formats UTC responses", () => {
  expect(shanghaiLocalToIso("2026-08-01T09:30")).toBe("2026-08-01T09:30:00+08:00")
  expect(formatShanghaiDateTime("2026-07-31T16:00:00Z")).toBe("2026-08-01 00:00")
})

it("converts yuan strings to cents without floating-point rounding", () => {
  expect(yuanToCents("2600.05")).toBe(260_005)
  expect(yuanToCents("0.1")).toBe(10)
  expect(() => yuanToCents("1.005")).toThrow("金额最多保留两位小数")
})
