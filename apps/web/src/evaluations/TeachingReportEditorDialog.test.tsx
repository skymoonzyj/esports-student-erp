import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import {
  TeachingReportEditorDialog,
  type TeachingReportEditorDialogProps,
} from "./TeachingReportEditorDialog"
import type { TeachingReportSlot } from "./reportApi"

const reportId = "10000000-0000-4000-8000-000000000001"
const generatedReportId = "90000000-0000-4000-8000-000000000001"
const accountId = "10000000-0000-4000-8000-000000000099"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

const baseSlot: TeachingReportSlot = {
  enrollment_id: "20000000-0000-4000-8000-000000000001",
  student_person_id: "30000000-0000-4000-8000-000000000001",
  cohort_id: "40000000-0000-4000-8000-000000000001",
  cohort_name: "2026 夏季营",
  project_id: "50000000-0000-4000-8000-000000000001",
  project_name: "英雄联盟",
  class_id: "60000000-0000-4000-8000-000000000001",
  class_name: "一班",
  report_kind: "daily",
  period_sequence: 9,
  period_start_date: "2026-08-09",
  period_end_date: "2026-08-09",
  submission_opens_at: "2026-08-08T16:00:00Z",
  initial_submission_deadline: "2026-08-24T15:59:59.999999Z",
  report_id: null,
  status: null,
  lock_version: null,
  draft_body: null,
  draft_dimension_values: null,
  draft_revision: null,
  allowed_actions: ["save_draft"],
}
const newSlot = baseSlot
const savedDraftSlot: TeachingReportSlot = {
  ...baseSlot,
  report_id: reportId,
  status: "draft",
  lock_version: 1,
  draft_body: "已保存草稿",
  draft_dimension_values: {},
  draft_revision: 1,
  allowed_actions: ["save_draft", "submit"],
}
const rejectedSlot: TeachingReportSlot = {
  ...savedDraftSlot,
  status: "rejected",
  draft_body: null,
  draft_dimension_values: null,
  draft_revision: null,
  allowed_actions: ["start_correction"],
}
const publishedSlot: TeachingReportSlot = {
  ...rejectedSlot,
  status: "published",
  allowed_actions: ["start_revision"],
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function draftResponse(id = generatedReportId) {
  return {
    report_id: id,
    status: "draft",
    lock_version: 1,
    draft_revision: 1,
    body: "本次私密日报正文",
    dimension_values: {},
    idempotent: false,
    allowed_actions: ["save_draft", "submit"],
  }
}

function renderEditor(
  slot: TeachingReportSlot,
  overrides: Partial<TeachingReportEditorDialogProps> = {},
) {
  const queryClient = new QueryClient({
    defaultOptions: { mutations: { retry: false } },
  })
  const props: TeachingReportEditorDialogProps = {
    slot,
    onClose: vi.fn(),
    onChanged: vi.fn(),
    onConflict: vi.fn(),
    onUnavailable: vi.fn(),
    idempotencyKeyFactory: () => "editor-idempotency-key",
    reportIdFactory: () => generatedReportId,
    ...overrides,
  }
  const editor = (nextSlot: TeachingReportSlot) => (
    <QueryClientProvider client={queryClient}>
      <TeachingReportEditorDialog {...props} slot={nextSlot} />
    </QueryClientProvider>
  )
  const view = render(editor(slot))
  return {
    props,
    queryClient,
    view,
    rerenderSlot: (nextSlot: TeachingReportSlot) => view.rerender(editor(nextSlot)),
  }
}

afterEach(() => {
  setCsrfToken(null)
  vi.unstubAllGlobals()
})

it.each([
  [newSlot, ["保存草稿"]],
  [savedDraftSlot, ["保存草稿", "提交审核"]],
  [rejectedSlot, ["开始整改"]],
  [publishedSlot, ["发起修订"]],
] as const)("renders only server-allowed actions", (slot, expectedNames) => {
  renderEditor(slot)
  const allActionNames = ["保存草稿", "提交审核", "开始整改", "发起修订"]
  for (const name of allActionNames) {
    if (expectedNames.includes(name as never)) {
      expect(screen.getByRole("button", { name })).toBeVisible()
    } else {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument()
    }
  }
})

it("disables submit after the saved draft becomes dirty", async () => {
  renderEditor(savedDraftSlot)
  const user = userEvent.setup()
  const submit = screen.getByRole("button", { name: "提交审核" })
  expect(submit).toBeEnabled()

  await user.type(screen.getByLabelText("评语正文"), "补充内容")

  expect(submit).toBeDisabled()
  expect(screen.getByText("请先保存草稿")).toBeVisible()
})

it("submits only the server-provided lock and draft revisions", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    jsonResponse({
      report_id: reportId,
      status: "pending_review",
      lock_version: 2,
      version_id: "70000000-0000-4000-8000-000000000001",
      version: 1,
      source: "initial",
      published_version_id: null,
      idempotent: false,
    }, 201),
  )
  vi.stubGlobal("fetch", fetchMock)
  const onChanged = vi.fn()
  renderEditor(savedDraftSlot, { onChanged })
  const user = userEvent.setup()

  await user.click(screen.getByRole("button", { name: "提交审核" }))

  await waitFor(() => expect(onChanged).toHaveBeenCalledWith(reportId))
  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
    expected_lock_version: 1,
    expected_draft_revision: 1,
  })
})

