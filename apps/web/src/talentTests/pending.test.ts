import { afterEach, expect, it, vi } from "vitest"

import {
  clearTalentPending,
  clearTalentPendingExcept,
  clearTalentAbandonRequest,
  confirmTalentSessionCreated,
  getOrCreateTalentAbandonRequest,
  getOrCreateTalentSessionRequest,
  putPending,
  readPending,
  removePending,
  sanitizeSubmission,
} from "./pending"
import type { Submission } from "./contracts"

function reactionSubmission(id: string): Submission {
  return {
    test_id: "reactiontime",
    raw: { samples_ms: [200, 200, 200, 200, 200], too_soon_count: 0 },
    device: {
      input_kind: "mouse",
      viewport_width: 1440,
      viewport_height: 900,
      geometry: null,
    },
    duration_ms: 10_000,
    background_interrupted: false,
    engine_version: "reactiontime-clone-v1",
    locale: "zh-CN",
    submission_id: id,
  }
}

function dynamicVisionSubmission(id: string): Submission {
  return {
    test_id: "dynamic-vision",
    raw: {
      selected_level: 4,
      outcomes: ["correct", "correct", "incorrect", "skipped", "correct"],
      measured_display_ms: [36, 36.25, 35.75, 36, 36],
      font_size_px: 56,
    },
    device: {
      input_kind: "touch",
      viewport_width: 1280,
      viewport_height: 720,
      geometry: { width: 800, height: 420, target_diameter: null },
    },
    duration_ms: 13_000,
    background_interrupted: false,
    engine_version: "dynamic-vision-v1",
    locale: "zh-CN",
    submission_id: id,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  clearTalentPending()
})

it("reuses an account-scoped creation request until a matching response is confirmed", () => {
  vi.spyOn(crypto, "randomUUID")
    .mockReturnValueOnce("f1000000-0000-4000-8000-000000000010")
    .mockReturnValueOnce("f1000000-0000-4000-8000-000000000011")
    .mockReturnValueOnce("f1000000-0000-4000-8000-000000000015")
  const first = getOrCreateTalentSessionRequest("account-a", "talent-training-v1")

  expect(getOrCreateTalentSessionRequest("account-a", "talent-training-v1")).toEqual(first)
  expect(getOrCreateTalentSessionRequest("account-a", "talent-training-v2")).toEqual(first)
  expect(getOrCreateTalentSessionRequest("account-b", "talent-training-v1")).not.toEqual(first)
  expect(confirmTalentSessionCreated("account-a", first, {
    id: "f1000000-0000-4000-8000-000000000012",
    catalog_version: "wrong-catalog",
  })).toBe(false)
  expect(getOrCreateTalentSessionRequest("account-a", "talent-training-v1")).toEqual(first)
  expect(confirmTalentSessionCreated("account-a", first, {
    id: "f1000000-0000-4000-8000-000000000012",
    catalog_version: "talent-training-v1",
  })).toBe(true)
  expect(getOrCreateTalentSessionRequest("account-a", "talent-training-v1")).not.toEqual(first)
})

it("keeps the same creation request in memory when session storage is disabled", () => {
  vi.spyOn(crypto, "randomUUID").mockReturnValue(
    "f1000000-0000-4000-8000-000000000013",
  )
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("disabled", "SecurityError")
  })
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("disabled", "SecurityError")
  })

  const first = getOrCreateTalentSessionRequest("account-a", "talent-training-v1")

  expect(getOrCreateTalentSessionRequest("account-a", "talent-training-v1")).toEqual(first)
})

it("recovers an unconfirmed creation envelope from session storage", () => {
  const stored = {
    request_id: "f1000000-0000-4000-8000-000000000018",
    catalog_version: "talent-training-v1",
  }
  sessionStorage.setItem(
    "erp:talent-tests:create:account-c:current",
    JSON.stringify(stored),
  )

  expect(getOrCreateTalentSessionRequest("account-c", "talent-training-v2")).toEqual(stored)
})

it("keeps the same abandon request in memory when session storage is disabled", () => {
  vi.spyOn(crypto, "randomUUID")
    .mockReturnValueOnce("f1000000-0000-4000-8000-000000000016")
    .mockReturnValueOnce("f1000000-0000-4000-8000-000000000017")
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
    throw new DOMException("disabled", "SecurityError")
  })
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("disabled", "SecurityError")
  })

  const first = getOrCreateTalentAbandonRequest("account-a", "session-a")
  expect(getOrCreateTalentAbandonRequest("account-a", "session-a")).toBe(first)
  clearTalentAbandonRequest("account-a", "session-a", first)
  expect(getOrCreateTalentAbandonRequest("account-a", "session-a")).not.toBe(first)
})

