import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { EvaluationTaskActions } from "./EvaluationTaskActions"
import type {
  SystemEvaluationAllowedActions,
  SystemEvaluationTask,
  SystemEvaluationTaskWorkspace,
} from "./systemEvaluationApi"

const taskId = "10000000-0000-4000-8000-000000000011"

afterEach(() => {
  vi.unstubAllGlobals()
})

const noActions: SystemEvaluationAllowedActions = {
  match: false,
  save_manual_draft: false,
  submit_review: false,
  create_correction: false,
  create_revision: false,
  review: false,
  archive: false,
  reopen: false,
  view_preview: false,
}

function task(
  allowedActions: SystemEvaluationAllowedActions,
  status: SystemEvaluationTask["status"] = "published",
): SystemEvaluationTask {
  return {
    id: taskId,
    public_number: "EV-2026-011",
    status,
    lock_version: 3,
    created_at: "2026-08-08T10:00:00Z",
    student_person_id: "40000000-0000-4000-8000-000000000001",
    enrollment_id: "50000000-0000-4000-8000-000000000001",
    cohort_id: "60000000-0000-4000-8000-000000000001",
    project_id: "70000000-0000-4000-8000-000000000001",
    class_id: "80000000-0000-4000-8000-000000000001",
    identity_summary: "学* · **0001",
    cohort_name: "2026 夏季营",
    project_name: "英雄联盟",
    class_name: "菁英班",
    media_count: 1,
    current_result: null,
    published_result: null,
    result_versions: [],
    allowed_actions: allowedActions,
    allowed_reopen_modes: [],
  }
}

function workspace(): SystemEvaluationTaskWorkspace {
  return {
    api_version: "v2",
    task_id: taskId,
    task_version: 3,
    profile: {
      mode: "manual_only",
      adapter_id: null,
      adapter_version: null,
      scoring_rule_version: null,
      result_schema_version: "schema-1",
      required_fields: ["kills"],
      media_slots: [],
    },
    media: [],
    draft: null,
  }
}

function manualWorkspace(): SystemEvaluationTaskWorkspace {
  return {
    ...workspace(),
    profile: {
      ...workspace().profile,
      required_fields: ["kills", "assists"],
    },
  }
}

function automaticWorkspace(): SystemEvaluationTaskWorkspace {
  return {
    ...workspace(),
    profile: {
      ...workspace().profile,
      mode: "automatic",
      adapter_id: "lol",
      adapter_version: "1.0.0",
      scoring_rule_version: "1",
    },
  }
}

function completeWorkspace(): SystemEvaluationTaskWorkspace {
  return {
    ...manualWorkspace(),
    draft: {
      draft_revision: 2,
      fields: { kills: "12", assists: "8" },
      confidences: { kills: 1, assists: 1 },
      warnings: [],
      components: [
        {
          key: "overall",
          score: 88,
          weight: 1,
          summary: "综合表现稳定",
        },
      ],
      missing_fields: [],
      score: 88,
      grade: "A",
      advice: "继续保持训练节奏",
      updated_at: "2026-08-08T12:00:00Z",
    },
  }
}

function completeWorkspaceWithExplicitMissingField(): SystemEvaluationTaskWorkspace {
  return {
    ...completeWorkspace(),
    draft: {
      ...completeWorkspace().draft!,
      fields: { kills: "12" },
      confidences: { kills: 1 },
      missing_fields: ["assists"],
    },
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function view(
  value: SystemEvaluationTask,
  workspaceValue: SystemEvaluationTaskWorkspace = workspace(),
) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  const onRefresh = vi.fn()
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <EvaluationTaskActions
        projection="admin"
        task={value}
        workspace={workspaceValue}
        onRefresh={onRefresh}
        idempotencyKeyFactory={() => "action-key-1"}
      />
    </QueryClientProvider>,
  )
  return { ...rendered, queryClient, onRefresh }
}

