import { z } from 'zod'
import { apiFetch, apiFetchResponse, ProblemDetailsError } from '../api/client'
import type { AuthUser } from '../auth/AuthProvider'
import { parseItemRankingView } from '../assessmentRankings/itemContracts'
import { parseArchiveRequest, parseUncertaintyPage, parseUncertaintyReceipt } from './uncertainty'
import { parseResultEnvelope, parseResultReceipt, validateManifest, type DeviceDefinition, type MetricResult, type Mode, type ResultEnvelope, type ResultReceipt, type RunConfig, type RunLayout, type RunManifest } from './contracts'

export interface PairingView { pairing_id: string; challenge: string; pair_uri: string; expires_at: string; verification_phrase: string }
export interface ClaimView { pairing_id: string; connector_id: string | null; verification_phrase: string; expires_at: string; status: string; browser_confirmed: boolean; device_confirmed: boolean; actor_account_id: string; actor_name: string; origin: string }
export interface ConnectorView { connector_id: string; name: string; online: boolean; compatible: boolean; locally_confirmed: boolean; protocol_version: number | null; connector_version: string | null; devices: (DeviceDefinition & { label: string; connected: boolean })[]; active_run_id: string | null; attention_required: boolean; unsafe_operation_id: string | null; unsafe_device_keys: string[] }
export interface RunView { run_id: string; connector_id: string; status: string; manifest: RunManifest; lease_expires_at: string; lease_remaining_ms: number; stop_confirmed: boolean; result_saved: boolean }
export interface ModeView { mode: Mode; available: boolean; verification_status: string; reason: string | null }
export interface ReleaseView { available: boolean; release_id: string | null; version: string | null; source_commit: string | null; protocol_version: number | null; filename: string | null; sha256: string | null; size: number | null; signature_status: string | null; distribution_status: 'unavailable' | 'internal_test' }
export interface SubjectView { id: string; name: string; nickname: string | null }
export interface ResultSummary { receipt: ResultReceipt; subject_person_id: string; actor_account_id: string; mode: Mode; end_kind: string; metrics: MetricResult; ranking_eligible: boolean; ranking_reason: string | null }
export interface ResultReport { receipt: ResultReceipt; subject_person_id: string; actor_account_id: string; manifest: RunManifest; envelope: ResultEnvelope; metrics: MetricResult; ranking_eligible: boolean; ranking_reason: string | null }
export interface ResultPage { items: ResultSummary[]; next_cursor: string | null }
export interface PrepareInput { person_id: string; connector_id: string; mode: Mode; config: RunConfig; devices: DeviceDefinition[]; layout: RunLayout }
export interface UploadAuthorization { authorization_id: string; run_id: string; digest: string; kind: 'original_reauthorization' | 'superadmin_recovery'; status: 'authorized'; expires_at: string }
export interface RecoveryRequest { request_id: string; run_id: string; digest: string; status: 'confirmation_required'; expires_at: string }
export const modeLabels: Record<Mode, string> = { manual: '自由训练', standard: '标准反应', sequence: '顺序反应', simultaneous: '同时反应', base: '基地反应', auditory: '听觉反应', true_false: '真假判断', counter: '计数反应', whack: '打地鼠' }
export function effective(user: AuthUser | null, permission: string) { return Boolean(user?.permissions.includes(permission) || user?.scoped_permissions?.includes(permission)) }
export function canControl(user: AuthUser | null) { return user?.is_super_admin === true && effective(user, 'reactionx.manage') && effective(user, 'reactionx.conduct') }
export function canReadReports(user: AuthUser | null) { return effective(user, 'talent_tests.read') }
export function identityKey(user: AuthUser) { return JSON.stringify([user.account_id, user.is_super_admin, [...user.permissions].sort(), [...user.scoped_permissions ?? []].sort()]) }
export function privateError(error: unknown) { return error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status) }
export function errorText(error: unknown) { return privateError(error) ? '记录不存在或无权操作，请重新确认当前账号权限。' : '请求失败，状态尚未确认。请检查网络后刷新，不要重复开始。' }