it("reconstructs the exact submission whitelist before storage or network use", () => {
  const unsafe = {
    ...reactionSubmission("f1000000-0000-4000-8000-000000000014"),
    answer: "secret-answer",
    email: "student@example.com",
    raw: {
      samples_ms: [200, 200, 200, 200, 200],
      too_soon_count: 0,
      answer: "raw-secret",
    },
    device: {
      input_kind: "mouse",
      viewport_width: 1440,
      viewport_height: 900,
      geometry: { width: 800, height: 500, target_diameter: null, secret: "geometry-secret" },
      token: "device-token",
    },
  }

  const sanitized = sanitizeSubmission(unsafe)
  expect(sanitized).toEqual({
    ...reactionSubmission("f1000000-0000-4000-8000-000000000014"),
    device: {
      input_kind: "mouse",
      viewport_width: 1440,
      viewport_height: 900,
      geometry: { width: 800, height: 500, target_diameter: null },
    },
  })
  putPending("account-a", "session-a", unsafe as Submission, 1)
  expect(JSON.stringify(sessionStorage)).not.toContain("secret")
  expect(JSON.stringify(readPending(
    "account-a", "session-a", "reactiontime", 2,
  ))).not.toContain("secret")

  expect(sanitizeSubmission({
    ...unsafe,
    raw: { samples_ms: [200, 200, 200, 200], too_soon_count: 0 },
  })).toBeNull()
  expect(sanitizeSubmission({
    ...unsafe,
    device: {
      input_kind: "mouse",
      viewport_width: 1440,
      viewport_height: 900,
      geometry: { width: 800, height: 500, target_diameter: 501 },
    },
  })).toBeNull()
})

it("keeps finite decimal timings and geometry accepted by the server contract", () => {
  const value = {
    ...reactionSubmission("f1000000-0000-4000-8000-000000000019"),
    raw: { samples_ms: [200.1, 200.2, 200.3, 200.4, 200.5], too_soon_count: 0 },
    device: {
      input_kind: "mouse",
      viewport_width: 1440.5,
      viewport_height: 900.25,
      geometry: { width: 800.5, height: 500.25, target_diameter: 42.5 },
    },
    duration_ms: 10_000.5,
  }

  expect(sanitizeSubmission(value)).toEqual(value)
})

it("whitelists only the four dynamic vision raw keys in storage and network recovery", () => {
  const value = {
    ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000020"),
    answer: "outer-secret",
    raw: {
      ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000020").raw,
      digits: ["00000"],
      answers: ["00000"],
    },
  }

  putPending("account-dynamic", "session-dynamic", value as Submission, 1000)

  const recovered = readPending(
    "account-dynamic", "session-dynamic", "dynamic-vision", 1001,
  )
  expect(recovered).toEqual(dynamicVisionSubmission(
    "f1000000-0000-4000-8000-000000000020",
  ))
  expect(JSON.stringify(recovered)).not.toMatch(/digits|answers|answer|secret/)
  expect(JSON.stringify(sessionStorage)).not.toMatch(/digits|answers|answer|secret/)
})