it("renders lifecycle controls only from the latest server allowed_actions", () => {
  const allowedActions: SystemEvaluationAllowedActions = {
    ...noActions,
    match: true,
    save_manual_draft: true,
    submit_review: true,
    create_correction: true,
    create_revision: true,
    archive: true,
    reopen: true,
  }
  const rendered = view(task(allowedActions, "uploaded"))

  for (const label of [
    "匹配学员",
    "人工录入",
    "提交审核",
    "开始整改",
    "创建修订",
    "归档任务",
    "重开任务",
  ]) {
    expect(screen.getByRole("button", { name: label })).toBeVisible()
  }

  rendered.rerender(
    <QueryClientProvider client={rendered.queryClient}>
      <EvaluationTaskActions
        projection="admin"
        task={task(noActions, "uploaded")}
        workspace={workspace()}
        onRefresh={rendered.onRefresh}
        idempotencyKeyFactory={() => "action-key-1"}
      />
    </QueryClientProvider>,
  )

  expect(screen.queryByRole("region", { name: "任务操作" })).not.toBeInTheDocument()
})

it("does not infer matching or manual controls from task status or projection", () => {
  view(task(noActions, "pending_match"))

  expect(screen.queryByRole("button", { name: "匹配学员" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "人工录入" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "提交审核" })).not.toBeInTheDocument()
})

it("searches the allowlisted directory and confirms a server-authorized match", async () => {
  const user = userEvent.setup()
  const forbiddenPhone = "13800138000"
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.includes("/match-targets?")) {
      return jsonResponse({
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
            phone: forbiddenPhone,
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      })
    }
    return jsonResponse({
      api_version: "v2",
      task_id: taskId,
      student_person_id: "40000000-0000-4000-8000-000000000001",
      enrollment_id: "50000000-0000-4000-8000-000000000001",
      cohort_id: "60000000-0000-4000-8000-000000000001",
      project_id: "70000000-0000-4000-8000-000000000001",
      class_id: "80000000-0000-4000-8000-000000000001",
      task_status: "queued",
      task_version: 4,
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const rendered = view(task({ ...noActions, match: true }, "pending_match"))

  await user.click(screen.getByRole("button", { name: "匹配学员" }))
  const dialog = screen.getByRole("dialog", { name: "匹配学员" })
  await user.type(screen.getByLabelText("查询学员"), "S-001")
  await user.click(screen.getByRole("button", { name: "查询候选学员" }))
  const candidate = await screen.findByRole("radio", {
    name: "张同学 · S-001 · 2026 夏季营 · 菁英班",
  })
  expect(dialog).not.toHaveTextContent(forbiddenPhone)

  await user.click(candidate)
  await user.click(screen.getByRole("button", { name: "确认匹配" }))

  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
    `/api/v1/evaluations/admin/system-tasks/${taskId}/match-targets?query=S-001&page=1&page_size=20`,
  )
  const [postPath, postInit] = fetchMock.mock.calls[1] ?? []
  expect(String(postPath)).toBe(
    `/api/v1/evaluations/admin/system-tasks/${taskId}/match`,
  )
  expect(new Headers(postInit?.headers).get("Idempotency-Key")).toBe("action-key-1")
  expect(JSON.parse(String(postInit?.body))).toEqual({
    expected_lock_version: 3,
    student_person_id: "40000000-0000-4000-8000-000000000001",
    enrollment_id: "50000000-0000-4000-8000-000000000001",
    cohort_id: "60000000-0000-4000-8000-000000000001",
    class_id: "80000000-0000-4000-8000-000000000001",
  })
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("dialog", { name: "匹配学员" })).not.toBeInTheDocument()
})

