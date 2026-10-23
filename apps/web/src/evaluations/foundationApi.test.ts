import { afterEach, describe, expect, expectTypeOf, it, vi } from "vitest"

import {
  addAuxiliaryPhone,
  bindGameAccount,
  captureAuxiliaryPhoneWrite,
  createAnalysisProfile,
  createCohortStaffAssignment,
  createStaffRole,
  deactivateAuxiliaryPhone,
  deactivateStaffRole,
  endStaffAssignment,
  evaluationFoundationKeys,
  getGameAccountLimit,
  getStudentMatchingProfile,
  listAllEvaluationProjects,
  listAnalysisProfiles,
  listAuxiliaryPhones,
  listCohortStaffAssignments,
  listEvaluationCohorts,
  listEvaluationProjects,
  listEvaluationStaffScopes,
  listGameAccountProjects,
  listGameAccountTargets,
  listGameAccounts,
  listStaffDirectory,
  listStaffRoles,
  listStudents,
  replaceAuxiliaryPhone,
  unbindGameAccount,
  updateGameAccountLimit,
} from "./foundationApi"
import type {
  AnalysisProfile,
  AuxiliaryPhone,
  EvaluationCohortDirectoryItem,
  EvaluationFoundationContext,
  EvaluationProjectDirectoryItem,
  EvaluationStaffScopeDirectoryItem,
  GameAccountTarget,
  StaffServiceItem,
  StudentGameAccount,
} from "./foundationApi"
import type { AuxiliaryPhoneWriteOnly } from "./foundationApi"

const accountId = "10000000-0000-4000-8000-000000000001"
const studentId = "20000000-0000-4000-8000-000000000001"
const gameAccountId = "21000000-0000-4000-8000-000000000001"
const projectId = "30000000-0000-4000-8000-000000000001"
const cohortId = "70000000-0000-4000-8000-000000000001"
const roleId = "41000000-0000-4000-8000-000000000001"
const staffId = "50000000-0000-4000-8000-000000000001"
const assignmentId = "51000000-0000-4000-8000-000000000001"
const phoneId = "60000000-0000-4000-8000-000000000001"

function jsonResponse(body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

function requestBody(init: RequestInit | undefined): unknown {
  return JSON.parse(String(init?.body))
}

function auxiliaryPhoneForm(phone: string, reason?: string): HTMLFormElement {
  const form = document.createElement("form")
  const phoneInput = document.createElement("input")
  phoneInput.name = "phone"
  phoneInput.value = phone
  form.append(phoneInput)
  if (reason !== undefined) {
    const reasonInput = document.createElement("input")
    reasonInput.name = "reason"
    reasonInput.value = reason
    form.append(reasonInput)
  }
  return form
}

afterEach(() => vi.unstubAllGlobals())

it("uses account-scoped foundation query keys", () => {
  expect(evaluationFoundationKeys.root(accountId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
  ])
  expect(evaluationFoundationKeys.targets(accountId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "game-account-targets",
  ])
  expect(evaluationFoundationKeys.gameAccounts(accountId, studentId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "game-accounts",
    studentId,
  ])
  expect(
    evaluationFoundationKeys.gameAccountHistory(accountId, studentId, 50),
  ).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "game-accounts",
    studentId,
    "include-unbound",
    true,
    "page-size",
    50,
  ])
  expect(evaluationFoundationKeys.gameAccountProjects(accountId, studentId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "game-account-projects",
    studentId,
  ])
  expect(evaluationFoundationKeys.admin(accountId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "admin",
  ])
  expect(evaluationFoundationKeys.staffCohorts(accountId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "admin",
    "cohorts",
  ])
  expect(evaluationFoundationKeys.staffScopes(accountId, cohortId, "class")).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "admin",
    "cohorts",
    cohortId,
    "staff-scopes",
    "class",
  ])
  expect(evaluationFoundationKeys.analysisProjects(accountId)).toEqual([
    "evaluations",
    accountId,
    "foundation",
    "admin",
    "analysis-projects",
  ])
})

