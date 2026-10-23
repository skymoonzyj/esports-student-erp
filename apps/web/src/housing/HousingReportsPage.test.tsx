import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import {
  downloadHousingReport,
  generateHousingReport,
  getHousingReport,
  listHousingReports,
  markHousingReportSubmitted,
  saveDownloadedBlob,
  voidHousingReport,
} from "./reportingApi"
import type {
  HousingReportBatchSummary,
  HousingReportListResponse,
} from "./reportingApi"
import { HousingReportsPage } from "./HousingReportsPage"
import { reportingKeys } from "./reportingKeys"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      account_id: "account-admin",
      permissions: ["housing.reports.export_sensitive"],
      scoped_permissions: [],
    },
  }),
}))

vi.mock(import("./reportingApi"), async (importOriginal) => {
  const actual = await importOriginal()
  return {
    ...actual,
    listHousingReports: vi.fn(),
    getHousingReport: vi.fn(),
    generateHousingReport: vi.fn(),
    markHousingReportSubmitted: vi.fn(),
    voidHousingReport: vi.fn(),
    downloadHousingReport: vi.fn(),
    saveDownloadedBlob: vi.fn(),
  }
})

const listMock = vi.mocked(listHousingReports)
const detailMock = vi.mocked(getHousingReport)
const generateMock = vi.mocked(generateHousingReport)
const submittedMock = vi.mocked(markHousingReportSubmitted)
const voidMock = vi.mocked(voidHousingReport)
const downloadMock = vi.mocked(downloadHousingReport)
const saveMock = vi.mocked(saveDownloadedBlob)

const GENERATED_BATCH: HousingReportBatchSummary = {
  id: "batch-1",
  batch_number: "HR-20260806-000001",
  status: "generated",
  version: 1,
  range_start: "2026-08-01T00:00:00+08:00",
  range_end: "2026-08-22T00:00:00+08:00",
  cohort_filter: ["cohort-1"],
  person_type_filter: ["student"],
  purpose: "派出所人工报送",
  resident_count: 12,
  stay_row_count: 13,
  guardian_row_count: 4,
  scopes: [{
    building_id: "building-1",
    building_name: "1号楼",
    floor_id: "floor-1",
    floor_name: "二层",
  }],
  created_at: "2026-08-06T12:00:00+08:00",
  created_by_user_id: "account-admin",
  created_by_name: "管理员",
  submitted_at: null,
  submitted_by_user_id: null,
  submitted_by_name: null,
  voided_at: null,
  voided_by_user_id: null,
  voided_by_name: null,
  corrects_batch_id: null,
  failure_code: null,
  allowed_actions: {
    download: true,
    mark_submitted: true,
    void: true,
    correct: false,
  },
}

function listResponse(items = [GENERATED_BATCH]): HousingReportListResponse {
  return {
    items,
    page: 1,
    page_size: 20,
    total: items.length,
    status_counts: {
      generated: items.filter((item) => item.status === "generated").length,
      marked_submitted: items.filter((item) => item.status === "marked_submitted").length,
      voided: items.filter((item) => item.status === "voided").length,
      failed: items.filter((item) => item.status === "failed").length,
    },
    access_revision: "a".repeat(64),
    filter_options: {
      buildings: [{ id: "building-1", name: "1号楼" }],
      floors: [{ id: "floor-1", building_id: "building-1", name: "二层" }],
      cohorts: [{ id: "cohort-1", name: "英雄联盟一期" }],
    },
  }
}

function setWidth(width: number): void {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width })
  window.dispatchEvent(new Event("resize"))
}

function renderPage(width: number): QueryClient {
  setWidth(width)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <HousingReportsPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return client
}

beforeEach(() => {
  listMock.mockResolvedValue(listResponse())
  detailMock.mockResolvedValue({
    ...GENERATED_BATCH,
    submission_note: null,
    void_reason: null,
    correction_chain: [],
    failure_summary: null,
  })
  generateMock.mockResolvedValue(GENERATED_BATCH)
  submittedMock.mockResolvedValue({
    ...GENERATED_BATCH,
    status: "marked_submitted",
    version: 2,
    submitted_at: "2026-08-06T20:30:00+08:00",
  })
  voidMock.mockResolvedValue({
    ...GENERATED_BATCH,
    status: "voided",
    version: 2,
    voided_at: "2026-08-06T21:00:00+08:00",
  })
  downloadMock.mockResolvedValue({
    blob: new Blob(["report"]),
    filename: "housing-report-HR-20260806-000001.xlsx",
  })
})

