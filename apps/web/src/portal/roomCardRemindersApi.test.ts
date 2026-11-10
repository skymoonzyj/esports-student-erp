import { afterEach, expect, it, vi } from "vitest"
import { getRoomCardReminders } from "./roomCardRemindersApi"

afterEach(() => vi.unstubAllGlobals())
it("requests the selected authorized page and refuses malformed or mismatched responses", async () => {
  const payload = { available: true, observed_at: "2026-11-05T12:00:00Z", business_date: "2026-11-05", next_refresh_at: "2026-11-05T16:00:00Z", items: [], total: 0, page: 2, page_size: 20 }
  const fetcher = vi.fn<typeof fetch>(async () => Response.json(payload)); vi.stubGlobal("fetch", fetcher)
  expect((await getRoomCardReminders(2)).page).toBe(2)
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/portal/room-card-reminders?page=2&page_size=20")
  await expect(getRoomCardReminders(1)).rejects.toThrow("响应格式无效")
})
it("does not retain upstream sensitive bodies in cached errors", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => Response.json({ detail: "secret-personal-data" }, { status: 403 })))
  const request = getRoomCardReminders()
  await expect(request).rejects.toThrow("房卡提醒暂时无法加载，请重试。")
  await expect(request).rejects.toMatchObject({ problem: { status: 403, detail: "房卡提醒暂时无法加载，请重试。" } })
  await expect(request).rejects.not.toHaveProperty("cause")
})
