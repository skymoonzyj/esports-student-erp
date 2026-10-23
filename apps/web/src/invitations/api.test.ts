import { afterEach, expect, it, vi } from "vitest"
import { getInvitationSources, getInvitedHousing } from "./api"

afterEach(() => vi.unstubAllGlobals())
it("projects only invitation whitelist fields and fetches without storing private responses", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    as_of: "2026-10-10T01:00:00Z", page: 1, page_size: 20, total: 1,
    items: [{ person_id: "e2000000-0000-4000-8000-000000000010", full_name: "受邀甲", nickname: null,
      phone: "secret", finance: { amount: 100 }, source: { source_kind: "person", organization_id: null, name: "我邀请的", email: "secret" },
      housing: { status: "unarranged", stay: null, next_planned_stay: null, roommates: ["secret"] } }],
  }), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetcher)
  const result = await getInvitedHousing({ sourceKind: "all", page: 1, pageSize: 20 })
  expect(result.items[0]).toEqual({ person_id: "e2000000-0000-4000-8000-000000000010", full_name: "受邀甲", nickname: null,
    source: { source_kind: "person", organization_id: null, name: "我邀请的" }, housing: { status: "unarranged", stay: null, next_planned_stay: null } })
  expect(fetcher.mock.calls[0][1]?.cache).toBe("no-store")
  expect(String(fetcher.mock.calls[0][0])).toBe("/api/v1/referrals/me/housing?source_kind=all&page=1&page_size=20")
})
it("rejects malformed responses without retaining private fragments", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response('{"secret-phone":')))
  await expect(getInvitationSources()).rejects.toThrow("邀请信息响应格式无效")
})
