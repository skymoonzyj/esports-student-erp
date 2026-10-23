import { afterEach, expect, it, vi } from 'vitest'
import { fixturePending } from './testSupport/fixtures'
import { clearAssessmentPending as cleanup, hasVolatilePending, loadPending, removePending, savePending } from './pending'
import { clearAssessmentPending, clearAssessmentPendingExcept } from './pendingStorage'

afterEach(() => { vi.restoreAllMocks(); cleanup(); sessionStorage.clear() })
it.each(['length', 'key'] as const)('auth leaf clears feature volatile results with blocked %s enumeration', blocked => {
  sessionStorage.setItem('erp:talent-tests:untouched', 'legacy')
  const own = fixturePending({ created_at: new Date().toISOString() })
  const other = { ...own, account_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' }
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  savePending(own); savePending(other)
  if (blocked === 'length') vi.spyOn(Storage.prototype, 'length', 'get').mockImplementation(() => { throw new Error('blocked') })
  else vi.spyOn(Storage.prototype, 'key').mockImplementation(() => { throw new Error('blocked') })
  clearAssessmentPendingExcept(own.account_id)
  expect(loadPending(own.account_id)).toEqual([own])
  expect(loadPending(other.account_id)).toEqual([])
  expect(hasVolatilePending(other.account_id)).toBe(false)
  clearAssessmentPending()
  expect(loadPending(own.account_id)).toEqual([])
  expect(hasVolatilePending(own.account_id)).toBe(false)
  expect(sessionStorage.getItem('erp:talent-tests:untouched')).toBe('legacy')
})
it('stale operation removal cannot delete a replacement sharing the same identity key', () => {
  const old = fixturePending({ created_at: new Date().toISOString() })
  const replacement = { ...old, operation_key: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' }
  savePending(old); savePending(replacement); removePending(old)
  expect(loadPending(old.account_id)).toEqual([replacement])
})
