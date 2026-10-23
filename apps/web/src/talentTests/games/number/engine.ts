export type NumberAnswerResult =
  | { correct: true; nextLevel: number }
  | { correct: false; failedLevel: number; scoreDigits: number }

export function createNumberString(
  length: number,
  random: () => number = Math.random,
): string {
  if (!Number.isSafeInteger(length) || length < 1) {
    throw new RangeError("Digit length must be a positive safe integer.")
  }

  return Array.from({ length }, (_, index) => {
    const possibilities = index === 0 ? 9 : 10
    const offset = index === 0 ? 1 : 0
    const draw = Math.max(0, Math.min(random(), 1 - Number.EPSILON))
    return String(offset + Math.floor(draw * possibilities))
  }).join("")
}

export function memoryDurationMs(digitCount: number): number {
  return 1000 + 800 * digitCount
}

export function evaluateNumberAnswer(
  expected: string,
  answer: string,
  level: number,
): NumberAnswerResult {
  if (answer.length > 0 && expected.length > 0 && answer === expected) {
    return { correct: true, nextLevel: level + 1 }
  }

  return {
    correct: false,
    failedLevel: level,
    scoreDigits: Math.max(level - 1, 0),
  }
}