it.each([
  ["wrong engine", { engine_version: "dynamic-vision-clone-v1" }],
  ["boolean level", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, selected_level: true } }],
  ["out-of-range level", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, selected_level: 6 } }],
  ["short outcomes", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, outcomes: ["correct"] } }],
  ["unknown outcome", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, outcomes: ["correct", "correct", "incorrect", "skipped", "partial"] } }],
  ["zero measured duration", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, measured_display_ms: [36, 36, 0, 36, 36] } }],
  ["long measured duration", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, measured_display_ms: [36, 36, 10_001, 36, 36] } }],
  ["wrong font size", { raw: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").raw, font_size_px: 48 } }],
  ["missing geometry", { device: { ...dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021").device, geometry: null } }],
  ["duration below measurements", { duration_ms: 100 }],
] as const)("rejects a dynamic vision submission with %s", (_name, change) => {
  const value = dynamicVisionSubmission("f1000000-0000-4000-8000-000000000021")
  const candidate = {
    ...value,
    ...change,
    raw: "raw" in change ? change.raw : value.raw,
    device: "device" in change ? change.device : value.device,
  }
  expect(sanitizeSubmission(candidate)).toBeNull()
})

it("isolates and expires dynamic vision pending results by account and round", () => {
  const value = dynamicVisionSubmission("f1000000-0000-4000-8000-000000000022")
  putPending("account-a", "session-a", value, 1000)

  expect(readPending("account-b", "session-a", "dynamic-vision", 1001)).toBeNull()
  expect(readPending("account-a", "session-b", "dynamic-vision", 1001)).toBeNull()
  expect(readPending("account-a", "session-a", "dynamic-vision", 1001)).toEqual(value)
  expect(readPending("account-a", "session-a", "dynamic-vision", 86_401_001)).toBeNull()
})

it("recovers both dynamic engines without upgrading stored results and permits level six only for v2", () => {
  const old = dynamicVisionSubmission("f1000000-0000-4000-8000-000000000023")
  const latest = { ...old, engine_version: "dynamic-vision-v2", raw: { ...old.raw, selected_level: 6 } } as unknown as Submission
  putPending("old-account", "old-round", old, 1000)
  putPending("new-account", "new-round", latest, 1000)
  expect(readPending("old-account", "old-round", "dynamic-vision", 1001)).toEqual(old)
  expect(readPending("new-account", "new-round", "dynamic-vision", 1001)).toEqual(latest)
  expect(readPending("old-account", "new-round", "dynamic-vision", 1001)).toBeNull()
  expect(sanitizeSubmission({ ...latest, engine_version: "dynamic-vision-v1" })).toBeNull()
  expect(sanitizeSubmission({ ...latest, raw: { ...latest.raw, selected_level: 7 } })).toBeNull()
  expect(sanitizeSubmission({ ...latest, raw: { ...latest.raw, selected_level: 0 } })).toBeNull()
  expect(sanitizeSubmission({ ...latest, raw: { ...latest.raw, selected_level: 5.5 } })).toBeNull()
})

it("隔离账号且 24 小时后丢弃待保存结果", () => {
  const a = "f1000000-0000-4000-8000-000000000001"
  const b = "f1000000-0000-4000-8000-000000000002"
  const session = "f1000000-0000-4000-8000-000000000003"
  const value = reactionSubmission("f1000000-0000-4000-8000-000000000004")
  putPending(a, session, value, 1000)
  expect(readPending(b, session, "reactiontime", 1001)).toBeNull()
  expect(readPending(a, session, "reactiontime", 1001)).toEqual(value)
  expect(readPending(a, session, "reactiontime", 86_401_001)).toBeNull()
})

it("survives unavailable, malformed, and quota-limited session storage", () => {
  const value = reactionSubmission("f1000000-0000-4000-8000-000000000005")
  putPending("a", "s", value, 1)
  const storedKey = sessionStorage.key(0)
  expect(storedKey).not.toBeNull()
  sessionStorage.setItem(storedKey!, "{")
  expect(readPending("a", "s", "reactiontime", 2)).toBeNull()
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new DOMException("full", "QuotaExceededError")
  })

  expect(() => putPending("a", "s", value, 1)).not.toThrow()
  expect(readPending("a", "s", "reactiontime", 2)).toBeNull()
  expect(() => clearTalentPending()).not.toThrow()
})

it("removes only the selected account, session, and test envelope", () => {
  const first = reactionSubmission("f1000000-0000-4000-8000-000000000006")
  const second = reactionSubmission("f1000000-0000-4000-8000-000000000007")
  putPending("a", "s1", first, 1)
  putPending("a", "s2", second, 1)

  removePending("a", "s1", "reactiontime")

  expect(readPending("a", "s1", "reactiontime", 2)).toBeNull()
  expect(readPending("a", "s2", "reactiontime", 2)).toEqual(second)
})

it("keeps only the authenticated account namespace after an account change", () => {
  const first = reactionSubmission("f1000000-0000-4000-8000-000000000008")
  const second = reactionSubmission("f1000000-0000-4000-8000-000000000009")
  putPending("account-a", "session-a", first, 1)
  putPending("account-b", "session-b", second, 1)

  clearTalentPendingExcept("account-b")

  expect(readPending("account-a", "session-a", "reactiontime", 2)).toBeNull()
  expect(readPending("account-b", "session-b", "reactiontime", 2)).toEqual(second)
})