it("matches the selected enrollment when one student has multiple eligible enrollments", async () => {
  const user = userEvent.setup()
  const studentPersonId = "40000000-0000-4000-8000-000000000001"
  const secondEnrollmentId = "50000000-0000-4000-8000-000000000002"
  const secondCohortId = "60000000-0000-4000-8000-000000000002"
  const secondClassId = "80000000-0000-4000-8000-000000000002"
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    if (String(request).includes("/match-targets?")) {
      return jsonResponse({
        api_version: "v2",
        items: [
          {
            student_person_id: studentPersonId,
            student_name: "张同学",
            student_number: "S-001",
            enrollment_id: "50000000-0000-4000-8000-000000000001",
            cohort_id: "60000000-0000-4000-8000-000000000001",
            cohort_name: "2026 夏季营",
            class_id: "80000000-0000-4000-8000-000000000001",
            class_name: "标准班",
          },
          {
            student_person_id: studentPersonId,
            student_name: "张同学",
            student_number: "S-001",
            enrollment_id: secondEnrollmentId,
            cohort_id: secondCohortId,
            cohort_name: "2026 秋季营",
            class_id: secondClassId,
            class_name: "菁英班",
          },
        ],
        total: 2,
        page: 1,
        page_size: 20,
      })
    }
    return jsonResponse({
      api_version: "v2",
      task_id: taskId,
      student_person_id: studentPersonId,
      enrollment_id: secondEnrollmentId,
      cohort_id: secondCohortId,
      project_id: "70000000-0000-4000-8000-000000000001",
      class_id: secondClassId,
      task_status: "queued",
      task_version: 4,
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  view(task({ ...noActions, match: true }, "pending_match"))

  await user.click(screen.getByRole("button", { name: "匹配学员" }))
  await user.click(screen.getByRole("button", { name: "查询候选学员" }))
  await user.click(await screen.findByRole("radio", {
    name: "张同学 · S-001 · 2026 秋季营 · 菁英班",
  }))
  await user.click(screen.getByRole("button", { name: "确认匹配" }))

  expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({
    expected_lock_version: 3,
    student_person_id: studentPersonId,
    enrollment_id: secondEnrollmentId,
    cohort_id: secondCohortId,
    class_id: secondClassId,
  })
})

it("keeps the selected match and refreshes the task after a stale 409", async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    if (String(request).includes("/match-targets?")) {
      return jsonResponse({
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
          },
        ],
        total: 1,
        page: 1,
        page_size: 20,
      })
    }
    return jsonResponse(
      {
        type: "about:blank",
        title: "Conflict",
        status: 409,
        detail: "任务版本已变化",
        code: "evaluation_stale_version",
        request_id: "action-stale",
      },
      409,
    )
  })
  vi.stubGlobal("fetch", fetchMock)
  const rendered = view(task({ ...noActions, match: true }, "pending_match"))

  await user.click(screen.getByRole("button", { name: "匹配学员" }))
  await user.click(screen.getByRole("button", { name: "查询候选学员" }))
  const candidate = await screen.findByRole("radio", {
    name: "张同学 · S-001 · 2026 夏季营 · 菁英班",
  })
  await user.click(candidate)
  await user.click(screen.getByRole("button", { name: "确认匹配" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "任务状态已变化，请刷新详情并比较后重试。",
  )
  expect(screen.getByRole("dialog", { name: "匹配学员" })).toBeVisible()
  expect(candidate).toBeChecked()
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
})

it("closes and clears the match form after a private 404", async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "评价资源不存在",
          code: "evaluation_resource_not_found",
        },
        404,
      ),
    ),
  )
  const rendered = view(task({ ...noActions, match: true }, "pending_match"))

  await user.click(screen.getByRole("button", { name: "匹配学员" }))
  await user.type(screen.getByLabelText("查询学员"), "private-query-13800138000")
  await user.click(screen.getByRole("button", { name: "查询候选学员" }))

  expect(
    await screen.findByRole("button", { name: "匹配学员" }),
  ).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "匹配学员" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("private-query-13800138000")).not.toBeInTheDocument()
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
})

