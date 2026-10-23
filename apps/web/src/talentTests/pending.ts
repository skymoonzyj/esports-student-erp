import type {
  DeviceSnapshot,
  RawByTest,
  SessionCreateRequest,
  Submission,
  TestId,
} from "./contracts"
import { ENGINE_VERSIONS } from "./contracts"

const TALENT_PREFIX = "erp:talent-tests:"
const PENDING_PREFIX = `${TALENT_PREFIX}pending:`
const CREATE_PREFIX = `${TALENT_PREFIX}create:`
const ABANDON_PREFIX = `${TALENT_PREFIX}abandon:`
const MAX_AGE_MS = 24 * 60 * 60 * 1000
const MAX_DURATION_MS = 86_400_000
const MAX_COUNTER = 1_000_000

const creationMemory = new Map<string, SessionCreateRequest>()
const abandonMemory = new Map<string, string>()

type PendingEnvelope = {
  savedAt: number
  value: Submission
}

function storage(): Storage | null {
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function key(accountId: string, sessionId: string, testId: TestId): string {
  return `${PENDING_PREFIX}${accountId}:${sessionId}:${testId}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function isUuid(value: unknown): value is string {
  return typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value)
}

function isNonNegativeInteger(value: unknown): value is number {
  return isFiniteNumber(value) && Number.isInteger(value) &&
    value >= 0 && value <= MAX_COUNTER
}

function isFiniteNonNegative(value: unknown): value is number {
  return isFiniteNumber(value) && value >= 0
}

function isFinitePositiveBounded(value: unknown): value is number {
  return isFiniteNumber(value) && value > 0 && value <= MAX_COUNTER
}

function numberArray(value: unknown, length: number): number[] | null {
  if (!Array.isArray(value) || value.length !== length || !value.every(isFiniteNonNegative)) {
    return null
  }
  return [...value]
}

function positiveNumberArray(value: unknown, length: number): number[] | null {
  if (
    !Array.isArray(value) ||
    value.length !== length ||
    !value.every((item) => isFiniteNumber(item) && item > 0 && item <= 10_000)
  ) return null
  return [...value]
}

function sanitizedDevice(value: unknown): DeviceSnapshot | null {
  if (!isRecord(value)) return null
  const inputKind = value.input_kind
  if (
    inputKind !== "mouse" && inputKind !== "touch" && inputKind !== "pen" &&
    inputKind !== "mixed" && inputKind !== "unknown"
  ) return null
  if (!isFinitePositiveBounded(value.viewport_width) ||
      !isFinitePositiveBounded(value.viewport_height)) {
    return null
  }
  let geometry: DeviceSnapshot["geometry"] = null
  if (value.geometry !== null) {
    if (!isRecord(value.geometry)) return null
    const targetDiameter = value.geometry.target_diameter
    if (
      !isFinitePositiveBounded(value.geometry.width) ||
      !isFinitePositiveBounded(value.geometry.height) ||
      (targetDiameter !== null && (
        !isFinitePositiveBounded(targetDiameter) ||
        targetDiameter > Math.min(value.geometry.width, value.geometry.height)
      ))
    ) return null
    geometry = {
      width: value.geometry.width,
      height: value.geometry.height,
      target_diameter: targetDiameter,
    }
  }
  return {
    input_kind: inputKind,
    viewport_width: value.viewport_width,
    viewport_height: value.viewport_height,
    geometry,
  }
}

export function sanitizeSubmission(value: unknown): Submission | null {
  if (!isRecord(value) || !isRecord(value.raw)) return null
  const testId = value.test_id
  if (
    testId !== "reactiontime" && testId !== "aim" && testId !== "sequence" &&
    testId !== "number-memory" && testId !== "verbal-memory" &&
    testId !== "chimp" && testId !== "memory" && testId !== "dynamic-vision"
  ) return null
  if (
    !isUuid(value.submission_id) ||
    value.locale !== "zh-CN" ||
    value.engine_version !== ENGINE_VERSIONS[testId] ||
    !isFiniteNonNegative(value.duration_ms) ||
    value.duration_ms > MAX_DURATION_MS ||
    typeof value.background_interrupted !== "boolean"
  ) return null
  const device = sanitizedDevice(value.device)
  if (!device) return null
  const raw = value.raw
  const common = {
    device,
    duration_ms: value.duration_ms,
    background_interrupted: value.background_interrupted,
    submission_id: value.submission_id,
    locale: "zh-CN" as const,
  }
  switch (testId) {
    case "reactiontime": {
      const samples = numberArray(raw.samples_ms, 5)
      if (!samples || !isNonNegativeInteger(raw.too_soon_count)) return null
      return {
        test_id: "reactiontime",
        raw: { samples_ms: samples, too_soon_count: raw.too_soon_count },
        engine_version: "reactiontime-clone-v1",
        ...common,
      }
    }
    case "aim": {
      const intervals = numberArray(raw.intervals_ms, 30)
      if (!intervals || !isNonNegativeInteger(raw.miss_count) ||
          !isFiniteNonNegative(raw.total_elapsed_ms) ||
          raw.total_elapsed_ms > MAX_DURATION_MS) return null
      return {
        test_id: "aim",
        raw: {
          intervals_ms: intervals,
          miss_count: raw.miss_count,
          total_elapsed_ms: raw.total_elapsed_ms,
        },
        engine_version: "aim-clone-v1",
        ...common,
      }
    }
    case "sequence":
      if (!isNonNegativeInteger(raw.reached_level) ||
          !isNonNegativeInteger(raw.completed_level) ||
          !isNonNegativeInteger(raw.failed_step) || raw.end_reason !== "wrong_tile") return null
      return {
        test_id: "sequence",
        raw: {
          reached_level: raw.reached_level,
          completed_level: raw.completed_level,
          failed_step: raw.failed_step,
          end_reason: "wrong_tile",
        },
        engine_version: "sequence-clone-v1",
        ...common,
      }
    case "number-memory":
      if (!isNonNegativeInteger(raw.correct_digits) ||
          !isNonNegativeInteger(raw.failed_digits) ||
          !isNonNegativeInteger(raw.display_duration_ms)) return null
      return {
        test_id: "number-memory",
        raw: {
          correct_digits: raw.correct_digits,
          failed_digits: raw.failed_digits,
          display_duration_ms: raw.display_duration_ms,
        },
        engine_version: "number-memory-clone-v1",
        ...common,
      }
    case "verbal-memory":
      if (!isNonNegativeInteger(raw.correct_count) ||
          !isNonNegativeInteger(raw.error_count) ||
          !isNonNegativeInteger(raw.total_count) || raw.word_bank_version !== "zh-192-v1") return null
      return {
        test_id: "verbal-memory",
        raw: {
          correct_count: raw.correct_count,
          error_count: raw.error_count,
          total_count: raw.total_count,
          word_bank_version: "zh-192-v1",
        },
        engine_version: "verbal-memory-clone-v1",
        ...common,
      }
    case "chimp":
      if (!isNonNegativeInteger(raw.reached_count) ||
          !isNonNegativeInteger(raw.completed_count) ||
          !isNonNegativeInteger(raw.strikes) ||
          (raw.end_reason !== "strikes" && raw.end_reason !== "engine_cap")) return null
      return {
        test_id: "chimp",
        raw: {
          reached_count: raw.reached_count,
          completed_count: raw.completed_count,
          strikes: raw.strikes,
          end_reason: raw.end_reason,
        },
        engine_version: "chimp-clone-v1",
        ...common,
      }
    case "memory":
      if (!isNonNegativeInteger(raw.reached_level) ||
          !isNonNegativeInteger(raw.completed_level) ||
          !isNonNegativeInteger(raw.accepted_misses) ||
          !isNonNegativeInteger(raw.lives_used) || raw.end_reason !== "lives") return null
      return {
        test_id: "memory",
        raw: {
          reached_level: raw.reached_level,
          completed_level: raw.completed_level,
          accepted_misses: raw.accepted_misses,
          lives_used: raw.lives_used,
          end_reason: "lives",
        },
        engine_version: "memory-clone-v1",
        ...common,
      }
    case "dynamic-vision": {
      const selectedLevel = raw.selected_level
      const outcomes = raw.outcomes
      const measured = positiveNumberArray(raw.measured_display_ms, 5)
      const fontSize = raw.font_size_px
      if (
        !Number.isInteger(selectedLevel) ||
        typeof selectedLevel !== "number" ||
        selectedLevel < 1 || selectedLevel > 5 ||
        !Array.isArray(outcomes) || outcomes.length !== 5 ||
        !outcomes.every((outcome) =>
          outcome === "correct" || outcome === "incorrect" || outcome === "skipped"
        ) ||
        !measured ||
        (fontSize !== 40 && fontSize !== 56) ||
        fontSize !== (device.viewport_width < 600 ? 40 : 56) ||
        device.geometry === null ||
        device.geometry.target_diameter !== null ||
        value.duration_ms < measured.reduce((total, duration) => total + duration, 0)
      ) return null
      return {
        test_id: "dynamic-vision",
        raw: {
          selected_level: selectedLevel as 1 | 2 | 3 | 4 | 5,
          outcomes: [...outcomes] as RawByTest["dynamic-vision"]["outcomes"],
          measured_display_ms: measured as [number, number, number, number, number],
          font_size_px: fontSize,
        },
        engine_version: "dynamic-vision-v1",
        ...common,
      }
    }
  }
}

function pendingEnvelope(value: unknown, testId: TestId): PendingEnvelope | null {
  if (!isRecord(value) || typeof value.savedAt !== "number" || !Number.isFinite(value.savedAt)) {
    return null
  }
  const submission = sanitizeSubmission(value.value)
  if (!submission || submission.test_id !== testId) return null
  return { savedAt: value.savedAt, value: submission }
}

export function putPending(
  accountId: string,
  sessionId: string,
  value: Submission,
  now: number,
): void {
  const submission = sanitizeSubmission(value)
  if (!submission) return
  try {
    storage()?.setItem(
      key(accountId, sessionId, submission.test_id),
      JSON.stringify({ savedAt: now, value: submission }),
    )
  } catch {
    // sessionStorage may be disabled or full; the server request can still proceed.
  }
}

function creationKey(accountId: string): string {
  return `${CREATE_PREFIX}${accountId}:current`
}

function storedCreationRequest(accountId: string): SessionCreateRequest | null {
  const memory = creationMemory.get(accountId)
  if (memory) return memory
  try {
    const raw = storage()?.getItem(creationKey(accountId))
    if (!raw) return null
    const parsed = JSON.parse(raw) as unknown
    if (!isRecord(parsed) || !isUuid(parsed.request_id) ||
        typeof parsed.catalog_version !== "string" || parsed.catalog_version.length === 0) {
      storage()?.removeItem(creationKey(accountId))
      return null
    }
    const request = {
      request_id: parsed.request_id,
      catalog_version: parsed.catalog_version,
    }
    creationMemory.set(accountId, request)
    return request
  } catch {
    return null
  }
}

export function getOrCreateTalentSessionRequest(
  accountId: string,
  catalogVersion: string,
): SessionCreateRequest {
  const existing = storedCreationRequest(accountId)
  if (existing) return existing
  const request = { request_id: crypto.randomUUID(), catalog_version: catalogVersion }
  creationMemory.set(accountId, request)
  try {
    storage()?.setItem(creationKey(accountId), JSON.stringify(request))
  } catch {
    // The in-memory envelope preserves this operation key for the current page.
  }
  return request
}

export function confirmTalentSessionCreated(
  accountId: string,
  request: SessionCreateRequest,
  response: { id: string; catalog_version: string },
): boolean {
  if (!isUuid(response.id) || response.catalog_version !== request.catalog_version) return false
  const current = storedCreationRequest(accountId)
  if (!current || current.request_id !== request.request_id ||
      current.catalog_version !== request.catalog_version) return false
  creationMemory.delete(accountId)
  try {
    storage()?.removeItem(creationKey(accountId))
  } catch {
    // In-memory cleanup is authoritative while storage is unavailable.
  }
  return true
}

export function discardTalentSessionRequest(
  accountId: string,
  request: SessionCreateRequest,
): void {
  const current = storedCreationRequest(accountId)
  if (!current || current.request_id !== request.request_id ||
      current.catalog_version !== request.catalog_version) return
  creationMemory.delete(accountId)
  try {
    storage()?.removeItem(creationKey(accountId))
  } catch {
    // The in-memory operation was discarded after an explicit user refresh.
  }
}

function abandonOperationKey(accountId: string, sessionId: string): string {
  return `${accountId}:${sessionId}`
}

function abandonStorageKey(accountId: string, sessionId: string): string {
  return `${ABANDON_PREFIX}${accountId}:${sessionId}`
}

export function getOrCreateTalentAbandonRequest(
  accountId: string,
  sessionId: string,
): string {
  const operationKey = abandonOperationKey(accountId, sessionId)
  const memory = abandonMemory.get(operationKey)
  if (memory) return memory
  try {
    const stored = storage()?.getItem(abandonStorageKey(accountId, sessionId))
    if (isUuid(stored)) {
      abandonMemory.set(operationKey, stored)
      return stored
    }
  } catch {
    // Fall through to an in-memory operation key.
  }
  const requestId = crypto.randomUUID()
  abandonMemory.set(operationKey, requestId)
  try {
    storage()?.setItem(abandonStorageKey(accountId, sessionId), requestId)
  } catch {
    // In-memory state preserves the key for this page lifetime.
  }
  return requestId
}

export function clearTalentAbandonRequest(
  accountId: string,
  sessionId: string,
  requestId: string,
): void {
  const operationKey = abandonOperationKey(accountId, sessionId)
  if (abandonMemory.get(operationKey) === requestId) {
    abandonMemory.delete(operationKey)
  }
  try {
    const itemKey = abandonStorageKey(accountId, sessionId)
    if (storage()?.getItem(itemKey) === requestId) storage()?.removeItem(itemKey)
  } catch {
    // In-memory cleanup is authoritative while storage is unavailable.
  }
}

export function readPending(
  accountId: string,
  sessionId: string,
  testId: TestId,
  now: number,
): Submission | null {
  const itemKey = key(accountId, sessionId, testId)
  try {
    const target = storage()
    const raw = target?.getItem(itemKey)
    if (!raw) return null
    const envelope = pendingEnvelope(JSON.parse(raw) as unknown, testId)
    if (!envelope || now - envelope.savedAt > MAX_AGE_MS || now < envelope.savedAt) {
      target?.removeItem(itemKey)
      return null
    }
    return envelope.value
  } catch {
    try {
      storage()?.removeItem(itemKey)
    } catch {
      // Storage remains unavailable; behave as if there is no recovery cache.
    }
    return null
  }
}

export function removePending(
  accountId: string,
  sessionId: string,
  testId: TestId,
): void {
  try {
    storage()?.removeItem(key(accountId, sessionId, testId))
  } catch {
    // Best-effort cache cleanup only.
  }
}

export function clearTalentSessionPending(accountId: string, sessionId: string): void {
  const prefix = `${PENDING_PREFIX}${accountId}:${sessionId}:`
  removeMatching(prefix)
}

function removeMatching(prefix: string): void {
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
