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
  constructor(
    public readonly problem: ProblemDetails,
    public readonly retryAfterSeconds: number | null = null,
  ) {
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
    const retryAfter = response.headers.get("Retry-After")?.trim() ?? ""
    let retryAfterSeconds: number | null = null
    if (/^\d+$/.test(retryAfter)) {
      const parsed = Number(retryAfter)
      if (Number.isSafeInteger(parsed)) retryAfterSeconds = parsed
    } else if (retryAfter) {
      const retryAt = Date.parse(retryAfter)
      if (Number.isFinite(retryAt)) {
        retryAfterSeconds = Math.max(0, Math.ceil((retryAt - Date.now()) / 1000))
      }
    }
    return new ProblemDetailsError(
      normalizedProblem(response.status, value),
      retryAfterSeconds,
    )
  }
}

let csrfToken: string | null = null
let csrfGeneration = 0
let csrfRestore:
  | { generation: number; promise: Promise<string> }
  | null = null
let invalidSessionHandler: (() => void) | null = null

export function setCsrfToken(value: string | null): void {
  csrfToken = value
  csrfGeneration += 1
}

export function setInvalidSessionHandler(handler: (() => void) | null): void {
  invalidSessionHandler = handler
}

function isSafeMethod(method: string): boolean {
  return ["GET", "HEAD", "OPTIONS"].includes(method)
}

async function request(
  path: string,
  init: RequestInit,
  token: string | null,
): Promise<Response> {
  const headers = new Headers(init.headers)
  const method = (init.method ?? "GET").toUpperCase()
  if (token && !isSafeMethod(method)) {
    headers.set("X-CSRF-Token", token)
  }
  return fetch(`/api/v1${path}`, {
    ...init,
    headers,
    credentials: "include",
  })
}

async function responseError(
  response: Response,
  requestGeneration: number,
): Promise<ProblemDetailsError> {
  const error = await ProblemDetailsError.fromResponse(response)
  if (
    response.status === 401 &&
    error.problem.code === "invalid_session" &&
    requestGeneration === csrfGeneration
  ) {
    invalidSessionHandler?.()
  }
  return error
}

async function restoreCsrfToken(generation: number): Promise<string> {
  if (csrfRestore?.generation === generation) return csrfRestore.promise
  const promise = (async () => {
    const response = await request("/auth/csrf", { method: "GET" }, null)
    if (!response.ok) throw await responseError(response, generation)
    const value = (await response.json()) as unknown
    if (
      !isRecord(value) ||
      typeof value.csrf_token !== "string" ||
      value.csrf_token.length === 0
    ) {
      throw new Error(GENERIC_REQUEST_ERROR)
    }
    return value.csrf_token
  })()
  const restore = { generation, promise }
  csrfRestore = restore
  try {
    return await promise
  } finally {
    if (csrfRestore === restore) csrfRestore = null
  }
}

function canRecoverCsrf(
  requestGeneration: number,
  allowClearedTokenRecovery: boolean,
): boolean {
  return (
    csrfGeneration === requestGeneration ||
    (allowClearedTokenRecovery &&
      csrfGeneration === requestGeneration + 1 &&
      csrfToken === null)
  )
}

export async function apiFetchResponse(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase()
  const requestCsrfToken = csrfToken
  const requestCsrfGeneration = csrfGeneration
  const allowClearedTokenRecovery = path === "/auth/logout"
  let response = await request(path, init, requestCsrfToken)
  if (!response.ok) {
    const error = await responseError(response, requestCsrfGeneration)
    if (
      !isSafeMethod(method) &&
      path !== "/auth/csrf" &&
      requestCsrfToken !== null &&
      response.status === 403 &&
      error.problem.code === "csrf_invalid"
    ) {
      if (!canRecoverCsrf(requestCsrfGeneration, allowClearedTokenRecovery)) throw error
      const restoredToken = await restoreCsrfToken(requestCsrfGeneration)
      if (!canRecoverCsrf(requestCsrfGeneration, allowClearedTokenRecovery)) throw error
      if (csrfGeneration === requestCsrfGeneration) csrfToken = restoredToken
      response = await request(path, init, restoredToken)
      if (!response.ok) throw await responseError(response, requestCsrfGeneration)
      return response
    }
    throw error
  }
  return response
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await apiFetchResponse(path, init)
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T)
}
