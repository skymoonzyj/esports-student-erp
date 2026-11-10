import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { claimAdminApi } from "./adminApi"
afterEach(() => { setCsrfToken(null); vi.unstubAllGlobals() })
it("parses return-permit person and guardian evidence in authenticated claim review", async () => {
  const id = "30000000-0000-4000-8000-000000000001"
  const completion = { phone: null, sex: null, date_of_birth: null, issuing_country: null, guardians: [{ full_name: "合成监护", identity_number: "M87654321", document_type: "hk_mo_permit", document_issuing_country: null,
    phone: "13800000000", email: null, relationship: "母亲", sex: "female", date_of_birth: "1980-01-02", is_primary: true }] }
  const detail = { id, version: 2, status: "pending", masked_name: "合**", masked_email: "s***@example.test", verified_at: "2026-10-30T00:00:00Z", expires_at: "2026-10-31T00:00:00Z", allowed_actions: ["approve"],
    person_id: id, applicant_masked_name: "合**", applicant_masked_identity: "H1*****78", applicant_email: "synthetic@example.test", profile_masked_name: "合**", profile_masked_identity: "H1*****78", profile_masked_email: null,
    private_match_reason: null, same_target_pending_count: 0, can_approve: true, applicant_document_type: "hk_mo_permit", applicant_issuing_country: null, applicant_completion: completion, profile_completion: completion }
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(detail)))
  expect(await claimAdminApi.detail(id, new AbortController().signal)).toEqual(detail)
})
it("uses formal authenticated transport and validates admin context without public-claim CSRF", async () => {
  setCsrfToken("formal-token")
  const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(Response.json({ can_enter: true }))
  vi.stubGlobal("fetch", fetcher)
  expect(await claimAdminApi.context(new AbortController().signal)).toEqual({ can_enter: true })
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/personnel-intake/profile-claims/context")
  expect(fetcher.mock.calls[0][1]).toMatchObject({ credentials: "include", cache: "no-store" })
})
it("fails closed on overbroad context and checks cancellation after receiving data", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ can_enter: true, full_name: "secret" })))
  await expect(claimAdminApi.context(new AbortController().signal)).rejects.toThrow()
})

it("uses bounded status and page query parameters without identity search fields", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({ items: [], total: 0, pending_count: 0, page: 2, page_size: 20 }))
  vi.stubGlobal("fetch", fetcher)
  await claimAdminApi.list({ status: "approved", page: 2, page_size: 20 }, new AbortController().signal)
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/personnel-intake/profile-claims?page=2&page_size=20&status=approved")
  expect(() => claimAdminApi.list({ page: 0, page_size: 101 }, new AbortController().signal)).toThrow()
})
