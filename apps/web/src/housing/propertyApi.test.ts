import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { exportHousingProperty, getHousingPropertyCapabilities, listHousingPropertyChanges, submitHousingPropertyChange } from "./propertyApi"

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it.each(["submitted", "review"] as const)("serializes the explicit %s list view", async (view) => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ items: [], total: 0, page: 2, page_size: 20 })))
  vi.stubGlobal("fetch", fetcher)
  await listHousingPropertyChanges({ status: "approved", page: 2, page_size: 20, room_id: "room/1", view })
  const query = new URL(String(fetcher.mock.calls[0][0]), "https://test.invalid").searchParams
  expect(query.get("view")).toBe(view)
  expect(query.get("room_id")).toBe("room/1")
  expect(query.get("page")).toBe("2")
})

it("sends only requested property fields with reason and expected room version", async () => {
  setCsrfToken("fixture")
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "request" }), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetcher)
  await submitHousingPropertyChange("room/1", { expected_version: 7, changes: { notes: "维修说明" }, reason: "现场检查" })
  expect(fetcher).toHaveBeenCalledWith("/api/v1/housing/rooms/room%2F1/change-requests", expect.objectContaining({ method: "POST", credentials: "include", body: JSON.stringify({ expected_version: 7, changes: { notes: "维修说明" }, reason: "现场检查" }) }))
  expect(new Headers(fetcher.mock.calls[0][1].headers).get("X-CSRF-Token")).toBe("fixture")
})

it("downloads basic export bytes with a safe format-matching filename", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("safe,csv", { headers: { "Content-Disposition": 'attachment; filename="../../secret.exe"' } }))
  vi.stubGlobal("fetch", fetcher)
  const result = await exportHousingProperty({ kind: "problems", format: "csv", purpose: "维修盘点", building_ids: ["building-1"] })
  expect(await result.blob.text()).toBe("safe,csv")
  expect(result.filename).toBe("housing-problems.csv")
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/housing/property/exports")
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ kind: "problems", format: "csv", purpose: "维修盘点", building_ids: ["building-1"] })
})

it("checks proposal capability for the specific room instead of relying on any-room access", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ can_submit: false, can_review: false, can_export: false, show_basic_export: false })))
  vi.stubGlobal("fetch", fetcher)
  await getHousingPropertyCapabilities(undefined, "room/1")
  expect(fetcher.mock.calls[0][0]).toBe("/api/v1/housing/property/capabilities?room_id=room%2F1")
})
