import { afterEach, expect, it, vi } from "vitest"

import * as api from "./api"

afterEach(() => vi.unstubAllGlobals())

it("sends bounded error filters to the request-errors endpoint", async () => {
  const response = { items: [], total: 0, page: 2, page_size: 20, retention_days: 30 }
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify(response), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  }))
  vi.stubGlobal("fetch", fetch)

  const result = await api.getOperationsRequestErrors({
    days: 7,
    page: 2,
    accountId: "11111111-1111-4111-8111-111111111111",
    sourceIp: "2001:db8::2",
    statusCode: 503,
  })

  expect(result).toEqual(response)
  const url = new URL(fetch.mock.calls[0][0] as string, "https://erp.example")
  expect(url.pathname).toBe("/api/v1/operations/request-errors")
  expect(Object.fromEntries(url.searchParams)).toEqual({
    days: "7",
    page: "2",
    page_size: "20",
    account_id: "11111111-1111-4111-8111-111111111111",
    source_ip: "2001:db8::2",
    status_code: "503",
  })
})
