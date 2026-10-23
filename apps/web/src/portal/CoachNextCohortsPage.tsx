import { useQuery, useQueryClient } from "@tanstack/react-query"
import { RefreshCw, UsersRound } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDateTime } from "../teaching/datetime"
import { getCoachNextCohorts, portalKeys } from "./api"
import { usePortalAccessError } from "./usePortalAccessError"
import { guardedPortalQueryOptions } from "./usePortalContext"

function initialPage(searchParams: URLSearchParams): number {
  const value = Number(searchParams.get("page") ?? "1")
  return Number.isInteger(value) && value > 0 ? value : 1
}

export function CoachNextCohortsPage() {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const [searchParams, setSearchParams] = useSearchParams()
  const [anchorCohortId, setAnchorCohortId] = useState<string | undefined>(
    () => searchParams.get("anchor_cohort_id") ?? undefined,
  )
  const [page, setPage] = useState(() => initialPage(searchParams))
  const headingRef = useRef<HTMLHeadingElement>(null)
  const queryClient = useQueryClient()
  const queryKey = portalKeys.coach(accountId, anchorCohortId, page)
  const roster = useQuery({
    queryKey,
    queryFn: () =>
      getCoachNextCohorts({ anchorCohortId, page, pageSize: 20 }),
    enabled: Boolean(user?.account_id),
    ...guardedPortalQueryOptions,
  })
  const revokedAnchor =
    Boolean(anchorCohortId) &&
    roster.error instanceof ProblemDetailsError &&
    roster.error.problem.code === "coach_cohort_not_found"
  const accessError = usePortalAccessError(revokedAnchor ? null : roster.error)

  useEffect(() => {
    const next = new URLSearchParams()
    if (anchorCohortId) next.set("anchor_cohort_id", anchorCohortId)
    if (page > 1) next.set("page", String(page))
    if (next.toString() !== searchParams.toString()) {
      setSearchParams(next, { replace: true })
    }
  }, [anchorCohortId, page, searchParams, setSearchParams])

  useEffect(() => {
    if (!revokedAnchor) return
    queryClient.removeQueries({ queryKey, exact: true })
    setPage(1)
    setAnchorCohortId(undefined)
    queueMicrotask(() => headingRef.current?.focus())
  }, [queryClient, queryKey, revokedAnchor])

  function selectStart(anchorId: string): void {
    setPage(1)
    setAnchorCohortId(anchorId)
  }

  const totalPages = roster.data
    ? Math.max(1, Math.ceil(roster.data.total / roster.data.page_size))
    : 1

  return (
    <div className="portalPage portalRosterPage">
      <header className="portalRosterPageHeader">
        <div>
          <p className="pageEyebrow">COACH</p>
          <h1 ref={headingRef} tabIndex={-1}>下一期学员</h1>
          <p>按开营时间查看本人负责班级</p>
        </div>
        {roster.data && !roster.isFetching ? (
          <span className="portalRosterTotal">
            <UsersRound aria-hidden="true" />
            {roster.data.total} 名学员
          </span>
        ) : null}
      </header>

      {accessError.permissionRevoked ? null : revokedAnchor || roster.isFetching ? (
        <section className="portalLoading" aria-live="polite">
          <span className="portalLoadingBar" />
          <span className="portalLoadingBar short" />
          <p>正在更新下一期名单</p>
        </section>
      ) : roster.error ? (
        <section className="portalError" role="alert">
          <div>
            <strong>暂时无法读取下一期名单</strong>
            <p>{accessError.message}</p>
          </div>
          <button type="button" onClick={() => void roster.refetch()}>
            <RefreshCw aria-hidden="true" />
            重试
          </button>
        </section>
      ) : roster.data?.options.length === 0 ? (
        <section className="portalEmpty" aria-label="暂无下一期安排">
          <h2>暂未安排下一期</h2>
          <p>当前没有已授权的未来班级。</p>
        </section>
      ) : roster.data ? (
        <>
          <section className="portalStartSection" aria-label="开营时间">
            <h2>开营时间</h2>
            <div className="portalStartOptions">
              {roster.data.options.map((option) => (
                <button
                  key={option.anchor_cohort_id}
                  type="button"
                  aria-label={formatShanghaiDateTime(option.starts_at)}
                  aria-pressed={roster.data?.selected_starts_at === option.starts_at}
                  onClick={() => selectStart(option.anchor_cohort_id)}
                >
                  <strong>{formatShanghaiDateTime(option.starts_at)}</strong>
                  <span>{option.class_count} 个班级</span>
                </button>
              ))}
            </div>
          </section>

          <div className="portalClassList">
            {roster.data.classes.map((classItem) => {
              const students = roster.data.items.filter(
                (student) => student.class_id === classItem.class_id,
              )
              return (
                <section className="portalClassSection" key={classItem.class_id}>
                  <header className="portalClassHeader">
                    <div>
                      <p>{classItem.project_name} · {classItem.cohort_name}</p>
                      <h2>{classItem.class_name}</h2>
                    </div>
                    <div className="portalClassMeta">
                      <span>{classItem.class_type === "elite" ? "菁英班" : "普通班"}</span>
                      {classItem.professional_team_name ? (
                        <span>{classItem.professional_team_name}</span>
                      ) : null}
                      <span>{classItem.assignment_type === "primary" ? "主教练" : "助理教练"}</span>
                      <strong>{classItem.student_count} 人</strong>
                    </div>
                  </header>

                  <div className="portalRosterHeader" aria-hidden="true">
                    <span>学员</span>
                    <span>班级</span>
                    <span>年龄</span>
                    <span>状态</span>
                  </div>
                  <div className="portalRosterRows">
                    {students.map((student) => (
                      <article className="portalRosterRow" key={student.enrollment_id}>
                        <div>
                          <strong>{student.student_name}</strong>
                          <span>{student.student_number}</span>
                        </div>
                        <span>{student.class_name}</span>
                        <span>{student.age_status === "minor" ? "未成年" : "成年"}</span>
                        <span className="portalRosterStatus">已报名</span>
                      </article>
                    ))}
                  </div>
                </section>
              )
            })}
          </div>

          {totalPages > 1 ? (
            <nav className="portalPagination" aria-label="名单分页">
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
        </>
      ) : null}
    </div>
  )
}
