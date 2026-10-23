import { describe, expect, it } from "vitest"

import {
  CHIMP_BOARD_SIZE,
  continueChimp,
  createChimpBoard,
  createInitialChimpState,
  restartChimp,
  selectChimpCell,
  startChimp,
  type ChimpState,
} from "./engine"

describe("chimp engine", () => {
  it("creates a deterministic 40-slot board without duplicate numbers", () => {
    const board = createChimpBoard(4, () => 0)

    expect(board).toHaveLength(CHIMP_BOARD_SIZE)
    expect(board.slice(0, 5)).toEqual([1, 2, 3, 4, null])
    expect(board.filter((cell) => cell !== null)).toEqual([1, 2, 3, 4])
  })

  it("does not mask level four but masks level five after the first correct cell", () => {
    const levelFour = startChimp(createInitialChimpState(), () => 0)
    const fourAfterFirst = selectChimpCell(levelFour, 0)

    expect(fourAfterFirst.masked).toBe(false)
    expect(fourAfterFirst.nextNumber).toBe(2)

    const levelFive = continueChimp(
      {
        ...levelFour,
        phase: "interstitial",
        numberCount: 5,
      },
      () => 0,
    )
    const fiveAfterFirst = selectChimpCell(levelFive, 0)

    expect(fiveAfterFirst.masked).toBe(true)
    expect(fiveAfterFirst.nextNumber).toBe(2)
  })

  it("ignores empty cells and retries the same difficulty after a mistake", () => {
    const playing = startChimp(createInitialChimpState(), () => 0)

    expect(selectChimpCell(playing, 39)).toBe(playing)

    const missed = selectChimpCell(playing, 1)
    expect(missed).toMatchObject({
      phase: "interstitial",
      numberCount: 4,
      strikes: 1,
      nextNumber: 1,
      masked: false,
    })

    const retried = continueChimp(missed, () => 0)
    expect(retried).toMatchObject({
      phase: "playing",
      numberCount: 4,
      strikes: 1,
      nextNumber: 1,
      masked: false,
    })
    expect(retried.board.slice(0, 4)).toEqual([1, 2, 3, 4])
  })

  it("第三次错误结束当前难度，首关未通过不记为 4", () => {
    const board = createChimpBoard(4, () => 0)
    const state = {
      phase: "playing" as const,
      numberCount: 4,
      strikes: 2,
      board,
      nextNumber: 1,
      masked: false,
    }

    const lost = selectChimpCell(state, 1)

    expect(lost.phase).toBe("result")
    expect(lost.strikes).toBe(3)
    expect(lost.numberCount).toBe(4)
  })

  it("advances after a complete round and distinguishes the 40-cell engine cap", () => {
    let state = startChimp(createInitialChimpState(), () => 0)
    for (let position = 0; position < 4; position += 1) {
      state = selectChimpCell(state, position)
    }

    expect(state).toMatchObject({ phase: "interstitial", numberCount: 5 })

    let capped: ChimpState = {
      ...state,
      phase: "playing",
      numberCount: CHIMP_BOARD_SIZE,
      board: createChimpBoard(CHIMP_BOARD_SIZE, () => 0),
      nextNumber: 1,
      masked: false,
    }
    for (let position = 0; position < CHIMP_BOARD_SIZE; position += 1) {
      capped = selectChimpCell(capped, position)
    }

    expect(capped).toMatchObject({
      phase: "result",
      numberCount: CHIMP_BOARD_SIZE,
      strikes: 0,
    })
    expect(restartChimp()).toEqual(createInitialChimpState())
  })
})
