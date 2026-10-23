import { apiFetch } from "../api/client"

export type ActivationStart = {
  accepted: boolean
  resend_available_at: string
  expires_at: string
}

export type ActivationPreview = {
  masked_name: string
  kind: "student" | "player" | "both" | "staff"
}

export type ActivationCompleted = { completed: true }

function record(value: unknown, keys: string[]): Record<string, unknown> {
  if (
    typeof value !== "object" || value === null || Array.isArray(value) ||
    Object.keys(value).length !== keys.length ||
    !keys.every((key) => Object.hasOwn(value, key))
  ) throw new Error("Invalid activation response")
  return value as Record<string, unknown>
}

function datetime(value: unknown): value is string {
  return typeof value === "string" &&
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/.test(value) &&
    Number.isFinite(Date.parse(value))
}

async function post(path: string, body: object, signal?: AbortSignal): Promise<unknown> {
  return apiFetch<unknown>(`/auth/profile-activation/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  })
}

export async function startProfileActivation(email: string, signal?: AbortSignal): Promise<ActivationStart> {
  const result = record(await post("start", { email }, signal), ["accepted", "resend_available_at", "expires_at"])
  if (typeof result.accepted !== "boolean" || !datetime(result.resend_available_at) || !datetime(result.expires_at)) {
    throw new Error("Invalid activation response")
  }
  return { accepted: result.accepted, resend_available_at: result.resend_available_at, expires_at: result.expires_at }
}

export async function verifyProfileActivation(code: string, signal?: AbortSignal): Promise<ActivationPreview> {
  const result = record(await post("verify", { code }, signal), ["masked_name", "kind"])
  if (typeof result.masked_name !== "string" ||
    (result.kind !== "student" && result.kind !== "player" && result.kind !== "both" && result.kind !== "staff")) {
    throw new Error("Invalid activation response")
  }
  return { masked_name: result.masked_name, kind: result.kind }
}

export async function completeProfileActivation(code: string, password: string, signal?: AbortSignal): Promise<ActivationCompleted> {
  const result = record(await post("complete", { code, password }, signal), ["completed"])
  if (result.completed !== true) throw new Error("Invalid activation response")
  return { completed: true }
}
