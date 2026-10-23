import { apiFetchResponse, ProblemDetailsError } from "../api/client"

export type Parser<T> = (value: unknown) => T
export type RegistrationIdentifierKind = "phone" | "email"
export type RegistrationPurpose =
  | "student"
  | "guardian"
  | "staff_applicant"
  | "visitor"
export type RegistrationState =
  | "draft"
  | "contact_verified"
  | "identity_completed"
  | "pending_guardian"
  | "ready_to_finalize"
  | "finalized"
  | "conflict"
  | "rejected"
  | "expired"
export type RegistrationNextAction =
  | "verify_contact"
  | "complete_profile"
  | "finalize"
  | "complete_guardian"
  | "await_review"
  | "login"
  | "login_or_reset"
  | "start_again"
  | "closed"

export type RegistrationApplicationStarted = {
  application_id: string
  state: "draft"
  masked_identifier: string
  expires_at: string
  request_id: string
}

export type RegistrationChannelSettings = {
  phone_enabled: boolean
  email_enabled: boolean
  phone_signup_allowed_by_deployment: boolean
  updated_at: string | null
  request_id: string
}

export type RegistrationChallenge = {
  challenge_id: string
  expires_at: string
  retry_after_seconds: number
  masked_destination: string
  request_id: string
}

export type RegistrationStateResponse = {
  state: RegistrationState
  primary_purpose: RegistrationPurpose
  masked_identifier: string
  next_action: RegistrationNextAction
  expires_at: string
  request_id: string
}

export type RegistrationVerifyResponse = RegistrationStateResponse & {
  csrf_token: string
}

export type RegistrationProfileInput = {
  password: string
  full_name: string
  identity_number: string
  sex: "male" | "female"
  date_of_birth: string
  consent_version: string
  purpose_details: {
    requested_staff_type:
      | "coach"
      | "life_teacher"
      | "customer_service"
      | "administration"
      | "other"
      | null
    staff_application_reason: string | null
    visit_reason: string | null
  }
}

export type RegistrationProfileResponse = RegistrationStateResponse

export type RegistrationTermsConsentStatus = {
  current_version: string
  accepted_version: string | null
  reconfirmation_required: boolean
  request_id: string
}

export type RegistrationFinalizeResponse = {
  state: "pending_guardian" | "finalized" | "conflict"
  next_action: "complete_guardian" | "login" | "login_or_reset" | "await_review"
  request_id: string
}

export type PasswordResetCompleteResponse = {
  status: "completed"
  request_id: string
}

export type AccountIdentifierSummary = {
  kind: RegistrationIdentifierKind
  masked_identifier: string
  verified_at: string
}

export type AccountIdentifiersResponse = {
  items: AccountIdentifierSummary[]
  request_id: string
}

export type AccountIdentifierChallengeInput = {
  identifier_kind: RegistrationIdentifierKind
  identifier: string
  current_password: string
}

export type AccountIdentifierVerifyInput = AccountIdentifierChallengeInput & {
  challenge_id: string
  code: string
}

export type StaffType =
  | "coach"
  | "life_teacher"
  | "customer_service"
  | "administration"
  | "other"

export type GuardianInvitation = {
  id: string
  masked_identifier: string
  relationship: string
  is_primary: boolean
  status: "pending" | "accepted" | "revoked" | "expired"
  expires_at: string
}

export type GuardianInvitationInput = {
  identifier_kind: RegistrationIdentifierKind
  identifier: string
  relationship: string
  is_primary: boolean
}

export type GuardianInvitationAcceptance = {
  status: "accepted"
  student_finalized: boolean
  request_id: string
}

export type GuardianInvitationAcceptanceReceipt = {
  status: "accepted"
  student_finalized: boolean
  request_id: string
}

export type GuardianInvitationContext = {
  masked_identifier: string
  relationship: string
  is_primary: boolean
  expires_at: string
  consent_version: string
  request_id: string
}

export type StaffApplicationStatus = "pending" | "approved" | "rejected" | "withdrawn"

export type StaffApplicationSelf = {
  application_id: string
  requested_staff_type: StaffType
  application_reason: string
  status: StaffApplicationStatus
  reviewed_at: string | null
  created_at: string
}

export type RegistrationAdminListItem = {
  application_id: string
  primary_purpose: RegistrationPurpose
  state: "conflict"
  masked_identifier: string
  conflict_code: string | null
  candidate_count: number
  expires_at: string
  created_at: string
}

export type RegistrationAdminCandidate = {
  person_id: string
  full_name: string
  identity_number: string
  sex: "male" | "female"
  date_of_birth: string
  contact?: string
  deleted: boolean
  account_status?: string
}

