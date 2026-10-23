import { describe, expect, it } from "vitest"

import {
  DYNAMIC_VISION_LEVELS,
  createDynamicVisionDigits,
  judgeDynamicVisionAnswer,
  normalizeDynamicVisionInput,
  summarizeDynamicVision,
  type DynamicVisionDifficulty,
  type DynamicVisionOutcome,
} from "./engine"

describe("dynamic vision engine", () => {
  it("retains leading zeroes and repeated digits", () => {
    expect(createDynamicVisionDigits(5, () => 0)).toBe("000000")
    expect(createDynamicVisionDigits(1, () => 0.999)).toBe("999")
    expect(judgeDynamicVisionAnswer("001", "001")).toBe("correct")
    expect(judgeDynamicVisionAnswer("001", "1")).toBe("incorrect")
    expect(normalizeDynamicVisionInput("00x1234", 3)).toBe("001")
  })

  it("uses the frozen five-level table", () => {
    expect(DYNAMIC_VISION_LEVELS).toEqual([
      { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
      { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
      { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
      { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
      { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
    ])
  })

  it("scores the selected level only", () => {
    expect(
      summarizeDynamicVision(4, [
        "correct",
        "correct",
        "correct",
        "correct",
        "skipped",
      ]),
    ).toEqual({
      correctCount: 4,
      skippedCount: 1,
      baseScore: 40,
      coefficient: 1.8,
      score: 72,
    })
  })

  it("rejects an outcome list that does not contain five questions", () => {
    expect(() => summarizeDynamicVision(1, [])).toThrow(
      new RangeError("需要完成五题"),
    )
  })

  const expectedScores: readonly (readonly number[])[] = [
    [0, 12, 24, 36, 48, 60],
    [0, 14, 28, 42, 56, 70],
    [0, 16, 32, 48, 64, 80],
    [0, 18, 36, 54, 72, 90],
    [0, 20, 40, 60, 80, 100],
  ]

  for (const [index, expected] of expectedScores.entries()) {
    for (let correct = 0; correct <= 5; correct += 1) {
      it(`level ${index + 1}, correct ${correct}`, () => {
        const outcomes: DynamicVisionOutcome[] = Array.from(
          { length: 5 },
          (_, questionIndex) =>
            questionIndex < correct ? "correct" : "skipped",
        )
        expect(
          summarizeDynamicVision(
            (index + 1) as DynamicVisionDifficulty,
            outcomes,
          ).score,
        ).toBe(expected[correct])
      })
    }
  }
})