afterEach(() => {
  vi.clearAllMocks()
})

it("renders desktop summary and table actions", async () => {
  renderPage(1024)

  expect(await screen.findByRole("heading", { name: "实名入住报送" })).toBeVisible()
  expect(await screen.findByRole("table", { name: "实名报送批次" })).toBeVisible()
  expect(screen.queryByRole("article", { name: /HR-20260806-000001/ })).not.toBeInTheDocument()
  const summary = screen.getByRole("region", { name: "实名报送汇总" })
  expect(within(summary).getByText("待人工报送").parentElement).toHaveTextContent("1")
  expect(screen.getByRole("button", { name: "下载 HR-20260806-000001 Excel" })).toBeVisible()
  expect(screen.getByRole("button", { name: "标记 HR-20260806-000001 已报送" })).toBeVisible()
})

it("renders one-column report cards on mobile without the desktop table", async () => {
  renderPage(360)

  const card = await screen.findByRole("article", { name: /HR-20260806-000001/ })
  expect(card).toBeVisible()
  expect(screen.queryByRole("table", { name: "实名报送批次" })).not.toBeInTheDocument()
  expect(within(card).getByText("12 人 / 13 条住宿")).toBeVisible()
})

it("exports a floor-scoped report and restores focus", async () => {
  const user = userEvent.setup()
  renderPage(1024)
  const opener = await screen.findByRole("button", { name: "导出实名名单" })

  await user.click(opener)
  const dialog = screen.getByRole("dialog", { name: "导出实名名单" })
  await user.clear(within(dialog).getByLabelText("开始日期"))
  await user.type(within(dialog).getByLabelText("开始日期"), "2026-08-01")
  await user.clear(within(dialog).getByLabelText("结束日期"))
  await user.type(within(dialog).getByLabelText("结束日期"), "2026-08-22")
  await user.click(within(dialog).getByLabelText("指定楼层"))
  await user.click(within(dialog).getByLabelText("二层（1号楼）"))
  await user.type(within(dialog).getByLabelText("导出用途（必填）"), "派出所人工报送")
  await user.click(within(dialog).getByRole("button", { name: "确认导出" }))

  await waitFor(() => expect(generateMock).toHaveBeenCalled())
  expect(generateMock.mock.calls[0]?.[0]).toEqual(expect.objectContaining({
    scope_mode: "floors",
    floor_ids: ["floor-1"],
    purpose: "派出所人工报送",
  }))
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "导出实名名单" })).not.toBeInTheDocument())
  await waitFor(() => expect(opener).toHaveFocus())
})

it("retains generation input and shows safe problem details after rejection", async () => {
  generateMock.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank",
    title: "资料不完整",
    status: 422,
    detail: "资料不完整，已生成失败批次",
    code: "housing_export_incomplete",
    request_id: "request-safe",
    errors: [{ field: "guardian.phone", message: "学员甲 138****8000 缺少监护人手机号" }],
  }))
  const user = userEvent.setup()
  renderPage(1024)

  await user.click(await screen.findByRole("button", { name: "导出实名名单" }))
  const dialog = screen.getByRole("dialog", { name: "导出实名名单" })
  await user.type(within(dialog).getByLabelText("导出用途（必填）"), "公安窗口补报")
  await user.click(within(dialog).getByRole("button", { name: "确认导出" }))

  expect(await within(dialog).findByText("资料不完整，已生成失败批次")).toBeVisible()
  expect(within(dialog).getByLabelText("导出用途（必填）")).toHaveValue("公安窗口补报")
  expect(within(dialog).getByText("学员甲 138****8000 缺少监护人手机号")).toBeVisible()
  expect(listMock).toHaveBeenCalledTimes(2)
})

