export const REACTION_TRIAL_COUNT = 5

export function measureReactionTime(startedAt: number, endedAt: number): number | null {
  if (!Number.isFinite(startedAt) || !Number.isFinite(endedAt) || endedAt < startedAt) {
    return null
  }

  return Math.max(0, Math.round(endedAt - startedAt))
}

export function appendReactionSample(
  samples: readonly number[],
  elapsed: number | null,
): number[] {
  if (
    elapsed === null
    || !Number.isFinite(elapsed)
    || elapsed < 0
    || samples.length >= REACTION_TRIAL_COUNT
  ) {
    return [...samples]
  }

  return [...samples, Math.round(elapsed)]
}

export function averageReactionTime(samples: readonly number[]): number | null {
  if (
    samples.length !== REACTION_TRIAL_COUNT
    || samples.some((sample) => !Number.isFinite(sample) || sample < 0)
  ) {
    return null
  }

  return Math.round(
    samples.reduce((total, sample) => total + sample, 0) / REACTION_TRIAL_COUNT,
  )
}
