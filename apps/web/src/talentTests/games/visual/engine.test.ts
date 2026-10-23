import { describe, expect, it } from "vitest"

import {
  advanceAfterSuccess,
  beginAnswering,
  createRound,
  gridSizeForLevel,
  selectTile,
  startVisualMemory,
} from "./engine"

describe("visual-memory engine", () => {
  it("同一错误格只累计一次，三个不同错误格扣命", () => {
    let state = beginAnswering(createRound(1, 3, () => 0))
    const wrong = Array.from({ length: 9 }, (_, i) => i).filter(
      (i) => !state.targets.includes(i),
    )
    state = selectTile(state, wrong[0])
    expect(selectTile(state, wrong[0])).toBe(state)
    state = selectTile(state, wrong[1])
    state = selectTile(state, wrong[2])
    expect(state.phase).toBe("lost-life")
    expect(state.lives).toBe(2)
    expect(state.misses).toHaveLength(3)
  })

  it("保留源棋盘递增规则并忽略展示期与越界输入", () => {
    const state = startVisualMemory(() => 0)

    expect(state.gridSize).toBe(3)
    expect(state.targets).toHaveLength(3)
    expect(gridSizeForLevel(7)).toBe(4)
    expect(selectTile(state, state.targets[0])).toBe(state)

    const answering = beginAnswering(state)
    expect(selectTile(answering, -1)).toBe(answering)
    expect(selectTile(answering, 9)).toBe(answering)
  })

  it("允许带误点通过并在下一轮重置本轮错误格", () => {
    let state = beginAnswering(createRound(1, 3, () => 0))
    const wrong = Array.from({ length: 9 }, (_, index) => index).find(
      (index) => !state.targets.includes(index),
    )!

    state = selectTile(state, wrong)
    for (const target of state.targets) state = selectTile(state, target)

    expect(state.phase).toBe("level-complete")
    expect(state.misses).toEqual([wrong])

    const next = advanceAfterSuccess(state, () => 0)
    expect(next.level).toBe(2)
    expect(next.lives).toBe(3)
    expect(next.phase).toBe("memorizing")
    expect(next.misses).toEqual([])
  })
})