it("downloads and saves the selected immutable snapshot", async () => {
  const user = userEvent.setup()
  renderPage(1024)

  await user.click(await screen.findByRole("button", {
    name: "下载 HR-20260806-000001 Excel",
  }))

  await waitFor(() => expect(downloadMock).toHaveBeenCalledWith("batch-1", "xlsx"))
  expect(saveMock).toHaveBeenCalledWith(
    expect.any(Blob),
    "housing-report-HR-20260806-000001.xlsx",
  )
})

it("invalidates the list root and exact detail after a state mutation", async () => {
  const user = userEvent.setup()
  const client = renderPage(1024)
  const invalidate = vi.spyOn(client, "invalidateQueries")
  const detailKey = reportingKeys.detail(
    "account-admin",
    "a".repeat(64),
    "batch-1",
  )
  client.setQueryData(detailKey, { ...GENERATED_BATCH, submission_note: null })

  await user.click(await screen.findByRole("button", {
    name: "标记 HR-20260806-000001 已报送",
  }))
  const dialog = screen.getByRole("dialog", { name: "标记人工报送" })
  await user.type(
    within(dialog).getByLabelText("报送日期"),
    "2026-08-06",
  )
  await user.click(within(dialog).getByRole("button", { name: "确认已报送" }))

  await waitFor(() => expect(submittedMock).toHaveBeenCalledOnce())
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: reportingKeys.lists("account-admin", "current"),
  })
  expect(invalidate).toHaveBeenCalledWith({
    queryKey: detailKey,
    exact: true,
  })
})

it.each([1440, 360])("uses the submitted response for immediate void while list refresh is slow at %i", async (width) => {
  const submitted: HousingReportBatchSummary = {
    ...GENERATED_BATCH,
    status: "marked_submitted",
    version: 2,
    submitted_at: "2026-08-06T20:30:00+08:00",
    submitted_by_user_id: "account-admin",
    submitted_by_name: "管理员",
    allowed_actions: { download: true, mark_submitted: false, void: true, correct: false },
  }
  let finishRefresh!: (response: HousingReportListResponse) => void
  const delayedList = new Promise<HousingReportListResponse>((resolve) => {
    finishRefresh = resolve
  })
  listMock.mockResolvedValueOnce(listResponse()).mockReturnValue(delayedList)
  submittedMock.mockResolvedValueOnce(submitted)
  const voided: HousingReportBatchSummary = {
    ...submitted,
    status: "voided",
    version: 3,
    voided_at: "2026-08-06T21:00:00+08:00",
    voided_by_user_id: "account-admin",
    voided_by_name: "管理员",
    allowed_actions: { download: false, mark_submitted: false, void: false, correct: true },
  }
  voidMock.mockResolvedValueOnce(voided)
  const user = userEvent.setup()
  renderPage(width)

  await user.click(await screen.findByRole("button", { name: "标记 HR-20260806-000001 已报送" }))
  const submittedDialog = screen.getByRole("dialog", { name: "标记人工报送" })
  await user.type(within(submittedDialog).getByLabelText("报送日期"), "2026-08-06")
  await user.click(within(submittedDialog).getByRole("button", { name: "确认已报送" }))
  await screen.findByText("批次 HR-20260806-000001 已标记报送")
  await waitFor(() => expect(listMock).toHaveBeenCalledTimes(2))

  await user.click(screen.getByRole("button", { name: "作废 HR-20260806-000001" }))
  const voidDialog = screen.getByRole("dialog", { name: "作废实名批次" })
  await user.type(within(voidDialog).getByLabelText("作废原因"), "窗口数据有误")
  await user.click(within(voidDialog).getByRole("button", { name: "确认作废" }))

  await waitFor(() => expect(voidMock).toHaveBeenCalledWith("batch-1", {
    reason: "窗口数据有误",
    version: 2,
  }))
  expect(await screen.findByRole("button", { name: "更正 HR-20260806-000001" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "作废 HR-20260806-000001" })).not.toBeInTheDocument()
  await act(async () => finishRefresh(listResponse([voided])))
})

