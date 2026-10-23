import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider, useAuth } from "../auth/AuthProvider"
import { evaluationKeys } from "./api"
import { EvaluationCenterPage } from "./EvaluationCenterPage"
import type { EvaluationFoundationContext } from "./foundationApi"

const accountId = "10000000-0000-4000-8000-000000000094"

const evaluationContext: EvaluationFoundationContext = {
  accessible: true,
  system_evaluation_available: true,
  embedded_start_url:
    "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
  standalone_start_url:
    "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
  upload_available: false,
  video_available: false,
  reports_available: false,
  surveys_available: false,
  survey_sections: {
    respondent: false,
    staff_feedback: false,
    administration: false,
  },
  game_accounts_available: false,
  admin_available: false,
  admin_sections: {
    cohort_staff: false,
    student_matching: false,
    analysis_profiles: false,
    system_tasks: false,
    adapter_certifications: false,
  },
  integration_api_version: "v1",
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function AuthenticatedEvaluationPage() {
  const { status } = useAuth()
  if (status === "loading") return <p>正在恢复会话</p>
  if (status !== "authenticated") return <p>登录页</p>
  return <EvaluationCenterPage />
}

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

function renderEvaluationCenter(input: {
  contextResponse?: () => Response | Promise<Response>
  queryClient?: QueryClient
} = {}) {
  let contextCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return jsonResponse({
        account_id: accountId,
        person_id: "20000000-0000-4000-8000-000000000094",
        full_name: "评价中心测试账号",
        email: "evaluation-center@example.test",
        force_password_change: false,
        permissions: ["evaluation_center.access"],
      })
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "evaluation-center-csrf" })
    }
    if (url.endsWith("/evaluations/context")) {
      contextCalls += 1
      return input.contextResponse?.() ?? jsonResponse(evaluationContext)
    }
    if (url.endsWith("/evaluations/system/dashboard")) {
      return jsonResponse({
        api_version: "v2",
        upload_available: false,
        processing: 1,
        pending_match: 2,
        manual_input: 3,
        needs_review: 4,
        rejected: 5,
        published: 6,
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient =
    input.queryClient ??
    new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <MemoryRouter initialEntries={["/evaluations"]}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <Routes>
            <Route path="/" element={<p>工作台首页</p>} />
            <Route
              path="/evaluations"
              element={<AuthenticatedEvaluationPage />}
            />
          </Routes>
          <LocationProbe />
        </AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  return { contextCalls: () => contextCalls, fetchMock, queryClient }
}

afterEach(() => vi.unstubAllGlobals())

it("hides every evaluation branch the current account cannot use", async () => {
  renderEvaluationCenter()

  expect(await screen.findByRole("link", { name: "打开系统评价" })).toBeVisible()
  expect(screen.queryByText("教学评语")).not.toBeInTheDocument()
  expect(screen.queryByText("营期评价")).not.toBeInTheDocument()
  expect(screen.queryByText("尚未开放")).not.toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: "进入教学评语" }),
  ).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /上传/ })).not.toBeInTheDocument()
})

it("shows only student-facing entries enabled by the server", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        reports_available: true,
        game_accounts_available: true,
        surveys_available: true,
        survey_sections: {
          respondent: true,
          staff_feedback: false,
          administration: false,
        },
      }),
  })

  expect(await screen.findByRole("link", { name: "打开系统评价" })).toBeVisible()
  expect(screen.getByRole("link", { name: "我的游戏账号" })).toBeVisible()
  expect(screen.getByRole("link", { name: "进入教学评语" })).toBeVisible()
  expect(screen.getByRole("link", { name: "填写营期问卷" })).toBeVisible()
  expect(
    screen.queryByRole("link", { name: "查看收到的匿名反馈" }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: "管理营期评价" }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: "基础数据管理" }),
  ).not.toBeInTheDocument()
})

