import { afterEach, expect, it, vi } from "vitest"
import { listPeople } from "./api"
afterEach(() => vi.unstubAllGlobals())
it.each(["pending", "assigned"] as const)("sends independent %s allocation alongside existing filters and pagination", async (status) => {
  const urls: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async url => { urls.push(String(url)); return new Response(JSON.stringify({ items: [], total: 0, page: 2, page_size: 20 }), { headers: { "Content-Type": "application/json" } }) }))
  await listPeople({ teamAllocationStatus: status, role: "team_member", name: "队员", type: "staff", staffType: "other", page: 2 })
  const query = new URL(urls[0], "https://erp.invalid").searchParams
  expect(Object.fromEntries(query)).toEqual({ team_allocation_status: status, role: "team_member", name: "队员", type: "staff", staff_type: "other", page: "2", page_size: "20" })
})
