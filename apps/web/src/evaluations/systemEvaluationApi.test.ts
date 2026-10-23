import { afterEach, describe, expect, it, vi } from "vitest"

import { ProblemDetailsError, setCsrfToken } from "../api/client"

const taskId = "10000000-0000-4000-8000-000000000001"
const resultId = "20000000-0000-4000-8000-000000000001"
const mediaId = "30000000-0000-4000-8000-000000000001"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function resultFixture(overrides: Record<string, unknown> = {}) {
  return {
    id: resultId,
    version: 1,
    source: "automatic",
    fields: { kills: 12 },
    confidences: { kills: 0.98 },
    warnings: [],
    components: [
      { key: "combat", score: 88, weight: 1, summary: "发挥稳定" },
    ],
    missing_fields: [],
    score: 88,
    grade: "A",
    advice: "继续保持训练节奏",
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "1",
    result_schema_version: "1",
    review_decision: "approved",
    reviewed_at: "2026-08-08T12:00:00Z",
    rejection_reason: null,
    published_at: "2026-08-08T12:00:00Z",
    previous_version_id: null,
    ...overrides,
  }
}

function taskFixture(overrides: Record<string, unknown> = {}) {
  const result = resultFixture()
  return {
    id: taskId,
    public_number: "EV-2026-001",
    status: "published",
    lock_version: 2,
    created_at: "2026-08-08T10:00:00Z",
    student_person_id: "40000000-0000-4000-8000-000000000001",
    enrollment_id: "50000000-0000-4000-8000-000000000001",
    cohort_id: "60000000-0000-4000-8000-000000000001",
    project_id: "70000000-0000-4000-8000-000000000001",
    class_id: "80000000-0000-4000-8000-000000000001",
    identity_summary: "学* · **0001",
    cohort_name: "2026 夏季营",
    project_name: "英雄联盟",
    class_name: "标准班",
    media_count: 1,
    current_result: result,
    published_result: result,
    result_versions: [result],
    allowed_actions: {
      match: false,
      save_manual_draft: false,
      submit_review: false,
      create_correction: false,
      create_revision: true,
      review: false,
      archive: false,
      reopen: false,
      view_preview: true,
    },
    allowed_reopen_modes: [],
    ...overrides,
  }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

describe("system evaluation API", () => {
  it("loads account and admin task filter directories through strict safe projections", async () => {
    const { getSystemEvaluationTaskFilterOptions } = await import(
      "./systemEvaluationApi"
    )
    const forbidden = "private-phone-13800138000"
    const response = {
      api_version: "v2",
      cohorts: [
        {
          id: "60000000-0000-4000-8000-000000000001",
          label: "2026 夏季营",
          phone: forbidden,
        },
      ],
      projects: [
        {
          id: "70000000-0000-4000-8000-000000000001",
          label: "英雄联盟",
          identity_number: forbidden,
        },
      ],
      classes: [
        {
          id: "80000000-0000-4000-8000-000000000001",
          label: "菁英班",
          account: forbidden,
        },
      ],
      students: [
        {
          id: "40000000-0000-4000-8000-000000000001",
          label: "学* / S***01",
          phone_number: forbidden,
          identity_summary: forbidden,
        },
      ],
      raw_identity: forbidden,
    }
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(jsonResponse(response))
      .mockResolvedValueOnce(jsonResponse(response))
    vi.stubGlobal("fetch", fetchMock)

    const account = await getSystemEvaluationTaskFilterOptions("account")
    const admin = await getSystemEvaluationTaskFilterOptions("admin")

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/evaluations/system/task-filter-options",
      "/api/v1/evaluations/admin/system-task-filter-options",
    ])
    expect(account.students).toEqual([
      {
        id: "40000000-0000-4000-8000-000000000001",
        label: "学* / S***01",
      },
    ])
    expect(admin).toEqual(account)
    expect(JSON.stringify([account, admin])).not.toContain(forbidden)
    expect(JSON.stringify([account, admin])).not.toContain("phone_number")
    expect(JSON.stringify([account, admin])).not.toContain("identity_summary")
  })

  it("normalizes account and admin list queries in backend order", async () => {
    const { listSystemEvaluationTasks } = await import("./systemEvaluationApi")
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
      jsonResponse({ api_version: "v2", items: [], total: 0, page: 1, page_size: 20 }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await listSystemEvaluationTasks("account", {
      status: "published",
      cohortId: "cohort-1",
      projectId: "project-1",
      classId: "class-1",
      studentId: "student-1",
      publishedOnly: true,
      page: 2,
      pageSize: 10,
    })
    await listSystemEvaluationTasks("account", {
      queue: "processing",
      page: 1,
      pageSize: 20,
    })
    await listSystemEvaluationTasks("admin", {
      queue: "needs_review",
      page: 1,
      pageSize: 20,
    })

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/evaluations/system/tasks?status=published&cohort_id=cohort-1&project_id=project-1&class_id=class-1&student_id=student-1&published_only=true&page=2&page_size=10",
      "/api/v1/evaluations/system/tasks?queue=processing&page=1&page_size=20",
      "/api/v1/evaluations/admin/system-tasks?queue=needs_review&page=1&page_size=20",
    ])
  })

  it("allowlists task results before returning cacheable data", async () => {
    const { getSystemEvaluationTask } = await import("./systemEvaluationApi")
    const forbidden = "private-ocr-path-13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          api_version: "v2",
          task: taskFixture({
            physical_path: forbidden,
            allowed_reopen_modes: ["correction"],
            current_result: resultFixture({ raw_ocr: forbidden }),
          }),
        }),
      ),
    )

    const result = await getSystemEvaluationTask("account", taskId)

    expect(result.task.public_number).toBe("EV-2026-001")
    expect(
      (result.task as unknown as { allowed_reopen_modes: string[] })
        .allowed_reopen_modes,
    ).toEqual(["correction"])
    expect(JSON.stringify(result)).not.toContain(forbidden)
    expect(JSON.stringify(result)).not.toContain("physical_path")
    expect(JSON.stringify(result)).not.toContain("raw_ocr")
  })

  it("rejects malformed nested result values without echoing them", async () => {
    const { getSystemEvaluationTask } = await import("./systemEvaluationApi")
    const malformed = "private-malformed-13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          api_version: "v2",
          task: taskFixture({
            current_result: resultFixture({
              fields: { kills: { physical_path: malformed } },
            }),
          }),
        }),
      ),
    )

    let caught: unknown
    try {
      await getSystemEvaluationTask("account", taskId)
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(Error)
    expect((caught as Error).message).toBe("系统评价响应格式无效，请刷新重试。")
    expect((caught as Error).cause).toBeUndefined()
    expect(String(caught)).not.toContain(malformed)
  })

  it("decodes dashboard and workspace without retaining server extras", async () => {
    const {
      getSystemEvaluationDashboard,
      getSystemEvaluationTaskWorkspace,
    } = await import("./systemEvaluationApi")
    const forbidden = "private-storage-key-13800138000"
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          api_version: "v2",
          upload_available: true,
          processing: 1,
          pending_match: 2,
          manual_input: 3,
          needs_review: 4,
          rejected: 5,
          published: 6,
          identity_number: forbidden,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          api_version: "v2",
          task_id: taskId,
          task_version: 2,
          profile: {
            mode: "automatic",
            adapter_id: "lol",
            adapter_version: "1.0.0",
            scoring_rule_version: "1",
            result_schema_version: "1",
            required_fields: ["kills", "score", "grade"],
            media_slots: [
              { key: "profile", label: "个人数据", position: 1, required: true },
            ],
          },
          media: [
            {
              id: mediaId,
              slot_key: "profile",
              position: 1,
              mime_type: "image/png",
              size_bytes: 100,
              width_pixels: 10,
              height_pixels: 10,
              storage_key: forbidden,
            },
          ],
          draft: null,
        }),
      )
    vi.stubGlobal("fetch", fetchMock)

    const dashboard = await getSystemEvaluationDashboard()
    const workspace = await getSystemEvaluationTaskWorkspace("account", taskId)

    expect(dashboard.published).toBe(6)
    expect(workspace.media[0]?.id).toBe(mediaId)
    expect(JSON.stringify([dashboard, workspace])).not.toContain(forbidden)
  })

  it("builds a protected same-origin preview URL without fetching or persisting bytes", async () => {
    const { systemEvaluationPreviewUrl } = await import("./systemEvaluationApi")

    expect(systemEvaluationPreviewUrl(taskId, mediaId)).toBe(
      `/api/v1/evaluations/system/tasks/${taskId}/media/${mediaId}/preview`,
    )
    expect(localStorage.length).toBe(0)
  })

  it("keeps manual text in a one-shot opaque write and omits idempotency for draft save", async () => {
    const {
      captureSystemEvaluationWrite,
      saveSystemEvaluationManualDraft,
    } = await import("./systemEvaluationApi")
    const advice = "private-advice-13800138000"
    const write = captureSystemEvaluationWrite({
      kind: "manual_draft",
      expected_lock_version: 2,
      draft_revision: null,
      fields: [{ key: "kills", value: 12 }],
      confidences: [{ key: "kills", confidence: 1 }],
      missing_fields: [],
      warnings: [],
      components: [],
      score: 88,
      grade: "A",
      advice,
    })
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        api_version: "v2",
        task_id: taskId,
        task_status: "manual_input",
        task_version: 2,
        draft_revision: 1,
        is_complete: true,
        missing_required_fields: [],
        updated_at: "2026-08-08T12:00:00Z",
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("csrf-memory")

    expect(JSON.stringify(write)).toBe("{}")
    await saveSystemEvaluationManualDraft("account", taskId, write)

    const [, init] = fetchMock.mock.calls[0] ?? []
    const headers = new Headers(init?.headers)
    expect(init?.method).toBe("PUT")
    expect(headers.get("X-CSRF-Token")).toBe("csrf-memory")
    expect(headers.has("Idempotency-Key")).toBe(false)
    expect(String(init?.body)).toContain(advice)
    expect(localStorage.length).toBe(0)
    await expect(
      saveSystemEvaluationManualDraft("account", taskId, write),
    ).rejects.toThrow("系统评价写入已失效")
  })

  it("captures an immutable manual write snapshot before the request starts", async () => {
    const {
      captureSystemEvaluationWrite,
      saveSystemEvaluationManualDraft,
    } = await import("./systemEvaluationApi")
    const fields = [{ key: "kills", value: 12 }]
    const input = {
      kind: "manual_draft" as const,
      expected_lock_version: 2,
      draft_revision: null,
      fields,
      confidences: [{ key: "kills", confidence: 1 }],
      missing_fields: [],
      warnings: [],
      components: [],
      score: 88,
      grade: "A",
      advice: "提交时快照",
    }
    const write = captureSystemEvaluationWrite(input)
    input.advice = "捕获后被修改"
    fields[0]!.value = 99
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        api_version: "v2",
        task_id: taskId,
        task_status: "manual_input",
        task_version: 2,
        draft_revision: 1,
        is_complete: true,
        missing_required_fields: [],
        updated_at: "2026-08-08T12:00:00Z",
      }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await saveSystemEvaluationManualDraft("account", taskId, write)

    const request = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)) as {
      advice: string
      fields: Array<{ value: number }>
    }
    expect(request.advice).toBe("提交时快照")
    expect(request.fields[0]?.value).toBe(12)
  })

  it("sends review with one explicit idempotency key and preserves typed 409 mapping", async () => {
    const {
      captureSystemEvaluationWrite,
      reviewSystemEvaluationTask,
    } = await import("./systemEvaluationApi")
    const write = captureSystemEvaluationWrite({
      kind: "review",
      expected_lock_version: 2,
      decision: "rejected",
      rejection_reason: "需要补充截图",
    })
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "任务版本已变化",
          code: "evaluation_stale_version",
          request_id: "request-409",
          raw_ocr: "private-ocr-13800138000",
        },
        409,
      ),
    )
    vi.stubGlobal("fetch", fetchMock)

    let caught: unknown
    try {
      await reviewSystemEvaluationTask(taskId, write, "review-key-1")
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(ProblemDetailsError)
    expect((caught as ProblemDetailsError).problem).toMatchObject({
      status: 409,
      code: "evaluation_stale_version",
    })
    expect(JSON.stringify(caught)).not.toContain("private-ocr")
    const [, init] = fetchMock.mock.calls[0] ?? []
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("review-key-1")
  })

  it("lists and creates adapter certifications through strict write-only payloads", async () => {
    const {
      captureSystemEvaluationWrite,
      createSystemEvaluationAdapterCertification,
      listSystemEvaluationAdapterCertifications,
    } = await import("./systemEvaluationApi")
    const certification = {
      id: "90000000-0000-4000-8000-000000000001",
      project_id: "70000000-0000-4000-8000-000000000001",
      adapter_id: "lol",
      adapter_version: "1.0.0",
      scoring_rule_version: "rule-1",
      result_schema_version: "schema-1",
      manifest_digest: "a".repeat(64),
      certified_by_user_id: "91000000-0000-4000-8000-000000000001",
      certified_at: "2026-08-08T12:00:00Z",
    }
    const forbidden = "private-path-13800138000"
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        jsonResponse({
          api_version: "v2",
          items: [{ ...certification, physical_path: forbidden }],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          api_version: "v2",
          certification: { ...certification, raw_manifest: forbidden },
        }, 201),
      )
    vi.stubGlobal("fetch", fetchMock)
    const write = captureSystemEvaluationWrite({
      kind: "adapter_certification",
      project_id: certification.project_id,
      adapter_id: certification.adapter_id,
      adapter_version: certification.adapter_version,
      scoring_rule_version: certification.scoring_rule_version,
      result_schema_version: certification.result_schema_version,
      manifest_digest: certification.manifest_digest,
    })

    expect(JSON.stringify(write)).toBe("{}")
    const listed = await listSystemEvaluationAdapterCertifications()
    const created = await createSystemEvaluationAdapterCertification(
      write,
      "certification-key-1",
    )

    expect(listed.items[0]?.id).toBe(certification.id)
    expect(created.certification.id).toBe(certification.id)
    expect(JSON.stringify([listed, created])).not.toContain(forbidden)
    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/v1/evaluations/admin/adapter-certifications",
      "/api/v1/evaluations/admin/adapter-certifications",
    ])
    const [, init] = fetchMock.mock.calls[1] ?? []
    expect(init?.method).toBe("POST")
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "certification-key-1",
    )
    expect(JSON.parse(String(init?.body))).toEqual({
      project_id: certification.project_id,
      adapter_id: "lol",
      adapter_version: "1.0.0",
      scoring_rule_version: "rule-1",
      result_schema_version: "schema-1",
      manifest_digest: "a".repeat(64),
    })
  })

  it("uses frozen paths and idempotency for task lifecycle writes", async () => {
    const api = await import("./systemEvaluationApi")
    const fetchMock = vi.fn<typeof fetch>(async (request) => {
      const url = String(request)
      if (url.endsWith("/match")) {
        return jsonResponse({
          api_version: "v2",
          task_id: taskId,
          student_person_id: "40000000-0000-4000-8000-000000000001",
          enrollment_id: "50000000-0000-4000-8000-000000000001",
          cohort_id: "60000000-0000-4000-8000-000000000001",
          project_id: "70000000-0000-4000-8000-000000000001",
          class_id: "80000000-0000-4000-8000-000000000001",
          task_status: "queued",
          task_version: 3,
        })
      }
      if (url.endsWith("/submit-review")) {
        return jsonResponse({
          api_version: "v2",
          task_id: taskId,
          result_id: resultId,
          result_version: 1,
          task_status: "needs_review",
          task_version: 4,
        }, 201)
      }
      if (url.endsWith("/corrections") || url.endsWith("/revisions")) {
        return jsonResponse({
          api_version: "v2",
          task_id: taskId,
          task_status: "manual_input",
          task_version: 5,
          draft_revision: 1,
        }, 201)
      }
      return jsonResponse({
        api_version: "v2",
        task_id: taskId,
        task_status: url.endsWith("/archive") ? "archived" : "queued",
        task_version: 6,
      })
    })
    vi.stubGlobal("fetch", fetchMock)

    await api.matchSystemEvaluationTask(
      "admin",
      taskId,
      api.captureSystemEvaluationWrite({
        kind: "match",
        expected_lock_version: 2,
        student_person_id: "40000000-0000-4000-8000-000000000001",
        enrollment_id: "50000000-0000-4000-8000-000000000001",
        cohort_id: "60000000-0000-4000-8000-000000000001",
        class_id: "80000000-0000-4000-8000-000000000001",
      }),
      "match-key",
    )
    await api.submitSystemEvaluationManualDraft(
      taskId,
      api.captureSystemEvaluationWrite({
        kind: "submit_review",
        expected_lock_version: 3,
        draft_revision: 1,
      }),
      "submit-key",
    )
    await api.startSystemEvaluationCorrection(
      taskId,
      api.captureSystemEvaluationWrite({
        kind: "correction",
        expected_lock_version: 4,
        mode: "manual",
      }),
      "correction-key",
    )
    await api.startSystemEvaluationRevision(
      taskId,
      api.captureSystemEvaluationWrite({
        kind: "revision",
        expected_lock_version: 5,
        mode: "automatic",
      }),
      "revision-key",
    )
    await api.archiveSystemEvaluationTask(
      taskId,
      api.captureSystemEvaluationWrite({
        kind: "archive",
        expected_lock_version: 5,
        reason: "无法恢复的任务",
      }),
      "archive-key",
    )
    await api.reopenSystemEvaluationTask(
      taskId,
      api.captureSystemEvaluationWrite({
        kind: "reopen",
        expected_lock_version: 6,
        resume: "correction",
        reason: "补充资料后重开",
      }),
      "reopen-key",
    )

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      `/api/v1/evaluations/admin/system-tasks/${taskId}/match`,
      `/api/v1/evaluations/system/tasks/${taskId}/submit-review`,
      `/api/v1/evaluations/system/tasks/${taskId}/corrections`,
      `/api/v1/evaluations/system/tasks/${taskId}/revisions`,
      `/api/v1/evaluations/admin/system-tasks/${taskId}/archive`,
      `/api/v1/evaluations/admin/system-tasks/${taskId}/reopen`,
    ])
    expect(
      fetchMock.mock.calls.map(([, init]) =>
        new Headers(init?.headers).get("Idempotency-Key"),
      ),
    ).toEqual([
      "match-key",
      "submit-key",
      "correction-key",
      "revision-key",
      "archive-key",
      "reopen-key",
    ])
  })

  it("allowlists the admin match-target directory", async () => {
    const { listSystemEvaluationMatchTargets } = await import("./systemEvaluationApi")
    const forbidden = "13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        jsonResponse({
          api_version: "v2",
          items: [
            {
              student_person_id: "40000000-0000-4000-8000-000000000001",
              student_name: "张同学",
              student_number: "S-001",
              enrollment_id: "50000000-0000-4000-8000-000000000001",
              cohort_id: "60000000-0000-4000-8000-000000000001",
              cohort_name: "2026 夏季营",
              class_id: "80000000-0000-4000-8000-000000000001",
              class_name: "菁英班",
              phone: forbidden,
            },
          ],
          total: 1,
          page: 1,
          page_size: 20,
        }),
      ),
    )

    const result = await listSystemEvaluationMatchTargets(taskId, {
      query: "S-001",
      page: 1,
      pageSize: 20,
    })

    expect(result.items[0]?.student_name).toBe("张同学")
    expect(JSON.stringify(result)).not.toContain(forbidden)
  })
})
