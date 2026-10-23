import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import {
  acceptHandedOffGuardianInvitation,
  completePasswordReset,
  confirmRegistrationTermsConsent,
  createMinorGuardianInvitation,
  finalizeRegistration,
  getGuardianInvitationAcceptance,
  getGuardianInvitationContext,
  getRegistrationConflict,
  getRegistrationChannelSettings,
  getRegistrationMe,
  getRegistrationTermsConsent,
  getStaffApplication,
  handoffGuardianInvitation,
  issueAccountIdentifierChallenge,
  issuePasswordResetChallenge,
  listAccountIdentifiers,
  listMinorGuardianInvitations,
  listRegistrationConflicts,
  listStaffApplications,
  registrationFetch,
  resolveRegistrationConflict,
  restoreRegistrationCsrf,
  reviewStaffApplication,
  setRegistrationCsrfToken,
  setRegistrationSessionBoundaryHandler,
  startRegistration,
  startGuardianInvitationRegistration,
  updateRegistrationProfile,
  verifyAccountIdentifier,
  verifyRegistration,
} from "./api"

const stateResponse = {
  state: "contact_verified" as const,
  primary_purpose: "student" as const,
  masked_identifier: "s***@example.com",
  next_action: "complete_profile" as const,
  expires_at: "2026-09-15T10:00:00+08:00",
  request_id: "registration-state-request",
}

it("uses an HttpOnly handoff flow without putting the invitation token in API paths", async () => {
  const context = {
    masked_identifier: "138****8888",
    relationship: "母亲",
    is_primary: true,
    expires_at: "2026-08-23T10:00:00+08:00",
    consent_version: "2026-08",
    request_id: "guardian-context",
  }
  const started = {
    application_id: "10000000-0000-4000-8000-000000000188",
    state: "draft",
    masked_identifier: "138****8888",
    expires_at: "2026-08-16T10:10:00+08:00",
    request_id: "guardian-start",
  }
  const accepted = {
    status: "accepted",
    student_finalized: true,
    request_id: "guardian-accepted",
  }
  const receipt = {
    status: "accepted",
    student_finalized: true,
    request_id: "guardian-acceptance-receipt",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/handoff")) return new Response(null, { status: 204 })
    if (url.endsWith("/context")) return jsonResponse(context)
    if (url.endsWith("/acceptance")) return jsonResponse(receipt)
    if (url.endsWith("/start-registration")) return jsonResponse(started, 201)
    if (url.endsWith("/accept")) return jsonResponse(accepted)
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("formal-csrf")

  await handoffGuardianInvitation("opaque-invitation-code")
  await getGuardianInvitationContext()
  await getGuardianInvitationAcceptance()
  await startGuardianInvitationRegistration()
  await acceptHandedOffGuardianInvitation("2026-08")

  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    "/api/v1/registration/guardian-invitations/handoff",
    "/api/v1/registration/guardian-invitations/context",
    "/api/v1/registration/guardian-invitations/acceptance",
    "/api/v1/registration/guardian-invitations/start-registration",
    "/api/v1/registration/guardian-invitations/accept",
  ])
  expect(fetchMock.mock.calls.every(([, init]) => init?.credentials === "include")).toBe(true)
  const handoffBody = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as unknown
  expect(handoffBody).toEqual({ token: "opaque-invitation-code" })
  expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("X-CSRF-Token")).toBeNull()
  expect(new Headers(fetchMock.mock.calls[4]?.[1]?.headers).get("X-CSRF-Token")).toBe(
    "formal-csrf",
  )
})

