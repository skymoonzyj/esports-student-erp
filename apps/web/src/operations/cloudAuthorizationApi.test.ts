import { afterEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import * as api from "./cloudAuthorizationApi"
import { getCloudBackups } from "./cloudBackupApi"

afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })
const importedSession = { id: "session", provider: "baidu", mode: "openlist_builtin_import", state: "awaiting_import", revision: 1, expires_at: "2099-01-01T00:00:00Z", account_label: null, selected_directory: null, error_code: null, next_poll_after_seconds: 1, verified_at: null, token_expires_at: null, expiry_source: null }
it("sends explicit builtin mode but keeps an omitted mode as a legacy own-app request", async () => {
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify(importedSession)))
  vi.stubGlobal("fetch", fetch)
  const signal = new AbortController().signal
  await api.createCloudAuthorization("baidu", "create", signal, "openlist_builtin_import")
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ operation_id: "create", mode: "openlist_builtin_import" })
  await api.createCloudAuthorization("baidu", "legacy", signal)
  expect(JSON.parse(fetch.mock.calls[1][1].body)).toEqual({ operation_id: "legacy" })
})
it("posts import credentials only in protected JSON and queries the same public operation UUID", async () => {
  const operation_id = "00000000-0000-4000-8000-000000000001"
  const fetch = vi.fn().mockImplementation(async () => new Response(JSON.stringify({ operation_id, status: "pending", session: importedSession, error_code: null, access_token: "server-private" })))
  vi.stubGlobal("fetch", fetch)
  const signal = new AbortController().signal
  const value = await api.submitCloudAuthorizationImport("baidu", "session", { operation_id, expected_revision: 1, access_token: "synthetic-access", refresh_token: "synthetic-refresh" }, signal)
  expect(fetch.mock.calls[0][0]).toBe("/api/v1/operations/cloud-backups/baidu/authorization/sessions/session/import")
  expect(fetch.mock.calls[0][1]).toMatchObject({ cache: "no-store", signal, method: "POST" })
  expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual({ operation_id, expected_revision: 1, access_token: "synthetic-access", refresh_token: "synthetic-refresh" })
  expect(JSON.stringify(value)).not.toMatch(/server-private|access_token/)
  await api.getCloudImportOperation("baidu", "session", operation_id, signal)
  expect(fetch.mock.calls[1][0]).toBe(`/api/v1/operations/cloud-backups/baidu/authorization/sessions/session/operations/${operation_id}`)
  expect(localStorage.length + sessionStorage.length).toBe(0)
})
it("rejects unknown explicit authorization modes instead of mislabelling their source", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ...importedSession, mode: "unknown" }))))
  await expect(api.getCloudAuthorization("baidu", "session", new AbortController().signal)).rejects.toMatchObject({ code: "cloud_auth_response_invalid" })
})
it.each(["cloud_auth_body_too_large", "cloud_import_rate_limited", "cloud_import_tokens_invalid", "cloud_import_credentials_reused", "cloud_import_account_mismatch", "cloud_import_checkpoint_invalid", "cloud_refresh_outcome_unknown", "validation_error"])("retains safe import error %s without vendor messages or validation input", async code => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code, detail: "synthetic-private", errors: [{ field: "access_token", message: "synthetic-private", input: "synthetic-private" }] }), { status: code === "cloud_import_rate_limited" ? 429 : 422 })))
  let caught: unknown
  try { await api.getCloudAuthorization("baidu", "session", new AbortController().signal) } catch (error) { caught = error }
  expect(caught).toMatchObject({ code })
  expect(String(caught)).not.toContain("synthetic-private")
})
it("rejects an unknown target authorization source instead of allowing a misleading label", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ targets: [{ provider: "baidu", authorization_mode: "unknown" }], jobs: [], worker_last_seen_at: null }))))
  await expect(getCloudBackups()).rejects.toThrow()
})
it("uses no-store and abort signals, sends secrets only in JSON, and keeps QR off shared browser storage", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ provider: "baidu", client_id: "app", secret_present: true, configured: true, revision: 2, prerequisites: [] })))
  vi.stubGlobal("fetch", fetch)
  setCsrfToken("csrf")
  const signal = new AbortController().signal
  await api.saveCloudApplication("baidu", { client_id: "app", client_secret: "test-secret", clear_secret: false, expected_revision: 1 }, signal)
  expect(fetch).toHaveBeenCalledWith("/api/v1/operations/cloud-backups/baidu/authorization/application", expect.objectContaining({ method: "PUT", cache: "no-store", signal, body: '{"client_id":"app","client_secret":"test-secret","clear_secret":false,"expected_revision":1}' }))
  expect(fetch.mock.calls[0][0]).not.toContain("test-secret")
  expect(fetch.mock.calls[0][1].headers.get("Content-Type")).toBe("application/json")
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})
it("discards provider error bodies, fields and malicious error codes from the public client", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "access_token=private", code: "private-token", errors: [{ field: "secret", message: "private-secret" }] }), { status: 429, headers: { "Retry-After": "25" } })))
  let caught: unknown
  try { await api.getCloudApplication("baidu", new AbortController().signal) } catch (error) { caught = error }
  expect(caught).toMatchObject({ status: 429, code: "cloud_auth_rate_limited", retryAfterSeconds: 25 })
  expect(JSON.stringify(caught)).not.toMatch(/private|access_token/)
  expect(String(caught)).not.toMatch(/private|access_token/)
})
it("rejects external QR links and token-shaped fields rather than retaining raw provider material", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ kind: "payload", content: "https://evil.example/token", official_open_url: "javascript:alert(1)", expires_at: "2099-01-01T00:00:00Z", access_token: "private" }))))
  await expect(api.getCloudAuthorizationQr("baidu", "session", new AbortController().signal)).rejects.toMatchObject({ code: "cloud_auth_response_invalid" })
})
it("also avoids HTTP caching for the existing manual cloud-backup status request", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(JSON.stringify({ targets: [], jobs: [], worker_last_seen_at: null })))
  vi.stubGlobal("fetch", fetch)
  const signal = new AbortController().signal
  await getCloudBackups(signal)
  expect(fetch).toHaveBeenCalledWith("/api/v1/operations/cloud-backups", expect.objectContaining({ signal, cache: "no-store" }))
})
it("never returns a late QR response once its owner abort signal is cancelled", async () => {
  let complete!: (response: Response) => void
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { complete = resolve })))
  const controller = new AbortController()
  const pending = api.getCloudAuthorizationQr("baidu", "session", controller.signal)
  controller.abort()
  complete(new Response(JSON.stringify({ kind: "payload", content: "https://openapi.baidu.com/fictional", official_open_url: null, expires_at: "2099-01-01T00:00:00Z" })))
  await expect(pending).rejects.toMatchObject({ name: "AbortError" })
})
it.each(["cloud_cleanup_pending", "cloud_delivery_unknown", "cloud_directory_delivery_unknown"])("retains canonical %s while discarding raw connector exception material", async code => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ code, detail: "access_token=private-provider-material", errors: [{ field: "secret", message: "private-secret" }] }), { status: 409 })))
  let caught: unknown
  try { await api.getCloudApplication("baidu", new AbortController().signal) } catch (error) { caught = error }
  expect(caught).toMatchObject({ status: 409, code })
  expect(String(caught)).toMatch(/管理员.*核查/)
  expect(String(caught)).toMatch(/受保护候选凭据.*尚未启用/)
  expect(JSON.stringify(caught)).not.toMatch(/private|access_token/)
})
it.each(["pending", "cleanup_pending", "failed", "succeeded"])("projects public %s probe completion without retaining internal or provider material", async status => {
  const probe_attempt = { operation_id: "00000000-0000-4000-8000-000000000001", status }
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ id: "session", provider: "baidu", state: "test_pending", revision: 2, expires_at: "2099-01-01T00:00:00Z", account_label: "用户***", selected_directory: "/ERP", error_code: "cloud_remote_corrupt", next_poll_after_seconds: 1, probe_attempt: { ...probe_attempt, internal_id: "private-internal", access_token: "private-token", error: "private-provider" } }))))
  const value = await api.getCloudAuthorization("baidu", "session", new AbortController().signal)
  expect(value).toHaveProperty("probe_attempt", probe_attempt)
  expect(JSON.stringify(value)).not.toMatch(/private|access_token|internal_id/)
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})
