import { useQuery } from "@tanstack/react-query"
import { ArrowRight } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router-dom"

import { listEnrollments } from "./api"
import type { Enrollment, EnrollmentStatus } from "./api"
import { formatCny } from "./money"
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

function CohortStudentBalance({ enrollment }: { enrollment: Enrollment }) {
  if (
    enrollment.net_paid_cents === null ||
    enrollment.due_cents === null ||
    enrollment.overpaid_cents === null
  ) {
    return <span className="enrollmentAmountHidden">金额不可见</span>
  }

  return (
    <span className="cohortStudentBalance">
      <strong>净付款 {formatCny(enrollment.net_paid_cents)}</strong>
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

function CohortStudentsPagination({
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
    <nav className="teachingPagination" aria-label="营期学员分页">
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

export function CohortStudentsPanel({ cohortId }: { cohortId: string }) {
  const [status, setStatus] = useState<EnrollmentStatusFilter>("active")
  const [page, setPage] = useState(1)
  const enrollments = useQuery({
    queryKey: [
      "cohort-students",
      cohortId,
      { status, page, pageSize: PAGE_SIZE },
    ],
    queryFn: () =>
      listEnrollments({
        cohortId,
        status: status === "all" ? undefined : status,
        page,
        pageSize: PAGE_SIZE,
      }),
  })

  return (
    <section className="cohortTabSurface cohortStudentsPanel" aria-label="营期学员名单">
      <header>
        <div>
          <h2>学员名单</h2>
          <span>{enrollments.data?.total ?? 0} 条可见报名</span>
        </div>
        <label className="cohortStudentStatusFilter">
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
      </header>

      {enrollments.isLoading ? (
        <div className="teachingEmptyState">正在读取营期学员…</div>
      ) : enrollments.isError ? (
        <div className="teachingEmptyState" role="alert">
          营期学员名单暂时无法读取。
        </div>
      ) : enrollments.data ? (
        <>
          {enrollments.data.items.length ? (
            <div
              className="admissionsDataTable cohortStudentsTable"
              role="table"
              aria-label="营期学员名单"
            >
              <div className="admissionsTableHeader" role="row">
                <span role="columnheader">学员</span>
                <span role="columnheader">班级</span>
                <span role="columnheader">报名状态</span>
                <span role="columnheader">付款状态</span>
                <span role="columnheader">结算</span>
                <span role="columnheader">操作</span>
              </div>
              {enrollments.data.items.map((item) => (
                <div className="admissionsTableRow" role="row" key={item.id}>
                  <span role="cell" data-label="学员" className="enrollmentIdentity">
                    <strong>{item.student_name}</strong>
                  </span>
                  <span role="cell" data-label="班级" className="enrollmentCohortCell">
                    <strong>{item.class_name}</strong>
                  </span>
                  <span role="cell" data-label="报名状态">
                    <span className={`admissionsStatus status-${item.status}`}>
                      {ENROLLMENT_STATUS_LABELS[item.status]}
                    </span>
                  </span>
                  <span role="cell" data-label="付款状态">
                    {PAYMENT_STATUS_LABELS[item.payment_status]}
                  </span>
                  <span role="cell" data-label="结算">
                    <CohortStudentBalance enrollment={item} />
                  </span>
                  <span role="cell" data-label="操作" className="actionCell">
                    <Link
                      className="admissionsRowButton"
                      to={`/teaching/enrollments/${item.id}`}
                    >
                      查看报名 <ArrowRight aria-hidden="true" />
                    </Link>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <div className="teachingEmptyState">当前状态下没有可见学员。</div>
          )}
          {enrollments.data.total > 0 ? (
            <CohortStudentsPagination
              page={enrollments.data.page}
              pageSize={enrollments.data.page_size}
              total={enrollments.data.total}
              onPageChange={setPage}
            />
          ) : null}
        </>
      ) : null}
    </section>
  )
}
