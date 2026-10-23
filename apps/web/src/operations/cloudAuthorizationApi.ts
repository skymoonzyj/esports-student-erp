import { apiFetch, ProblemDetailsError } from "../api/client"

export type AuthProvider = "baidu" | "115"
export type AuthMode = "own_app" | "openlist_builtin_import"
export type AuthState = "creating" | "awaiting_confirmation" | "awaiting_import" | "verifying_import" | "authorized" | "configuring" | "test_pending" | "ready" | "applying" | "applied" | "expired" | "cancelled" | "failed"
export type CloudAppView = { provider: AuthProvider; client_id: string | null; secret_present: boolean; revision: number; configured: boolean; prerequisites: string[] }
export type CloudAppUpdate = { client_id: string; client_secret?: string; clear_secret: boolean; expected_revision: number }
export type CloudProbeAttempt = { operation_id: string; status: "pending" | "cleanup_pending" | "failed" | "succeeded" }
export type CloudAuthSessionView = { id: string; provider: AuthProvider; mode?: AuthMode; state: AuthState; revision: number; expires_at: string; account_label: string | null; selected_directory: string | null; error_code: string | null; next_poll_after_seconds: number; probe_attempt?: CloudProbeAttempt | null; verified_at?: string | null; token_expires_at?: string | null; expiry_source?: "provider" | "unreported" | null }
export type CloudImportInput = { expected_revision: number; operation_id: string; access_token: string; refresh_token: string }
export type CloudImportOperationView = { operation_id: string; status: "pending" | "succeeded" | "failed" | "cancelled"; session: CloudAuthSessionView; error_code: string | null }
export type QrPresentation = { kind: "image" | "payload"; content: string; official_open_url?: string | null; expires_at: string }
export type CloudDirectoryResult = { operation_id: string; status: "pending" | "ready" | "failed"; entries: { name: string; path: string; selectable: boolean }[]; error_code: string | null }
export type CloudUnbindResponse = { operation_id: string; session_id: string; status: "pending"; official_revoke_required: boolean }

