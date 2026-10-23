import { useQuery } from "@tanstack/react-query"
import { ArrowRight } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router-dom"

import { TeachingSectionNav } from "../teaching/TeachingSectionNav"
import { listEnrollments } from "./api"
import type { Enrollment, EnrollmentStatus } from "./api"
import { formatCny } from "./money"
import { OrderNumber } from "./OrderNumber"
import { formatExclusiveEndDate, formatShanghaiDate } from "../teaching/datetime"
import "../teaching/teaching.css"
import "./admissions.css"

const PAGE_SIZE = 20

type EnrollmentStatusFilter = EnrollmentStatus | "all"

const ENROLLMENT_STATUS_LABELS: Record<EnrollmentStatus, string> = {
  active: "在营",
  cancelled: "已取消",
  completed: "已结营",
}

const PAYMENT_STATUS_LABELS = {
  unpaid: "未付款",
  partial: "部分付款",
  paid: "已付清",
  overpaid: "有多付款",
  refunded: "已退款",
} as const

function formatDateTime(value: string): string {
  return formatShanghaiDate(value)
}

function EnrollmentAmounts({ enrollment }: { enrollment: Enrollment }) {
  if (
    enrollment.agreed_tuition_cents === null ||
    enrollment.net_paid_cents === null ||
    enrollment.due_cents === null ||
    enrollment.overpaid_cents === null
  ) {
    return <span className="enrollmentAmountHidden">金额不可见</span>
  }

  return (
    <span className="enrollmentAmountSummary">
      <strong>约定学费 {formatCny(enrollment.agreed_tuition_cents)}</strong>
      <small>净付款 {formatCny(enrollment.net_paid_cents)}</small>
      {enrollment.due_cents > 0 ? (
        <em className="dangerText">欠款 {formatCny(enrollment.due_cents)}</em>
      ) : enrollment.overpaid_cents > 0 ? (
        <em>多付 {formatCny(enrollment.overpaid_cents)}</em>
      ) : (
        <em>已结清</em>
      )}
    </span>
  )
}

function EnrollmentPagination({
  page,
  pageSize,
  total,
  onPageChange,
}: {
  page: number
  pageSize: number
  total: number
  onPageChange: (page: number) => void
}) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  return (
    <nav className="teachingPagination" aria-label="正式报名分页">
      <span>第 {page} / {pageCount} 页，共 {total} 条</span>
      <div>
        <button
          type="button"
          className="teachingSecondaryButton"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </button>
        <button
          type="button"
          className="teachingSecondaryButton"
          disabled={page >= pageCount}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </nav>
  )
}

export function EnrollmentsPage({
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const [status, setStatus] = useState<EnrollmentStatusFilter>("active")
  const [page, setPage] = useState(1)
  const enrollments = useQuery({
    queryKey: ["enrollments", { status, page, pageSize: PAGE_SIZE }],
    queryFn: () =>
      listEnrollments({
        status: status === "all" ? undefined : status,
        page,
        pageSize: PAGE_SIZE,
      }),
  })

  return (
    <div className="paymentsPage enrollmentsPage">
      <TeachingSectionNav
        permissions={permissions}
        scopedPermissions={scopedPermissions}
      />
      <header className="teachingPageHeader admissionsPageHeader">
        <div>
          <p className="teachingEyebrow">招生教学</p>
          <h1>正式报名</h1>
        </div>
      </header>

      <section className="admissionCaseFilters" aria-label="正式报名筛选">
        <label>
          <span>报名状态</span>
          <select
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as EnrollmentStatusFilter)
              setPage(1)
            }}
          >
            <option value="active">在营</option>
            <option value="cancelled">已取消</option>
            <option value="completed">已结营</option>
            <option value="all">全部状态</option>
          </select>
        </label>
      </section>

      {enrollments.isLoading ? (
        <div className="admissionsEmptyState">正在读取正式报名名单…</div>
      ) : enrollments.isError ? (
        <div className="admissionsEmptyState" role="alert">
          正式报名名单暂时无法读取。
        </div>
      ) : enrollments.data ? (
        <>
          {enrollments.data.items.length ? (
            <div
              className="admissionsDataTable enrollmentsTable"
              role="table"
              aria-label="正式报名名单"
            >
              <div className="admissionsTableHeader" role="row">
                <span role="columnheader">学员</span>
                <span role="columnheader">营期与班级</span>
                <span role="columnheader">报名状态</span>
                <span role="columnheader">付款状态</span>
                <span role="columnheader">学费与付款</span>
                <span role="columnheader">操作</span>
              </div>
              {enrollments.data.items.map((item) => (
                <div className="admissionsTableRow" role="row" key={item.id}>
                  <span role="cell" data-label="学员" className="enrollmentIdentity">
                    <strong>{item.student_name}</strong>
                    <OrderNumber orderNumber={item.order_number} />
                    <small>{formatDateTime(item.confirmed_at)}确认</small>
                  </span>
                  <span role="cell" data-label="营期与班级" className="enrollmentCohortCell">
                    <strong>{item.cohort_name} · {item.class_name}</strong>
                    <small>
                      {item.schedule_mode === "custom" ? "单独参加时间" : "跟随营期"}
                    </small>
                    {item.schedule_mode === "custom" &&
                    item.effective_starts_at &&
                    item.effective_ends_at ? (
                      <small>
                        {formatDateTime(item.effective_starts_at)} 至 {formatExclusiveEndDate(item.effective_ends_at)}
                      </small>
                    ) : null}
                  </span>
                  <span role="cell" data-label="报名状态">
                    <span className={`admissionsStatus status-${item.status}`}>
                      {ENROLLMENT_STATUS_LABELS[item.status]}
                    </span>
                  </span>
                  <span role="cell" data-label="付款状态">
                    {PAYMENT_STATUS_LABELS[item.payment_status]}
                  </span>
                  <span role="cell" data-label="学费与付款">
                    <EnrollmentAmounts enrollment={item} />
                  </span>
                  <span role="cell" data-label="操作" className="actionCell">
                    <Link
                      className="admissionsRowButton"
                      to={`/teaching/enrollments/${item.id}`}
                    >
                      查看详情 <ArrowRight aria-hidden="true" />
                    </Link>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="admissionsEmptyState">当前状态下没有正式报名记录。</div>
          )}
          {enrollments.data.total > 0 ? (
            <EnrollmentPagination
              page={enrollments.data.page}
              pageSize={enrollments.data.page_size}
              total={enrollments.data.total}
              onPageChange={setPage}
            />
          ) : null}
        </>
      ) : null}
    </div>
  )
}
