import type {
  RegistrationChallenge,
  RegistrationIdentifierKind,
} from "../registration/api"

export type AccountIdentifierChallengeDraft = {
  identifierKind: RegistrationIdentifierKind
  identifier: string
  challenge: RegistrationChallenge
}

const storagePrefix = "esports-erp.account-identifier-challenge:"

function sessionStorageOrNull(): Storage | null {
  if (typeof window === "undefined") return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isChallenge(value: unknown): value is RegistrationChallenge {
  if (!isRecord(value)) return false
  return (
    typeof value.challenge_id === "string" &&
    value.challenge_id.length > 0 &&
    typeof value.expires_at === "string" &&
    Number.isFinite(Date.parse(value.expires_at)) &&
    typeof value.retry_after_seconds === "number" &&
    Number.isFinite(value.retry_after_seconds) &&
    value.retry_after_seconds >= 0 &&
    typeof value.masked_destination === "string" &&
    typeof value.request_id === "string"
  )
}

function isDraft(value: unknown): value is AccountIdentifierChallengeDraft {
  if (!isRecord(value)) return false
  return (
    (value.identifierKind === "phone" || value.identifierKind === "email") &&
    typeof value.identifier === "string" &&
    value.identifier.trim().length > 0 &&
    isChallenge(value.challenge)
  )
}

export function accountIdentifierChallengeStorageKey(accountId: string): string {
  return `${storagePrefix}${accountId}`
}

export function readAccountIdentifierChallengeDraft(
  accountId: string,
): AccountIdentifierChallengeDraft | null {
  const storage = sessionStorageOrNull()
  if (!storage) return null
  const key = accountIdentifierChallengeStorageKey(accountId)
  try {
    const raw = storage.getItem(key)
    if (!raw) return null
    const draft: unknown = JSON.parse(raw)
    if (!isDraft(draft) || Date.parse(draft.challenge.expires_at) <= Date.now()) {
      storage.removeItem(key)
      return null
    }
    return {
      identifierKind: draft.identifierKind,
      identifier: draft.identifier,
      challenge: draft.challenge,
    }
  } catch {
    storage.removeItem(key)
    return null
  }
}

export function writeAccountIdentifierChallengeDraft(
  accountId: string,
  draft: AccountIdentifierChallengeDraft,
): void {
  const storage = sessionStorageOrNull()
  if (!storage) return
  try {
    storage.setItem(accountIdentifierChallengeStorageKey(accountId), JSON.stringify(draft))
  } catch {
    // Browser storage can be unavailable in private or restricted contexts.
  }
}

export function clearAccountIdentifierChallengeDraft(accountId: string): void {
  const storage = sessionStorageOrNull()
  if (!storage) return
  try {
    storage.removeItem(accountIdentifierChallengeStorageKey(accountId))
  } catch {
    // Browser storage can be unavailable in private or restricted contexts.
  }
}

export function clearAllAccountIdentifierChallengeDrafts(): void {
  const storage = sessionStorageOrNull()
  if (!storage) return
  try {
    for (let index = storage.length - 1; index >= 0; index -= 1) {
      const key = storage.key(index)
      if (key?.startsWith(storagePrefix)) storage.removeItem(key)
    }
  } catch {
    // Browser storage can be unavailable in private or restricted contexts.
  }
}
