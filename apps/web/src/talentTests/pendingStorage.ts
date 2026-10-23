import type { SessionCreateRequest } from "./contracts"

export const TALENT_PREFIX = "erp:talent-tests:"
export const PENDING_PREFIX = `${TALENT_PREFIX}pending:`
export const CREATE_PREFIX = `${TALENT_PREFIX}create:`
export const ABANDON_PREFIX = `${TALENT_PREFIX}abandon:`

export const creationMemory = new Map<string, SessionCreateRequest>()
export const abandonMemory = new Map<string, string>()

export function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function removeMatching(prefix: string): void {
  try {
    const target = storage()
    if (!target) return
    const keys: string[] = []
    for (let index = 0; index < target.length; index += 1) {
      const candidate = target.key(index)
      if (candidate?.startsWith(prefix)) keys.push(candidate)
    }
    for (const candidate of keys) target.removeItem(candidate)
  } catch {
    // Best-effort cache cleanup only.
  }
}

export function clearTalentPending(): void {
  creationMemory.clear()
  abandonMemory.clear()
  removeMatching(TALENT_PREFIX)
}

export function clearTalentPendingExcept(accountId: string): void {
  for (const key of creationMemory.keys()) {
    if (key !== accountId) creationMemory.delete(key)
  }
  for (const key of abandonMemory.keys()) {
    if (!key.startsWith(`${accountId}:`)) abandonMemory.delete(key)
  }
  try {
    const target = storage()
    if (!target) return
    const accountMarker = `:${accountId}:`
    const keys: string[] = []
    for (let index = 0; index < target.length; index += 1) {
      const candidate = target.key(index)
      if (
        candidate?.startsWith(TALENT_PREFIX) &&
        !candidate.includes(accountMarker)
      ) keys.push(candidate)
    }
    for (const candidate of keys) target.removeItem(candidate)
  } catch {
    // Best-effort identity-boundary cleanup only.
  }
}
