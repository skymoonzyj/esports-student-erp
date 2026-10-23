import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import type { ReferralCode, RevenueShareSelf } from "./api"
import { MyRevenueSharePage } from "./MyRevenueSharePage"

const mocks = vi.hoisted(() => ({
  accountId: "coach-account",
  permissions: [] as string[],
  getMyRevenueShare: vi.fn(),
  ensureMyReferralCode: vi.fn(),
}))

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal()),
  getMyRevenueShare: mocks.getMyRevenueShare,
  ensureMyReferralCode: mocks.ensureMyReferralCode,
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    status: "authenticated",
    user: {
      account_id: mocks.accountId,
      person_id: "coach-person",
      full_name: "测试教练",
      permissions: [],
      scoped_permissions: mocks.permissions,
    },
  }),
}))

const emptyRevenueShare: RevenueShareSelf = {
  earned_cents: 0,
  settled_cents: 0,
  outstanding_cents: 0,
  items: [],
  referral_codes: [],
  total: 0,
  page_size: 20,
  has_more: false,
  next_cursor: null,
}

const generatedCode: ReferralCode = {
  id: "referral-self-1",
  code: "INV-SELF2026",
  label: "测试教练专属邀请",
  source_kind: "person",
  source_name: "测试教练",
  is_active: true,
  registration_url: "https://erp.example.test/register?invite=INV-SELF2026",
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const rendered = render(
    <QueryClientProvider client={queryClient}>
      <MyRevenueSharePage />
    </QueryClientProvider>,
  )
  return { ...rendered, queryClient }
}

beforeEach(() => {
  mocks.permissions = []
  mocks.accountId = "coach-account"
  mocks.getMyRevenueShare.mockReset().mockResolvedValue(emptyRevenueShare)
  mocks.ensureMyReferralCode.mockReset().mockResolvedValue(generatedCode)
})

it("lets an authorized coach generate and immediately open their invitation QR", async () => {
  mocks.permissions = ["revenue_share.create_self_referral"]
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "生成我的邀请二维码" }))

  await waitFor(() => expect(mocks.ensureMyReferralCode).toHaveBeenCalledTimes(1))
  const dialog = await screen.findByRole("dialog", { name: "邀请注册二维码" })
  expect(dialog).toBeVisible()
  expect(within(dialog).getByText("INV-SELF2026")).toBeVisible()
  expect(within(dialog).getByText(generatedCode.registration_url)).toBeVisible()
})

it("does not expose self-generation to a role without permission", async () => {
  renderPage()

  expect(await screen.findByText("管理员尚未为你生成邀请码。")).toBeVisible()
  expect(screen.queryByRole("button", { name: "生成我的邀请二维码" })).toBeNull()
  expect(mocks.ensureMyReferralCode).not.toHaveBeenCalled()
})

function pageWithEntry(paymentId: string, next: string | null): RevenueShareSelf {
  return {
    ...emptyRevenueShare,
    earned_cents: 10000,
    outstanding_cents: 10000,
    total: 21,
    has_more: Boolean(next),
    next_cursor: next,
    items: [{
      id: paymentId, payment_id: paymentId, entry_kind: "earning", role_kind: "inviter",
      amount_cents: 500, occurred_at: "2026-09-11T08:00:00+08:00",
    }],
  }
}

it("moves forward and back with cursors and resets to the first page when size changes", async () => {
  mocks.getMyRevenueShare.mockImplementation(({ cursor }) => Promise.resolve(
    cursor ? pageWithEntry("second-page-payment", null) : pageWithEntry("first-page-payment", "opaque-next"),
  ))
  const user = userEvent.setup()
  renderPage()
  expect(await screen.findByText("first-page-payment")).toBeVisible()
  expect(screen.getByText("共 21 条，本页 1 条")).toBeVisible()
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByText("second-page-payment")).toBeVisible()
  expect(mocks.getMyRevenueShare).toHaveBeenLastCalledWith({ page_size: 20, cursor: "opaque-next" })
  expect(screen.getByText("第 2 页")).toBeVisible()
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "上一页" }))
  expect(await screen.findByText("first-page-payment")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "下一页" }))
  await screen.findByText("second-page-payment")
  await user.selectOptions(screen.getByLabelText("每页条数"), "50")
  await waitFor(() => expect(mocks.getMyRevenueShare).toHaveBeenLastCalledWith({ page_size: 50, cursor: null }))
  expect(screen.getByText("第 1 页")).toBeVisible()
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled()
})

it("keeps the current cursor after a failed page fetch and retries that page", async () => {
  mocks.getMyRevenueShare
    .mockResolvedValueOnce(pageWithEntry("first-page-payment", "opaque-next"))
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(pageWithEntry("second-page-payment", null))
  const user = userEvent.setup()
  renderPage()
  await screen.findByText("first-page-payment")
  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("分成记录暂时无法读取")
  expect(screen.getByText("第 2 页")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "重试" }))
  expect(await screen.findByText("second-page-payment")).toBeVisible()
  expect(mocks.getMyRevenueShare).toHaveBeenLastCalledWith({ page_size: 20, cursor: "opaque-next" })
})

it("updates referral codes across cached pages without losing the current page", async () => {
  mocks.permissions = ["revenue_share.create_self_referral"]
  let generated = false
  mocks.getMyRevenueShare.mockImplementation(({ cursor }) => Promise.resolve({
    ...pageWithEntry(cursor ? "second-page-payment" : "first-page-payment", cursor ? null : "opaque-next"),
    referral_codes: generated ? [generatedCode] : [],
  }))
  mocks.ensureMyReferralCode.mockImplementation(() => {
    generated = true
    return Promise.resolve(generatedCode)
  })
  const user = userEvent.setup()
  const { queryClient } = renderPage()
  await screen.findByText("first-page-payment")
  await user.click(screen.getByRole("button", { name: "下一页" }))
  await screen.findByText("second-page-payment")
  await user.click(screen.getByRole("button", { name: "生成我的邀请二维码" }))
  await screen.findByRole("dialog", { name: "邀请注册二维码" })
  expect(screen.getByText("第 2 页")).toBeVisible()
  expect(screen.getByText("second-page-payment")).toBeVisible()
  for (const cursor of [null, "opaque-next"]) {
    expect(queryClient.getQueryData<RevenueShareSelf>(["revenue-share", "me", "coach-account", 20, cursor])?.referral_codes).toEqual([generatedCode])
  }
})

it("isolates cached balances and resets pagination when the authenticated account changes", async () => {
  mocks.getMyRevenueShare.mockImplementation(() => Promise.resolve(pageWithEntry(mocks.accountId, "opaque-next")))
  const user = userEvent.setup()
  const { rerender, queryClient } = renderPage()
  await screen.findByText("coach-account")
  await user.click(screen.getByRole("button", { name: "下一页" }))
  await screen.findByText("第 2 页")
  mocks.accountId = "other-account"
  rerender(<QueryClientProvider client={queryClient}><MyRevenueSharePage /></QueryClientProvider>)
  expect(await screen.findByText("other-account")).toBeVisible()
  expect(screen.queryByText("coach-account")).toBeNull()
  expect(screen.getByText("第 1 页")).toBeVisible()
  expect(queryClient.getQueryData(["revenue-share", "me", "other-account", 20, null])).toBeDefined()
})
