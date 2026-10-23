import { apiFetch } from "../api/client"

export type HealthStatus = "healthy" | "warning" | "critical" | "unknown"

export type OperationsOverview = {
  generated_at: string
  window_minutes: number
  health: {
    score: number
    status: HealthStatus
  }
  traffic: {
    request_count: number
    client_error_count: number
    client_error_details: Array<{
      status_code: number
      count: number
      reason: string
    }>
    server_error_count: number
    error_rate_percent: number
    requests_per_second: number
    average_latency_ms: number
    p50_latency_ms: number
    p95_latency_ms: number
    p99_latency_ms: number
    max_latency_ms: number
    series: Array<{
      starts_at: string
      request_count: number
      error_count: number
      average_latency_ms: number
    }>
    top_routes: Array<{
      method: string
      route: string
      request_count: number
      error_count: number
      average_latency_ms: number
    }>
  }
  database: {
    status: HealthStatus
    response_time_ms: number | null
    active_connections: number | null
    idle_connections: number | null
    total_connections: number | null
    max_connections: number | null
    utilization_percent: number | null
  }
  runtime: {
    status: HealthStatus
    cpu_percent: number
    memory_used_mb: number
    memory_limit_mb: number | null
    memory_percent: number | null
    process_memory_mb: number
    uptime_seconds: number
  }
  background_tasks: {
    status: HealthStatus
    scheduler_status: HealthStatus
    scheduler_last_seen_at: string | null
    scheduler_lag_seconds: number | null
    scheduler_consecutive_failures: number
    scheduler_last_result_count: number | null
    total_tasks: number
    queued_tasks: number
    processing_tasks: number
    pending_review_tasks: number
    failed_tasks: number
    needs_attention_tasks: number
  }
}

export const operationsKeys = {
  overview: (windowMinutes: number) => ["operations", "overview", windowMinutes] as const,
}

export function getOperationsOverview(windowMinutes: number): Promise<OperationsOverview> {
  return apiFetch<OperationsOverview>(
    `/operations/overview?window_minutes=${windowMinutes}`,
  )
}

export type OperationsHistoryPoint = {
  starts_at: string
  request_count: number
  client_error_count: number
  server_error_count: number
  average_latency_ms: number
  max_latency_ms: number
}

export type OperationsHistory = {
  items: OperationsHistoryPoint[]
  total: number
  page: number
  page_size: number
  retention_days: number
}

export type OperationsAlert = {
  id: string
  key: string
  severity: "warning" | "critical"
  title: string
  detail: string
  status: "active" | "resolved"
  first_seen_at: string
  last_seen_at: string
  resolved_at: string | null
  acknowledged_at: string | null
  acknowledged_by: string | null
}

export type OperationsAlerts = {
  items: OperationsAlert[]
  total: number
  page: number
  page_size: number
}

export type AlertFilter = "active" | "resolved" | "all"

export function getOperationsHistory(startsAt: string, endsAt: string, page: number): Promise<OperationsHistory> {
  const params = new URLSearchParams({ starts_at: startsAt, ends_at: endsAt, page: String(page), page_size: "20" })
  return apiFetch<OperationsHistory>(`/operations/history?${params}`)
}

export function getOperationsAlerts(status: AlertFilter, page: number): Promise<OperationsAlerts> {
  return apiFetch<OperationsAlerts>(`/operations/alerts?status=${status}&page=${page}&page_size=10`)
}

export function acknowledgeOperationsAlert(id: string): Promise<OperationsAlert> {
  return apiFetch<OperationsAlert>(`/operations/alerts/${encodeURIComponent(id)}/acknowledge`, { method: "POST" })
}

export type OperationsBackupStatus = {
  status: "running" | "ok" | "failed" | "unknown"
  completed_at: string | null
  last_success_at: string | null
  started_at: string | null
  verification_at: string | null
  verification_status: "verified" | "unverified"
  size_bytes: number | null
  error_code: string | null
  reason: string
  secondary_copy: {
    configured: boolean
    status: "not_configured" | "ok" | "failed" | "unknown"
    last_success_at: string | null
  }
}

export function getOperationsBackupStatus(): Promise<OperationsBackupStatus> {
  return apiFetch<OperationsBackupStatus>("/operations/backup-status")
}
