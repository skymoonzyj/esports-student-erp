import { afterEach, describe, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import {
  exportFoodPreferences,
  createFoodCuisine,
  getDishSuggestions,
  getFoodPreferenceCatalog,
  getFoodPreferenceProfile,
  getFoodPreferenceStatistics,
  getMyFoodPreferences,
  patchFoodCuisine,
  updateFoodPreferenceProfile,
  updateMyFoodPreferences,
  type FoodPreferenceExportInput,
  type FoodPreferenceWriteInput,
} from "./api"

const personId = "10000000-0000-4000-8000-000000000001"
const cuisineId = "20000000-0000-4000-8000-000000000001"
const revision = "a".repeat(64)
const invalidResponseMessage = "餐饮偏好响应格式无效，请刷新重试。"
const xlsxMime =
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function catalogFixture() {
  return {
    items: [
      {
        id: cuisineId,
        name: "川菜",
        sort_order: 0,
        is_active: true,
        is_other: false,
      },
    ],
    allowed_actions: ["create", "update", "disable"],
    access_revision: revision,
  }
}

function profileFixture() {
  return {
    person_id: personId,
    version: 2,
    cuisines: [
      { cuisine_option_id: cuisineId, name: "川菜", custom_name: null },
    ],
    dishes: ["鱼香肉丝"],
    updated_at: "2026-08-12T10:00:00+08:00",
    allowed_actions: ["update"],
    access_revision: revision,
  }
}

function selfFixture() {
  return {
    profile: profileFixture(),
    onboarding_required: true,
    onboarding_completed_at: null,
  }
}

function suggestionsFixture() {
  return {
    items: [{ name: "鱼香肉丝" }],
    access_revision: revision,
  }
}

function statisticsFixture() {
  return {
    population: "students",
    include_inactive: false,
    total_people: 2,
    filled_people: 1,
    unfilled_people: 1,
    filled_ratio: 0.5,
    cuisines: [{ label: "川菜", count: 1, ratio: 0.5 }],
    dishes: [{ label: "鱼香肉丝", count: 1, ratio: 0.5 }],
    people: {
      items: [
        {
          person_id: personId,
          full_name: "张三",
          person_types: ["student"],
          status: "active",
          cuisines: ["川菜"],
          dishes: ["鱼香肉丝"],
          updated_at: "2026-08-12T10:00:00+08:00",
        },
      ],
      page: 1,
      page_size: 20,
      total: 2,
    },
    access_revision: revision,
  }
}

const writeInput = {
  expected_version: 2,
  cuisines: [{ cuisine_option_id: cuisineId, custom_name: null }],
  dishes: ["鱼香肉丝"],
}

const exportInput = {
  population: "students" as const,
  include_inactive: false,
  format: "xlsx" as const,
  purpose: "餐饮偏好统计",
}

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

describe("food preference JSON API", () => {
  it("creates and patches cuisine options through the shared client", async () => {
    const created = {
      id: cuisineId,
      name: "湘菜",
      sort_order: 4,
      is_active: true,
      is_other: false,
    }
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(created, 201))
      .mockResolvedValueOnce(jsonResponse({ ...created, name: "湖南菜", is_active: false }))
    vi.stubGlobal("fetch", fetchMock)
    const controller = new AbortController()

    await expect(
      createFoodCuisine({ name: " 湘菜 ", sort_order: 4 }, controller.signal),
    ).resolves.toEqual(created)
    await expect(
      patchFoodCuisine(cuisineId, { name: "湖南菜", is_active: false }, controller.signal),
    ).resolves.toEqual({ ...created, name: "湖南菜", is_active: false })

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/food-preferences/cuisines",
      `/api/v1/food-preferences/cuisines/${cuisineId}`,
    ])
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST")
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("PATCH")
    expect(fetchMock.mock.calls[0]?.[1]?.signal).toBe(controller.signal)
    expect(fetchMock.mock.calls[1]?.[1]?.signal).toBe(controller.signal)
  })

  it("rejects malformed cuisine mutation inputs before a request", async () => {
    const fetchMock = vi.fn<typeof fetch>()
    vi.stubGlobal("fetch", fetchMock)
    await expect(createFoodCuisine({ name: "", sort_order: 0 })).rejects.toThrow(
      invalidResponseMessage,
    )
    await expect(
      patchFoodCuisine(cuisineId, { is_active: "false" } as never),
    ).rejects.toThrow(invalidResponseMessage)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("uses the exact paths, query parameters, bodies, and abort signals", async () => {
    const controller = new AbortController()
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(catalogFixture()))
      .mockResolvedValueOnce(jsonResponse(profileFixture()))
      .mockResolvedValueOnce(jsonResponse(profileFixture()))
      .mockResolvedValueOnce(jsonResponse(selfFixture()))
      .mockResolvedValueOnce(jsonResponse(selfFixture()))
      .mockResolvedValueOnce(jsonResponse(suggestionsFixture()))
      .mockResolvedValueOnce(jsonResponse(statisticsFixture()))
    vi.stubGlobal("fetch", fetchMock)

    await getFoodPreferenceCatalog(true, controller.signal)
    await getFoodPreferenceProfile("person/id?private", controller.signal)
    await updateFoodPreferenceProfile("person/id?private", writeInput, controller.signal)
    await getMyFoodPreferences(controller.signal)
    await updateMyFoodPreferences(writeInput, controller.signal)
    await getDishSuggestions("米饭 &", 5, controller.signal)
    await getFoodPreferenceStatistics(
      {
        population: "students",
        include_inactive: true,
        page: 2,
        page_size: 10,
      },
      controller.signal,
    )

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/food-preferences/cuisines?include_inactive=true",
      "/api/v1/people/person%2Fid%3Fprivate/food-preferences",
      "/api/v1/people/person%2Fid%3Fprivate/food-preferences",
      "/api/v1/me/food-preferences",
      "/api/v1/me/food-preferences",
      "/api/v1/food-preferences/dish-suggestions?query=%E7%B1%B3%E9%A5%AD+%26&limit=5",
      "/api/v1/food-preferences/statistics?population=students&include_inactive=true&page=2&page_size=10",
    ])
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.signal).toBe(controller.signal)
    }

    const [, updateInit] = fetchMock.mock.calls[2] ?? []
    expect(updateInit?.method).toBe("PUT")
    expect(new Headers(updateInit?.headers).get("Content-Type")).toBe(
      "application/json",
    )
    expect(updateInit?.body).toBe(JSON.stringify(writeInput))

    const [, selfUpdateInit] = fetchMock.mock.calls[4] ?? []
    expect(selfUpdateInit?.method).toBe("PUT")
    expect(selfUpdateInit?.body).toBe(JSON.stringify(writeInput))
    expect(fetchMock.mock.calls[3]?.[0]).not.toContain(personId)
    expect(fetchMock.mock.calls[4]?.[0]).not.toContain(personId)
  })

  it("uses false as the default catalog query value", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(catalogFixture()),
    )
    vi.stubGlobal("fetch", fetchMock)

    await getFoodPreferenceCatalog()

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/food-preferences/cuisines?include_inactive=false",
      expect.objectContaining({ signal: undefined }),
    )
  })

  it("accepts generic profile responses with more than three cuisines and dishes", async () => {
    const response = {
      ...profileFixture(),
      cuisines: [
        { cuisine_option_id: cuisineId, name: "川菜", custom_name: null },
        { cuisine_option_id: cuisineId, name: "粤菜", custom_name: null },
        { cuisine_option_id: cuisineId, name: "鲁菜", custom_name: null },
        { cuisine_option_id: cuisineId, name: "淮扬菜", custom_name: null },
      ],
      dishes: ["鱼香肉丝", "宫保鸡丁", "麻婆豆腐", "水煮鱼"],
    }
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(response)),
    )

    await expect(getFoodPreferenceProfile(personId)).resolves.toEqual(response)
  })

  it("maps invalid JSON and every invalid parsed response to one safe error", async () => {
    const badBodies: Array<[string, unknown, () => Promise<unknown>]> = [
      [
        "catalog nested unknown",
        (() => {
          const value = catalogFixture()
          return {
            ...value,
            items: [{ ...value.items[0], unexpected: "private" }],
          }
        })(),
        () => getFoodPreferenceCatalog(),
      ],
      [
        "catalog top-level unknown",
        { ...catalogFixture(), unexpected: "private" },
        () => getFoodPreferenceCatalog(),
      ],
      [
        "profile nested unknown",
        (() => {
          const value = profileFixture()
          return {
            ...value,
            cuisines: [{ ...value.cuisines[0], unexpected: "private" }],
          }
        })(),
        () => getFoodPreferenceProfile(personId),
      ],
      [
        "profile top-level unknown",
        { ...profileFixture(), unexpected: "private" },
        () => getFoodPreferenceProfile(personId),
      ],
      [
        "self nested unknown",
        (() => {
          const value = selfFixture()
          return {
            ...value,
            profile: { ...value.profile, unexpected: "private" },
          }
        })(),
        () => getMyFoodPreferences(),
      ],
      [
        "self top-level unknown",
        { ...selfFixture(), unexpected: "private" },
        () => getMyFoodPreferences(),
      ],
      [
        "suggestion nested unknown",
        (() => {
          const value = suggestionsFixture()
          return {
            ...value,
            items: [{ ...value.items[0], unexpected: "private" }],
          }
        })(),
        () => getDishSuggestions(""),
      ],
      [
        "suggestion top-level unknown",
        { ...suggestionsFixture(), unexpected: "private" },
        () => getDishSuggestions(""),
      ],
      [
        "statistics rank nested unknown",
        (() => {
          const value = statisticsFixture()
          return {
            ...value,
            cuisines: [{ ...value.cuisines[0], unexpected: "private" }],
          }
        })(),
        () =>
          getFoodPreferenceStatistics({
            population: "students",
            include_inactive: false,
            page: 1,
            page_size: 20,
          }),
      ],
      [
        "statistics person nested unknown",
        (() => {
          const value = statisticsFixture()
          return {
            ...value,
            people: {
              ...value.people,
              items: [
                { ...value.people.items[0], unexpected: "private" },
              ],
            },
          }
        })(),
        () =>
          getFoodPreferenceStatistics({
            population: "students",
            include_inactive: false,
            page: 1,
            page_size: 20,
          }),
      ],
      [
        "statistics people page unknown",
        (() => {
          const value = statisticsFixture()
          return {
            ...value,
            people: { ...value.people, unexpected: "private" },
          }
        })(),
        () =>
          getFoodPreferenceStatistics({
            population: "students",
            include_inactive: false,
            page: 1,
            page_size: 20,
          }),
      ],
      [
        "statistics top-level unknown",
        { ...statisticsFixture(), unexpected: "private" },
        () =>
          getFoodPreferenceStatistics({
            population: "students",
            include_inactive: false,
            page: 1,
            page_size: 20,
          }),
      ],
    ]

    for (const [, body, request] of badBodies) {
      vi.stubGlobal(
        "fetch",
        vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(body)),
      )
      await expect(request()).rejects.toThrow(invalidResponseMessage)
    }

    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response("malformed private body", {
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )
    let caught: unknown
    try {
      await getMyFoodPreferences()
    } catch (error) {
      caught = error
    }
    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toBe(invalidResponseMessage)
    expect((caught as Error).cause).toBeUndefined()
    expect(String(caught)).not.toContain("malformed private body")
  })

  it.each([
    ["bad UUID", () => ({ ...profileFixture(), person_id: "not-a-uuid" }), () => getFoodPreferenceProfile(personId)],
    ["bad version", () => ({ ...profileFixture(), version: -1 }), () => getFoodPreferenceProfile(personId)],
    ["fractional version", () => ({ ...profileFixture(), version: 1.5 }), () => getFoodPreferenceProfile(personId)],
    ["string version", () => ({ ...profileFixture(), version: "2" }), () => getFoodPreferenceProfile(personId)],
    ["boolean version", () => ({ ...profileFixture(), version: true }), () => getFoodPreferenceProfile(personId)],
    ["bad revision", () => ({ ...profileFixture(), access_revision: "A".repeat(64) }), () => getFoodPreferenceProfile(personId)],
    ["bad action", () => ({ ...profileFixture(), allowed_actions: ["delete"] }), () => getFoodPreferenceProfile(personId)],
    ["bad boolean", () => ({ ...selfFixture(), onboarding_required: 1 }), () => getMyFoodPreferences()],
    ["bad datetime", () => ({ ...profileFixture(), updated_at: "2026-08-12T10:00:00" }), () => getFoodPreferenceProfile(personId)],
    ["bad onboarding datetime", () => ({ ...selfFixture(), onboarding_completed_at: "2026-08-12T10:00:00" }), () => getMyFoodPreferences()],
    ["bad ratio", () => ({ ...statisticsFixture(), filled_ratio: 1.1 }), () => getFoodPreferenceStatistics({ population: "students", include_inactive: false, page: 1, page_size: 20 })],
    ["bad population enum", () => ({ ...statisticsFixture(), population: "staff" }), () => getFoodPreferenceStatistics({ population: "students", include_inactive: false, page: 1, page_size: 20 })],
    ["bad person type enum", () => { const value = statisticsFixture(); return { ...value, people: { ...value.people, items: [{ ...value.people.items[0], person_types: ["admin"] }] } } }, () => getFoodPreferenceStatistics({ population: "students", include_inactive: false, page: 1, page_size: 20 })],
    ["bad status enum", () => { const value = statisticsFixture(); return { ...value, people: { ...value.people, items: [{ ...value.people.items[0], status: "blocked" }] } } }, () => getFoodPreferenceStatistics({ population: "students", include_inactive: false, page: 1, page_size: 20 })],
    ["bad page size", () => { const value = statisticsFixture(); return { ...value, people: { ...value.people, page_size: 0 } } }, () => getFoodPreferenceStatistics({ population: "students", include_inactive: false, page: 1, page_size: 20 })],
    ["non-integer count", () => ({ ...statisticsFixture(), total_people: 1.5 }), () => getFoodPreferenceStatistics({ population: "students", include_inactive: false, page: 1, page_size: 20 })],
  ])("rejects %s without exposing parser details", async (_name, makeBody, request) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(makeBody())),
    )

    await expect(request()).rejects.toThrow(invalidResponseMessage)
  })

  it("accepts a non-null aware onboarding completion timestamp", async () => {
    const response = {
      ...selfFixture(),
      onboarding_required: false,
      onboarding_completed_at: "2026-08-12T02:00:00Z",
    }
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(jsonResponse(response)),
    )

    await expect(getMyFoodPreferences()).resolves.toEqual(response)
  })

  it("preserves an HTTP 409 ProblemDetailsError unchanged", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "版本已变化",
          code: "food_preference_version_changed",
          request_id: "request-409",
        },
        409,
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    let caught: unknown
    try {
      await getMyFoodPreferences()
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(ProblemDetailsError)
    expect((caught as ProblemDetailsError).problem).toMatchObject({
      status: 409,
      code: "food_preference_version_changed",
      request_id: "request-409",
    })
  })

  it("strictly validates write and export inputs before sending", async () => {
    const fetchMock = vi.fn<typeof fetch>()
    vi.stubGlobal("fetch", fetchMock)

    const invalidWrites: unknown[] = [
      { ...writeInput, private_note: "secret" },
      { ...writeInput, expected_version: -1 },
      { ...writeInput, expected_version: 1.5 },
      { ...writeInput, expected_version: "2" },
      { ...writeInput, cuisines: Array.from({ length: 4 }, () => writeInput.cuisines[0]) },
      { ...writeInput, dishes: ["一", "二", "三", "四"] },
      {
        ...writeInput,
        cuisines: [{ ...writeInput.cuisines[0], private_note: "secret" }],
      },
    ]
    for (const input of invalidWrites) {
      await expect(
        updateMyFoodPreferences(input as FoodPreferenceWriteInput),
      ).rejects.toThrow(invalidResponseMessage)
    }

    const invalidExports: unknown[] = [
      { ...exportInput, private_note: "secret" },
      { ...exportInput, population: "coaches" },
      { ...exportInput, include_inactive: "false" },
      { ...exportInput, format: "csv" },
      { ...exportInput, purpose: "abc" },
    ]
    for (const input of invalidExports) {
      await expect(
        exportFoodPreferences(input as FoodPreferenceExportInput),
      ).rejects.toThrow(invalidResponseMessage)
    }
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe("food preference exports", () => {
  it("downloads an xlsx export with an exact MIME type and safe filename", async () => {
    const response = new Response("xlsx bytes", {
      headers: {
        "Content-Type": xlsxMime,
        "Content-Disposition": 'attachment; filename="food-report.xlsx"',
      },
    })
    const expectedBlob = new Blob(["xlsx bytes"], { type: xlsxMime })
    const blobSpy = vi.spyOn(response, "blob").mockResolvedValue(expectedBlob)
    const jsonSpy = vi.spyOn(response, "json")
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response)
    vi.stubGlobal("fetch", fetchMock)
    const controller = new AbortController()

    const result = await exportFoodPreferences(exportInput, controller.signal)

    expect(result).toEqual({
      blob: expectedBlob,
      filename: "food-report.xlsx",
    })
    expect(blobSpy).toHaveBeenCalledTimes(1)
    expect(jsonSpy).not.toHaveBeenCalled()
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/food-preferences/exports",
      expect.objectContaining({
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify(exportInput),
      }),
    )
    const [, init] = fetchMock.mock.calls[0] ?? []
    expect(new Headers(init?.headers).get("Content-Type")).toBe(
      "application/json",
    )
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })

  it("downloads a csv_zip export and safely decodes filename-star", async () => {
    const input = { ...exportInput, format: "csv_zip" as const }
    const response = new Response("zip bytes", {
      headers: {
        "Content-Type": "application/zip",
        "Content-Disposition": "attachment; filename*=UTF-8''food-report.zip",
      },
    })
    const expectedBlob = new Blob(["zip bytes"], { type: "application/zip" })
    vi.spyOn(response, "blob").mockResolvedValue(expectedBlob)
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response))

    await expect(exportFoodPreferences(input)).resolves.toEqual({
      blob: expectedBlob,
      filename: "food-report.zip",
    })
  })

  it.each([
    ["missing MIME", { "Content-Disposition": 'attachment; filename="report.xlsx"' }],
    ["wrong MIME", { "Content-Type": "application/zip", "Content-Disposition": 'attachment; filename="report.xlsx"' }],
    ["MIME parameters", { "Content-Type": `${xlsxMime}; charset=binary`, "Content-Disposition": 'attachment; filename="report.xlsx"' }],
    ["missing disposition", { "Content-Type": xlsxMime }],
    ["inline disposition", { "Content-Type": xlsxMime, "Content-Disposition": 'inline; filename="report.xlsx"' }],
    ["missing filename", { "Content-Type": xlsxMime, "Content-Disposition": "attachment" }],
    ["empty filename", { "Content-Type": xlsxMime, "Content-Disposition": 'attachment; filename=""' }],
    ["space", { "Content-Type": xlsxMime, "Content-Disposition": 'attachment; filename="report file.xlsx"' }],
    ["unicode", { "Content-Type": xlsxMime, "Content-Disposition": "attachment; filename*=UTF-8''%E6%8A%A5%E5%91%8A.xlsx" }],
    ["slash", { "Content-Type": xlsxMime, "Content-Disposition": 'attachment; filename="../report.xlsx"' }],
    ["backslash", { "Content-Type": xlsxMime, "Content-Disposition": 'attachment; filename="..\\report.xlsx"' }],
    ["encoded slash", { "Content-Type": xlsxMime, "Content-Disposition": "attachment; filename*=UTF-8''..%2Freport.xlsx" }],
    ["encoded backslash", { "Content-Type": xlsxMime, "Content-Disposition": "attachment; filename*=UTF-8''..%5Creport.xlsx" }],
    ["malformed encoding", { "Content-Type": xlsxMime, "Content-Disposition": "attachment; filename*=UTF-8''report%ZZ.xlsx" }],
    ["wrong extension", { "Content-Type": xlsxMime, "Content-Disposition": 'attachment; filename="report.zip"' }],
    ["mismatched filename parameters", { "Content-Type": xlsxMime, "Content-Disposition": 'attachment; filename="report.xlsx"; filename*=UTF-8\'\'other.xlsx' }],
  ])("rejects unsafe export response: %s", async (_name, headers) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(new Response("bytes", { headers })),
    )

    await expect(exportFoodPreferences(exportInput)).rejects.toThrow(
      invalidResponseMessage,
    )
  })

  it("maps a blob failure to the same fixed safe response error", async () => {
    const response = new Response("bytes", {
      headers: {
        "Content-Type": xlsxMime,
        "Content-Disposition": 'attachment; filename="report.xlsx"',
      },
    })
    vi.spyOn(response, "blob").mockRejectedValue(new Error("private blob detail"))
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response))

    await expect(exportFoodPreferences(exportInput)).rejects.toThrow(
      invalidResponseMessage,
    )
  })

  it("does not write purpose or export data to browser storage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem")
    const response = new Response("bytes", {
      headers: {
        "Content-Type": xlsxMime,
        "Content-Disposition": 'attachment; filename="report.xlsx"',
      },
    })
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response))

    await exportFoodPreferences({
      ...exportInput,
      purpose: "private purpose should not be cached",
    })

    expect(setItem).not.toHaveBeenCalled()
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
  })
})
