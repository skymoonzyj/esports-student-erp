import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { readBuildInfo } from './buildInfo'

beforeEach(() => vi.stubGlobal('__ERP_BUILD_ID__', 'build-a'))
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers() })
const json = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } })

it.each([
  [{ schema_version: 1, build_id: 'build-a' }, 'same'],
  [{ schema_version: 1, build_id: 'next-release' }, 'changed'],
  [{ schema_version: 2, build_id: 'next-release' }, 'unknown'],
  [{ schema_version: 1, build_id: '../invalid' }, 'unknown'],
  [{ schema_version: 1, build_id: '' }, 'unknown'],
  [{ schema_version: 1, build_id: 'a'.repeat(201) }, 'unknown'],
  [null, 'unknown'],
])('validates the public version schema: %j', async (body, kind) => {
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    expect(url).toBe('/build-info.json')
    expect(init).toMatchObject({ cache: 'no-store', credentials: 'same-origin', redirect: 'error' })
    return json(body)
  })
  expect(await readBuildInfo(new AbortController().signal)).toEqual({ kind })
})

it('times out at five seconds even if the network ignores abort', async () => {
  vi.useFakeTimers()
  let signal: AbortSignal | undefined
  vi.stubGlobal('fetch', (_url: string, init: RequestInit) => { signal = init.signal!; return new Promise(() => undefined) })
  const result = readBuildInfo(new AbortController().signal)
  await vi.advanceTimersByTimeAsync(4999)
  expect(signal?.aborted).toBe(false)
  await vi.advanceTimersByTimeAsync(1)
  expect(await result).toEqual({ kind: 'unknown' })
  expect(signal?.aborted).toBe(true)
})

it('external abort ends a probe and prevents late response use', async () => {
  const controller = new AbortController()
  vi.stubGlobal('fetch', () => new Promise(() => undefined))
  const result = readBuildInfo(controller.signal)
  controller.abort()
  expect(await result).toEqual({ kind: 'unknown' })
})

it('refuses HTTP errors and non JSON without making auth requests', async () => {
  vi.stubGlobal('fetch', async () => new Response('<html>fallback</html>', { status: 200 }))
  expect(await readBuildInfo(new AbortController().signal)).toEqual({ kind: 'unknown' })
  vi.stubGlobal('fetch', async () => new Response(null, { status: 503 }))
  expect(await readBuildInfo(new AbortController().signal)).toEqual({ kind: 'unknown' })
})
