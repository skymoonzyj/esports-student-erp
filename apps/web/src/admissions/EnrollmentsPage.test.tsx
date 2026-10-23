import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import type { Enrollment } from "./api"
import { EnrollmentsPage } from "./EnrollmentsPage"

const apiMocks = vi.hoisted(() => ({
  listEnrollments: vi.fn(),
}))

vi.mock("./api", () => apiMocks)

const enrollment: Enrollment = {
  id: "enrollment-1",
  order_number: "BM20260911000001",
  admission_case_id: "case-1",
  student_person_id: "student-1",
  student_name: "张同学",
  cohort_id: "cohort-1",
  cohort_name: "2026 暑期营",
  class_id: "class-1",
  class_name: "英雄联盟普通班",
  agreed_tuition_cents: 1_280_000,
  payment_status: "partial",
  paid_cents: 500_000,
  refunded_cents: 0,
  net_paid_cents: 500_000,
  due_cents: 780_000,
  overpaid_cents: 0,
  status: "active",
  confirmed_at: "2026-08-01T10:00:00+08:00",
  cancelled_at: null,
  completed_at: null,
  created_at: "2026-08-01T10:00:00+08:00",
  updated_at: "2026-08-01T10:00:00+08:00",
}

const permissions = new Set([
  "teaching.read",
  "enrollments.read",
  "payments.read",
])

function renderPage(customPermissions: ReadonlySet<string> = permissions): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter initialEntries={["/teaching/enrollments"]}>
      <QueryClientProvider client={queryClient}>
        <EnrollmentsPage permissions={customPermissions} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return queryClient
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.listEnrollments.mockResolvedValue({
    items: [enrollment],
    page: 1,
    page_size: 20,
    total: 1,
  })
})

it("shows the active enrollment, payment state, debt and detail entry", async () => {
  renderPage()

  expect(await screen.findByRole("heading", { name: "正式报名" })).toBeVisible()
  const row = await screen.findByRole("row", { name: /张同学/ })
  expect(within(row).getByText("张同学")).toBeVisible()
  expect(within(row).getByText(enrollment.order_number)).toBeVisible()
  expect(within(row).getByText("2026 暑期营 · 英雄联盟普通班")).toBeVisible()
  expect(within(row).getByText("在营")).toBeVisible()
  expect(within(row).getByText("部分付款")).toBeVisible()
  expect(within(row).getByText("约定学费 ¥12,800.00")).toBeVisible()
  expect(within(row).getByText("欠款 ¥7,800.00")).toBeVisible()
  expect(within(row).getByRole("link", { name: "查看详情" })).toHaveAttribute(
    "href",
    "/teaching/enrollments/enrollment-1",
  )
})

it("keeps server-hidden amounts hidden without inventing zero values", async () => {
  apiMocks.listEnrollments.mockResolvedValue({
    items: [
      {
        ...enrollment,
        agreed_tuition_cents: null,
        paid_cents: null,
        refunded_cents: null,
        net_paid_cents: null,
        due_cents: null,
        overpaid_cents: null,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })

  renderPage(new Set(["enrollments.read"]))

  expect(await screen.findByText("金额不可见")).toBeVisible()
  expect(screen.getByText("部分付款")).toBeVisible()
  expect(screen.queryByText("¥0.00")).not.toBeInTheDocument()
})

it("resets pagination when each enrollment status is selected", async () => {
  apiMocks.listEnrollments.mockImplementation(
    async ({ status, page = 1 }) => ({
      items: [{ ...enrollment, status: status ?? "active" }],
      page,
      page_size: 20,
      total: 41,
    }),
  )
  const user = userEvent.setup()
  renderPage()

  const pagination = await screen.findByRole("navigation", {
    name: "正式报名分页",
  })
  await user.click(within(pagination).getByRole("button", { name: "下一页" }))
  await waitFor(() =>
    expect(apiMocks.listEnrollments).toHaveBeenCalledWith({
      status: "active",
      page: 2,
      pageSize: 20,
    }),
  )

  for (const status of ["cancelled", "completed", "all", "active"] as const) {
    await user.selectOptions(screen.getByLabelText("报名状态"), status)
    await waitFor(() =>
      expect(apiMocks.listEnrollments).toHaveBeenCalledWith({
        status: status === "all" ? undefined : status,
        page: 1,
        pageSize: 20,
      }),
    )
  }
})

it("keeps pagination available when a later enrollment page becomes empty", async () => {
  apiMocks.listEnrollments.mockImplementation(
    async ({ page = 1 }) => ({
      items: page === 1 ? [enrollment] : [],
      page,
      page_size: 20,
      total: 21,
    }),
  )
  const user = userEvent.setup()
  renderPage()

  const pagination = await screen.findByRole("navigation", {
    name: "正式报名分页",
  })
  await user.click(within(pagination).getByRole("button", { name: "下一页" }))

  expect(await screen.findByText("当前状态下没有正式报名记录。")).toBeVisible()
  expect(
    within(screen.getByRole("navigation", { name: "正式报名分页" })).getByRole(
      "button",
      { name: "上一页" },
    ),
  ).toBeEnabled()
})

it("renders labeled cells for tablet and mobile card layouts", async () => {
  renderPage()

  const row = await screen.findByRole("row", { name: /张同学/ })
  expect(within(row).getByRole("cell", { name: /张同学/ })).toHaveAttribute(
    "data-label",
    "学员",
  )
  expect(within(row).getByRole("cell", { name: /2026 暑期营/ })).toHaveAttribute(
    "data-label",
    "营期与班级",
  )
  expect(within(row).getByRole("cell", { name: /欠款/ })).toHaveAttribute(
    "data-label",
    "学费与付款",
  )
})

it("shows a stable inline error when the enrollment list cannot be read", async () => {
  apiMocks.listEnrollments.mockRejectedValue(new Error("network unavailable"))

  renderPage()

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "正式报名名单暂时无法读取",
  )
  expect(screen.queryByText("当前状态下没有正式报名记录。")).not.toBeInTheDocument()
})
