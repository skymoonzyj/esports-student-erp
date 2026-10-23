import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { assistedEditorApi } from "./editorApi"

const person = "30000000-0000-4000-8000-000000000001"
const coach = "30000000-0000-4000-8000-000000000002"
const metadata = { person_id: person, kind: "both", profile_version: 4, can_edit_activation_email: true, current_coach_person_ids: [coach], eligible_coaches: [{ id: coach, label: "教练" }], allowed_actions: ["correct_profile", "replace_coaches"] }

it("requires the server email-edit capability instead of guessing from other fields", async () => {
  const { can_edit_activation_email: _capability, ...legacy } = metadata
  void _capability
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(legacy)))
  await expect(assistedEditorApi.context(person, new AbortController().signal)).rejects.toThrow()
})
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it("accepts explicit pending capability and uses the separate immutable-facts correction contract", async () => {
  setCsrfToken("test")
  const pending = { ...metadata, kind: "staff", intake_mode: "pending", allowed_actions: ["correct_pending_profile"] }
  const fetcher = vi.fn<typeof fetch>(async (_url, init) => Response.json(init?.method === "POST" ? { request_id: null, person_id: person, approval_id: coach } : pending))
  vi.stubGlobal("fetch", fetcher)
  const signal = new AbortController().signal
  expect((await assistedEditorApi.context(person, signal)).allowed_actions).toEqual(["correct_pending_profile"])
  const input = { full_name: "Passport Person", identity_number: "AB123456", document_type: "passport" as const, document_issuing_country: null,
    phone: null, email: null, sex: null, date_of_birth: null, guardians: [], expected_version: 4, reason: "真实资料核对", idempotency_key: coach }
  await assistedEditorApi.correctPending(person, input, signal)
  expect(fetcher.mock.calls[1][0]).toBe(`/api/v1/personnel-intake/people/${person}/pending-corrections`)
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(input)
})

it("reads independent readiness only through the private detail endpoint and rejects extra secrets", async () => {
  const readiness = { approval_valid: true, housing_ready: false, completion_allowed: true, activation_profile_ready: false,
    approval_id: coach, version: 4, kind: "staff", profile_missing: ["sex", "date_of_birth"], housing_missing: ["sex"], activation_missing: ["sex", "date_of_birth", "email"] }
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json(readiness)).mockResolvedValueOnce(Response.json({ ...readiness, identity_number: "secret" }))
  vi.stubGlobal("fetch", fetcher)
  const signal = new AbortController().signal
  expect(await assistedEditorApi.readiness(person, signal)).toEqual(readiness)
  expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/personnel-intake/people/${person}/readiness`)
  expect(fetcher.mock.calls[0][1]).toMatchObject({ signal, cache: "no-store" })
  await expect(assistedEditorApi.readiness(person, signal)).rejects.toThrow()
})

it("reads only private metadata with cancellation and rejects any extra decrypted fields", async () => {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify(metadata), { headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...metadata, phone: "13800000000" }), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetcher)
  const controller = new AbortController()
  expect(await assistedEditorApi.context(person, controller.signal)).toEqual(metadata)
  expect(fetcher.mock.calls[0][0]).toBe(`/api/v1/personnel-intake/people/${person}/editor-context`)
  expect(fetcher.mock.calls[0][1]).toMatchObject({ signal: controller.signal, cache: "no-store", credentials: "include" })
  await expect(assistedEditorApi.context(person, controller.signal)).rejects.toThrow()
})

it("keeps correction and coach writes distinct and never sends immutable correction affiliations", async () => {
  setCsrfToken("test")
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ request_id: null, person_id: person, approval_id: coach }), { headers: { "Content-Type": "application/json" } }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
  vi.stubGlobal("fetch", fetcher)
  const signal = new AbortController().signal
  const input = { full_name: "测试", identity_number: "110105199001021238", phone: "13800000000", email: "test@example.test", sex: "male" as const, date_of_birth: "1990-01-02", guardians: [], expected_version: 4, reason: "资料核对", idempotency_key: "40000000-0000-4000-8000-000000000001" }
  await assistedEditorApi.correct(person, input, signal)
  await assistedEditorApi.coaches(person, { coach_person_ids: [coach], expected_version: 4, reason: "职责调整" }, signal)
  expect(fetcher.mock.calls[0][1]).toMatchObject({ method: "POST", signal, cache: "no-store" })
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual(input)
  expect(fetcher.mock.calls[1][1]).toMatchObject({ method: "PUT", signal, cache: "no-store" })
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ coach_person_ids: [coach], expected_version: 4, reason: "职责调整" })
})
