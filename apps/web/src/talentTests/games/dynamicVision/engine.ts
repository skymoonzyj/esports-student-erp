import type { DynamicVisionLevel, DynamicVisionRule } from "../../contracts"

export type DynamicVisionDifficulty = 1 | 2 | 3 | 4 | 5 | 6

export type DynamicVisionOutcome = "correct" | "incorrect" | "skipped"

export const DYNAMIC_VISION_LEVELS = [
  { level: 1, digit_count: 3, nominal_display_ms: 100, coefficient_tenths: 12 },
  { level: 2, digit_count: 4, nominal_display_ms: 100, coefficient_tenths: 14 },
  { level: 3, digit_count: 5, nominal_display_ms: 100, coefficient_tenths: 16 },
  { level: 4, digit_count: 5, nominal_display_ms: 36, coefficient_tenths: 18 },
  { level: 5, digit_count: 6, nominal_display_ms: 36, coefficient_tenths: 20 },
  { level: 6, digit_count: 7, nominal_display_ms: 36, coefficient_tenths: 22 },
] as const satisfies readonly DynamicVisionLevel[]

export const DYNAMIC_VISION_RULE: DynamicVisionRule = {
  kind: "dynamic_vision", engine_version: "dynamic-vision-v2",
  source_label: "内部试行训练规则", unit: "count", base_points_per_correct: 10,
  max_score: 100, levels: [...DYNAMIC_VISION_LEVELS],
}

export function createDynamicVisionDigits(
  level: DynamicVisionDifficulty | DynamicVisionLevel,
  random: () => number = Math.random,
): string {
  const { digit_count } = typeof level === "number" ? DYNAMIC_VISION_LEVELS[level - 1] : level
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
  rule: DynamicVisionRule = DYNAMIC_VISION_RULE,
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
  const selectedRule = rule.levels.find((candidate) => candidate.level === level)
  if (!selectedRule) throw new RangeError("不支持的难度")
  const maxScore = rule.engine_version === "dynamic-vision-v2" ? rule.max_score : 100
  return {
    correctCount,
    skippedCount,
    baseScore: correctCount * rule.base_points_per_correct,
    coefficient: selectedRule.coefficient_tenths / 10,
    score: Math.min(maxScore, correctCount * rule.base_points_per_correct * selectedRule.coefficient_tenths / 10),
  }
}
