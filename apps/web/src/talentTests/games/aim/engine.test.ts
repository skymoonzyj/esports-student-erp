import { describe, expect, it } from "vitest"

import {
  beginAimTest,
  clampTargetPosition,
  createAimState,
  randomTargetPosition,
  recordAimHit,
  recordAimMiss,
  targetDiameterForWidth,
} from "./engine"

describe("aim engine", () => {
  it("固定 30 次命中，失误不重置计时", () => {
    let state = beginAimTest(createAimState(), 5000)
    expect(recordAimMiss(state)).toBe(state)
    for (let i = 1; i <= 30; i += 1) {
      state = recordAimHit(state, 5000 + i * 100)
    }

    expect(state.phase).toBe("result")
    expect(state.scoreMs).toBe(100)
    expect(state.intervalsMs).toEqual(Array.from({ length: 30 }, () => 100))
    expect(state.totalElapsedMs).toBe(3000)
    expect(targetDiameterForWidth(700)).toBe(100)
    expect(targetDiameterForWidth(699)).toBe(64)
  })

  it("uses the same clamped intervals for the list, total and score", () => {
    let state = beginAimTest(createAimState(), 100)
    for (let i = 0; i < 30; i += 1) state = recordAimHit(state, 100)

    expect(state.intervalsMs).toEqual(Array.from({ length: 30 }, () => 1))
    expect(state.totalElapsedMs).toBe(30)
    expect(state.scoreMs).toBe(1)
    expect(recordAimHit(state, 1000)).toBe(state)
  })

  it("keeps random and clamped target centers fully inside the arena", () => {
    expect(randomTargetPosition(800, 500, 100, () => 0)).toEqual({ x: 50, y: 50 })
    expect(randomTargetPosition(800, 500, 100, () => 1)).toEqual({ x: 750, y: 450 })
    expect(randomTargetPosition(40, 30, 64, () => 0.5)).toEqual({ x: 20, y: 15 })
    expect(clampTargetPosition({ x: -10, y: 1000 }, 800, 500, 100)).toEqual({
      x: 50,
      y: 450,
    })
  })
})
