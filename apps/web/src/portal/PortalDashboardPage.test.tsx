import {
  QueryClient,
  QueryClientProvider,
  focusManager,
} from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import type { PortalContext } from "./api"
import { portalKeys } from "./api"
import { PortalDashboardPage } from "./PortalDashboardPage"

const accountId = "10000000-0000-4000-8000-000000000090"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function authUser(permissions: string[] = [], scopedPermissions: string[] = []) {
  return {
    account_id: accountId,
    person_id: "20000000-0000-4000-8000-000000000090",
    full_name: "门户测试用户",
    email: "portal-dashboard@example.test",
    force_password_change: false,
    permissions,
    scoped_permissions: scopedPermissions,
  }
}

function coachResponse(overrides: Record<string, unknown> = {}) {
  return {
    observed_at: "2026-07-30T08:00:00+08:00",
    selected_starts_at: "2030-08-01T09:00:00+08:00",
    options: [
      {
        anchor_cohort_id: "cohort-1",
        starts_at: "2030-08-01T09:00:00+08:00",
        cohort_names: ["英雄联盟暑期营"],
        class_count: 2,
      },
    ],
    classes: [],
    items: [],
    page: 1,
    page_size: 1,
    total: 18,
    ...overrides,
  }
}

function renderDashboard(input: {
  context: PortalContext
  permissions?: string[]
  scopedPermissions?: string[]
  coach?: ReturnType<typeof coachResponse>
  students?: { items: unknown[]; total: number }
  portalError?: boolean
  queryClient?: QueryClient
}) {
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser(input.permissions, input.scopedPermissions))
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "portal-dashboard-csrf" })
    }
    if (url.endsWith("/portal/context")) return jsonResponse(input.context)
    if (url.includes("/coach/next-cohorts?")) {
      if (input.portalError) {
        return jsonResponse(
          {
            type: "about:blank",
            title: "Server Error",
            status: 500,
            detail: "门户暂时不可用",
            code: "internal_error",
            request_id: "dashboard-error",
          },
          500,
        )
      }
      return jsonResponse(input.coach ?? coachResponse())
    }
    if (url.endsWith("/family/students")) {
      return jsonResponse(input.students ?? { items: [], total: 2 })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient =
    input.queryClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <PortalDashboardPage />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  return { fetchMock, queryClient }
}

afterEach(() => {
  focusManager.setFocused(undefined)
  vi.unstubAllGlobals()
})

it("shows coach, learning and payment tasks for a multi-identity account", async () => {
  renderDashboard({
    context: {
      coach_available: true,
      learning_available: true,
      payments_available: true,
      housing_management_available: false,
      housing_available: true,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: "family",
    },
  })

  expect(await screen.findByRole("link", { name: "查看下一期名单" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看家庭信息" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看本人付款" })).toBeVisible()
  expect(screen.getByRole("link", { name: "查看住宿信息" })).toBeVisible()
  expect(screen.getByText("英雄联盟暑期营")).toBeVisible()
  expect(screen.getByText("18 名待查看")).toBeVisible()
})

it("keeps the existing back-office overview for a pure admin", async () => {
  renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    permissions: ["people.list", "accounts.manage"],
  })

  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(screen.queryByText("教练工作")).not.toBeInTheDocument()
})

it("does not read payment amounts just to render a payment task", async () => {
  const { fetchMock } = renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: true,
      housing_management_available: false,
      housing_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
  })

  expect(await screen.findByRole("link", { name: "查看本人付款" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) => String(request).includes("/me/payments")),
  ).toBe(false)
})

it("renders a personal housing task without reading the housing projection", async () => {
  const { fetchMock } = renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: true,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
  })

  const link = await screen.findByRole("link", { name: "查看住宿信息" })
  expect(link).toHaveAttribute("href", "/me/housing")
  expect(screen.getByText("当前住宿与未来预约")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/me/housing"),
    ),
  ).toBe(false)
  expect(screen.queryByText("人员与实名档案")).not.toBeInTheDocument()
})