it("reads and confirms current registration terms in the registration csrf domain", async () => {
  const status = {
    current_version: "2026-08",
    accepted_version: "2026-07",
    reconfirmation_required: true,
    request_id: "terms-status",
  }
  const confirmed = {
    ...status,
    accepted_version: "2026-08",
    reconfirmation_required: false,
    request_id: "terms-confirmed",
  }
  const fetchMock = vi.fn<typeof fetch>(async (_input, init) =>
    jsonResponse(init?.method === "POST" ? confirmed : status))
  vi.stubGlobal("fetch", fetchMock)
  setRegistrationCsrfToken("registration-csrf")

  await expect(getRegistrationTermsConsent()).resolves.toEqual(status)
  await expect(confirmRegistrationTermsConsent("2026-08")).resolves.toEqual(confirmed)

  expect(
    new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get(
      "X-Registration-CSRF-Token",
    ),
  ).toBe("registration-csrf")
  expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
    consent_version: "2026-08",
  })
})

it("rejects guardian invitation context responses with private identifiers", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => jsonResponse({
      masked_identifier: "138****8888",
      relationship: "母亲",
      is_primary: true,
      expires_at: "2026-08-23T10:00:00+08:00",
      consent_version: "2026-08",
      request_id: "guardian-context",
      invitation_id: "10000000-0000-4000-8000-000000000181",
    })),
  )

  await expect(getGuardianInvitationContext()).rejects.toThrow(
    "Invalid registration response",
  )
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function deferred<T>(): {
  promise: Promise<T>
  resolve: (value: T | PromiseLike<T>) => void
  reject: (reason?: unknown) => void
} {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  setCsrfToken(null)
  setRegistrationCsrfToken(null)
  setRegistrationSessionBoundaryHandler(null)
  vi.unstubAllGlobals()
})

it("keeps public channel settings valid across an unrelated auth boundary reset", async () => {
  const channelResponse = deferred<Response>()
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => channelResponse.promise))

  const request = getRegistrationChannelSettings()
  setRegistrationCsrfToken(null)
  channelResponse.resolve(jsonResponse({
    phone_enabled: false,
    email_enabled: true,
    phone_signup_allowed_by_deployment: false,
    updated_at: null,
    request_id: "public-registration-channels",
  }))

  await expect(request).resolves.toEqual({
    phone_enabled: false,
    email_enabled: true,
    phone_signup_allowed_by_deployment: false,
    updated_at: null,
    request_id: "public-registration-channels",
  })
})

it("shares one in-flight csrf restore within a registration session generation", async () => {
  const responseGate = deferred<void>()
  const fetchMock = vi.fn<typeof fetch>(async () => {
    await responseGate.promise
    return jsonResponse({ csrf_token: "shared-registration-csrf" })
  })
  vi.stubGlobal("fetch", fetchMock)

  const firstRestore = restoreRegistrationCsrf()
  const secondRestore = restoreRegistrationCsrf()
  const csrfRequestCount = fetchMock.mock.calls.length
  responseGate.resolve()

  await expect(Promise.all([firstRestore, secondRestore])).resolves.toEqual([
    "shared-registration-csrf",
    "shared-registration-csrf",
  ])
  expect(csrfRequestCount).toBe(1)
  expect(fetchMock).toHaveBeenCalledTimes(1)
})

