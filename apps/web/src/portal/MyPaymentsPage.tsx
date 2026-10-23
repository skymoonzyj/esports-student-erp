import { useQuery } from "@tanstack/react-query"
import { ChevronDown, ReceiptText, RefreshCw } from "lucide-react"
import { useRef, useState } from "react"

import { formatCny } from "../admissions/money"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDateTime } from "../teaching/datetime"
import type { PortalPayment, PortalRefund } from "./api"
import { getSelfPayments, portalKeys } from "./api"
import { usePortalAccessError } from "./usePortalAccessError"
import { guardedPortalQueryOptions } from "./usePortalContext"

const PAYMENT_LABELS: Record<PortalPayment["status"], string> = {
  paid: "已付款",
  partially_refunded: "部分退款",
  refunded: "已全部退款",
}

const REFUND_LABELS: Record<PortalRefund["refund_type"], string> = {
  manual: "人工退款",
  withdrawal_prorated: "按剩余日期退款",
  withdrawal_full: "全额退营退款",
}

export function MyPaymentsPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const [page, setPage] = useState(1)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const payments = useQuery({
    queryKey: portalKeys.payments(accountId, page),
    queryFn: () => getSelfPayments({ page, pageSize: 20 }),
    enabled: Boolean(user?.account_id),
    ...guardedPortalQueryOptions,
  })
  const accessError = usePortalAccessError(payments.error)
  const totalPages = payments.data
    ? Math.max(1, Math.ceil(payments.data.total / payments.data.page_size))
    : 1

  function changePage(nextPage: number): void {
    setPage(nextPage)
    requestAnimationFrame(() => headingRef.current?.focus())
  }

  return (
    <div className="portalPage portalPaymentsPage">
      <header className="portalPaymentsHeader">
        <div>
          <p className="pageEyebrow">PAYMENTS</p>
          <h1 ref={headingRef} tabIndex={-1}>本人付款</h1>
          <p>当前付款账号的付款与退款记录</p>
        </div>
        {payments.data && !payments.isFetching ? (
          <span className="portalPaymentCount">
            <ReceiptText aria-hidden="true" />
            {payments.data.total} 笔记录
          </span>
        ) : null}
      </header>

      {accessError.permissionRevoked ? null : payments.isFetching ? (
        <section className="portalLoading" aria-live="polite">
          <span className="portalLoadingBar" />
          <span className="portalLoadingBar short" />
          <p>正在更新付款记录</p>
        </section>
      ) : payments.error ? (
        <section className="portalError" role="alert">
          <div>
            <strong>付款记录暂时无法读取</strong>
            <p>{accessError.message}</p>
          </div>
          <button type="button" onClick={() => void payments.refetch()}>
            <RefreshCw aria-hidden="true" />
            重试
          </button>
        </section>
      ) : payments.data?.items.length === 0 ? (
        <section className="portalEmpty" aria-label="暂无付款记录">
          <h2>暂无付款记录</h2>
          <p>当前账号尚无可查看的付款或退款。</p>
        </section>
      ) : payments.data ? (
        <>
          <section className="portalPaymentGrid" aria-label="本人付款记录">
            {payments.data.items.map((payment) => (
              <article className="portalPaymentCard" key={payment.payment_id}>
                <header>
                  <div>
                    <p>{payment.related_student_name ?? "历史付款"}</p>
                    <h2>{formatCny(payment.amount_cents)}</h2>
                  </div>
                  <span className={`portalPaymentStatus ${payment.status}`}>
                    {PAYMENT_LABELS[payment.status]}
                  </span>
                </header>

                <p className="portalPaymentTime">
                  付款时间 {formatShanghaiDateTime(payment.paid_at)}
                </p>
                <div className="portalPaymentAmounts">
                  <span>已退款 {formatCny(payment.refunded_cents)}</span>
                  <strong>净额 {formatCny(payment.net_cents)}</strong>
                </div>

                {payment.refunds.length > 0 ? (
                  <details className="portalRefundDetails">
                    <summary>
                      <span>查看退款明细（{payment.refunds.length}）</span>
                      <ChevronDown aria-hidden="true" />
                    </summary>
                    <div className="portalRefundList">
                      {payment.refunds.map((refund) => (
                        <div className="portalRefundRow" key={refund.refund_id}>
                          <div>
                            <strong>{REFUND_LABELS[refund.refund_type]}</strong>
                            <span>{formatShanghaiDateTime(refund.refunded_at)}</span>
                          </div>
                          <span>{formatCny(refund.amount_cents)}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}
              </article>
            ))}
          </section>

          {totalPages > 1 ? (
            <nav className="portalPagination" aria-label="付款记录分页">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => changePage(Math.max(1, page - 1))}
              >
                上一页
              </button>
              <span>第 {page} / {totalPages} 页</span>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => changePage(page + 1)}
              >
                下一页
              </button>
            </nav>
          ) : null}
        </>
      ) : null}
    </div>
  )
}
