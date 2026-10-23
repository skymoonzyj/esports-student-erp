import { afterEach, describe, expect, it, vi } from "vitest"

import {
  backfillHousingStay,
  cancelHousingReservation,
  confirmHousingCheckIn,
  confirmHousingCheckout,
  completeRoomCleaning,
  exportHousingOccupancy,
  exportHousingRoomStatus,
  uploadRoomPhoto,
  deleteRoomPhoto,
  fetchRoomPhoto,
  createHousingBuilding,
  createHousingFloor,
  createHousingRoom,
  createHousingStay,
  deleteHousingBuilding,
  deleteHousingFloor,
  deleteHousingRoom,
  getHousingCatalog,
  getHousingCohortOptions,
  getHousingRoomDetail,
  getHousingTransferOptions,
  listHousingOverview,
  listHousingResidentOptions,
  reportHousingIssue,
  requestHousingCheckout,
  resolveHousingIssue,
  transferHousingStay,
  updateHousingBuilding,
  updateHousingFloor,
  updateHousingRoom,
  updateHousingStay,
  updateHousingPlannedDeparture,
  verifyHousingRoom,
  verifyHousingRooms,
  voidHousingStayHistory,
} from "./api"
import { housingKeys } from "./housingKeys"

afterEach(() => vi.unstubAllGlobals())