it("builds an explicit manual draft from the workspace required fields", async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse({
      api_version: "v2",
      task_id: taskId,
      task_status: "manual_input",
      task_version: 4,
      draft_revision: 1,
      is_complete: false,
      missing_required_fields: ["assists"],
      updated_at: "2026-08-08T12:00:00Z",
    }),
  )
  vi.stubGlobal("fetch", fetchMock)
  const rendered = view(
    task({ ...noActions, save_manual_draft: true }, "manual_input"),
    manualWorkspace(),
  )

  await user.click(screen.getByRole("button", { name: "人工录入" }))
  expect(screen.getByRole("dialog", { name: "人工录入系统评价" })).toBeVisible()
  await user.type(screen.getByLabelText("字段 kills"), "12")
  await user.type(screen.getByLabelText("总分"), "88")
  await user.type(screen.getByLabelText("等级"), "A")
  await user.type(screen.getByLabelText("分项说明"), "综合表现稳定")
  await user.type(screen.getByLabelText("训练建议"), "继续保持训练节奏")
  await user.click(screen.getByRole("button", { name: "保存草稿" }))

  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [path, init] = fetchMock.mock.calls[0] ?? []
  expect(String(path)).toBe(
    `/api/v1/evaluations/admin/system-tasks/${taskId}/manual-draft`,
  )
  expect(init?.method).toBe("PUT")
  expect(new Headers(init?.headers).has("Idempotency-Key")).toBe(false)
  expect(JSON.parse(String(init?.body))).toEqual({
    expected_lock_version: 3,
    draft_revision: null,
    fields: [{ key: "kills", value: "12" }],
    confidences: [{ key: "kills", confidence: 1 }],
    missing_fields: ["assists"],
    warnings: [],
    components: [
      {
        key: "overall",
        score: 88,
        weight: 1,
        summary: "综合表现稳定",
      },
    ],
    score: 88,
    grade: "A",
    advice: "继续保持训练节奏",
  })
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
  expect(
    screen.queryByRole("dialog", { name: "人工录入系统评价" }),
  ).not.toBeInTheDocument()
})

it("preserves existing numeric and boolean field types after editing a draft", async () => {
  const user = userEvent.setup()
  const existing = {
    ...completeWorkspace(),
    draft: {
      ...completeWorkspace().draft!,
      fields: { kills: 12, assists: false },
    },
  }
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse({
      api_version: "v2",
      task_id: taskId,
      task_status: "manual_input",
      task_version: 4,
      draft_revision: 3,
      is_complete: true,
      missing_required_fields: [],
      updated_at: "2026-08-08T13:00:00Z",
    }),
  )
  vi.stubGlobal("fetch", fetchMock)
  view(task({ ...noActions, save_manual_draft: true }, "manual_input"), existing)

  await user.click(screen.getByRole("button", { name: "人工录入" }))
  await user.clear(screen.getByLabelText("字段 kills"))
  await user.type(screen.getByLabelText("字段 kills"), "13")
  await user.clear(screen.getByLabelText("字段 assists"))
  await user.type(screen.getByLabelText("字段 assists"), "true")
  await user.click(screen.getByRole("button", { name: "保存草稿" }))

  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body)).fields).toEqual([
    { key: "kills", value: 13 },
    { key: "assists", value: true },
  ])
})

it("submits only the current complete draft revision for review", async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse(
      {
        api_version: "v2",
        task_id: taskId,
        result_id: "20000000-0000-4000-8000-000000000001",
        result_version: 1,
        task_status: "needs_review",
        task_version: 4,
      },
      201,
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
  const rendered = view(
    task({ ...noActions, submit_review: true }, "manual_input"),
    completeWorkspace(),
  )

  await user.click(screen.getByRole("button", { name: "提交审核" }))
  expect(screen.getByRole("dialog", { name: "提交人工评价审核" })).toHaveTextContent(
    "草稿版本 2",
  )
  await user.click(screen.getByRole("button", { name: "确认提交审核" }))

  expect(fetchMock).toHaveBeenCalledTimes(1)
  const [path, init] = fetchMock.mock.calls[0] ?? []
  expect(String(path)).toBe(
    `/api/v1/evaluations/system/tasks/${taskId}/submit-review`,
  )
  expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("action-key-1")
  expect(JSON.parse(String(init?.body))).toEqual({
    expected_lock_version: 3,
    draft_revision: 2,
  })
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
  expect(
    screen.queryByRole("dialog", { name: "提交人工评价审核" }),
  ).not.toBeInTheDocument()
})

