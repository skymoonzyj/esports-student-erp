import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { ReactNode } from 'react'
import { setCsrfToken } from '../api/client'
import { useAssessmentSession } from './useAssessmentSession'
import { fixtureAttempt, fixturePending, fixtureQuality, fixtureRaw, fixtureSuiteConfig } from './testSupport/fixtures'
import { clearAssessmentPending, loadPending, savePending } from './pending'

const auth = vi.hoisted(() => ({ user: { account_id: '11111111-1111-4111-8111-111111111111' } as { account_id: string } | null }))
vi.mock('../auth/AuthProvider', () => ({ useAuth: () => auth }))
const view = fixtureAttempt()
const options = { mode: 'formal' as const, suite_id: view.suite_id, session_id: view.session_id!, test_id: view.test_id }
const scored = { metrics: { accuracy: 0 }, score: 0, grade: 'low', quality_flags: [] }
const saved = { mode: 'formal', result_id: '55555555-5555-4555-8555-555555555555', scored, session: { id: view.session_id, suite_id: view.suite_id, suite_config: fixtureSuiteConfig(view.suite_id), status: 'in_progress', members: ['precision', 'tracking', 'switching', 'recognition'], results: [{ result_id: '55555555-5555-4555-8555-555555555555', test_id: 'precision', scored }], report_id: null } }
const json = (value: unknown, status = 200, headers: Record<string, string> = {}) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json', ...headers } })
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return renderHook(() => useAssessmentSession(options), { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider> })
}
beforeEach(() => {
  auth.user = { account_id: '11111111-1111-4111-8111-111111111111' }
  clearAssessmentPending()
  setCsrfToken('test')
})
afterEach(() => { clearAssessmentPending(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('restarts delayed recovery across real StrictMode effect replay and rejects the obsolete response', async () => {
  const replies: Array<(response: Response) => void> = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(() => new Promise(resolve => { replies.push(resolve) })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { result } = renderHook(() => useAssessmentSession(options), { reactStrictMode: true, wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider> })
  await act(async () => { replies.at(-1)!(json({ items: [], next_cursor: null, visible_total: 0 })) })
  await waitFor(() => expect(result.current.ready).toBe(true))
  expect(result.current.busy).toBe(false)
  expect(replies).toHaveLength(2)
  await act(async () => { replies[0](json({ detail: 'obsolete denial' }, 403)) })
  expect(result.current.revoked).toBe(false)
  expect(result.current.error).toBeNull()
})

it('rejects delayed recovery from an old route after the current route becomes ready', async () => {
  const replies: Array<(response: Response) => void> = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(() => new Promise(resolve => { replies.push(resolve) })))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const { result, rerender } = renderHook(({ test_id }) => useAssessmentSession({ ...options, test_id }), { initialProps: { test_id: view.test_id }, wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider> })
  rerender({ test_id: 'tracking' })
  await act(async () => { replies[1](json({ items: [], next_cursor: null, visible_total: 0 })) })
  expect(result.current.ready).toBe(true)
  await act(async () => { replies[0](json({ detail: 'old route denial' }, 403)) })
  expect(result.current.busy).toBe(false)
  expect(result.current.revoked).toBe(false)
})

it('recovers a server attempt without local cache and reconciles saved before interrupt', async () => {
  const fetcher = vi.fn<typeof fetch>(async input => {
    const url = String(input)
    if (url.includes('/recovery?')) return json({ items: [{ mode: view.mode, attempt_id: view.id, session_id: view.session_id, suite_id: view.suite_id, test_id: view.test_id, state: 'running', issued_at: view.issued_at, run_until: view.run_until, submit_until: view.submit_until }], next_cursor: null, visible_total: 1 })
    return json({ attempt: { ...view, state: 'submitted' }, saved })
  })
  vi.stubGlobal('fetch', fetcher)
  const { result } = mount()
  await waitFor(() => expect(result.current.saved?.result_id).toBe(saved.result_id))
  await act(() => result.current.interrupt())
  expect(fetcher.mock.calls.every(([url]) => !String(url).endsWith('/interrupt'))).toBe(true)
})

it('keeps the same pending operation across 500, retry, and authoritative save', async () => {
  const bodies: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (String(input).endsWith('/result')) { bodies.push(String(init?.body)); return bodies.length === 1 ? json({ detail: '故障' }, 500) : json(saved) }
    if (init?.method === 'POST') return json(view)
    return json({ attempt: view, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  await act(() => result.current.submit(fixtureRaw('pc-fps', 'precision', false), fixtureQuality()))
  expect(result.current.saved).toBeNull()
  expect(loadPending(auth.user!.account_id)).toHaveLength(1)
  await act(() => result.current.retry())
  expect(bodies[1]).toBe(bodies[0])
  expect(loadPending(auth.user!.account_id)).toEqual([])
  expect(result.current.saved?.result_id).toBe(saved.result_id)
})

it('keeps the full pending payload and original operation key after an early-save 409', async () => {
  const bodies: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (url.endsWith('/result')) {
      bodies.push(String(init?.body))
      return bodies.length === 1
        ? json({ detail: '测试尚未完成，请等待完整测试结束后再保存', code: 'esports_result_too_early' }, 409)
        : json(saved)
    }
    if (init?.method === 'POST') return json(view)
    return json({ attempt: view, saved: null })
  }))
  const raw = fixtureRaw('pc-fps', 'precision', false)
  const quality = fixtureQuality()
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))

  await act(() => result.current.submit(raw, quality))

  const pending = loadPending(auth.user!.account_id)[0]
  expect(result.current.error).toBe('测试尚未完成，请等待完整测试结束后再手动重试保存。')
  expect(result.current.pending).toEqual(pending)
  expect(pending).toMatchObject({ raw, quality })
  expect(JSON.parse(bodies[0])).toEqual({ operation_key: pending.operation_key, raw, quality })

  await act(() => result.current.retry())

  expect(bodies).toHaveLength(2)
  expect(bodies[1]).toBe(bodies[0])
  expect(result.current.saved?.result_id).toBe(saved.result_id)
  expect(loadPending(auth.user!.account_id)).toEqual([])
})

