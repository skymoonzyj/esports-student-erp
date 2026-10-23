import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowLeft,
  CalendarDays,
  CheckCircle2,
  Clock3,
  History,
  Pencil,
  Plus,
  Power,
  PowerOff,
  Search,
  TriangleAlert,
  UserMinus,
  UserPlus,
  X,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import { Link } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { CohortStudentsPanel } from "../admissions/CohortStudentsPanel"
import { useAuth } from "../auth/AuthProvider"
import { CohortStaffPanel } from "../evaluations/CohortStaffPanel"
import { listEvaluationStaffScopes } from "../evaluations/foundationApi"
import {
  createClass,
  createCoachAssignment,
  endCoachAssignment,
  getCohort,
  listClasses,
  listCoachAssignments,
  listCohortStatusEvents,
  listEligibleCoaches,
  listProfessionalTeams,
  listProjects,
  transitionCohort,
  updateClass,
} from "./api"
import type {
  AssignmentType,
  ClassType,
  CoachAssignment,
  Cohort,
  CohortStatus,
  GameProject,
  TeachingClass,
  TeachingClassInput,
} from "./api"
import {
  formatExclusiveEndDate,
  formatShanghaiDate,
  formatYuan,
  isoToShanghaiDate,
  shanghaiDateToIso,
  yuanToCents,
} from "./datetime"
import {
  ASSIGNMENT_TYPE_LABELS,
  AUTOMATION_ISSUE_LABELS,
  CLASS_TYPE_LABELS,
  COHORT_STATUS_LABELS,
  TRIGGER_LABELS,
} from "./labels"
import { TeachingSectionNav } from "./TeachingSectionNav"
import "./teaching.css"

type DetailTab = "overview" | "classes" | "coaches" | "workers" | "students"
type ClassDialogState = { mode: "create" } | { mode: "edit"; item: TeachingClass }

