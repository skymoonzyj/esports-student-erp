import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { getRoomCards, saveRoomCard } from "./roomCardsApi"

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it("uses the shared authenticated client with a versioned date-pair payload", async () => {
  setCsrfToken("test-token")
  const calls: Array<{ url: string; init: RequestInit | undefined }> = []
  const item = { stay_id: "stay", person_id: "person", person_name: "住客", nickname: null, issued_on: "2026-11-01", expires_on: "2026-11-08", version: 2, can_edit: true }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    calls.push({ url: String(url), init })
    return Response.json(init?.method === "PUT" ? item : { items: [item] })
  }))
  expect((await getRoomCards("room")).items[0].person_name).toBe("住客")
  expect(await saveRoomCard("stay", { issued_on: "2026-11-01", expires_on: "2026-11-08", expected_version: 1 })).toEqual(item)
  expect(calls.map((call) => call.url)).toEqual(["/api/v1/housing/rooms/room/room-cards", "/api/v1/housing/stays/stay/room-card"])
  expect(JSON.parse(String(calls[1].init?.body))).toEqual({ issued_on: "2026-11-01", expires_on: "2026-11-08", expected_version: 1 })
  expect(calls[1].init?.credentials).toBe("include")
  expect(new Headers(calls[1].init?.headers).get("X-CSRF-Token")).toBe("test-token")
})
