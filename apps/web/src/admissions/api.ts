import { apiFetch } from "../api/client"

export { ProblemDetailsError } from "../api/client"

export type Page<T> = {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type PaymentStatus = "unpaid" | "partial" | "paid" | "overpaid" | "refunded"
export type AdmissionCaseStatus = "pending_match" | "ready" | "enrolled" | "closed"
export type EnrollmentStatus = "active" | "cancelled" | "completed"
export type RefundPolicy = "prorated_remaining_days" | "full_remaining_paid"
export type PaymentSourceKind = "api" | "manual"
export type RefundType = "manual" | "withdrawal_prorated" | "withdrawal_full"
export type PaymentConflictStatus = "open" | "resolved" | "dismissed"
export type PaymentConflictType =
  | "external_order_payload_mismatch"
  | "idempotency_payload_mismatch"
  | "ambiguous_payer"

export type Payment = {
  id: string
  admission_case_id: string
  source_kind: PaymentSourceKind
  source_system: string | null
  external_order_id: string | null
  payer_person_id: string | null
  amount_cents: number | null
  refunded_cents: number | null
  available_refund_cents: number | null
  currency: "CNY"
  paid_at: string
  created_at: string
}

export type Refund = {
  id: string
  payment_id: string
  amount_cents: number
  refunded_at: string
  refund_type: RefundType
  reason: string
  created_at: string
}

export type AdmissionCase = {
  id: string
  student_person_id: string | null
  student_name: string | null
  status: AdmissionCaseStatus
  current_enrollment_id: string | null
  payment_status: PaymentStatus
  paid_cents: number | null
  refunded_cents: number | null
  net_paid_cents: number | null
  agreed_tuition_cents: number | null
  due_cents: number | null
  overpaid_cents: number | null
  manage_allowed: boolean
  read_only_reason: "archived" | null
  created_at: string
  updated_at: string
}

export type AdmissionStudentCandidate = {
  id: string
  full_name: string
  student_number: string
  student_status: "draft" | "active" | "inactive"
  masked_phone: string | null
}

export type AdmissionEnrollmentOption = {
  cohort_id: string
  cohort_name: string
  cohort_starts_at: string
  cohort_status: "open"
  class_id: string
  class_name: string
  class_is_active: true
  capacity: number
  active_enrollment_count: number
  is_full: boolean
  default_tuition_cents: number
  readiness_at_start: "ready" | "minor_guardian_required"
  manage_allowed: boolean
  capacity_override_allowed: boolean
}

export type Enrollment = {
  id: string
  admission_case_id: string
  student_person_id: string
  student_name: string
  cohort_id: string
  cohort_name: string
  class_id: string
  class_name: string
  agreed_tuition_cents: number | null
  payment_status: PaymentStatus
  paid_cents: number | null
  refunded_cents: number | null
  net_paid_cents: number | null
  due_cents: number | null
  overpaid_cents: number | null
  status: EnrollmentStatus
  confirmed_at: string
  cancelled_at: string | null
  completed_at: string | null
  created_at: string
  updated_at: string
}

export type EnrollmentActionContext = {
  manage_allowed: boolean
  refund_allowed: boolean
  read_only_reason: "archived" | null
}

export type EnrollmentTransferOption = {
  class_id: string
  class_name: string
  capacity: number
  active_enrollment_count: number
  is_full: boolean
  capacity_override_allowed: boolean
}

export type ServiceCredential = {
  id: string
  service_account_id: string
  public_id: string
  name: string
  is_active: boolean
  disabled_at: string | null
}

export type CreatedServiceCredential = {
  id: string
  service_account_id: string
  public_id: string
  name: string
  secret: string
}

export type ServiceAccount = {
  id: string
  name: string
  source_system: string
  is_active: boolean
  credentials: ServiceCredential[]
  created_at: string
  updated_at: string
}

export type PaymentConflict = {
  id: string
  original_import_event_id: string
  conflict_type: PaymentConflictType
  status: PaymentConflictStatus
  resolution_note: string | null
  resolved_at: string | null
  created_at: string
  updated_at: string
}

export type RefundAllocation = {
  payment_id: string
  amount_cents: number
}

export type WithdrawalPreviewInput = {
  withdrawal_date: string
  policy: RefundPolicy
}

export type WithdrawalPreview = WithdrawalPreviewInput & {
  total_days: number
  remaining_days: number
  agreed_tuition_cents: number
  available_paid_cents: number
  refund_cents: number
  allocations: RefundAllocation[]
}

export type WithdrawalConfirmInput = WithdrawalPreview & { reason: string }

export type ManualPaymentInput = {
  student_person_id: string | null
  admission_case_id: string | null
  amount: string
  paid_at: string
  note: string
}

export type RefundInput = {
  amount: string
  refunded_at: string
  reason: string
}

export type ConfirmEnrollmentInput = {
  class_id: string
  agreed_tuition_cents?: number | null
  tuition_override_reason?: string | null
  capacity_override_reason?: string | null
}

export type TransferEnrollmentInput = {
  target_class_id: string
  reason: string
  capacity_override_reason?: string | null
}

export type Pagination = { page?: number; pageSize?: number }
export type PaymentFilters = Pagination & {
  sourceKind?: PaymentSourceKind
  sourceSystem?: string
  payerPhone?: string
  dateFrom?: string
  dateTo?: string
}
export type AdmissionCaseFilters = Pagination & {
  status?: AdmissionCaseStatus
  studentPhone?: string
}
export type AdmissionStudentCandidateFilters = Pagination & { query: string }
export type EnrollmentFilters = Pagination & {
  status?: EnrollmentStatus
  cohortId?: string
  classId?: string
}
export type PaymentConflictFilters = Pagination & { status?: PaymentConflictStatus }

function pageQuery(filters: Pagination = {}): URLSearchParams {
  return new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 20),
  })
}

