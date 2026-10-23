import { describe, expect, it } from "vitest"

import {
  createNumberString,
  evaluateNumberAnswer,
  memoryDurationMs,
} from "./engine"

describe("number-memory engine", () => {
  it("失败位数不计入最长正确位数", () => {
    expect(memoryDurationMs(1)).toBe(1800)
    expect(memoryDurationMs(5)).toBe(5000)
    const result = evaluateNumberAnswer("12", "21", 2)
    expect(result.correct).toBe(false)
    if (result.correct) throw new Error("expected failed answer")
    expect(result.scoreDigits).toBe(1)
    expect(result.failedLevel).toBe(2)
  })

  it("creates the requested number of digits without a leading zero", () => {
    const draws = [0, 0, 0.999999999]
    expect(createNumberString(3, () => draws.shift() ?? 0)).toBe("109")
    expect(() => createNumberString(0)).toThrow(RangeError)
  })

  it("uses exact string equality so a typed leading zero is not ignored", () => {
    expect(evaluateNumberAnswer("1", "01", 1)).toEqual({
      correct: false,
      failedLevel: 1,
      scoreDigits: 0,
    })
  })
})