it("matches the frozen public DTOs without storage metadata or full auxiliary phones", () => {
  expectTypeOf<EvaluationFoundationContext>().toEqualTypeOf<{
    accessible: true
    system_evaluation_available: true
    embedded_start_url: string
    standalone_start_url: string
    upload_available: boolean
    video_available: false
    reports_available: boolean
    surveys_available: boolean
    survey_sections: {
      respondent: boolean
      staff_feedback: boolean
      administration: boolean
    }
    game_accounts_available: boolean
    admin_available: boolean
    admin_sections: {
      cohort_staff: boolean
      student_matching: boolean
      analysis_profiles: boolean
      system_tasks: boolean
      adapter_certifications: boolean
    }
    integration_api_version: "v1"
  }>()
  expectTypeOf<GameAccountTarget>().toEqualTypeOf<{
    student_person_id: string
    student_name: string
    student_number: string
    access_source: "self" | "family"
    active_count: number
    active_limit: number
  }>()
  expectTypeOf<StudentGameAccount>().toEqualTypeOf<{
    id: string
    project_id: string
    project_name: string
    game_id: string
    status: "active" | "unbound"
    valid_from: string
    unbound_at: string | null
    unbind_reason: string | null
  }>()
  expectTypeOf<AuxiliaryPhone>().toEqualTypeOf<{
    id: string
    masked_phone: string
    status: "active" | "inactive"
    created_at: string
    disabled_at: string | null
  }>()
  expectTypeOf<StaffServiceItem>().toEqualTypeOf<{
    id: string
    source: "coach_assignment" | "cohort_staff_assignment"
    staff_person_id: string
    staff_name: string
    role_name: string
    role_category: "teaching" | "life_service" | "operations"
    scope_kind: "project" | "class"
    project_id: string
    project_name: string
    class_id: string | null
    class_name: string | null
    permission_keys: string[]
    valid_from: string
    valid_until: string | null
    ended_at: string | null
    end_reason: string | null
    manage_allowed: boolean
  }>()
  expectTypeOf<AnalysisProfile>().toEqualTypeOf<{
    id: string
    project_id: string
    project_name: string
    version: number
    mode: "manual_only" | "automatic"
    adapter_id: string | null
    adapter_version: string | null
    scoring_rule_version: string | null
    required_fields: string[]
    confidence_thresholds: Record<string, number>
    auto_publish_warning_levels: string[]
    publish_mode: "auto_publish" | "review_required"
    effective_from: string
    configured_by_name: string | null
    change_reason: string
    is_current: boolean
  }>()
  expectTypeOf<EvaluationProjectDirectoryItem>().toEqualTypeOf<{
    id: string
    name: string
    is_active: boolean
  }>()
  expectTypeOf<EvaluationCohortDirectoryItem>().toEqualTypeOf<{
    id: string
    name: string
  }>()
  expectTypeOf<EvaluationStaffScopeDirectoryItem>().toEqualTypeOf<{
    id: string
    name: string
    project_id: string
    project_name: string
    is_active: boolean
  }>()

  type ForbiddenResponseKeys<T> = Extract<
    keyof T,
    | "phone"
    | "full_phone"
    | "storage_key"
    | "preview_storage_key"
    | "storage_path"
    | "absolute_path"
    | "sha256"
    | "hash"
  >
  expectTypeOf<ForbiddenResponseKeys<EvaluationFoundationContext>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<GameAccountTarget>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<StudentGameAccount>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<AuxiliaryPhone>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<StaffServiceItem>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<AnalysisProfile>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<EvaluationProjectDirectoryItem>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<EvaluationCohortDirectoryItem>>().toEqualTypeOf<never>()
  expectTypeOf<ForbiddenResponseKeys<EvaluationStaffScopeDirectoryItem>>().toEqualTypeOf<never>()
})

describe("game-account API", () => {
  it("serializes target, project directory, and history URLs exactly", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse([]))
    vi.stubGlobal("fetch", fetchMock)

    await listGameAccountTargets()
    await listGameAccountProjects(studentId, { page: 2, pageSize: 25 })
    await listGameAccounts(studentId, {
      includeUnbound: true,
      page: 2,
      pageSize: 25,
    })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/evaluations/game-account-targets",
      expect.objectContaining({ credentials: "include" }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      `/api/v1/evaluations/students/${studentId}/game-account-projects?page=2&page_size=25`,
      expect.objectContaining({ credentials: "include" }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      `/api/v1/evaluations/students/${studentId}/game-accounts?include_unbound=true&page=2&page_size=25`,
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("sends exact bind and reason-only unbind bodies", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse())
    vi.stubGlobal("fetch", fetchMock)

    await bindGameAccount(studentId, {
      project_id: projectId,
      game_id: "Authorized#2026",
    })
    await unbindGameAccount(studentId, gameAccountId, {
      reason: "学员更换账号",
    })

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      `/api/v1/evaluations/students/${studentId}/game-accounts`,
    )
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST")
    expect(requestBody(fetchMock.mock.calls[0]?.[1])).toEqual({
      project_id: projectId,
      game_id: "Authorized#2026",
    })
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      `/api/v1/evaluations/students/${studentId}/game-accounts/${gameAccountId}/unbind`,
    )
    expect(fetchMock.mock.calls[1]?.[1]?.method).toBe("POST")
    expect(requestBody(fetchMock.mock.calls[1]?.[1])).toEqual({
      reason: "学员更换账号",
    })
  })

})

