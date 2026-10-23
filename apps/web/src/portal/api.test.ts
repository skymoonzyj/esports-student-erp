import { afterEach, describe, expect, expectTypeOf, it, vi } from "vitest"

import {
  getCoachNextCohorts,
  getLearningStudents,
  getLearningTeaching,
  getMyHousing,
  getPortalContext,
  getSelfPayments,
  portalKeys,
} from "./api"
import type { PortalCapability, PortalContext } from "./api"

const validPortalContext = {
  coach_available: false,
  learning_available: true,
  payments_available: true,
  housing_management_available: false,
  housing_available: true,
  adult_guardian_invitations_available: true,
  food_profile_available: true,
  food_preferences_onboarding_required: false,
  learning_mode: "family",
} as const

afterEach(() => vi.unstubAllGlobals())

describe("portal API", () => {
  it("requests and allowlists the portal context", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          ...validPortalContext,
          private_profile_kind: "staff",
        }),
        {
        status: 200,
        headers: { "Content-Type": "application/json" },
        },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    const result = await getPortalContext()

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/portal/context",
      expect.objectContaining({ credentials: "include" }),
    )
    expect(result).toEqual(validPortalContext)
    expect(result).not.toHaveProperty("private_profile_kind")
  })

  it.each([
    [
      "missing food profile capability",
      { ...validPortalContext, food_profile_available: undefined },
    ],
    [
      "missing onboarding capability",
      {
        ...validPortalContext,
        food_preferences_onboarding_required: undefined,
      },
    ],
    [
      "string food profile capability",
      { ...validPortalContext, food_profile_available: "true" },
    ],
    [
      "numeric onboarding capability",
      { ...validPortalContext, food_preferences_onboarding_required: 1 },
    ],
  ])("rejects a %s", async (_label, payload) => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    await expect(getPortalContext()).rejects.toThrow(
      "门户上下文响应格式无效",
    )
  })

  it("keeps food capabilities required in the portal contract", () => {
    expectTypeOf<PortalContext>().toEqualTypeOf<{
      coach_available: boolean
      learning_available: boolean
      payments_available: boolean
      housing_management_available: boolean
      housing_available: boolean
      adult_guardian_invitations_available: boolean
      food_profile_available: boolean
      food_preferences_onboarding_required: boolean
      learning_mode: "self" | "family" | "self_and_family" | null
    }>()
    expectTypeOf<PortalCapability>().toEqualTypeOf<
      | "coach_available"
      | "learning_available"
      | "payments_available"
      | "housing_management_available"
      | "housing_available"
      | "adult_guardian_invitations_available"
      | "food_profile_available"
      | "food_preferences_onboarding_required"
    >()
  })

  it("requests the available learning students", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await getLearningStudents()

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/family/students",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("requests each private portal endpoint with stable pagination", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
      new Response(
        JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }),
        {
          status: 200,
          headers: { "Content-Type": "application/json" },
        },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    await getCoachNextCohorts({
      anchorCohortId: "cohort-1",
      page: 2,
      pageSize: 20,
    })
    await getLearningTeaching("student-1", { page: 3, pageSize: 10 })
    await getSelfPayments({ page: 1, pageSize: 5 })

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/coach/next-cohorts?anchor_cohort_id=cohort-1&page=2&page_size=20",
      "/api/v1/family/students/student-1/teaching?page=3&page_size=10",
      "/api/v1/me/payments?page=1&page_size=5",
    ])
  })

  it("uses stable defaults when coach pagination is omitted", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ items: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await getCoachNextCohorts()

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/api/v1/coach/next-cohorts?page=1&page_size=20",
    )
  })

  it("requests and allowlists personal housing before it reaches query cache", async () => {
    const forbiddenPhone = "13800138000"
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          as_of: "2026-08-05T16:00:00+08:00",
          subjects: [
            {
              subject_person_id: "e1000000-0000-4000-8000-000000000004",
              subject_name: "个人住宿学员",
              phone: forbiddenPhone,
              current_stay: null,
              planned_stays: [],
            },
          ],
          identity_number: "11010520120102106X",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    const result = await getMyHousing()

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/me/housing",
      expect.objectContaining({ credentials: "include" }),
    )
    expect(result).toEqual({
      as_of: "2026-08-05T16:00:00+08:00",
      subjects: [
        {
          subject_person_id: "e1000000-0000-4000-8000-000000000004",
          subject_name: "个人住宿学员",
          current_stay: null,
          planned_stays: [],
        },
      ],
    })
    expect(JSON.stringify(result)).not.toContain(forbiddenPhone)
  })

  it("preserves null future roommates, strips stay extras and forwards abort", async () => {
    const forbiddenStaySecret = "private-future-stay-note-13800138000"
    const controller = new AbortController()
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          as_of: "2026-08-05T16:00:00+08:00",
          subjects: [
            {
              subject_person_id: "e1000000-0000-4000-8000-000000000004",
              subject_name: "个人住宿学员",
              current_stay: null,
              planned_stays: [
                {
                  building_name: "青训楼",
                  floor_name: "四层",
                  room_name: "4-202",
                  stay_purpose: "camp",
                  planned_check_in_at: "2030-08-01T09:00:00+08:00",
                  planned_check_out_at: "2030-08-22T09:00:00+08:00",
                  actual_check_in_at: null,
                  actual_check_out_at: null,
                  status: "planned",
                  roommates: null,
                  notes: forbiddenStaySecret,
                  stay_id: "e9000000-0000-4000-8000-000000000004",
                },
              ],
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    const result = await getMyHousing(controller.signal)

    expect(result.subjects[0]?.planned_stays[0]?.roommates).toBeNull()
    expect(JSON.stringify(result)).not.toContain(forbiddenStaySecret)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/me/housing",
      expect.objectContaining({ signal: controller.signal }),
    )
  })

  it("rejects malformed housing without echoing the malformed value", async () => {
    const malformedSecret = "private-malformed-roommate-13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            as_of: "2026-08-05T16:00:00+08:00",
            subjects: [
              {
                subject_person_id: malformedSecret,
                subject_name: "个人住宿学员",
                current_stay: null,
                planned_stays: [],
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    let caught: unknown
    try {
      await getMyHousing()
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toBe("个人住宿响应格式无效")
    expect(String(caught)).not.toContain(malformedSecret)
  })

  it("maps invalid housing JSON to a generic error without echoing the body", async () => {
    const malformedSecret = "private-invalid-json-roommate-13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(malformedSecret, {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    let caught: unknown
    try {
      await getMyHousing()
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toBe("个人住宿响应格式无效")
    expect((caught as Error).cause).toBeUndefined()
    expect(String(caught)).not.toContain(malformedSecret)
  })

  it("scopes every private query key to the current account", () => {
    expect(portalKeys.root("account-1")).toEqual(["portal", "account-1"])
    expect(portalKeys.context("account-1")).toEqual([
      "portal",
      "account-1",
      "context",
    ])
    expect(portalKeys.coach("account-1", undefined, 2)).toEqual([
      "portal",
      "account-1",
      "coach",
      null,
      2,
    ])
    expect(portalKeys.students("account-1")).toEqual([
      "portal",
      "account-1",
      "students",
    ])
    expect(portalKeys.teaching("account-1", "student-1", 3)).toEqual([
      "portal",
      "account-1",
      "teaching",
      "student-1",
      3,
    ])
    expect(portalKeys.payments("account-1", 4)).toEqual([
      "portal",
      "account-1",
      "payments",
      4,
    ])
    expect(portalKeys.housing("account-1")).toEqual([
      "portal",
      "account-1",
      "housing",
    ])
  })
})
