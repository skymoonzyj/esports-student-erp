import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useState } from "react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { apiFetch } from "../api/client"
import { portalKeys } from "../portal/api"
import { usePortalAccessError } from "../portal/usePortalAccessError"
import {
  guardedPortalQueryOptions,
  privatePortalQueryOptions,
  usePortalContext,
} from "../portal/usePortalContext"
import { AuthProvider, useAuth } from "./AuthProvider"

const accountId = "10000000-0000-4000-8000-000000000001"
const nextAccountId = "10000000-0000-4000-8000-000000000002"

function authUser(overrides: Record<string, unknown> = {}) {
  return {
    account_id: accountId,
    person_id: "20000000-0000-4000-8000-000000000001",
    full_name: "平台管理员",
    email: "admin@example.com",
    force_password_change: false,
    permissions: ["people.list"],
    ...overrides,
  }
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function AuthProbe() {
  const auth = useAuth()
  return (
    <>
      <p>{`${auth.status}:${auth.user?.full_name ?? "none"}`}</p>
      <button type="button" onClick={() => void auth.logout()}>
        测试退出
      </button>
      <button type="button" onClick={() => void auth.refreshUser().catch(() => undefined)}>
        刷新权限
      </button>
      <button
        type="button"
        onClick={() => void auth.login("13800138000", "new-password")}
      >
        测试登录
      </button>
      <output>{auth.user?.permissions.join(",") ?? ""}</output>
    </>
  )
}

function PortalRequestProbe() {
  const auth = useAuth()
  const [error, setError] = useState<unknown>(null)
  const [loaded, setLoaded] = useState(false)
  const accessError = usePortalAccessError(error)

  async function requestPortal(): Promise<void> {
    try {
      await apiFetch("/portal/private")
      setError(null)
      setLoaded(true)
    } catch (caught) {
      setError(caught)
    }
  }

  return (
    <>
      <p>{`${auth.status}:${auth.user?.full_name ?? "none"}`}</p>
      <button type="button" onClick={() => void requestPortal()}>
        {error ? "重试" : "加载门户"}
      </button>
      <button
        type="button"
        onClick={() => void apiFetch("/write-probe", { method: "POST" })}
      >
        检查 CSRF
      </button>
      {error ? <p role="alert">{accessError.message}</p> : null}
      {accessError.permissionRevoked ? <p>门户权限已撤销</p> : null}
      {loaded ? <p>门户已加载</p> : null}
    </>
  )
}

function PortalContextProbe() {
  const context = usePortalContext()
  if (context.isFetching) return <p>正在加载门户上下文</p>
  return <p>{context.data?.learning_mode ?? "无门户上下文"}</p>
}

afterEach(() => vi.unstubAllGlobals())

it("fetches portal context only for the current authenticated account", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser())
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "context-csrf" })
    }
    if (url.endsWith("/portal/context")) {
      return jsonResponse({
        coach_available: true,
        learning_available: true,
        payments_available: false,
        housing_management_available: false,
        housing_available: false,
        learning_mode: "self",
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <PortalContextProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("self")).toBeVisible()
  expect(fetchMock.mock.calls.filter(([input]) =>
    String(input).endsWith("/portal/context"),
  )).toHaveLength(1)
  expect(privatePortalQueryOptions).toEqual({
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: true,
  })
  expect(guardedPortalQueryOptions).toEqual({
    staleTime: 0,
    gcTime: 0,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
  })
})

it("clears the previous account cache before accepting a successful login", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "请登录",
          code: "invalid_session",
          request_id: "request-login-restore",
        },
        401,
      )
    }
    if (url.endsWith("/auth/login")) {
      return jsonResponse({
        user: authUser({
          account_id: nextAccountId,
          full_name: "新账号",
          email: "new@example.com",
        }),
        csrf_token: "new-account-csrf",
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  queryClient.setQueryData(portalKeys.payments(accountId, 1), {
    items: [{ related_student_name: "旧账号学员", amount_cents: 120_000 }],
  })
  const user = userEvent.setup()

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("anonymous:none")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "测试登录" }))

  expect(await screen.findByText("authenticated:新账号")).toBeVisible()
  expect(queryClient.getQueryData(portalKeys.payments(accountId, 1))).toBeUndefined()
})

it("restores a cookie session and rotates csrf on page load", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    if (String(input).endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000001",
          person_id: "20000000-0000-4000-8000-000000000001",
          full_name: "平台管理员",
          email: "admin@example.com",
          force_password_change: false,
          permissions: ["people.list"],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (String(input).endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "rotated-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${String(input)}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  expect(fetchMock).toHaveBeenCalledTimes(2)
})

it("refreshes current permissions and clears cached private data", async () => {
  let meCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000001",
          person_id: "20000000-0000-4000-8000-000000000001",
          full_name: "平台管理员",
          email: "admin@example.com",
          force_password_change: false,
          permissions: meCalls === 1 ? ["audit.read"] : [],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "refresh-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  queryClient.setQueryData(["audit-events"], { private: true })
  const user = userEvent.setup()

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("audit.read")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "刷新权限" }))

  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  expect(screen.queryByText("audit.read")).not.toBeInTheDocument()
  expect(queryClient.getQueryData(["audit-events"])).toBeUndefined()
})