export type RegistrationAdminDetail = RegistrationAdminListItem & {
  full_name?: string
  identity_number?: string
  sex?: "male" | "female"
  date_of_birth?: string
  verified_identifier?: string
  candidates?: RegistrationAdminCandidate[]
}

export type RegistrationReviewAction =
  | {
    state: "pending_guardian"
    next_action: "complete_guardian"
    request_id: string
  }
  | {
    state: "finalized"
    next_action: "login" | "login_or_reset"
    request_id: string
  }
  | {
    state: "rejected"
    next_action: "closed"
    request_id: string
  }

export type StaffApplicationAdminListItem = {
  application_id: string
  requested_staff_type: StaffType
  status: StaffApplicationStatus
  created_at: string
}

export type StaffApplicationAdminDetail = StaffApplicationAdminListItem & {
  application_reason: string
  person_id?: string
  full_name?: string
  identity_number?: string
  sex?: "male" | "female"
  date_of_birth?: string
  phone?: string
  email?: string
}

export type StaffApplicationReview = {
  application_id: string
  status: "approved" | "rejected"
  requested_staff_type: StaffType
  assigned_role: "coach" | "life_teacher" | "customer_service" | "administration" | "staff" | null
  request_id: string
}

const REGISTRATION_STATES: readonly RegistrationState[] = [
  "draft",
  "contact_verified",
  "identity_completed",
  "pending_guardian",
  "ready_to_finalize",
  "finalized",
  "conflict",
  "rejected",
  "expired",
]
const REGISTRATION_NEXT_ACTIONS: readonly RegistrationNextAction[] = [
  "verify_contact",
  "complete_profile",
  "finalize",
  "complete_guardian",
  "await_review",
  "login",
  "login_or_reset",
  "start_again",
  "closed",
]
const REGISTRATION_PURPOSES: readonly RegistrationPurpose[] = [
  "student",
  "guardian",
  "staff_applicant",
  "visitor",
]
const STAFF_TYPES: readonly StaffType[] = [
  "coach",
  "life_teacher",
  "customer_service",
  "administration",
  "other",
]
const STAFF_APPLICATION_STATUSES: readonly StaffApplicationStatus[] = [
  "pending",
  "approved",
  "rejected",
  "withdrawn",
]

let registrationCsrfToken: string | null = null
let registrationSessionGeneration = 0
let registrationCsrfRestoreInFlight: {
  generation: number
  promise: Promise<string>
} | null = null
let registrationSessionBoundaryHandler: (() => void) | null = null

class RegistrationSessionStaleError extends Error {
  constructor() {
    super("Registration session changed")
    this.name = "RegistrationSessionStaleError"
  }
}

export function setRegistrationCsrfToken(value: string | null): void {
  if (value === null) {
    registrationSessionGeneration += 1
    registrationCsrfRestoreInFlight = null
  }
  registrationCsrfToken = value
}

export function setRegistrationSessionBoundaryHandler(
  handler: (() => void) | null,
): void {
  registrationSessionBoundaryHandler = handler
}

function resetRegistrationSessionBoundary(): void {
  setRegistrationCsrfToken(null)
  registrationSessionBoundaryHandler?.()
}

function invalidResponse(): never {
  throw new Error("Invalid registration response")
}

function exactRecord(
  value: unknown,
  expectedKeys: readonly string[],
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return invalidResponse()
  }
  const record = value as Record<string, unknown>
  const actualKeys = Object.keys(record).sort()
  const requiredKeys = [...expectedKeys].sort()
  if (
    actualKeys.length !== requiredKeys.length ||
    actualKeys.some((key, index) => key !== requiredKeys[index])
  ) {
    return invalidResponse()
  }
  return record
}

function knownRecord(
  value: unknown,
  requiredKeys: readonly string[],
  optionalKeys: readonly string[],
): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return invalidResponse()
  }
  const record = value as Record<string, unknown>
  const allowed = new Set([...requiredKeys, ...optionalKeys])
  if (
    requiredKeys.some((key) => !(key in record)) ||
    Object.keys(record).some((key) => !allowed.has(key))
  ) {
    return invalidResponse()
  }
  return record
}

function stringField(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== "string" || value.length === 0) return invalidResponse()
  return value
}

function nullableStringField(
  record: Record<string, unknown>,
  key: string,
): string | null {
  const value = record[key]
  if (value === null) return null
  if (typeof value !== "string" || value.length === 0) return invalidResponse()
  return value
}

function optionalStringField(
  record: Record<string, unknown>,
  key: string,
): string | undefined {
  if (!(key in record)) return undefined
  return stringField(record, key)
}

function numberField(record: Record<string, unknown>, key: string): number {
  const value = record[key]
  if (typeof value !== "number" || !Number.isInteger(value)) {
    return invalidResponse()
  }
  return value
}

