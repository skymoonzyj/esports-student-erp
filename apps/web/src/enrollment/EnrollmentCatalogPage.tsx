import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Check,
  ChevronRight,
  CircleAlert,
  ClipboardCheck,
  Clock3,
  QrCode,
  RefreshCw,
  ReceiptText,
  UserRound,
  X,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { OrderNumber } from "../admissions/OrderNumber"
import { useAuth } from "../auth/AuthProvider"
import {
  createEnrollmentApplication,
  enrollmentKeys,
  getEnrollmentCatalog,
} from "./api"
import type {
  EnrollmentApplication,
  EnrollmentCatalog,
  EnrollmentPaymentStatus,
  EnrollmentProgram,
  EnrollmentProgramCategory,
} from "./api"
import "./enrollment.css"

const categoryLabels: Record<EnrollmentProgramCategory, string> = {
  weekend: "周末课程",
  experience: "体验课程",
  family: "家庭教育",
  summer: "暑期课程",
}

const applicationStatusLabels: Record<EnrollmentApplication["status"], string> = {
  pending_match: "待匹配",
  ready: "待分配班级",
  enrolled: "已完成报名",
  closed: "已关闭",
}

const paymentStatusLabels: Record<EnrollmentPaymentStatus, string> = {
  unpaid: "待付款",
  partial: "部分付款",
  paid: "已付款",
  overpaid: "超额付款",
  refunded: "已退款",
}

function formatCny(cents: number): string {
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(cents / 100)
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(value))
}

function enrollmentError(error: unknown): string {
  if (!(error instanceof ProblemDetailsError)) return "报名申请提交失败，请稍后重试"
  if (error.problem.code === "duplicate_enrollment_application") {
    return "该学员在相同游戏项目和班型下已有待处理申请，请勿重复提交"
  }
  if (error.problem.code === "enrollment_application_forbidden") {
    return "当前账号没有为所选学员报名的权限"
  }
  if (
    error.problem.code === "enrollment_program_not_found" ||
    error.problem.code === "enrollment_project_not_found"
  ) {
    return "所选项目或班型已停止报名，请刷新后重新选择"
  }
  return error.problem.detail || "报名申请提交失败，请稍后重试"
}

function ProgramDetailDialog({
  program,
  onClose,
}: {
  program: EnrollmentProgram
  onClose: () => void
}) {
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose()
    }
    document.addEventListener("keydown", closeOnEscape)
    return () => document.removeEventListener("keydown", closeOnEscape)
  }, [onClose])

  return (
    <div className="enrollmentDialogBackdrop" onMouseDown={onClose}>
      <section
        className="enrollmentDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="enrollment-program-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>{categoryLabels[program.category]}</span>
            <h2 id="enrollment-program-title">{program.name}</h2>
          </div>
          <button type="button" aria-label="关闭详细介绍" title="关闭" onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="enrollmentDialogPrice">
          <strong>{formatCny(program.price_cents)}</strong>
          <span>{program.duration_label} · {program.inclusion_note}</span>
        </div>

        <section>
          <h3>课程介绍</h3>
          <p>{program.summary}</p>
        </section>
        <section>
          <h3>适合学员</h3>
          <p>{program.target_audience}</p>
        </section>
        <section>
          <h3>课程内容</h3>
          <ul>
            {program.highlights.map((highlight) => <li key={highlight}>{highlight}</li>)}
          </ul>
        </section>
        {program.eligibility_note ? (
          <section className="enrollmentEligibility">
            <h3>报名条件</h3>
            <p>{program.eligibility_note}</p>
          </section>
        ) : null}
        {program.schedule.length > 0 ? (
          <section>
            <h3>课程安排</h3>
            <div className="enrollmentScheduleTable" role="table" aria-label={`${program.name}课程安排`}>
              <div className="enrollmentScheduleHeader" role="row">
                <span role="columnheader">时间</span>
                <span role="columnheader">内容</span>
                <span role="columnheader">说明</span>
              </div>
              {program.schedule.map((row, index) => (
                <div className="enrollmentScheduleRow" role="row" key={`${row.time}-${row.item}-${index}`}>
                  <span role="cell" data-label="时间">{row.time}</span>
                  <strong role="cell" data-label="内容">{row.item}</strong>
                  <span role="cell" data-label="说明">{row.note}</span>
                </div>
              ))}
            </div>
          </section>
        ) : null}
      </section>
    </div>
  )
}

