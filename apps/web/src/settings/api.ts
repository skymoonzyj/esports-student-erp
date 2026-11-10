import { apiFetch } from "../api/client"
import type { PersonType } from "../people/api"

export type AccountStatus = "active" | "disabled"
export type AccountIdentifierKind = "phone" | "email"

export type MailTlsMode = "implicit_tls" | "starttls"
export type MailTestStatus = "never" | "passed" | "failed"
export type MailTemplateEventKey = "verification_code" | "password_reset"

const MAIL_TEMPLATE_PATH: Record<MailTemplateEventKey, string> = {
  verification_code: "verification-code",
  password_reset: "password-reset",
}

export type MailDeliverySettings = {
  configured: boolean
  enabled: boolean
  smtp_host: string | null
  smtp_port: number | null
  tls_mode: MailTlsMode | null
  username_masked: string | null
  sender_masked: string | null
  from_name: string | null
  reply_to_masked: string | null
  authorization_code_configured: boolean
  last_test_status: MailTestStatus
  last_test_at: string | null
  updated_at: string | null
}

export type MailDeliverySettingsInput = {
  smtp_host: string
  smtp_port: number
  tls_mode: MailTlsMode
  username?: string
  sender?: string
  from_name: string
  reply_to?: string
  authorization_code?: string
  enabled: boolean
}

export type MailDeliverySettingsTestResult = {
  status: MailTestStatus
  request_id: string
}

export type MailTemplatePlaceholder = {
  token: string
  label: string
  example: string
}

export type MailTemplate = {
  event_key: MailTemplateEventKey
  event_name: string
  description: string
  subject_template: string
  html_template: string
  placeholders: MailTemplatePlaceholder[]
  is_default: boolean
  updated_at: string | null
}

export type MailTemplateInput = {
  subject_template: string
  html_template: string
}

export type MailTemplatePreview = {
  subject: string
  html: string
  plain_text: string
}

export type Account = {
  id: string
  person_id: string
  full_name: string
  nickname: string | null
  masked_primary_identifier: string
  primary_identifier_kind: AccountIdentifierKind
  masked_phone: string | null
  masked_email: string | null
  verified_identifier_kinds: AccountIdentifierKind[]
  status: AccountStatus
  force_password_change: boolean
  created_at: string
}

export type Page<T> = {
  items: T[]
  page: number
  page_size: number
  total: number
}

export type AccountCreated = {
  account: Account
  temporary_password: string
}

export type AccountCandidate = {
  id: string
  full_name: string
  person_types: PersonType[]
  phone: string
}

export type RolePermission = {
  id: string
  key: string
  name: string
  sensitive: boolean
  scope_kind: ScopeKind
}
export type Role = {
  id: string
  key: string
  name: string
  permissions?: RolePermission[]
}
export type Permission = { id: string; key: string; name: string; sensitive: boolean }
export type PermissionEffect = "allow" | "deny"
export type ScopeKind =
  | "global"
  | "self"
  | "family"
  | "person"
  | "assigned_class"
  | "assigned_team"
  | "cohort"
  | "project"
  | "enrollment"
  | "building"
  | "floor"

export type PermissionOverride = {
  id: string
  user_id: string
  permission_id: string
  effect: PermissionEffect
  scope_kind: ScopeKind
  scope_id: string | null
  scope_label?: string | null
  valid_from?: string | null
  valid_until?: string | null
  grant_reason?: string | null
}

export type EffectivePermission = {
  id: string
  key: string
  name: string
  sensitive: boolean
  scope_kinds: ScopeKind[]
  sources: Array<"role" | "account_override">
}

export type AccountAccess = {
  account_id: string
  roles: Role[]
  permission_overrides: PermissionOverride[]
  effective_permissions: EffectivePermission[]
}

export type AccountJobRoleChangeInput = {
  from_role_id: string | null
  to_role_id: string
  keep_concurrent_roles: boolean
  expected_role_ids: string[]
}

export type AccountJobRoleChangeResult = {
  account_id: string
  added_role_ids: string[]
  removed_role_ids: string[]
  retained_role_ids: string[]
  roles: Role[]
}

export type PermissionPreview = {
  allowed: boolean
  reason: string
}

export type PermissionOverrideInput = {
  permission_id: string
  effect: PermissionEffect
  scope_kind: ScopeKind
  scope_id: string | null
  valid_from?: string
  valid_until?: string
  grant_reason?: string
}

export type PermissionAccountDirectoryItem = Pick<
  Account,
  "id" | "full_name" | "status"
>

export type PermissionPersonDirectoryItem = {
  id: string
  full_name: string
}

export type PermissionClassDirectoryItem = {
  id: string
  class_name: string
  cohort_name: string
  project_name: string
  label: string
  is_active: boolean
}

export type PermissionTeamDirectoryItem = {
  id: string
  name: string
  project_id: string
  project_name: string
  is_active: boolean
  label: string
}

export type PermissionBuildingDirectoryItem = {
  id: string
  code: string
  name: string
  is_active: boolean
  label: string
}

