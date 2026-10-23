import { apiFetch, apiFetchResponse } from "../api/client"

export type ProcurementSettlementStatus = "settled" | "unsettled"
export type ProcurementReimbursementStatus = "reimbursed" | "unreimbursed"
export type ProcurementInvoiceStatus = "invoiced" | "uninvoiced"
export type ProcurementExportFormat = "xlsx" | "csv"
export type ProcurementExportScope =
  | "current"
  | "all"
  | "unreimbursed"
  | "reimbursed"
  | "unsettled"
  | "settled"
export type ProcurementPurchaseChannel = "online" | "offline"

export type ProcurementInvoiceSummary = {
  id: string
  amount_cents: number
  invoice_date: string
  invoice_number: string | null
  original_filename: string
  mime_type: string
  byte_size: number
  created_at: string
}

export type SettlementCenter = {
  id: string
  name: string
  is_active: boolean
  reserve_fund_total_cents: number
  reserve_fund_used_cents: number
  reserve_fund_remaining_cents: number
  created_at: string
  updated_at: string
}

export type PurchaseRecord = {
  id: string
  purchase_date: string
  item_name: string
  purchase_channel: ProcurementPurchaseChannel
  product_url: string | null
  quantity: string
  unit_price_cents: number
  total_price_cents: number
  settlement_center_id: string
  settlement_center_name: string
  uses_reserve_fund: boolean
  is_reimbursed: boolean
  is_settled: boolean
  settled_on: string | null
  has_invoice: boolean
  invoice: ProcurementInvoiceSummary | null
  note: string | null
  created_at: string
  updated_at: string
}

export type PurchasePage = {
  items: PurchaseRecord[]
  page: number
  page_size: number
  total: number
  total_amount_cents: number
  reimbursed_amount_cents: number
  unreimbursed_amount_cents: number
  settled_amount_cents: number
  unsettled_amount_cents: number
}

export type PurchaseFilters = {
  query?: string
  settlementCenterId?: string
  reimbursementStatus?: ProcurementReimbursementStatus | ""
  settlementStatus?: ProcurementSettlementStatus | ""
  invoiceStatus?: ProcurementInvoiceStatus | ""
  startDate?: string
  endDate?: string
  page?: number
  pageSize?: number
}

export type PurchaseInput = {
  purchase_date: string
  item_name: string
  purchase_channel: ProcurementPurchaseChannel
  product_url: string | null
  quantity: string
  unit_price_cents: number
  settlement_center_id: string
  uses_reserve_fund: boolean
  note: string | null
}

export type PurchaseCreateInput = PurchaseInput & {
  operation_key: string
  is_reimbursed: boolean
  is_settled: boolean
  settled_on: string | null
  add_to_assets: boolean
  asset_category: string | null
  asset_warranty_expires_on: string | null
}

export type PurchaseExportInput = PurchaseFilters & {
  format: ProcurementExportFormat
  scope: ProcurementExportScope
  purpose: string
}

export type InvoiceMatchCandidate = {
  id: string
  purchase_date: string
  item_name: string
  total_price_cents: number
  settlement_center_id: string
  settlement_center_name: string
}

export type InvoiceMatchSuggestion = {
  amount_cents: number
  matched_total_cents: number
  suggested_purchase_ids: string[]
  candidates: InvoiceMatchCandidate[]
}

export type InvoiceUploadInput = {
  file: File
  amountCents: number
  invoiceDate: string
  invoiceNumber?: string
  note?: string
  purchaseIds: string[]
}

export function listSettlementCenters(includeInactive = false): Promise<SettlementCenter[]> {
  const params = new URLSearchParams()
  if (includeInactive) params.set("include_inactive", "true")
  const suffix = params.size > 0 ? `?${params.toString()}` : ""
  return apiFetch<SettlementCenter[]>(`/procurement/settlement-centers${suffix}`)
}