it("keeps a late csrf restore from an old start boundary out of the new session", async () => {
  const oldCsrfResponse = deferred<Response>()
  const newCsrfResponse = deferred<Response>()
  const boundaryHandler = vi.fn()
  let csrfRequestCount = 0
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    if (url.endsWith("/csrf")) {
      csrfRequestCount += 1
      return csrfRequestCount === 1
        ? oldCsrfResponse.promise
        : newCsrfResponse.promise
    }
    if (url.endsWith("/applications")) {
      return Promise.resolve(jsonResponse({
        application_id: "10000000-0000-4000-8000-000000000201",
        state: "draft",
        masked_identifier: "138****0201",
        expires_at: "2026-08-17T14:00:00+08:00",
        request_id: "registration-start-boundary",
      }, 201))
    }
    if (url.endsWith("/probe")) return Promise.resolve(jsonResponse({ ok: true }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setRegistrationSessionBoundaryHandler(boundaryHandler)

  const oldRestore = restoreRegistrationCsrf()
  await startRegistration({
    primary_purpose: "student",
    identifier_kind: "phone",
    identifier: "13800000201",
  })
  const newRestore = restoreRegistrationCsrf()

  expect(csrfRequestCount).toBe(2)
  expect(boundaryHandler).toHaveBeenCalledTimes(1)
  newCsrfResponse.resolve(jsonResponse({ csrf_token: "new-registration-csrf" }))
  await expect(newRestore).resolves.toBe("new-registration-csrf")
  oldCsrfResponse.resolve(jsonResponse({ csrf_token: "old-secret-registration-csrf" }))
  const staleError = await oldRestore.catch((error: unknown) => error)
  expect(staleError).toMatchObject({ name: "RegistrationSessionStaleError" })
  expect(String(staleError)).not.toContain("old-secret-registration-csrf")
  expect(String(staleError)).not.toContain("new-registration-csrf")

  await registrationFetch("/probe", { method: "POST" }, (value) => value)
  const probeCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/probe"))
  expect(new Headers(probeCall?.[1]?.headers).get(
    "X-Registration-CSRF-Token",
  )).toBe("new-registration-csrf")
})

it("keeps a stale restore error from clearing the new generation in-flight restore", async () => {
  const oldCsrfResponse = deferred<Response>()
  const newCsrfResponseGate = deferred<void>()
  const boundaryHandler = vi.fn()
  let csrfRequestCount = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/csrf")) {
      csrfRequestCount += 1
      if (csrfRequestCount === 1) return oldCsrfResponse.promise
      await newCsrfResponseGate.promise
      return jsonResponse({ csrf_token: "new-registration-csrf" })
    }
    if (url.endsWith("/applications")) {
      return jsonResponse({
        application_id: "10000000-0000-4000-8000-000000000202",
        state: "draft",
        masked_identifier: "138****0202",
        expires_at: "2026-08-17T14:00:00+08:00",
        request_id: "registration-start-error-boundary",
      }, 201)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setRegistrationSessionBoundaryHandler(boundaryHandler)

  const oldRestore = restoreRegistrationCsrf()
  await startRegistration({
    primary_purpose: "student",
    identifier_kind: "phone",
    identifier: "13800000202",
  })
  const newRestore = restoreRegistrationCsrf()
  oldCsrfResponse.resolve(jsonResponse({
    type: "about:blank",
    title: "Unauthorized",
    status: 401,
    detail: "注册会话已失效",
    code: "registration_session_invalid",
    request_id: "stale-registration-invalid",
  }, 401))
  await expect(oldRestore).rejects.toMatchObject({
    problem: { code: "registration_session_invalid" },
  })
  const sharedNewRestore = restoreRegistrationCsrf()
  const csrfRequestCountAfterStaleError = csrfRequestCount
  newCsrfResponseGate.resolve()
  const results = await Promise.allSettled([newRestore, sharedNewRestore])

  expect(csrfRequestCountAfterStaleError).toBe(2)
  expect(boundaryHandler).toHaveBeenCalledTimes(1)
  expect(results).toEqual([
    { status: "fulfilled", value: "new-registration-csrf" },
    { status: "fulfilled", value: "new-registration-csrf" },
  ])
})

it("does not let an old restore finally clear the new generation in-flight restore", async () => {
  const oldCsrfResponse = deferred<Response>()
  const newCsrfResponse = deferred<Response>()
  let csrfRequestCount = 0
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    if (url.endsWith("/csrf")) {
      csrfRequestCount += 1
      return csrfRequestCount === 1
        ? oldCsrfResponse.promise
        : newCsrfResponse.promise
    }
    if (url.endsWith("/verify")) {
      return Promise.resolve(jsonResponse({
        ...stateResponse,
        csrf_token: "verified-registration-csrf",
      }))
    }
    if (url.endsWith("/probe")) return Promise.resolve(jsonResponse({ ok: true }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  const oldRestore = restoreRegistrationCsrf()
  await verifyRegistration("application-1", {
    challenge_id: "challenge-1",
    code: "123456",
  })
  const newRestore = restoreRegistrationCsrf()

  oldCsrfResponse.resolve(jsonResponse({ csrf_token: "old-secret-registration-csrf" }))
  await expect(oldRestore).rejects.toMatchObject({
    name: "RegistrationSessionStaleError",
  })
  const sharedNewRestore = restoreRegistrationCsrf()
  expect(csrfRequestCount).toBe(2)
  newCsrfResponse.resolve(jsonResponse({ csrf_token: "new-registration-csrf" }))
  await expect(Promise.all([newRestore, sharedNewRestore])).resolves.toEqual([
    "new-registration-csrf",
    "new-registration-csrf",
  ])

  await registrationFetch("/probe", { method: "POST" }, (value) => value)
  const probeCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/probe"))
  expect(new Headers(probeCall?.[1]?.headers).get(
    "X-Registration-CSRF-Token",
  )).toBe("new-registration-csrf")
})

it("rejects a stale successful verification without changing the newer registration session", async () => {
  const oldVerifyResponse = deferred<Response>()
  const newCsrfResponse = deferred<Response>()
  const boundaryHandler = vi.fn()
  let csrfRequestCount = 0
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    if (url.endsWith("/applications/application-old/verify")) {
      return oldVerifyResponse.promise
    }
    if (url.endsWith("/applications")) {
      return Promise.resolve(jsonResponse({
        application_id: "10000000-0000-4000-8000-000000000203",
        state: "draft",
        masked_identifier: "138****0203",
        expires_at: "2026-08-17T14:00:00+08:00",
        request_id: "registration-new-session-after-old-verify",
      }, 201))
    }
    if (url.endsWith("/csrf")) {
      csrfRequestCount += 1
      return newCsrfResponse.promise
    }
    if (url.endsWith("/probe")) return Promise.resolve(jsonResponse({ ok: true }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setRegistrationSessionBoundaryHandler(boundaryHandler)

  const oldVerification = verifyRegistration("application-old", {
    challenge_id: "challenge-old",
    code: "123456",
  })
  await startRegistration({
    primary_purpose: "student",
    identifier_kind: "phone",
    identifier: "13800000203",
  })
  setRegistrationCsrfToken("current-registration-csrf")
  const currentRestore = restoreRegistrationCsrf()

  oldVerifyResponse.resolve(jsonResponse({
    ...stateResponse,
    csrf_token: "old-verify-registration-csrf",
  }))
  const staleError = await oldVerification.then(
    () => null,
    (error: unknown) => error,
  )

  expect(staleError).toMatchObject({ name: "RegistrationSessionStaleError" })
  expect(String(staleError)).toBe(
    "RegistrationSessionStaleError: Registration session changed",
  )
  expect(String(staleError)).not.toContain("old-verify-registration-csrf")
  expect(String(staleError)).not.toContain("current-registration-csrf")
  expect(boundaryHandler).toHaveBeenCalledTimes(1)

  await registrationFetch("/probe", { method: "POST" }, (value) => value)
  const probeCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/probe"))
  expect(new Headers(probeCall?.[1]?.headers).get(
    "X-Registration-CSRF-Token",
  )).toBe("current-registration-csrf")

  const sharedRestore = restoreRegistrationCsrf()
  expect(sharedRestore).toBe(currentRestore)
  expect(csrfRequestCount).toBe(1)
  newCsrfResponse.resolve(jsonResponse({ csrf_token: "new-registration-csrf" }))
  await expect(Promise.all([currentRestore, sharedRestore])).resolves.toEqual([
    "new-registration-csrf",
    "new-registration-csrf",
  ])
})

it("rejects a stale successful finalization without resetting the newer registration session", async () => {
  const oldFinalizeResponse = deferred<Response>()
  const newCsrfResponse = deferred<Response>()
  const boundaryHandler = vi.fn()
  let csrfRequestCount = 0
  const fetchMock = vi.fn<typeof fetch>((input) => {
    const url = String(input)
    if (url.endsWith("/me/finalize")) return oldFinalizeResponse.promise
    if (url.endsWith("/applications")) {
      return Promise.resolve(jsonResponse({
        application_id: "10000000-0000-4000-8000-000000000204",
        state: "draft",
        masked_identifier: "138****0204",
        expires_at: "2026-08-17T14:00:00+08:00",
        request_id: "registration-new-session-after-old-finalize",
      }, 201))
    }
    if (url.endsWith("/csrf")) {
      csrfRequestCount += 1
      return newCsrfResponse.promise
    }
    if (url.endsWith("/probe")) return Promise.resolve(jsonResponse({ ok: true }))
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setRegistrationSessionBoundaryHandler(boundaryHandler)
  setRegistrationCsrfToken("old-finalize-registration-csrf")

  const oldFinalization = finalizeRegistration()
  await startRegistration({
    primary_purpose: "student",
    identifier_kind: "phone",
    identifier: "13800000204",
  })
  setRegistrationCsrfToken("current-registration-csrf")
  const currentRestore = restoreRegistrationCsrf()

  oldFinalizeResponse.resolve(jsonResponse({
    state: "finalized",
    next_action: "login",
    request_id: "old-finalization-response",
  }))
  const staleError = await oldFinalization.then(
    () => null,
    (error: unknown) => error,
  )

  expect(staleError).toMatchObject({ name: "RegistrationSessionStaleError" })
  expect(String(staleError)).toBe(
    "RegistrationSessionStaleError: Registration session changed",
  )
  expect(String(staleError)).not.toContain("old-finalize-registration-csrf")
  expect(String(staleError)).not.toContain("current-registration-csrf")
  expect(boundaryHandler).toHaveBeenCalledTimes(1)

  await registrationFetch("/probe", { method: "POST" }, (value) => value)
  const probeCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/probe"))
  expect(new Headers(probeCall?.[1]?.headers).get(
    "X-Registration-CSRF-Token",
  )).toBe("current-registration-csrf")

  const sharedRestore = restoreRegistrationCsrf()
  expect(sharedRestore).toBe(currentRestore)
  expect(csrfRequestCount).toBe(1)
  newCsrfResponse.resolve(jsonResponse({ csrf_token: "new-registration-csrf" }))
  await expect(Promise.all([currentRestore, sharedRestore])).resolves.toEqual([
    "new-registration-csrf",
    "new-registration-csrf",
  ])
})

it("keeps the registration session boundary when invitation context is unavailable", async () => {
  const boundaryHandler = vi.fn()
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/guardian-invitations/context")) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "监护人邀请不可用",
          code: "registration_action_unavailable",
          request_id: "guardian-context-unavailable",
        },
        404,
      )
    }
    if (url.endsWith("/probe")) return jsonResponse({ ok: true })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("formal-csrf")
  setRegistrationCsrfToken("registration-csrf")
  setRegistrationSessionBoundaryHandler(boundaryHandler)

  await expect(getGuardianInvitationContext()).rejects.toMatchObject({
    name: "ProblemDetailsError",
    problem: {
      status: 404,
      code: "registration_action_unavailable",
    },
  })
  await registrationFetch("/probe", { method: "POST" }, (value) => value)

  const contextInit = fetchMock.mock.calls[0]?.[1]
  const contextHeaders = new Headers(contextInit?.headers)
  const probeHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
  expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
    "/api/v1/registration/guardian-invitations/context",
  )
  expect(contextInit?.method ?? "GET").toBe("GET")
  expect(contextInit?.credentials).toBe("include")
  expect(contextHeaders.get("X-CSRF-Token")).toBeNull()
  expect(contextHeaders.get("X-Registration-CSRF-Token")).toBeNull()
  expect(boundaryHandler).not.toHaveBeenCalled()
  expect(probeHeaders.get("X-Registration-CSRF-Token")).toBe(
    "registration-csrf",
  )
})

it("rejects extra sensitive fields in a registration response", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () =>
      jsonResponse({
        ...stateResponse,
        identity_number: "110105199001021234",
      }),
    ),
  )

  await expect(getRegistrationMe()).rejects.toThrow(
    "Invalid registration response",
  )
})

it("keeps registration csrf separate and attaches it only to unsafe registration requests", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/applications/application-1/verify")) {
      return jsonResponse({
        ...stateResponse,
        csrf_token: "registration-csrf",
        primary_purpose: "student",
      })
    }
    if (url.endsWith("/me")) {
      return jsonResponse({ ...stateResponse, primary_purpose: "student" })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("formal-csrf")

  await verifyRegistration("application-1", {
    challenge_id: "challenge-1",
    code: "123456",
  })
  await updateRegistrationProfile({
    password: "Valid-password-2026",
    full_name: "注册学员",
    identity_number: "110105199001021234",
    sex: "male",
    date_of_birth: "1990-01-02",
    consent_version: "registration-v1",
    purpose_details: {
      requested_staff_type: null,
      staff_application_reason: null,
      visit_reason: null,
    },
  })

  const verifyHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
  const profileHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
  expect(verifyHeaders.get("X-Registration-CSRF-Token")).toBeNull()
  expect(verifyHeaders.get("X-CSRF-Token")).toBeNull()
  expect(profileHeaders.get("X-Registration-CSRF-Token")).toBe(
    "registration-csrf",
  )
  expect(profileHeaders.get("X-CSRF-Token")).toBeNull()
  expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("POST")
  expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toMatchObject({
    full_name: "注册学员",
    purpose_details: {
      requested_staff_type: null,
      staff_application_reason: null,
      visit_reason: null,
    },
  })
})

