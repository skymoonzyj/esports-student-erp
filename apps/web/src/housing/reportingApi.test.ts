import { afterEach, describe, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import {
  downloadHousingReport,
  generateHousingReport,
  getHousingReport,
  listHousingReports,
  markHousingReportSubmitted,
  safeAttachmentFilename,
  saveDownloadedBlob,
  voidHousingReport,
} from "./reportingApi"
import type { HousingReportIssue } from "./reportingApi"

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function jsonResponse(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("housing reporting API", () => {
  it("models the structured incomplete issue returned by the API", () => {
    const issue: HousingReportIssue = {
      field: "report_issue.person-1",
      message: "待补资料学员（138****0001，住宿 stay-1）缺少：phone",
      batch_id: "batch-failed-1",
      person_id: "person-1",
      person_name: "待补资料学员",
      masked_phone: "138****0001",
      stay_id: "stay-1",
      missing_fields: "phone",
    }

    expect(issue.missing_fields).toBe("phone")
  })

  it("serializes normalized list filters in backend order", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({}))
    vi.stubGlobal("fetch", fetchMock)

    await listHousingReports({
      statuses: ["voided", "generated", "generated"],
      rangeStart: "2026-08-01T00:00:00+08:00",
      rangeEnd: "2026-08-22T00:00:00+08:00",
      buildingIds: ["building-b", "building-a", "building-a"],
      floorIds: ["floor-b", "floor-a"],
      cohortIds: ["cohort-b", "cohort-a"],
      createdByUserId: "creator-1",
      page: 2,
      pageSize: 25,
    })

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/report-batches?status=generated&status=voided&range_start=2026-08-01T00%3A00%3A00%2B08%3A00&range_end=2026-08-22T00%3A00%3A00%2B08%3A00&building_id=building-a&building_id=building-b&floor_id=floor-a&floor_id=floor-b&cohort_id=cohort-a&cohort_id=cohort-b&created_by_user_id=creator-1&page=2&page_size=25",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("uses the private detail endpoint", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ id: "batch-1" }))
    vi.stubGlobal("fetch", fetchMock)

    await getHousingReport("batch/1")

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/report-batches/batch%2F1",
      expect.objectContaining({ credentials: "include" }),
    )
  })

  it("posts exact generation input without adding client-only values", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({ id: "batch-1" }))
    vi.stubGlobal("fetch", fetchMock)
    const input = {
      range_start: "2026-08-01T00:00:00+08:00",
      range_end: "2026-08-22T00:00:00+08:00",
      scope_mode: "floors" as const,
      building_ids: [],
      floor_ids: ["floor-1"],
      cohort_ids: ["cohort-1"],
      person_types: ["student" as const, "coach" as const],
      purpose: "派出所人工报送",
      corrects_batch_id: null,
    }

    await generateHousingReport(input)

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/report-batches",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(input),
        credentials: "include",
      }),
    )
  })

  it("posts versioned submitted and void actions", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockImplementation(
      async () => jsonResponse({ id: "batch-1" }),
    )
    vi.stubGlobal("fetch", fetchMock)

    await markHousingReportSubmitted("batch-1", {
      submitted_at: "2026-08-06T20:30:00+08:00",
      note: "窗口报送",
      version: 3,
    })
    await voidHousingReport("batch-1", { reason: "发现房号错误", version: 4 })

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/api/v1/housing/report-batches/batch-1/mark-submitted",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    )
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/api/v1/housing/report-batches/batch-1/void",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    )
  })

  it("downloads an attachment using a safe server filename", async () => {
    const bytes = new Uint8Array([80, 75, 3, 4])
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(new Response(bytes, {
      status: 200,
      headers: {
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": 'attachment; filename="housing-report-HR-20260806-000001.xlsx"',
        "Cache-Control": "no-store, private",
      },
    }))
    vi.stubGlobal("fetch", fetchMock)

    const result = await downloadHousingReport("batch-1", "xlsx")

    expect(result.filename).toBe("housing-report-HR-20260806-000001.xlsx")
    expect(result.blob.size).toBe(bytes.byteLength)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/housing/report-batches/batch-1/download?format=xlsx",
      expect.objectContaining({
        credentials: "include",
        headers: expect.any(Headers),
      }),
    )
  })

  it.each([
    ["", "fallback.zip"],
    ['attachment; filename="../../secret.xlsx"', "fallback.zip"],
    ['attachment; filename="unsafe name.xlsx"', "fallback.zip"],
    ['attachment; filename="safe-name_1.zip"', "safe-name_1.zip"],
  ])("sanitizes content disposition %s", (disposition, expected) => {
    expect(safeAttachmentFilename(disposition, "fallback.zip")).toBe(expected)
  })

  it("surfaces problem details instead of returning an error file", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(jsonResponse({
      type: "about:blank",
      title: "Not Found",
      detail: "房态资源不存在",
      code: "housing_resource_not_found",
      request_id: "request-1",
    }, 404)))

    const request = downloadHousingReport("hidden-batch", "csv")

    await expect(request).rejects.toBeInstanceOf(ProblemDetailsError)
    await expect(request).rejects.toMatchObject({
      problem: { status: 404, code: "housing_resource_not_found" },
    })
  })

  it("always revokes the temporary object URL after saving", () => {
    const click = vi.fn()
    const remove = vi.fn()
    const anchor = { click, remove, href: "", download: "" } as unknown as HTMLAnchorElement
    vi.spyOn(document, "createElement").mockReturnValue(anchor)
    const createObjectURL = vi.fn(() => "blob:report")
    const revokeObjectURL = vi.fn()
    vi.stubGlobal("URL", { createObjectURL, revokeObjectURL })

    saveDownloadedBlob(new Blob(["report"]), "housing-report.zip")

    expect(click).toHaveBeenCalledOnce()
    expect(remove).toHaveBeenCalledOnce()
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:report")
  })
})