function booleanField(record: Record<string, unknown>, key: string): boolean {
  const value = record[key]
  if (typeof value !== "boolean") return invalidResponse()
  return value
}

function enumField<T extends string>(
  record: Record<string, unknown>,
  key: string,
  values: readonly T[],
): T {
  const value = record[key]
  if (typeof value !== "string" || !values.includes(value as T)) {
    return invalidResponse()
  }
  return value as T
}

const STATE_KEYS = [
  "state",
  "primary_purpose",
  "masked_identifier",
  "next_action",
  "expires_at",
  "request_id",
] as const

function stateFromRecord(record: Record<string, unknown>): RegistrationStateResponse {
  return {
    state: enumField(record, "state", REGISTRATION_STATES),
    primary_purpose: enumField(record, "primary_purpose", REGISTRATION_PURPOSES),
    masked_identifier: stringField(record, "masked_identifier"),
    next_action: enumField(record, "next_action", REGISTRATION_NEXT_ACTIONS),
    expires_at: stringField(record, "expires_at"),
    request_id: stringField(record, "request_id"),
  }
}

export const parseRegistrationState: Parser<RegistrationStateResponse> = (
  value,
) => stateFromRecord(exactRecord(value, STATE_KEYS))

const parseRegistrationTermsConsent: Parser<RegistrationTermsConsentStatus> = (
  value,
) => {
  const record = exactRecord(value, [
    "current_version",
    "accepted_version",
    "reconfirmation_required",
    "request_id",
  ])
  const acceptedVersion = record.accepted_version
  if (acceptedVersion !== null && typeof acceptedVersion !== "string") {
    return invalidResponse()
  }
  return {
    current_version: stringField(record, "current_version"),
    accepted_version: acceptedVersion,
    reconfirmation_required: booleanField(record, "reconfirmation_required"),
    request_id: stringField(record, "request_id"),
  }
}

const parseApplicationStarted: Parser<RegistrationApplicationStarted> = (value) => {
  const record = exactRecord(value, [
    "application_id",
    "state",
    "masked_identifier",
    "expires_at",
    "request_id",
  ])
  if (record.state !== "draft") return invalidResponse()
  return {
    application_id: stringField(record, "application_id"),
    state: "draft",
    masked_identifier: stringField(record, "masked_identifier"),
    expires_at: stringField(record, "expires_at"),
    request_id: stringField(record, "request_id"),
  }
}

const parseRegistrationChannelSettings: Parser<RegistrationChannelSettings> = (value) => {
  const record = exactRecord(value, [
    "phone_enabled",
    "email_enabled",
    "phone_signup_allowed_by_deployment",
    "updated_at",
    "request_id",
  ])
  return {
    phone_enabled: booleanField(record, "phone_enabled"),
    email_enabled: booleanField(record, "email_enabled"),
    phone_signup_allowed_by_deployment: booleanField(
      record,
      "phone_signup_allowed_by_deployment",
    ),
    updated_at: nullableStringField(record, "updated_at"),
    request_id: stringField(record, "request_id"),
  }
}

const parseChallenge: Parser<RegistrationChallenge> = (value) => {
  const record = exactRecord(value, [
    "challenge_id",
    "expires_at",
    "retry_after_seconds",
    "masked_destination",
    "request_id",
  ])
  return {
    challenge_id: stringField(record, "challenge_id"),
    expires_at: stringField(record, "expires_at"),
    retry_after_seconds: numberField(record, "retry_after_seconds"),
    masked_destination: stringField(record, "masked_destination"),
    request_id: stringField(record, "request_id"),
  }
}

const parseVerify: Parser<RegistrationVerifyResponse> = (value) => {
  const record = exactRecord(value, [
    ...STATE_KEYS,
    "csrf_token",
  ])
  return {
    ...stateFromRecord(record),
    csrf_token: stringField(record, "csrf_token"),
  }
}

const parseProfile: Parser<RegistrationProfileResponse> = parseRegistrationState

const parseFinalize: Parser<RegistrationFinalizeResponse> = (value) => {
  const record = exactRecord(value, ["state", "next_action", "request_id"])
  const state = enumField(record, "state", [
    "pending_guardian",
    "finalized",
    "conflict",
  ] as const)
  const nextAction = enumField(record, "next_action", [
    "complete_guardian",
    "login",
    "login_or_reset",
    "await_review",
  ] as const)
  return {
    state,
    next_action: nextAction,
    request_id: stringField(record, "request_id"),
  }
}

const parsePasswordResetComplete: Parser<PasswordResetCompleteResponse> = (
  value,
) => {
  const record = exactRecord(value, ["status", "request_id"])
  if (record.status !== "completed") return invalidResponse()
  return {
    status: "completed",
    request_id: stringField(record, "request_id"),
  }
}

