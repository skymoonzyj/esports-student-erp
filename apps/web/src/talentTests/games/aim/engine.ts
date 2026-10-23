export const AIM_TARGET_COUNT = 30

export type AimPhase = "intro" | "playing" | "result"

export interface AimState {
  phase: AimPhase
  hits: number
  remaining: number
  intervalsMs: number[]
  totalElapsedMs: number
  targetStartedAt: number | null
  scoreMs: number | null
}

export interface TargetPosition {
  x: number
  y: number
}

export function createAimState(): AimState {
  return {
    phase: "intro",
    hits: 0,
    remaining: AIM_TARGET_COUNT,
    intervalsMs: [],
    totalElapsedMs: 0,
    targetStartedAt: null,
    scoreMs: null,
  }
}

export function beginAimTest(_state: AimState, now: number): AimState {
  return {
    phase: "playing",
    hits: 0,
    remaining: AIM_TARGET_COUNT,
    intervalsMs: [],
    totalElapsedMs: 0,
    targetStartedAt: now,
    scoreMs: null,
  }
}

export function recordAimHit(state: AimState, now: number): AimState {
  if (state.phase !== "playing" || state.targetStartedAt === null) return state

  const elapsedMs = Math.max(1, now - state.targetStartedAt)
  const hits = state.hits + 1
  const intervalsMs = [...state.intervalsMs, elapsedMs]
  const totalElapsedMs = state.totalElapsedMs + elapsedMs
  const remaining = Math.max(0, AIM_TARGET_COUNT - hits)

  if (hits >= AIM_TARGET_COUNT) {
    return {
      phase: "result",
      hits: AIM_TARGET_COUNT,
      remaining: 0,
      intervalsMs,
      totalElapsedMs,
      targetStartedAt: null,
      scoreMs: Math.max(1, Math.round(totalElapsedMs / AIM_TARGET_COUNT)),
    }
  }

  return {
    phase: "playing",
    hits,
    remaining,
    intervalsMs,
    totalElapsedMs,
    targetStartedAt: now,
    scoreMs: null,
  }
}

export function recordAimMiss(state: AimState): AimState {
  return state
}

export function targetDiameterForWidth(width: number): number {
  return width >= 700 ? 100 : 64
}

function coordinateForAxis(
  length: number,
  diameter: number,
  random: () => number,
): number {
  const safeLength = Math.max(length, 0)
  const safeDiameter = Math.min(Math.max(diameter, 0), safeLength)
  const radius = safeDiameter / 2
  const range = Math.max(0, safeLength - safeDiameter)
  const fraction = Math.min(1, Math.max(0, random()))
  return radius + range * fraction
}

export function randomTargetPosition(
  width: number,
  height: number,
  diameter: number,
  random: () => number = Math.random,
): TargetPosition {
  return {
    x: coordinateForAxis(width, diameter, random),
    y: coordinateForAxis(height, diameter, random),
  }
}

export function clampTargetPosition(
  position: TargetPosition,
  width: number,
  height: number,
  diameter: number,
): TargetPosition {
  const horizontalRadius = Math.min(Math.max(diameter, 0), Math.max(width, 0)) / 2
  const verticalRadius = Math.min(Math.max(diameter, 0), Math.max(height, 0)) / 2
  return {
    x: Math.min(
      Math.max(position.x, horizontalRadius),
      Math.max(horizontalRadius, width - horizontalRadius),
    ),
    y: Math.min(
      Math.max(position.y, verticalRadius),
      Math.max(verticalRadius, height - verticalRadius),
    ),
  }
}