function setQueryValue(
  query: URLSearchParams,
  key: string,
  value: string | undefined,
): void {
  if (value) query.set(key, value)
}

function postJson<T>(path: string, input?: unknown): Promise<T> {
  const init: RequestInit = { method: "POST" }
  if (input !== undefined) {
    init.headers = { "Content-Type": "application/json" }
    init.body = JSON.stringify(input)
  }
  return apiFetch<T>(path, init)
}

export function listPayments(filters: PaymentFilters = {}): Promise<Page<Payment>> {
  const query = pageQuery(filters)
  setQueryValue(query, "source_kind", filters.sourceKind)
  setQueryValue(query, "source_system", filters.sourceSystem)
  setQueryValue(query, "payer_phone", filters.payerPhone)
  setQueryValue(query, "date_from", filters.dateFrom)
  setQueryValue(query, "date_to", filters.dateTo)
  return apiFetch<Page<Payment>>(`/payments?${query.toString()}`)
}

export function createManualPayment(input: ManualPaymentInput): Promise<Payment> {
  return postJson<Payment>("/payments", input)
}

export function createRefund(paymentId: string, input: RefundInput): Promise<Refund> {
  return postJson<Refund>(`/payments/${paymentId}/refunds`, input)
}

export function listPaymentConflicts(
  filters: PaymentConflictFilters = {},
): Promise<Page<PaymentConflict>> {
  const query = pageQuery(filters)
  setQueryValue(query, "status", filters.status)
  return apiFetch<Page<PaymentConflict>>(`/payment-conflicts?${query.toString()}`)
}

export function resolvePaymentConflict(
  conflictId: string,
  input: { action: "resolved" | "dismissed"; note: string },
): Promise<PaymentConflict> {
  return postJson<PaymentConflict>(`/payment-conflicts/${conflictId}/resolve`, input)
}

export function listServiceAccounts(
  filters: Pagination = {},
): Promise<Page<ServiceAccount>> {
  const query = pageQuery(filters)
  return apiFetch<Page<ServiceAccount>>(`/service-accounts?${query.toString()}`)
}

export function createServiceAccount(input: {
  name: string
  source_system: string
}): Promise<ServiceAccount> {
  return postJson<ServiceAccount>("/service-accounts", input)
}

export function disableServiceAccount(serviceAccountId: string): Promise<void> {
  return postJson<void>(`/service-accounts/${serviceAccountId}/disable`)
}

export function createServiceCredential(
  serviceAccountId: string,
  input: { name: string },
): Promise<CreatedServiceCredential> {
  return postJson<CreatedServiceCredential>(
    `/service-accounts/${serviceAccountId}/credentials`,
    input,
  )
}

export function disableServiceCredential(credentialId: string): Promise<void> {
  return postJson<void>(`/service-account-credentials/${credentialId}/disable`)
}