const parseAccountIdentifier: Parser<AccountIdentifierSummary> = (value) => {
  const record = exactRecord(value, [
    "kind",
    "masked_identifier",
    "verified_at",
  ])
  return {
    kind: enumField(record, "kind", ["phone", "email"] as const),
    masked_identifier: stringField(record, "masked_identifier"),
    verified_at: stringField(record, "verified_at"),
  }
}

const parseAccountIdentifiers: Parser<AccountIdentifiersResponse> = (value) => {
  const record = exactRecord(value, ["items", "request_id"])
  if (!Array.isArray(record.items)) return invalidResponse()
  return {
    items: record.items.map(parseAccountIdentifier),
    request_id: stringField(record, "request_id"),
  }
}

const parseGuardianInvitation: Parser<GuardianInvitation> = (value) => {
  const record = exactRecord(value, [
    "id",
    "masked_identifier",
    "relationship",
    "is_primary",
    "status",
    "expires_at",
  ])
  return {
    id: stringField(record, "id"),
    masked_identifier: stringField(record, "masked_identifier"),
    relationship: stringField(record, "relationship"),
    is_primary: booleanField(record, "is_primary"),
    status: enumField(record, "status", [
      "pending",
      "accepted",
      "revoked",
      "expired",
    ] as const),
    expires_at: stringField(record, "expires_at"),
  }
}

const parseGuardianInvitations: Parser<GuardianInvitation[]> = (value) => {
  if (!Array.isArray(value)) return invalidResponse()
  return value.map(parseGuardianInvitation)
}

const parseGuardianAcceptance: Parser<GuardianInvitationAcceptance> = (value) => {
  const record = exactRecord(value, [
    "status",
    "student_finalized",
    "request_id",
  ])
  if (record.status !== "accepted") return invalidResponse()
  return {
    status: "accepted",
    student_finalized: booleanField(record, "student_finalized"),
    request_id: stringField(record, "request_id"),
  }
}

const parseGuardianAcceptanceReceipt: Parser<GuardianInvitationAcceptanceReceipt> = (
  value,
) => {
  const record = exactRecord(value, [
    "status",
    "student_finalized",
    "request_id",
  ])
  if (record.status !== "accepted") return invalidResponse()
  return {
    status: "accepted",
    student_finalized: booleanField(record, "student_finalized"),
    request_id: stringField(record, "request_id"),
  }
}

const parseGuardianInvitationContext: Parser<GuardianInvitationContext> = (
  value,
) => {
  const record = exactRecord(value, [
    "masked_identifier",
    "relationship",
    "is_primary",
    "expires_at",
    "consent_version",
    "request_id",
  ])
  return {
    masked_identifier: stringField(record, "masked_identifier"),
    relationship: stringField(record, "relationship"),
    is_primary: booleanField(record, "is_primary"),
    expires_at: stringField(record, "expires_at"),
    consent_version: stringField(record, "consent_version"),
    request_id: stringField(record, "request_id"),
  }
}

const parseStaffApplicationSelf: Parser<StaffApplicationSelf> = (value) => {
  const record = exactRecord(value, [
    "application_id",
    "requested_staff_type",
    "application_reason",
    "status",
    "reviewed_at",
    "created_at",
  ])
  return {
    application_id: stringField(record, "application_id"),
    requested_staff_type: enumField(record, "requested_staff_type", STAFF_TYPES),
    application_reason: stringField(record, "application_reason"),
    status: enumField(record, "status", STAFF_APPLICATION_STATUSES),
    reviewed_at: nullableStringField(record, "reviewed_at"),
    created_at: stringField(record, "created_at"),
  }
}

const REGISTRATION_ADMIN_LIST_KEYS = [
  "application_id",
  "primary_purpose",
  "state",
  "masked_identifier",
  "conflict_code",
  "candidate_count",
  "expires_at",
  "created_at",
] as const

function registrationAdminListFromRecord(
  record: Record<string, unknown>,
): RegistrationAdminListItem {
  if (record.state !== "conflict") return invalidResponse()
  const candidateCount = numberField(record, "candidate_count")
  if (candidateCount < 0) return invalidResponse()
  return {
    application_id: stringField(record, "application_id"),
    primary_purpose: enumField(record, "primary_purpose", REGISTRATION_PURPOSES),
    state: "conflict",
    masked_identifier: stringField(record, "masked_identifier"),
    conflict_code: nullableStringField(record, "conflict_code"),
    candidate_count: candidateCount,
    expires_at: stringField(record, "expires_at"),
    created_at: stringField(record, "created_at"),
  }
}

const parseRegistrationAdminListItem: Parser<RegistrationAdminListItem> = (value) =>
  registrationAdminListFromRecord(exactRecord(value, REGISTRATION_ADMIN_LIST_KEYS))

