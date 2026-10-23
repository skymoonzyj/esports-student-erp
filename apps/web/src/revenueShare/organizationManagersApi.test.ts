import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { grantOrganizationManager, listOrganizationManagerCandidates, listOrganizationManagers, revokeOrganizationManager } from "./organizationManagersApi"

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it("uses private abortable GETs and encoded account/organization paths with CSRF writes", async () => {
  const fetcher = vi.fn<typeof fetch>(async (_input, init) => new Response(init?.method === "DELETE" ? null : JSON.stringify([]), { status: init?.method === "DELETE" ? 204 : 200 }))
  vi.stubGlobal("fetch", fetcher)
  setCsrfToken("manager-csrf")
  const signal = new AbortController().signal
  await listOrganizationManagers("org/a", signal)
  await listOrganizationManagerCandidates({ q: "同 名", page: 2, page_size: 20 }, signal)
  await grantOrganizationManager("org/a", "account/b")
  await revokeOrganizationManager("org/a", "account/b")
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/revenue-share/organizations/org%2Fa/managers")
  expect(fetcher.mock.calls[0][1]).toMatchObject({ cache: "no-store", signal })
  expect(fetcher.mock.calls[1][0]).toBe("/api/v1/revenue-share/organization-manager-candidates?q=%E5%90%8C+%E5%90%8D&page=2&page_size=20")
  expect(fetcher.mock.calls[1][1]).toMatchObject({ cache: "no-store", signal })
  for (const [index, method] of [[2, "PUT"], [3, "DELETE"]] as const) {
    expect(fetcher.mock.calls[index][0]).toBe("/api/v1/revenue-share/organizations/org%2Fa/managers/account%2Fb")
    expect(fetcher.mock.calls[index][1]?.method).toBe(method)
    expect(new Headers(fetcher.mock.calls[index][1]?.headers).get("X-CSRF-Token")).toBe("manager-csrf")
  }
})