it("clears registration csrf after finalization and an invalid registration session", async () => {
  const requestHeaders: Headers[] = []
  const fetchMock = vi.fn<typeof fetch>(async (_input, init) => {
    requestHeaders.push(new Headers(init?.headers))
    if (requestHeaders.length === 1) {
      return jsonResponse({
        state: "finalized",
        next_action: "login",
        request_id: "registration-finalized",
      })
    }
    if (requestHeaders.length === 2) {
      return jsonResponse({ ok: true })
    }
    if (requestHeaders.length === 3) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "注册会话已失效",
          code: "registration_session_invalid",
          request_id: "registration-invalid",
        },
        401,
      )
    }
    return jsonResponse({ ok: true })
  })
  vi.stubGlobal("fetch", fetchMock)

  setRegistrationCsrfToken("first-registration-csrf")
  await finalizeRegistration()
  await registrationFetch("/probe", { method: "POST" }, (value) => value)
  setRegistrationCsrfToken("second-registration-csrf")
  await expect(getRegistrationMe()).rejects.toMatchObject({
    problem: { code: "registration_session_invalid" },
  })
  await registrationFetch("/probe", { method: "POST" }, (value) => value)

  expect(requestHeaders[0]?.get("X-Registration-CSRF-Token")).toBe(
    "first-registration-csrf",
  )
  expect(requestHeaders[1]?.get("X-Registration-CSRF-Token")).toBeNull()
  expect(requestHeaders[2]?.get("X-Registration-CSRF-Token")).toBeNull()
  expect(requestHeaders[3]?.get("X-Registration-CSRF-Token")).toBeNull()
})

