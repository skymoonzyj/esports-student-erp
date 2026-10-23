import { afterEach, expect, it } from 'vitest'
import { createStyleGate } from './styleGate'
import { installModuleLoadEvents } from './moduleLoadEvents'

let uninstall: (() => void) | undefined
afterEach(() => uninstall?.())
it('concurrent style consumers share the pending promise and retain the successful owner', async () => {
  let finish!: () => void, calls = 0
  const gate = createStyleGate(() => { calls++; return new Promise<void>(resolve => { finish = resolve }) })
  const first = gate(), second = gate()
  expect(first).toBe(second)
  expect(calls).toBe(1)
  finish(); await first
  expect(gate()).toBe(first)
  expect(calls).toBe(1)
})
it('positively observed CSS failure stays rejected for concurrent and sequential consumers', async () => {
  uninstall = installModuleLoadEvents()
  let reject!: (error: Error) => void, calls = 0
  const gate = createStyleGate(() => { calls++; return new Promise((_, fail) => { reject = fail }) })
  const one = gate(), two = gate()
  expect(one).toBe(two)
  const error = new Error('Unable to preload CSS for /assets/shared.css')
  const event = new Event('vite:preloadError', { cancelable: true })
  Object.assign(event, { payload: error }); window.dispatchEvent(event)
  reject(error)
  await expect(one).rejects.toBe(error)
  await expect(two).rejects.toBe(error)
  await expect(gate()).rejects.toBe(error)
  expect(calls).toBe(1)
  expect(event.defaultPrevented).toBe(false)
})
it('ordinary JS import rejection permits a later explicit invocation without automatic retries', async () => {
  let calls = 0
  const gate = createStyleGate(async () => { if (++calls === 1) throw new TypeError('network import'); return 'ready' })
  await expect(gate()).rejects.toThrow('network import')
  expect(calls).toBe(1)
  await expect(gate()).resolves.toBe('ready')
  await expect(gate()).resolves.toBe('ready')
  expect(calls).toBe(2)
})