function RecentApplications({ applications }: { applications: EnrollmentApplication[] }) {
  if (applications.length === 0) {
    return <div className="enrollmentEmpty">暂无报名申请</div>
  }
  return (
    <div className="enrollmentApplicationTable" role="table" aria-label="最近报名申请">
      <div className="enrollmentApplicationHeader" role="row">
        <span role="columnheader">学员</span>
        <span role="columnheader">报名选择</span>
        <span role="columnheader">费用</span>
        <span role="columnheader">申请状态</span>
        <span role="columnheader">付款状态</span>
        <span role="columnheader">申请日期</span>
      </div>
      {applications.map((application) => (
        <div className="enrollmentApplicationRow" role="row" key={application.id}>
          <span role="cell" data-label="学员">
            <strong>{application.student_name}</strong>
            <OrderNumber orderNumber={application.order_number} />
          </span>
          <span role="cell" data-label="报名选择">
            {application.project_name}
            <small>
              {application.program_name}
              {application.coach_name ? ` · 教练：${application.coach_name}` : ""}
            </small>
          </span>
          <span role="cell" data-label="费用">{formatCny(application.price_cents)}</span>
          <span role="cell" data-label="申请状态">
            <em className={`enrollmentStatus status-${application.status}`}>
              {applicationStatusLabels[application.status]}
            </em>
          </span>
          <span role="cell" data-label="付款状态">
            <em className={`enrollmentStatus payment-${application.payment_status}`}>
              {paymentStatusLabels[application.payment_status]}
            </em>
          </span>
          <span role="cell" data-label="申请日期">{formatDate(application.created_at)}</span>
        </div>
      ))}
    </div>
  )
}