it("strictly parses password reset responses without exposing account existence", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/password-reset/challenges")) {
      return jsonResponse({
        challenge_id: "password-challenge-1",
        expires_at: "2026-08-16T10:10:00+08:00",
        retry_after_seconds: 60,
        masked_destination: "s***@example.com",
        request_id: "password-reset-challenge",
      }, 201)
    }
    if (url.endsWith("/password-reset/complete")) {
      return jsonResponse({
        status: "completed",
        request_id: "password-reset-complete",
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  await expect(issuePasswordResetChallenge("student@example.com")).resolves.toMatchObject({
    challenge_id: "password-challenge-1",
    masked_destination: "s***@example.com",
  })
  await expect(completePasswordReset({
    challenge_id: "password-challenge-1",
    code: "123456",
    new_password: "Recovered-password-2026",
  })).resolves.toEqual({
    status: "completed",
    request_id: "password-reset-complete",
  })

  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    "/api/v1/auth/password-reset/challenges",
    "/api/v1/auth/password-reset/complete",
  ])
})

it("rejects unknown fields in password reset and account identifier responses", async () => {
  const responses = [
    {
      status: "completed",
      request_id: "password-reset-extra",
      account_exists: true,
    },
    {
      items: [{
        kind: "email",
        masked_identifier: "s***@example.com",
        verified_at: "2026-08-16T10:00:00+08:00",
        identifier: "student@example.com",
      }],
      request_id: "identifier-extra",
    },
  ]
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => jsonResponse(responses.shift())),
  )

  await expect(completePasswordReset({
    challenge_id: "password-challenge-1",
    code: "123456",
    new_password: "Recovered-password-2026",
  })).rejects.toThrow("Invalid registration response")
  await expect(listAccountIdentifiers()).rejects.toThrow(
    "Invalid registration response",
  )
})

