import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import {
  getAdminSurveyFeedback,
  getMySurveyFeedback,
  getSurveyContext,
  getSurveyPrint,
  submitSurvey,
  submitSurveySupplement,
} from "./surveyApi"

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it("loads context and posts the closed submission payload", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).includes("/evaluations/surveys/context?")) return new Response(JSON.stringify([]), { status: 200 })
    expect(init?.method).toBe("POST")
    expect(init?.body).toContain("idempotency_key")
    return new Response(JSON.stringify({ submission_id: "00000000-0000-4000-8000-000000000001", version_id: "00000000-0000-4000-8000-000000000002", version: 1, published_at: "2026-01-01T00:00:00Z", idempotent: false }), { status: 201 })
  })
  vi.stubGlobal("fetch", fetchMock)
  expect(await getSurveyContext()).toEqual([])
  await expect(submitSurvey("00000000-0000-4000-8000-000000000003", { idempotency_key: "abcdefgh", expected_snapshot_version: 1, expected_food_preference_version: 0, favorite_cuisines: [{ cuisine_option_id: "00000000-0000-4000-8000-000000000004", name: "川菜" }], favorite_dishes: ["鱼香肉丝"], course_training_score: 8, life_service_score: 8, service_management_score: 8, staff_ratings: [{ staff_snapshot_id: "00000000-0000-4000-8000-000000000005", score: 9 }] })).resolves.toMatchObject({ version: 1 })
})

it("requests an explicit respondent context page", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    expect(String(input)).toContain("/evaluations/surveys/context?page=2&page_size=20")
    return new Response(JSON.stringify([]), { status: 200 })
  })
  vi.stubGlobal("fetch", fetchMock)

  await expect(getSurveyContext(2, 20)).resolves.toEqual([])
})

it("posts a supplement to the current snapshot", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_input, init) => {
    expect(init?.method).toBe("POST")
    return new Response(JSON.stringify({ supplement_id: "40000000-0000-4000-8000-000000000001", idempotent: false }), { status: 201 })
  }))
  await expect(submitSurveySupplement("40000000-0000-4000-8000-000000000002", "40000000-0000-4000-8000-000000000003", { idempotency_key: "supplement-1", expected_snapshot_version: 2, staff_ratings: [{ staff_snapshot_id: "40000000-0000-4000-8000-000000000004", score: 9 }] })).resolves.toMatchObject({ idempotent: false })
})

it("decodes the privacy-safe staff feedback projection", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    items: [{
      cycle_id: "00000000-0000-4000-8000-000000000001",
      cohort_id: "00000000-0000-4000-8000-000000000002",
      version: 1,
      ordinal: 1,
      role_category: "teaching",
      score: 9,
      reason: "清楚",
      suggestion: "继续",
      course_training_score: 8,
      course_training_reason: "课程原因",
      life_service_score: null,
      life_service_reason: null,
      service_management_score: null,
      service_management_reason: null,
      team_suggestion: "复盘",
      project_id: "00000000-0000-4000-8000-000000000003",
      role_key: "primary_coach",
      role_name: "主教练",
    }],
    total: 1,
    page: 1,
    page_size: 50,
  }), { status: 200 })))

  await expect(getMySurveyFeedback()).resolves.toMatchObject({
    items: [expect.objectContaining({ role_category: "teaching", team_suggestion: "复盘" })],
  })
})

it("sends documented staff feedback scope filters", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    expect(String(input)).toContain("cohort_id=00000000-0000-0000-0000-000000000001")
    expect(String(input)).toContain("role_id=00000000-0000-0000-0000-000000000002")
    expect(String(input)).toContain("role_key=primary_coach")
    return new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }), { status: 200 })
  })
  vi.stubGlobal("fetch", fetchMock)
  await expect(getMySurveyFeedback(1, 50, undefined, {
    cohortId: "00000000-0000-0000-0000-000000000001",
    roleId: "00000000-0000-0000-0000-000000000002",
    roleKey: "primary_coach",
  })).resolves.toMatchObject({ total: 0 })
})

it.each([
  "respondent_person_id",
  "respondent_name",
  "respondent_kind",
])("rejects hidden print responses containing %s", async (identityField) => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    identity: "hidden",
    items: [{
      cohort_id: "00000000-0000-0000-0000-000000000010",
      project_id: null,
      staff_person_id: null,
      staff_name: null,
      role_key: "coach",
      role_name: "教练",
      role_category: "teaching",
      service_intervals: [],
      average_score: 8,
      item_count: 1,
      details: [{
        score: 8,
        reason: null,
        suggestion: null,
        ordinal: 1,
        respondent_person_id: null,
        respondent_name: null,
        respondent_kind: null,
        [identityField]: identityField === "respondent_person_id"
          ? "00000000-0000-4000-8000-000000000011"
          : "self",
      }],
      team_suggestions: [],
      roles: [],
    }],
  }), { status: 200 })))

  await expect(getSurveyPrint()).rejects.toThrow("营期评价响应格式无效")
})

it("requests the admin real-name projection independently of print access", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    expect(String(input)).toContain("identity=real_name")
    return new Response(JSON.stringify({ items: [], total: 0, page: 1, page_size: 50 }), { status: 200 })
  })
  vi.stubGlobal("fetch", fetchMock)

  await expect(getAdminSurveyFeedback({ identity: "real_name" })).resolves.toMatchObject({ total: 0 })
})