const messages: Record<string, string> = {
  validation_error: "提交字段无效，请重新检查两项授权令牌。",
  cloud_auth_body_too_large: "授权请求过大，请仅粘贴两项令牌。",
  cloud_import_rate_limited: "授权验证提交过于频繁，请按提示稍后重试。",
  cloud_import_tokens_invalid: "授权令牌无效，请重新取得本次授权结果。",
  cloud_import_credentials_reused: "此授权凭据已被使用，请重新取得未被其他挂载使用的授权结果。",
  cloud_import_account_mismatch: "刷新前后账号不一致，旧配置未更改，请重新授权。",
  cloud_import_checkpoint_invalid: "授权验证检查点无效，请重新取得授权结果。",
  cloud_refresh_outcome_unknown: "续期结果未确认，不能重试旧刷新令牌，请重新取得授权结果。",
  cloud_app_unconfigured: "自有应用尚未配置完整。",
  cloud_connector_unavailable: "连接器未安装或不可达，请联系运维配置。",
  cloud_runner_offline: "授权运行器离线，请联系运维检查。",
  cloud_maintenance_active: "系统维护中，暂不能发起授权。",
  cloud_auth_denied: "用户拒绝授权",
  cloud_auth_expired: "授权或配置期限已过期，请重新获取。",
  cloud_auth_rate_limited: "提供商限流，请按提示稍后重试。",
  cloud_auth_qr_unavailable: "二维码正在准备，请等待。",
  cloud_auth_revision_changed: "配置版本已改变，请关闭后重新读取。",
  cloud_auth_state_invalid: "当前状态不允许此操作，请重新读取。",
  cloud_auth_operation_conflict: "操作版本冲突，请重新读取。",
  cloud_auth_live: "此网盘已有进行中的授权，请先完成或取消。",
  cloud_auth_not_found: "授权会话不存在或已失效。",
  cloud_credentials_invalid: "授权已失效，请重新绑定。",
  cloud_auth_identity_unconfirmed: "无法确认账号身份，不能应用候选配置。",
  cloud_auth_directory_denied: "目录无写入权限，请选择其他目录。",
  cloud_provider_unsupported: "提供商不支持当前授权方式，暂不能使用。",
  cloud_provider_throttled: "网盘请求受到限流，请等待服务器提示的间隔。",
  cloud_authorization_invalid: "网盘授权已失效，请重新绑定。",
  cloud_account_unverified: "无法可靠确认网盘账号身份，不能应用。",
  cloud_directory_denied: "目录无写入权限，请选择其他目录。",
  cloud_remote_corrupt: "上传读回校验失败，旧配置未更改。",
  cloud_worker_timeout: "授权运行器处理超时，请重试。",
  cloud_connector_failed: "连接器操作失败，请联系运维检查。",
  cloud_invalid_path: "目录路径无效，请选择专用备份目录。",
  cloud_redirect_rejected: "网盘返回非预期跳转，已拒绝请求。",
  cloud_cleanup_pending: "连接器清理尚未完成，请联系管理员监督核查；受保护候选凭据保留但尚未启用，现有保护配置将保留。",
  cloud_delivery_unknown: "连接器创建结果未确认，请联系管理员暂停连接器后监督核查，勿重复授权；受保护候选凭据保留但尚未启用。",
  cloud_directory_delivery_unknown: "目录配置结果未确认，请联系管理员暂停连接器后监督核查，暂不切换目录；受保护候选凭据保留但尚未启用。",
  cloud_auth_probe_failed: "写入或完整读回校验失败，旧配置未更改。",
  cloud_target_busy: "目标正在执行任务，请完成后再操作。",
  cloud_config_changed: "目标配置已变更，请重新验证。",
  cloud_auth_forbidden: "登录身份或运维权限已改变，授权材料已清除。",
  cloud_auth_response_invalid: "授权响应无效，未展示授权材料。",
  cloud_auth_failed: "操作未完成，请检查授权、连接器与备份状态后重试。",
}
export function cloudAuthorizationReason(code: string | null): string { return code ? messages[code] ?? messages.cloud_auth_failed : messages.cloud_auth_failed }
function safeCode(code: unknown): string { return typeof code === "string" && Object.hasOwn(messages, code) ? code : "cloud_auth_failed" }
export class CloudAuthorizationError extends Error {
  constructor(public readonly status: number, public readonly code: string, public readonly retryAfterSeconds: number | null = null) {
    super(cloudAuthorizationReason(code)); this.name = "CloudAuthorizationError"
  }
}
async function call<T>(provider: AuthProvider, suffix: string, signal: AbortSignal, method = "GET", body?: unknown): Promise<T> {
  try {
    const value = await apiFetch<T>(`/operations/cloud-backups/${provider}/authorization/${suffix}`, { method, signal, cache: "no-store", ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) })
    if (signal.aborted) throw new DOMException("请求已取消", "AbortError")
    return value
  } catch (error) {
    if (signal.aborted) throw new DOMException("请求已取消", "AbortError")
    if (error instanceof ProblemDetailsError) {
      const status = error.problem.status
      throw new CloudAuthorizationError(status, status === 401 || status === 403 ? "cloud_auth_forbidden" : status === 429 && safeCode(error.problem.code) !== "cloud_import_rate_limited" ? "cloud_auth_rate_limited" : safeCode(error.problem.code), error.retryAfterSeconds)
    }
    throw new CloudAuthorizationError(0, "cloud_auth_failed")
  }
}
function application(value: CloudAppView): CloudAppView {
  return { provider: value.provider, client_id: value.client_id, secret_present: value.secret_present, revision: value.revision, configured: value.configured, prerequisites: value.prerequisites.map(safeCode) }
}
function session(value: CloudAuthSessionView): CloudAuthSessionView {
  if (!value || (value.mode !== undefined && value.mode !== "own_app" && value.mode !== "openlist_builtin_import") || (value.expiry_source != null && value.expiry_source !== "provider" && value.expiry_source !== "unreported")) throw new CloudAuthorizationError(0, "cloud_auth_response_invalid")
  const attempt = value.probe_attempt
  const probe_attempt = attempt && typeof attempt.operation_id === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(attempt.operation_id) && ["pending", "cleanup_pending", "failed", "succeeded"].includes(attempt.status) ? { operation_id: attempt.operation_id, status: attempt.status } : null
  return { id: value.id, provider: value.provider, mode: value.mode ?? "own_app", state: value.state, revision: value.revision, expires_at: value.expires_at, account_label: value.account_label, selected_directory: value.selected_directory, error_code: value.error_code ? safeCode(value.error_code) : null, next_poll_after_seconds: value.next_poll_after_seconds, probe_attempt, verified_at: value.verified_at ?? null, token_expires_at: value.token_expires_at ?? null, expiry_source: value.expiry_source ?? null }
}
function directories(value: CloudDirectoryResult): CloudDirectoryResult {
  return { operation_id: value.operation_id, status: value.status, entries: value.entries.map(entry => ({ name: entry.name, path: entry.path, selectable: entry.selectable })), error_code: value.error_code ? safeCode(value.error_code) : null }
}
export async function getCloudApplication(provider: AuthProvider, signal: AbortSignal) { return application(await call<CloudAppView>(provider, "application", signal)) }
export async function saveCloudApplication(provider: AuthProvider, update: CloudAppUpdate, signal: AbortSignal) { return application(await call<CloudAppView>(provider, "application", signal, "PUT", update)) }
export async function createCloudAuthorization(provider: AuthProvider, operation_id: string, signal: AbortSignal, mode?: AuthMode) { return session(await call<CloudAuthSessionView>(provider, "sessions", signal, "POST", { operation_id, ...(mode ? { mode } : {}) })) }
function importOperation(value: CloudImportOperationView): CloudImportOperationView {
  if (!value || typeof value.operation_id !== "string" || !["pending", "succeeded", "failed", "cancelled"].includes(value.status)) throw new CloudAuthorizationError(0, "cloud_auth_response_invalid")
  return { operation_id: value.operation_id, status: value.status, session: session(value.session), error_code: value.error_code ? safeCode(value.error_code) : null }
}
export async function submitCloudAuthorizationImport(provider: AuthProvider, id: string, input: CloudImportInput, signal: AbortSignal) { return importOperation(await call<CloudImportOperationView>(provider, `sessions/${encodeURIComponent(id)}/import`, signal, "POST", { expected_revision: input.expected_revision, operation_id: input.operation_id, access_token: input.access_token, refresh_token: input.refresh_token })) }
export async function getCloudImportOperation(provider: AuthProvider, id: string, operation_id: string, signal: AbortSignal) { return importOperation(await call<CloudImportOperationView>(provider, `sessions/${encodeURIComponent(id)}/operations/${encodeURIComponent(operation_id)}`, signal)) }
export async function getCloudAuthorization(provider: AuthProvider, id: string, signal: AbortSignal) { return session(await call<CloudAuthSessionView>(provider, `sessions/${encodeURIComponent(id)}`, signal)) }
export async function cancelCloudAuthorization(provider: AuthProvider, id: string, expected_revision: number, signal: AbortSignal) { return session(await call<CloudAuthSessionView>(provider, `sessions/${encodeURIComponent(id)}/cancel`, signal, "POST", { expected_revision })) }
const officialHosts = new Set(["openapi.baidu.com", "passport.baidu.com", "open.115.com", "115.com", "passportapi.115.com", "qrcodeapi.115.com"])
function officialUrl(value: string): boolean {
  try { const url = new URL(value); return url.protocol === "https:" && officialHosts.has(url.hostname) && !url.username && !url.password && (!url.port || url.port === "443") && !/[\s\\]/.test(value) && !Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127) } catch { return false }
}
export async function getCloudAuthorizationQr(provider: AuthProvider, id: string, signal: AbortSignal): Promise<QrPresentation> {
  const value = await call<QrPresentation>(provider, `sessions/${encodeURIComponent(id)}/qr`, signal)
  if (!value || Object.keys(value).some(key => !["kind", "content", "official_open_url", "expires_at"].includes(key)) || typeof value.content !== "string" || value.content.length > 48000 || (value.kind === "payload" ? !officialUrl(value.content) : value.kind !== "image" || !/^data:image\/png;base64,iVBORw0KGgo[A-Za-z0-9+/]*={0,2}$/.test(value.content)) || (value.official_open_url != null && !officialUrl(value.official_open_url)) || !Number.isFinite(Date.parse(value.expires_at))) throw new CloudAuthorizationError(0, "cloud_auth_response_invalid")
  return { kind: value.kind, content: value.content, official_open_url: value.official_open_url ?? null, expires_at: value.expires_at }
}
export async function requestCloudDirectories(provider: AuthProvider, id: string, operation_id: string, expected_revision: number, parent: string, signal: AbortSignal) { return directories(await call<CloudDirectoryResult>(provider, `sessions/${encodeURIComponent(id)}/directories`, signal, "POST", { operation_id, expected_revision, parent })) }
export async function getCloudDirectories(provider: AuthProvider, id: string, operation_id: string, signal: AbortSignal) { return directories(await call<CloudDirectoryResult>(provider, `sessions/${encodeURIComponent(id)}/directories?operation_id=${encodeURIComponent(operation_id)}`, signal)) }
async function command(provider: AuthProvider, id: string, action: string, operation_id: string, expected_revision: number, signal: AbortSignal, extra: object = {}) { return session(await call<CloudAuthSessionView>(provider, `sessions/${encodeURIComponent(id)}/${action}`, signal, "POST", { operation_id, expected_revision, ...extra })) }
export function selectCloudDirectory(provider: AuthProvider, id: string, operation_id: string, expected_revision: number, directory: string, signal: AbortSignal) { return command(provider, id, "select-directory", operation_id, expected_revision, signal, { directory }) }
export function testCloudAuthorization(provider: AuthProvider, id: string, operation_id: string, expected_revision: number, signal: AbortSignal) { return command(provider, id, "test", operation_id, expected_revision, signal) }
export function applyCloudAuthorization(provider: AuthProvider, id: string, operation_id: string, expected_revision: number, signal: AbortSignal) { return command(provider, id, "apply", operation_id, expected_revision, signal) }
export async function unbindCloudAuthorization(provider: AuthProvider, operation_id: string, expected_revision: number, signal: AbortSignal): Promise<CloudUnbindResponse> {
  const value = await call<CloudUnbindResponse>(provider, "unbind", signal, "POST", { operation_id, expected_revision })
  return { operation_id: value.operation_id, session_id: value.session_id, status: value.status, official_revoke_required: value.official_revoke_required }
}
