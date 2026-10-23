import { useQuery, useQueryClient } from "@tanstack/react-query"
import { RefreshCw } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react"
import { useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { useAuth } from "../auth/AuthProvider"
import { formatShanghaiDate } from "../teaching/datetime"
import { useEvaluationAuthorization } from "./EvaluationAuthorization"
import { ReportVersionTimeline } from "./ReportVersionTimeline"
import { TeachingReportEditorDialog } from "./TeachingReportEditorDialog"
import {
  listTeachingReportSlots,
  listTeachingReportTargets,
  type TeachingReportSlot,
  type TeachingReportTargetPage,
} from "./reportApi"
import {
  reportKeys,
  type ReportTargetFilters,
  type TeachingReportKind,
  type TeachingReportStatus,
} from "./reportKeys"

const statusLabels: Record<TeachingReportStatus, string> = {
  draft: "草稿",
  pending_review: "待审核",
  rejected: "已驳回",
  correction_draft: "整改草稿",
  published: "已发布",
  revision_pending_review: "修订待审",
}

const reportStatuses = Object.entries(statusLabels) as Array<
  [TeachingReportStatus, string]
>

function kindLabel(kind: TeachingReportKind): string {
  return kind === "daily" ? "日报" : "周报"
}

function slotWindowLabel(slot: TeachingReportSlot): string {
  const deadline = formatShanghaiDate(slot.initial_submission_deadline)
  if (slot.allowed_actions.includes("submit")) {
    return `当前可提交 · 截止 ${deadline}`
  }
  if (slot.status === "pending_review" || slot.status === "revision_pending_review") {
    return "已提交，等待审核"
  }
  if (slot.status === "published") {
    return "已发布；如有修订权限可重新打开"
  }
  return `开放 ${formatShanghaiDate(slot.submission_opens_at)} · 截止 ${deadline}`
}

function slotKey(slot: TeachingReportSlot): string {
  return [
    slot.enrollment_id,
    slot.report_kind,
    slot.period_start_date,
  ].join(":")
}

function isPrivateTargetError(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    error.problem.status === 404 &&
    error.problem.code === "evaluation_resource_not_found"
  )
}

function isReportAccessRevoked(error: unknown): error is ProblemDetailsError {
  return error instanceof ProblemDetailsError && error.problem.status === 403
}