const parseRegistrationAdminList: Parser<RegistrationAdminListItem[]> = (value) => {
  if (!Array.isArray(value)) return invalidResponse()
  return value.map(parseRegistrationAdminListItem)
}

const parseRegistrationAdminCandidate: Parser<RegistrationAdminCandidate> = (value) => {
  const record = knownRecord(
    value,
    ["person_id", "full_name", "identity_number", "sex", "date_of_birth", "deleted"],
    ["contact", "account_status"],
  )
  const contact = optionalStringField(record, "contact")
  const accountStatus = optionalStringField(record, "account_status")
  return {
    person_id: stringField(record, "person_id"),
    full_name: stringField(record, "full_name"),
    identity_number: stringField(record, "identity_number"),
    sex: enumField(record, "sex", ["male", "female"] as const),
    date_of_birth: stringField(record, "date_of_birth"),
    deleted: booleanField(record, "deleted"),
    ...(contact === undefined ? {} : { contact }),
    ...(accountStatus === undefined ? {} : { account_status: accountStatus }),
  }
}

const parseRegistrationAdminDetail: Parser<RegistrationAdminDetail> = (value) => {
  const optionalKeys = [
    "full_name",
    "identity_number",
    "sex",
    "date_of_birth",
    "verified_identifier",
    "candidates",
  ] as const
  const record = knownRecord(value, REGISTRATION_ADMIN_LIST_KEYS, optionalKeys)
  const base = registrationAdminListFromRecord(record)
  const fullName = optionalStringField(record, "full_name")
  const identityNumber = optionalStringField(record, "identity_number")
  const dateOfBirth = optionalStringField(record, "date_of_birth")
  const verifiedIdentifier = optionalStringField(record, "verified_identifier")
  let sex: "male" | "female" | undefined
  if ("sex" in record) sex = enumField(record, "sex", ["male", "female"] as const)
  let candidates: RegistrationAdminCandidate[] | undefined
  if ("candidates" in record) {
    if (!Array.isArray(record.candidates)) return invalidResponse()
    candidates = record.candidates.map(parseRegistrationAdminCandidate)
  }
  return {
    ...base,
    ...(fullName === undefined ? {} : { full_name: fullName }),
    ...(identityNumber === undefined ? {} : { identity_number: identityNumber }),
    ...(sex === undefined ? {} : { sex }),
    ...(dateOfBirth === undefined ? {} : { date_of_birth: dateOfBirth }),
    ...(verifiedIdentifier === undefined ? {} : { verified_identifier: verifiedIdentifier }),
    ...(candidates === undefined ? {} : { candidates }),
  }
}

const parseRegistrationReviewAction: Parser<RegistrationReviewAction> = (value) => {
  const record = exactRecord(value, ["state", "next_action", "request_id"])
  const state = enumField(record, "state", [
    "pending_guardian",
    "finalized",
    "rejected",
  ] as const)
  const requestId = stringField(record, "request_id")
  if (state === "pending_guardian") {
    if (record.next_action !== "complete_guardian") return invalidResponse()
    return {
      state,
      next_action: "complete_guardian",
      request_id: requestId,
    }
  }
  if (state === "rejected") {
    if (record.next_action !== "closed") return invalidResponse()
    return {
      state,
      next_action: "closed",
      request_id: requestId,
    }
  }
  return {
    state,
    next_action: enumField(record, "next_action", ["login", "login_or_reset"] as const),
    request_id: requestId,
  }
}

const STAFF_ADMIN_LIST_KEYS = [
  "application_id",
  "requested_staff_type",
  "status",
  "created_at",
] as const

function staffAdminListFromRecord(
  record: Record<string, unknown>,
): StaffApplicationAdminListItem {
  return {
    application_id: stringField(record, "application_id"),
    requested_staff_type: enumField(record, "requested_staff_type", STAFF_TYPES),
    status: enumField(record, "status", STAFF_APPLICATION_STATUSES),
    created_at: stringField(record, "created_at"),
  }
}

const parseStaffAdminListItem: Parser<StaffApplicationAdminListItem> = (value) =>
  staffAdminListFromRecord(exactRecord(value, STAFF_ADMIN_LIST_KEYS))

const parseStaffAdminList: Parser<StaffApplicationAdminListItem[]> = (value) => {
  if (!Array.isArray(value)) return invalidResponse()
  return value.map(parseStaffAdminListItem)
}

