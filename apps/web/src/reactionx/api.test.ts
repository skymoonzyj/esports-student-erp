import { afterEach, expect, it, vi } from 'vitest'
import { documentControl, reactionxApi, canControl, canReadReports } from './api'
import { setCsrfToken } from '../api/client'

afterEach(() => { documentControl.clear(); vi.unstubAllGlobals(); setCsrfToken(null) })
it('requires actual superadmin AND both effective pilot permissions, report readers remain independent', () => {
  const user = { account_id: 'actor', person_id: 'person', full_name: 'Test', email: null, force_password_change: false, permissions: ['reactionx.manage'], scoped_permissions: ['reactionx.conduct', 'talent_tests.read'] }
  expect(canControl(user)).toBe(false)
  expect(canControl({ ...user, is_super_admin: true })).toBe(true)
  expect(canControl({ ...user, is_super_admin: true, scoped_permissions: [] })).toBe(false)
  expect(canReadReports(user)).toBe(true)
})
it('keeps SPA identity only in memory and never adopts an inherited sessionStorage tab', () => {
  sessionStorage.setItem('reactionx-tab', 'inherited')
  const first = documentControl.forAccount('a')
  expect(first.tabId).toMatch(/^[0-9a-f-]{36}$/)
  expect(documentControl.forAccount('a').tabId).toBe(first.tabId)
  expect(documentControl.forAccount('b').tabId).not.toBe(first.tabId)
  documentControl.clear()
  expect(documentControl.forAccount('a').tabId).not.toBe(first.tabId)
})
it('sends the document proof on pairing and uses private no-store abortable browser requests', async () => {
  const seen: [string, RequestInit | undefined][] = []
  vi.stubGlobal('fetch', vi.fn(async (url, init) => { seen.push([String(url), init]); return Response.json({ pairing_id: 'pair', challenge: 'private', pair_uri: 'reactionx-erp://pair?challenge=private', expires_at: '2027-01-01T00:00:00Z', verification_phrase: 'TEST' }) }))
  setCsrfToken('synthetic')
  const controller = new AbortController()
  await reactionxApi.pair('document-id', controller.signal)
  expect(seen[0][0]).toBe('/api/v1/reactionx/pairings')
  expect(JSON.parse(String(seen[0][1]?.body))).toEqual({ browser_tab_id: 'document-id' })
  expect(seen[0][1]).toMatchObject({ cache: 'no-store', signal: controller.signal, credentials: 'include' })
})
it('rejects saved report identity mismatch before any rank can be shown', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ receipt: { result_id: 'different' } })))
  await expect(reactionxApi.report('expected')).rejects.toThrow()
})
it.each([202, 503])('ordinary logout clears browser proof and attempts stop without revoking retained-package or OFF credentials (%s)', async status => {
  const proof = documentControl.forAccount('a'); proof.connectorId = 'connector'; proof.runId = 'run'
  const methods: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => { methods.push(`${init?.method} ${input}`); return Response.json({ run_status: 'stopping' }, { status }) }))
  setCsrfToken('synthetic')
  await documentControl.release()
  expect(methods).toEqual(['POST /api/v1/reactionx/runs/run/stop'])
  expect(documentControl.forAccount('a').connectorId).toBeNull()
  const next = documentControl.forAccount('a').tabId
  window.dispatchEvent(new CustomEvent('reactionx-auth-boundary', { detail: null }))
  expect(documentControl.forAccount('a').tabId).not.toBe(next)
})
it('uses an already-loaded feature logout barrier without requiring a new chunk download', async () => {
  const proof = documentControl.forAccount('a'); proof.connectorId = 'connector'
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({})))
  setCsrfToken('synthetic')
  const pending: Promise<void>[] = []
  window.dispatchEvent(new CustomEvent('reactionx-before-logout', { detail: pending }))
  expect(pending).toHaveLength(1)
  await Promise.all(pending)
  expect(documentControl.forAccount('a').connectorId).toBeNull()
})