it("uses formal csrf for account identifier verification and never registration csrf", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/identifiers/challenges")) {
      return jsonResponse({
        challenge_id: "identifier-challenge-1",
        expires_at: "2026-08-16T10:10:00+08:00",
        retry_after_seconds: 60,
        masked_destination: "138****7777",
        request_id: "identifier-challenge",
      }, 201)
    }
    return jsonResponse({
      items: [{
        kind: "phone",
        masked_identifier: "138****7777",
        verified_at: "2026-08-16T10:05:00+08:00",
      }],
      request_id: "identifier-verified",
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("formal-csrf")
  setRegistrationCsrfToken("registration-csrf")

  await issueAccountIdentifierChallenge({
    identifier_kind: "phone",
    identifier: "13800007777",
    current_password: "Current-password-2026",
  })
  await verifyAccountIdentifier({
    challenge_id: "identifier-challenge-1",
    code: "123456",
    identifier_kind: "phone",
    identifier: "13800007777",
    current_password: "Current-password-2026",
  })

  for (const [, init] of fetchMock.mock.calls) {
    const headers = new Headers(init?.headers)
    expect(headers.get("X-CSRF-Token")).toBe("formal-csrf")
    expect(headers.get("X-Registration-CSRF-Token")).toBeNull()
  }
  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
    identifier_kind: "phone",
    identifier: "13800007777",
    current_password: "Current-password-2026",
  })
})

