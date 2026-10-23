import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { claimAdminApi } from "./adminApi"
afterEach(() => { setCsrfToken(null); vi.unstubAllGlobals() })
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
