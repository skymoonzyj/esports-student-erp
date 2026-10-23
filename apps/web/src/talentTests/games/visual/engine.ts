export type VisualMemoryPhase =
  | "memorizing"
  | "answering"
  | "level-complete"
  | "lost-life"
  | "result"

export type VisualMemoryRound = {
  phase: VisualMemoryPhase
  level: number
  lives: number
  gridSize: number
  targets: number[]
  selected: number[]
  misses: number[]
}

type RandomSource = () => number

export function gridSizeForLevel(level: number): number {
  const targetCount = Math.max(3, Math.floor(level) + 2)
  return Math.max(3, Math.ceil(Math.sqrt(targetCount)) + 1)
}

export function createPattern(
  cellCount: number,
  targetCount: number,
  random: RandomSource = Math.random,
): number[] {
  const cells = Array.from({ length: cellCount }, (_, index) => index)

  for (let index = cells.length - 1; index > 0; index -= 1) {
    const randomIndex = Math.floor(random() * (index + 1))
    ;[cells[index], cells[randomIndex]] = [cells[randomIndex], cells[index]]
  }

  return cells.slice(0, Math.min(targetCount, cellCount))
}

export function createRound(
  level: number,
  lives: number,
  random: RandomSource = Math.random,
): VisualMemoryRound {
  const safeLevel = Math.max(1, Math.floor(level))
  const gridSize = gridSizeForLevel(safeLevel)

  return {
    phase: "memorizing",
    level: safeLevel,
    lives: Math.max(0, Math.floor(lives)),
    gridSize,
    targets: createPattern(gridSize ** 2, safeLevel + 2, random),
    selected: [],
    misses: [],
  }
}

export function startVisualMemory(
  random: RandomSource = Math.random,
): VisualMemoryRound {
  return createRound(1, 3, random)
}

export function beginAnswering(round: VisualMemoryRound): VisualMemoryRound {
  if (round.phase !== "memorizing") return round
  return { ...round, phase: "answering" }
}

export function selectTile(
  round: VisualMemoryRound,
  index: number,
): VisualMemoryRound {
  if (
    round.phase !== "answering" ||
    !Number.isInteger(index) ||
    index < 0 ||
    index >= round.gridSize ** 2 ||
    round.selected.includes(index) ||
    round.misses.includes(index)
  ) {
    return round
  }

  if (round.targets.includes(index)) {
    const selected = [...round.selected, index]
    return {
      ...round,
      phase:
        selected.length === round.targets.length
          ? "level-complete"
          : "answering",
      selected,
    }
  }

  const misses = [...round.misses, index]
  if (misses.length < 3) return { ...round, misses }

  const lives = round.lives - 1
  return {
    ...round,
    phase: lives === 0 ? "result" : "lost-life",
    lives,
    misses,
  }
}

export function advanceAfterSuccess(
  round: VisualMemoryRound,
  random: RandomSource = Math.random,
): VisualMemoryRound {
  if (round.phase !== "level-complete") return round
  return createRound(round.level + 1, round.lives, random)
}
