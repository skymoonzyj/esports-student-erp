export const CHIMP_BOARD_SIZE = 40

export type ChimpPhase = "intro" | "playing" | "interstitial" | "result"

export type ChimpState = {
  phase: ChimpPhase
  numberCount: number
  strikes: number
  board: Array<number | null>
  nextNumber: number
  masked: boolean
}

function emptyBoard(): Array<number | null> {
  return Array.from({ length: CHIMP_BOARD_SIZE }, () => null)
}

export function createChimpBoard(
  numberCount: number,
  random: () => number = Math.random,
): Array<number | null> {
  const board = emptyBoard()
  const availablePositions = Array.from(
    { length: CHIMP_BOARD_SIZE },
    (_, position) => position,
  )
  const safeCount = Math.min(
    CHIMP_BOARD_SIZE,
    Math.max(0, Math.floor(numberCount)),
  )

  for (let number = 1; number <= safeCount; number += 1) {
    const sample = random()
    const unit = Number.isFinite(sample)
      ? Math.min(Math.max(sample, 0), 1 - Number.EPSILON)
      : 0
    const availableIndex = Math.floor(unit * availablePositions.length)
    const [position] = availablePositions.splice(availableIndex, 1)
    board[position] = number
  }

  return board
}

export function createInitialChimpState(): ChimpState {
  return {
    phase: "intro",
    numberCount: 4,
    strikes: 0,
    board: emptyBoard(),
    nextNumber: 1,
    masked: false,
  }
}

export function startChimp(
  state: ChimpState,
  random: () => number = Math.random,
): ChimpState {
  if (state.phase !== "intro") return state
  return {
    ...state,
    phase: "playing",
    board: createChimpBoard(state.numberCount, random),
    nextNumber: 1,
    masked: false,
  }
}

export function continueChimp(
  state: ChimpState,
  random: () => number = Math.random,
): ChimpState {
  if (state.phase !== "interstitial") return state
  return {
    ...state,
    phase: "playing",
    board: createChimpBoard(state.numberCount, random),
    nextNumber: 1,
    masked: false,
  }
}

export function selectChimpCell(state: ChimpState, position: number): ChimpState {
  if (state.phase !== "playing") return state

  const selectedNumber = state.board[position]
  if (selectedNumber === null || selectedNumber === undefined) return state

  if (selectedNumber !== state.nextNumber) {
    const strikes = state.strikes + 1
    return {
      ...state,
      phase: strikes >= 3 ? "result" : "interstitial",
      strikes,
      board: emptyBoard(),
      nextNumber: 1,
      masked: false,
    }
  }

  const board = [...state.board]
  board[position] = null

  if (selectedNumber === state.numberCount) {
    const reachedLimit = state.numberCount >= CHIMP_BOARD_SIZE
    return {
      ...state,
      phase: reachedLimit ? "result" : "interstitial",
      numberCount: reachedLimit ? CHIMP_BOARD_SIZE : state.numberCount + 1,
      board: emptyBoard(),
      nextNumber: 1,
      masked: false,
    }
  }

  return {
    ...state,
    board,
    nextNumber: state.nextNumber + 1,
    masked: state.numberCount >= 5,
  }
}

export function restartChimp(): ChimpState {
  return createInitialChimpState()
}
