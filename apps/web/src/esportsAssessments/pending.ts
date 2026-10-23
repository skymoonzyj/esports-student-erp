import { pendingSubmissionSchema, type PendingSubmission } from './contracts'
import { PENDING_PREFIX, pendingMemory, storage, storageKeys } from './pendingStorage'
export { clearAssessmentPending, clearAssessmentPendingExcept } from './pendingStorage'

const MAX_AGE_MS = 24 * 60 * 60 * 1000

export type PendingIdentity = Pick<PendingSubmission, 'account_id' | 'mode' | 'suite_id' | 'session_id' | 'test_id' | 'attempt_id' | 'operation_key'>

function pendingKey(value: PendingIdentity): string {
  const parentId = value.session_id ?? value.attempt_id
  return PENDING_PREFIX + [
    value.account_id,
    value.mode,
    value.suite_id,
    parentId,
    value.test_id,
    value.attempt_id,
  ].join(':')
}

/** A late save may only remove its own operation, never a newer or sibling result. */
export function removePending(identity: PendingIdentity): void {
  const key = pendingKey(identity)
  const matches = (value: PendingSubmission | null | undefined) => value
    && pendingKey(value) === key && value.operation_key === identity.operation_key
  if (matches(pendingMemory.get(key))) pendingMemory.delete(key)
  try {
    const target = storage()
    const raw = target?.getItem(key)
    if (raw && matches(parsedPending(raw))) target?.removeItem(key)
  } catch {
    // Best effort; future GET reconciliation still verifies the persisted identity.
  }
}

function current(value: PendingSubmission, now = Date.now()): boolean {
  const createdAt = Date.parse(value.created_at)
  return Number.isFinite(createdAt) && createdAt <= now && now - createdAt <= MAX_AGE_MS
}

function parsedPending(raw: string): PendingSubmission | null {
  try {
    const parsed = pendingSubmissionSchema.safeParse(JSON.parse(raw) as unknown)
    return parsed.success ? parsed.data : null
  } catch {
    return null
  }
}

export function savePending(value: PendingSubmission): { persisted: boolean } {
  const parsed = pendingSubmissionSchema.safeParse(value)
  if (!parsed.success || !current(parsed.data)) return { persisted: false }
  const itemKey = pendingKey(parsed.data)
  try {
    const target = storage()
    if (!target) throw new Error('session storage unavailable')
    target.setItem(itemKey, JSON.stringify(parsed.data))
    pendingMemory.delete(itemKey)
    return { persisted: true }
  } catch {
    pendingMemory.set(itemKey, parsed.data)
    return { persisted: false }
  }
}

export function loadPending(account_id: string): PendingSubmission[] {
  const result: PendingSubmission[] = []
  const seen = new Set<string>()
  const target = storage()
  if (target) {
    for (const itemKey of storageKeys(target)) {
      if (!itemKey.startsWith(PENDING_PREFIX)) continue
      let value: PendingSubmission | null
      try {
        const raw = target.getItem(itemKey)
        value = raw ? parsedPending(raw) : null
      } catch {
        value = null
      }
      if (!value || pendingKey(value) !== itemKey || !current(value)) {
        try {
          target.removeItem(itemKey)
        } catch {
          // Best-effort cleanup; an unavailable cache is treated as absent.
        }
        continue
      }
      seen.add(itemKey)
      if (value.account_id === account_id) result.push(value)
    }
  }
  for (const [itemKey, value] of pendingMemory) {
    if (pendingKey(value) !== itemKey || !current(value)) {
      pendingMemory.delete(itemKey)
      continue
    }
    if (!seen.has(itemKey) && value.account_id === account_id) result.push(value)
  }
  return result
}

export function hasVolatilePending(account_id: string): boolean {
  for (const [itemKey, value] of pendingMemory) {
    if (pendingKey(value) !== itemKey || !current(value)) {
      pendingMemory.delete(itemKey)
      continue
    }
    if (value.account_id === account_id) return true
  }
  return false
}
