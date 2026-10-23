import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  CalendarDays,
  Clock3,
  Plus,
  RefreshCw,
  Search,
  SlidersHorizontal,
  TriangleAlert,
  X,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { FormEvent } from "react"
import { Link, useNavigate } from "react-router-dom"

import { ProblemDetailsError } from "../api/client"
import { createCohort, listCohorts } from "./api"
import type { Cohort, CohortFilters, CohortInput, CohortStatus } from "./api"
import {
  formatShanghaiDateTime,
  nextCalendarDate,
  shanghaiDateEndExclusive,
  shanghaiDateStart,
  shanghaiLocalToIso,
} from "./datetime"
import { AUTOMATION_ISSUE_LABELS, COHORT_STATUS_LABELS } from "./labels"
import { TeachingSectionNav } from "./TeachingSectionNav"
import "./teaching.css"

const PAGE_SIZE = 20

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function filtersOpenByDefault(): boolean {
  return window.innerWidth >= 720
}

function CohortCreateDialog({
  pending,
  error,
  onClose,
  onSubmit,
}: {
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: CohortInput) => void
}) {
  const [name, setName] = useState("")
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  const [startsAt, setStartsAt] = useState("")
  const [endsAt, setEndsAt] = useState("")
  const [checkInAt, setCheckInAt] = useState("")
  const [checkOutAt, setCheckOutAt] = useState("")
  const [autoStart, setAutoStart] = useState(false)
  const [autoComplete, setAutoComplete] = useState(false)
  const [validationError, setValidationError] = useState<string | null>(null)

  useEffect(() => {
    if (pending) return
    function closeOnEscape(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", closeOnEscape)
    return () => window.removeEventListener("keydown", closeOnEscape)
  }, [onClose, pending])

  function changeStartDate(value: string): void {
    setStartDate(value)
    if (!value) return
    const local = `${value}T00:00`
    setStartsAt(local)
    setCheckInAt(local)
  }

  function changeEndDate(value: string): void {
    setEndDate(value)
    if (!value) return
    const local = `${nextCalendarDate(value)}T00:00`
    setEndsAt(local)
    setCheckOutAt(local)
  }

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    try {
      setValidationError(null)
      onSubmit({
        name: name.trim(),
        starts_at: shanghaiLocalToIso(startsAt),
        ends_at: shanghaiLocalToIso(endsAt),
        default_check_in_at: shanghaiLocalToIso(checkInAt),
        default_check_out_at: shanghaiLocalToIso(checkOutAt),
        auto_start_enabled: autoStart,
        auto_complete_enabled: autoComplete,
      })
    } catch (caught) {
      setValidationError(caught instanceof Error ? caught.message : "时间填写无效")
    }
  }

  return (
    <div className="teachingDialogBackdrop" onMouseDown={pending ? undefined : onClose}>
      <section
        className="teachingDialog cohortDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cohort-create-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="teachingDialogHeader">
          <h2 id="cohort-create-title">新增营期</h2>
          <button
            type="button"
            aria-label="关闭营期表单"
            title="关闭"
            disabled={pending}
            onClick={onClose}
          >
            <X aria-hidden="true" />
          </button>
        </header>
        <form className="teachingDialogForm cohortForm" onSubmit={submit}>
          <label className="cohortNameField">
            <span>营期名称</span>
            <input
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={120}
              disabled={pending}
              autoFocus
              required
            />
          </label>
          <div className="cohortDateGrid">
            <label>
              <span>开营日期</span>
              <input
                type="date"
                value={startDate}
                onChange={(event) => changeStartDate(event.target.value)}
                disabled={pending}
                required
              />
            </label>
            <label>
              <span>结营日期</span>
              <input
                type="date"
                value={endDate}
                min={startDate || undefined}
                onChange={(event) => changeEndDate(event.target.value)}
                disabled={pending}
                required
              />
            </label>
          </div>
          <div className="cohortDateGrid">
            <label>
              <span>开营时间</span>
              <input
                type="datetime-local"
                value={startsAt}
                onChange={(event) => setStartsAt(event.target.value)}
                disabled={pending}
                required
              />
            </label>
            <label>
              <span>结营时间</span>
              <input
                type="datetime-local"
                value={endsAt}
                onChange={(event) => setEndsAt(event.target.value)}
                disabled={pending}
                required
              />
            </label>
          </div>
          <div className="cohortDateGrid">
            <label>
              <span>默认入住时间</span>
              <input
                type="datetime-local"
                value={checkInAt}
                onChange={(event) => setCheckInAt(event.target.value)}
                disabled={pending}
                required
              />
            </label>
            <label>
              <span>默认离营时间</span>
              <input
                type="datetime-local"
                value={checkOutAt}
                onChange={(event) => setCheckOutAt(event.target.value)}
                disabled={pending}
                required
              />
            </label>
          </div>
          <div className="cohortToggleGrid">
            <label className="teachingSwitch">
              <input
                type="checkbox"
                checked={autoStart}
                onChange={(event) => setAutoStart(event.target.checked)}
                disabled={pending}
              />
              <span>自动开营</span>
            </label>
            <label className="teachingSwitch">
              <input
                type="checkbox"
                checked={autoComplete}
                onChange={(event) => setAutoComplete(event.target.checked)}
                disabled={pending}
              />
              <span>自动结营</span>
            </label>
          </div>
          {validationError || error ? (
            <div className="teachingFormError" role="alert">{validationError ?? error}</div>
          ) : null}
          <footer className="teachingDialogActions">
            <button
              type="button"
              className="teachingSecondaryButton"
              onClick={onClose}
              disabled={pending}
            >
              取消
            </button>
            <button type="submit" className="teachingPrimaryButton" disabled={pending}>
              {pending ? "正在保存" : "保存营期"}
            </button>
          </footer>
        </form>
      </section>
    </div>
  )
}

