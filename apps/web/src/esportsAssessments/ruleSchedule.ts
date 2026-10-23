import { nextU32, type TaskConfig } from './contracts'

export interface DodgeChoicePlanEntry {
  readonly safe_option_id: number
  readonly warning_at_ms: number
  readonly activation_at_ms: number
  readonly resolved_at_ms: number
}

export function buildDodgeChoicePlan(
  seed: number,
  params: Extract<TaskConfig['params'], { kind: 'dodge_choice' }>,
): readonly DodgeChoicePlanEntry[] {
  nextU32(seed)
  const choices = params.warning_start_max_ms - params.warning_start_min_ms + 1
  let state = seed
  return Array.from({ length: params.opportunity_count }, (_, index) => {
    state = nextU32(state)
    const warning_at_ms = index * params.window_ms + params.warning_start_min_ms + state % choices
    state = nextU32(state)
    const activation_at_ms = warning_at_ms + params.warning_ms
    return {
      safe_option_id: state % 4,
      warning_at_ms,
      activation_at_ms,
      resolved_at_ms: activation_at_ms + params.burst_ms,
    }
  })
}
