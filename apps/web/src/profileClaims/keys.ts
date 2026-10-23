// Public claim state is deliberately not a React Query cache or a storage key.
// It has its own generation, unrelated to auth CSRF and registration sessions.
let generation = 0
const flights = new Set<AbortController>()
const boundaryListeners = new Set<() => void>()

export function replaceProfileClaimGeneration(): void {
  generation += 1
  for (const controller of flights) controller.abort()
  flights.clear()
}

/** Clears only local memory and I/O; cannot delete the server's HttpOnly cookie. */
export function clearProfileClaimSession(): void {
  replaceProfileClaimGeneration()
  for (const listener of boundaryListeners) listener()
}

export function subscribeProfileClaimBoundary(listener: () => void): () => void {
  boundaryListeners.add(listener)
  return () => { boundaryListeners.delete(listener) }
}

export function beginProfileClaimRequest(signal?: AbortSignal) {
  const currentGeneration = generation
  const controller = new AbortController()
  const abort = () => controller.abort()
  if (signal?.aborted) controller.abort()
  else signal?.addEventListener("abort", abort, { once: true })
  flights.add(controller)
  return {
    signal: controller.signal,
    check: () => {
      if (controller.signal.aborted || generation !== currentGeneration) throw new DOMException("Claim request cancelled", "AbortError")
    },
    release: () => { flights.delete(controller); signal?.removeEventListener("abort", abort) },
  }
}
