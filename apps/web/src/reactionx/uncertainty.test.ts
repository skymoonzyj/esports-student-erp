import { afterEach, expect, it, vi } from 'vitest'
import { reactionxApi } from './api'
import { parseArchiveRequest, parseUncertaintyPage, parseUncertaintyReceipt } from './uncertainty'

const run = '11111111-1111-4111-8111-111111111111'
const digest = 'a'.repeat(64)
const request = { request_id: '22222222-2222-4222-8222-222222222222', run_id: run, declaration_digest: digest, expires_at: '2099-01-01T00:00:00.000Z' }
const receipt = { version: 1, archive_id: '33333333-3333-4333-8333-333333333333', run_id: run, connector_id: '44444444-4444-4444-8444-444444444444', manifest_digest: 'b'.repeat(64), declaration_digest: digest, stop_operation_id: '55555555-5555-4555-8555-555555555555', status: 'execution_unknown', closed_at: '2026-11-04T00:00:00.000Z' }
const row = { run_id: run, declaration_digest: digest, subject_name: '合成参测者', mode: 'manual', created_at: '2026-11-04T00:00:00Z', state: 'pending_review' }
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('rejects non-result payloads with extra score fields, malformed identity or wrong state', () => {
  expect(parseUncertaintyPage({ items: [row], total: 1, offset: 0, limit: 20 }).items[0].subject_name).toBe('合成参测者')
  for (const changed of [{ ...row, score: 0 }, { ...row, run_id: run.toUpperCase().replace('1111', 'AAAA') }, { ...row, declaration_digest: 'A'.repeat(64) }, { ...row, state: 'archived' }, { ...row, created_at: 'yesterday' }]) {
    expect(() => parseUncertaintyPage({ items: [changed], total: 1, offset: 0, limit: 20 })).toThrow()
  }
  expect(() => parseUncertaintyPage({ items: [row], total: 0, offset: 0, limit: 20 })).toThrow()
})

it('binds the confirmation request and archive receipt to the selected original evidence', () => {
  expect(parseArchiveRequest(request, run, digest).request_id).toBe('22222222-2222-4222-8222-222222222222')
  expect(parseUncertaintyReceipt(receipt, run, digest).status).toBe('execution_unknown')
  expect(() => parseArchiveRequest({ ...request, run_id: request.request_id }, run, digest)).toThrow()
  expect(() => parseArchiveRequest({ ...request, expires_at: '2099-01-01' }, run, digest)).toThrow()
  expect(() => parseArchiveRequest({ ...request, expires_at: '2099-01-01T00:00:00Z' }, run, digest)).toThrow()
  expect(() => parseUncertaintyReceipt({ ...receipt, declaration_digest: 'c'.repeat(64) }, run, digest)).toThrow()
  expect(() => parseUncertaintyReceipt({ ...receipt, result_id: receipt.archive_id }, run, digest)).toThrow()
  expect(() => parseUncertaintyReceipt({ ...receipt, status: 'saved' }, run, digest)).toThrow()
})

it('uses independent uncertainty endpoints, explicit confirmations and no-store CSRF transport', async () => {
  const { setCsrfToken } = await import('../api/client')
  setCsrfToken('synthetic-token')
  const calls: { url: string; init: RequestInit | undefined }[] = []
  vi.stubGlobal('fetch', vi.fn(async (input, init) => {
    const url = String(input); calls.push({ url, init })
    return Response.json(url.includes('/uncertainties?') ? { items: [row], total: 1, offset: 0, limit: 20 } : url.endsWith('/request') ? request : receipt)
  }))
  const signal = new AbortController().signal
  expect((await reactionxApi.uncertainties(0, signal)).total).toBe(1)
  await reactionxApi.requestUncertaintyArchive(run, digest, '人工核对原始异常记录原因', signal)
  await reactionxApi.confirmUncertaintyArchive(run, request.request_id, digest, signal)
  expect(calls.map(call => call.url)).toEqual(['/api/v1/reactionx/uncertainties?offset=0&limit=20', `/api/v1/reactionx/runs/${run}/uncertainty/request`, `/api/v1/reactionx/runs/${run}/uncertainty/confirm`])
  expect(JSON.parse(String(calls[1].init?.body))).toEqual({ declaration_digest: digest, reason: '人工核对原始异常记录原因', confirmation: true })
  expect(JSON.parse(String(calls[2].init?.body))).toEqual({ request_id: request.request_id, confirmation: true })
  expect(calls.every(call => call.init?.signal === signal && call.init.cache === 'no-store' && call.init.credentials === 'include')).toBe(true)
  expect(new Headers(calls[1].init?.headers).get('X-CSRF-Token')).toBe('synthetic-token')
  setCsrfToken(null)
})
