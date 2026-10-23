import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { fixturePending, fixtureQuality, fixtureRaw } from './testSupport/fixtures'
import historicalPending from './testSupport/pending-legacy-v1-v2.json'
import type { PendingSubmission, SuiteId, TestId } from './contracts'
import { assessmentKeys } from './keys'
import { ASSESSMENT_SUITE_AVAILABILITY } from './availability'
import {
  clearAssessmentPending,
  clearAssessmentPendingExcept,
  hasVolatilePending,
  loadPending,
  savePending,
  removePending,
} from './pending'

const ACCOUNT = '11111111-1111-4111-8111-111111111111'
const OTHER_ACCOUNT = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const NOW = new Date('2026-09-22T00:00:00.000Z')

it('removes only the exact identity and operation, retaining other pending results', () => {
  const a = fixturePending({ created_at: NOW.toISOString() })
  const b = fixturePending({ attempt_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', created_at: NOW.toISOString() })
  savePending(a)
  savePending(b)
  removePending({ ...a, operation_key: b.attempt_id })
  expect(loadPending(a.account_id)).toHaveLength(2)
  removePending(a)
  expect(loadPending(a.account_id)).toEqual([b])
})

it('removes matching volatile pending even when storage cannot be enumerated', () => {
  const a = fixturePending({ created_at: NOW.toISOString() })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota') })
  savePending(a)
  vi.spyOn(Storage.prototype, 'key').mockImplementation(() => { throw new Error('denied') })
  removePending(a)
  expect(loadPending(a.account_id)).toEqual([])
})

it('keeps every production suite closed until acceptance persists an opening', () => {
  expect(ASSESSMENT_SUITE_AVAILABILITY).toEqual({
    'pc-fps': false,
    'mobile-fps': false,
    'pc-moba': false,
    'mobile-moba': false,
  })
})

function uuid(index: number): string {
  return `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`
}

function pendingFor(
  suite_id: SuiteId,
  test_id: TestId,
  index: number,
  mode: 'formal' | 'practice' = 'formal',
): PendingSubmission {
  return fixturePending({
    suite_id,
    test_id,
    mode,
    session_id: mode === 'formal' ? uuid(index + 100) : null,
    attempt_id: uuid(index + 200),
    operation_key: uuid(index + 300),
    raw: fixtureRaw(suite_id, test_id, false),
    quality: fixtureQuality(suite_id.startsWith('mobile') ? 'touch' : 'mouse'),
    created_at: NOW.toISOString(),
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(NOW)
  sessionStorage.clear()
  clearAssessmentPending()
})

afterEach(() => {
  clearAssessmentPending()
  sessionStorage.clear()
  vi.restoreAllMocks()
  vi.useRealTimers()
})

describe('assessment pending identity isolation', () => {
  it('keeps account, mode, suite and resource identities in query keys', () => {
    const keys = [
      assessmentKeys.session(ACCOUNT, 'pc-fps', uuid(1)),
      assessmentKeys.session(ACCOUNT, 'mobile-fps', uuid(1)),
      assessmentKeys.practice(ACCOUNT, 'pc-fps', uuid(1)),
      assessmentKeys.attempt(ACCOUNT, 'formal', 'pc-fps', uuid(1)),
      assessmentKeys.attempt(ACCOUNT, 'practice', 'pc-fps', uuid(1)),
      assessmentKeys.session(OTHER_ACCOUNT, 'pc-fps', uuid(1)),
    ]

    expect(new Set(keys.map((key) => JSON.stringify(key))).size).toBe(keys.length)
    expect(keys.every((key) => key[0] === 'esports-assessments')).toBe(true)
  })

  it('keeps formal, practice and all four suites as separate pending results', () => {
    const values = [
      pendingFor('pc-fps', 'precision', 1),
      pendingFor('mobile-fps', 'move-fire', 2),
      pendingFor('pc-moba', 'last-hit', 3),
      pendingFor('mobile-moba', 'skill-aim', 4),
      pendingFor('pc-fps', 'precision', 5, 'practice'),
    ]

    for (const value of values) expect(savePending(value).persisted).toBe(true)

    expect(loadPending(ACCOUNT)).toEqual(values)
    expect(new Set(loadPending(ACCOUNT).map((value) => value.operation_key)).size).toBe(5)
  })

  it('loads only the requested account without deleting another account pending result', () => {
    const own = pendingFor('pc-fps', 'precision', 10)
    const other = { ...pendingFor('pc-moba', 'dodge', 11), account_id: OTHER_ACCOUNT }
    expect(savePending(own).persisted).toBe(true)
    expect(savePending(other).persisted).toBe(true)

    expect(loadPending(ACCOUNT)).toEqual([own])
    expect(loadPending(OTHER_ACCOUNT)).toEqual([other])
  })
})

it('loads immutable v1 and v2 pending JSON from the historical storage keys', () => {
  for (const entry of historicalPending.cases) {
    sessionStorage.setItem(entry.storage_key, JSON.stringify(entry.pending))
  }

  expect(loadPending(ACCOUNT)).toEqual(historicalPending.cases.map(entry => entry.pending))
  expect(historicalPending.cases.map(entry => entry.catalog_version)).toEqual(['pc-fps-v1', 'mobile-fps-v2', 'pc-moba-v2'])
  expect(historicalPending.cases.map(entry => entry.pending.raw.kind)).toEqual(['aim', 'move_fire', 'dodge'])
  expect(historicalPending.cases.map(entry => sessionStorage.getItem(entry.storage_key)))
    .toEqual(historicalPending.cases.map(entry => JSON.stringify(entry.pending)))
})

it('storage blocked does not fake a saved result', () => {
  const value = fixturePending({ created_at: NOW.toISOString() })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('quota', 'QuotaExceededError')
  })

  const result = savePending(value)

  expect(result.persisted).toBe(false)
  expect(loadPending(value.account_id)).toEqual([value])
  expect(hasVolatilePending(value.account_id)).toBe(true)
})

it('recovers volatile pending when storage length enumeration is denied', () => {
  const value = fixturePending({ created_at: NOW.toISOString() })
  const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })
  expect(savePending(value).persisted).toBe(false)
  setItem.mockRestore()
  const length = vi.spyOn(Storage.prototype, 'length', 'get').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })

  try {
    expect(loadPending(value.account_id)).toEqual([value])
  } finally {
    length.mockRestore()
  }
})

