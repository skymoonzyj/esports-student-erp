import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { assistedEditorApi } from "./editorApi"

const person = "30000000-0000-4000-8000-000000000001"
const coach = "30000000-0000-4000-8000-000000000002"
const metadata = { person_id: person, kind: "both", profile_version: 4, current_coach_person_ids: [coach], eligible_coaches: [{ id: coach, label: "教练" }], allowed_actions: ["correct_profile", "replace_coaches"] }
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

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
