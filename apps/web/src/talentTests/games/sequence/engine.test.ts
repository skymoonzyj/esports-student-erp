import { describe, expect, it } from "vitest"

import {
  beginResponse,
  createInitialSequenceState,
  restartSequence,
  startSequence,
  submitSequenceTile,
} from "./engine"

describe("sequence engine", () => {
  it("失败第一关是到达 1、通过 0，并按原序列追加下一格", () => {
    const first = beginResponse(startSequence(createInitialSequenceState(), 0.01))
    const lost = submitSequenceTile(first, 8, 0.99)

    expect(lost.phase).toBe("result")
    expect(lost.level).toBe(1)
    expect(lost.failedStep).toBe(1)

    const second = submitSequenceTile(first, 0, 0.99)
    expect(second.level).toBe(2)
    expect(second.sequence).toEqual([0, 8])
  })

  it("accepts two consecutive presses of the same tile", () => {
    const levelOne = beginResponse(startSequence(createInitialSequenceState(), 0))
    const levelTwo = beginResponse(submitSequenceTile(levelOne, 0, 0))
    const afterFirstPress = submitSequenceTile(levelTwo, 0, 0.5)
    const afterSecondPress = submitSequenceTile(afterFirstPress, 0, 0.5)

    expect(afterFirstPress.phase).toBe("responding")
    expect(afterFirstPress.inputIndex).toBe(1)
    expect(afterSecondPress.phase).toBe("showing")
    expect(afterSecondPress.level).toBe(3)
    expect(afterSecondPress.sequence).toEqual([0, 0, 4])
  })

  it("ignores input outside the response phase and records the failed position", () => {
    const showing = startSequence(createInitialSequenceState(), 0)
    expect(submitSequenceTile(showing, 8, 0.5)).toBe(showing)

    const levelTwo = beginResponse(
      submitSequenceTile(beginResponse(showing), 0, 0.99),
    )
    const afterFirst = submitSequenceTile(levelTwo, 0, 0.5)
    const lost = submitSequenceTile(afterFirst, 7, 0.5)

    expect(lost.phase).toBe("result")
    expect(lost.level).toBe(2)
    expect(lost.failedStep).toBe(2)
    expect(restartSequence()).toEqual(createInitialSequenceState())
  })
})