it.each([
  ["global", ["housing.read"], []],
  ["scoped", [], ["housing.read"]],
] as const)("keeps the back-office dashboard for a %s housing manager without promoting personal housing", async (_label, permissions, scopedPermissions) => {
  renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: true,
      housing_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    permissions: [...permissions],
    scopedPermissions: [...scopedPermissions],
  })

  expect(await screen.findByText("人员与实名档案")).toBeVisible()
  expect(screen.queryByRole("link", { name: "查看住宿信息" })).not.toBeInTheDocument()
})

it("shows the evaluation card without reading the evaluation context", async () => {
  const { fetchMock } = renderDashboard({
    context: {
      coach_available: false,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    permissions: ["evaluation_center.access"],
  })

  expect(
    await screen.findByRole("link", { name: "进入评价中心" }),
  ).toBeVisible()
  expect(screen.queryByText("当前暂无可用工作项")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).endsWith("/evaluations/context"),
    ),
  ).toBe(false)
})

it("hides cached portal names and counts when reauthorization fails", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(portalKeys.coach(accountId, undefined, 1),
    coachResponse({
      options: [
        {
          anchor_cohort_id: "old-cohort",
          starts_at: "2030-08-01T09:00:00+08:00",
          cohort_names: ["旧缓存名单"],
          class_count: 1,
        },
      ],
      total: 9,
    }),
  )
  const user = userEvent.setup()
  renderDashboard({
    context: {
      coach_available: true,
      learning_available: false,
      payments_available: false,
      housing_management_available: false,
      housing_available: false,
      adult_guardian_invitations_available: false,
      food_profile_available: false,
      food_preferences_onboarding_required: false,
      learning_mode: null,
    },
    portalError: true,
    queryClient,
  })

  expect(await screen.findByRole("alert")).toHaveTextContent("门户暂时不可用")
  expect(screen.queryByText("旧缓存名单")).not.toBeInTheDocument()
  expect(screen.queryByText("9 名待查看")).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "重试" }))
  expect(screen.getByRole("alert")).toBeVisible()
})

it("reauthorizes before refreshing a dashboard projection once", async () => {
  let contextCalls = 0
  let coachCalls = 0
  let resolveContext!: (response: Response) => void
  let resolveCoach!: (response: Response) => void
  const pendingContext = new Promise<Response>((resolve) => {
    resolveContext = resolve
  })
  const pendingCoach = new Promise<Response>((resolve) => {
    resolveCoach = resolve
  })
  const context: PortalContext = {
    coach_available: true,
    learning_available: false,
    payments_available: false,
    housing_management_available: false,
    housing_available: false,
    adult_guardian_invitations_available: false,
    food_profile_available: false,
    food_preferences_onboarding_required: false,
    learning_mode: null,
  }
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) return jsonResponse(authUser())
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "dashboard-focus-csrf" })
    }
    if (url.endsWith("/portal/context")) {
      contextCalls += 1
      return contextCalls === 1 ? jsonResponse(context) : pendingContext
    }
    if (url.includes("/coach/next-cohorts?")) {
      coachCalls += 1
      return coachCalls === 1
        ? jsonResponse(coachResponse())
        : pendingCoach
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <AuthProvider>
          <PortalDashboardPage />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("英雄联盟暑期营")).toBeVisible()
  expect(coachCalls).toBe(1)

  await act(async () => {
    focusManager.setFocused(false)
    focusManager.setFocused(true)
  })
  await waitFor(() => expect(contextCalls).toBe(2))
  expect(screen.queryByText("英雄联盟暑期营")).not.toBeInTheDocument()
  expect(coachCalls).toBe(1)

  await act(async () => {
    resolveContext(jsonResponse(context))
  })
  await waitFor(() => expect(coachCalls).toBe(2))
  expect(screen.queryByText("英雄联盟暑期营")).not.toBeInTheDocument()

  await act(async () => {
    resolveCoach(
      jsonResponse(
        coachResponse({
          options: [
            {
              anchor_cohort_id: "cohort-refocused",
              starts_at: "2030-08-15T09:00:00+08:00",
              cohort_names: ["重新授权后的营期"],
              class_count: 1,
            },
          ],
        }),
      ),
    )
  })
  expect(await screen.findByText("重新授权后的营期")).toBeVisible()
  expect(coachCalls).toBe(2)
})