it('accepts an authoritative GET save after an early 409 without a second submit or create', async () => {
  let starts = 0
  let submits = 0
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (url.endsWith('/result')) {
      submits += 1
      return json({ detail: '测试尚未完成，请等待完整测试结束后再保存', code: 'esports_result_too_early' }, 409)
    }
    if (init?.method === 'POST') { starts += 1; return json(view) }
    return json({ attempt: { ...view, state: 'submitted' }, saved })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  await act(() => result.current.submit(fixtureRaw('pc-fps', 'precision', false), fixtureQuality()))
  expect(result.current.pending).not.toBeNull()

  await act(() => result.current.retry())

  expect(result.current.saved?.result_id).toBe(saved.result_id)
  expect(result.current.pending).toBeNull()
  expect(loadPending(auth.user!.account_id)).toEqual([])
  expect(starts).toBe(1)
  expect(submits).toBe(1)
})

it('does not describe an unrelated 409 as an incomplete full test', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (url.endsWith('/result')) return json({ detail: '保存状态冲突', code: 'esports_result_conflict' }, 409)
    if (init?.method === 'POST') return json(view)
    return json({ attempt: view, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))

  await act(() => result.current.submit(fixtureRaw('pc-fps', 'precision', false), fixtureQuality()))

  expect(result.current.error).toBe('已有运行或保存状态与当前操作冲突，请先重新对账。')
  expect(result.current.error).not.toContain('测试尚未完成')
  expect(result.current.pending).not.toBeNull()
})

it.each([401, 403])('clears a pending result when submit confirms current-account revocation with HTTP %s', async status => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (url.endsWith('/result')) return json({ detail: '权限已失效', code: 'esports_access_denied' }, status)
    if (init?.method === 'POST') return json(view)
    return json({ attempt: view, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))

  await act(() => result.current.submit(fixtureRaw('pc-fps', 'precision', false), fixtureQuality()))

  expect(result.current.revoked).toBe(true)
  expect(result.current.pending).toBeNull()
  expect(loadPending(auth.user!.account_id)).toEqual([])
})