export function createSettlementCenter(name: string): Promise<SettlementCenter> {
  return apiFetch<SettlementCenter>("/procurement/settlement-centers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  })
}

export function updateSettlementCenter(
  centerId: string,
  input: { name?: string; is_active?: boolean },
): Promise<SettlementCenter> {
  return apiFetch<SettlementCenter>(
    `/procurement/settlement-centers/${encodeURIComponent(centerId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  )
}

export function addSettlementCenterReserveFund(
  centerId: string,
  input: { operation_key: string; amount_cents: number; note: string | null },
): Promise<SettlementCenter> {
  return apiFetch<SettlementCenter>(
    `/procurement/settlement-centers/${encodeURIComponent(centerId)}/reserve-fund-entries`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  )
}

function purchaseParams(filters: PurchaseFilters): URLSearchParams {
  const params = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 50),
  })
  if (filters.query) params.set("query", filters.query)
  if (filters.settlementCenterId) {
    params.set("settlement_center_id", filters.settlementCenterId)
  }
  if (filters.settlementStatus) {
    params.set("settlement_status", filters.settlementStatus)
  }
  if (filters.reimbursementStatus) {
    params.set("reimbursement_status", filters.reimbursementStatus)
  }
  if (filters.invoiceStatus) params.set("invoice_status", filters.invoiceStatus)
  if (filters.startDate) params.set("start_date", filters.startDate)
  if (filters.endDate) params.set("end_date", filters.endDate)
  return params
}

export function findInvoiceMatches(
  amountCents: number,
  settlementCenterId?: string,
): Promise<InvoiceMatchSuggestion> {
  const params = new URLSearchParams({ amount_cents: String(amountCents) })
  if (settlementCenterId) params.set("settlement_center_id", settlementCenterId)
  return apiFetch<InvoiceMatchSuggestion>(
    `/procurement/invoices/match-suggestions?${params.toString()}`,
  )
}

export function uploadProcurementInvoice(
  input: InvoiceUploadInput,
): Promise<ProcurementInvoiceSummary> {
  const body = new FormData()
  body.set("file", input.file)
  body.set("amount_cents", String(input.amountCents))
  body.set("invoice_date", input.invoiceDate)
  if (input.invoiceNumber) body.set("invoice_number", input.invoiceNumber)
  if (input.note) body.set("note", input.note)
  for (const purchaseId of input.purchaseIds) body.append("purchase_ids", purchaseId)
  return apiFetch<ProcurementInvoiceSummary>("/procurement/invoices", {
    method: "POST",
    body,
  })
}

function responseFilename(response: Response, fallback: string): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const match = /filename="([A-Za-z0-9._-]+)"/.exec(disposition)
  return match?.[1] ?? fallback
}

export async function downloadProcurementInvoice(
  invoiceId: string,
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiFetchResponse(
    `/procurement/invoices/${encodeURIComponent(invoiceId)}/file`,
  )
  return {
    blob: await response.blob(),
    filename: responseFilename(response, `procurement-invoice-${invoiceId}`),
  }
}

export function removeProcurementInvoice(invoiceId: string): Promise<void> {
  return apiFetch<void>(`/procurement/invoices/${encodeURIComponent(invoiceId)}`, {
    method: "DELETE",
  })
}

export function listPurchases(filters: PurchaseFilters = {}): Promise<PurchasePage> {
  return apiFetch<PurchasePage>(`/procurement?${purchaseParams(filters).toString()}`)
}

export function createPurchase(input: PurchaseCreateInput): Promise<PurchaseRecord> {
  return apiFetch<PurchaseRecord>("/procurement", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function updatePurchase(
  purchaseId: string,
  input: PurchaseInput,
): Promise<PurchaseRecord> {
  return apiFetch<PurchaseRecord>(`/procurement/${encodeURIComponent(purchaseId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function updatePurchaseSettlement(
  purchaseId: string,
  input: { is_settled: boolean; settled_on: string | null },
): Promise<PurchaseRecord> {
  return apiFetch<PurchaseRecord>(
    `/procurement/${encodeURIComponent(purchaseId)}/settlement`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  )
}

export function updatePurchaseReimbursement(
  purchaseId: string,
  isReimbursed: boolean,
): Promise<PurchaseRecord> {
  return apiFetch<PurchaseRecord>(
    `/procurement/${encodeURIComponent(purchaseId)}/reimbursement`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_reimbursed: isReimbursed }),
    },
  )
}

function attachmentFilename(response: Response, format: ProcurementExportFormat): string {
  const disposition = response.headers.get("Content-Disposition") ?? ""
  const match = /filename="([A-Za-z0-9._-]+)"/.exec(disposition)
  const extension = format === "xlsx" ? ".xlsx" : ".csv"
  if (!match || !match[1].endsWith(extension)) throw new Error("导出文件无效")
  return match[1]
}

export async function exportPurchases(
  input: PurchaseExportInput,
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiFetchResponse("/procurement/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      format: input.format,
      scope: input.scope,
      purpose: input.purpose,
      query: input.query || undefined,
      settlement_center_id: input.settlementCenterId || undefined,
      reimbursement_status: input.reimbursementStatus || undefined,
      settlement_status: input.settlementStatus || undefined,
      invoice_status: input.invoiceStatus || undefined,
      start_date: input.startDate || undefined,
      end_date: input.endDate || undefined,
    }),
  })
  return {
    blob: await response.blob(),
    filename: attachmentFilename(response, input.format),
  }
}
