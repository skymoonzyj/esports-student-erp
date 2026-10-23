import type { PendingSubmission } from './contracts'

export const ASSESSMENT_PREFIX = 'erp:esports-assessments:'
export const PENDING_PREFIX = `${ASSESSMENT_PREFIX}pending:`

export const pendingMemory = new Map<string, PendingSubmission>()

export function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function storageKeys(target: Storage): string[] {
  try {
    const keys: string[] = []
    for (let index = 0; index < target.length; index += 1) {
      const candidate = target.key(index)
      if (candidate?.startsWith(ASSESSMENT_PREFIX)) keys.push(candidate)
    }
    return keys
  } catch {
    return []
  }
}

export function clearAssessmentPending(account_id?: string): void {
  const accountPrefix = account_id ? `${PENDING_PREFIX}${account_id}:` : ASSESSMENT_PREFIX
  for (const itemKey of pendingMemory.keys()) {
    if (itemKey.startsWith(accountPrefix)) pendingMemory.delete(itemKey)
  }
  const target = storage()
  if (!target) return
  for (const itemKey of storageKeys(target)) {
    if (itemKey.startsWith(accountPrefix)) {
      try {
        target.removeItem(itemKey)
      } catch {
        // Best-effort cache cleanup only.
      }
    }
  }
}

export function clearAssessmentPendingExcept(account_id: string): void {
  const keepPrefix = `${PENDING_PREFIX}${account_id}:`
  for (const itemKey of pendingMemory.keys()) {
    if (!itemKey.startsWith(keepPrefix)) pendingMemory.delete(itemKey)
  }
  const target = storage()
  if (!target) return
  for (const itemKey of storageKeys(target)) {
    if (!itemKey.startsWith(keepPrefix)) {
      try {
        target.removeItem(itemKey)
      } catch {
        // Best-effort identity-boundary cleanup only.
      }
    }
  }
}
