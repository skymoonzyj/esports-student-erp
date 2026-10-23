import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, RefreshCw } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { PublishedEvaluationPanel } from "../evaluations/PublishedEvaluationPanel"
import { PublishedReportsPanel } from "../evaluations/PublishedReportsPanel"
import { FoodPreferencesPanel } from "../foodPreferences/FoodPreferencesPanel"
import { formatExclusiveEndDate, formatShanghaiDate } from "../teaching/datetime"
import type { LearningEnrollment } from "./api"
import {
  getLearningStudents,
  getLearningTeaching,
  portalKeys,
} from "./api"
import { usePortalAccessError } from "./usePortalAccessError"
import { guardedPortalQueryOptions } from "./usePortalContext"

function enrollmentStatusLabel(status: LearningEnrollment["status"]): string {
  if (status === "active") return "进行中"
  if (status === "completed") return "已结营"
  return "已取消"
}

function EnrollmentDetails({ enrollment }: { enrollment: LearningEnrollment }) {
  return (
    <>
      <div className="portalEnrollmentTitle">
        <div>
          <p>{enrollment.project_name}</p>
          <h3>{enrollment.cohort_name}</h3>
        </div>
        <span className={`portalEnrollmentStatus ${enrollment.status}`}>
          {enrollmentStatusLabel(enrollment.status)}
        </span>
      </div>
      <dl className="portalEnrollmentFacts">
        <div>
          <dt>班级</dt>
          <dd>{enrollment.class_name}</dd>
        </div>
        <div>
          <dt>班型</dt>
          <dd>{enrollment.class_type === "elite" ? "菁英班" : "普通班"}</dd>
        </div>
        {enrollment.professional_team_name ? (
          <div>
            <dt>训练队伍</dt>
            <dd>{enrollment.professional_team_name}</dd>
          </div>
        ) : null}
        <div>
          <dt>时间</dt>
          <dd>
            {formatShanghaiDate(enrollment.starts_at)} 至{" "}
            {formatExclusiveEndDate(enrollment.ends_at)}
          </dd>
        </div>
      </dl>
    </>
  )
}

