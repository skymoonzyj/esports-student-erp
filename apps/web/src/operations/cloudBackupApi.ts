import { apiFetch } from "../api/client"
export type CloudProvider = "baidu" | "115" | "quark"
export type CloudTarget = { provider: CloudProvider; enabled: boolean; configured: boolean; remote_path: string | null; username: string | null; credential_present: boolean; revision: number; connection_status: "untested" | "ok" | "failed"; last_tested_at: string | null; last_success_at: string | null; last_backup_id: string | null; last_error_code: string | null; managed?: boolean; authorization_status?: "manual" | "valid" | "reauthorization_required"; authorization_mode?: "own_app" | "openlist_builtin_import" | null; selected_directory?: string | null }
export type CloudJob = { id: string; provider: CloudProvider; kind: "test" | "copy"; status: "queued" | "running" | "succeeded" | "failed"; created_at: string; started_at: string | null; completed_at: string | null; backup_id: string | null; error_code: string | null }
export type CloudConfig = { remote_path: string; username: string; password?: string; enabled: boolean }
export async function getCloudBackups(signal?: AbortSignal): Promise<{ targets: CloudTarget[]; jobs: CloudJob[]; worker_last_seen_at: string | null }> {
  const value = await apiFetch<{ targets: CloudTarget[]; jobs: CloudJob[]; worker_last_seen_at: string | null }>("/operations/cloud-backups", { signal, cache: "no-store" })
  if (value.targets.some(target => target.authorization_mode != null && !["own_app", "openlist_builtin_import"].includes(target.authorization_mode))) throw new Error("云备份授权来源无效")
  return value
}
export function saveCloudBackup(provider: CloudProvider, config: CloudConfig, signal?: AbortSignal): Promise<CloudTarget> { return apiFetch(`/operations/cloud-backups/${provider}`, { method: "PUT", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) }) }
export function enqueueCloudBackup(provider: CloudProvider, kind: "test" | "copy", signal?: AbortSignal): Promise<CloudJob> { return apiFetch(`/operations/cloud-backups/${provider}/${kind}`, { method: "POST", signal, cache: "no-store" }) }
