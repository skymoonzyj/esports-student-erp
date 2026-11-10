import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { RevenueShareFinancePage } from "./RevenueShareFinancePage"

const mocks = vi.hoisted(() => ({ summary: vi.fn(), settle: vi.fn() }))
vi.mock("./api", async (original) => ({
  ...(await original()),
  getRevenueShareFinanceSummary: mocks.summary,
  createRevenueShareSettlement: mocks.settle,
}))

beforeEach(() => {
  mocks.summary.mockReset().mockResolvedValue({
    earned_cents: 10000, settled_cents: 0, outstanding_cents: 10000, pending_payment_count: 0,
    items: [{ beneficiary_kind: "person", beneficiary_id: "test-payee", beneficiary_name: "张教练", earned_cents: 10000, settled_cents: 0, outstanding_cents: 10000 }],
  })
  mocks.settle.mockReset()
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={queryClient}><RevenueShareFinancePage /></QueryClientProvider>)
}

it("keeps settlement amounts and actions in a keyboard-accessible ledger region", async () => {
  renderPage()
  const ledger = await screen.findByRole("region", { name: "收款对象余额，可横向滚动" })
  expect(ledger).toHaveAttribute("tabindex", "0")
  expect(within(ledger).getAllByRole("cell", { name: "¥100.00" })).toHaveLength(2)
  expect(within(ledger).getByRole("button", { name: "登记打款" })).toBeEnabled()
  expect(mocks.settle).not.toHaveBeenCalled()
})

it("retains the operation key after a lost response and replays the same settlement", async () => {
  mocks.settle.mockRejectedValueOnce(new TypeError("network lost")).mockResolvedValueOnce({ id: "record" })
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole("button", { name: "登记打款" }))
  await user.type(screen.getByLabelText("打款凭证号"), "BANK-0001")
  await user.click(screen.getByRole("button", { name: "确认已打款" }))
  expect(await screen.findByText(/未能确认登记结果/)).toBeVisible()
  const firstInput = mocks.settle.mock.calls[0]?.[0]
  expect(firstInput.idempotency_key).toMatch(/^[0-9a-f-]{36}$/)
  await user.click(screen.getByRole("button", { name: "确认已打款" }))
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "登记结算" })).toBeNull())
  expect(mocks.settle.mock.calls[1]?.[0]).toEqual(firstInput)
})

it("keeps the same operation when uncertain data is edited so the server can reject a mismatch", async () => {
  mocks.settle.mockRejectedValue(new TypeError("network lost"))
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole("button", { name: "登记打款" }))
  await user.type(screen.getByLabelText("打款凭证号"), "BANK-0001")
  await user.click(screen.getByRole("button", { name: "确认已打款" }))
  await screen.findByText(/未能确认登记结果/)
  await user.clear(screen.getByLabelText("本次结算金额（元）"))
  await user.type(screen.getByLabelText("本次结算金额（元）"), "50")
  await user.click(screen.getByRole("button", { name: "确认已打款" }))
  await waitFor(() => expect(mocks.settle).toHaveBeenCalledTimes(2))
  expect(mocks.settle.mock.calls[1]?.[0].idempotency_key).toBe(mocks.settle.mock.calls[0]?.[0].idempotency_key)
  expect(mocks.settle.mock.calls[1]?.[0].amount_cents).toBe(5000)
})

it("disables submitting and closing while a settlement is in flight", async () => {
  mocks.settle.mockReturnValue(new Promise(() => {}))
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole("button", { name: "登记打款" }))
  await user.type(screen.getByLabelText("打款凭证号"), "BANK-0001")
  await user.click(screen.getByRole("button", { name: "确认已打款" }))
  expect(screen.getByRole("button", { name: "正在登记" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "关闭" }))
  expect(screen.getByRole("dialog", { name: "登记结算" })).toBeVisible()
  expect(mocks.settle).toHaveBeenCalledTimes(1)
})