it("reuses the generated report id and idempotency key after an unknown failure", async () => {
  const body = "本次私密日报正文"
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockRejectedValueOnce(new TypeError("network unavailable"))
    .mockResolvedValueOnce(jsonResponse(draftResponse()))
  vi.stubGlobal("fetch", fetchMock)
  setCsrfToken("editor-csrf")
  const onChanged = vi.fn()
  const onClose = vi.fn()
  const keyFactory = vi.fn(() => "stable-editor-key")
  const idFactory = vi.fn(() => generatedReportId)
  const { queryClient, rerenderSlot } = renderEditor(newSlot, {
    onChanged,
    onClose,
    idempotencyKeyFactory: keyFactory,
    reportIdFactory: idFactory,
  })
  const user = userEvent.setup()
  const textarea = screen.getByLabelText("评语正文")
  await user.type(textarea, body)

  await user.click(screen.getByRole("button", { name: "保存草稿" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("网络结果未知")
  rerenderSlot({
    ...newSlot,
    report_id: generatedReportId,
    status: "draft",
    lock_version: 4,
    draft_body: "服务端已写入但客户端未知的正文",
    draft_dimension_values: {},
    draft_revision: 4,
    allowed_actions: ["save_draft", "submit"],
  })
  expect(textarea).toHaveAttribute("readonly")
  await user.type(textarea, "不得改写同一幂等请求")
  expect(textarea).toHaveValue(body)
  await user.click(screen.getByRole("button", { name: "保存草稿" }))
  await waitFor(() => expect(onChanged).toHaveBeenCalledWith(generatedReportId))

  expect(fetchMock).toHaveBeenCalledTimes(2)
  expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
    `/api/v1/evaluations/reports/${generatedReportId}/draft`,
    `/api/v1/evaluations/reports/${generatedReportId}/draft`,
  ])
  const requestBodies = fetchMock.mock.calls.map(([, init]) => String(init?.body))
  expect(requestBodies[1]).toBe(requestBodies[0])
  for (const [, init] of fetchMock.mock.calls) {
    expect(new Headers(init?.headers).get("Idempotency-Key")).toBe(
      "stable-editor-key",
    )
    expect(String(init?.body)).toContain(body)
  }
  expect(keyFactory).toHaveBeenCalledTimes(1)
  expect(idFactory).toHaveBeenCalledTimes(1)
  expect(
    JSON.stringify(
      queryClient.getMutationCache().getAll().map((mutation) =>
        mutation.state.variables,
      ),
    ),
  ).not.toContain(body)
  expect(onClose).toHaveBeenCalledTimes(1)
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

it("uses a fresh idempotency key after a typed failure", async () => {
  const fetchMock = vi
    .fn<typeof fetch>()
    .mockResolvedValueOnce(
      jsonResponse(
        {
          type: "about:blank",
          title: "Unprocessable Entity",
          status: 422,
          detail: "private validation detail",
          code: "evaluation_report_invalid",
        },
        422,
      ),
    )
    .mockResolvedValueOnce(jsonResponse(draftResponse()))
  vi.stubGlobal("fetch", fetchMock)
  const keyFactory = vi
    .fn<() => string>()
    .mockReturnValueOnce("typed-attempt-1")
    .mockReturnValueOnce("typed-attempt-2")
  renderEditor(newSlot, { idempotencyKeyFactory: keyFactory })
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("评语正文"), "可重新尝试的正文")

  await user.click(screen.getByRole("button", { name: "保存草稿" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("提交内容无效")
  await user.click(screen.getByRole("button", { name: "保存草稿" }))

  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
  expect(
    fetchMock.mock.calls.map(([, init]) =>
      new Headers(init?.headers).get("Idempotency-Key"),
    ),
  ).toEqual(["typed-attempt-1", "typed-attempt-2"])
})

it("keeps the dialog and body visible on a typed conflict", async () => {
  const body = "发生并发冲突时保留的正文"
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse(
        {
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "private stale lock detail",
          code: "evaluation_report_conflict",
        },
        409,
      ),
    ),
  )
  let rerenderSlot: (slot: TeachingReportSlot) => void = () => undefined
  const onConflict = vi.fn(() => {
    rerenderSlot({
      ...savedDraftSlot,
      lock_version: 2,
      draft_revision: 2,
      draft_body: "服务端权威草稿",
    })
  })
  const onClose = vi.fn()
  ;({ rerenderSlot } = renderEditor(savedDraftSlot, { onConflict, onClose }))
  const user = userEvent.setup()
  const textarea = screen.getByLabelText("评语正文")
  await user.clear(textarea)
  await user.type(textarea, body)

  await user.click(screen.getByRole("button", { name: "保存草稿" }))

  const dialog = screen.getByRole("dialog", { name: "编辑教学评语" })
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "刷新并比较后重试",
  )
  expect(screen.getByLabelText("评语正文")).toHaveValue(body)
  expect(onConflict).toHaveBeenCalledWith(reportId)
  expect(onClose).not.toHaveBeenCalled()
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

it.each([
  [rejectedSlot, "开始整改", "correction-drafts"],
  [publishedSlot, "发起修订", "revisions"],
] as const)(
  "refreshes and closes after starting a server-authorized draft",
  async (slot, buttonName, operation) => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      jsonResponse({
        report_id: reportId,
        status: slot.status === "rejected" ? "correction_draft" : "published",
        lock_version: 2,
        draft_revision: 1,
        body: "服务端新草稿",
        dimension_values: {},
        idempotent: false,
      }),
    )
    vi.stubGlobal("fetch", fetchMock)
    const onChanged = vi.fn()
    const onClose = vi.fn()
    renderEditor(slot, { onChanged, onClose })
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: buttonName }))

    await waitFor(() => expect(onChanged).toHaveBeenCalledWith(reportId))
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(`/${operation}`)
    expect(onClose).toHaveBeenCalledTimes(1)
  },
)