it('ignores a late save response after an account switch and retains the original account pending result', async () => {
  let resolveSave!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    if (url.endsWith('/result')) return new Promise<Response>(resolve => { resolveSave = resolve })
    if (init?.method === 'POST') return json(view)
    return json({ attempt: view, saved: null })
  }))
  const originalAccount = auth.user!.account_id
  const nextAccount = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  const { result, rerender } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  let submitting!: Promise<void>
  act(() => { submitting = result.current.submit(fixtureRaw('pc-fps', 'precision', false), fixtureQuality()) })
  await waitFor(() => expect(result.current.pending).not.toBeNull())
  auth.user = { account_id: nextAccount }
  rerender()
  await waitFor(() => expect(result.current.accountId).toBe(nextAccount))

  await act(async () => { resolveSave(json(saved)); await submitting })

  expect(result.current.saved).toBeNull()
  expect(result.current.pending).toBeNull()
  expect(loadPending(originalAccount)).toHaveLength(1)
  expect(loadPending(nextAccount)).toEqual([])
})

it('ignores an old-account start response and never interrupts using the new credentials', async () => {
  let resolve!: (response: Response) => void
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === 'POST') return new Promise<Response>(done => { resolve = done })
    if (String(input).includes('/recovery?')) return json({ items: [], next_cursor: null, visible_total: 0 })
    throw new Error('unexpected GET')
  })
  vi.stubGlobal('fetch', fetcher)
  const { result, rerender } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  let starting!: Promise<unknown>
  act(() => { starting = result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }) })
  auth.user = { account_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa' }
  rerender()
  await act(async () => { resolve(json(view)); await starting })
  expect(result.current.serverView).toBeNull()
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})

it('does not submit a pending formal result through a practice identity', async () => {
  savePending(fixturePending({ created_at: new Date().toISOString() }))
  vi.stubGlobal('fetch', vi.fn(async () => json({ items: [], next_cursor: null, visible_total: 0 })))
  const client = new QueryClient()
  const { result } = renderHook(() => useAssessmentSession({ ...options, mode: 'practice', session_id: undefined }), { wrapper: ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}><MemoryRouter>{children}</MemoryRouter></QueryClientProvider> })
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.retry())
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
})

it('keeps start disabled when the initial recovery read failed', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => json({ detail: 'failure' }, 500)))
  const { result } = mount()
  await waitFor(() => expect(result.current.error).not.toBeNull())
  expect(result.current.ready).toBe(false)
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  expect(vi.mocked(fetch).mock.calls).toHaveLength(1)
})

it('reconciles an interrupt 410 and discards expired raw without moving it to a new attempt', async () => {
  savePending(fixturePending({ created_at: new Date().toISOString() }))
  let expired = false
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input) => {
    if (String(input).endsWith('/interrupt')) { expired = true; return json({ detail: 'expired' }, 410) }
    return json({ attempt: { ...view, state: expired ? 'expired' : 'running' }, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.interrupt())
  expect(result.current.serverView?.state).toBe('expired')
  expect(result.current.pending).toBeNull()
  expect(loadPending(auth.user!.account_id)).toEqual([])
})

it.each([401, 403])('clears current UI and prevents further mutations after HTTP %s', async status => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => init?.method === 'POST' ? json({ detail: 'denied' }, status)
    : String(input).includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: view, saved: null })))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  expect(result.current.revoked).toBe(true)
  expect(result.current.serverView).toBeNull()
  const calls = vi.mocked(fetch).mock.calls.length
  await act(() => result.current.retry())
  expect(vi.mocked(fetch).mock.calls).toHaveLength(calls)
})

it('shows Retry-After seconds without automatically retrying a 429', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (_input, init) => init?.method === 'POST' ? json({ detail: 'limited' }, 429, { 'Retry-After': '17' }) : json({ items: [], next_cursor: null, visible_total: 0 })))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  expect(result.current.error).toContain('17 秒')
  expect(vi.mocked(fetch).mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1)
})

