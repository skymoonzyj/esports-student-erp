import { apiFetch, apiFetchResponse } from "../api/client"

export type TravelReport = {
  id: string
  original_filename: string
  converted_filename: string
  source_sha256: string
  converted_sha256: string
  source_byte_size: number
  converted_byte_size: number
  company_name: string
  cost_center: string
  billing_period_start: string
  billing_period_end: string
  domestic_air_count: number
  international_air_count: number
  train_count: number
  source_total_amount: string
  converted_total_amount: string
  warnings: string[]
  imported_by_user_id: string
  imported_by_name: string
  created_at: string
}

export type TravelReportPage = {
  items: TravelReport[]
  page: number
  page_size: number
  total: number
  filter_options: {
    billing_periods: Array<{
      billing_period_start: string
      billing_period_end: string
    }>
    cost_centers: string[]
  }
}

export type TravelReportFilters = {
  billingPeriodStart?: string
  billingPeriodEnd?: string
  costCenter?: string
}

export const travelReportKeys = {
  all: ["travel-reports"] as const,
  page: (page: number, filters: TravelReportFilters = {}) => (
    ["travel-reports", "page", page, filters] as const
  ),
}

export function listTravelReports(
  page = 1,
  pageSize = 30,
  filters: TravelReportFilters = {},
): Promise<TravelReportPage> {
  const params = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (filters.billingPeriodStart) {
    params.set("billing_period_start", filters.billingPeriodStart)
  }
  if (filters.billingPeriodEnd) {
    params.set("billing_period_end", filters.billingPeriodEnd)
  }
  if (filters.costCenter) params.set("cost_center", filters.costCenter)
  return apiFetch<TravelReportPage>(`/travel-reports?${params.toString()}`)
}

export function importTravelReport(file: File): Promise<TravelReport> {
  const body = new FormData()
  body.set("file", file)
  return apiFetch<TravelReport>("/travel-reports/imports", { method: "POST", body })
}

function responseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1]
  if (encoded) {
    try {
      return decodeURIComponent(encoded)
    } catch {
      return fallback
    }
  }
  return /filename="?([^";]+)"?/i.exec(disposition)?.[1] ?? fallback
}

export async function downloadTravelReport(
  reportId: string,
  kind: "source" | "converted",
  fallback: string,
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiFetchResponse(
    `/travel-reports/${encodeURIComponent(reportId)}/${kind}`,
  )
  return {
    blob: await response.blob(),
    filename: responseFilename(response, fallback),
  }
}
