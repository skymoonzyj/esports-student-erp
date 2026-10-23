import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { Payment, PaymentConflict, ServiceAccount } from "./api"
import { PaymentsPage } from "./PaymentsPage"

const apiMocks = vi.hoisted(() => ({
  closeAdmissionCase: vi.fn(),
  createManualPayment: vi.fn(),
  createRefund: vi.fn(),
  createServiceAccount: vi.fn(),
  createServiceCredential: vi.fn(),
  disableServiceAccount: vi.fn(),
  disableServiceCredential: vi.fn(),
  listPaymentConflicts: vi.fn(),
  listPayments: vi.fn(),
  listServiceAccounts: vi.fn(),
  resolvePaymentConflict: vi.fn(),
}))

vi.mock("./api", () => apiMocks)

const payment: Payment = {
  id: "payment-1",
  admission_case_id: "case-1",
  source_kind: "manual",
  source_system: null,
  external_order_id: null,
  payer_person_id: "student-1",
  amount_cents: 10_000,
  refunded_cents: 2_000,
  available_refund_cents: 8_000,
  currency: "CNY",
  paid_at: "2026-08-01T09:30:00+08:00",
  created_at: "2026-08-01T09:31:00+08:00",
}

const conflict: PaymentConflict = {
  id: "conflict-1",
  original_import_event_id: "event-1",
  conflict_type: "external_order_payload_mismatch",
  status: "open",
  resolution_note: null,
  resolved_at: null,
  created_at: "2026-08-01T10:00:00+08:00",
  updated_at: "2026-08-01T10:00:00+08:00",
}

const serviceAccount: ServiceAccount = {
  id: "service-1",
  name: "付款系统",
  source_system: "payment-provider",
  is_active: true,
  credentials: [
    {
      id: "credential-old",
      service_account_id: "service-1",
      public_id: "svc_old",
      name: "旧凭证",
      is_active: false,
      disabled_at: "2026-07-01T00:00:00+08:00",
    },
  ],
  created_at: "2026-07-01T00:00:00+08:00",
  updated_at: "2026-07-01T00:00:00+08:00",
}

const permissions = new Set([
  "teaching.read",
  "payments.read",
  "payments.manage",
  "refunds.manage",
  "payment_imports.manage",
  "admission_cases.read",
  "enrollments.read",
])

function renderPage(customPermissions = permissions) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter initialEntries={["/teaching/payments"]}>
      <QueryClientProvider client={queryClient}>
        <PaymentsPage permissions={customPermissions} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return queryClient
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.listPayments.mockResolvedValue({
    items: [payment],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listPaymentConflicts.mockResolvedValue({
    items: [conflict],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listServiceAccounts.mockResolvedValue({
    items: [serviceAccount],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.createManualPayment.mockResolvedValue(payment)
  apiMocks.createRefund.mockResolvedValue({
    id: "refund-1",
    payment_id: payment.id,
    amount_cents: 8_000,
    refunded_at: "2026-08-02T09:00:00+08:00",
    refund_type: "manual",
    reason: "家长申请",
    created_at: "2026-08-02T09:00:00+08:00",
  })
  apiMocks.resolvePaymentConflict.mockResolvedValue({
    ...conflict,
    status: "resolved",
    resolution_note: "已核对付款平台",
  })
  apiMocks.createServiceCredential.mockResolvedValue({
    id: "credential-new",
    service_account_id: serviceAccount.id,
    public_id: "svc_new",
    name: "付款系统测试凭证",
    secret: "one-time-machine-secret",
  })
})

it("creates a manual payment and keeps yuan as the typed decimal string", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("button", { name: "登记付款" }))
  await user.type(screen.getByLabelText("学员 ID"), "student-1")
  await user.type(screen.getByLabelText("付款金额（元）"), "36800.05")
  await user.type(screen.getByLabelText("备注"), "线下转账")
  await user.click(screen.getByRole("button", { name: "确认登记" }))

  await waitFor(() =>
    expect(apiMocks.createManualPayment).toHaveBeenCalledWith(
      expect.objectContaining({
        student_person_id: "student-1",
        admission_case_id: null,
        amount: "36800.05",
        note: "线下转账",
      }),
    ),
  )
})

it("refunds from one payment and shows original, refunded, and remaining amounts", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "退款 payment-1" }))
  const dialog = screen.getByRole("dialog", { name: "登记退款" })
  expect(within(dialog).getByText("¥100.00")).toBeVisible()
  expect(within(dialog).getByText("¥20.00")).toBeVisible()
  expect(within(dialog).getByText("¥80.00")).toBeVisible()
  await user.type(within(dialog).getByLabelText("退款金额（元）"), "80.00")
  await user.type(within(dialog).getByLabelText("退款原因"), "家长申请")
  await user.click(within(dialog).getByRole("button", { name: "确认退款" }))

  await waitFor(() =>
    expect(apiMocks.createRefund).toHaveBeenCalledWith(
      "payment-1",
      expect.objectContaining({ amount: "80.00", reason: "家长申请" }),
    ),
  )
})

it("requires a note before resolving an import conflict", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("tab", { name: "导入冲突" }))
  await user.click(await screen.findByRole("button", { name: "处理冲突" }))
  const dialog = screen.getByRole("dialog", { name: "处理导入冲突" })
  expect(within(dialog).getByRole("button", { name: "确认处理" })).toBeDisabled()
  await user.type(within(dialog).getByLabelText("处理说明"), "已核对付款平台")
  await user.click(within(dialog).getByRole("button", { name: "确认处理" }))

  await waitFor(() =>
    expect(apiMocks.resolvePaymentConflict).toHaveBeenCalledWith("conflict-1", {
      action: "resolved",
      note: "已核对付款平台",
    }),
  )
})