it("submits a complete draft whose missing required field was explicitly recorded", async () => {
  const user = userEvent.setup()
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse(
      {
        api_version: "v2",
        task_id: taskId,
        result_id: "20000000-0000-4000-8000-000000000001",
        result_version: 1,
        task_status: "needs_review",
        task_version: 4,
      },
      201,
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
  view(
    task({ ...noActions, submit_review: true }, "manual_input"),
    completeWorkspaceWithExplicitMissingField(),
  )

  await user.click(screen.getByRole("button", { name: "提交审核" }))
  const confirm = screen.getByRole("button", { name: "确认提交审核" })
  expect(confirm).toBeEnabled()
  await user.click(confirm)

  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
    expected_lock_version: 3,
    draft_revision: 2,
  })
})

it.each([
  {
    flag: "create_correction" as const,
    button: "开始整改",
    title: "开始评价整改",
    modeLabel: "人工处理",
    mode: "manual",
    operation: "corrections",
    status: "manual_input",
    draftRevision: 1,
  },
  {
    flag: "create_revision" as const,
    button: "创建修订",
    title: "创建发布后修订",
    modeLabel: "自动处理",
    mode: "automatic",
    operation: "revisions",
    status: "queued",
    draftRevision: null,
  },
])("starts $operation only after an explicit mode choice", async (input) => {
  const user = userEvent.setup()
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse(
      {
        api_version: "v2",
        task_id: taskId,
        task_status: input.status,
        task_version: 4,
        draft_revision: input.draftRevision,
      },
      201,
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
  const rendered = view(
    task({ ...noActions, [input.flag]: true }, input.flag === "create_correction" ? "rejected" : "published"),
    input.flag === "create_revision" ? automaticWorkspace() : workspace(),
  )

  await user.click(screen.getByRole("button", { name: input.button }))
  expect(screen.getByRole("dialog", { name: input.title })).toBeVisible()
  await user.click(screen.getByRole("radio", { name: input.modeLabel }))
  await user.click(screen.getByRole("button", { name: "确认开始" }))

  const [path, init] = fetchMock.mock.calls[0] ?? []
  expect(String(path)).toBe(
    `/api/v1/evaluations/system/tasks/${taskId}/${input.operation}`,
  )
  expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("action-key-1")
  expect(JSON.parse(String(init?.body))).toEqual({
    expected_lock_version: 3,
    mode: input.mode,
  })
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("dialog", { name: input.title })).not.toBeInTheDocument()
})

it("never offers automatic processing for a rejected correction", async () => {
  const user = userEvent.setup()
  view(task({ ...noActions, create_correction: true }, "rejected"), automaticWorkspace())

  await user.click(screen.getByRole("button", { name: "开始整改" }))

  expect(screen.getByRole("radio", { name: "人工处理" })).toBeVisible()
  expect(screen.queryByRole("radio", { name: "自动处理" })).not.toBeInTheDocument()
})

it("does not offer automatic revision for a manual-only task snapshot", async () => {
  const user = userEvent.setup()
  view(task({ ...noActions, create_revision: true }, "published"), workspace())

  await user.click(screen.getByRole("button", { name: "创建修订" }))

  expect(screen.getByRole("radio", { name: "人工处理" })).toBeVisible()
  expect(screen.queryByRole("radio", { name: "自动处理" })).not.toBeInTheDocument()
})

