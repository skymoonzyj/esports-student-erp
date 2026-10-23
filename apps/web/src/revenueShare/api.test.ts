import { beforeEach, expect, it, vi } from "vitest"

import { getMyRevenueShare, reconcileRevenueShare } from "./api"

const mocks = vi.hoisted(() => ({ fetch: vi.fn() }))
vi.mock("../api/client", () => ({ apiFetch: mocks.fetch, apiFetchResponse: vi.fn() }))

beforeEach(() => mocks.fetch.mockReset().mockResolvedValue({}))

it("encodes opaque ledger cursors and sends the selected page size", async () => {
  await getMyRevenueShare({ page_size: 50, cursor: "opaque+/cursor=" })
  expect(mocks.fetch).toHaveBeenCalledWith("/revenue-share/me?page_size=50&cursor=opaque%2B%2Fcursor%3D")
})

it("sends bounded reconciliation batches while retaining the legacy empty-body call", async () => {
  await reconcileRevenueShare({ batch_size: 50, cursor: "opaque-cursor" })
  expect(mocks.fetch).toHaveBeenLastCalledWith("/revenue-share/reconcile", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ batch_size: 50, cursor: "opaque-cursor" }),
  })
  await reconcileRevenueShare()
  expect(mocks.fetch).toHaveBeenLastCalledWith("/revenue-share/reconcile", { method: "POST" })
})
