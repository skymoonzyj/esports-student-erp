import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { evaluationKeys } from "./api"
import { EvaluationAdminPage } from "./EvaluationAdminPage"

const accountId = "10000000-0000-4000-8000-000000000001"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPage(sections: {
  cohort_staff: boolean
  student_matching: boolean
  analysis_profiles: boolean
}, contextResponses: Array<{
  cohort_staff: boolean
  student_matching: boolean
  analysis_profiles: boolean
} | Promise<{
  cohort_staff: boolean
  student_matching: boolean
  analysis_profiles: boolean
}>> = [sections]) {
  let contextCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/evaluations/context")) {
      const responseIndex = contextCalls
      contextCalls += 1
      const currentSections = await contextResponses[
        Math.min(responseIndex, contextResponses.length - 1)
      ]
      return jsonResponse({
        accessible: true,
        system_evaluation_available: true,
        embedded_start_url: "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
        standalone_start_url:
          "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
        upload_available: false,
        video_available: false,
        reports_available: false,
        surveys_available: false,
        game_accounts_available: false,
        admin_available: Object.values(currentSections).some(Boolean),
        admin_sections: currentSections,
        integration_api_version: "v1",
      })
    }
    if (url.includes("/evaluations/admin/students?")) {
      return jsonResponse({ items: [], total: 0, page: 1, page_size: 20 })
    }
    if (url.includes("/projects?")) {
      return jsonResponse({
        items: [{ id: "project-admin-page", name: "英雄联盟", is_active: true }],
        total: 1,
        page: 1,
        page_size: 100,
      })
    }
    if (url.endsWith("/evaluations/admin/projects/project-admin-page/analysis-profiles")) {
      return jsonResponse([
        {
          id: "profile-admin-page",
          project_id: "project-admin-page",
          project_name: "英雄联盟",
          version: 1,
          mode: "manual_only",
          adapter_id: null,
          adapter_version: null,
          scoring_rule_version: null,
          required_fields: [],
          confidence_thresholds: {},
          auto_publish_warning_levels: [],
          publish_mode: "review_required",
          effective_from: "2026-08-01T09:00:00+08:00",
          configured_by_name: "配置管理员",
          change_reason: "初始配置",
          is_current: true,
        },
      ])
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <EvaluationAdminPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { contextCalls: () => contextCalls, fetchMock, queryClient }
}

afterEach(() => vi.unstubAllGlobals())

it("shows only server-enabled administration sections", async () => {
  renderPage({
    cohort_staff: false,
    student_matching: true,
    analysis_profiles: true,
  })

  expect(await screen.findByRole("heading", { name: "基础数据管理" })).toBeVisible()
  expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()
  expect(screen.getByRole("tab", { name: "学员匹配" })).toBeVisible()
  expect(screen.getByRole("tab", { name: "分析配置" })).toBeVisible()
})

it("switches between enabled focused panels without exposing unavailable sections", async () => {
  const user = userEvent.setup()
  renderPage({
    cohort_staff: false,
    student_matching: true,
    analysis_profiles: true,
  })

  const studentTab = await screen.findByRole("tab", { name: "学员匹配" })
  expect(studentTab).toHaveAttribute("aria-selected", "true")
  expect(await screen.findByRole("region", { name: "学员匹配" })).toBeVisible()

  await user.click(screen.getByRole("tab", { name: "分析配置" }))
  expect(screen.getByRole("tab", { name: "分析配置" })).toHaveAttribute(
    "aria-selected",
    "true",
  )
  expect(await screen.findByRole("region", { name: "分析配置" })).toBeVisible()
  expect(screen.queryByText("工作人员服务" )).not.toBeInTheDocument()
})

it("preserves an open administration form across a successful context revalidation", async () => {
  const sections = {
    cohort_staff: false,
    student_matching: false,
    analysis_profiles: true,
  }
  let resolveContext!: (value: typeof sections) => void
  const pendingContext = new Promise<typeof sections>((resolve) => {
    resolveContext = resolve
  })
  const result = renderPage(sections, [sections, pendingContext])
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "创建新版本" }))
  await user.type(screen.getByLabelText("变更原因"), "保留未提交分析配置")

  const refresh = result.queryClient.invalidateQueries({
    queryKey: evaluationKeys.context(accountId),
  })
  await waitFor(() => expect(result.contextCalls()).toBe(2))
  resolveContext(sections)
  await refresh

  expect(await screen.findByRole("dialog", { name: "创建分析配置新版本" })).toBeVisible()
  expect(screen.getByLabelText("变更原因")).toHaveValue("保留未提交分析配置")
})