it("shows a new credential secret only inside the open one-time modal", async () => {
  const user = userEvent.setup()
  const queryClient = renderPage()

  await user.click(screen.getByRole("tab", { name: "接口凭证" }))
  expect(await screen.findByText("已停用")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "新建凭证" }))
  await user.type(screen.getByLabelText("凭证名称"), "付款系统测试凭证")
  await user.click(screen.getByRole("button", { name: "确认创建" }))

  expect(await screen.findByText("关闭后无法再次查看此密钥")).toBeVisible()
  expect(screen.getByText("one-time-machine-secret")).toBeVisible()
  expect(screen.getByRole("button", { name: "复制密钥" })).toBeVisible()
  expect(JSON.stringify(queryClient.getQueryCache().getAll())).not.toContain(
    "one-time-machine-secret",
  )
  expect(JSON.stringify(queryClient.getMutationCache().getAll())).not.toContain(
    "one-time-machine-secret",
  )
  await user.click(screen.getByRole("button", { name: "关闭密钥窗口" }))
  expect(screen.queryByText("one-time-machine-secret")).not.toBeInTheDocument()
})

it("applies payment filters without changing the typed phone or source values", async () => {
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("table", { name: "付款记录" })
  apiMocks.listPayments.mockClear()

  await user.selectOptions(screen.getByLabelText("付款来源"), "api")
  await user.type(screen.getByLabelText("来源系统"), "payment-provider")
  await user.type(screen.getByLabelText("付款手机号"), "13800138000")
  await user.click(screen.getByRole("button", { name: "应用筛选" }))

  await waitFor(() =>
    expect(apiMocks.listPayments).toHaveBeenCalledWith(
      expect.objectContaining({
        sourceKind: "api",
        sourceSystem: "payment-provider",
        payerPhone: "13800138000",
        page: 1,
      }),
    ),
  )
})

it("rejects a refund above the remaining refundable amount before calling the API", async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "退款 payment-1" }))
  const dialog = screen.getByRole("dialog", { name: "登记退款" })
  await user.type(within(dialog).getByLabelText("退款金额（元）"), "80.01")
  await user.type(within(dialog).getByLabelText("退款原因"), "金额超过上限")
  await user.click(within(dialog).getByRole("button", { name: "确认退款" }))

  expect(apiMocks.createRefund).not.toHaveBeenCalled()
  expect(
    within(dialog).getByText("退款金额必须为正数且不能超过剩余可退金额"),
  ).toBeVisible()
})

it("locks a payment form while its request is pending", async () => {
  let resolvePayment: ((value: Payment) => void) | undefined
  apiMocks.createManualPayment.mockImplementation(
    () =>
      new Promise<Payment>((resolve) => {
        resolvePayment = resolve
      }),
  )
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("button", { name: "登记付款" }))
  await user.type(screen.getByLabelText("学员 ID"), "student-1")
  await user.type(screen.getByLabelText("付款金额（元）"), "100.00")
  await user.type(screen.getByLabelText("备注"), "锁定测试")
  await user.click(screen.getByRole("button", { name: "确认登记" }))

  expect(await screen.findByRole("button", { name: "正在登记" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "关闭付款登记" })).toBeDisabled()
  expect(apiMocks.createManualPayment).toHaveBeenCalledTimes(1)
  resolvePayment?.(payment)
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "登记付款" })).not.toBeInTheDocument(),
  )
})

it("shows the server problem detail inline and keeps the payment form open", async () => {
  apiMocks.createManualPayment.mockRejectedValue(
    new ProblemDetailsError({
      type: "about:blank",
      title: "无法登记付款",
      status: 409,
      detail: "该营期已经归档，不能再登记付款",
      code: "cohort_archived",
      request_id: "request-1",
    }),
  )
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("button", { name: "登记付款" }))
  await user.type(screen.getByLabelText("学员 ID"), "student-1")
  await user.type(screen.getByLabelText("付款金额（元）"), "100.00")
  await user.type(screen.getByLabelText("备注"), "归档错误测试")
  await user.click(screen.getByRole("button", { name: "确认登记" }))

  expect(await screen.findByText("该营期已经归档，不能再登记付款")).toBeVisible()
  expect(screen.getByRole("dialog", { name: "登记付款" })).toBeVisible()
})

it("distinguishes an import-conflict read failure from an empty list", async () => {
  apiMocks.listPaymentConflicts.mockRejectedValue(new Error("network unavailable"))
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("tab", { name: "导入冲突" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "导入冲突暂时无法读取。",
  )
  expect(screen.queryByText("当前没有需要处理的导入冲突。")).not.toBeInTheDocument()
})

it("distinguishes an interface-account read failure from an empty list", async () => {
  apiMocks.listServiceAccounts.mockRejectedValue(new Error("network unavailable"))
  const user = userEvent.setup()
  renderPage()

  await user.click(screen.getByRole("tab", { name: "接口凭证" }))

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "接口账号暂时无法读取。",
  )
  expect(screen.queryByText("尚未创建付款接口账号。")).not.toBeInTheDocument()
})

it("renders hidden amount projections and semantic mobile labels without guessing values", async () => {
  apiMocks.listPayments.mockResolvedValue({
    items: [
      {
        ...payment,
        amount_cents: null,
        refunded_cents: null,
        available_refund_cents: null,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })
  renderPage(new Set(["payments.read"]))

  expect(await screen.findByText("金额不可见")).toBeVisible()
  expect(screen.getByRole("cell", { name: "金额不可见" })).toHaveAttribute(
    "data-label",
    "付款金额",
  )
  expect(screen.queryByRole("button", { name: "退款 payment-1" })).not.toBeInTheDocument()
})
