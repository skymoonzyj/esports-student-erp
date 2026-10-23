export type SequencePhase = "intro" | "showing" | "responding" | "result"

export type SequenceState = {
  phase: SequencePhase
  level: number
  sequence: number[]
  inputIndex: number
  failedStep: number | null
}

export function createInitialSequenceState(): SequenceState {
  return {
    phase: "intro",
    level: 1,
    sequence: [],
    inputIndex: 0,
    failedStep: null,
  }
}

export function tileFromRandom(randomValue: number): number {
  return Math.max(0, Math.min(8, Math.floor(randomValue * 9)))
}

export function startSequence(
  state: SequenceState,
  randomValue: number,
): SequenceState {
  return {
    phase: "showing",
    level: state.level,
    sequence: [tileFromRandom(randomValue)],
    inputIndex: 0,
    failedStep: null,
  }
}

export function beginResponse(state: SequenceState): SequenceState {
  if (state.phase !== "showing") return state

  return { ...state, phase: "responding", inputIndex: 0 }
}

export function submitSequenceTile(
  state: SequenceState,
  tile: number,
  randomValue: number,
): SequenceState {
  if (state.phase !== "responding") return state

  if (state.sequence[state.inputIndex] !== tile) {
    return {
      ...state,
      phase: "result",
      failedStep: state.inputIndex + 1,
    }
  }

  const nextInputIndex = state.inputIndex + 1
  if (nextInputIndex < state.sequence.length) {
    return { ...state, inputIndex: nextInputIndex }
  }

  return {
    phase: "showing",
    level: state.level + 1,
    sequence: [...state.sequence, tileFromRandom(randomValue)],
    inputIndex: 0,
    failedStep: null,
  }
}

export function restartSequence(): SequenceState {
  return createInitialSequenceState()
}