it("keeps guardian registration and formal acceptance csrf domains separate", async () => {
  const invitation = {
    id: "10000000-0000-4000-8000-000000000181",
    masked_identifier: "138****8888",
    relationship: "母亲",
    is_primary: true,
    status: "pending",
    expires_at: "2026-08-23T10:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/accept")) {
      return jsonResponse({
        status: "accepted",
        student_finalized: true,
        request_id: "guardian-accepted",
      })
    }
    return jsonResponse(invitation, 201)
  })
  vi.stubGlobal("fetch", fetchMock)
  setRegistrationCsrfToken("registration-csrf")
  setCsrfToken("formal-csrf")

  await createMinorGuardianInvitation({
    identifier_kind: "phone",
    identifier: "13800008888",
    relationship: "母亲",
    is_primary: true,
  })
  await acceptHandedOffGuardianInvitation("2026-08")

  const registrationHeaders = new Headers(fetchMock.mock.calls[0]?.[1]?.headers)
  const formalHeaders = new Headers(fetchMock.mock.calls[1]?.[1]?.headers)
  expect(registrationHeaders.get("X-Registration-CSRF-Token")).toBe("registration-csrf")
  expect(registrationHeaders.get("X-CSRF-Token")).toBeNull()
  expect(formalHeaders.get("X-CSRF-Token")).toBe("formal-csrf")
  expect(formalHeaders.get("X-Registration-CSRF-Token")).toBeNull()
})