const parseStaffAdminDetail: Parser<StaffApplicationAdminDetail> = (value) => {
  const record = knownRecord(
    value,
    [...STAFF_ADMIN_LIST_KEYS, "application_reason"],
    ["person_id", "full_name", "identity_number", "sex", "date_of_birth", "phone", "email"],
  )
  const base = staffAdminListFromRecord(record)
  const personId = optionalStringField(record, "person_id")
  const fullName = optionalStringField(record, "full_name")
  const identityNumber = optionalStringField(record, "identity_number")
  const dateOfBirth = optionalStringField(record, "date_of_birth")
  const phone = optionalStringField(record, "phone")
  const email = optionalStringField(record, "email")
  let sex: "male" | "female" | undefined
  if ("sex" in record) sex = enumField(record, "sex", ["male", "female"] as const)
  return {
    ...base,
    application_reason: stringField(record, "application_reason"),
    ...(personId === undefined ? {} : { person_id: personId }),
    ...(fullName === undefined ? {} : { full_name: fullName }),
    ...(identityNumber === undefined ? {} : { identity_number: identityNumber }),
    ...(sex === undefined ? {} : { sex }),
    ...(dateOfBirth === undefined ? {} : { date_of_birth: dateOfBirth }),
    ...(phone === undefined ? {} : { phone }),
    ...(email === undefined ? {} : { email }),
  }
}

const parseStaffApplicationReview: Parser<StaffApplicationReview> = (value) => {
  const record = exactRecord(value, [
    "application_id",
    "status",
    "requested_staff_type",
    "assigned_role",
    "request_id",
  ])
  const assignedRole = record.assigned_role === null
    ? null
    : enumField(record, "assigned_role", [
        "coach",
        "life_teacher",
        "customer_service",
        "administration",
        "staff",
      ] as const)
  return {
    application_id: stringField(record, "application_id"),
    status: enumField(record, "status", ["approved", "rejected"] as const),
    requested_staff_type: enumField(record, "requested_staff_type", STAFF_TYPES),
    assigned_role: assignedRole,
    request_id: stringField(record, "request_id"),
  }
}

export async function registrationFetch<T>(
  path: string,
  init: RequestInit,
  parse: Parser<T>,
): Promise<T> {
  const generation = registrationSessionGeneration
  const headers = new Headers(init.headers)
  const method = (init.method ?? "GET").toUpperCase()
  if (
    registrationCsrfToken &&
    !["GET", "HEAD", "OPTIONS"].includes(method)
  ) {
    headers.set("X-Registration-CSRF-Token", registrationCsrfToken)
  }
  const response = await fetch(`/api/v1/registration${path}`, {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    const error = await ProblemDetailsError.fromResponse(response)
    if (
      generation === registrationSessionGeneration &&
      (
        (response.status === 401 &&
          error.problem.code === "registration_session_invalid") ||
        (response.status === 404 &&
          error.problem.code === "registration_action_unavailable")
      )
    ) {
      resetRegistrationSessionBoundary()
    }
    throw error
  }
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return invalidResponse()
  }
  if (generation !== registrationSessionGeneration) {
    throw new RegistrationSessionStaleError()
  }
  return parse(value)
}

function jsonRequest(body?: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  }
}

async function authFetch<T>(
  path: string,
  init: RequestInit,
  parse: Parser<T>,
): Promise<T> {
  const response = await apiFetchResponse(`/auth${path}`, init)
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return invalidResponse()
  }
  return parse(value)
}

async function formalRegistrationFetch<T>(
  path: string,
  init: RequestInit,
  parse: Parser<T>,
): Promise<T> {
  const response = await apiFetchResponse(`/registration${path}`, init)
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return invalidResponse()
  }
  return parse(value)
}

async function registrationAdminFetch<T>(
  path: string,
  init: RequestInit,
  parse: Parser<T>,
): Promise<T> {
  const response = await apiFetchResponse(`/registration-admin${path}`, init)
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return invalidResponse()
  }
  return parse(value)
}

export function startRegistration(input: {
  primary_purpose: RegistrationPurpose
  identifier_kind: RegistrationIdentifierKind
  identifier: string
  referral_code?: string
}): Promise<RegistrationApplicationStarted> {
  resetRegistrationSessionBoundary()
  return registrationFetch("/applications", jsonRequest(input), parseApplicationStarted)
}

export function getRegistrationChannelSettings(): Promise<RegistrationChannelSettings> {
  return formalRegistrationFetch(
    "/channel-settings",
    {},
    parseRegistrationChannelSettings,
  )
}

export function issueRegistrationChallenge(
  applicationId: string,
): Promise<RegistrationChallenge> {
  return registrationFetch(
    `/applications/${encodeURIComponent(applicationId)}/challenge`,
    jsonRequest(),
    parseChallenge,
  )
}

export async function verifyRegistration(
  applicationId: string,
  input: { challenge_id: string; code: string },
): Promise<RegistrationVerifyResponse> {
  const result = await registrationFetch(
    `/applications/${encodeURIComponent(applicationId)}/verify`,
    jsonRequest(input),
    parseVerify,
  )
  resetRegistrationSessionBoundary()
  setRegistrationCsrfToken(result.csrf_token)
  return result
}

