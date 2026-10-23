export type DynamicVisionDifficulty = 1 | 2 | 3 | 4 | 5

export type DynamicVisionOutcome = "correct" | "incorrect" | "skipped"

export const DYNAMIC_VISION_LEVELS: readonly {
  level: DynamicVisionDifficulty
  digit_count: number
  nominal_display_ms: number
  coefficient_tenths: number
}[] = [
  { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
  { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
  { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
  { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
  { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
]

export function createDynamicVisionDigits(
  level: DynamicVisionDifficulty,
  random: () => number = Math.random,
): string {
  const { digit_count } = DYNAMIC_VISION_LEVELS[level - 1]
  return Array.from({ length: digit_count }, () =>
    String(Math.floor(random() * 10)),
  ).join("")
}

export function normalizeDynamicVisionInput(value: string, maxLength: number): string {
  return value.replace(/[^0-9]/g, "").slice(0, maxLength)
}

export function judgeDynamicVisionAnswer(
  expected: string,
  answer: string,
): "correct" | "incorrect" {
  return expected === answer ? "correct" : "incorrect"
}

export function summarizeDynamicVision(
  level: DynamicVisionDifficulty,
  outcomes: readonly DynamicVisionOutcome[],
): {
  correctCount: number
  skippedCount: number
  baseScore: number
  coefficient: number
  score: number
} {
  if (outcomes.length !== 5) throw new RangeError("需要完成五题")
  const correctCount = outcomes.filter((value) => value === "correct").length
  const skippedCount = outcomes.filter((value) => value === "skipped").length
  return {
    correctCount,
    skippedCount,
    baseScore: correctCount * 10,
    coefficient: (10 + 2 * level) / 10,
    score: correctCount * (10 + 2 * level),
  }
}
