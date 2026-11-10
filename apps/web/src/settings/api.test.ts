import { afterEach, expect, it, vi } from "vitest"

import { changeAccountJobRole, listAccounts } from "./api"

afterEach(() => vi.unstubAllGlobals())

it("posts the complete atomic job change including explicit false retention", async () => {
  let request: { url: string; method: string | undefined; input: unknown } | undefined
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    request = { url: String(url), method: init?.method, input: JSON.parse(String(init?.body)) }
    return new Response(JSON.stringify({ account_id: "target", added_role_ids: ["new"], removed_role_ids: ["old"], retained_role_ids: ["student"], roles: [{ id: "new", key: "coach", name: "教练" }] }), { headers: { "Content-Type": "application/json" } })
  }))
  const result = await changeAccountJobRole("target", { from_role_id: "old", to_role_id: "new", keep_concurrent_roles: false, expected_role_ids: ["old", "student"] })
  expect(request).toEqual({ url: "/api/v1/accounts/target/job-role-change", method: "POST", input: { from_role_id: "old", to_role_id: "new", keep_concurrent_roles: false, expected_role_ids: ["old", "student"] } })
  expect(result.removed_role_ids).toEqual(["old"])
})

it("keeps the server-projected primary identifier contract", async () => {
  const account = {
    id: "10000000-0000-4000-8000-000000000092",
    person_id: "30000000-0000-4000-8000-000000000092",
    full_name: "仅邮箱账号",
    masked_primary_identifier: "m***@example.com",
    primary_identifier_kind: "email" as const,
    masked_phone: null,
    masked_email: "m***@example.com",
    verified_identifier_kinds: ["email"] as const,
    status: "active" as const,
    force_password_change: false,
    created_at: "2026-08-16T10:00:00+08:00",
  }
  const fetchMock = vi.fn<typeof fetch>(async () =>
    new Response(
      JSON.stringify({ items: [account], page: 1, page_size: 20, total: 1 }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  )
  vi.stubGlobal("fetch", fetchMock)

  const result = await listAccounts("active", 1, 20)

  expect(result.items[0]?.masked_primary_identifier).toBe("m***@example.com")
  expect(result.items[0]?.primary_identifier_kind).toBe("email")
  expect(result.items[0]?.masked_phone).toBeNull()
  expect(result.items[0]?.verified_identifier_kinds).toEqual(["email"])
  expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
    "/api/v1/accounts?page=1&page_size=20&status=active",
  )
})