const STATUS_ACTIONS: Record<CohortStatus, Array<{ target: CohortStatus; label: string }>> = {
  draft: [{ target: "open", label: "开放报名" }],
  open: [
    { target: "upcoming", label: "标记即将开营" },
    { target: "active", label: "开始营期" },
  ],
  upcoming: [
    { target: "open", label: "返回开放报名" },
    { target: "active", label: "开始营期" },
  ],
  active: [{ target: "completed", label: "完成营期" }],
  completed: [{ target: "archived", label: "归档营期" }],
  archived: [],
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function isObjectAccessDenied(error: unknown): boolean {
  return (
    error instanceof ProblemDetailsError &&
    (error.problem.status === 403 || error.problem.status === 404)
  )
}

function DetailDialog({
  title,
  pending,
  closeLabel,
  onClose,
  children,
}: {
  title: string
  pending: boolean
  closeLabel: string
  onClose: () => void
  children: ReactNode
}) {
  const titleId = `teaching-detail-dialog-${title.replaceAll(" ", "-")}`
  useEffect(() => {
    if (pending) return
    function close(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", close)
    return () => window.removeEventListener("keydown", close)
  }, [onClose, pending])

  return (
    <div className="teachingDialogBackdrop" onMouseDown={pending ? undefined : onClose}>
      <section
        className="teachingDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="teachingDialogHeader">
          <h2 id={titleId}>{title}</h2>
          <button type="button" aria-label={closeLabel} disabled={pending} onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}

function ClassFormDialog({
  state,
  cohortId,
  projects,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  state: ClassDialogState
  cohortId: string
  projects: readonly GameProject[]
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: TeachingClassInput | Omit<TeachingClassInput, "cohort_id" | "project_id">) => void
}) {
  const existing = state.mode === "edit" ? state.item : null
  const [projectId, setProjectId] = useState(
    existing?.project_id ?? projects.find((project) => project.is_active)?.id ?? "",
  )
  const [name, setName] = useState(existing?.name ?? "")
  const [classType, setClassType] = useState<ClassType>(existing?.class_type ?? "standard")
  const [teamId, setTeamId] = useState(existing?.professional_team_id ?? "")
  const [capacity, setCapacity] = useState(String(existing?.capacity ?? 20))
  const [tuition, setTuition] = useState(
    existing ? formatYuan(existing.default_tuition_cents) : "0.00",
  )
  const [validationError, setValidationError] = useState<string | null>(null)
  const teams = useQuery({
    queryKey: ["professional-teams", { projectId, includeInactive: existing !== null }],
    queryFn: () =>
      listProfessionalTeams({
        projectId,
        includeInactive: existing !== null,
        page: 1,
        pageSize: 100,
      }),
    enabled: classType === "elite" && Boolean(projectId),
  })

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (classType === "elite" && !teamId) {
      setValidationError("菁英班必须选择职业队伍")
      return
    }
    try {
      const shared = {
        name: name.trim(),
        class_type: classType,
        professional_team_id: classType === "elite" ? teamId : null,
        capacity: Number(capacity),
        default_tuition_cents: yuanToCents(tuition),
      }
      setValidationError(null)
      onSubmit(
        state.mode === "create"
          ? { ...shared, cohort_id: cohortId, project_id: projectId }
          : shared,
      )
    } catch (caught) {
      setValidationError(caught instanceof Error ? caught.message : "班级信息填写无效")
    }
  }

  const title = state.mode === "create" ? "新增班级" : "编辑班级"
  return (
    <DetailDialog title={title} pending={pending} closeLabel="关闭班级表单" onClose={onClose}>
      <form className="teachingDialogForm" onSubmit={submit}>
        <fieldset className="teachingSegmented" disabled={pending}>
          <legend>班级类型</legend>
          {(["standard", "elite"] as const).map((value) => (
            <label key={value}>
              <input
                type="radio"
                name="class-type"
                value={value}
                checked={classType === value}
                onChange={() => {
                  setClassType(value)
                  if (value === "standard") setTeamId("")
                }}
              />
              <span>{CLASS_TYPE_LABELS[value]}</span>
            </label>
          ))}
        </fieldset>
        <label>
          <span>电竞项目</span>
          <select
            value={projectId}
            onChange={(event) => {
              setProjectId(event.target.value)
              setTeamId("")
            }}
            disabled={pending || existing !== null}
            required
          >
            <option value="" disabled>请选择项目</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id} disabled={!project.is_active}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        {classType === "elite" ? (
          <label>
            <span>职业队伍</span>
            <select
              value={teamId}
              onChange={(event) => setTeamId(event.target.value)}
              disabled={pending || teams.isLoading}
              required
            >
              <option value="" disabled>请选择同项目队伍</option>
              {teams.data?.items.map((team) => (
                <option key={team.id} value={team.id} disabled={!team.is_active && team.id !== existing?.professional_team_id}>
                  {team.name}{team.is_active ? "" : "（已停用）"}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label>
          <span>班级名称</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={pending}
            maxLength={120}
            required
          />
        </label>
        <div className="cohortDateGrid">
          <label>
            <span>人数上限</span>
            <input
              type="number"
              min={1}
              max={10_000}
              step={1}
              value={capacity}
              onChange={(event) => setCapacity(event.target.value)}
              disabled={pending}
              required
            />
          </label>
          <label>
            <span>默认学费（元）</span>
            <input
              inputMode="decimal"
              value={tuition}
              onChange={(event) => setTuition(event.target.value)}
              disabled={pending}
              required
            />
          </label>
        </div>
        {validationError || error ? (
          <div className="teachingFormError" role="alert">{validationError ?? error}</div>
        ) : null}
        <footer className="teachingDialogActions">
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>
            取消
          </button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending || !projectId}>
            {pending ? "正在保存" : "保存班级"}
          </button>
        </footer>
      </form>
    </DetailDialog>
  )
}

function CoachAssignmentDialog({
  cohort,
  classId,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  cohort: Cohort
  classId: string
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: {
    coach_person_id: string
    assignment_type: AssignmentType
    valid_from: string
    valid_until: string | null
  }) => void
}) {
  const [queryText, setQueryText] = useState("")
  const [appliedQuery, setAppliedQuery] = useState("")
  const [coachId, setCoachId] = useState("")
  const [assignmentType, setAssignmentType] = useState<AssignmentType>("primary")
  const [validFrom, setValidFrom] = useState(isoToShanghaiDate(cohort.starts_at))
  const [validUntil, setValidUntil] = useState("")
  const [validationError, setValidationError] = useState<string | null>(null)
  const coaches = useQuery({
    queryKey: ["eligible-coaches", classId, appliedQuery],
    queryFn: () => listEligibleCoaches(classId, appliedQuery, 1, 20),
    enabled: appliedQuery.length > 0,
  })

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!coachId) {
      setValidationError("请选择一名教练")
      return
    }
    try {
      setValidationError(null)
      onSubmit({
        coach_person_id: coachId,
        assignment_type: assignmentType,
        valid_from: shanghaiDateToIso(validFrom),
        valid_until: validUntil ? shanghaiDateToIso(validUntil) : null,
      })
    } catch (caught) {
      setValidationError(caught instanceof Error ? caught.message : "教练分配时间无效")
    }
  }

  return (
    <DetailDialog title="分配教练" pending={pending} closeLabel="关闭教练分配表单" onClose={onClose}>
      <form className="teachingDialogForm" onSubmit={submit}>
        <div className="eligibleCoachSearch">
          <label>
            <span>查找教练</span>
            <input value={queryText} onChange={(event) => setQueryText(event.target.value)} disabled={pending} />
          </label>
          <button
            type="button"
            className="teachingSecondaryButton"
            disabled={pending || queryText.trim().length === 0}
            onClick={() => {
              setCoachId("")
              setAppliedQuery(queryText.trim())
            }}
          >
            <Search aria-hidden="true" />查询教练
          </button>
        </div>
        {coaches.data ? (
          <div className="eligibleCoachList" aria-label="可分配教练">
            {coaches.data.items.map((coach) => (
              <label key={coach.id}>
                <input
                  type="radio"
                  name="eligible-coach"
                  checked={coachId === coach.id}
                  onChange={() => setCoachId(coach.id)}
                  disabled={pending}
                />
                <span>{coach.full_name}</span>
              </label>
            ))}
            {coaches.data.items.length === 0 ? <p>没有符合条件的可分配教练。</p> : null}
          </div>
        ) : null}
        <fieldset className="teachingSegmented" disabled={pending}>
          <legend>教练角色</legend>
          {(["primary", "assistant"] as const).map((value) => (
            <label key={value}>
              <input
                type="radio"
                name="assignment-type"
                checked={assignmentType === value}
                onChange={() => setAssignmentType(value)}
              />
              <span>{ASSIGNMENT_TYPE_LABELS[value]}</span>
            </label>
          ))}
        </fieldset>
        <label>
          <span>生效日期</span>
          <input
            type="date"
            value={validFrom}
            onChange={(event) => setValidFrom(event.target.value)}
            disabled={pending}
            required
          />
        </label>
        <label>
          <span>计划结束日期（可选）</span>
          <input
            type="date"
            value={validUntil}
            onChange={(event) => setValidUntil(event.target.value)}
            disabled={pending}
          />
        </label>
        {validationError || error ? (
          <div className="teachingFormError" role="alert">{validationError ?? error}</div>
        ) : null}
        <footer className="teachingDialogActions">
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending}>
            {pending ? "正在分配" : "确认分配"}
          </button>
        </footer>
      </form>
    </DetailDialog>
  )
}