it.each([-600000, 600000])('subtracts a 28-second HTTP delay even with client calendar offset %s', async offset => {
  vi.spyOn(Date, 'now').mockReturnValue(Date.parse(view.server_now) + offset)
  let monotonic = 100
  vi.spyOn(performance, 'now').mockImplementation(() => monotonic)
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (_input, init) => {
    if (init?.method === 'POST') { monotonic += 28000; return json(view) }
    return json({ items: [], next_cursor: null, visible_total: 0 })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  expect(result.current.leaseMs).toBe(62000)
})

it('waits for a late start before confirming exit and interrupts that exact attempt', async () => {
  let reply!: (response: Response) => void
  const posts: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (init?.method === 'POST') {
      posts.push(url)
      if (url.endsWith('/interrupt')) return json({ ...view, state: 'interrupted' })
      return new Promise(resolve => { reply = resolve })
    }
    return url.includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: view, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  let start!: Promise<unknown>
  let leaving!: Promise<boolean>
  act(() => { start = result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }) })
  act(() => { leaving = result.current.leave() })
  let allowed: boolean | undefined
  void leaving.then(value => { allowed = value })
  expect(allowed).toBeUndefined()
  await act(async () => { reply(json(view)); await start; await leaving })
  expect(allowed).toBe(true)
  expect(posts).toHaveLength(2)
  expect(posts[1]).toContain(`/attempts/${view.id}/interrupt`)
  expect(result.current.serverView?.state).toBe('interrupted')
})

it('retains durable pending results when exiting without sending an interruption', async () => {
  const pending = fixturePending({ created_at: new Date().toISOString() })
  savePending(pending)
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async () => json({ attempt: view, saved: null })))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  let allowed = false
  await act(async () => { allowed = await result.current.leave() })
  expect(allowed).toBe(true)
  expect(loadPending(auth.user!.account_id)).toEqual([pending])
  expect(vi.mocked(fetch).mock.calls.every(([, init]) => init?.method !== 'POST')).toBe(true)
})

it('refuses exit after a failed interruption so the unresolved attempt stays visible', async () => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/interrupt')) return json({ detail: 'offline' }, 500)
    if (init?.method === 'POST') return json(view)
    return String(input).includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: view, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  let allowed = true
  await act(async () => { allowed = await result.current.leave() })
  expect(allowed).toBe(false)
  expect(result.current.serverView?.state).toBe('running')
  expect(result.current.error).not.toBeNull()
})

it('does not let an old-account exit continuation interrupt using a new account', async () => {
  let reply!: (response: Response) => void
  const posts: string[] = []
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === 'POST') { posts.push(String(input)); return new Promise(resolve => { reply = resolve }) }
    return json({ items: [], next_cursor: null, visible_total: 0 })
  }))
  const { result, rerender } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  let starting!: Promise<unknown>
  let leaving!: Promise<boolean>
  act(() => { starting = result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }) })
  act(() => { leaving = result.current.leave() })
  auth.user = { account_id: '22222222-2222-4222-8222-222222222222' }
  rerender()
  let allowed = true
  await act(async () => { reply(json(view)); await starting; allowed = await leaving })
  expect(allowed).toBe(false)
  expect(posts).toHaveLength(1)
  expect(result.current.serverView).toBeNull()
})

it('allows exit when interruption reconciles an authoritative expired attempt', async () => {
  let expired = false
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/interrupt')) { expired = true; return json({ detail: 'expired' }, 410) }
    if (init?.method === 'POST') return json(view)
    return String(input).includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: { ...view, state: expired ? 'expired' : 'running' }, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  let allowed = false
  await act(async () => { allowed = await result.current.leave() })
  expect(allowed).toBe(true)
  expect(result.current.serverView?.state).toBe('expired')
})

it.each([401, 403])('allows a terminal same-account revoked exit after HTTP %s without further requests', async status => {
  vi.stubGlobal('fetch', vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith('/interrupt')) return json({ detail: 'denied' }, status)
    if (init?.method === 'POST') return json(view)
    return String(input).includes('/recovery?') ? json({ items: [], next_cursor: null, visible_total: 0 }) : json({ attempt: view, saved: null })
  }))
  const { result } = mount()
  await waitFor(() => expect(result.current.ready).toBe(true))
  await act(() => result.current.start({ test_id: view.test_id, settings: view.settings, environment: view.environment, difficulty: 3 }))
  let allowed = false
  await act(async () => { allowed = await result.current.leave() })
  expect(allowed).toBe(true)
  expect(result.current.revoked).toBe(true)
  const requests = vi.mocked(fetch).mock.calls.length
  await act(async () => { allowed = await result.current.leave() })
  expect(allowed).toBe(true)
  expect(vi.mocked(fetch).mock.calls).toHaveLength(requests)
})