function AutomationSummary({ cohort }: { cohort: Cohort }) {
  return (
    <div className="cohortAutomationSummary">
      <span className={cohort.auto_start_enabled ? "enabled" : "disabled"}>
        <RefreshCw aria-hidden="true" />自动开营 {cohort.auto_start_enabled ? "开启" : "关闭"}
      </span>
      <span className={cohort.auto_complete_enabled ? "enabled" : "disabled"}>
        <RefreshCw aria-hidden="true" />自动结营 {cohort.auto_complete_enabled ? "开启" : "关闭"}
      </span>
    </div>
  )
}

export function CohortsPage({
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [status, setStatus] = useState<CohortStatus | "">("")
  const [dateFrom, setDateFrom] = useState("")
  const [dateTo, setDateTo] = useState("")
  const [filters, setFilters] = useState<CohortFilters>({ page: 1, pageSize: PAGE_SIZE })
  const [filterOpen, setFilterOpen] = useState(filtersOpenByDefault)
  const [creating, setCreating] = useState(false)

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const media = window.matchMedia("(min-width: 720px)")
    const synchronize = (event: MediaQueryListEvent) => setFilterOpen(event.matches)
    media.addEventListener("change", synchronize)
    return () => media.removeEventListener("change", synchronize)
  }, [])

  const cohorts = useQuery({
    queryKey: ["cohorts", filters],
    queryFn: () => listCohorts(filters),
  })
  const createMutation = useMutation({
    mutationFn: (input: CohortInput) => createCohort(input),
    onSuccess: async (created) => {
      setCreating(false)
      await queryClient.invalidateQueries({ queryKey: ["cohorts"] })
      navigate(`/teaching/cohorts/${created.id}`)
    },
  })

  function applyFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setFilters({
      name: name.trim() || undefined,
      status: status || undefined,
      dateFrom: dateFrom ? shanghaiDateStart(dateFrom) : undefined,
      dateTo: dateTo ? shanghaiDateEndExclusive(dateTo) : undefined,
      page: 1,
      pageSize: PAGE_SIZE,
    })
  }

  const canManage = permissions.has("cohorts.manage")
  const currentPage = filters.page ?? 1
  const pages = Math.max(1, Math.ceil((cohorts.data?.total ?? 0) / PAGE_SIZE))

  return (
    <div className="teachingCohortsPage">
      <TeachingSectionNav
        permissions={permissions}
        scopedPermissions={scopedPermissions}
      />
      <header className="teachingPageHeader">
        <div>
          <p className="teachingEyebrow">招生教学</p>
          <h1>营期管理</h1>
        </div>
        <div className="teachingHeaderActions">
          {canManage ? (
            <button
              type="button"
              className="teachingPrimaryButton"
              onClick={() => {
                createMutation.reset()
                setCreating(true)
              }}
            >
              <Plus aria-hidden="true" />新增营期
            </button>
          ) : null}
        </div>
      </header>

      <details
        className="teachingFilterMenu cohortFilterMenu"
        open={filterOpen}
        onToggle={(event) => setFilterOpen(event.currentTarget.open)}
      >
        <summary><SlidersHorizontal aria-hidden="true" />筛选营期</summary>
        <form className="teachingFilters cohortFilters" onSubmit={applyFilters}>
          <label>
            <span>营期名称</span>
            <input value={name} onChange={(event) => setName(event.target.value)} />
          </label>
          <label>
            <span>营期状态</span>
            <select value={status} onChange={(event) => setStatus(event.target.value as CohortStatus | "")}>
              <option value="">全部状态</option>
              {Object.entries(COHORT_STATUS_LABELS).map(([value, label]) => (
                <option key={value} value={value}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            <span>开营日期从</span>
            <input type="date" value={dateFrom} onChange={(event) => setDateFrom(event.target.value)} />
          </label>
          <label>
            <span>开营日期至</span>
            <input type="date" value={dateTo} onChange={(event) => setDateTo(event.target.value)} />
          </label>
          <button type="submit" className="teachingSecondaryButton">
            <Search aria-hidden="true" />查询营期
          </button>
        </form>
      </details>

      <section className="cohortListSurface" aria-label="营期列表">
        {cohorts.isLoading ? <div className="teachingEmptyState">正在读取营期…</div> : null}
        {cohorts.isError ? <div className="teachingEmptyState">暂时无法读取营期列表。</div> : null}
        {cohorts.data?.items.length === 0 ? (
          <div className="teachingEmptyState">没有符合条件的营期。</div>
        ) : null}
        {cohorts.data?.items.map((item) => (
          <article className="cohortListRow" key={item.id} data-testid="cohort-row">
            <div className="cohortListIdentity" data-label="营期">
              <Link to={`/teaching/cohorts/${item.id}`}>{item.name}</Link>
              <span className={`cohortStatus status-${item.status}`}>
                {COHORT_STATUS_LABELS[item.status]}
              </span>
            </div>
            <div className="cohortSchedule" data-label="营期时间">
              <span><CalendarDays aria-hidden="true" />{formatShanghaiDateTime(item.starts_at)}</span>
              <span><Clock3 aria-hidden="true" />{formatShanghaiDateTime(item.ends_at)}</span>
            </div>
            <div data-label="自动化">
              <AutomationSummary cohort={item} />
            </div>
            <div className="cohortReadiness" data-label="配置检查">
              <span className="cohortClassCount">
                {item.enabled_class_count} 个启用班级
              </span>
              {item.missing_primary_class_count > 0 ? (
                <span className="cohortWarning">
                  <TriangleAlert aria-hidden="true" />
                  {item.missing_primary_class_count} 个班级缺少主教练
                </span>
              ) : (
                <span className="cohortReady">主教练配置完整</span>
              )}
              {item.automation_issues.map((issue) => (
                <span className="cohortWarning" key={issue}>
                  <TriangleAlert aria-hidden="true" />{AUTOMATION_ISSUE_LABELS[issue]}
                </span>
              ))}
            </div>
          </article>
        ))}
        {cohorts.data ? (
          <nav className="teachingPagination" aria-label="营期分页">
            <span>第 {currentPage} / {pages} 页，共 {cohorts.data.total} 条</span>
            <div>
              <button
                type="button"
                className="teachingSecondaryButton"
                disabled={currentPage <= 1}
                onClick={() => setFilters((current) => ({ ...current, page: currentPage - 1 }))}
              >
                上一页
              </button>
              <button
                type="button"
                className="teachingSecondaryButton"
                disabled={currentPage >= pages}
                onClick={() => setFilters((current) => ({ ...current, page: currentPage + 1 }))}
              >
                下一页
              </button>
            </div>
          </nav>
        ) : null}
      </section>

      {creating ? (
        <CohortCreateDialog
          pending={createMutation.isPending}
          error={createMutation.error ? errorMessage(createMutation.error, "营期暂时无法保存") : null}
          onClose={() => {
            if (createMutation.isPending) return
            createMutation.reset()
            setCreating(false)
          }}
          onSubmit={(input) => createMutation.mutate(input)}
        />
      ) : null}
    </div>
  )
}
