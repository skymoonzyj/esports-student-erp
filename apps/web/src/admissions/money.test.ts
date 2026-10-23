import { describe, expect, it } from "vitest"

import { formatCny } from "./money"

describe("formatCny", () => {
  it("formats integer cents without floating point drift", () => {
    expect(formatCny(3_680_005)).toBe("¥36,800.05")
  })

  it("keeps zero and sub-yuan amounts at two decimal places", () => {
    expect(formatCny(0)).toBe("¥0.00")
    expect(formatCny(1)).toBe("¥0.01")
  })

  it("renders hidden amount projections without inventing a value", () => {
    expect(formatCny(null)).toBe("—")
  })

  it("rejects unsafe or fractional cent values", () => {
    expect(() => formatCny(1.5)).toThrow(RangeError)
    expect(() => formatCny(Number.MAX_SAFE_INTEGER + 1)).toThrow(RangeError)
  })
})
