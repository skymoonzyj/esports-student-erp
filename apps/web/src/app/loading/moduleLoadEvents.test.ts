import { expect, it, vi } from 'vitest'
import { installModuleLoadEvents, isPreloadFailure } from './moduleLoadEvents'

it('observes Vite payload without preventing import/CSS rejection or global errors', () => {
  const fetch = vi.spyOn(globalThis, 'fetch')
  const uninstall = installModuleLoadEvents()
  const error = new TypeError('import failed')
  const event = Object.assign(new Event('vite:preloadError', { cancelable: true }), { payload: error })
  window.dispatchEvent(event)
  expect(event.defaultPrevented).toBe(false)
  expect(isPreloadFailure(error)).toBe(true)
  expect(isPreloadFailure(new Error('business'))).toBe(false)
  expect(fetch).not.toHaveBeenCalled()
  uninstall()
  const after = new Error('after teardown')
  window.dispatchEvent(Object.assign(new Event('vite:preloadError'), { payload: after }))
  expect(isPreloadFailure(after)).toBe(false)
  fetch.mockRestore()
})
