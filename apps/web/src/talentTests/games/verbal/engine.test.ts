import { describe, expect, it } from "vitest"

import {
  CHINESE_WORDS,
  answerVerbalMemory,
  startVerbalMemory,
} from "./engine"

function sequenceRandom(...values: number[]) {
  let index = 0
  return () => {
    const value = values[index]
    index += 1
    if (value === undefined) throw new Error("随机数测试序列已耗尽")
    return value
  }
}

describe("verbal memory engine", () => {
  it("累计正确判断而非不同词数量，第三错结束", () => {
    let state = startVerbalMemory(["山川"], () => 0)
    state = answerVerbalMemory(state, "new", ["山川"], () => 0)
    state = answerVerbalMemory(state, "seen", ["山川"], () => 0)
    expect(state.score).toBe(2)

    for (let i = 0; i < 3; i += 1) {
      state = answerVerbalMemory(state, "new", ["山川"], () => 0)
    }

    expect(state.finished).toBe(true)
    expect(state.turn).toBe(5)
    expect(state.score).toBe(2)
    expect(state.lives).toBe(0)
  })

  it("uses the unchanged 192-word Chinese bank", () => {
    expect(CHINESE_WORDS).toHaveLength(192)
    expect(new Set(CHINESE_WORDS)).toHaveLength(192)
    expect(CHINESE_WORDS[0]).toBe("山川")
    expect(CHINESE_WORDS[191]).toBe("青蛙")
  })

  it("adds an incorrectly answered first word to seen words", () => {
    let state = startVerbalMemory(["山川"], () => 0)

    state = answerVerbalMemory(state, "seen", ["山川"], () => 0)
    expect(state.currentWord).toBe("山川")
    expect(state.lives).toBe(2)

    state = answerVerbalMemory(state, "seen", ["山川"], () => 0)
    expect(state.score).toBe(1)
    expect(state.lives).toBe(2)
  })

  it("chooses seen below the 50 percent boundary and unseen at the boundary", () => {
    const bank = ["山川", "海洋"]
    const initial = startVerbalMemory(bank, () => 0)

    const seenChoice = answerVerbalMemory(
      initial,
      "new",
      bank,
      sequenceRandom(0.49, 0),
    )
    expect(seenChoice.currentWord).toBe("山川")

    const unseenChoice = answerVerbalMemory(
      initial,
      "new",
      bank,
      sequenceRandom(0.5, 0),
    )
    expect(unseenChoice.currentWord).toBe("海洋")
  })

  it("rejects an empty word bank and ignores answers after completion", () => {
    expect(() => startVerbalMemory([], () => 0)).toThrow("词库不能为空")

    let state = startVerbalMemory(["山川"], () => 0)
    state = answerVerbalMemory(state, "seen", ["山川"], () => 0)
    state = answerVerbalMemory(state, "new", ["山川"], () => 0)
    state = answerVerbalMemory(state, "new", ["山川"], () => 0)
    expect(answerVerbalMemory(state, "new", ["山川"], () => 0)).toBe(state)
  })
})
