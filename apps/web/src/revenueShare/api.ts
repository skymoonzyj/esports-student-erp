import { apiFetch, apiFetchResponse } from "../api/client"

export type BeneficiaryKind = "person" | "organization"
export type RevenueShareRole =
  | "inviter"
  | "subject_coach"
  | "platform_developer"
  | "assistant"
  | "additional"
export type CalculationType = "percentage" | "fixed"

export type RevenueShareOrganization = {
  id: string
  name: string
  is_active: boolean
}

export type ReferralCode = {
  id: string
  code: string
  label: string
  source_kind: BeneficiaryKind
  source_name: string
  is_active: boolean
  registration_url: string
}

export type RevenueShareRule = {
  id: string
  project_id: string | null
  project_name: string | null
  role_kind: RevenueShareRole
  recipient_person_id: string | null
  recipient_organization_id: string | null
  recipient_name: string | null
  label: string | null
  calculation_type: CalculationType
  percentage_basis_points: number | null
  fixed_amount_cents: number | null
  effective_from: string
  effective_to: string | null
  is_active: boolean
}

export type DirectoryOption = { id: string; name: string }

export type RevenueShareConfiguration = {
  organizations: RevenueShareOrganization[]
  referral_codes: ReferralCode[]
  rules: RevenueShareRule[]
  people: DirectoryOption[]
  projects: DirectoryOption[]
}

export type RevenueShareBalance = {
  beneficiary_kind: BeneficiaryKind
  beneficiary_id: string
  beneficiary_name: string
  earned_cents: number
  settled_cents: number
  outstanding_cents: number
}

export type RevenueShareFinanceSummary = {
  earned_cents: number
  settled_cents: number
  outstanding_cents: number
  pending_payment_count: number
  items: RevenueShareBalance[]
}

export type RevenueShareLedgerItem = {
  id: string
  payment_id: string
  entry_kind: "earning" | "refund_adjustment" | "manual_adjustment"
  role_kind: RevenueShareRole
  amount_cents: number
  occurred_at: string
}

export type RevenueShareSelf = {
  earned_cents: number
  settled_cents: number
  outstanding_cents: number
  items: RevenueShareLedgerItem[]
  referral_codes: ReferralCode[]
  total: number
  page_size: number
  has_more: boolean
  next_cursor: string | null
}

export function getRevenueShareConfiguration(): Promise<RevenueShareConfiguration> {
  return apiFetch<RevenueShareConfiguration>("/revenue-share/configuration")
}

export function createRevenueShareOrganization(name: string): Promise<RevenueShareOrganization> {
  return apiFetch<RevenueShareOrganization>("/revenue-share/organizations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  })
}

export function createReferralCode(input: {
  code?: string
  label: string
  source_kind: BeneficiaryKind
  referrer_person_id: string | null
  referrer_organization_id: string | null
}): Promise<ReferralCode> {
  return apiFetch<ReferralCode>("/revenue-share/referral-codes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function createRevenueShareRule(input: {
  project_id: string | null
  role_kind: RevenueShareRole
  recipient_person_id: string | null
  recipient_organization_id: string | null
  label: string | null
  calculation_type: CalculationType
  percentage: string | null
  fixed_amount: string | null
  effective_from: string
  effective_to: string | null
}): Promise<RevenueShareRule> {
  return apiFetch<RevenueShareRule>("/revenue-share/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function disableRevenueShareRule(ruleId: string): Promise<{ id: string; is_active: false }> {
  return apiFetch<{ id: string; is_active: false }>(
    `/revenue-share/rules/${encodeURIComponent(ruleId)}/disable`,
    { method: "POST" },
  )
}

export type RevenueShareReconcileResult = {
  processed: number
  allocated: number
  pending: number
  has_more: boolean
  next_cursor: string | null
}

export function reconcileRevenueShare(input?: {
  batch_size: number
  cursor: string | null
}): Promise<RevenueShareReconcileResult> {
  return apiFetch("/revenue-share/reconcile", {
    method: "POST",
    ...(input ? {
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    } : {}),
  })
}

export function getRevenueShareFinanceSummary(): Promise<RevenueShareFinanceSummary> {
  return apiFetch<RevenueShareFinanceSummary>("/revenue-share/finance")
}

export type RevenueShareSettlementInput = {
  idempotency_key: string
  beneficiary_kind: BeneficiaryKind
  beneficiary_id: string
  amount_cents: number
  settled_on: string
  reference: string
  note: string | null
}

export function createRevenueShareSettlement(input: RevenueShareSettlementInput): Promise<unknown> {
  return apiFetch("/revenue-share/settlements", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export async function exportRevenueShareSummary(purpose: string): Promise<void> {
  const response = await apiFetchResponse("/revenue-share/finance/exports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ purpose }),
  })
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = "revenue-share-summary.csv"
  anchor.click()
  URL.revokeObjectURL(url)
}

export function getMyRevenueShare(input: {
  page_size?: number
  cursor?: string | null
} = {}): Promise<RevenueShareSelf> {
  const parameters = new URLSearchParams({ page_size: String(input.page_size ?? 20) })
  if (input.cursor) parameters.set("cursor", input.cursor)
  return apiFetch<RevenueShareSelf>(`/revenue-share/me?${parameters}`)
}

export function ensureMyReferralCode(): Promise<ReferralCode> {
  return apiFetch<ReferralCode>("/revenue-share/me/referral-code", {
    method: "PUT",
  })
}