export function LearningPage() {
  const { user } = useAuth()
  const { studentId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const headingRef = useRef<HTMLHeadingElement>(null)
  const mountedRef = useRef(true)
  const routeStudentIdRef = useRef(studentId)
  const recoveryTargetRef = useRef<string | null>(null)
  const [page, setPage] = useState(1)
  const [recovering, setRecovering] = useState(false)
  const accountId = user?.account_id ?? "anonymous"
  routeStudentIdRef.current = studentId

  const students = useQuery({
    queryKey: portalKeys.students(accountId),
    queryFn: getLearningStudents,
    enabled: Boolean(user?.account_id),
    ...guardedPortalQueryOptions,
  })
  const teachingKey = useMemo(
    () => portalKeys.teaching(accountId, studentId ?? "missing", page),
    [accountId, page, studentId],
  )
  const teaching = useQuery({
    queryKey: teachingKey,
    queryFn: () => getLearningTeaching(studentId!, { page, pageSize: 20 }),
    enabled: Boolean(user?.account_id && studentId),
    ...guardedPortalQueryOptions,
  })
  const maskedStudent =
    teaching.error instanceof ProblemDetailsError &&
    teaching.error.problem.code === "family_student_not_found"
  const portalError = students.error ?? (maskedStudent ? null : teaching.error)
  const accessError = usePortalAccessError(portalError)

  const recoverStudentAccess = useCallback(
    (revokedStudentId: string, clearTeaching: boolean) => {
      if (!user || recoveryTargetRef.current === revokedStudentId) return
      recoveryTargetRef.current = revokedStudentId
      // A food-preference 403/404 does not revoke the learning relationship.
      // Keep the teaching view mounted while the family list is reauthorized;
      // otherwise the panel would unmount and remount with a fresh request,
      // briefly repainting stale food data. Teaching revocation still enters
      // the existing fail-closed recovery state.
      if (clearTeaching) setRecovering(true)
      if (clearTeaching) {
        queryClient.removeQueries({ queryKey: teachingKey, exact: true })
      }
      void queryClient
        .fetchQuery({
          queryKey: portalKeys.students(user.account_id),
          queryFn: getLearningStudents,
          staleTime: 0,
          gcTime: 0,
        })
        .then((refreshed) => {
          if (
            !mountedRef.current ||
            routeStudentIdRef.current !== revokedStudentId
          ) {
            return
          }
          const currentStillAuthorized = refreshed.items.some(
            (student) => student.student_person_id === revokedStudentId,
          )
          const next = refreshed.items[0]
          if (clearTeaching) setRecovering(false)
          if (!clearTeaching && currentStillAuthorized) {
            recoveryTargetRef.current = null
          } else {
            if (!clearTeaching) {
              queryClient.removeQueries({ queryKey: teachingKey, exact: true })
            }
            setPage(1)
            if (next?.student_person_id === revokedStudentId) {
              recoveryTargetRef.current = null
            } else {
              navigate(next ? `/learning/${next.student_person_id}` : "/learning", {
                replace: true,
              })
            }
          }
          requestAnimationFrame(() => headingRef.current?.focus())
        })
        .catch(() => {
          if (
            mountedRef.current &&
            routeStudentIdRef.current === revokedStudentId
          ) {
            recoveryTargetRef.current = null
            setRecovering(false)
          }
        })
    },
    [navigate, queryClient, teachingKey, user],
  )

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  useEffect(() => {
    if (
      studentId ||
      students.isFetching ||
      students.error ||
      !students.data?.items[0]
    ) {
      return
    }
    navigate(`/learning/${students.data.items[0].student_person_id}`, {
      replace: true,
    })
  }, [navigate, studentId, students.data, students.error, students.isFetching])

  useEffect(() => {
    if (!maskedStudent || !user || !studentId) return
    recoverStudentAccess(studentId, true)
  }, [maskedStudent, recoverStudentAccess, studentId, user])

  useEffect(() => {
    if (teaching.isSuccess) recoveryTargetRef.current = null
  }, [studentId, teaching.isSuccess])

  function selectStudent(nextStudentId: string): void {
    setPage(1)
    navigate(`/learning/${nextStudentId}`)
  }

  function retryLearning(): void {
    if (students.error) void students.refetch()
    if (teaching.error) void teaching.refetch()
  }

  const privateDataFetching =
    recovering || students.isFetching || (Boolean(studentId) && teaching.isFetching)
  const currentEnrollment = teaching.data?.student.current_enrollment
  const history = teaching.data?.items.filter(
    (item) => item.enrollment_id !== currentEnrollment?.enrollment_id,
  )
  const totalPages = teaching.data
    ? Math.max(1, Math.ceil(teaching.data.total / teaching.data.page_size))
    : 1

  return (
    <div className="portalPage portalLearningPage">
      {accessError.permissionRevoked ? null : portalError ? (
        <>
          <header className="portalPageHeader">
            <p className="pageEyebrow">LEARNING</p>
            <h1>学习信息</h1>
          </header>
          <section className="portalError" role="alert">
            <div>
              <strong>暂时无法读取学习信息</strong>
              <p>{accessError.message}</p>
            </div>
            <button type="button" onClick={retryLearning}>
              <RefreshCw aria-hidden="true" />
              重试
            </button>
          </section>
        </>
      ) : privateDataFetching || maskedStudent ? (
        <>
          <header className="portalPageHeader">
            <p className="pageEyebrow">LEARNING</p>
            <h1>学习信息</h1>
          </header>
          <section className="portalLoading" aria-live="polite">
            <span className="portalLoadingBar" />
            <span className="portalLoadingBar short" />
            <p>正在更新学习信息</p>
          </section>
        </>
      ) : students.data?.items.length === 0 ? (
        <>
          <header className="portalPageHeader">
            <p className="pageEyebrow">LEARNING</p>
            <h1>学习信息</h1>
          </header>
          <section className="portalEmpty" aria-label="暂无可查看学员">
            <h2>暂无可查看学员</h2>
            <Link className="portalBackLink" to="/">
              <ArrowLeft aria-hidden="true" />
              返回工作台
            </Link>
          </section>
        </>
      ) : teaching.data ? (
        <>
          <header className="portalLearningHeader">
            <div>
              <p className="pageEyebrow">LEARNING</p>
              <h1 ref={headingRef} tabIndex={-1}>{teaching.data.student.student_name}</h1>
              <p>
                {teaching.data.student.student_number} ·{" "}
                {teaching.data.student.age_status === "minor" ? "未成年" : "成年"}
              </p>
            </div>
            {students.data && students.data.items.length > 1 ? (
              <label className="portalStudentSelector">
                <span>当前学员</span>
                <select
                  aria-label="当前学员"
                  value={studentId}
                  onChange={(event) => selectStudent(event.target.value)}
                >
                  {students.data.items.map((student) => (
                    <option
                      key={student.student_person_id}
                      value={student.student_person_id}
                    >
                      {student.student_name}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </header>

          <div className="portalLearningGrid">
            <div className="portalLearningPrimary">
              <section aria-labelledby="current-enrollment-heading">
                <div className="portalSectionHeading">
                  <h2 id="current-enrollment-heading">当前学习</h2>
                </div>
                {currentEnrollment ? (
                  <article className="portalEnrollmentCard current">
                    <EnrollmentDetails enrollment={currentEnrollment} />
                  </article>
                ) : (
                  <div className="portalInlineEmpty">暂无当前或即将开始的营期</div>
                )}
              </section>

              <FoodPreferencesPanel
                mode="profile"
                personId={teaching.data.student.student_person_id}
                onAccessRevoked={() =>
                  recoverStudentAccess(
                    teaching.data.student.student_person_id,
                    false,
                  )
                }
              />

              <section aria-labelledby="learning-history-heading">
                <div className="portalSectionHeading">
                  <h2 id="learning-history-heading">学习记录</h2>
                  <span>{teaching.data.total} 条</span>
                </div>
                <div className="portalEnrollmentHistory">
                  {history?.map((enrollment) => (
                    <article
                      className="portalEnrollmentCard"
                      key={enrollment.enrollment_id}
                    >
                      <EnrollmentDetails enrollment={enrollment} />
                    </article>
                  ))}
                </div>
                {totalPages > 1 ? (
                  <nav className="portalPagination" aria-label="学习记录分页">
                    <button
                      type="button"
                      disabled={page <= 1}
                      onClick={() => setPage((current) => Math.max(1, current - 1))}
                    >
                      上一页
                    </button>
                    <span>第 {page} / {totalPages} 页</span>
                    <button
                      type="button"
                      disabled={page >= totalPages}
                      onClick={() => setPage((current) => current + 1)}
                    >
                      下一页
                    </button>
                  </nav>
                ) : null}
              </section>
            </div>

            <aside className="portalLearningAside">
              <PublishedEvaluationPanel
                studentId={teaching.data.student.student_person_id}
              />
              <PublishedReportsPanel
                studentId={teaching.data.student.student_person_id}
              />
            </aside>
          </div>
        </>
      ) : null}
    </div>
  )
}