it("preserves an open form version and reports a genuine external conflict without retry", async () => {
  voidMock.mockRejectedValueOnce(new ProblemDetailsError({
    type: "about:blank",
    title: "Conflict",
    status: 409,
    detail: "报送批次状态已发生变化",
    code: "housing_report_state_conflict",
    request_id: "request-safe",
  }))
  const user = userEvent.setup()
  const client = renderPage(1440)
  await user.click(await screen.findByRole("button", { name: "作废 HR-20260806-000001" }))
  const dialog = screen.getByRole("dialog", { name: "作废实名批次" })
  await user.type(within(dialog).getByLabelText("作废原因"), "核对发现错误")
  act(() => {
    client.setQueryData(
      reportingKeys.list("account-admin", "current", { page: 1, pageSize: 20 }),
      listResponse([{
        ...GENERATED_BATCH,
        status: "marked_submitted",
        version: 2,
        submitted_at: "2026-08-06T20:30:00+08:00",
        submitted_by_user_id: "account-admin",
        submitted_by_name: "管理员",
        allowed_actions: { download: true, mark_submitted: false, void: true, correct: false },
      }]),
    )
  })
  await user.click(within(dialog).getByRole("button", { name: "确认作废" }))

  expect(await within(dialog).findByRole("alert")).toHaveTextContent("报送批次状态已发生变化")
  expect(within(dialog).getByLabelText("作废原因")).toHaveValue("核对发现错误")
  expect(voidMock).toHaveBeenCalledExactlyOnceWith("batch-1", { reason: "核对发现错误", version: 1 })
  expect(screen.queryByText("批次 HR-20260806-000001 已作废")).not.toBeInTheDocument()
})

it("does not replace a newer refetched batch with a delayed mutation response", async () => {
  const submitted: HousingReportBatchSummary = {
    ...GENERATED_BATCH,
    status: "marked_submitted",
    version: 2,
    submitted_at: "2026-08-06T20:30:00+08:00",
    submitted_by_user_id: "account-admin",
    submitted_by_name: "管理员",
    allowed_actions: { download: true, mark_submitted: false, void: true, correct: false },
  }
  const voided: HousingReportBatchSummary = {
    ...submitted,
    status: "voided",
    version: 3,
    voided_at: "2026-08-06T21:00:00+08:00",
    voided_by_user_id: "other-admin",
    voided_by_name: "另一管理员",
    allowed_actions: { download: false, mark_submitted: false, void: false, correct: true },
  }
  let finishMutation!: (batch: HousingReportBatchSummary) => void
  submittedMock.mockReturnValueOnce(new Promise((resolve) => { finishMutation = resolve }))
  let finishRefresh!: (response: HousingReportListResponse) => void
  const delayedRefresh = new Promise<HousingReportListResponse>((resolve) => {
    finishRefresh = resolve
  })
  listMock.mockResolvedValueOnce(listResponse())
    .mockResolvedValueOnce(listResponse([voided]))
    .mockReturnValue(delayedRefresh)
  const user = userEvent.setup()
  const client = renderPage(1440)
  const listKey = reportingKeys.list("account-admin", "current", { page: 1, pageSize: 20 })
  await user.click(await screen.findByRole("button", { name: "标记 HR-20260806-000001 已报送" }))
  const dialog = screen.getByRole("dialog", { name: "标记人工报送" })
  await user.type(within(dialog).getByLabelText("报送日期"), "2026-08-06")
  await user.click(within(dialog).getByRole("button", { name: "确认已报送" }))
  await waitFor(() => expect(submittedMock).toHaveBeenCalledOnce())

  await act(async () => { await client.refetchQueries({ queryKey: listKey, exact: true }) })
  expect(client.getQueryData<HousingReportListResponse>(listKey)?.items[0]?.version).toBe(3)
  await act(async () => finishMutation(submitted))
  await screen.findByText("批次 HR-20260806-000001 已标记报送")
  await waitFor(() => expect(listMock).toHaveBeenCalledTimes(3))

  expect(client.getQueryData<HousingReportListResponse>(listKey)?.items[0]?.version).toBe(3)
  expect(screen.getByRole("button", { name: "更正 HR-20260806-000001" })).toBeEnabled()
  expect(screen.queryByRole("button", { name: "作废 HR-20260806-000001" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "下载 HR-20260806-000001 Excel" })).not.toBeInTheDocument()
  await act(async () => finishRefresh(listResponse([voided])))
})
