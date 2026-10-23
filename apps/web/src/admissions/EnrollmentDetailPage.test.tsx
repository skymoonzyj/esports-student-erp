import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type {
  Enrollment,
  EnrollmentActionContext,
  EnrollmentTransferOption,
  WithdrawalPreview,
} from "./api"
import { EnrollmentDetailPage } from "./EnrollmentDetailPage"

const apiMocks = vi.hoisted(() => ({
  cancelEnrollment: vi.fn(),
  confirmWithdrawal: vi.fn(),
  getEnrollment: vi.fn(),
  getEnrollmentActionContext: vi.fn(),
  listEnrollmentTransferOptions: vi.fn(),
  previewWithdrawal: vi.fn(),
  transferEnrollment: vi.fn(),
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
  paid_cents: 960_000,
  refunded_cents: 0,
  net_paid_cents: 960_000,
  due_cents: 320_000,
  overpaid_cents: 0,
  status: "active",
  confirmed_at: "2026-08-01T10:00:00+08:00",
  cancelled_at: null,
  completed_at: null,
  created_at: "2026-08-01T10:00:00+08:00",
  updated_at: "2026-08-01T10:00:00+08:00",
}

const actionContext: EnrollmentActionContext = {
  manage_allowed: true,
  refund_allowed: true,
  read_only_reason: null,
}

const transferOption: EnrollmentTransferOption = {
  class_id: "class-2",
  class_name: "英雄联盟菁英班",
  capacity: 20,
  active_enrollment_count: 18,
  is_full: false,
  capacity_override_allowed: true,
}

const preview: WithdrawalPreview = {
  withdrawal_date: "2026-08-06",
  policy: "prorated_remaining_days",
  total_days: 10,
  remaining_days: 4,
  agreed_tuition_cents: 1_280_000,
  available_paid_cents: 960_000,
  refund_cents: 512_000,
  allocations: [
    { payment_id: "payment-new", amount_cents: 400_000 },
    { payment_id: "payment-old", amount_cents: 112_000 },
  ],
}

const permissions = new Set([
  "teaching.read",
  "enrollments.read",
  "enrollments.manage",
  "enrollments.capacity_override",
  "payments.read",
  "refunds.manage",
])

function renderPage(): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter initialEntries={["/teaching/enrollments/enrollment-1"]}>
      <QueryClientProvider client={queryClient}>
        <EnrollmentDetailPage
          enrollmentId="enrollment-1"
          permissions={permissions}
        />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return queryClient
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getEnrollment.mockResolvedValue(enrollment)
  apiMocks.getEnrollmentActionContext.mockResolvedValue(actionContext)
  apiMocks.listEnrollmentTransferOptions.mockResolvedValue({
    items: [transferOption],
    page: 1,
    page_size: 100,
    total: 1,
  })
  apiMocks.transferEnrollment.mockResolvedValue({
    ...enrollment,
    class_id: transferOption.class_id,
    class_name: transferOption.class_name,
  })
  apiMocks.cancelEnrollment.mockResolvedValue({
    ...enrollment,
    status: "cancelled",
    cancelled_at: "2026-08-06T10:00:00+08:00",
  })
  apiMocks.previewWithdrawal.mockResolvedValue(preview)
  apiMocks.confirmWithdrawal.mockResolvedValue({
    ...enrollment,
    status: "cancelled",
    cancelled_at: "2026-08-06T10:00:00+08:00",
  })
})

it("shows enrollment identity, payment state and server-authorized actions", async () => {
  renderPage()

  expect(await screen.findByRole("heading", { name: "张同学" })).toBeVisible()
  expect(screen.getByText(enrollment.order_number)).toBeVisible()
  expect(screen.getByText("2026 暑期营")).toBeVisible()
  expect(screen.getByText("英雄联盟普通班")).toBeVisible()
  expect(screen.getByText("部分付款")).toBeVisible()
  expect(screen.getByText("¥12,800.00")).toBeVisible()
  expect(screen.getByText("欠款 ¥3,200.00")).toBeVisible()
  expect(screen.getByRole("button", { name: "转班" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "取消报名" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "退营并退款" })).toBeEnabled()
})

it("keeps archived or amount-hidden enrollment details read only", async () => {
  apiMocks.getEnrollment.mockResolvedValue({
    ...enrollment,
    agreed_tuition_cents: null,
    paid_cents: null,
    refunded_cents: null,
    net_paid_cents: null,
    due_cents: null,
    overpaid_cents: null,
  })
  apiMocks.getEnrollmentActionContext.mockResolvedValue({
    manage_allowed: false,
    refund_allowed: false,
    read_only_reason: "archived",
  })

  renderPage()

  expect(await screen.findByText("营期已归档，报名记录仅可查看。")).toBeVisible()
  expect(screen.getByText("金额不可见")).toBeVisible()
  expect(screen.queryByRole("button", { name: "转班" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "取消报名" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "退营并退款" })).not.toBeInTheDocument()
})

