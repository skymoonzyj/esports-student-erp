import { expect, it } from 'vitest'
import { nativeActiveDuration } from '../../tests/helpers/esportsTiming'
it('requires the full explicit lane deadline without weakening the legacy 60-second proof', () => {
  const timing = { startedAt: 3000, deadline: 303000, finishedAt: 303010 }
  expect(nativeActiveDuration(timing, 300000)).toBe(300010)
  expect(() => nativeActiveDuration(timing)).toThrow()
  expect(() => nativeActiveDuration({ ...timing, deadline: 63000 }, 300000)).toThrow()
})

it('accepts unequal RAF/React commit lag without treating DOM duration as the business window', () => {
  const runningCommit = 3220
  const pendingCommit = 63020
  expect(pendingCommit - runningCommit).toBeLessThan(60000)
  expect(nativeActiveDuration({ startedAt: 3000, deadline: 63000, finishedAt: 63010 })).toBe(60010)
})

it.each([
  { startedAt: 3000, deadline: 62999, finishedAt: 63010 },
  { startedAt: 3000, deadline: 63000, finishedAt: 62999 },
  { startedAt: 3000, deadline: 63001, finishedAt: 63010 },
  { startedAt: NaN, deadline: 63000, finishedAt: 63010 },
])('rejects a short or malformed native active-window proof: %j', timing => {
  expect(() => nativeActiveDuration(timing)).toThrow()
})
