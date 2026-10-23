export type ProblemDetails = {
  type: string
  title: string
  status: number
  detail: string
  code: string
  request_id: string
  errors?: Array<{ field: string; message: string }>
}

const GENERIC_REQUEST_ERROR = "服务暂时不可用，请重试。"

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function problemString(
  problem: Record<string, unknown>,
  field: string,
  fallback: string,
): string {
  const value = problem[field]
  return typeof value === "string" ? value : fallback
}

function problemErrors(
  value: unknown,
): ProblemDetails["errors"] | undefined {
  if (!Array.isArray(value)) return undefined
  return value.flatMap((item) => {
    if (!isRecord(item)) return []
    if (typeof item.field !== "string" || typeof item.message !== "string") {
      return []
    }
    return [{ field: item.field, message: item.message }]
  })
}

function normalizedProblem(
  responseStatus: number,
  value: unknown,
): ProblemDetails {
  const problem = isRecord(value) ? value : {}
  const errors = problemErrors(problem.errors)
  return {
    type: problemString(problem, "type", "about:blank"),
    title: problemString(problem, "title", "Request failed"),
    status: responseStatus,
    detail: problemString(problem, "detail", GENERIC_REQUEST_ERROR),
    code: problemString(problem, "code", "http_error"),
    request_id: problemString(problem, "request_id", ""),
    ...(errors === undefined ? {} : { errors }),
  }
}

export class ProblemDetailsError extends Error {
  constructor(public readonly problem: ProblemDetails) {
    super(problem.detail)
    this.name = "ProblemDetailsError"
  }

  static async fromResponse(response: Response): Promise<ProblemDetailsError> {
    let value: unknown
    try {
      value = await response.json()
    } catch {
      value = null
    }
    return new ProblemDetailsError(normalizedProblem(response.status, value))
  }
}

let csrfToken: string | null = null
let invalidSessionHandler: (() => void) | null = null

export function setCsrfToken(value: string | null): void {
  csrfToken = value
}

export function setInvalidSessionHandler(handler: (() => void) | null): void {
  invalidSessionHandler = handler
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const method = (init.method ?? "GET").toUpperCase()
  if (csrfToken && !["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers.set("X-CSRF-Token", csrfToken)
  }
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  })
  if (!response.ok) {
    const error = await ProblemDetailsError.fromResponse(response)
    if (response.status === 401 && error.problem.code === "invalid_session") {
      invalidSessionHandler?.()
    }
    throw error
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}