export function EnrollmentCatalogPage() {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const accountId = user?.account_id ?? "anonymous"
  const queryKey = enrollmentKeys.catalog(accountId)
  const catalog = useQuery({
    queryKey,
    queryFn: ({ signal }) => getEnrollmentCatalog(signal),
    enabled: accountId !== "anonymous",
    staleTime: 30_000,
    refetchInterval: (query) => query.state.data?.applications.some(
      (application) => ["unpaid", "partial", "refunded"].includes(application.payment_status),
    ) ? 15_000 : false,
  })
  const [studentId, setStudentId] = useState("")
  const [projectId, setProjectId] = useState("")
  const [coachPersonId, setCoachPersonId] = useState("")
  const [programId, setProgramId] = useState("")
  const [detailProgram, setDetailProgram] = useState<EnrollmentProgram | null>(null)
  const [submitted, setSubmitted] = useState<EnrollmentApplication | null>(null)

  useEffect(() => {
    if (!catalog.data) return
    if (!catalog.data.students.some((student) => student.student_person_id === studentId)) {
      setStudentId(catalog.data.students[0]?.student_person_id ?? "")
    }
    if (!catalog.data.projects.some((project) => project.id === projectId)) {
      setProjectId(catalog.data.projects[0]?.id ?? "")
    }
    const project = catalog.data.projects.find((item) => item.id === projectId)
    if (!project?.coaches.some((coach) => coach.person_id === coachPersonId)) {
      setCoachPersonId("")
    }
    if (!catalog.data.programs.some((program) => program.id === programId)) {
      setProgramId("")
    }
  }, [catalog.data, coachPersonId, programId, projectId, studentId])

  const selectedProject = useMemo(
    () => catalog.data?.projects.find((project) => project.id === projectId) ?? null,
    [catalog.data?.projects, projectId],
  )

  const selectedProgram = useMemo(
    () => catalog.data?.programs.find((program) => program.id === programId) ?? null,
    [catalog.data?.programs, programId],
  )
  const duplicateApplication = catalog.data?.applications.find(
    (application) =>
      application.student_person_id === studentId &&
      application.project_id === projectId &&
      application.program_id === programId &&
      ["pending_match", "ready"].includes(application.status),
  )
  const mutation = useMutation({
    mutationFn: createEnrollmentApplication,
    onSuccess: (application) => {
      queryClient.setQueryData<EnrollmentCatalog>(queryKey, (current) => current
        ? { ...current, applications: [application, ...current.applications] }
        : current)
      setSubmitted(application)
    },
  })

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setSubmitted(null)
    mutation.reset()
    const coachRequired = Boolean(selectedProject?.coaches.length)
    if (
      !studentId
      || !projectId
      || !programId
      || duplicateApplication
      || (coachRequired && !coachPersonId)
    ) return
    mutation.mutate({
      student_person_id: studentId,
      project_id: projectId,
      program_id: programId,
      ...(coachPersonId ? { coach_person_id: coachPersonId } : {}),
      payment_preference: "offline",
    })
  }

  if (catalog.isPending) {
    return <div className="enrollmentPageState" aria-live="polite">正在读取报名项目…</div>
  }
  if (catalog.isError || !catalog.data) {
    return (
      <div className="enrollmentPageState error" role="alert">
        <CircleAlert aria-hidden="true" />
        <p>{enrollmentError(catalog.error)}</p>
        <button type="button" onClick={() => void catalog.refetch()}>重新加载</button>
      </div>
    )
  }

  return (
    <main className="enrollmentPage">
      <header className="enrollmentPageHeader">
        <div>
          <p className="pageEyebrow">ENROLLMENT</p>
          <h1>学员报名</h1>
        </div>
        <div className="enrollmentCatalogCount">
          <ReceiptText aria-hidden="true" />
          <span><strong>{catalog.data.programs.length}</strong> 个可选班型</span>
        </div>
      </header>

      <form onSubmit={submit}>
        <section className="enrollmentSelectionBand" aria-labelledby="enrollment-selection-title">
          <div>
            <h2 id="enrollment-selection-title">报名对象</h2>
            <p>选择学员与游戏项目</p>
          </div>
          <label>
            <span><UserRound aria-hidden="true" />学员</span>
            <select
              aria-label="报名学员"
              value={studentId}
              onChange={(event) => {
                setStudentId(event.target.value)
                setSubmitted(null)
                mutation.reset()
              }}
            >
              {catalog.data.students.map((student) => (
                <option value={student.student_person_id} key={student.student_person_id}>
                  {student.student_name} · {student.student_number}
                  {student.access_source === "self" ? " · 本人" : " · 绑定学员"}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>游戏项目</span>
            <select
              aria-label="游戏项目"
              value={projectId}
              onChange={(event) => {
                setProjectId(event.target.value)
                setCoachPersonId("")
                setSubmitted(null)
                mutation.reset()
              }}
            >
              {catalog.data.projects.map((project) => (
                <option value={project.id} key={project.id}>{project.name}</option>
              ))}
            </select>
          </label>
          {selectedProject?.coaches.length ? (
            <label>
              <span>项目教练</span>
              <select
                aria-label="项目教练"
                value={coachPersonId}
                onChange={(event) => {
                  setCoachPersonId(event.target.value)
                  setSubmitted(null)
                  mutation.reset()
                }}
                required
              >
                <option value="">请选择教练</option>
                {selectedProject.coaches.map((coach) => (
                  <option value={coach.person_id} key={coach.person_id}>
                    {coach.full_name}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <div className="enrollmentCoachPending" aria-label="项目教练">
              <span>项目教练</span>
              <strong>提交后由后台安排</strong>
            </div>
          )}
        </section>

        <section className="enrollmentProgramSection" aria-labelledby="enrollment-programs-title">
          <header>
            <div>
              <h2 id="enrollment-programs-title">选择班型</h2>
              <p>价格以提交申请时显示的金额为准</p>
            </div>
            {selectedProgram ? (
              <span className="enrollmentSelectedPrice">
                已选 {selectedProgram.name} · {formatCny(selectedProgram.price_cents)}
              </span>
            ) : null}
          </header>
          <div className="enrollmentProgramGrid">
            {catalog.data.programs.map((program) => {
              const selected = program.id === programId
              return (
                <article className={selected ? "selected" : ""} key={program.id}>
                  <div className="enrollmentProgramHeading">
                    <span>{categoryLabels[program.category]}</span>
                    {selected ? <Check aria-label="已选择" /> : null}
                  </div>
                  <h3>{program.name}</h3>
                  <div className="enrollmentProgramPrice">
                    <strong>{formatCny(program.price_cents)}</strong>
                    <span>{program.price_note}</span>
                  </div>
                  <p>{program.summary}</p>
                  <dl>
                    <div><dt>周期</dt><dd>{program.duration_label}</dd></div>
                    <div><dt>费用说明</dt><dd>{program.inclusion_note}</dd></div>
                  </dl>
                  <footer>
                    <label>
                      <input
                        type="radio"
                        name="program"
                        value={program.id}
                        checked={selected}
                        onChange={() => {
                          setProgramId(program.id)
                          setSubmitted(null)
                          mutation.reset()
                        }}
                      />
                      选择此班型
                    </label>
                    <button type="button" onClick={() => setDetailProgram(program)}>
                      详细介绍<ChevronRight aria-hidden="true" />
                    </button>
                  </footer>
                </article>
              )
            })}
          </div>
        </section>

        <section className="enrollmentPaymentSection" aria-labelledby="enrollment-payment-title">
          <header>
            <h2 id="enrollment-payment-title">付款方式</h2>
            <p>申请提交后由工作人员确认收款</p>
          </header>
          <div className="enrollmentPaymentOptions">
            <label className="selected">
              <input
                type="radio"
                name="payment"
                value="offline"
                aria-label="线下付款"
                defaultChecked
              />
              <ReceiptText aria-hidden="true" />
              <span><strong>线下付款</strong><small>转账或现场付款，后台确认金额</small></span>
            </label>
            <label className="disabled" aria-disabled="true">
              <input
                type="radio"
                name="payment"
                value="qr"
                aria-label="二维码支付"
                disabled
              />
              <QrCode aria-hidden="true" />
              <span><strong>二维码支付</strong><small>待支付渠道接入后开放</small></span>
              <em>待接入</em>
            </label>
          </div>
        </section>

        <section className="enrollmentSubmitBand" aria-label="提交报名申请">
          <div>
            <span>本次应付</span>
            <strong>{selectedProgram ? formatCny(selectedProgram.price_cents) : "请选择班型"}</strong>
            {selectedProgram ? <small>{selectedProgram.name} · 线下付款</small> : null}
          </div>
          <button
            type="submit"
            disabled={
              !studentId
              || !projectId
              || !programId
              || Boolean(selectedProject?.coaches.length && !coachPersonId)
              || mutation.isPending
              || Boolean(duplicateApplication)
            }
          >
            <ClipboardCheck aria-hidden="true" />
            {mutation.isPending ? "正在提交" : "提交报名申请"}
          </button>
        </section>
        {duplicateApplication ? (
          <div className="enrollmentNotice warning" role="alert">
            该学员的相同项目和班型已有待处理申请，不能重复提交。
          </div>
        ) : null}
        {mutation.isError ? (
          <div className="enrollmentNotice error" role="alert">{enrollmentError(mutation.error)}</div>
        ) : null}
        {submitted ? (
          <div className="enrollmentNotice success" role="status">
            报名申请已提交：{submitted.student_name} · {submitted.project_name} · {submitted.program_name}
              {submitted.coach_name ? ` · 教练：${submitted.coach_name}` : ""}。当前为待付款、待分配班级状态。
              <OrderNumber orderNumber={submitted.order_number} />
          </div>
        ) : null}
      </form>

      <section className="enrollmentHistory" aria-labelledby="enrollment-history-title">
        <header>
          <div>
            <h2 id="enrollment-history-title">最近申请</h2>
            <p>最多显示最近 20 条</p>
          </div>
          <div className="enrollmentHistoryActions">
            <Clock3 aria-hidden="true" />
            <button
              type="button"
              aria-label="刷新付款状态"
              title="刷新付款状态"
              disabled={catalog.isFetching}
              onClick={() => void catalog.refetch()}
            >
              <RefreshCw aria-hidden="true" />
            </button>
          </div>
        </header>
        <RecentApplications applications={catalog.data.applications} />
      </section>

      {detailProgram ? (
        <ProgramDetailDialog program={detailProgram} onClose={() => setDetailProgram(null)} />
      ) : null}
    </main>
  )
}