// A fresh module/document identity is intentionally NOT persisted to Web Storage.
// locally_confirmed from discovery is NOT proof of ownership by this document.
type DocumentControl = { account: string; tabId: string; connectorId: string | null; runId: string | null; stoppingRunId: string | null }
let current: DocumentControl | null = null
function clearControl() {
  current = null
  if (typeof window !== 'undefined') window.dispatchEvent(new Event('reactionx-control-cleared'))
}
if (typeof window !== 'undefined') window.addEventListener('reactionx-auth-boundary', event => {
  const account = (event as CustomEvent<string | null>).detail
  if (!account || (current && current.account !== account)) clearControl()
})
export const documentControl = {
  forAccount(account: string): DocumentControl {
    if (!current || current.account !== account) current = { account, tabId: crypto.randomUUID(), connectorId: null, runId: null, stoppingRunId: null }
    return current
  },
  isCurrent(proof: DocumentControl) { return current === proof },
  clear: clearControl,
  async release() {
    const previous = current
    clearControl()
    if (!previous) return
    // Ordinary logout ends browser control, not the connector identity needed
    // for safety OFF acknowledgements and authorized retained-package recovery.
    // Neither HTTP acceptance nor logout proves physical OFF.
    if (previous.runId) await reactionxApi.operation(previous.runId, 'stop', previous.tabId, AbortSignal.timeout(600)).catch(() => undefined)
  },
}
if (typeof window !== 'undefined') window.addEventListener('reactionx-before-logout', event => {
  const pending = (event as CustomEvent<Promise<void>[]>).detail
  if (Array.isArray(pending)) pending.push(documentControl.release())
})
function request<T>(path: string, signal?: AbortSignal, body?: unknown, method = body === undefined ? 'GET' : 'POST') {
  return apiFetch<T>(`/reactionx${path}`, { method, signal, cache: 'no-store', ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }) })
}
const uuid = z.uuid()
const metricSchema = z.object({ eligible: z.boolean(), reason: z.string().nullable(), values: z.record(z.string(), z.number().finite().nullable()), comparison_key: z.string(), dimensions: z.array(z.object({ key: z.string(), direction: z.enum(['asc', 'desc']), unit: z.enum(['ms', 'count']), numerator: z.number().int().nonnegative(), denominator: z.number().int().positive() })) })
const summarySchema = z.object({ receipt: z.unknown(), subject_person_id: uuid, actor_account_id: uuid, mode: z.enum(['manual', 'standard', 'sequence', 'simultaneous', 'base', 'auditory', 'true_false', 'counter', 'whack']), end_kind: z.string(), metrics: metricSchema, ranking_eligible: z.boolean(), ranking_reason: z.string().nullable() })
export const reactionxApi = {
  pair: (tabId: string, signal?: AbortSignal) => request<PairingView>('/pairings', signal, { browser_tab_id: tabId }),
  pairing: (id: string, signal?: AbortSignal) => request<ClaimView>(`/pairings/${encodeURIComponent(id)}`, signal),
  confirm: (id: string, phrase: string, signal?: AbortSignal) => request<ClaimView>(`/pairings/${encodeURIComponent(id)}/confirm`, signal, { verification_phrase: phrase }),
  cancel: (id: string, signal?: AbortSignal) => request<void>(`/pairings/${encodeURIComponent(id)}`, signal, undefined, 'DELETE'),
  revoke: (id: string, signal?: AbortSignal) => request<void>(`/connectors/${encodeURIComponent(id)}`, signal, undefined, 'DELETE'),
  connectors: (signal?: AbortSignal) => request<ConnectorView[]>('/connectors', signal),
  modes: (signal?: AbortSignal) => request<ModeView[]>('/modes', signal),
  release: (signal?: AbortSignal) => request<ReleaseView>('/releases/current', signal),
  people: (query: string, signal?: AbortSignal) => request<SubjectView[]>(`/people?${new URLSearchParams({ query })}`, signal),
  device: (connector: string, tab: string, kind: 'scan' | 'connect' | 'disconnect' | 'identify', payload: object, signal?: AbortSignal) => request(`/connectors/${encodeURIComponent(connector)}/commands`, signal, { operation_id: crypto.randomUUID(), tab_id: tab, kind, payload }),
  prepare: (input: PrepareInput, tab: string, signal?: AbortSignal) => request<RunView>('/runs/prepare', signal, { ...input, operation_id: crypto.randomUUID(), tab_id: tab }),
  run: (id: string, signal?: AbortSignal) => request<RunView>(`/runs/${encodeURIComponent(id)}`, signal),
  operation: (id: string, kind: 'start' | 'stop', tab: string, signal?: AbortSignal) => request<{ run_status: string }>(`/runs/${encodeURIComponent(id)}/${kind}`, signal, { operation_id: crypto.randomUUID(), tab_id: tab }),
  lease: (id: string, tab: string, signal?: AbortSignal) => request(`/runs/${encodeURIComponent(id)}/lease`, signal, { tab_id: tab }),
  activate: (id: string, tab: string, device_key: string, color: number, signal?: AbortSignal) => request(`/runs/${encodeURIComponent(id)}/manual-activate`, signal, { operation_id: crypto.randomUUID(), tab_id: tab, payload: { device_key, color } }),
  authorizeUpload: (id: string, digest: string, signal?: AbortSignal) => request<UploadAuthorization>(`/runs/${encodeURIComponent(id)}/upload-authorization`, signal, { digest, confirm_original_package: true }),
  recover: (id: string, digest: string, reason: string, signal?: AbortSignal) => request<RecoveryRequest>(`/runs/${encodeURIComponent(id)}/recovery`, signal, { digest, reason, confirmation: true }),
  confirmRecovery: (id: string, requestId: string, signal?: AbortSignal) => request<UploadAuthorization>(`/runs/${encodeURIComponent(id)}/recovery/${encodeURIComponent(requestId)}/confirm`, signal, { confirmation: true }),
  async uncertainties(offset = 0, signal?: AbortSignal) {
    const page = parseUncertaintyPage(await request(`/uncertainties?${new URLSearchParams({ offset: String(offset), limit: '20' })}`, signal))
    if (page.offset !== offset || page.limit !== 20) throw new Error('Invalid uncertainty page identity')
    return page
  },
  async requestUncertaintyArchive(id: string, digest: string, reason: string, signal?: AbortSignal) {
    return parseArchiveRequest(await request(`/runs/${encodeURIComponent(id)}/uncertainty/request`, signal, { declaration_digest: digest, reason, confirmation: true }), id, digest)
  },
  async confirmUncertaintyArchive(id: string, requestId: string, digest: string, signal?: AbortSignal) {
    return parseUncertaintyReceipt(await request(`/runs/${encodeURIComponent(id)}/uncertainty/confirm`, signal, { request_id: requestId, confirmation: true }), id, digest)
  },
  async reports(person?: string, cursor?: string, signal?: AbortSignal): Promise<ResultPage> {
    const params = new URLSearchParams({ limit: '20' }); if (person) params.set('person_id', person); if (cursor) params.set('cursor', cursor)
    const parsed = z.object({ items: z.array(summarySchema), next_cursor: z.string().nullable() }).parse(await request(`/results?${params}`, signal))
    return { ...parsed, items: parsed.items.map(item => ({ ...item, receipt: parseResultReceipt(item.receipt) })) }
  },
  async report(id: string, signal?: AbortSignal): Promise<ResultReport> {
    const data = z.object({ receipt: z.unknown(), subject_person_id: uuid, actor_account_id: uuid, manifest: z.unknown(), envelope: z.unknown(), metrics: metricSchema, ranking_eligible: z.boolean(), ranking_reason: z.string().nullable() }).parse(await request(`/results/${encodeURIComponent(id)}`, signal))
    const receipt = parseResultReceipt(data.receipt), manifest = validateManifest(data.manifest), envelope = parseResultEnvelope(data.envelope)
    if (receipt.result_id !== id || receipt.run_id !== manifest.run_id || envelope.run_id !== manifest.run_id || envelope.manifest_digest !== manifest.manifest_digest || data.subject_person_id !== manifest.subject_person_id || data.actor_account_id !== manifest.actor_account_id) throw new Error('Invalid report identity')
    return { ...data, receipt, manifest, envelope }
  },
  async ranking(id: string, signal?: AbortSignal) { return parseItemRankingView(await request(`/results/${encodeURIComponent(id)}/ranking`, signal)) },
  async download(release: ReleaseView, signal?: AbortSignal) {
    if (!release.available || !release.release_id || !/^[a-f0-9]{64}$/.test(release.release_id) || release.distribution_status !== 'internal_test') throw new Error('Package unavailable')
    const response = await apiFetchResponse(`/reactionx/releases/${release.release_id}/download`, { cache: 'no-store', signal })
    return response.blob()
  },
}