it("clears private cache before a failed permission refresh while preserving the authenticated session", async () => {
  let meCalls = 0
  let rejectRefresh!: (error: unknown) => void
  const pendingRefresh = new Promise<Response>((_, reject) => {
    rejectRefresh = reject
  })
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      if (meCalls === 1) return jsonResponse(authUser())
      return pendingRefresh
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "refresh-network-csrf" })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  queryClient.setQueryData(["person-detail"], { full_name: "仍在 ERP 中" })
  const user = userEvent.setup()

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "刷新权限" }))

  await waitFor(() => expect(meCalls).toBe(2))
  expect(queryClient.getQueryData(["person-detail"])).toBeUndefined()
  expect(screen.getByText("loading:平台管理员")).toBeVisible()
  rejectRefresh(new TypeError("Failed to fetch"))
  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  expect(queryClient.getQueryData(["person-detail"])).toBeUndefined()
})

it("clears cached data and in-memory auth state before a delayed logout response settles", async () => {
  let finishLogout: (() => void) | undefined
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    if (String(input).endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          account_id: "10000000-0000-4000-8000-000000000001",
          person_id: "20000000-0000-4000-8000-000000000001",
          full_name: "平台管理员",
          email: "admin@example.com",
          force_password_change: false,
          permissions: ["people.list"],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (String(input).endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "logout-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (String(input).endsWith("/auth/logout")) {
      return new Promise<Response>((resolve) => {
        finishLogout = () => resolve(new Response(null, { status: 204 }))
      })
    }
    throw new Error(`Unexpected request: ${String(input)}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  queryClient.setQueryData(["private-person"], { full_name: "不应保留" })
  const user = userEvent.setup()

  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "测试退出" }))

  expect(await screen.findByText("anonymous:none")).toBeVisible()
  expect(queryClient.getQueryData(["private-person"])).toBeUndefined()
  const logoutCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/auth/logout"),
  )
  expect(new Headers(logoutCall?.[1]?.headers).get("X-CSRF-Token")).toBe("logout-csrf")
  await act(async () => {
    finishLogout?.()
    await Promise.resolve()
  })
})

it("clears auth, csrf and private cache when the session is truly invalid", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser())
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "session-csrf" })
    }
    if (url.endsWith("/portal/private")) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "登录已失效",
          code: "invalid_session",
          request_id: "request-session-expired",
        },
        401,
      )
    }
    if (url.endsWith("/write-probe")) return new Response(null, { status: 204 })
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  queryClient.setQueryData(portalKeys.students(accountId), {
    items: [{ student_name: "不应保留的姓名" }],
  })
  queryClient.setQueryData(portalKeys.payments(accountId, 1), {
    items: [{ amount_cents: 88_000 }],
  })
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <PortalRequestProbe />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载门户" }))

  expect(await screen.findByText("anonymous:none")).toBeVisible()
  expect(queryClient.getQueryData(portalKeys.students(accountId))).toBeUndefined()
  expect(queryClient.getQueryData(portalKeys.payments(accountId, 1))).toBeUndefined()

  await user.click(screen.getByRole("button", { name: "检查 CSRF" }))
  const writeCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/write-probe"),
  )
  expect(new Headers(writeCall?.[1]?.headers).get("X-CSRF-Token")).toBeNull()
})

it("revokes only portal data on a permission-denied response", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser())
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "permission-csrf" })
    }
    if (url.endsWith("/portal/private")) {
      return jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "门户权限已变更",
          code: "permission_denied",
          request_id: "request-permission-denied",
        },
        403,
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  queryClient.setQueryData(portalKeys.students(accountId), {
    items: [{ student_name: "旧门户姓名" }],
  })
  queryClient.setQueryData(["other-private"], { full_name: "仍保留" })
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={["/learning"]}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <PortalRequestProbe />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "加载门户" }))

  expect(await screen.findByText("门户权限已撤销")).toBeVisible()
  expect(screen.getByText("authenticated:平台管理员")).toBeVisible()
  expect(queryClient.getQueryData(portalKeys.students(accountId))).toBeUndefined()
  expect(queryClient.getQueryData(["other-private"])).toEqual({ full_name: "仍保留" })
})

it.each([
  ["server", "服务暂时不可用", "服务端暂时不可用"],
  ["network", "Failed to fetch", "服务暂时不可用，请重试。"],
])(
  "keeps the session and exposes retry after a %s failure",
  async (failureKind, failureDetail, expectedMessage) => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith("/auth/me")) return jsonResponse(authUser())
      if (url.endsWith("/auth/csrf")) {
        return jsonResponse({ csrf_token: "retry-csrf" })
      }
      if (url.endsWith("/portal/private")) {
        if (failureKind === "network") throw new TypeError(failureDetail)
        return jsonResponse(
          {
            type: "about:blank",
            title: "Server Error",
            status: 500,
            detail: expectedMessage,
            code: "internal_error",
            request_id: "request-server-error",
          },
          500,
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal("fetch", fetchMock)
    const queryClient = new QueryClient()
    queryClient.setQueryData(portalKeys.students(accountId), {
      items: [{ student_name: "会话仍可访问" }],
    })
    const user = userEvent.setup()

    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <AuthProvider>
            <PortalRequestProbe />
          </AuthProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText("authenticated:平台管理员")).toBeVisible()
    await user.click(screen.getByRole("button", { name: "加载门户" }))

    expect(await screen.findByRole("alert")).toHaveTextContent(expectedMessage)
    expect(screen.getByRole("button", { name: "重试" })).toBeVisible()
    expect(screen.getByText("authenticated:平台管理员")).toBeVisible()
    expect(queryClient.getQueryData(portalKeys.students(accountId))).toEqual({
      items: [{ student_name: "会话仍可访问" }],
    })
  },
)
