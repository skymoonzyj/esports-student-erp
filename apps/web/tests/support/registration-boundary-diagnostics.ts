import type { Page, Request, Response } from "@playwright/test"

type Stage = "before_navigation" | "navigation_complete" | "context_response" | "heading_ready"
type Route = "root" | "login" | "password" | "register_status" | "evaluations" | "other"
type Endpoint = "auth_me" | "auth_csrf" | "evaluation_context"
type ErrorClass = "timeout" | "network" | "aborted" | "other" | "unknown"
type Counts = {
  request: number
  response: number
  finished: number
  failed: number
  last_status: number | null
  last_elapsed_ms: number | null
}

const endpointPaths: Record<string, Endpoint> = {
  "/api/v1/auth/me": "auth_me",
  "/api/v1/auth/csrf": "auth_csrf",
  "/api/v1/evaluations/context": "evaluation_context",
}
const routePaths: Record<string, Route> = {
  "/": "root", "/login": "login", "/change-password": "password",
  "/register/status": "register_status", "/evaluations": "evaluations",
}

/** Synchronous projections only: no requests, response bodies, DOM or waits. */
export function observeRegistrationBoundary(page: Page) {
  const origin = new URL(page.url()).origin
  const started = performance.now()
  const counts = (): Counts => ({
    request: 0, response: 0, finished: 0, failed: 0, last_status: null, last_elapsed_ms: null,
  })
  const data = {
    version: 1, stage: "before_navigation" as Stage, route: "other" as Route,
    error_class: "unknown" as ErrorClass, elapsed_ms: 0,
    page_errors: 0, document_failures: 0, script_failures: 0,
    endpoints: { auth_me: counts(), auth_csrf: counts(), evaluation_context: counts() },
  }
  const elapsed = () => Math.min(86_400_000, Math.max(0, Math.floor(performance.now() - started)))
  const increment = (value: number) => Math.min(1000, value + 1)
  const route = (): Route => routePaths[new URL(page.url()).pathname] ?? "other"
  const endpoint = (request: Request): Endpoint | undefined => {
    if (request.method() !== "GET") return undefined
    const url = new URL(request.url())
    return url.origin === origin ? endpointPaths[url.pathname] : undefined
  }
  const record = (request: Request, field: "request" | "response" | "finished" | "failed") => {
    const name = endpoint(request)
    if (!name) return
    data.endpoints[name][field] = increment(data.endpoints[name][field])
    data.endpoints[name].last_elapsed_ms = elapsed()
  }
  const onRequest = (request: Request) => record(request, "request")
  const onResponse = (response: Response) => {
    const request = response.request()
    record(request, "response")
    const name = endpoint(request)
    if (name) data.endpoints[name].last_status = response.status()
  }
  const onFinished = (request: Request) => record(request, "finished")
  const onFailed = (request: Request) => {
    record(request, "failed")
    if (request.resourceType() === "document") data.document_failures = increment(data.document_failures)
    if (request.resourceType() === "script") data.script_failures = increment(data.script_failures)
  }
  const onPageError = () => { data.page_errors = increment(data.page_errors) }
  page.on("request", onRequest)
  page.on("response", onResponse)
  page.on("requestfinished", onFinished)
  page.on("requestfailed", onFailed)
  page.on("pageerror", onPageError)

  return {
    mark(stage: Stage) {
      data.stage = stage
      data.route = route()
      data.elapsed_ms = elapsed()
    },
    recordError(error: unknown) {
      data.error_class = error instanceof Error
        ? error.name === "TimeoutError" ? "timeout"
          : /net::ERR_ABORTED\b/.test(error.message) ? "aborted"
            : /net::ERR_[A-Z_]+\b/.test(error.message) ? "network" : "other"
        : "unknown"
      data.route = route()
      data.elapsed_ms = elapsed()
    },
    stop() {
      page.off("request", onRequest)
      page.off("response", onResponse)
      page.off("requestfinished", onFinished)
      page.off("requestfailed", onFailed)
      page.off("pageerror", onPageError)
      data.elapsed_ms = elapsed()
    },
    serialize: () => JSON.stringify(data),
  }
}