it("rejects guardian invitation responses containing a full identifier", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => jsonResponse([{
      id: "10000000-0000-4000-8000-000000000181",
      masked_identifier: "138****8888",
      relationship: "母亲",
      is_primary: true,
      status: "pending",
      expires_at: "2026-08-23T10:00:00+08:00",
      identifier: "13800008888",
    }])),
  )

  await expect(listMinorGuardianInvitations()).rejects.toThrow(
    "Invalid registration response",
  )
})

it("strictly parses masked registration administration and optional sensitive detail", async () => {
  const listItem = {
    application_id: "10000000-0000-4000-8000-000000000191",
    primary_purpose: "student",
    state: "conflict",
    masked_identifier: "138****7791",
    conflict_code: "multiple_person_match",
    candidate_count: 1,
    expires_at: "2026-09-16T10:00:00+08:00",
    created_at: "2026-08-16T10:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    return jsonResponse(url.endsWith("/applications") ? [listItem] : {
      ...listItem,
      full_name: "冲突学员",
      identity_number: "110105199001021238",
      sex: "male",
      date_of_birth: "1990-01-02",
      verified_identifier: "13800007791",
      candidates: [{
        person_id: "20000000-0000-4000-8000-000000000191",
        full_name: "候选学员",
        identity_number: "110105199001021238",
        sex: "male",
        date_of_birth: "1990-01-02",
        deleted: false,
      }],
    })
  })
  vi.stubGlobal("fetch", fetchMock)

  await expect(listRegistrationConflicts()).resolves.toEqual([listItem])
  await expect(getRegistrationConflict(listItem.application_id)).resolves.toMatchObject({
    full_name: "冲突学员",
    candidates: [{ full_name: "候选学员" }],
  })
})

it("accepts pending guardian as an admin conflict resolution outcome", async () => {
  const result = {
    state: "pending_guardian",
    next_action: "complete_guardian",
    request_id: "registration-review-minor-guardian-gate",
  }
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => jsonResponse(result)),
  )
  setCsrfToken("formal-csrf")

  await expect(resolveRegistrationConflict(
    "10000000-0000-4000-8000-000000000191",
    {
      candidate_person_id: "20000000-0000-4000-8000-000000000191",
      reason: "实名字段复核一致，继续等待监护确认",
    },
  )).resolves.toEqual(result)
})

it("rejects an invalid pending guardian admin next action", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>(async () => jsonResponse({
      state: "pending_guardian",
      next_action: "login",
      request_id: "registration-review-invalid-pair",
    })),
  )
  setCsrfToken("formal-csrf")

  await expect(resolveRegistrationConflict(
    "10000000-0000-4000-8000-000000000191",
    {
      candidate_person_id: "20000000-0000-4000-8000-000000000191",
      reason: "实名字段复核一致，继续等待监护确认",
    },
  )).rejects.toThrow("Invalid registration response")
})

it("uses strict staff application contracts and formal csrf for review", async () => {
  const item = {
    application_id: "10000000-0000-4000-8000-000000000192",
    requested_staff_type: "coach",
    status: "pending",
    created_at: "2026-08-16T10:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/approve")) {
      return jsonResponse({
        application_id: item.application_id,
        status: "approved",
        requested_staff_type: "coach",
        assigned_role: "coach",
        request_id: "staff-approved",
      })
    }
    if (url.endsWith(`/staff-applications/${item.application_id}`)) {
      return jsonResponse({ ...item, application_reason: "青训教学申请" })
    }
    return jsonResponse([item])
  })
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("formal-csrf")

  await expect(listStaffApplications()).resolves.toEqual([item])
  await expect(getStaffApplication(item.application_id)).resolves.toMatchObject({
    application_reason: "青训教学申请",
  })
  await reviewStaffApplication(item.application_id, "approve", "审核通过")

  const reviewHeaders = new Headers(fetchMock.mock.calls[2]?.[1]?.headers)
  expect(reviewHeaders.get("X-CSRF-Token")).toBe("formal-csrf")
})