export function TeachingReportsPage() {
  const { user } = useAuth()
  const { pending, revision } = useEvaluationAuthorization()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const handledAccessRevocation = useRef(false)
  const accountId = user?.account_id ?? "anonymous"
  const [targetFilters, setTargetFilters] = useState<ReportTargetFilters>({
    cohortId: null,
    projectId: null,
    classId: null,
    studentId: null,
    query: null,
    page: 1,
    pageSize: 20,
  })
  const [searchText, setSearchText] = useState("")
  const [selectedEnrollmentId, setSelectedEnrollmentId] = useState<string | null>(
    null,
  )
  const [kind, setKind] = useState<TeachingReportKind | "all">("all")
  const [status, setStatus] = useState<TeachingReportStatus | "all">("all")
  const [editorSlot, setEditorSlot] = useState<TeachingReportSlot | null>(null)
  const [timelineReportId, setTimelineReportId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const targets = useQuery({
    queryKey: [
      ...reportKeys.targets(accountId, targetFilters),
      { authorizationRevision: revision },
    ],
    queryFn: ({ signal }) => listTeachingReportTargets(targetFilters, signal),
    enabled: Boolean(user?.account_id && !pending),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const slots = useQuery({
    queryKey: [
      ...reportKeys.slots(accountId, selectedEnrollmentId ?? "none"),
      { authorizationRevision: revision },
    ],
    queryFn: ({ signal }) =>
      listTeachingReportSlots(selectedEnrollmentId ?? "", signal),
    enabled: Boolean(user?.account_id && selectedEnrollmentId && !pending),
    staleTime: 0,
    gcTime: 0,
    retry: false,
  })
  const privateTargetMissing = isPrivateTargetError(slots.error)
  const reportAccessRevoked = [targets.error, slots.error].some(
    isReportAccessRevoked,
  )

  useEffect(() => {
    if (!reportAccessRevoked) {
      handledAccessRevocation.current = false
      return
    }
    if (!user?.account_id || handledAccessRevocation.current) return
    handledAccessRevocation.current = true
    setSelectedEnrollmentId(null)
    setEditorSlot(null)
    setTimelineReportId(null)
    setNotice(null)
    const reportRoot = reportKeys.root(accountId)
    const cancellation = queryClient.cancelQueries({ queryKey: reportRoot })
    const removePrivateCache = () => queryClient.removeQueries({ queryKey: reportRoot })
    removePrivateCache()
    navigate("/evaluations", { replace: true })
    void cancellation.finally(removePrivateCache)
  }, [
    accountId,
    navigate,
    queryClient,
    reportAccessRevoked,
    user?.account_id,
  ])

  useEffect(() => {
    setSelectedEnrollmentId(null)
    setEditorSlot(null)
    setTimelineReportId(null)
    setNotice(null)
  }, [accountId])

  const recoverPrivateTarget = useCallback(
    async (reportId?: string): Promise<void> => {
      if (!selectedEnrollmentId) return
      const targetsKey = [...reportKeys.root(accountId), "targets"] as const
      const slotsKey = reportKeys.slots(accountId, selectedEnrollmentId)
      void queryClient.cancelQueries({ queryKey: targetsKey })
      void queryClient.cancelQueries({ queryKey: slotsKey })
      queryClient.setQueriesData<TeachingReportTargetPage>(
        { queryKey: targetsKey },
        (page) => {
          if (!page) return page
          const items = page.items.filter(
            (item) => item.enrollment_id !== selectedEnrollmentId,
          )
          const removed = page.items.length - items.length
          return removed === 0
            ? page
            : { ...page, items, total: Math.max(0, page.total - removed) }
        },
      )
      queryClient.removeQueries({ queryKey: slotsKey })
      if (reportId) {
        const detailKey = reportKeys.detail(accountId, reportId)
        const versionsKey = [
          ...reportKeys.root(accountId),
          "versions",
          { reportId },
        ] as const
        void queryClient.cancelQueries({ queryKey: detailKey })
        void queryClient.cancelQueries({ queryKey: versionsKey })
        queryClient.removeQueries({ queryKey: detailKey })
        queryClient.removeQueries({ queryKey: versionsKey })
      }
      setSelectedEnrollmentId(null)
      setEditorSlot(null)
      setTimelineReportId(null)
      setNotice("该学员已不可访问，名单已刷新。")
      await queryClient.invalidateQueries({
        queryKey: targetsKey,
      })
    },
    [accountId, queryClient, selectedEnrollmentId],
  )

  useEffect(() => {
    if (!selectedEnrollmentId || !privateTargetMissing) return
    void recoverPrivateTarget()
  }, [
    privateTargetMissing,
    recoverPrivateTarget,
    selectedEnrollmentId,
  ])

  useEffect(() => {
    if (!editorSlot || !slots.data) return
    const refreshed = slots.data.items.find(
      (item) => slotKey(item) === slotKey(editorSlot),
    )
    if (refreshed) {
      if (refreshed !== editorSlot) setEditorSlot(refreshed)
      return
    }
    if (editorSlot.allowed_actions.length > 0) {
      setEditorSlot({ ...editorSlot, allowed_actions: [] })
    }
  }, [editorSlot, slots.data])

  const facets = targets.data?.facets
  const projectOptions = useMemo(() => {
    const projects = facets?.projects ?? []
    return targetFilters.cohortId
      ? projects.filter((project) =>
          project.cohort_ids.includes(targetFilters.cohortId ?? ""),
        )
      : projects
  }, [facets?.projects, targetFilters.cohortId])
  const classOptions = useMemo(() => {
    return (facets?.classes ?? []).filter(
      (item) =>
        (!targetFilters.cohortId || item.cohort_id === targetFilters.cohortId) &&
        (!targetFilters.projectId || item.project_id === targetFilters.projectId),
    )
  }, [facets?.classes, targetFilters.cohortId, targetFilters.projectId])

  function resetSelection(): void {
    setSelectedEnrollmentId(null)
    setEditorSlot(null)
    setTimelineReportId(null)
    setNotice(null)
  }

  function updateDirectoryFilter(
    key: "cohortId" | "projectId" | "classId",
    value: string,
  ): void {
    setTargetFilters((current) => {
      if (key === "cohortId") {
        return {
          ...current,
          cohortId: value || null,
          projectId: null,
          classId: null,
          page: 1,
        }
      }
      if (key === "projectId") {
        return {
          ...current,
          projectId: value || null,
          classId: null,
          page: 1,
        }
      }
      return { ...current, classId: value || null, page: 1 }
    })
    resetSelection()
  }

  function submitSearch(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setTargetFilters((current) => ({
      ...current,
      query: searchText.trim() || null,
      page: 1,
    }))
    resetSelection()
  }

  async function invalidateWorkspace(reportId: string): Promise<void> {
    if (!selectedEnrollmentId) return
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: [...reportKeys.root(accountId), "targets"],
      }),
      queryClient.invalidateQueries({
        queryKey: reportKeys.slots(accountId, selectedEnrollmentId),
      }),
      queryClient.invalidateQueries({
        queryKey: reportKeys.detail(accountId, reportId),
      }),
      queryClient.invalidateQueries({
        queryKey: [...reportKeys.root(accountId), "list"],
      }),
      queryClient.invalidateQueries({
        queryKey: [...reportKeys.root(accountId), "versions"],
      }),
    ])
  }

  async function refreshConflict(reportId: string): Promise<void> {
    if (!selectedEnrollmentId) return
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: reportKeys.slots(accountId, selectedEnrollmentId),
      }),
      queryClient.invalidateQueries({
        queryKey: reportKeys.detail(accountId, reportId),
      }),
      queryClient.invalidateQueries({
        queryKey: [...reportKeys.root(accountId), "versions"],
      }),
    ])
  }

  if (pending || targets.isPending) {
    return (
      <main className="teachingReportPage">
        <header className="teachingReportHeader">
          <p className="pageEyebrow">TEACHING REPORTS</p>
          <h1>教学评语工作台</h1>
        </header>
        <section className="teachingReportState" aria-live="polite">
          <p>正在更新可填写学员名单…</p>
        </section>
      </main>
    )
  }

  if (targets.isError) {
    return (
      <main className="teachingReportPage">
        <header className="teachingReportHeader">
          <p className="pageEyebrow">TEACHING REPORTS</p>
          <h1>教学评语工作台</h1>
        </header>
        <section className="teachingReportState error" role="alert">
          <p>可填写学员名单暂时无法读取。</p>
          <button
            type="button"
            aria-label="重试学员名单"
            onClick={() => void targets.refetch()}
          >
            <RefreshCw aria-hidden="true" /> 重试
          </button>
        </section>
      </main>
    )
  }

  const targetItems = targets.data.items
  const slotItems = slots.data?.items ?? []
  const filteredSlots = slotItems.filter(
    (item) =>
      (kind === "all" || item.report_kind === kind) &&
      (status === "all" || item.status === status),
  )
  const targetTotalPages = Math.max(
    1,
    Math.ceil(targets.data.total / targets.data.page_size),
  )

  return (
    <main
      className="teachingReportPage teachingReportsAuthorPage"
      data-evaluation-dialog-owner
      tabIndex={-1}
    >
      <header className="teachingReportHeader">
        <div>
          <p className="pageEyebrow">TEACHING REPORTS</p>
          <h1>教学评语工作台</h1>
          <p>选择已授权学员，填写日报、周报并查看不可变版本记录。</p>
        </div>
      </header>

      {notice ? <p className="teachingReportNotice" role="status">{notice}</p> : null}

      <section className="teachingReportFilters" aria-label="学员目录筛选">
        <label>
          <span>营期</span>
          <select
            value={targetFilters.cohortId ?? ""}
            onChange={(event) => updateDirectoryFilter("cohortId", event.target.value)}
          >
            <option value="">全部营期</option>
            {(facets?.cohorts ?? []).map((cohort) => (
              <option key={cohort.id} value={cohort.id}>{cohort.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>项目</span>
          <select
            value={targetFilters.projectId ?? ""}
            onChange={(event) => updateDirectoryFilter("projectId", event.target.value)}
          >
            <option value="">全部项目</option>
            {projectOptions.map((project) => (
              <option key={project.id} value={project.id}>{project.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>班级</span>
          <select
            value={targetFilters.classId ?? ""}
            onChange={(event) => updateDirectoryFilter("classId", event.target.value)}
          >
            <option value="">全部班级</option>
            {classOptions.map((item) => (
              <option key={item.id} value={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <form onSubmit={submitSearch}>
          <label>
            <span>搜索学员</span>
            <input
              value={searchText}
              maxLength={80}
              onChange={(event) => setSearchText(event.target.value)}
            />
          </label>
          <button type="submit" aria-label="搜索">搜索</button>
        </form>
      </section>

      {targetItems.length === 0 ? (
        <section className="teachingReportState" aria-label="暂无可填写学员">
          <h2>暂无可填写学员</h2>
          <p>当前筛选范围内没有获得教学评语授权的报名。</p>
        </section>
      ) : (
        <section className="teachingReportTargetList" aria-label="可填写学员名单">
          {targetItems.map((target) => (
            <article key={target.enrollment_id}>
              <div>
                <h2>{target.student_name}</h2>
                <p>{target.student_number}</p>
                <p>{target.cohort_name} · {target.project_name} · {target.class_name}</p>
              </div>
              <button
                type="button"
                aria-label={`选择${target.student_name}`}
                aria-pressed={selectedEnrollmentId === target.enrollment_id}
                onClick={() => {
                  setSelectedEnrollmentId(target.enrollment_id)
                  setEditorSlot(null)
                  setTimelineReportId(null)
                  setNotice(null)
                }}
              >
                {selectedEnrollmentId === target.enrollment_id ? "已选择" : "选择"}
              </button>
            </article>
          ))}
        </section>
      )}

      {targetTotalPages > 1 ? (
        <nav className="teachingReportPagination" aria-label="学员名单分页">
          <button
            type="button"
            aria-label="上一页学员"
            disabled={(targetFilters.page ?? 1) <= 1}
            onClick={() => {
              setTargetFilters((current) => ({
                ...current,
                page: Math.max(1, (current.page ?? 1) - 1),
              }))
              resetSelection()
            }}
          >上一页</button>
          <span>第 {targetFilters.page ?? 1} / {targetTotalPages} 页</span>
          <button
            type="button"
            aria-label="下一页学员"
            disabled={(targetFilters.page ?? 1) >= targetTotalPages}
            onClick={() => {
              setTargetFilters((current) => ({
                ...current,
                page: (current.page ?? 1) + 1,
              }))
              resetSelection()
            }}
          >下一页</button>
        </nav>
      ) : null}

      {selectedEnrollmentId ? (
        privateTargetMissing ? (
          <section className="teachingReportState" aria-live="polite">
            <p>正在刷新学员名单…</p>
          </section>
        ) : slots.isPending ? (
          <section className="teachingReportState" aria-live="polite">
            <p>正在更新评语任务…</p>
          </section>
        ) : slots.isError ? (
          <section className="teachingReportState error" role="alert">
            <p>该学员的评语任务暂时无法读取。</p>
            <button type="button" onClick={() => void slots.refetch()}>
              <RefreshCw aria-hidden="true" /> 重试评语任务
            </button>
          </section>
        ) : (
          <section className="teachingReportWorkspace" role="region" aria-label="评语任务">
            <div className="teachingReportFilters" aria-label="评语任务筛选">
              <label>
                <span>评语类型</span>
                <select
                  value={kind}
                  onChange={(event) => {
                    setKind(event.target.value as TeachingReportKind | "all")
                    setEditorSlot(null)
                    setTimelineReportId(null)
                  }}
                >
                  <option value="all">全部类型</option>
                  <option value="daily">日报</option>
                  <option value="weekly">周报</option>
                </select>
              </label>
              <label>
                <span>任务状态</span>
                <select
                  value={status}
                  onChange={(event) => {
                    setStatus(event.target.value as TeachingReportStatus | "all")
                    setEditorSlot(null)
                    setTimelineReportId(null)
                  }}
                >
                  <option value="all">全部状态</option>
                  {reportStatuses.map(([value, label]) => (
                    <option key={value} value={value}>{label}</option>
                  ))}
                </select>
              </label>
            </div>

            {filteredSlots.length === 0 ? (
              <div className="teachingReportState"><p>当前筛选下暂无任务。</p></div>
            ) : (
              <div className="teachingReportSlotList">
                {filteredSlots.map((item) => {
                  const label = `${kindLabel(item.report_kind)} 第 ${item.period_sequence} 期`
                  return (
                    <article key={slotKey(item)} aria-label={label}>
                      <header>
                        <h3>{label}</h3>
                        <span>{item.status ? statusLabels[item.status] : "未创建"}</span>
                      </header>
                      <p>{item.period_start_date} 至 {item.period_end_date}</p>
                      <p className="teachingReportSlotWindow">
                        {slotWindowLabel(item)}
                      </p>
                      <div>
                        {item.allowed_actions.length > 0 ? (
                          <button
                            type="button"
                            aria-label={`编辑${kindLabel(item.report_kind)}第 ${item.period_sequence} 期`}
                            onClick={() => setEditorSlot(item)}
                          >
                            打开操作
                          </button>
                        ) : null}
                        {item.report_id ? (
                          <button
                            type="button"
                            aria-label={`查看${kindLabel(item.report_kind)}第 ${item.period_sequence} 期版本`}
                            onClick={() => setTimelineReportId(item.report_id)}
                          >
                            查看版本
                          </button>
                        ) : null}
                      </div>
                    </article>
                  )
                })}
              </div>
            )}
          </section>
        )
      ) : null}

      {timelineReportId ? (
        <section className="teachingReportTimelineWorkspace">
          <button
            type="button"
            aria-label="关闭版本记录"
            onClick={() => setTimelineReportId(null)}
          >
            关闭版本记录
          </button>
          <ReportVersionTimeline
            reportId={timelineReportId}
            compareCurrent
            onUnavailable={recoverPrivateTarget}
          />
        </section>
      ) : null}

      {editorSlot ? (
        <TeachingReportEditorDialog
          slot={editorSlot}
          onClose={() => setEditorSlot(null)}
          onChanged={invalidateWorkspace}
          onConflict={refreshConflict}
          onUnavailable={recoverPrivateTarget}
          authoritativeReady={!slots.isFetching && !slots.isError}
        />
      ) : null}
    </main>
  )
}
