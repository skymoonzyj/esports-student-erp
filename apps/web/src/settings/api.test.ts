import { afterEach, expect, it, vi } from "vitest"

import { listAccounts } from "./api"

afterEach(() => vi.unstubAllGlobals())

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