it.each([
  {
    flag: "archive" as const,
    button: "归档任务",
    title: "归档任务",
    confirm: "确认归档",
    operation: "archive",
    status: "archived",
    reason: "任务资料无法恢复",
  },
  {
    flag: "reopen" as const,
    button: "重开任务",
    title: "重开任务",
    confirm: "确认重开",
    operation: "reopen",
    status: "queued",
    reason: "已补充新的截图资料",
  },
])("requires a reason before $operation", async (input) => {
  const user = userEvent.setup()
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse({
      api_version: "v2",
      task_id: taskId,
      task_status: input.status,
      task_version: 4,
    }),
  )
  vi.stubGlobal("fetch", fetchMock)
  const rendered = view(
    {
      ...task(
        { ...noActions, [input.flag]: true },
        input.flag === "archive" ? "failed" : "archived",
      ),
      allowed_reopen_modes: input.flag === "reopen" ? ["automatic"] : [],
    },
    input.flag === "reopen" ? automaticWorkspace() : workspace(),
  )

  await user.click(screen.getByRole("button", { name: input.button }))
  expect(screen.getByRole("dialog", { name: input.title })).toBeVisible()
  const confirm = screen.getByRole("button", { name: input.confirm })
  expect(confirm).toBeDisabled()
  await user.type(screen.getByLabelText("操作原因"), input.reason)
  if (input.flag === "reopen") {
    await user.click(screen.getByRole("radio", { name: "继续自动处理" }))
  }
  await user.click(confirm)

  const [path, init] = fetchMock.mock.calls[0] ?? []
  expect(String(path)).toBe(
    `/api/v1/evaluations/admin/system-tasks/${taskId}/${input.operation}`,
  )
  expect(new Headers(init?.headers).get("Idempotency-Key")).toBe("action-key-1")
  expect(JSON.parse(String(init?.body))).toEqual(
    input.flag === "archive"
      ? { expected_lock_version: 3, reason: input.reason }
      : {
          expected_lock_version: 3,
          resume: "automatic",
          reason: input.reason,
        },
  )
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
  expect(screen.queryByRole("dialog", { name: input.title })).not.toBeInTheDocument()
})

it("does not offer an invalid reopen mode for a manual-only archived task", async () => {
  const user = userEvent.setup()
  view(task({ ...noActions, reopen: true }, "archived"), workspace())

  await user.click(screen.getByRole("button", { name: "重开任务" }))

  expect(screen.queryByRole("radio", { name: "继续自动处理" })).not.toBeInTheDocument()
  expect(screen.queryByRole("radio", { name: "转入整改" })).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "确认重开" })).toBeDisabled()
})

it("uses server-authoritative reopen modes after automatic retry exhaustion", async () => {
  const user = userEvent.setup()
  const rejectedResult = {
    id: "20000000-0000-4000-8000-000000000009",
    version: 1,
    source: "automatic" as const,
    fields: { kills: 12 },
    confidences: { kills: 0.9 },
    warnings: [],
    components: [
      { key: "overall", score: 88, weight: 1, summary: "综合表现稳定" },
    ],
    missing_fields: [],
    score: 88,
    grade: "A",
    advice: "继续保持训练节奏",
    adapter_id: "lol",
    adapter_version: "1.0.0",
    scoring_rule_version: "1",
    result_schema_version: "1",
    review_decision: "rejected" as const,
    reviewed_at: "2026-08-08T12:00:00Z",
    rejection_reason: "需要人工整改",
    published_at: null,
    previous_version_id: null,
  }
  const exhaustedTask = {
    ...task({ ...noActions, reopen: true }, "archived"),
    current_result: rejectedResult,
    result_versions: [rejectedResult],
    allowed_reopen_modes: ["correction"] satisfies SystemEvaluationTask["allowed_reopen_modes"],
  }
  view(exhaustedTask, automaticWorkspace())

  await user.click(screen.getByRole("button", { name: "重开任务" }))

  expect(screen.queryByRole("radio", { name: "继续自动处理" })).not.toBeInTheDocument()
  expect(screen.getByRole("radio", { name: "转入整改" })).toBeVisible()
})

it("refreshes and clears a lifecycle form after a private mutation 404", async () => {
  const user = userEvent.setup()
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Not Found",
          status: 404,
          detail: "评价资源不存在",
          code: "evaluation_resource_not_found",
        },
        404,
      ),
    ),
  )
  const rendered = view(task({ ...noActions, archive: true }, "failed"))

  await user.click(screen.getByRole("button", { name: "归档任务" }))
  await user.type(screen.getByLabelText("操作原因"), "private-archive-reason")
  await user.click(screen.getByRole("button", { name: "确认归档" }))

  expect(screen.queryByRole("dialog", { name: "归档任务" })).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("private-archive-reason")).not.toBeInTheDocument()
  expect(rendered.onRefresh).toHaveBeenCalledTimes(1)
})