function EndAssignmentDialog({
  assignment,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  assignment: CoachAssignment
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (reason: string) => void
}) {
  const [reason, setReason] = useState("")
  const [validationError, setValidationError] = useState<string | null>(null)

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    const normalized = reason.trim()
    if (normalized.length < 2) {
      setValidationError("请填写结束原因")
      return
    }
    setValidationError(null)
    onSubmit(normalized)
  }

  return (
    <DetailDialog title="结束教练分配" pending={pending} closeLabel="关闭结束分配表单" onClose={onClose}>
      <form className="teachingDialogForm" onSubmit={submit}>
        <p className="dialogSubject">{assignment.coach_full_name} · {ASSIGNMENT_TYPE_LABELS[assignment.assignment_type]}</p>
        <label>
          <span>结束原因</span>
          <textarea value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} disabled={pending} />
        </label>
        {validationError || error ? (
          <div className="teachingFormError" role="alert">{validationError ?? error}</div>
        ) : null}
        <footer className="teachingDialogActions">
          <button type="button" className="teachingSecondaryButton" disabled={pending} onClick={onClose}>取消</button>
          <button type="submit" className="teachingDangerButton" disabled={pending}>
            {pending ? "正在结束" : "确认结束"}
          </button>
        </footer>
      </form>
    </DetailDialog>
  )
}

