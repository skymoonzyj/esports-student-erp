import { isPreloadFailure } from './moduleLoadEvents'

// A finite shared CSS owner must complete before any dependent page mounts.
// Most consumers also wait before import; two authorized route leaves overlap it.
// Vite's seen set is not a pending-promise cache and cannot recover failed CSS.
export function createStyleGate(loader: () => Promise<unknown>): () => Promise<unknown> {
  let promise: Promise<unknown> | undefined
  return () => {
    if (!promise) {
      promise = loader().catch(error => {
        const cssFailure = isPreloadFailure(error) && error instanceof Error && error.message.startsWith('Unable to preload CSS for ')
        if (!cssFailure) promise = undefined // A later explicit module retry may try JS again.
        throw error // Each page's existing factory tags its own moduleId.
      })
    }
    return promise
  }
}
