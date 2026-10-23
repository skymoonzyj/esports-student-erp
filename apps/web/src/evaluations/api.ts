import { apiFetch } from "../api/client"
import type { EvaluationFoundationContext } from "./foundationApi"

export type EvaluationCenterContext = Pick<
  EvaluationFoundationContext,
  | "accessible"
  | "system_evaluation_available"
  | "embedded_start_url"
  | "standalone_start_url"
  | "upload_available"
  | "video_available"
  | "reports_available"
  | "surveys_available"
  | "integration_api_version"
>

export type EvaluationTicketIssued = {
  callback_url: string
  ticket: string
  state: string
  expires_at: string
}

export const evaluationKeys = {
  root: (accountId: string) => ["evaluations", accountId] as const,
  context: (accountId: string) =>
    ["evaluations", accountId, "context"] as const,
}

export function getEvaluationContext(): Promise<EvaluationFoundationContext> {
  return apiFetch<EvaluationFoundationContext>("/evaluations/context")
}

export function createEvaluationTicket(
  requestId: string,
): Promise<EvaluationTicketIssued> {
  return apiFetch<EvaluationTicketIssued>("/evaluations/bridge/tickets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request_id: requestId }),
  })
}