export function CohortDetailPage({
  cohortId,
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  cohortId: string
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const { user } = useAuth()
  const accountId = user?.account_id ?? "anonymous"
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<DetailTab>("overview")
  const [classDialog, setClassDialog] = useState<ClassDialogState | null>(null)
  const [selectedClassId, setSelectedClassId] = useState("")
  const [assigning, setAssigning] = useState(false)
  const [endingAssignment, setEndingAssignment] = useState<CoachAssignment | null>(null)
  const [transitionError, setTransitionError] = useState<string | null>(null)
  const [staffRevocation, setStaffRevocation] = useState<{
    accountId: string
    cohortId: string | null
  } | null>(null)

  const cohort = useQuery({
    queryKey: ["cohort", cohortId],
    queryFn: () => getCohort(cohortId),
  })
  const events = useQuery({
    queryKey: ["cohort-status-events", cohortId],
    queryFn: () => listCohortStatusEvents(cohortId, 1, 20),
    enabled: tab === "overview",
  })
  const classes = useQuery({
    queryKey: ["classes", { cohortId, page: 1, pageSize: 100 }],
    queryFn: () => listClasses({ cohortId, page: 1, pageSize: 100 }),
    enabled: tab === "classes" || tab === "coaches",
  })
  const globalReader = permissions.has("teaching.read")
  const canReadEnrollments =
    permissions.has("enrollments.read") || scopedPermissions.has("enrollments.read")
  const staffCapability = useQuery({
    queryKey: ["cohort-staff-capability", accountId, cohortId],
    queryFn: () =>
      listEvaluationStaffScopes(cohortId, {
        scopeKind: "project",
        page: 1,
        pageSize: 1,
      }),
    retry: false,
  })
  const staffCapabilityDenied = isObjectAccessDenied(staffCapability.error)
  const staffAccessRevoked =
    staffRevocation?.accountId === accountId &&
    (staffRevocation.cohortId === null || staffRevocation.cohortId === cohortId)
  const canReadStaff = staffCapability.isSuccess && !staffAccessRevoked
  const projects = useQuery({
    queryKey: ["projects", { includeInactive: true, page: 1, pageSize: 100 }],
    queryFn: () => listProjects({ includeInactive: true, page: 1, pageSize: 100 }),
    enabled: tab === "classes" && globalReader,
  })

  useEffect(() => {
    if (selectedClassId || !classes.data?.items.length) return
    setSelectedClassId(classes.data.items[0].id)
  }, [classes.data?.items, selectedClassId])

  useEffect(() => {
    if (!staffCapabilityDenied) return
    queryClient.removeQueries({
      predicate: ({ queryKey }) =>
        queryKey[0] === "evaluations" &&
        queryKey.includes(cohortId) &&
        (queryKey.includes("staff") || queryKey.includes("staff-scopes")),
    })
    setTab((current) => (current === "workers" ? "overview" : current))
  }, [cohortId, queryClient, staffCapabilityDenied])

  const assignments = useQuery({
    queryKey: ["coach-assignments", selectedClassId, true],
    queryFn: () => listCoachAssignments(selectedClassId, true, 1, 20),
    enabled: tab === "coaches" && Boolean(selectedClassId),
  })
  const transitionMutation = useMutation({
    mutationFn: (target: CohortStatus) => transitionCohort(cohortId, target),
    onSuccess: async () => {
      setTransitionError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["cohort", cohortId] }),
        queryClient.invalidateQueries({ queryKey: ["cohort-status-events", cohortId] }),
        queryClient.invalidateQueries({ queryKey: ["cohorts"] }),
      ])
    },
    onError: (error) => setTransitionError(errorMessage(error, "营期状态暂时无法修改")),
  })
  const classSave = useMutation({
    mutationFn: ({
      state,
      input,
    }: {
      state: ClassDialogState
      input: TeachingClassInput | Omit<TeachingClassInput, "cohort_id" | "project_id">
    }) =>
      state.mode === "create"
        ? createClass(input as TeachingClassInput)
        : updateClass(state.item.id, input as Omit<TeachingClassInput, "cohort_id" | "project_id">),
    onSuccess: async () => {
      setClassDialog(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["classes"] }),
        queryClient.invalidateQueries({ queryKey: ["cohort", cohortId] }),
        queryClient.invalidateQueries({ queryKey: ["cohorts"] }),
      ])
    },
  })
  const classStatus = useMutation({
    mutationFn: (item: TeachingClass) => updateClass(item.id, { is_active: !item.is_active }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["classes"] }),
        queryClient.invalidateQueries({ queryKey: ["cohort", cohortId] }),
        queryClient.invalidateQueries({ queryKey: ["cohorts"] }),
      ])
    },
  })
  const assignmentCreate = useMutation({
    mutationFn: (input: {
      coach_person_id: string
      assignment_type: AssignmentType
      valid_from: string
      valid_until: string | null
    }) => createCoachAssignment(selectedClassId, input),
    onSuccess: async () => {
      setAssigning(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["coach-assignments", selectedClassId] }),
        queryClient.invalidateQueries({ queryKey: ["cohort", cohortId] }),
        queryClient.invalidateQueries({ queryKey: ["cohorts"] }),
      ])
    },
  })
  const assignmentEnd = useMutation({
    mutationFn: ({ assignment, reason }: { assignment: CoachAssignment; reason: string }) =>
      endCoachAssignment(selectedClassId, assignment.id, reason),
    onSuccess: async () => {
      setEndingAssignment(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["coach-assignments", selectedClassId] }),
        queryClient.invalidateQueries({ queryKey: ["cohort", cohortId] }),
        queryClient.invalidateQueries({ queryKey: ["cohorts"] }),
      ])
    },
  })

  const projectNames = useMemo(
    () => new Map((projects.data?.items ?? []).map((project) => [project.id, project.name])),
    [projects.data?.items],
  )
  const archived = cohort.data?.status === "archived"
  const canManageCohorts = permissions.has("cohorts.manage") && !archived
  const canManageClasses = permissions.has("classes.manage") && !archived
  const canManageCoaches = permissions.has("coach_assignments.manage") && !archived

  if (cohort.isLoading) return <div className="teachingEmptyState">正在读取营期详情…</div>
  if (cohort.isError || !cohort.data) return <div className="teachingEmptyState">暂时无法读取营期详情。</div>
  const item = cohort.data
  const statusActions = STATUS_ACTIONS[item.status]

  return (
    <div className="cohortDetailPage">
      <TeachingSectionNav
        permissions={permissions}
        scopedPermissions={scopedPermissions}
      />
      <Link className="teachingBackLink" to="/teaching/cohorts">
        <ArrowLeft aria-hidden="true" />返回营期列表
      </Link>
      <header className="cohortDetailHeader">
        <div>
          <div className="cohortHeadingStatus">
            <p className="teachingEyebrow">招生教学</p>
            <span className={`cohortStatus status-${item.status}`}>{COHORT_STATUS_LABELS[item.status]}</span>
          </div>
          <h1>{item.name}</h1>
        </div>
        {canManageCohorts && statusActions.length > 0 ? (
          <div className="cohortStatusActions">
            {statusActions.map((action) => (
              <button
                type="button"
                className="teachingPrimaryButton"
                key={action.target}
                disabled={transitionMutation.isPending}
                onClick={() => transitionMutation.mutate(action.target)}
              >
                {action.label}
              </button>
            ))}
          </div>
        ) : null}
      </header>
      {transitionError ? <div className="teachingPageError" role="alert">{transitionError}</div> : null}
      {staffCapability.isError && !staffCapabilityDenied ? (
        <div className="teachingPageError" role="alert">
          <span>工作人员权限检查失败</span>
          <button
            type="button"
            disabled={staffCapability.isFetching}
            onClick={() => void staffCapability.refetch()}
          >
            {staffCapability.isFetching ? "正在重试" : "重试工作人员权限检查"}
          </button>
        </div>
      ) : null}

      <div className="cohortDetailTabs" role="tablist" aria-label="营期详情">
        <button type="button" role="tab" aria-selected={tab === "overview"} onClick={() => setTab("overview")}>概览</button>
        <button type="button" role="tab" aria-selected={tab === "classes"} onClick={() => setTab("classes")}>班级</button>
        <button type="button" role="tab" aria-selected={tab === "coaches"} onClick={() => setTab("coaches")}>教练</button>
        {canReadStaff ? (
          <button type="button" role="tab" aria-selected={tab === "workers"} onClick={() => setTab("workers")}>工作人员</button>
        ) : null}
        {canReadEnrollments ? (
          <button type="button" role="tab" aria-selected={tab === "students"} onClick={() => setTab("students")}>学员</button>
        ) : null}
      </div>

      {tab === "overview" ? (
        <div className="cohortOverview">
          <section className="cohortOverviewPanel" aria-label="营期安排">
            <h2>营期安排</h2>
            <dl className="cohortDefinitionGrid">
              <div><dt><CalendarDays aria-hidden="true" />开营</dt><dd>{formatShanghaiDate(item.starts_at)}</dd></div>
              <div><dt><Clock3 aria-hidden="true" />结营</dt><dd>{formatExclusiveEndDate(item.ends_at)}</dd></div>
              <div><dt>默认入住</dt><dd>{formatShanghaiDate(item.default_check_in_at)}</dd></div>
              <div><dt>默认离营</dt><dd>{formatExclusiveEndDate(item.default_check_out_at)}</dd></div>
            </dl>
          </section>
          <section className="cohortOverviewPanel" aria-label="自动化与主教练">
            <h2>运行状态</h2>
            <div className="cohortRuntimeStatus">
              <span className={item.auto_start_enabled ? "enabled" : "disabled"}>自动开营 {item.auto_start_enabled ? "开启" : "关闭"}</span>
              <span className={item.auto_complete_enabled ? "enabled" : "disabled"}>自动结营 {item.auto_complete_enabled ? "开启" : "关闭"}</span>
              {item.missing_primary_class_count > 0 ? (
                <span className="cohortWarning"><TriangleAlert aria-hidden="true" />{item.missing_primary_class_count} 个班级缺少主教练</span>
              ) : (
                <span className="cohortReady"><CheckCircle2 aria-hidden="true" />主教练配置完整</span>
              )}
              {item.automation_issues.map((issue) => (
                <span className="cohortWarning" key={issue}><TriangleAlert aria-hidden="true" />{AUTOMATION_ISSUE_LABELS[issue]}</span>
              ))}
            </div>
          </section>
          <section className="cohortHistoryPanel" aria-label="状态历史">
            <h2><History aria-hidden="true" />状态历史</h2>
            {events.isLoading ? <div className="teachingEmptyState">正在读取状态历史…</div> : null}
            {events.data?.items.map((event) => (
              <div className="cohortHistoryRow" key={event.id}>
                <span>{COHORT_STATUS_LABELS[event.old_status]} → {COHORT_STATUS_LABELS[event.new_status]}</span>
                <span className={`triggerBadge trigger-${event.trigger}`}>{TRIGGER_LABELS[event.trigger]}</span>
                <time>{formatShanghaiDate(event.occurred_at)}</time>
              </div>
            ))}
            {events.data?.items.length === 0 ? <div className="teachingEmptyState">暂无状态变化。</div> : null}
          </section>
        </div>
      ) : null}

      {tab === "classes" ? (
        <section className="cohortTabSurface" aria-label="班级配置">
          <header>
            <div><h2>班级配置</h2><span>{classes.data?.total ?? 0} 个可见班级</span></div>
            {canManageClasses ? (
              <button type="button" className="teachingPrimaryButton" onClick={() => { classSave.reset(); setClassDialog({ mode: "create" }) }}>
                <Plus aria-hidden="true" />新增班级
              </button>
            ) : null}
          </header>
          {classStatus.error ? (
            <div className="teachingPageError" role="alert">
              {errorMessage(classStatus.error, "班级状态暂时无法修改")}
            </div>
          ) : null}
          {classes.isLoading ? <div className="teachingEmptyState">正在读取班级…</div> : null}
          {classes.data?.items.map((classItem) => (
            <article className="teachingClassRow" key={classItem.id}>
              <div>
                <strong>{classItem.name}</strong>
                <span>
                  {CLASS_TYPE_LABELS[classItem.class_type]}
                  {globalReader
                    ? ` · ${projectNames.get(classItem.project_id) ?? "项目读取中"}`
                    : ""}
                </span>
              </div>
              <dl>
                <div><dt>人数上限</dt><dd>{classItem.capacity}</dd></div>
                <div><dt>默认学费</dt><dd>¥{formatYuan(classItem.default_tuition_cents)}</dd></div>
                <div><dt>状态</dt><dd>{classItem.is_active ? "启用" : "停用"}</dd></div>
              </dl>
              {canManageClasses ? (
                <div className="teachingRowActions">
                  <button type="button" aria-label={`编辑 ${classItem.name}`} onClick={() => { classSave.reset(); setClassDialog({ mode: "edit", item: classItem }) }}><Pencil aria-hidden="true" /></button>
                  <button type="button" aria-label={`${classItem.is_active ? "停用" : "启用"} ${classItem.name}`} disabled={classStatus.isPending} onClick={() => classStatus.mutate(classItem)}>
                    {classItem.is_active ? <PowerOff aria-hidden="true" /> : <Power aria-hidden="true" />}
                  </button>
                </div>
              ) : null}
            </article>
          ))}
          {classes.data?.items.length === 0 ? <div className="teachingEmptyState">当前没有可见班级。</div> : null}
        </section>
      ) : null}

      {tab === "coaches" ? (
        <section className="cohortTabSurface" aria-label="教练分配">
          <header>
            <div><h2>教练分配</h2><span>当前与历史分配</span></div>
            {canManageCoaches && selectedClassId ? (
              <button type="button" className="teachingPrimaryButton" onClick={() => { assignmentCreate.reset(); setAssigning(true) }}>
                <UserPlus aria-hidden="true" />分配教练
              </button>
            ) : null}
          </header>
          <label className="classPicker">
            <span>选择班级</span>
            <select value={selectedClassId} onChange={(event) => setSelectedClassId(event.target.value)}>
              {(classes.data?.items ?? []).map((classItem) => <option key={classItem.id} value={classItem.id}>{classItem.name}</option>)}
            </select>
          </label>
          {classes.isLoading || assignments.isLoading ? <div className="teachingEmptyState">正在读取教练分配…</div> : null}
          {assignments.data?.items.map((assignmentItem) => (
            <article className={`coachAssignmentRow${assignmentItem.ended_at ? " ended" : ""}`} key={assignmentItem.id}>
              <div><strong>{assignmentItem.coach_full_name}</strong><span>{ASSIGNMENT_TYPE_LABELS[assignmentItem.assignment_type]}</span></div>
              <div className="assignmentValidity">
                <span>{formatShanghaiDate(assignmentItem.valid_from)}</span>
                <span>{assignmentItem.ended_at ? `已于 ${formatShanghaiDate(assignmentItem.ended_at)} 结束` : assignmentItem.valid_until ? `至 ${formatShanghaiDate(assignmentItem.valid_until)}` : "持续有效"}</span>
                {assignmentItem.end_reason ? <small>{assignmentItem.end_reason}</small> : null}
              </div>
              {canManageCoaches && !assignmentItem.ended_at ? (
                <button type="button" className="teachingDangerButton" aria-label={`结束 ${assignmentItem.coach_full_name}`} onClick={() => { assignmentEnd.reset(); setEndingAssignment(assignmentItem) }}>
                  <UserMinus aria-hidden="true" />结束
                </button>
              ) : null}
            </article>
          ))}
          {assignments.data?.items.length === 0 ? <div className="teachingEmptyState">当前班级没有教练分配记录。</div> : null}
        </section>
      ) : null}

      {tab === "students" && canReadEnrollments ? (
        <CohortStudentsPanel cohortId={cohortId} />
      ) : null}

      {tab === "workers" && canReadStaff ? (
        <section className="cohortTabSurface" aria-label="工作人员">
          <CohortStaffPanel
            cohortId={cohortId}
            onAccessRevoked={(scope) => {
              setStaffRevocation({
                accountId,
                cohortId: scope === "cohort" ? cohortId : null,
              })
              setTab("overview")
            }}
          />
        </section>
      ) : null}

      {classDialog ? (
        <ClassFormDialog
          state={classDialog}
          cohortId={cohortId}
          projects={projects.data?.items ?? []}
          pending={classSave.isPending}
          error={classSave.error ? errorMessage(classSave.error, "班级暂时无法保存") : null}
          onClose={() => { if (!classSave.isPending) { classSave.reset(); setClassDialog(null) } }}
          onSubmit={(input) => classSave.mutate({ state: classDialog, input })}
        />
      ) : null}
      {assigning && selectedClassId ? (
        <CoachAssignmentDialog
          cohort={item}
          classId={selectedClassId}
          pending={assignmentCreate.isPending}
          error={assignmentCreate.error ? errorMessage(assignmentCreate.error, "教练暂时无法分配") : null}
          onClose={() => { if (!assignmentCreate.isPending) { assignmentCreate.reset(); setAssigning(false) } }}
          onSubmit={(input) => assignmentCreate.mutate(input)}
        />
      ) : null}
      {endingAssignment ? (
        <EndAssignmentDialog
          assignment={endingAssignment}
          pending={assignmentEnd.isPending}
          error={assignmentEnd.error ? errorMessage(assignmentEnd.error, "教练分配暂时无法结束") : null}
          onClose={() => { if (!assignmentEnd.isPending) { assignmentEnd.reset(); setEndingAssignment(null) } }}
          onSubmit={(reason) => assignmentEnd.mutate({ assignment: endingAssignment, reason })}
        />
      ) : null}
    </div>
  )
}