export function getRegistrationMe(): Promise<RegistrationStateResponse> {
  return registrationFetch("/me", {}, parseRegistrationState)
}

export function getRegistrationTermsConsent(): Promise<RegistrationTermsConsentStatus> {
  return registrationFetch(
    "/me/consents/registration-terms",
    {},
    parseRegistrationTermsConsent,
  )
}

export function confirmRegistrationTermsConsent(
  consentVersion: string,
): Promise<RegistrationTermsConsentStatus> {
  return registrationFetch(
    "/me/consents/registration-terms",
    jsonRequest({ consent_version: consentVersion }),
    parseRegistrationTermsConsent,
  )
}

export function restoreRegistrationCsrf(): Promise<string> {
  const generation = registrationSessionGeneration
  if (registrationCsrfRestoreInFlight?.generation === generation) {
    return registrationCsrfRestoreInFlight.promise
  }

  const request = registrationFetch(
    "/csrf",
    {},
    (value) => {
      const record = exactRecord(value, ["csrf_token"])
      return { csrf_token: stringField(record, "csrf_token") }
    },
  )
  const promise = request
    .then((result) => {
      if (generation !== registrationSessionGeneration) {
        throw new RegistrationSessionStaleError()
      }
      setRegistrationCsrfToken(result.csrf_token)
      return result.csrf_token
    })
    .finally(() => {
      if (
        registrationCsrfRestoreInFlight?.generation === generation &&
        registrationCsrfRestoreInFlight.promise === promise
      ) {
        registrationCsrfRestoreInFlight = null
      }
    })
  registrationCsrfRestoreInFlight = { generation, promise }
  return promise
}

export function issueRegistrationContactChallenge(input: {
  identifier_kind: RegistrationIdentifierKind
  identifier: string
}): Promise<RegistrationChallenge> {
  return registrationFetch(
    "/me/contact-challenges",
    jsonRequest(input),
    parseChallenge,
  )
}

export function verifyRegistrationContactChange(input: {
  challenge_id: string
  code: string
  identifier_kind: RegistrationIdentifierKind
  identifier: string
}): Promise<RegistrationProfileResponse> {
  return registrationFetch(
    "/me/contact",
    jsonRequest(input),
    parseProfile,
  )
}

export function updateRegistrationProfile(
  input: RegistrationProfileInput,
): Promise<RegistrationProfileResponse> {
  return registrationFetch(
    "/me",
    {
      // Some embedded iOS browsers send PATCH requests without their body.
      // The endpoint accepts POST as a compatibility-safe profile completion
      // operation while retaining PATCH for existing API clients.
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    parseProfile,
  )
}

export async function finalizeRegistration(): Promise<RegistrationFinalizeResponse> {
  const result = await registrationFetch(
    "/me/finalize",
    jsonRequest(),
    parseFinalize,
  )
  if (result.state === "finalized") resetRegistrationSessionBoundary()
  return result
}

export function issuePasswordResetChallenge(
  identifier: string,
): Promise<RegistrationChallenge> {
  return authFetch(
    "/password-reset/challenges",
    jsonRequest({ identifier }),
    parseChallenge,
  )
}

export function completePasswordReset(input: {
  challenge_id: string
  code: string
  new_password: string
}): Promise<PasswordResetCompleteResponse> {
  return authFetch(
    "/password-reset/complete",
    jsonRequest(input),
    parsePasswordResetComplete,
  )
}

export function listAccountIdentifiers(): Promise<AccountIdentifiersResponse> {
  return authFetch("/identifiers", {}, parseAccountIdentifiers)
}

export function issueAccountIdentifierChallenge(
  input: AccountIdentifierChallengeInput,
): Promise<RegistrationChallenge> {
  return authFetch(
    "/identifiers/challenges",
    jsonRequest(input),
    parseChallenge,
  )
}

export function verifyAccountIdentifier(
  input: AccountIdentifierVerifyInput,
): Promise<AccountIdentifiersResponse> {
  return authFetch(
    "/identifiers/verify",
    jsonRequest(input),
    parseAccountIdentifiers,
  )
}

export function listMinorGuardianInvitations(): Promise<GuardianInvitation[]> {
  return registrationFetch(
    "/me/guardian-invitations",
    {},
    parseGuardianInvitations,
  )
}

export async function handoffGuardianInvitation(token: string): Promise<void> {
  const response = await fetch(
    "/api/v1/registration/guardian-invitations/handoff",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
      credentials: "include",
    },
  )
  if (!response.ok) throw await ProblemDetailsError.fromResponse(response)
  if (response.status !== 204) return invalidResponse()
}