describe("student matching API", () => {
  it("serializes search, profile, limit, and masked-phone routes exactly", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse([]))
    vi.stubGlobal("fetch", fetchMock)

    await listStudents({ query: "张 同学", page: 3, pageSize: 10 })
    await getStudentMatchingProfile(studentId)
    await getGameAccountLimit(studentId)
    await listAuxiliaryPhones(studentId, {
      includeInactive: true,
      page: 2,
      pageSize: 50,
    })

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/evaluations/admin/students?query=%E5%BC%A0+%E5%90%8C%E5%AD%A6&page=3&page_size=10",
      `/api/v1/evaluations/admin/students/${studentId}/matching-profile`,
      `/api/v1/evaluations/admin/students/${studentId}/game-account-limit`,
      `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones?include_inactive=true&page=2&page_size=50`,
    ])
  })

  it("keeps full auxiliary phones behind an opaque write-only boundary", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse())
    vi.stubGlobal("fetch", fetchMock)

    type ForbiddenWriteKeys<T> = Extract<keyof T, "phone" | "full_phone">
    expectTypeOf<Parameters<typeof addAuxiliaryPhone>[1]>().toEqualTypeOf<AuxiliaryPhoneWriteOnly>()
    expectTypeOf<Parameters<typeof replaceAuxiliaryPhone>[2]>().toEqualTypeOf<AuxiliaryPhoneWriteOnly>()
    expectTypeOf<ForbiddenWriteKeys<AuxiliaryPhoneWriteOnly>>().toEqualTypeOf<never>()

    await updateGameAccountLimit(studentId, {
      max_active_accounts: 5,
      reason: "赛事账号增加",
    })
    const addWrite = captureAuxiliaryPhoneWrite(auxiliaryPhoneForm("13800138000"))
    const replaceWrite = captureAuxiliaryPhoneWrite(
      auxiliaryPhoneForm("13900139000", "  联系电话更新  "),
    )
    expect(Object.values(addWrite)).not.toContain("13800138000")
    expect(Object.values(replaceWrite)).not.toContain("13900139000")
    await addAuxiliaryPhone(studentId, addWrite)
    await replaceAuxiliaryPhone(studentId, phoneId, replaceWrite)
    await deactivateAuxiliaryPhone(studentId, phoneId, {
      reason: "号码不再使用",
    })

    expect(fetchMock.mock.calls.map(([input, init]) => [
      String(input),
      init?.method,
      requestBody(init),
    ])).toEqual([
      [
        `/api/v1/evaluations/admin/students/${studentId}/game-account-limit`,
        "PUT",
        { max_active_accounts: 5, reason: "赛事账号增加" },
      ],
      [
        `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones`,
        "POST",
        { phone: "13800138000" },
      ],
      [
        `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones/${phoneId}/replace`,
        "POST",
        { phone: "13900139000", reason: "联系电话更新" },
      ],
      [
        `/api/v1/evaluations/admin/students/${studentId}/auxiliary-phones/${phoneId}/deactivate`,
        "POST",
        { reason: "号码不再使用" },
      ],
    ])
  })

  it("consumes each auxiliary-phone write token exactly once", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse())
    vi.stubGlobal("fetch", fetchMock)
    const write = captureAuxiliaryPhoneWrite(auxiliaryPhoneForm("13912345678"))

    await addAuxiliaryPhone(studentId, write)

    expect(() => addAuxiliaryPhone(studentId, write)).toThrow(
      "Invalid auxiliary phone write",
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })
})

