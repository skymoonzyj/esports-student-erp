import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import * as travelReportsApi from "./api"
import type { TravelReport } from "./api"
import { TravelReportsPage } from "./TravelReportsPage"

const sampleReport: TravelReport = {
  id: "10000000-0000-4000-8000-000000000001",
  original_filename: "飞巴商旅原始账单.xlsx",
  converted_filename: "飞巴商旅26年8月账单-苍渊.xlsx",
  source_sha256: "a".repeat(64),
  converted_sha256: "b".repeat(64),
  source_byte_size: 8192,
  converted_byte_size: 6144,
  company_name: "杭州艾及帝文化创意有限公司（飞书）",
  cost_center: "苍渊",
  billing_period_start: "2026-08-01",
  billing_period_end: "2026-08-31",
  domestic_air_count: 29,
  international_air_count: 0,
  train_count: 0,
  source_total_amount: "30260.00",
  converted_total_amount: "30260.00",
  warnings: [],
  imported_by_user_id: "20000000-0000-4000-8000-000000000001",
  imported_by_name: "张英君",
  created_at: "2026-09-02T08:00:00Z",
}

const sampleFilterOptions = {
  billing_periods: [
    {
      billing_period_start: "2026-08-01",
      billing_period_end: "2026-08-31",
    },
  ],
  cost_centers: ["苍渊", "露神"],
}

function renderPage(canImport = true, canExport = true) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TravelReportsPage canImport={canImport} canExport={canExport} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.restoreAllMocks()
})

it("shows conversion history, totals and download actions", async () => {
  vi.spyOn(travelReportsApi, "listTravelReports").mockResolvedValue({
    items: [sampleReport],
    page: 1,
    page_size: 30,
    total: 1,
    filter_options: sampleFilterOptions,
  })
  renderPage()

  expect(await screen.findByRole("heading", { name: "订票报表转换" })).toBeVisible()
  expect(await screen.findByText("国内机票 29")).toBeVisible()
  expect(screen.getAllByText(/30,260\.00/).length).toBeGreaterThan(0)
  expect(screen.getByText("金额一致")).toBeVisible()
  expect(screen.getByRole("button", { name: "转换结果" })).toBeVisible()
  expect(screen.getByRole("button", { name: "原始账单" })).toBeVisible()
})

it("uploads an xlsx and reports conversion warnings", async () => {
  vi.spyOn(travelReportsApi, "listTravelReports").mockResolvedValue({
    items: [],
    page: 1,
    page_size: 30,
    total: 0,
    filter_options: sampleFilterOptions,
  })
  vi.spyOn(travelReportsApi, "importTravelReport").mockResolvedValue({
    ...sampleReport,
    warnings: ["国内酒店有 1 条明细，当前目标模板尚未定义该区块，已保留在原始文件中。"],
  })
  const user = userEvent.setup()
  renderPage()

  const file = new File(["xlsx"], "billing.xlsx", {
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  })
  await user.upload(await screen.findByLabelText("选择飞巴商旅账单"), file)

  await waitFor(() => expect(travelReportsApi.importTravelReport).toHaveBeenCalled())
  expect(vi.mocked(travelReportsApi.importTravelReport).mock.calls[0][0]).toBe(file)
  expect(await screen.findByRole("status")).toHaveTextContent("发现 1 项需核对内容")
})

it("hides mutating and download controls when permissions are absent", async () => {
  vi.spyOn(travelReportsApi, "listTravelReports").mockResolvedValue({
    items: [sampleReport],
    page: 1,
    page_size: 30,
    total: 1,
    filter_options: sampleFilterOptions,
  })
  renderPage(false, false)

  await screen.findByText("国内机票 29")
  expect(screen.queryByRole("button", { name: "上传账单" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "转换结果" })).not.toBeInTheDocument()
  expect(screen.getByText("无下载权限")).toBeVisible()
})

it("filters the full report history by billing period and cost center", async () => {
  const listReports = vi.spyOn(travelReportsApi, "listTravelReports").mockResolvedValue({
    items: [sampleReport],
    page: 1,
    page_size: 30,
    total: 1,
    filter_options: sampleFilterOptions,
  })
  const user = userEvent.setup()
  renderPage()

  await screen.findByText("国内机票 29")
  await user.selectOptions(screen.getByLabelText("账期"), "2026-08-01|2026-08-31")
  await waitFor(() => expect(listReports).toHaveBeenLastCalledWith(1, 30, {
    billingPeriodStart: "2026-08-01",
    billingPeriodEnd: "2026-08-31",
  }))

  await user.selectOptions(screen.getByLabelText("成本中心"), "苍渊")
  await waitFor(() => expect(listReports).toHaveBeenLastCalledWith(1, 30, {
    billingPeriodStart: "2026-08-01",
    billingPeriodEnd: "2026-08-31",
    costCenter: "苍渊",
  }))
  expect(screen.getByText("筛选结果")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "重置筛选" }))
  await waitFor(() => expect(listReports).toHaveBeenLastCalledWith(1, 30, {}))
  expect(screen.getByText("历史导入")).toBeVisible()
})
