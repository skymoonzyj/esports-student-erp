import { afterEach, expect, it, vi } from "vitest"

import { ProblemDetailsError, setCsrfToken } from "../api/client"
import {
  completeProfileActivation,
  startProfileActivation,
  verifyProfileActivation,
} from "./activationApi"

afterEach(() => {
  setCsrfToken(null)
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("posts only the activation payloads through the credentialed CSRF transport", async () => {
  const responses = [
    { accepted: true, resend_available_at: "2026-10-18T02:01:00Z", expires_at: "2026-10-18T02:10:00Z" },
    { masked_name: "李*", kind: "both" },
    { completed: true },
  ]
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(responses.shift())))
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("existing-csrf")

  await expect(startProfileActivation("activation@example.test")).resolves.toEqual({
    accepted: true, resend_available_at: "2026-10-18T02:01:00Z", expires_at: "2026-10-18T02:10:00Z",
  })
  await expect(verifyProfileActivation("123456")).resolves.toEqual({ masked_name: "李*", kind: "both" })
  await expect(completeProfileActivation("123456", "Valid123!")).resolves.toEqual({ completed: true })

  expect(fetchMock.mock.calls.map(([url, init]) => ({
    url,
    method: init?.method,
    credentials: init?.credentials,
    body: JSON.parse(String(init?.body)),
    csrf: new Headers(init?.headers).get("X-CSRF-Token"),
    contentType: new Headers(init?.headers).get("Content-Type"),
    authorization: new Headers(init?.headers).get("Authorization"),
  }))).toEqual([
    { url: "/api/v1/auth/profile-activation/start", method: "POST", credentials: "include", body: { email: "activation@example.test" }, csrf: "existing-csrf", contentType: "application/json", authorization: null },
    { url: "/api/v1/auth/profile-activation/verify", method: "POST", credentials: "include", body: { code: "123456" }, csrf: "existing-csrf", contentType: "application/json", authorization: null },
    { url: "/api/v1/auth/profile-activation/complete", method: "POST", credentials: "include", body: { code: "123456", password: "Valid123!" }, csrf: "existing-csrf", contentType: "application/json", authorization: null },
  ])
})

it.each([429, 503])("preserves Retry-After for recoverable HTTP %s", async (status) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    type: "about:blank", title: "Unavailable", status, detail: "暂时不可用", code: "activation_unavailable", request_id: "activation-error",
  }), { status, headers: { "Retry-After": "37" } })))
  try {
    await startProfileActivation("activation@example.test")
    expect.fail("Expected request failure")
  } catch (error) {
    expect(error).toBeInstanceOf(ProblemDetailsError)
    expect((error as ProblemDetailsError).retryAfterSeconds).toBe(37)
    expect((error as ProblemDetailsError).problem.status).toBe(status)
  }
})

it("accepts FastAPI offset timestamps without interpreting accepted as proof of delivery", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    accepted: false,
    resend_available_at: "2026-10-18T02:01:00.123456+00:00",
    expires_at: "2026-10-18T02:10:00+00:00",
  }))))
  await expect(startProfileActivation("activation@example.test")).resolves.toEqual({
    accepted: false,
    resend_available_at: "2026-10-18T02:01:00.123456+00:00",
    expires_at: "2026-10-18T02:10:00+00:00",
  })
})

it.each([
  { masked_name: "李*", kind: "student", token: "must-not-be-exposed" },
  { masked_name: "李*", kind: "guardian" },
  { kind: "player" },
])("rejects non-public or malformed preview responses %j", async (body) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(body))))
  await expect(verifyProfileActivation("123456")).rejects.toThrow("Invalid activation response")
})

it("accepts staff as profile kind without any account role in the preview or request", async () => {
  const fetchMock = vi.fn<typeof fetch>(async () => Response.json({ masked_name: "工*", kind: "staff" }))
  vi.stubGlobal("fetch", fetchMock)
  await expect(verifyProfileActivation("123456")).resolves.toEqual({ masked_name: "工*", kind: "staff" })
  expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({ code: "123456" })
})

it("rejects malformed resend timestamps and false completion", async () => {
  const bodies = [
    { accepted: true, resend_available_at: "invalid", expires_at: "2026-10-18T02:10:00Z" },
    { completed: false },
  ]
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify(bodies.shift()))))
  await expect(startProfileActivation("activation@example.test")).rejects.toThrow("Invalid activation response")
  await expect(completeProfileActivation("123456", "Valid123!")).rejects.toThrow("Invalid activation response")
})

it("forwards optional cancellation signals for all three credentialed requests", async () => {
  const responses = [
    { accepted: true, resend_available_at: "2026-10-18T02:01:00Z", expires_at: "2026-10-18T02:10:00Z" },
    { masked_name: "李*", kind: "both" },
    { completed: true },
  ]
  const signals = [new AbortController().signal, new AbortController().signal, new AbortController().signal]
  const fetchMock = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(responses.shift())))
  vi.stubGlobal("fetch", fetchMock)
  await startProfileActivation("activation@example.test", signals[0])
  await verifyProfileActivation("123456", signals[1])
  await completeProfileActivation("123456", "Valid123!", signals[2])
  expect(fetchMock.mock.calls.map(([, init]) => init?.signal)).toEqual(signals)
  expect(fetchMock.mock.calls.every(([, init]) => init?.credentials === "include")).toBe(true)
})