describe("staff foundation API", () => {
  it("uses exact evaluation cohort, scope, staff directory, role, and assignment URLs", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse([]))
    vi.stubGlobal("fetch", fetchMock)

    await listEvaluationCohorts({ query: "暑 期", page: 2, pageSize: 25 })
    await listEvaluationStaffScopes(cohortId, { scopeKind: "class", page: 3, pageSize: 30 })
    await listStaffDirectory({ cohortId, query: "王 教练", page: 2, pageSize: 20 })
    await listStaffRoles()
    await listCohortStaffAssignments(cohortId)

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/evaluations/admin/cohorts?query=%E6%9A%91+%E6%9C%9F&page=2&page_size=25",
      `/api/v1/evaluations/admin/cohorts/${cohortId}/staff-scopes?scope_kind=class&page=3&page_size=30`,
      `/api/v1/evaluations/admin/staff-directory?cohort_id=${cohortId}&query=%E7%8E%8B+%E6%95%99%E7%BB%83&page=2&page_size=20`,
      "/api/v1/evaluations/admin/staff-roles",
      `/api/v1/evaluations/admin/cohorts/${cohortId}/staff-assignments`,
    ])
  })

  it("sends exact role and assignment mutation bodies", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse())
    vi.stubGlobal("fetch", fetchMock)
    const assignmentInput = {
      role_id: roleId,
      staff_person_id: staffId,
      scope_kind: "project" as const,
      scope_id: projectId,
      valid_from: "2026-08-01T09:00:00+08:00",
      valid_until: "2026-08-22T18:00:00+08:00",
      permission_keys: ["evaluation_media.original.read"],
    }

    await createStaffRole({
      key: "team_manager",
      name: "战队领队",
      category: "operations",
    })
    await deactivateStaffRole(roleId)
    await createCohortStaffAssignment(cohortId, assignmentInput)
    await endStaffAssignment(assignmentId, { reason: "服务周期结束" })

    expect(fetchMock.mock.calls.map(([input, init]) => [
      String(input),
      init?.method,
      init?.body ? requestBody(init) : undefined,
    ])).toEqual([
      [
        "/api/v1/evaluations/admin/staff-roles",
        "POST",
        { key: "team_manager", name: "战队领队", category: "operations" },
      ],
      [
        `/api/v1/evaluations/admin/staff-roles/${roleId}/deactivate`,
        "POST",
        undefined,
      ],
      [
        `/api/v1/evaluations/admin/cohorts/${cohortId}/staff-assignments`,
        "POST",
        assignmentInput,
      ],
      [
        `/api/v1/evaluations/admin/staff-assignments/${assignmentId}/end`,
        "POST",
        { reason: "服务周期结束" },
      ],
    ])
  })
})

it("uses the evaluation project directory and append-only analysis profile routes", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => jsonResponse([]))
  vi.stubGlobal("fetch", fetchMock)
  const input = {
    expected_current_version: 2,
    mode: "manual_only" as const,
    adapter_id: null,
    adapter_version: null,
    scoring_rule_version: null,
    required_fields: ["game_id"],
    confidence_thresholds: { exact: 1 },
    auto_publish_warning_levels: [],
    publish_mode: "review_required" as const,
    effective_from: "2026-08-10T09:00:00+08:00",
    change_reason: "更新人工分析字段",
  }

  await listEvaluationProjects({ query: "英 雄", page: 2, pageSize: 25 })
  await listAnalysisProfiles(projectId)
  await createAnalysisProfile(projectId, input)

  expect(fetchMock.mock.calls[0]?.[0]).toBe(
    "/api/v1/evaluations/admin/projects?query=%E8%8B%B1+%E9%9B%84&page=2&page_size=25",
  )
  expect(fetchMock.mock.calls[1]?.[0]).toBe(
    `/api/v1/evaluations/admin/projects/${projectId}/analysis-profiles`,
  )
  expect(fetchMock.mock.calls[2]?.[0]).toBe(
    `/api/v1/evaluations/admin/projects/${projectId}/analysis-profiles`,
  )
  expect(fetchMock.mock.calls[2]?.[1]?.method).toBe("POST")
  expect(requestBody(fetchMock.mock.calls[2]?.[1])).toEqual(input)
})

it("rejects repeated directory metadata instead of returning a silent partial result", async () => {
  const firstProject = { id: projectId, name: "英雄联盟", is_active: true }
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    jsonResponse({
      items: [firstProject],
      total: 3,
      page: 1,
      page_size: 1,
    }),
  )
  vi.stubGlobal("fetch", fetchMock)

  await expect(listAllEvaluationProjects()).rejects.toThrow(
    "目录分页响应无效，请重试。",
  )
  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    "/api/v1/evaluations/admin/projects?query=&page=1&page_size=100",
    "/api/v1/evaluations/admin/projects?query=&page=2&page_size=100",
  ])
})

it("stops large directory aggregation with a controlled narrowing prompt", async () => {
  let requests = 0
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async (input) => {
    requests += 1
    const page = Number(new URL(String(input), "http://test").searchParams.get("page"))
    return jsonResponse({
      items: Array.from({ length: 100 }, (_, index) => ({
        id: `project-${page}-${index}`,
        name: `项目 ${page}-${index}`,
        is_active: true,
      })),
      total: 1001,
      page,
      page_size: 100,
    })
  })
  vi.stubGlobal("fetch", fetchMock)

  await expect(listAllEvaluationProjects()).rejects.toThrow(
    "目录结果过多，请使用搜索缩小范围。",
  )
  expect(requests).toBe(10)
})