it("shows only staff-facing entries enabled by the server", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        reports_available: true,
        surveys_available: true,
        survey_sections: {
          respondent: false,
          staff_feedback: true,
          administration: false,
        },
      }),
  })

  expect(await screen.findByRole("link", { name: "进入教学评语" })).toBeVisible()
  expect(
    screen.getByRole("link", { name: "查看收到的匿名反馈" }),
  ).toBeVisible()
  expect(
    screen.queryByRole("link", { name: "填写营期问卷" }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: "基础数据管理" }),
  ).not.toBeInTheDocument()
})

it("shows only administration entries explicitly enabled by the server", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        admin_available: true,
        admin_sections: {
          ...evaluationContext.admin_sections,
          student_matching: true,
        },
        survey_sections: {
          respondent: false,
          staff_feedback: false,
          administration: true,
        },
      }),
  })

  expect(
    await screen.findByRole("link", { name: "基础数据管理" }),
  ).toBeVisible()
  expect(screen.getByRole("link", { name: "管理营期评价" })).toBeVisible()
  expect(
    screen.queryByRole("link", { name: "我的游戏账号" }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: "进入教学评语" }),
  ).not.toBeInTheDocument()
})

it("shows an empty state and skips system data when no evaluation is available", async () => {
  const result = renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        system_evaluation_available: false,
      }),
  })

  expect(await screen.findByRole("heading", { name: "暂无可用评价" })).toBeVisible()
  expect(screen.queryByLabelText("评价类型")).not.toBeInTheDocument()
  expect(screen.queryByText("系统评价概览")).not.toBeInTheDocument()
  expect(
    result.fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith("/evaluations/system/dashboard"),
    ),
  ).toBe(false)
})

it("opens the teaching report entry only when the server enables it", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        reports_available: true,
      }),
  })

  expect(
    await screen.findByRole("link", { name: "进入教学评语" }),
  ).toHaveAttribute("href", "/evaluations/reports")
  expect(screen.getByText("教学评语").closest("article")).toHaveClass("active")
})

it("routes a staff-only survey capability directly to anonymous feedback", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        surveys_available: true,
        survey_sections: {
          respondent: false,
          staff_feedback: true,
          administration: false,
        },
      }),
  })

  expect(
    await screen.findByRole("link", { name: "查看收到的匿名反馈" }),
  ).toHaveAttribute("href", "/evaluations/surveys/feedback")
  expect(
    screen.queryByRole("link", { name: "填写营期问卷" }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("link", { name: "管理营期评价" }),
  ).not.toBeInTheDocument()
})

it("renders each enabled camp-survey entry independently of the aggregate flag", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        surveys_available: false,
        survey_sections: {
          respondent: true,
          staff_feedback: true,
          administration: true,
        },
      }),
  })

  expect(
    await screen.findByRole("link", { name: "填写营期问卷" }),
  ).toHaveAttribute("href", "/evaluations/surveys")
  expect(
    screen.getByRole("link", { name: "查看收到的匿名反馈" }),
  ).toHaveAttribute("href", "/evaluations/surveys/feedback")
  expect(
    screen.getByRole("link", { name: "管理营期评价" }),
  ).toHaveAttribute("href", "/evaluations/surveys/admin")
  expect(screen.getByText("营期评价").closest("article")).toHaveClass("active")
})

it("embeds the ERP-native system evaluation dashboard", async () => {
  const result = renderEvaluationCenter()

  expect(await screen.findByRole("heading", { name: "系统评价概览" })).toBeVisible()
  expect(screen.getByRole("article", { name: "待审核" })).toHaveTextContent("4")
  expect(
    result.fetchMock.mock.calls.some(([input]) =>
      String(input).endsWith("/evaluations/system/dashboard"),
    ),
  ).toBe(true)
})

it("shows My game accounts only when the server context enables it", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({ ...evaluationContext, game_accounts_available: true }),
  })

  expect(
    await screen.findByRole("link", { name: "我的游戏账号" }),
  ).toHaveAttribute("href", "/evaluations/game-accounts")
})

it("hides My game accounts when the server context disables it", async () => {
  renderEvaluationCenter()

  await screen.findByRole("heading", { name: "评价中心" })
  expect(
    screen.queryByRole("link", { name: "我的游戏账号" }),
  ).not.toBeInTheDocument()
})