it('clears volatile pending when storage key enumeration is denied', () => {
  sessionStorage.setItem('unrelated', 'preserve')
  const value = fixturePending({ created_at: NOW.toISOString() })
  const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })
  expect(savePending(value).persisted).toBe(false)
  setItem.mockRestore()
  const key = vi.spyOn(Storage.prototype, 'key').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })

  try {
    expect(() => clearAssessmentPending(value.account_id)).not.toThrow()
    expect(hasVolatilePending(value.account_id)).toBe(false)
  } finally {
    key.mockRestore()
  }
})

it('keeps the original operation key for an in-memory retry after storage failure', () => {
  const value = fixturePending({ created_at: NOW.toISOString() })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('blocked', 'SecurityError')
  })

  savePending(value)
  const retry = loadPending(value.account_id)[0]

  expect(retry.operation_key).toBe('44444444-4444-4444-8444-444444444444')
  expect(retry.attempt_id).toBe(value.attempt_id)
})

it('removes expired, future, malformed and key-identity-mismatched cache entries safely', () => {
  const expired = fixturePending({ created_at: '2026-09-20T23:59:59.999Z' })
  const future = fixturePending({
    attempt_id: uuid(801),
    operation_key: uuid(802),
    created_at: '2026-09-22T00:00:00.001Z',
  })
  const valid = pendingFor('pc-moba', 'map-awareness', 20)
  vi.setSystemTime(new Date(expired.created_at))
  expect(savePending(expired).persisted).toBe(true)
  vi.setSystemTime(new Date(future.created_at))
  expect(savePending(future).persisted).toBe(true)
  vi.setSystemTime(NOW)
  expect(savePending(valid).persisted).toBe(true)
  sessionStorage.setItem('erp:esports-assessments:pending:broken', '{')

  const validKey = Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index))
    .find((key) => key && sessionStorage.getItem(key)?.includes(valid.operation_key))!
  const stored = sessionStorage.getItem(validKey)!
  sessionStorage.removeItem(validKey)
  sessionStorage.setItem(validKey.replace(valid.attempt_id, uuid(999)), stored)

  expect(loadPending(ACCOUNT)).toEqual([])
  expect(
    Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index))
      .filter((key) => key?.startsWith('erp:esports-assessments:')),
  ).toEqual([])
})

it('clears new assessment cache and memory without touching legacy talent cache', () => {
  const value = fixturePending({ created_at: NOW.toISOString() })
  sessionStorage.setItem(
    'erp:talent-tests:pending:legacy-account:legacy-session:reactiontime',
    JSON.stringify({ savedAt: NOW.valueOf(), value: { submission_id: 'legacy' } }),
  )
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('quota', 'QuotaExceededError')
  })
  savePending(value)

  clearAssessmentPending()

  expect(loadPending(value.account_id)).toEqual([])
  expect(sessionStorage.getItem('erp:talent-tests:pending:legacy-account:legacy-session:reactiontime'))
    .not.toBeNull()
})

it('identity-boundary cleanup preserves only the restored account pending results', () => {
  const own = pendingFor('pc-fps', 'precision', 30)
  const other = { ...pendingFor('pc-moba', 'last-hit', 31), account_id: OTHER_ACCOUNT }
  savePending(own)
  savePending(other)

  clearAssessmentPendingExcept(ACCOUNT)

  expect(loadPending(ACCOUNT)).toEqual([own])
  expect(loadPending(OTHER_ACCOUNT)).toEqual([])
})
