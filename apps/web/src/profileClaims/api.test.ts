import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { clearProfileClaimSession, getProfileClaimStatus, startProfileClaim, verifyProfileClaim } from "./api"

const input = { full_name: "  测试本人  ", identity_number: " 110105199001021238 ", email: " Test@Example.test " }
const started = { accepted: true, resend_available_at: "2026-10-20T01:01:00Z", expires_at: "2026-10-20T01:10:00Z" }
afterEach(() => { clearProfileClaimSession(); setCsrfToken(null); vi.unstubAllGlobals() })

it("uses independent private POST transport without formal auth CSRF or sensitive URL fields", async () => {
  setCsrfToken("formal-auth-secret")
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(started))
    .mockResolvedValueOnce(Response.json({ status: "waiting_review", reason: null }))
    .mockResolvedValueOnce(Response.json({ status: "approved", reason: null }))
  vi.stubGlobal("fetch", fetcher)
  expect(await startProfileClaim(input)).toEqual(started)
  expect(await verifyProfileClaim("123456")).toEqual({ status: "waiting_review", reason: null })
  expect(await getProfileClaimStatus()).toEqual({ status: "approved", reason: null })
  expect(fetcher.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
    ["/api/v1/auth/profile-claims/start", { full_name: "测试本人", identity_number: "110105199001021238", email: "test@example.test" }],
    ["/api/v1/auth/profile-claims/verify", { code: "123456" }],
    ["/api/v1/auth/profile-claims/status", {}],
  ])
  for (const [, init] of fetcher.mock.calls) {
    expect(init).toMatchObject({ method: "POST", credentials: "include", cache: "no-store" })
    expect(new Headers(init?.headers).has("X-CSRF-Token")).toBe(false)
  }
})

it.each([
  { ...started, accepted: false }, { ...started, session_token: "secret" }, { ...started, expires_at: "bad" },
])("rejects malformed or overbroad public start response %#", async (body) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(body)))
  await expect(startProfileClaim(input)).rejects.toThrow()
})

it.each([
  { status: "pending", reason: null }, { status: "approved", reason: null, masked_name: "原姓名" }, { status: "approved" },
])("rejects malformed or identity-leaking status %#", async (body) => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(body)))
  await expect(getProfileClaimStatus()).rejects.toThrow()
})

it("aborts old start verify and status responses across a replacement session", async () => {
  const pending: Array<(response: Response) => void> = []
  const fetcher = vi.fn<typeof fetch>(() => new Promise((resolve) => { pending.push(resolve) }))
  vi.stubGlobal("fetch", fetcher)
  const first = startProfileClaim(input)
  const firstResult = expect(first).rejects.toMatchObject({ name: "AbortError" })
  const verify = verifyProfileClaim("123456")
  const verifyResult = expect(verify).rejects.toMatchObject({ name: "AbortError" })
  const status = getProfileClaimStatus()
  const statusResult = expect(status).rejects.toMatchObject({ name: "AbortError" })
  const second = startProfileClaim({ ...input, email: "next@example.test" })
  for (const index of [0, 1, 2]) expect(fetcher.mock.calls[index][1]?.signal?.aborted).toBe(true)
  pending[0](Response.json(started)); pending[1](Response.json({ status: "approved", reason: null })); pending[2](Response.json({ status: "approved", reason: null }))
  pending[3](Response.json(started))
  await Promise.all([firstResult, verifyResult, statusResult, second])
})

it("clears only in-memory access and cancels I/O, without claiming to remove the HttpOnly cookie", async () => {
  let finish!: (response: Response) => void
  const fetcher = vi.fn<typeof fetch>(() => new Promise((resolve) => { finish = resolve }))
  vi.stubGlobal("fetch", fetcher)
  const cookie = vi.spyOn(document, "cookie", "set")
  const request = getProfileClaimStatus()
  const result = expect(request).rejects.toMatchObject({ name: "AbortError" })
  clearProfileClaimSession()
  expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true)
  finish(Response.json({ status: "approved", reason: null }))
  await result
  expect(cookie).not.toHaveBeenCalled()
  cookie.mockRestore()
})

it("validates six ASCII digits and input fields before sending requests", async () => {
  const fetcher = vi.fn()
  vi.stubGlobal("fetch", fetcher)
  await expect(verifyProfileClaim("１２３４５６")).rejects.toThrow()
  await expect(startProfileClaim({ ...input, identity_number: "invalid" })).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})

it("preserves passport spelling and sends nullable self-entered completion without authority", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(started))
  vi.stubGlobal("fetch", fetcher)
  await startProfileClaim({ ...input, document_type: "passport", identity_number: "  aBc-123  ", issuing_country: "us",
    completion: { phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [] } })
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ full_name: "测试本人", email: "test@example.test",
    document_type: "passport", identity_number: "  aBc-123  ", issuing_country: "US",
    completion: { phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [] } })
})

it.each(["person_id", "role", "kind", "staff", "coach_person_ids", "team_id", "team_allocation_status"])("rejects public authority field %s at every input level before transport", async (key) => {
  const fetcher = vi.fn()
  vi.stubGlobal("fetch", fetcher)
  await expect(startProfileClaim({ ...input, [key]: "unauthorized" })).rejects.toThrow()
  await expect(startProfileClaim({ ...input, completion: { [key]: "unauthorized" } } as never)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})

it("rejects conflicting issuers and an eleventh guardian before sending", async () => {
  const fetcher = vi.fn()
  vi.stubGlobal("fetch", fetcher)
  await expect(startProfileClaim({ ...input, document_type: "passport", identity_number: "Abc123", issuing_country: "US",
    completion: { issuing_country: "CA" } } as never)).rejects.toThrow()
  const guardian = { full_name: "监护测试", identity_number: "110105198001021234", phone: "13800000000", relationship: "父亲", sex: "male", date_of_birth: "1980-01-02" }
  await expect(startProfileClaim({ ...input, completion: { guardians: Array.from({ length: 11 }, () => guardian) } } as never)).rejects.toThrow()
  expect(fetcher).not.toHaveBeenCalled()
})

it("keeps an explicitly unknown passport issuer private and normalizes an empty optional contact to null", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(Response.json(started))
  vi.stubGlobal("fetch", fetcher)
  await startProfileClaim({ ...input, document_type: "passport", identity_number: "xY123", issuing_country: null, completion: { phone: "   " } })
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ full_name: "测试本人", email: "test@example.test",
    document_type: "passport", identity_number: "xY123", issuing_country: null,
    completion: { phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [] } })
})