it.each([
  { cohort_staff: true, student_matching: false, analysis_profiles: false },
  { cohort_staff: false, student_matching: true, analysis_profiles: false },
  { cohort_staff: false, student_matching: false, analysis_profiles: true },
])("shows foundation administration when any server admin section is enabled: %#", async (adminSections) => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        admin_available: true,
        admin_sections: adminSections,
      }),
  })

  expect(
    await screen.findByRole("link", { name: "基础数据管理" }),
  ).toHaveAttribute("href", "/evaluations/admin")
})

it("hides foundation administration when every server admin section is disabled", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({ ...evaluationContext, admin_available: true }),
  })

  await screen.findByRole("heading", { name: "评价中心" })
  expect(
    screen.queryByRole("link", { name: "基础数据管理" }),
  ).not.toBeInTheDocument()
})

it("hides foundation administration when its aggregate capability is disabled", async () => {
  renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse({
        ...evaluationContext,
        admin_available: false,
        admin_sections: {
          ...evaluationContext.admin_sections,
          cohort_staff: true,
        },
      }),
  })

  await screen.findByRole("heading", { name: "评价中心" })
  expect(
    screen.queryByRole("link", { name: "基础数据管理" }),
  ).not.toBeInTheDocument()
})

it("never renders upload controls when upload is unavailable and keeps task history reachable", async () => {
  renderEvaluationCenter()

  await screen.findByRole("heading", { name: "评价中心" })
  expect(document.querySelector('input[type="file"]')).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /上传/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: /上传/ })).not.toBeInTheDocument()
  expect(screen.getByText("上传截图，并查看 ERP 任务处理与已发布结果。")).toBeVisible()
  expect(await screen.findByRole("article", { name: "可上传" })).toHaveTextContent(
    "未开放",
  )
})

it("hides cached entry actions while the private context is refetching", async () => {
  let resolveContext!: (response: Response) => void
  const pendingContext = new Promise<Response>((resolve) => {
    resolveContext = resolve
  })
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(evaluationKeys.context(accountId), evaluationContext)
  renderEvaluationCenter({
    queryClient,
    contextResponse: () => pendingContext,
  })

  expect(await screen.findByText("正在更新评价中心")).toBeVisible()
  expect(
    screen.queryByRole("link", { name: "打开系统评价" }),
  ).not.toBeInTheDocument()

  resolveContext(jsonResponse(evaluationContext))
  expect(await screen.findByRole("link", { name: "打开系统评价" })).toBeVisible()
})

it("keeps the ERP session, refreshes auth, and leaves evaluations after access is revoked", async () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  queryClient.setQueryData(evaluationKeys.context(accountId), {
    ...evaluationContext,
    embedded_start_url: "https://cached.invalid/old-entry",
  })
  const result = renderEvaluationCenter({
    queryClient,
    contextResponse: () =>
      jsonResponse(
        {
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "evaluation-context-403",
        },
        403,
      ),
  })

  expect(await screen.findByText("正在更新评价中心")).toBeVisible()
  expect(await screen.findByText("工作台首页")).toBeVisible()
  expect(screen.getByTestId("location")).toHaveTextContent("/")
  await waitFor(() => {
    expect(
      queryClient.getQueryData(evaluationKeys.context(accountId)),
    ).toBeUndefined()
  })
  expect(
    result.fetchMock.mock.calls.filter(([input]) => String(input).endsWith("/auth/me")),
  ).toHaveLength(2)
  expect(screen.queryByText("登录页")).not.toBeInTheDocument()
})

it("keeps the ERP session and offers retry after a server error", async () => {
  const user = userEvent.setup()
  const result = renderEvaluationCenter({
    contextResponse: () =>
      jsonResponse(
        {
          type: "about:blank",
          title: "Server Error",
          status: 500,
          detail: "评价中心暂时不可用",
          code: "internal_error",
          request_id: "evaluation-context-500",
        },
        500,
      ),
  })

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "评价中心暂时不可用",
  )
  expect(screen.getByTestId("location")).toHaveTextContent("/evaluations")
  expect(screen.queryByText("登录页")).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "重试" }))
  await waitFor(() => expect(result.contextCalls()).toBeGreaterThan(1))
})
