import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  createMemoryRouter,
  MemoryRouter,
  RouterProvider,
} from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "./AuthProvider"
import { LoginPage } from "./LoginPage"
import { appRoutes } from "../app/router"

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("submits an account identifier and password and handles forced password change", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    if (String(input).endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "登录已失效",
          code: "invalid_session",
          request_id: "request-login-bootstrap",
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      )
    }
    if (String(input).endsWith("/auth/login")) {
      return new Response(
        JSON.stringify({
          user: {
            account_id: "10000000-0000-4000-8000-000000000001",
            person_id: "20000000-0000-4000-8000-000000000001",
            full_name: "新员工",
            email: "staff@example.com",
            force_password_change: true,
            permissions: [],
          },
          csrf_token: "csrf-after-login",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${String(input)}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <AuthProvider>
          <LoginPage />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.type(await screen.findByLabelText("账号"), "13800138000")
  const password = screen.getByLabelText("密码")
  await user.type(password, "A-strong-password-2026")
  expect(password).toHaveAttribute("type", "password")
  await user.click(screen.getByRole("button", { name: "显示密码" }))
  expect(password).toHaveAttribute("type", "text")
  await user.click(screen.getByRole("button", { name: "登录" }))

  expect(await screen.findByText("请先修改临时密码")).toBeVisible()
  const loginCall = fetchMock.mock.calls.find(([input]) =>
    String(input).endsWith("/auth/login"),
  )
  expect(loginCall).toBeDefined()
  expect(JSON.parse(String(loginCall?.[1]?.body))).toEqual({
    identifier: "13800138000",
    password: "A-strong-password-2026",
  })
})

type TestEntry = string | { pathname: string; state?: unknown }

function continuationFetch(forcePasswordChange: boolean) {
  return vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Unauthorized",
          status: 401,
          detail: "登录已失效",
          code: "invalid_session",
          request_id: "continuation-bootstrap",
        }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/login")) {
      return new Response(
        JSON.stringify({
          user: {
            account_id: "account-login-continuation",
            person_id: "person-login-continuation",
            full_name: "评价登录账号",
            email: "evaluation-login@example.test",
            force_password_change: forcePasswordChange,
            permissions: ["evaluation_center.access"],
          },
          csrf_token: "continuation-login-csrf",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/auth/change-password")) {
      return new Response(
        JSON.stringify({
          user: {
            account_id: "account-login-continuation",
            person_id: "person-login-continuation",
            full_name: "评价登录账号",
            email: "evaluation-login@example.test",
            force_password_change: false,
            permissions: ["evaluation_center.access"],
          },
          csrf_token: "continuation-password-csrf",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/portal/context")) {
      return new Response(
        JSON.stringify({
          coach_available: false,
          learning_available: false,
          payments_available: false,
          housing_management_available: false,
          housing_available: false,
          learning_mode: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.endsWith("/evaluations/bridge/tickets")) {
      return new Promise<Response>(() => undefined)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
}

function renderRouter(input: {
  initialEntries: TestEntry[]
  forcePasswordChange?: boolean
}) {
  vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(
    () => undefined,
  )
  vi.stubGlobal(
    "fetch",
    continuationFetch(input.forcePasswordChange ?? false),
  )
  const router = createMemoryRouter(appRoutes, {
    initialEntries: input.initialEntries,
  })
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return router
}

async function submitValidLogin(): Promise<void> {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText("账号"), "13800138000")
  await user.type(screen.getByLabelText("密码"), "A-strong-password-2026")
  await user.click(screen.getByRole("button", { name: "登录" }))
}

async function submitValidPasswordChange(): Promise<void> {
  const user = userEvent.setup()
  await user.type(
    await screen.findByLabelText("当前临时密码"),
    "A-temporary-password-2026",
  )
  await user.type(screen.getByLabelText("新密码"), "A-new-password-2026")
  await user.type(screen.getByLabelText("再次输入新密码"), "A-new-password-2026")
  await user.click(screen.getByRole("button", { name: "保存新密码" }))
}

it("returns to an evaluation connection after successful login", async () => {
  const requestId = "90000000-0000-4000-8000-000000000001"
  const router = renderRouter({
    initialEntries: [
      {
        pathname: "/login",
        state: { returnTo: `/evaluation-connect/${requestId}` },
      },
    ],
  })

  await submitValidLogin()

  await waitFor(() =>
    expect(router.state.location.pathname).toBe(
      `/evaluation-connect/${requestId}`,
    ),
  )
})

it.each([
  "https://attacker.example/",
  "//attacker.example/",
  "/people",
  "/evaluation-connect/not-a-uuid",
])("rejects unsafe login continuation %s", async (returnTo) => {
  const router = renderRouter({
    initialEntries: [{ pathname: "/login", state: { returnTo } }],
  })

  await submitValidLogin()

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
})

it("returns to an evaluation connection after forced password change", async () => {
  const requestId = "90000000-0000-4000-8000-000000000001"
  const router = renderRouter({
    initialEntries: [
      {
        pathname: "/login",
        state: { returnTo: `/evaluation-connect/${requestId}` },
      },
    ],
    forcePasswordChange: true,
  })

  await submitValidLogin()
  expect(
    await screen.findByText("新密码需为 8–16 位，并同时包含大写、小写字母和数字。"),
  ).toBeVisible()
  const newPassword = await screen.findByLabelText("新密码")
  expect(newPassword).toHaveAttribute("minLength", "8")
  expect(newPassword).toHaveAttribute("maxLength", "16")
  await submitValidPasswordChange()

  await waitFor(() =>
    expect(router.state.location.pathname).toBe(
      `/evaluation-connect/${requestId}`,
    ),
  )
})

it.each([
  "https://attacker.example/",
  "//attacker.example/",
  "/people",
  "/evaluation-connect/not-a-uuid",
])("rejects unsafe password continuation %s", async (returnTo) => {
  const router = renderRouter({
    initialEntries: [{ pathname: "/login", state: { returnTo } }],
    forcePasswordChange: true,
  })

  await submitValidLogin()
  await submitValidPasswordChange()

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
})