export async function getGuardianInvitationContext(): Promise<GuardianInvitationContext> {
  const response = await fetch(
    "/api/v1/registration/guardian-invitations/context",
    { credentials: "include" },
  )
  if (!response.ok) throw await ProblemDetailsError.fromResponse(response)
  let value: unknown
  try {
    value = await response.json()
  } catch {
    return invalidResponse()
  }
  return parseGuardianInvitationContext(value)
}

export function getGuardianInvitationAcceptance(): Promise<GuardianInvitationAcceptanceReceipt> {
  return formalRegistrationFetch(
    "/guardian-invitations/acceptance",
    {},
    parseGuardianAcceptanceReceipt,
  )
}

export function startGuardianInvitationRegistration(): Promise<RegistrationApplicationStarted> {
  resetRegistrationSessionBoundary()
  return registrationFetch(
    "/guardian-invitations/start-registration",
    jsonRequest({}),
    parseApplicationStarted,
  )
}

export function acceptHandedOffGuardianInvitation(
  consentVersion: string,
): Promise<GuardianInvitationAcceptance> {
  return formalRegistrationFetch(
    "/guardian-invitations/accept",
    jsonRequest({ consent_version: consentVersion }),
    parseGuardianAcceptance,
  )
}

export function createMinorGuardianInvitation(
  input: GuardianInvitationInput,
): Promise<GuardianInvitation> {
  return registrationFetch(
    "/me/guardian-invitations",
    jsonRequest(input),
    parseGuardianInvitation,
  )
}

export function resendMinorGuardianInvitation(
  invitationId: string,
): Promise<GuardianInvitation> {
  return registrationFetch(
    `/me/guardian-invitations/${encodeURIComponent(invitationId)}/resend`,
    jsonRequest(),
    parseGuardianInvitation,
  )
}

export function revokeMinorGuardianInvitation(
  invitationId: string,
): Promise<GuardianInvitation> {
  return registrationFetch(
    `/me/guardian-invitations/${encodeURIComponent(invitationId)}`,
    { method: "DELETE" },
    parseGuardianInvitation,
  )
}

export function listAdultGuardianInvitations(): Promise<GuardianInvitation[]> {
  return formalRegistrationFetch(
    "/guardian-invitations",
    {},
    parseGuardianInvitations,
  )
}

export function createAdultGuardianInvitation(
  input: GuardianInvitationInput,
): Promise<GuardianInvitation> {
  return formalRegistrationFetch(
    "/guardian-invitations",
    jsonRequest(input),
    parseGuardianInvitation,
  )
}

export function getOwnStaffApplication(): Promise<StaffApplicationSelf> {
  return formalRegistrationFetch(
    "/staff-application",
    {},
    parseStaffApplicationSelf,
  )
}

export function listRegistrationConflicts(): Promise<RegistrationAdminListItem[]> {
  return registrationAdminFetch("/applications", {}, parseRegistrationAdminList)
}

export function saveRegistrationChannelSettings(input: {
  phone_enabled: boolean
  email_enabled: boolean
}): Promise<RegistrationChannelSettings> {
  return registrationAdminFetch(
    "/channel-settings",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
    parseRegistrationChannelSettings,
  )
}

export function getRegistrationConflict(
  applicationId: string,
): Promise<RegistrationAdminDetail> {
  return registrationAdminFetch(
    `/applications/${encodeURIComponent(applicationId)}`,
    {},
    parseRegistrationAdminDetail,
  )
}

export function rejectRegistrationConflict(
  applicationId: string,
  reason: string,
): Promise<RegistrationReviewAction> {
  return registrationAdminFetch(
    `/applications/${encodeURIComponent(applicationId)}/reject`,
    jsonRequest({ reason }),
    parseRegistrationReviewAction,
  )
}

export function resolveRegistrationConflict(
  applicationId: string,
  input: { candidate_person_id: string; reason: string },
): Promise<RegistrationReviewAction> {
  return registrationAdminFetch(
    `/applications/${encodeURIComponent(applicationId)}/resolve`,
    jsonRequest(input),
    parseRegistrationReviewAction,
  )
}

export function listStaffApplications(): Promise<StaffApplicationAdminListItem[]> {
  return registrationAdminFetch(
    "/staff-applications",
    {},
    parseStaffAdminList,
  )
}

export function getStaffApplication(
  applicationId: string,
): Promise<StaffApplicationAdminDetail> {
  return registrationAdminFetch(
    `/staff-applications/${encodeURIComponent(applicationId)}`,
    {},
    parseStaffAdminDetail,
  )
}

export function reviewStaffApplication(
  applicationId: string,
  action: "approve" | "reject",
  reason: string | null,
): Promise<StaffApplicationReview> {
  return registrationAdminFetch(
    `/staff-applications/${encodeURIComponent(applicationId)}/${action}`,
    jsonRequest({ reason }),
    parseStaffApplicationReview,
  )
}