function successfulJson(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

describe("housing API", () => {
  it("sends repeated plural cleaning_statuses understood by the overview endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)
    await listHousingOverview({ cleaningStatuses: ["pending", "ready", "pending"], dailyCleaningEnabled: false })
    const url = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost")
    expect(url.searchParams.getAll("cleaning_statuses")).toEqual(["ready", "pending"])
    expect(url.searchParams.has("cleaning_status")).toBe(false)
    expect(url.searchParams.get("daily_cleaning_enabled")).toBe("false")
  })
  it("uses distinct snapshot and historical export endpoints with strict matching filenames", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (url) => new Response("csv", { headers: { "Content-Disposition": `attachment; filename="${String(url).includes("room-status") ? "housing-room-status" : "housing-occupancy"}-20261023.csv"` } }))
    vi.stubGlobal("fetch", fetchMock)
    await expect(exportHousingRoomStatus({ format: "csv", purpose: "快照核对", building_ids: ["a", "b", "a"], floor_ids: [] })).resolves.toEqual(expect.objectContaining({ filename: "housing-room-status-20261023.csv" }))
    await expect(exportHousingOccupancy({ format: "csv", purpose: "历史核对" })).resolves.toEqual(expect.objectContaining({ filename: "housing-occupancy-20261023.csv" }))
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/v1/housing/room-status/exports", "/api/v1/housing/exports"])
    expect(JSON.parse(fetchMock.mock.calls[0][1]!.body as string).building_ids).toEqual(["a", "b"])
    for (const filename of ["housing-occupancy-20261023.csv", "../housing-room-status-20261023.csv", "housing-room-status-20261023.xlsx", "housing-room-status-20261023.csv\"junk"]) {
      fetchMock.mockResolvedValueOnce(new Response("bad", { headers: { "Content-Disposition": `attachment; filename="${filename}"` } }))
      await expect(exportHousingRoomStatus({ format: "csv", purpose: "快照核对", building_ids: [], floor_ids: [] })).rejects.toThrow("导出文件无效")
    }
  })

  it("sends private versioned photo writes and explicit thumbnail reads", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({ photos: [] }))
    vi.stubGlobal("fetch", fetchMock)
    await uploadRoomPhoto("room", 2, new File(["image"], "a.jpg", { type: "image/jpeg" }), 4)
    await deleteRoomPhoto("room", "photo", 5)
    await fetchRoomPhoto("room", "photo", "thumbnail")
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([["/api/v1/housing/rooms/room/photos/2", "PUT"], ["/api/v1/housing/rooms/room/photos/photo", "DELETE"], ["/api/v1/housing/rooms/room/photos/photo?variant=thumbnail", undefined]])
    expect((fetchMock.mock.calls[0][1]!.body as FormData).get("version")).toBe("4")
    expect(fetchMock.mock.calls[1][1]!.body).toBe('{"version":5}')
  })
  it("preserves a false daily-cleaning predicate and normalizes cleaning status filters", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)
    await listHousingOverview({ dailyCleaningEnabled: false, cleaningStatuses: ["pending", "ready", "pending"] })
    const query = new URL(String(fetchMock.mock.calls[0][0]), "http://localhost").searchParams
    expect(query.get("daily_cleaning_enabled")).toBe("false")
    expect(query.getAll("cleaning_statuses")).toEqual(["ready", "pending"])
  })

  it("completes cleaning with the expected room version and returns the full room", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({ id: "room-1", version: 4, cleaning_status: "ready" }))
    vi.stubGlobal("fetch", fetchMock)
    await expect(completeRoomCleaning("room-1", { version: 3 })).resolves.toEqual({ id: "room-1", version: 4, cleaning_status: "ready" })
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/housing/rooms/room-1/cleaning/complete")
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: "POST", body: '{"version":3}' }))
  })
  it("keeps includeVoided detail variants isolated and explicitly requests tombstones", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)
    await getHousingRoomDetail("room-1", undefined, true)
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/housing/rooms/room-1/detail?include_voided=true")
    expect(housingKeys.detail("a", "r", "room-1")).not.toEqual(housingKeys.detail("a", "r", "room-1", true))
  })

  it("posts explicit history invalidation with a stable operation credential", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({ operation_id: "operation" }))
    vi.stubGlobal("fetch", fetchMock)
    const payload = { expected_version: 3, reason: "重复历史", confirmed: true as const, idempotency_key: "7b4da08b-1896-49b2-af1e-23ea5dc3b187" }
    await expect(voidHousingStayHistory("stay-1", payload)).resolves.toEqual({ operation_id: "operation" })
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/v1/housing/stays/stay-1/void-history", "POST", JSON.stringify(payload)],
    ])
  })
  it("patches a checked-in planned departure without reservation or checkout fields", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)
    await updateHousingPlannedDeparture("stay-1", { version: 2, duration_mode: "long_term", planned_check_out_at: null })
    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/v1/housing/stays/stay-1/planned-departure", "PATCH", '{"version":2,"duration_mode":"long_term","planned_check_out_at":null}'],
    ])
  })
  it("serializes every overview filter in stable backend order", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await listHousingOverview({
      buildingId: "building-1",
      floorId: "floor-1",
      query: "  409  ",
      roomTypes: ["single", "double"],
      verificationStatuses: ["pending_verification", "verified"],
      serviceStatuses: ["available", "maintenance"],
      currentGender: "unspecified",
      occupancy: "unoccupied",
      assignability: "assignable",
      page: 2,
      pageSize: 40,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/overview?building_id=building-1&floor_id=floor-1&query=409&room_type=single&room_type=double&verification_status=pending_verification&verification_status=verified&service_status=available&service_status=maintenance&current_gender=unspecified&occupancy=unoccupied&assignability=assignable&page=2&page_size=40",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("omits blank and empty filters", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await listHousingOverview({ query: "   ", roomTypes: [], serviceStatuses: [] })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/overview",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("deduplicates and serializes multi-select filters in enum order", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await listHousingOverview({
      roomTypes: ["functional", "single", "double", "single"],
      verificationStatuses: ["verified", "pending_verification", "verified"],
      serviceStatuses: ["disabled", "problem", "available", "disabled"],
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/overview?room_type=single&room_type=double&room_type=functional&verification_status=pending_verification&verification_status=verified&service_status=available&service_status=problem&service_status=disabled",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("reads the scoped housing catalog", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await getHousingCatalog()

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/catalog",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("uses exact private read contracts for room details and option directories", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await getHousingRoomDetail("room-1")
    await listHousingResidentOptions("room-1", "  张 三  ")
    await getHousingCohortOptions("room-1")
    await getHousingTransferOptions("stay-1")

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/v1/housing/rooms/room-1/detail", undefined, undefined],
      ["/api/v1/housing/rooms/room-1/resident-options?query=%E5%BC%A0+%E4%B8%89", undefined, undefined],
      ["/api/v1/housing/rooms/room-1/cohort-options", undefined, undefined],
      ["/api/v1/housing/stays/stay-1/transfer-options", undefined, undefined],
    ])
  })

  it("uses exact lifecycle mutation paths, methods and request bodies", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)
    const stayInput = {
      person_id: "person-1",
      stay_purpose: "camp" as const,
      duration_mode: "fixed_term" as const,
      cohort_id: "cohort-1",
      visit_reason: null,
      planned_check_in_at: "2026-08-10T08:00:00+08:00",
      planned_check_out_at: "2026-08-31T18:00:00+08:00",
      notes: "随营入住",
    }

    await createHousingStay("room-1", stayInput)
    await backfillHousingStay("room-1", stayInput)
    await updateHousingStay("stay-1", {
      version: 2,
      planned_check_in_at: "2026-08-11T08:00:00+08:00",
      planned_check_out_at: "2026-08-31T18:00:00+08:00",
      notes: null,
    })
    await confirmHousingCheckIn("stay-1", { version: 3, actual_check_in_at: "2026-08-11T08:10:00+08:00" })
    await cancelHousingReservation("stay-1", { version: 4, reason: "计划变更" })
    await transferHousingStay("stay-1", {
      version: 5,
      target_room_id: "room-2",
      transferred_at: "2026-08-12T09:00:00+08:00",
      reason: "设备调整",
    })
    await requestHousingCheckout("stay-1", { version: 6, actual_check_out_at: "2026-08-31T17:30:00+08:00" })
    await confirmHousingCheckout("stay-1", { version: 7 })
    await reportHousingIssue("room-1", { room_version: 8, description: "空调故障" })
    await resolveHousingIssue("issue-1", { room_version: 9, resolution_note: "空调已修复" })

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/v1/housing/rooms/room-1/stays", "POST", JSON.stringify(stayInput)],
      ["/api/v1/housing/rooms/room-1/stays/backfill", "POST", JSON.stringify(stayInput)],
      ["/api/v1/housing/stays/stay-1", "PATCH", JSON.stringify({ version: 2, planned_check_in_at: "2026-08-11T08:00:00+08:00", planned_check_out_at: "2026-08-31T18:00:00+08:00", notes: null })],
      ["/api/v1/housing/stays/stay-1/confirm-check-in", "POST", JSON.stringify({ version: 3, actual_check_in_at: "2026-08-11T08:10:00+08:00" })],
      ["/api/v1/housing/stays/stay-1/cancel", "POST", JSON.stringify({ version: 4, reason: "计划变更" })],
      ["/api/v1/housing/stays/stay-1/transfer", "POST", JSON.stringify({ version: 5, target_room_id: "room-2", transferred_at: "2026-08-12T09:00:00+08:00", reason: "设备调整" })],
      ["/api/v1/housing/stays/stay-1/request-checkout", "POST", JSON.stringify({ version: 6, actual_check_out_at: "2026-08-31T17:30:00+08:00" })],
      ["/api/v1/housing/stays/stay-1/confirm-checkout", "POST", JSON.stringify({ version: 7 })],
      ["/api/v1/housing/rooms/room-1/issues", "POST", JSON.stringify({ room_version: 8, description: "空调故障" })],
      ["/api/v1/housing/issues/issue-1/resolve", "POST", JSON.stringify({ room_version: 9, resolution_note: "空调已修复" })],
    ])
    for (const [, init] of fetchMock.mock.calls) {
      expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json")
    }
  })

  it("scopes every housing query tree by account and access revision without private search text", () => {
    expect(housingKeys.root("account-1")).toEqual(["housing", "account-1"])
    expect(housingKeys.catalog("account-1")).toEqual(["housing", "account-1", "catalog"])
    expect(housingKeys.scope("account-1", "revision-1")).toEqual([
      "housing", "account-1", "scope", "revision-1",
    ])
    expect(housingKeys.overviews("account-1", "revision-1")).toEqual([
      "housing", "account-1", "scope", "revision-1", "overviews",
    ])
    expect(housingKeys.overview("account-1", "revision-1", { page: 2 })).toEqual([
      "housing", "account-1", "scope", "revision-1", "overviews", { page: 2 },
    ])
    expect(housingKeys.details("account-1", "revision-1")).toEqual([
      "housing", "account-1", "scope", "revision-1", "details",
    ])
    expect(housingKeys.detail("account-1", "revision-1", "room-1")).toEqual([
      "housing", "account-1", "scope", "revision-1", "details", "room-1", false,
    ])
    expect(housingKeys.cohortOptions("account-1", "revision-1", "room-1")).toEqual([
      "housing", "account-1", "scope", "revision-1", "cohort-options", "room-1",
    ])
    expect(housingKeys.transferOptions("account-1", "revision-1", "stay-1")).toEqual([
      "housing", "account-1", "scope", "revision-1", "transfer-options", "stay-1",
    ])
  })

  it("uses the housing mutation contract for every catalog write", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => successfulJson({}))
    vi.stubGlobal("fetch", fetchMock)

    await createHousingBuilding({ code: "A", name: "一号楼", display_order: 1, notes: null })
    await updateHousingBuilding("building-1", { is_active: false })
    await deleteHousingBuilding("building-1")
    await createHousingFloor({ building_id: "building-1", number: 4, name: "四层", display_order: 4, notes: null })
    await updateHousingFloor("floor-1", { is_active: false })
    await deleteHousingFloor("floor-1")
    await createHousingRoom({ floor_id: "floor-1", room_number: "409", room_type: "single", capacity: 1, area_square_meters: "18.00", notes: null })
    await updateHousingRoom("room-1", { version: 7, room_number: "410" })
    await deleteHousingRoom("room-1")
    await verifyHousingRoom("room-1", 7)
    await verifyHousingRooms([{ room_id: "room-1", version: 7 }])

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method, init?.body])).toEqual([
      ["/api/v1/housing/buildings", "POST", JSON.stringify({ code: "A", name: "一号楼", display_order: 1, notes: null })],
      ["/api/v1/housing/buildings/building-1", "PATCH", JSON.stringify({ is_active: false })],
      ["/api/v1/housing/buildings/building-1", "DELETE", undefined],
      ["/api/v1/housing/floors", "POST", JSON.stringify({ building_id: "building-1", number: 4, name: "四层", display_order: 4, notes: null })],
      ["/api/v1/housing/floors/floor-1", "PATCH", JSON.stringify({ is_active: false })],
      ["/api/v1/housing/floors/floor-1", "DELETE", undefined],
      ["/api/v1/housing/rooms", "POST", JSON.stringify({ floor_id: "floor-1", room_number: "409", room_type: "single", capacity: 1, area_square_meters: "18.00", notes: null })],
      ["/api/v1/housing/rooms/room-1", "PATCH", JSON.stringify({ version: 7, room_number: "410" })],
      ["/api/v1/housing/rooms/room-1", "DELETE", undefined],
      ["/api/v1/housing/rooms/room-1/verify", "POST", JSON.stringify({ version: 7 })],
      ["/api/v1/housing/rooms/batch-verify", "POST", JSON.stringify({ rooms: [{ room_id: "room-1", version: 7 }] })],
    ])
    for (const [, init] of fetchMock.mock.calls.filter(([, options]) => options?.body)) {
      expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json")
    }
  })
})
