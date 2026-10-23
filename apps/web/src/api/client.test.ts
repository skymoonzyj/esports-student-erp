import { afterEach, describe, expect, it, vi } from "vitest"

import {
  ProblemDetailsError,
  apiFetch,
  setCsrfToken,
  setInvalidSessionHandler,
} from "./client"

afterEach(() => {
  setCsrfToken(null)
  setInvalidSessionHandler(null)
  vi.unstubAllGlobals()
})

describe("apiFetch", () => {
  it("keeps csrf in memory and sends it only on writes", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("csrf-memory-only")

    await apiFetch<void>("/auth/logout", { method: "POST" })

    const [, init] = fetchMock.mock.calls[0] ?? []
    const headers = new Headers(init?.headers)
    expect(headers.get("X-CSRF-Token")).toBe("csrf-memory-only")
    expect(init?.credentials).toBe("include")
    expect(localStorage.length).toBe(0)
  })

  it("turns problem details into a typed error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "没有权限",
            code: "permission_denied",
            request_id: "request-1",
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    const request = apiFetch("/people")
    await expect(request).rejects.toBeInstanceOf(ProblemDetailsError)
    await expect(request).rejects.toMatchObject({
      problem: { code: "permission_denied" },
    })
  })

  it("uses the HTTP status and allowlists parsed problem details", async () => {
    const leakedIdentity = "11010520120102106X"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Not Found",
            status: 500,
            detail: "资源不存在",
            code: "resource_not_found",
            request_id: "request-status-mismatch",
            identity_number: leakedIdentity,
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    let caught: unknown
    try {
      await apiFetch("/people/private")
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(ProblemDetailsError)
    expect((caught as ProblemDetailsError).problem).toEqual({
      type: "about:blank",
      title: "Not Found",
      status: 404,
      detail: "资源不存在",
      code: "resource_not_found",
      request_id: "request-status-mismatch",
    })
    expect(JSON.stringify(caught)).not.toContain(leakedIdentity)
    expect(JSON.stringify(caught)).not.toContain("identity_number")
  })

  it("creates a generic typed error for malformed error JSON", async () => {
    const leakedBody = "private-invalid-json-13800138000"
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(leakedBody, {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    )

    let caught: unknown
    try {
      await apiFetch("/people/private")
    } catch (error) {
      caught = error
    }

    expect(caught).toBeInstanceOf(ProblemDetailsError)
    expect((caught as ProblemDetailsError).problem).toEqual({
      type: "about:blank",
      title: "Request failed",
      status: 404,
      detail: "服务暂时不可用，请重试。",
      code: "http_error",
      request_id: "",
    })
    expect((caught as Error).cause).toBeUndefined()
    expect(String(caught)).not.toContain(leakedBody)
    expect(JSON.stringify(caught)).not.toContain(leakedBody)
  })

  it("publishes only a real invalid session response", async () => {
    const handler = vi.fn()
    setInvalidSessionHandler(handler)
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Unauthorized",
            status: 401,
            detail: "登录已失效",
            code: "invalid_session",
            request_id: "request-invalid-session",
          }),
          { status: 401, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    await expect(apiFetch("/portal/context")).rejects.toBeInstanceOf(
      ProblemDetailsError,
    )
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it.each([
    [401, "invalid_credentials"],
    [401, "unauthorized"],
    [403, "permission_denied"],
    [500, "internal_error"],
  ])("does not publish an HTTP %i %s response", async (status, code) => {
    const handler = vi.fn()
    setInvalidSessionHandler(handler)
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockResolvedValue(
        new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Request failed",
            status,
            detail: "请求失败",
            code,
            request_id: `request-${code}`,
          }),
          { status, headers: { "Content-Type": "application/json" } },
        ),
      ),
    )

    await expect(apiFetch("/auth/login")).rejects.toBeInstanceOf(
      ProblemDetailsError,
    )
    expect(handler).not.toHaveBeenCalled()
  })

  it("does not publish a network failure", async () => {
    const handler = vi.fn()
    setInvalidSessionHandler(handler)
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>().mockRejectedValue(new TypeError("Failed to fetch")),
    )

    await expect(apiFetch("/portal/context")).rejects.toThrow("Failed to fetch")
    expect(handler).not.toHaveBeenCalled()
  })
})
