import { describe, expect, it } from 'vitest'
import { acceptResponse, remainingLeaseMs, matchesAttempt, acceptsSave } from './hostState'
import { fixtureAttempt, fixturePending } from './testSupport/fixtures'

describe('host identity and monotonic lease', () => {
  it('rejects every stale account or request generation', () => {
    expect(acceptResponse(1, 2, 'a', 'b')).toBe(false)
    expect(acceptResponse(2, 2, 'a', 'b')).toBe(false)
    expect(acceptResponse(1, 2, 'b', 'b')).toBe(false)
    expect(acceptResponse(2, 2, 'b', 'b')).toBe(true)
  })
  it('charges request time, independent of client calendar time', () => {
    expect(remainingLeaseMs(fixtureAttempt(), 28000)).toBe(62000)
    expect(remainingLeaseMs(fixtureAttempt(), 27000)).toBe(63000)
    expect(remainingLeaseMs(fixtureAttempt(), 100000)).toBe(0)
  })
  it('never binds another mode, suite, parent or attempt cache', () => {
    const attempt = fixtureAttempt()
    const pending = fixturePending()
    expect(matchesAttempt(pending, attempt)).toBe(true)
    for (const field of ['attempt_id', 'session_id', 'suite_id', 'test_id', 'mode'] as const) {
      expect(matchesAttempt({ ...pending, [field]: 'wrong' } as typeof pending, attempt)).toBe(false)
    }
  })
  it('rejects a practice save returned for a formal attempt', () => {
    expect(acceptsSave(fixtureAttempt(), { mode: 'practice', result_id: 'x', session: null, scored: { metrics: {}, score: null, grade: null, quality_flags: [] } })).toBe(false)
  })
})
