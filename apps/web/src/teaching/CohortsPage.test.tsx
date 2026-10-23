import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { CohortsPage } from "./CohortsPage"

const api = vi.hoisted(() => ({
  listCohorts: vi.fn(),
  createCohort: vi.fn(),
}))

vi.mock("./api", () => api)

const cohort = {
  id: "70000000-0000-4000-8000-000000000001",
  name: "2026 八月青训营",
  starts_at: "2026-08-01T00:00:00+08:00",
  ends_at: "2026-08-08T00:00:00+08:00",
  default_check_in_at: "2026-08-01T00:00:00+08:00",
  default_check_out_at: "2026-08-08T00:00:00+08:00",
  status: "upcoming" as const,
  auto_start_enabled: true,
  auto_complete_enabled: false,
  automation_issues: ["auto_start_transition_blocked" as const],
  enabled_class_count: 3,
  missing_primary_class_count: 2,
  created_at: "2026-07-01T00:00:00+08:00",
  updated_at: "2026-07-01T00:00:00+08:00",
}

function renderPage(permissions: string[]) {
  return render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <CohortsPage permissions={new Set(permissions)} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  api.listCohorts.mockResolvedValue({
    items: [cohort],
    page: 1,
    page_size: 20,
    total: 21,
  })
  api.createCohort.mockResolvedValue(cohort)
})

it("applies exact cohort filters, paginates, and keeps global readers read-only", async () => {
  const user = userEvent.setup()
  renderPage(["teaching.read"])

  expect(await screen.findByRole("link", { name: "2026 八月青训营" })).toBeVisible()
  expect(screen.getByText("3 个启用班级")).toBeVisible()
  expect(screen.getByText("2 个班级缺少主教练")).toBeVisible()
  expect(screen.getByText("自动开营受阻")).toBeVisible()
  expect(screen.getByText("自动开营 开启")).toBeVisible()
  expect(screen.getByText("自动结营 关闭")).toBeVisible()
  expect(screen.queryByRole("button", { name: "新增营期" })).not.toBeInTheDocument()

  await user.type(screen.getByLabelText("营期名称"), "2026 八月青训营")
  await user.selectOptions(screen.getByLabelText("营期状态"), "upcoming")
  fireEvent.change(screen.getByLabelText("开营日期从"), { target: { value: "2026-08-01" } })
  fireEvent.change(screen.getByLabelText("开营日期至"), { target: { value: "2026-08-31" } })
  await user.click(screen.getByRole("button", { name: "查询营期" }))

  await waitFor(() =>
    expect(api.listCohorts).toHaveBeenLastCalledWith({
      name: "2026 八月青训营",
      status: "upcoming",
      dateFrom: "2026-08-01T00:00:00+08:00",
      dateTo: "2026-09-01T00:00:00+08:00",
      page: 1,
      pageSize: 20,
    }),
  )

  await user.click(screen.getByRole("button", { name: "下一页" }))
  await waitFor(() =>
    expect(api.listCohorts).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 })),
  )
})

it("initializes exact cohort times, keeps automation toggles independent, and retains errors", async () => {
  const user = userEvent.setup()
  api.createCohort.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "营期名称已存在",
      code: "duplicate_cohort",
      request_id: "cohort-conflict",
    }),
  )
  renderPage(["teaching.read", "cohorts.manage"])
  await screen.findByText("2026 八月青训营")

  await user.click(screen.getByRole("button", { name: "新增营期" }))
  const dialog = screen.getByRole("dialog", { name: "新增营期" })
  await user.type(within(dialog).getByLabelText("营期名称"), "八月菁英营")
  fireEvent.change(within(dialog).getByLabelText("开营日期"), {
    target: { value: "2026-08-01" },
  })
  fireEvent.change(within(dialog).getByLabelText("结营日期"), {
    target: { value: "2026-08-07" },
  })

  expect(within(dialog).getByLabelText("开营时间")).toHaveValue("2026-08-01T00:00")
  expect(within(dialog).getByLabelText("结营时间")).toHaveValue("2026-08-08T00:00")
  expect(within(dialog).getByLabelText("默认入住时间")).toHaveValue("2026-08-01T00:00")
  expect(within(dialog).getByLabelText("默认离营时间")).toHaveValue("2026-08-08T00:00")

  fireEvent.change(within(dialog).getByLabelText("开营时间"), {
    target: { value: "2026-08-01T09:30" },
  })
  await user.click(within(dialog).getByLabelText("自动开营"))
  expect(within(dialog).getByLabelText("自动开营")).toBeChecked()
  expect(within(dialog).getByLabelText("自动结营")).not.toBeChecked()
  await user.click(within(dialog).getByLabelText("自动结营"))
  await user.click(within(dialog).getByLabelText("自动开营"))

  await user.click(within(dialog).getByRole("button", { name: "保存营期" }))
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("营期名称已存在")
  expect(within(dialog).getByLabelText("营期名称")).toHaveValue("八月菁英营")

  await user.click(within(dialog).getByRole("button", { name: "保存营期" }))
  await waitFor(() =>
    expect(api.createCohort).toHaveBeenLastCalledWith({
      name: "八月菁英营",
      starts_at: "2026-08-01T09:30:00+08:00",
      ends_at: "2026-08-08T00:00:00+08:00",
      default_check_in_at: "2026-08-01T00:00:00+08:00",
      default_check_out_at: "2026-08-08T00:00:00+08:00",
      auto_start_enabled: false,
      auto_complete_enabled: true,
    }),
  )
})
