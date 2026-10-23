import { afterEach, expect, it, vi } from 'vitest'
import { clearTalentPending as cleanup, confirmTalentSessionCreated, getOrCreateTalentSessionRequest, getOrCreateTalentAbandonRequest } from './pending'
import { clearTalentPending, clearTalentPendingExcept } from './pendingStorage'

afterEach(() => { vi.restoreAllMocks(); cleanup(); sessionStorage.clear() })
it.each(['length', 'key'] as const)('auth leaf shares creation and abandon identity with feature despite blocked %s', blocked => {
  sessionStorage.setItem('erp:esports-assessments:untouched', 'assessment')
  let sequence = 0
  vi.spyOn(crypto, 'randomUUID').mockImplementation(() => `00000000-0000-4000-8000-${String(++sequence).padStart(12, '0')}`)
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
  const own = getOrCreateTalentSessionRequest('own', 'catalog')
  const other = getOrCreateTalentSessionRequest('other', 'catalog')
  const ownAbandon = getOrCreateTalentAbandonRequest('own', 'session')
  const otherAbandon = getOrCreateTalentAbandonRequest('other', 'session')
  if (blocked === 'length') vi.spyOn(Storage.prototype, 'length', 'get').mockImplementation(() => { throw new Error('blocked') })
  else vi.spyOn(Storage.prototype, 'key').mockImplementation(() => { throw new Error('blocked') })
  clearTalentPendingExcept('own')
  expect(getOrCreateTalentSessionRequest('own', 'catalog')).toEqual(own)
  expect(getOrCreateTalentAbandonRequest('own', 'session')).toBe(ownAbandon)
  expect(confirmTalentSessionCreated('other', other, { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', catalog_version: 'catalog' })).toBe(false)
  expect(getOrCreateTalentSessionRequest('other', 'catalog').request_id).not.toBe(other.request_id)
  expect(getOrCreateTalentAbandonRequest('other', 'session')).not.toBe(otherAbandon)
  clearTalentPending()
  expect(confirmTalentSessionCreated('own', own, { id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', catalog_version: 'catalog' })).toBe(false)
  expect(getOrCreateTalentSessionRequest('own', 'catalog').request_id).not.toBe(own.request_id)
  expect(getOrCreateTalentAbandonRequest('own', 'session')).not.toBe(ownAbandon)
  expect(sessionStorage.getItem('erp:esports-assessments:untouched')).toBe('assessment')
})
