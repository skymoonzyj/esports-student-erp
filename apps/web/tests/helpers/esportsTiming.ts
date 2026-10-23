/** Ephemeral read-only native evidence, never a clock/input control or persisted DTO. */
export interface NativeRunTiming { startedAt: number; deadline: number; finishedAt: number }

export function nativeActiveDuration(timing: NativeRunTiming): number {
  if (![timing.startedAt, timing.deadline, timing.finishedAt].every(Number.isFinite)
    || timing.startedAt < 0 || timing.deadline !== timing.startedAt + 60_000
    || timing.finishedAt < timing.deadline) {
    throw new Error('native assessment active-window proof is invalid')
  }
  return timing.finishedAt - timing.startedAt
}
