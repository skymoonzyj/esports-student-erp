// Vite's payload is the actual rejection, not CustomEvent.detail. Observing it
// must not preventDefault: Vite otherwise fulfills imports with undefined and
// may continue after a missing CSS dependency.
const preloadFailures = new WeakSet<object>()

export function isPreloadFailure(error: unknown): boolean {
  return typeof error === 'object' && error !== null && preloadFailures.has(error)
}

export function installModuleLoadEvents(): () => void {
  const onPreloadError = (event: Event) => {
    const payload: unknown = (event as Event & { payload?: unknown }).payload
    if (typeof payload === 'object' && payload !== null) preloadFailures.add(payload)
  }
  window.addEventListener('vite:preloadError', onPreloadError)
  return () => window.removeEventListener('vite:preloadError', onPreloadError)
}
