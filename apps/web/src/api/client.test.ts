import { afterEach, describe, expect, it, vi } from "vitest"

import {
  ProblemDetailsError,
  apiFetch,
  apiFetchResponse,
  setCsrfToken,
  setInvalidSessionHandler,
} from "./client"

afterEach(() => {
  setCsrfToken(null)
  setInvalidSessionHandler(null)
  vi.unstubAllGlobals()
})

describe("apiFetchResponse", () => {
  it("preserves Retry-After seconds on an actual 429 response", async () => {
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({
        type: "about:blank",
        title: "Too Many Requests",
        status: 429,
        detail: "请求过于频繁",
        code: "rate_limited",
        request_id: "request-retry-after",
      }), {
        status: 429,
        headers: { "Content-Type": "application/json", "Retry-After": "12" },
      }),
    ))

    const caught = await apiFetchResponse("/rate-limited").catch(
      (error: unknown) => error,
    )

    expect(caught).toBeInstanceOf(ProblemDetailsError)
    expect((caught as ProblemDetailsError).retryAfterSeconds).toBe(12)
  })

  it("sends a prefixed POST with merged headers, in-memory csrf, and cookies", async () => {
    const response = new Response(null, { status: 204 })
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(response)
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("csrf-memory-only")

    await apiFetchResponse("/auth/logout", {
      method: "post",
      headers: {
        Accept: "application/json",
        "X-CSRF-Token": "caller-value",
      },
      credentials: "omit",
    })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0] ?? []
    const headers = new Headers(init?.headers)
    expect(url).toBe("/api/v1/auth/logout")
    expect(headers.get("Accept")).toBe("application/json")
    expect(headers.get("X-CSRF-Token")).toBe("csrf-memory-only")
    expect(init?.credentials).toBe("include")
    expect(localStorage.length).toBe(0)
  })

  it("refreshes csrf once and retries concurrent writes rejected by an iframe rotation", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "csrf-refreshed" }), {
          headers: { "Content-Type": "application/json" },
        })
      }
      const token = new Headers(init?.headers).get("X-CSRF-Token")
      if (token === "csrf-stale") {
        return new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Forbidden",
            status: 403,
            detail: "安全校验失败，请刷新后重试",
            code: "csrf_invalid",
            request_id: "request-stale-csrf",
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        )
      }
      expect(token).toBe("csrf-refreshed")
      return new Response(null, { status: 204 })
    })
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("csrf-stale")

    await Promise.all([
      apiFetchResponse("/write-one", { method: "POST" }),
      apiFetchResponse("/write-two", { method: "DELETE" }),
    ])

    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/csrf")),
    ).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(5)
  })

  it("uses recovered csrf only for an in-flight logout after local token cleanup", async () => {
    let releaseInitialLogout!: (response: Response) => void
    let markInitialLogoutStarted!: () => void
    const initialLogoutStarted = new Promise<void>((resolve) => {
      markInitialLogoutStarted = resolve
    })
    const initialLogoutResponse = new Promise<Response>((resolve) => {
      releaseInitialLogout = resolve
    })
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const token = new Headers(init?.headers).get("X-CSRF-Token")
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "logout-recovered-csrf" }), {
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/auth/logout") && token === "logout-stale-csrf") {
        markInitialLogoutStarted()
        return initialLogoutResponse
      }
      if (url.endsWith("/auth/logout")) {
        expect(token).toBe("logout-recovered-csrf")
        return new Response(null, { status: 204 })
      }
      if (url.endsWith("/after-logout")) {
        expect(token).toBeNull()
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("logout-stale-csrf")

    const logoutRequest = apiFetchResponse("/auth/logout", { method: "POST" })
    await initialLogoutStarted
    setCsrfToken(null)
    releaseInitialLogout(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "安全校验失败，请刷新后重试",
          code: "csrf_invalid",
          request_id: "request-logout-stale-csrf",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    )

    await logoutRequest
    await apiFetchResponse("/after-logout", { method: "POST" })

    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/csrf")),
    ).toHaveLength(1)
    expect(fetchMock).toHaveBeenCalledTimes(4)
  })

  it("does not recover an in-flight non-logout write after local token cleanup", async () => {
    let releaseOldWrite!: (response: Response) => void
    let markOldWriteStarted!: () => void
    const oldWriteStarted = new Promise<void>((resolve) => {
      markOldWriteStarted = resolve
    })
    const oldWriteResponse = new Promise<Response>((resolve) => {
      releaseOldWrite = resolve
    })
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const token = new Headers(init?.headers).get("X-CSRF-Token")
      if (url.endsWith("/old-session-write") && token === "old-session-csrf") {
        markOldWriteStarted()
        return oldWriteResponse
      }
      if (url.endsWith("/auth/csrf")) {
        throw new Error("logout must not recover this write")
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("old-session-csrf")

    const oldWrite = apiFetchResponse("/old-session-write", { method: "POST" })
    await oldWriteStarted
    setCsrfToken(null)
    releaseOldWrite(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "安全校验失败，请刷新后重试",
          code: "csrf_invalid",
          request_id: "request-old-write-after-logout",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    )

    await expect(oldWrite).rejects.toMatchObject({
      problem: { code: "csrf_invalid" },
    })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("does not replay an old write after a new session replaces its csrf token", async () => {
    let releaseOldWrite!: (response: Response) => void
    let markOldWriteStarted!: () => void
    const oldWriteStarted = new Promise<void>((resolve) => {
      markOldWriteStarted = resolve
    })
    const oldWriteResponse = new Promise<Response>((resolve) => {
      releaseOldWrite = resolve
    })
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input)
      const token = new Headers(init?.headers).get("X-CSRF-Token")
      if (url.endsWith("/old-session-write") && token === "old-session-csrf") {
        markOldWriteStarted()
        return oldWriteResponse
      }
      if (url.endsWith("/auth/csrf")) {
        return new Response(JSON.stringify({ csrf_token: "old-session-recovered" }), {
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith("/new-session-write")) {
        expect(token).toBe("new-session-csrf")
        return new Response(null, { status: 204 })
      }
      if (url.endsWith("/old-session-write")) {
        return new Response(null, { status: 204 })
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("old-session-csrf")

    const oldWrite = apiFetchResponse("/old-session-write", { method: "POST" })
    await oldWriteStarted
    setCsrfToken("new-session-csrf")
    releaseOldWrite(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "安全校验失败，请刷新后重试",
          code: "csrf_invalid",
          request_id: "request-old-session-stale-csrf",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    )

    await expect(oldWrite).rejects.toMatchObject({
      problem: { code: "csrf_invalid" },
    })
    await apiFetchResponse("/new-session-write", { method: "POST" })

    expect(
      fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/csrf")),
    ).toHaveLength(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })

  it("does not retry a non-csrf forbidden write", async () => {
    const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "没有权限",
          code: "permission_denied",
          request_id: "request-permission-denied",
        }),
        { status: 403, headers: { "Content-Type": "application/json" } },
      ),
    )
    vi.stubGlobal("fetch", fetchMock)
    setCsrfToken("csrf-current")

    await expect(
      apiFetchResponse("/write-denied", { method: "POST" }),
    ).rejects.toMatchObject({ problem: { code: "permission_denied" } })
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("throws an allowlisted problem using the HTTP response status", async () => {
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
      await apiFetchResponse("/people/private")
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

  it("publishes an exact 401 invalid_session response", async () => {
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

    await expect(apiFetchResponse("/portal/context")).rejects.toBeInstanceOf(
      ProblemDetailsError,
    )
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it("does not invalidate a newer login when an older request returns 401", async () => {
    const handler = vi.fn()
    let finish: ((response: Response) => void) | undefined
    setInvalidSessionHandler(handler)
    setCsrfToken("previous-account-csrf")
    vi.stubGlobal("fetch", vi.fn<typeof fetch>(() => new Promise((resolve) => {
      finish = resolve
    })))
    const pending = apiFetchResponse("/people").catch((error: unknown) => error)

    setCsrfToken("current-account-csrf")
    finish!(new Response(JSON.stringify({ code: "invalid_session" }), { status: 401 }))

    expect(await pending).toBeInstanceOf(ProblemDetailsError)
    expect(handler).not.toHaveBeenCalled()
  })

  it.each(["restore", "retry"])(
    "ignores an old session 401 during csrf %s after another account logs in",
    async (stage) => {
      const handler = vi.fn()
      let finish: ((response: Response) => void) | undefined
      let writes = 0
      const deferred = () => new Promise<Response>((resolve) => { finish = resolve })
      setInvalidSessionHandler(handler)
      setCsrfToken("previous-account-csrf")
      vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
        const url = String(input)
        if (url.endsWith("/auth/csrf")) {
          return stage === "restore" ? deferred() : new Response(
            JSON.stringify({ csrf_token: "recovered-previous-csrf" }), { status: 200 },
          )
        }
        if (url.endsWith("/old-write")) {
          writes += 1
          return writes === 1 ? new Response(
            JSON.stringify({ code: "csrf_invalid" }), { status: 403 },
          ) : deferred()
        }
        if (url.endsWith("/current-write")) {
          expect(new Headers(init?.headers).get("X-CSRF-Token")).toBe("current-account-csrf")
          return new Response(null, { status: 204 })
        }
        throw new Error("Unexpected request")
      }))
      const pending = apiFetchResponse("/old-write", { method: "POST" }).catch(
        (error: unknown) => error,
      )
      await vi.waitFor(() => expect(finish).toBeDefined())
      setCsrfToken("current-account-csrf")
      finish!(new Response(JSON.stringify({ code: "invalid_session" }), { status: 401 }))

      expect(await pending).toBeInstanceOf(ProblemDetailsError)
      expect(handler).not.toHaveBeenCalled()
      await expect(apiFetchResponse("/current-write", { method: "POST" })).resolves.toBeDefined()
    },
  )

  it.each([
    [401, "invalid_credentials"],
    [403, "invalid_session"],
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

    await expect(apiFetchResponse("/auth/login")).rejects.toBeInstanceOf(
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

    await expect(apiFetchResponse("/portal/context")).rejects.toThrow(
      "Failed to fetch",
    )
    expect(handler).not.toHaveBeenCalled()
  })

  it("returns the identical successful response without reading its body", async () => {
    const response = new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
    })
    const jsonSpy = vi.spyOn(response, "json")
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response))

    const result = await apiFetchResponse("/portal/context")

    expect(result).toBe(response)
    expect(result.bodyUsed).toBe(false)
    expect(jsonSpy).not.toHaveBeenCalled()
  })
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

  it("still parses every non-204 successful response as JSON", async () => {
    const response = new Response(JSON.stringify({ ok: true }), {
      headers: { "Content-Type": "application/json" },
    })
    const jsonSpy = vi.spyOn(response, "json")
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response))

    await expect(apiFetch<{ ok: boolean }>("/portal/context")).resolves.toEqual({
      ok: true,
    })
    expect(jsonSpy).toHaveBeenCalledTimes(1)
  })

  it("still returns undefined for a successful 204 without parsing JSON", async () => {
    const response = new Response(null, { status: 204 })
    const jsonSpy = vi.spyOn(response, "json")
    vi.stubGlobal("fetch", vi.fn<typeof fetch>().mockResolvedValue(response))

    await expect(apiFetch<void>("/auth/logout")).resolves.toBeUndefined()
    expect(jsonSpy).not.toHaveBeenCalled()
  })
})