it("transfers within the cohort and requires a reason for a full class override", async () => {
  const user = userEvent.setup()
  const queryClient = renderPage()
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries")
  const fullOption = {
    ...transferOption,
    active_enrollment_count: 20,
    is_full: true,
  }
  apiMocks.listEnrollmentTransferOptions.mockResolvedValue({
    items: [fullOption],
    page: 1,
    page_size: 100,
    total: 1,
  })
  await user.click(await screen.findByRole("button", { name: "转班" }))
  const dialog = await screen.findByRole("dialog", { name: "转班" })
  await user.click(within(dialog).getByRole("radio", { name: /英雄联盟菁英班/ }))
  await user.type(within(dialog).getByLabelText("转班原因"), "调整训练方向")
  expect(within(dialog).getByRole("button", { name: "确认转班" })).toBeDisabled()
  await user.type(within(dialog).getByLabelText("超额转班原因"), "职业队试训安排")
  await user.click(within(dialog).getByRole("button", { name: "确认转班" }))

  await waitFor(() =>
    expect(apiMocks.transferEnrollment).toHaveBeenCalledWith("enrollment-1", {
      target_class_id: "class-2",
      reason: "调整训练方向",
      capacity_override_reason: "职业队试训安排",
    }),
  )
  expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ["cohort-students", "cohort-1"],
  })
})

it("cancels enrollment without silently creating a refund", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "取消报名" }))
  const dialog = await screen.findByRole("dialog", { name: "取消报名" })
  expect(within(dialog).getByText("取消报名不会自动退款，付款记录将继续保留。")).toBeVisible()
  await user.type(within(dialog).getByLabelText("取消原因"), "改到下一期")
  await user.click(within(dialog).getByRole("button", { name: "确认取消报名" }))

  await waitFor(() =>
    expect(apiMocks.cancelEnrollment).toHaveBeenCalledWith(
      "enrollment-1",
      "改到下一期",
    ),
  )
  expect(apiMocks.previewWithdrawal).not.toHaveBeenCalled()
  expect(apiMocks.confirmWithdrawal).not.toHaveBeenCalled()
})

it("requires a read-only preview and second confirmation for prorated withdrawal", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "退营并退款" }))
  const dialog = await screen.findByRole("dialog", { name: "退营并退款" })
  expect(within(dialog).getByText(enrollment.order_number)).toBeVisible()
  expect(within(dialog).getByRole("button", { name: `复制订单号 ${enrollment.order_number}` })).toBeEnabled()
  await user.clear(within(dialog).getByLabelText("退营日期"))
  await user.type(within(dialog).getByLabelText("退营日期"), "2026-08-06")
  await user.click(within(dialog).getByLabelText("按剩余日期退款"))
  await user.click(within(dialog).getByRole("button", { name: "计算退款" }))

  expect(await within(dialog).findByRole("heading", { name: "退款计算预览" })).toBeVisible()
  expect(within(dialog).getByText("共 10 天")).toBeVisible()
  expect(within(dialog).getByText("剩余 4 天")).toBeVisible()
  expect(within(dialog).getByText("¥5,120.00")).toBeVisible()
  expect(within(dialog).getByText("payment-new")).toBeVisible()
  expect(apiMocks.confirmWithdrawal).not.toHaveBeenCalled()

  await user.type(within(dialog).getByLabelText("退营原因"), "家庭安排变化")
  await user.click(
    within(dialog).getByRole("button", { name: "二次确认退营并退款" }),
  )
  await waitFor(() =>
    expect(apiMocks.confirmWithdrawal).toHaveBeenCalledWith("enrollment-1", {
      ...preview,
      reason: "家庭安排变化",
    }),
  )
})

it("supports refunding all remaining paid money", async () => {
  const user = userEvent.setup()
  apiMocks.previewWithdrawal.mockResolvedValue({
    ...preview,
    policy: "full_remaining_paid",
    refund_cents: 960_000,
    allocations: [{ payment_id: "payment-new", amount_cents: 960_000 }],
  })
  renderPage()

  await user.click(await screen.findByRole("button", { name: "退营并退款" }))
  const dialog = await screen.findByRole("dialog", { name: "退营并退款" })
  await user.clear(within(dialog).getByLabelText("退营日期"))
  await user.type(within(dialog).getByLabelText("退营日期"), "2026-08-06")
  await user.click(within(dialog).getByLabelText("退还剩余已付款"))
  await user.click(within(dialog).getByRole("button", { name: "计算退款" }))

  await waitFor(() =>
    expect(apiMocks.previewWithdrawal).toHaveBeenCalledWith("enrollment-1", {
      withdrawal_date: "2026-08-06",
      policy: "full_remaining_paid",
    }),
  )
  const refundTotal = await within(dialog).findByText("本次退款")
  expect(within(refundTotal.parentElement as HTMLElement).getByText("¥9,600.00")).toBeVisible()
})

it("discards a stale withdrawal preview and forces a fresh calculation", async () => {
  const user = userEvent.setup()
  apiMocks.confirmWithdrawal.mockRejectedValue(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "招生业务状态已发生变化",
      code: "withdrawal_refund_changed",
      request_id: "stale-preview",
    }),
  )
  renderPage()

  await user.click(await screen.findByRole("button", { name: "退营并退款" }))
  const dialog = await screen.findByRole("dialog", { name: "退营并退款" })
  await user.clear(within(dialog).getByLabelText("退营日期"))
  await user.type(within(dialog).getByLabelText("退营日期"), "2026-08-06")
  await user.click(within(dialog).getByRole("button", { name: "计算退款" }))
  await user.type(await within(dialog).findByLabelText("退营原因"), "家庭安排变化")
  await user.click(
    within(dialog).getByRole("button", { name: "二次确认退营并退款" }),
  )

  expect(
    await within(dialog).findByText("付款或退款状态已变化，请重新计算退款。"),
  ).toBeVisible()
  expect(within(dialog).queryByRole("heading", { name: "退款计算预览" })).not.toBeInTheDocument()
  expect(within(dialog).getByRole("button", { name: "计算退款" })).toBeEnabled()
  await waitFor(() => expect(apiMocks.getEnrollment).toHaveBeenCalledTimes(2))
})
