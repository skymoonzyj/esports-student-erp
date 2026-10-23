import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import type { PortalPayment, SelfPayments } from "./api"
import { portalKeys } from "./api"
import { MyPaymentsPage } from "./MyPaymentsPage"

const partiallyRefundedPayment: PortalPayment = {
  payment_id: "payment-1",
  source_kind: "manual",
  amount_cents: 100_000,
  currency: "CNY",
  paid_at: "2026-07-01T09:00:00+08:00",
  refunded_cents: 20_000,
  net_cents: 80_000,
  status: "partially_refunded",
  related_student_name: "未成年在营学员",
  refunds: [
    {
      refund_id: "refund-1",
      amount_cents: 20_000,
      refunded_at: "2026-07-12T10:00:00+08:00",
      refund_type: "withdrawal_prorated",
    },
  ],
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function renderPaymentsPage(
  response: (call: number) => Response | Promise<Response>,
) {
  let paymentCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return jsonResponse({
        account_id: "account-payer",
        person_id: "person-payer",
        full_name: "付款账号",
        email: "payer@example.test",
        force_password_change: false,
        permissions: [],
      })
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "payment-page-csrf" })
    }
    if (url.includes("/me/payments?")) {
      paymentCalls += 1
      return response(paymentCalls)
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(
    [
      { path: "/me/payments", element: <MyPaymentsPage /> },
      { path: "/", element: <p>工作台</p> },
    ],
    { initialEntries: ["/me/payments"] },
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { fetchMock, queryClient, router }
}

function paymentPage(payment: PortalPayment): SelfPayments {
  return { items: [payment], page: 1, page_size: 20, total: 1 }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it("shows only read-only self payments and refund totals", async () => {
  const user = userEvent.setup()
  renderPaymentsPage(() => jsonResponse(paymentPage(partiallyRefundedPayment)))

  expect(await screen.findByText("¥1,000.00")).toBeVisible()
  expect(screen.getByText("已退款 ¥200.00")).toBeVisible()
  expect(screen.getByText("净额 ¥800.00")).toBeVisible()
  expect(screen.getByText("部分退款")).toBeVisible()
  expect(
    screen.queryByRole("button", { name: /退款|编辑|匹配|导出/ }),
  ).not.toBeInTheDocument()

  await user.click(screen.getByText("查看退款明细（1）"))
  expect(screen.getByText("按剩余日期退款")).toBeVisible()
  expect(screen.getByText("2026-07-12 10:00")).toBeVisible()
})

it("uses a neutral label after related student access ends", async () => {
  renderPaymentsPage(() =>
    jsonResponse(
      paymentPage({
        ...partiallyRefundedPayment,
        related_student_name: null,
      }),
    ),
  )

  expect(await screen.findByText("历史付款")).toBeVisible()
})

it("loads the next payment page and returns focus to the heading", async () => {
  const user = userEvent.setup()
  let resolveSecondPage!: (response: Response) => void
  const secondPage = new Promise<Response>((resolve) => {
    resolveSecondPage = resolve
  })
  const { fetchMock } = renderPaymentsPage((call) =>
    call === 1
      ? jsonResponse({
          ...paymentPage(partiallyRefundedPayment),
          total: 21,
        })
      : secondPage,
  )
  expect(await screen.findByText("¥1,000.00")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "下一页" }))
  await waitFor(() => {
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).includes("/me/payments?page=2&page_size=20"),
      ),
    ).toBe(true)
  })
  await waitFor(() => {
    expect(screen.getByRole("heading", { name: "本人付款" })).toHaveFocus()
  })
  expect(screen.queryByText("¥1,000.00")).not.toBeInTheDocument()
  expect(screen.getByText("正在更新付款记录")).toBeVisible()

  await act(async () => {
    resolveSecondPage(
      jsonResponse({
        items: [
          {
            ...partiallyRefundedPayment,
            payment_id: "payment-2",
            amount_cents: 50_000,
            refunded_cents: 0,
            net_cents: 50_000,
            status: "paid",
            refunds: [],
          },
        ],
        page: 2,
        page_size: 20,
        total: 21,
      }),
    )
  })
  expect(await screen.findByText("¥500.00")).toBeVisible()
  expect(screen.getByRole("button", { name: "上一页" })).toBeEnabled()
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled()
})

it("purges late portal cache data after a private 404 cancellation settles", async () => {
  let finishCancellation: (() => void) | undefined
  const { queryClient, router } = renderPaymentsPage((call) =>
    call === 1
      ? jsonResponse(paymentPage(partiallyRefundedPayment))
      : jsonResponse(
          {
            type: "about:blank",
            title: "Not Found",
            status: 404,
            detail: "付款资源不存在",
            code: "payment_resource_not_found",
            request_id: "payment-private-404",
          },
          404,
        ),
  )
  expect(await screen.findByText("¥1,000.00")).toBeVisible()
  const otherAccountHousingKey = portalKeys.housing("account-other")
  queryClient.setQueryData(otherAccountHousingKey, {
    subjects: [{ subject_name: "其他账号住宿学员" }],
  })
  queryClient.setQueryData(portalKeys.context("account-payer"), {
    housing_available: true,
  })
  queryClient.setQueryData(
    portalKeys.coach("account-payer", undefined, 1),
    { items: [{ student_name: "旧教练名单学员" }] },
  )
  vi.spyOn(queryClient, "cancelQueries").mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishCancellation = resolve
      }),
  )

  await act(async () => {
    await queryClient.refetchQueries({
      queryKey: portalKeys.payments("account-payer", 1),
      exact: true,
    })
  })

  await waitFor(() => expect(finishCancellation).toBeDefined())
  expect(router.state.location.pathname).toBe("/")
  expect(
    queryClient.getQueryData(portalKeys.payments("account-payer", 1)),
  ).toBeUndefined()
  expect(
    queryClient.getQueryCache().findAll({
      queryKey: portalKeys.root("account-payer"),
    }),
  ).toHaveLength(0)
  expect(queryClient.getQueryData(otherAccountHousingKey)).toEqual({
    subjects: [{ subject_name: "其他账号住宿学员" }],
  })

  queryClient.setQueryData(portalKeys.housing("account-payer"), {
    subjects: [{ subject_name: "延迟回填的旧住宿学员" }],
  })
  await act(async () => {
    finishCancellation?.()
    await Promise.resolve()
  })

  expect(
    queryClient.getQueryData(portalKeys.housing("account-payer")),
  ).toBeUndefined()
  expect(
    queryClient.getQueryCache().findAll({
      queryKey: portalKeys.root("account-payer"),
    }),
  ).toHaveLength(0)
  expect(queryClient.getQueryData(otherAccountHousingKey)).toEqual({
    subjects: [{ subject_name: "其他账号住宿学员" }],
  })
  expect(screen.queryByText("¥1,000.00")).not.toBeInTheDocument()
})
