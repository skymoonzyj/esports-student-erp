import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { setCsrfToken } from "../api/client"
import { quickImportApi } from "./quickImportApi"

beforeEach(() => setCsrfToken("test-csrf"))
afterEach(() => { vi.unstubAllGlobals(); setCsrfToken(null) })

it.each(["preview", "commit"] as const)("sends %s to the registered API route with one version prefix", async method => {
  const signal = new AbortController().signal
  const operation = "10000000-0000-4000-8000-000000000001"
  const payload = method === "preview"
    ? { text: "synthetic table", default_arrival_date: "2026-11-07", team_choices: {} }
    : { ticket: "encrypted-test-ticket", idempotency_key: operation }
  const response = method === "preview"
    ? { import_date: "2026-11-07", rows: [] }
    : { person_id: operation, stay_id: "10000000-0000-4000-8000-000000000002", stay_status: "checked_in", role_pending: true }
  const path = method === "preview"
    ? "/api/v1/personnel-intake/quick-import/preview"
    : "/api/v1/personnel-intake/quick-import/commit"
  const requests: Array<{ url: string; init: RequestInit | undefined }> = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url, init) => {
    requests.push({ url: String(url), init })
    if (url !== path) return Response.json({ status: 404, detail: "Route not found", code: "route_not_found" }, { status: 404 })
    return Response.json(response, { status: method === "preview" ? 200 : 201 })
  }))
  const result = method === "preview"
    ? quickImportApi.preview("synthetic table", "2026-11-07", {}, signal)
    : quickImportApi.commit("encrypted-test-ticket", operation, signal)
  await expect(result).resolves.toEqual(response)
  expect(requests).toHaveLength(1)
  expect(requests[0].url).toBe(path)
  expect(requests[0].init).toMatchObject({ method: "POST", credentials: "include", cache: "no-store", signal })
  expect(new Headers(requests[0].init?.headers).get("X-CSRF-Token")).toBe("test-csrf")
  expect(JSON.parse(String(requests[0].init?.body))).toEqual(payload)
})