export type PermissionFloorDirectoryItem = {
  id: string
  building_id: string
  building_name: string
  number: number
  name: string
  building_is_active: boolean
  is_active: boolean
  label: string
}

export type PermissionCohortDirectoryItem = {
  id: string
  name: string
  status: "draft" | "open" | "upcoming" | "active" | "completed" | "archived"
  label: string
}

export type PermissionProjectDirectoryItem = {
  id: string
  name: string
  is_active: boolean
  label: string
}

export type PermissionEnrollmentDirectoryItem = {
  id: string
  student_name: string
  cohort_name: string
  class_name: string
  project_name: string
  status: "active" | "cancelled" | "completed"
  label: string
}

export type AuditOutcome = "allowed" | "denied" | "failed"
export type AuditMetadata = {
  field?: unknown
  error_code?: unknown
  result_count?: unknown
  role_key?: unknown
  permission_key?: unknown
  effect?: unknown
  scope_kind?: unknown
  scope_id?: unknown
  [key: string]: unknown
}

export type AuditEvent = {
  id: string
  actor_user_id: string | null
  actor_name?: string | null
  action: string
  resource_type: string
  resource_id: string | null
  purpose: string | null
  outcome: AuditOutcome
  request_id: string
  metadata: AuditMetadata
  created_at: string
}

export type AuditFilters = {
  actorUserId?: string
  action?: string
  resourceType?: string
  dateFrom?: string
  dateTo?: string
  page?: number
  pageSize?: number
}

export function listAccounts(
  status?: AccountStatus | "",
  page = 1,
  pageSize = 20,
): Promise<Page<Account>> {
  const query = new URLSearchParams({ page: String(page), page_size: String(pageSize) })
  if (status) query.set("status", status)
  return apiFetch<Page<Account>>(`/accounts?${query.toString()}`)
}

export function getMailDeliverySettings(): Promise<MailDeliverySettings> {
  return apiFetch<MailDeliverySettings>("/mail/settings")
}

