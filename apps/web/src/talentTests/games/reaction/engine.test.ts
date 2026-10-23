import { expect, it } from "vitest"

import {
  appendReactionSample,
  averageReactionTime,
  measureReactionTime,
} from "./engine"

it("五次有效反应后才汇总，抢点不算试次", () => {
  expect(averageReactionTime([200, 220, 240, 260])).toBeNull()
  expect(averageReactionTime([200, 220, 240, 260, 281])).toBe(240)
  expect(appendReactionSample([218, 241], null)).toEqual([218, 241])
  expect(measureReactionTime(100, 100.2)).toBe(0)
})

it("rejects invalid clocks and never appends more than five samples", () => {
  expect(measureReactionTime(Number.NaN, 200)).toBeNull()
  expect(measureReactionTime(201, 200)).toBeNull()
  expect(appendReactionSample([1, 2, 3, 4, 5], 6)).toEqual([1, 2, 3, 4, 5])
  expect(averageReactionTime([1, 2, 3, 4, -1])).toBeNull()
})