export function listAdmissionCases(
  filters: AdmissionCaseFilters = {},
): Promise<Page<AdmissionCase>> {
  const query = pageQuery(filters)
  setQueryValue(query, "status", filters.status)
  setQueryValue(query, "student_phone", filters.studentPhone)
  return apiFetch<Page<AdmissionCase>>(`/admission-cases?${query.toString()}`)
}

export function listAdmissionStudentCandidates(
  admissionCaseId: string,
  filters: AdmissionStudentCandidateFilters,
): Promise<Page<AdmissionStudentCandidate>> {
  const query = pageQuery(filters)
  query.set("query", filters.query)
  return apiFetch<Page<AdmissionStudentCandidate>>(
    `/admission-cases/${admissionCaseId}/student-candidates?${query.toString()}`,
  )
}

export function listAdmissionEnrollmentOptions(
  admissionCaseId: string,
  filters: Pagination = {},
): Promise<Page<AdmissionEnrollmentOption>> {
  const query = pageQuery(filters)
  return apiFetch<Page<AdmissionEnrollmentOption>>(
    `/admission-cases/${admissionCaseId}/enrollment-options?${query.toString()}`,
  )
}

export function matchAdmissionStudent(
  admissionCaseId: string,
  studentPersonId: string,
): Promise<AdmissionCase> {
  return postJson<AdmissionCase>(`/admission-cases/${admissionCaseId}/match-student`, {
    student_person_id: studentPersonId,
  })
}

export function closeAdmissionCase(
  admissionCaseId: string,
  reason: string,
): Promise<AdmissionCase> {
  return postJson<AdmissionCase>(`/admission-cases/${admissionCaseId}/close`, { reason })
}

export function reopenAdmissionCase(admissionCaseId: string): Promise<AdmissionCase> {
  return postJson<AdmissionCase>(`/admission-cases/${admissionCaseId}/reopen`)
}

export function confirmAdmissionEnrollment(
  admissionCaseId: string,
  input: ConfirmEnrollmentInput,
): Promise<Enrollment> {
  return postJson<Enrollment>(
    `/admission-cases/${admissionCaseId}/confirm-enrollment`,
    input,
  )
}

export function listEnrollments(
  filters: EnrollmentFilters = {},
): Promise<Page<Enrollment>> {
  const query = pageQuery(filters)
  setQueryValue(query, "status", filters.status)
  setQueryValue(query, "cohort_id", filters.cohortId)
  setQueryValue(query, "class_id", filters.classId)
  return apiFetch<Page<Enrollment>>(`/enrollments?${query.toString()}`)
}

export function getEnrollment(enrollmentId: string): Promise<Enrollment> {
  return apiFetch<Enrollment>(`/enrollments/${enrollmentId}`)
}

export function getEnrollmentActionContext(
  enrollmentId: string,
): Promise<EnrollmentActionContext> {
  return apiFetch<EnrollmentActionContext>(
    `/enrollments/${enrollmentId}/action-context`,
  )
}

export function listEnrollmentTransferOptions(
  enrollmentId: string,
  filters: Pagination = {},
): Promise<Page<EnrollmentTransferOption>> {
  const query = pageQuery(filters)
  return apiFetch<Page<EnrollmentTransferOption>>(
    `/enrollments/${enrollmentId}/transfer-options?${query.toString()}`,
  )
}

export function transferEnrollment(
  enrollmentId: string,
  input: TransferEnrollmentInput,
): Promise<Enrollment> {
  return postJson<Enrollment>(`/enrollments/${enrollmentId}/transfer`, input)
}

export function cancelEnrollment(
  enrollmentId: string,
  reason: string,
): Promise<Enrollment> {
  return postJson<Enrollment>(`/enrollments/${enrollmentId}/cancel`, { reason })
}

export function previewWithdrawal(
  enrollmentId: string,
  input: WithdrawalPreviewInput,
): Promise<WithdrawalPreview> {
  return postJson<WithdrawalPreview>(
    `/enrollments/${enrollmentId}/withdrawal-refund-preview`,
    input,
  )
}

export function confirmWithdrawal(
  enrollmentId: string,
  input: WithdrawalConfirmInput,
): Promise<Enrollment> {
  return postJson<Enrollment>(
    `/enrollments/${enrollmentId}/withdrawal-refund-confirm`,
    input,
  )
}