export function saveMailDeliverySettings(
  input: MailDeliverySettingsInput,
): Promise<MailDeliverySettings> {
  return apiFetch<MailDeliverySettings>("/mail/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function testMailDeliverySettings(): Promise<MailDeliverySettingsTestResult> {
  return apiFetch<MailDeliverySettingsTestResult>("/mail/settings/test", {
    method: "POST",
  })
}

export function getMailTemplate(eventKey: MailTemplateEventKey): Promise<MailTemplate> {
  return apiFetch<MailTemplate>(`/mail/settings/templates/${MAIL_TEMPLATE_PATH[eventKey]}`)
}

export function saveMailTemplate(
  eventKey: MailTemplateEventKey,
  input: MailTemplateInput,
): Promise<MailTemplate> {
  return apiFetch<MailTemplate>(`/mail/settings/templates/${MAIL_TEMPLATE_PATH[eventKey]}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function restoreMailTemplate(eventKey: MailTemplateEventKey): Promise<MailTemplate> {
  return apiFetch<MailTemplate>(`/mail/settings/templates/${MAIL_TEMPLATE_PATH[eventKey]}`, {
    method: "DELETE",
  })
}

export function previewMailTemplate(
  eventKey: MailTemplateEventKey,
  input: MailTemplateInput,
): Promise<MailTemplatePreview> {
  return apiFetch<MailTemplatePreview>(
    `/mail/settings/templates/${MAIL_TEMPLATE_PATH[eventKey]}/preview`,
    {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
    },
  )
}

export function getVerificationCodeMailTemplate(): Promise<MailTemplate> {
  return getMailTemplate("verification_code")
}

export function saveVerificationCodeMailTemplate(input: MailTemplateInput): Promise<MailTemplate> {
  return saveMailTemplate("verification_code", input)
}

export function restoreVerificationCodeMailTemplate(): Promise<MailTemplate> {
  return restoreMailTemplate("verification_code")
}

export function previewVerificationCodeMailTemplate(
  input: MailTemplateInput,
): Promise<MailTemplatePreview> {
  return previewMailTemplate("verification_code", input)
}

export function createAccount(personId: string): Promise<AccountCreated> {
  return apiFetch<AccountCreated>("/accounts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ person_id: personId }),
  })
}

export function listAccountCandidates(name: string): Promise<AccountCandidate[]> {
  const query = new URLSearchParams({ name })
  return apiFetch<AccountCandidate[]>(`/account-candidates?${query.toString()}`)
}

export function disableAccount(accountId: string): Promise<void> {
  return apiFetch<void>(`/accounts/${accountId}/disable`, { method: "POST" })
}

export function enableAccount(accountId: string): Promise<void> {
  return apiFetch<void>(`/accounts/${accountId}/enable`, { method: "POST" })
}

export function deleteAccount(accountId: string): Promise<void> {
  return apiFetch<void>(`/accounts/${accountId}`, { method: "DELETE" })
}

export function resetAccountPassword(
  accountId: string,
): Promise<{ account_id: string; temporary_password: string }> {
  return apiFetch(`/accounts/${accountId}/reset-password`, { method: "POST" })
}

export function listRoles(): Promise<Role[]> {
  return apiFetch<Role[]>("/roles")
}

export function listPermissions(): Promise<Permission[]> {
  return apiFetch<Permission[]>("/permissions")
}

export function getAccountAccess(accountId: string): Promise<AccountAccess> {
  return apiFetch<AccountAccess>(`/accounts/${accountId}/access`)
}

export function listPermissionAccounts(): Promise<PermissionAccountDirectoryItem[]> {
  return apiFetch<PermissionAccountDirectoryItem[]>("/permission-account-directory")
}

export function listPermissionPeople(name: string): Promise<PermissionPersonDirectoryItem[]> {
  const query = new URLSearchParams({ name })
  return apiFetch<PermissionPersonDirectoryItem[]>(
    `/permission-person-directory?${query.toString()}`,
  )
}

export function listPermissionClasses(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionClassDirectoryItem>> {
  const query = new URLSearchParams({
    query: queryText,
    page: String(page),
    page_size: String(pageSize),
  })
  return apiFetch<Page<PermissionClassDirectoryItem>>(
    `/permission-class-directory?${query.toString()}`,
  )
}

export function listPermissionTeams(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionTeamDirectoryItem>> {
  const query = new URLSearchParams({ query: queryText, page: String(page), page_size: String(pageSize) })
  return apiFetch<Page<PermissionTeamDirectoryItem>>(`/permissions/directory/teams?${query.toString()}`)
}

export function listPermissionBuildings(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionBuildingDirectoryItem>> {
  const query = new URLSearchParams({
    query: queryText,
    page: String(page),
    page_size: String(pageSize),
  })
  return apiFetch<Page<PermissionBuildingDirectoryItem>>(
    `/permission-building-directory?${query.toString()}`,
  )
}

export function listPermissionFloors(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionFloorDirectoryItem>> {
  const query = new URLSearchParams({
    query: queryText,
    page: String(page),
    page_size: String(pageSize),
  })
  return apiFetch<Page<PermissionFloorDirectoryItem>>(
    `/permission-floor-directory?${query.toString()}`,
  )
}

function listPermissionObjects<T>(
  kind: "cohort" | "project" | "enrollment",
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<T>> {
  const query = new URLSearchParams({
    query: queryText,
    page: String(page),
    page_size: String(pageSize),
  })
  return apiFetch<Page<T>>(`/permission-${kind}-directory?${query.toString()}`)
}

export function listPermissionCohorts(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionCohortDirectoryItem>> {
  return listPermissionObjects("cohort", queryText, page, pageSize)
}

export function listPermissionProjects(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionProjectDirectoryItem>> {
  return listPermissionObjects("project", queryText, page, pageSize)
}

export function listPermissionEnrollments(
  queryText: string,
  page = 1,
  pageSize = 20,
): Promise<Page<PermissionEnrollmentDirectoryItem>> {
  return listPermissionObjects("enrollment", queryText, page, pageSize)
}

export function assignRole(accountId: string, roleId: string): Promise<void> {
  return apiFetch<void>(`/accounts/${accountId}/roles`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role_id: roleId }),
  })
}

export function removeRole(accountId: string, roleId: string): Promise<void> {
  return apiFetch<void>(`/accounts/${accountId}/roles/${roleId}`, { method: "DELETE" })
}

export function changeAccountJobRole(accountId: string, input: AccountJobRoleChangeInput): Promise<AccountJobRoleChangeResult> {
  return apiFetch<AccountJobRoleChangeResult>(`/accounts/${accountId}/job-role-change`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function addPermissionOverride(
  accountId: string,
  input: PermissionOverrideInput,
): Promise<PermissionOverride> {
  return apiFetch<PermissionOverride>(`/accounts/${accountId}/permission-overrides`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  })
}

export function previewPermissionOverride(
  accountId: string,
  input: PermissionOverrideInput,
): Promise<PermissionPreview> {
  return apiFetch<PermissionPreview>(
    `/accounts/${accountId}/permission-overrides/preview`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(input),
    },
  )
}

export function removePermissionOverride(accountId: string, overrideId: string): Promise<void> {
  return apiFetch<void>(`/accounts/${accountId}/permission-overrides/${overrideId}`, {
    method: "DELETE",
  })
}

export function listAuditEvents(filters: AuditFilters): Promise<Page<AuditEvent>> {
  const query = new URLSearchParams({
    page: String(filters.page ?? 1),
    page_size: String(filters.pageSize ?? 20),
  })
  if (filters.actorUserId) query.set("actor_user_id", filters.actorUserId)
  if (filters.action) query.set("action", filters.action)
  if (filters.resourceType) query.set("resource_type", filters.resourceType)
  if (filters.dateFrom) query.set("date_from", filters.dateFrom)
  if (filters.dateTo) query.set("date_to", filters.dateTo)
  return apiFetch<Page<AuditEvent>>(`/audit-events?${query.toString()}`)
}
