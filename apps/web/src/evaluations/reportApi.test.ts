import { afterEach, describe, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"

const reportId = "10000000-0000-4000-8000-000000000001"
const enrollmentId = "20000000-0000-4000-8000-000000000001"
const studentId = "30000000-0000-4000-8000-000000000001"
const cohortId = "40000000-0000-4000-8000-000000000001"
const projectId = "50000000-0000-4000-8000-000000000001"
const classId = "60000000-0000-4000-8000-000000000001"
const versionId = "70000000-0000-4000-8000-000000000001"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function slotFixture(overrides: Record<string, unknown> = {}) {
  return {
    enrollment_id: enrollmentId,
    student_person_id: studentId,
    cohort_id: cohortId,
    cohort_name: "2026 夏季营",
    project_id: projectId,
    project_name: "英雄联盟",
    class_id: classId,
    class_name: "一班",
    report_kind: "daily",
    period_sequence: 1,
    period_start_date: "2026-08-09",
    period_end_date: "2026-08-09",
    submission_opens_at: "2026-08-08T16:00:00Z",
    initial_submission_deadline: "2026-08-24T15:59:59.999999Z",
    report_id: reportId,
    status: "draft",
    lock_version: 1,
    draft_body: "已保存的日报",
    draft_dimension_values: {},
    draft_revision: 1,
    allowed_actions: ["save_draft", "submit"],
    ...overrides,
  }
}

function targetFixture(overrides: Record<string, unknown> = {}) {
  return {
    enrollment_id: enrollmentId,
    student_person_id: studentId,
    student_name: "授权学员",
    student_number: "STU-2026-001",
    cohort_id: cohortId,
    cohort_name: "2026 夏季营",
    cohort_starts_at: "2026-08-01T00:00:00Z",
    cohort_ends_at: "2026-08-21T15:59:59Z",
    project_id: projectId,
    project_name: "英雄联盟",
    class_id: classId,
    class_name: "一班",
    ...overrides,
  }
}

function targetPageFixture(overrides: Record<string, unknown> = {}) {
  return {
    items: [targetFixture()],
    facets: {
      cohorts: [
        {
          id: cohortId,
          name: "2026 夏季营",
          starts_at: "2026-08-01T00:00:00Z",
          ends_at: "2026-08-21T15:59:59Z",
        },
      ],
      projects: [{ id: projectId, name: "英雄联盟", cohort_ids: [cohortId] }],
      classes: [
        {
          id: classId,
          name: "一班",
          cohort_id: cohortId,
          project_id: projectId,
        },
      ],
    },
    total: 1,
    page: 2,
    page_size: 10,
    ...overrides,
  }
}

function versionPageFixture(versionOverrides: Record<string, unknown> = {}) {
  return {
    items: [
      {
        report_id: reportId,
        version_id: versionId,
        version: 2,
        source: "revision",
        body: "复盘后的完整评语",
        dimension_values: {},
        submitted_at: "2026-08-22T02:00:00Z",
        is_current: true,
        is_published: false,
        review: {
          decision: "rejected",
          reason: "需要补充训练结论",
          reviewed_at: "2026-08-22T04:00:00Z",
        },
        ...versionOverrides,
      },
    ],
    total: 1,
    page: 1,
    page_size: 20,
  }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

describe("teaching report API", () => {
  it("allowlists context and author slots before returning cacheable data", async () => {
    const { getTeachingReportContext, listTeachingReportSlots } = await import(
      "./reportApi"
    )
    const forbidden = "private-phone-13800138000"
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          author_available: true,
          reviewer_available: false,
          family_available: false,
          identity_number: forbidden,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [slotFixture({ phone: forbidden })],
          storage_path: forbidden,
        }),
      )
    vi.stubGlobal("fetch", fetchMock)

    const context = await getTeachingReportContext()
    const slots = await listTeachingReportSlots(enrollmentId)

    expect(context).toEqual({
      author_available: true,
      reviewer_available: false,
      family_available: false,
    })
    expect(slots.items[0]?.cohort_name).toBe("2026 夏季营")
    expect(JSON.stringify([context, slots])).not.toContain(forbidden)
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/evaluations/reports/context",
      `/api/v1/evaluations/reports/slots?enrollment_id=${enrollmentId}`,
    ])
  })

  it("normalizes author, review, and family list queries in backend order", async () => {
    const {
      listPublishedTeachingReports,
      listTeachingReportReviewQueue,
      listTeachingReports,
    } = await import("./reportApi")
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, page: 2, page_size: 10 }))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }))
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, page: 3, page_size: 5 }))
    vi.stubGlobal("fetch", fetchMock)

    await listTeachingReports({
      enrollmentId,
      kind: "weekly",
      status: "published",
      page: 2,
      pageSize: 10,
    })
    await listTeachingReportReviewQueue({
      cohortId,
      projectId,
      classId,
      page: 1,
      pageSize: 20,
    })
    await listPublishedTeachingReports({
      studentId,
      enrollmentId,
      page: 3,
      pageSize: 5,
    })

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/v1/evaluations/reports?enrollment_id=${enrollmentId}&kind=weekly&status=published&page=2&page_size=10`,
      `/api/v1/evaluations/reports/review-queue?cohort_id=${cohortId}&project_id=${projectId}&class_id=${classId}&page=1&page_size=20`,
      `/api/v1/evaluations/students/${studentId}/published-reports?enrollment_id=${enrollmentId}&page=3&page_size=5`,
    ])
  })

  it("normalizes cancellable target and version reads in backend order", async () => {
    const {
      listTeachingReportTargets,
      listTeachingReportVersions,
    } = await import("./reportApi")
    const forbidden = "private-workspace-value"
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          ...targetPageFixture(),
          items: [targetFixture({ phone: forbidden, identity_number: forbidden })],
          audit_request_id: forbidden,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          ...versionPageFixture(),
          items: [
            {
              ...versionPageFixture().items[0],
              reviewed_by_user_id: forbidden,
              reopen_reason: forbidden,
              audit_request_id: forbidden,
            },
          ],
        }),
      )
    vi.stubGlobal("fetch", fetchMock)
    const controller = new AbortController()

    const targets = await listTeachingReportTargets(
      {
        cohortId,
        projectId,
        classId,
        studentId,
        query: " 学员_% ",
        page: 2,
        pageSize: 10,
      },
      controller.signal,
    )
    const versions = await listTeachingReportVersions(
      { reportId, page: 1, pageSize: 20 },
      controller.signal,
    )

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/v1/evaluations/reports/targets?cohort_id=${cohortId}&project_id=${projectId}&class_id=${classId}&student_id=${studentId}&query=${encodeURIComponent("学员_%")}&page=2&page_size=10`,
      `/api/v1/evaluations/reports/${reportId}/versions?page=1&page_size=20`,
    ])
    expect(
      fetchMock.mock.calls.every(([, init]) => init?.signal === controller.signal),
    ).toBe(true)
    expect(JSON.stringify([targets, versions])).not.toContain(forbidden)
    expect(targets.items[0]?.student_name).toBe("授权学员")
    expect(versions.items[0]?.review?.reason).toBe("需要补充训练结论")
  })

  it("forwards cancellation to every existing report GET", async () => {
    const {
      getTeachingReport,
      getTeachingReportContext,
      listPublishedTeachingReports,
      listTeachingReportReviewQueue,
      listTeachingReports,
      listTeachingReportSlots,
    } = await import("./reportApi")
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          author_available: true,
          reviewer_available: false,
          family_available: false,
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ items: [slotFixture()] }))
      .mockResolvedValueOnce(
        jsonResponse({ items: [slotFixture()], total: 1, page: 1, page_size: 20 }),
      )
      .mockResolvedValueOnce(jsonResponse(slotFixture()))
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }),
      )
      .mockResolvedValueOnce(
        jsonResponse({ items: [], total: 0, page: 1, page_size: 20 }),
      )
    vi.stubGlobal("fetch", fetchMock)
    const controller = new AbortController()

    await getTeachingReportContext(controller.signal)
    await listTeachingReportSlots(enrollmentId, controller.signal)
    await listTeachingReports({ enrollmentId }, controller.signal)
    await getTeachingReport(reportId, controller.signal)
    await listTeachingReportReviewQueue({}, controller.signal)
    await listPublishedTeachingReports({ studentId }, controller.signal)

    expect(fetchMock).toHaveBeenCalledTimes(6)
    expect(
      fetchMock.mock.calls.every(([, init]) => init?.signal === controller.signal),
    ).toBe(true)
  })

  it("rejects malformed private version values without echoing them", async () => {
    const { listTeachingReportVersions } = await import("./reportApi")
    const privateValue = "private-invalid-version-value"
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse(
          versionPageFixture({
            dimension_values: { leaked: privateValue },
          }),
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          versionPageFixture({
            review: {
              decision: privateValue,
              reason: null,
              reviewed_at: "2026-08-22T04:00:00Z",
            },
          }),
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          versionPageFixture({
            review: {
              decision: "approved",
              reason: privateValue,
              reviewed_at: "2026-08-22T04:00:00Z",
            },
          }),
        ),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          versionPageFixture({
            review: {
              decision: "rejected",
              reason: null,
              reviewed_at: "2026-08-22T04:00:00Z",
            },
          }),
        ),
      )
    vi.stubGlobal("fetch", fetchMock)

    for (let attempt = 0; attempt < 4; attempt += 1) {
      let caught: unknown
      try {
        await listTeachingReportVersions({ reportId })
      } catch (error) {
        caught = error
      }
      expect(caught).toBeInstanceOf(Error)
      expect((caught as Error).message).toBe(
        "教学评语响应格式无效，请刷新重试。",
      )
      expect(String(caught)).not.toContain(privateValue)
    }
  })

  it("rejects malformed responses without echoing private server values", async () => {
    const { listTeachingReportSlots } = await import("./reportApi")
    const malformed = "private-malformed-13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          items: [slotFixture({ allowed_actions: [malformed] })],
        }),
      ),
    )

    let caught: unknown
    try {
      await listTeachingReportSlots(enrollmentId)
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toBe("教学评语响应格式无效，请刷新重试。")
    expect((caught as Error).cause).toBeUndefined()
    expect(String(caught)).not.toContain(malformed)
  })

  it("keeps report text in an immutable one-shot opaque draft write", async () => {
    const {
      captureTeachingReportWrite,
      saveTeachingReportDraft,
    } = await import("./reportApi")
    const body = "private-report-body-13800138000"
    const input = {
      kind: "draft" as const,
      enrollment_id: enrollmentId,
      report_kind: "daily" as const,
      period_start_date: "2026-08-09",
      body,
      dimension_values: {},
      expected_draft_revision: 0,
    }
    const write = captureTeachingReportWrite(input)
    input.body = "mutated-after-capture"
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        report_id: reportId,
        status: "draft",
        lock_version: 1,
        draft_revision: 1,
        body,
        dimension_values: {},
        idempotent: false,
        allowed_actions: ["save_draft", "submit"],
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("csrf-report")

    expect(JSON.stringify(write)).toBe("{}")
    await saveTeachingReportDraft(reportId, write, "draft-key")

    const [, init] = fetchMock.mock.calls[0] ?? []
    const headers = new Headers(init?.headers)
    expect(init?.method).toBe("PUT")
    expect(headers.get("X-CSRF-Token")).toBe("csrf-report")
    expect(headers.get("Idempotency-Key")).toBe("draft-key")
    expect(String(init?.body)).toContain(body)
    expect(String(init?.body)).not.toContain("mutated-after-capture")
    expect(localStorage.length).toBe(0)
    expect(sessionStorage.length).toBe(0)
    await expect(
      saveTeachingReportDraft(reportId, write, "draft-key"),
    ).rejects.toThrow("教学评语写入已失效")
  })

  it("uses opaque review and reopen writes without exposing reasons", async () => {
    const {
      captureTeachingReportWrite,
      reopenTeachingReport,
      reviewTeachingReport,
    } = await import("./reportApi")
    const rejectionReason = "private-rejection-reason"
    const reopenReason = "private-reopen-reason"
    const reviewWrite = captureTeachingReportWrite({
      kind: "review",
      expected_lock_version: 2,
      decision: "rejected",
      reason: rejectionReason,
    })
    const reopenWrite = captureTeachingReportWrite({
      kind: "reopen",
      expected_lock_version: 3,
      grant_kind: "correction",
      reason: reopenReason,
    })
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          report_id: reportId,
          status: "rejected",
          lock_version: 3,
          published_version_id: null,
          review_id: versionId,
          decision: "rejected",
          idempotent: false,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          report_id: reportId,
          status: "rejected",
          lock_version: 4,
          grant_id: versionId,
          grant_kind: "correction",
          opens_at: "2026-08-10T00:00:00Z",
          expires_at: "2026-08-13T15:59:59.999999Z",
          idempotent: false,
        }),
      )
    vi.stubGlobal("fetch", fetchMock)

    expect(JSON.stringify([reviewWrite, reopenWrite])).toBe("[{},{}]")
    await reviewTeachingReport(reportId, reviewWrite, "review-key")
    await reopenTeachingReport(reportId, reopenWrite, "reopen-key")

    const calls = fetchMock.mock.calls.map(([input, init]) => ({
      url: String(input),
      body: String(init?.body),
    }))
    expect(calls).toEqual([
      {
        url: `/api/v1/evaluations/reports/${reportId}/reviews`,
        body: JSON.stringify({
          expected_lock_version: 2,
          decision: "rejected",
          reason: rejectionReason,
        }),
      },
      {
        url: `/api/v1/evaluations/reports/${reportId}/reopen`,
        body: JSON.stringify({
          expected_lock_version: 3,
          grant_kind: "correction",
          reason: reopenReason,
        }),
      },
    ])
  })
})
