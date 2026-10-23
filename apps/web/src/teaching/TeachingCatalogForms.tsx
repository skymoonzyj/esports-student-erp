import { X } from "lucide-react"
import { useEffect, useState } from "react"
import type { FormEvent, ReactNode } from "react"

import type { GameProject, ProfessionalTeam, ProjectCoachConfiguration } from "./api"

export type ProjectDialogState = { mode: "create" } | { mode: "edit"; project: GameProject }
export type TeamDialogState = { mode: "create" } | { mode: "edit"; team: ProfessionalTeam }

function useEscapeToClose(onClose: () => void, enabled: boolean): void {
  useEffect(() => {
    if (!enabled) return
    function close(event: KeyboardEvent): void {
      if (event.key === "Escape") onClose()
    }
    window.addEventListener("keydown", close)
    return () => window.removeEventListener("keydown", close)
  }, [enabled, onClose])
}

function CatalogDialog({
  title,
  closeLabel,
  pending,
  onClose,
  children,
}: {
  title: string
  closeLabel: string
  pending: boolean
  onClose: () => void
  children: ReactNode
}) {
  const titleId = `catalog-dialog-${title === "新增项目" || title === "编辑项目" ? "project" : "team"}`
  useEscapeToClose(onClose, !pending)

  return (
    <div
      className="teachingDialogBackdrop"
      onMouseDown={pending ? undefined : onClose}
    >
      <section
        className="teachingDialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="teachingDialogHeader">
          <h2 id={titleId}>{title}</h2>
          <button
            type="button"
            aria-label={closeLabel}
            title="关闭"
            onClick={onClose}
            disabled={pending}
          >
            <X aria-hidden="true" />
          </button>
        </header>
        {children}
      </section>
    </div>
  )
}

export function PageControls({
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
  const pages = Math.max(1, Math.ceil(total / pageSize))
  return (
    <nav className="teachingPagination" aria-label="列表分页">
      <span>
        第 {page} / {pages} 页，共 {total} 条
      </span>
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
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </nav>
  )
}

export function ProjectForm({
  state,
  canEditDivision,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  state: ProjectDialogState
  canEditDivision: boolean
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: { name: string; sort_order: number; is_team_division?: boolean }) => void
}) {
  const existing = state.mode === "edit" ? state.project : null
  const [name, setName] = useState(existing?.name ?? "")
  const [sortOrder, setSortOrder] = useState(String(existing?.sort_order ?? 0))
  const [isTeamDivision, setIsTeamDivision] = useState(existing?.is_team_division ?? false)
  const title = state.mode === "create" ? "新增项目" : "编辑项目"

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    onSubmit({
      name: name.trim(),
      sort_order: Number(sortOrder),
      ...(canEditDivision && (state.mode === "create" || isTeamDivision !== existing?.is_team_division)
        ? { is_team_division: isTeamDivision }
        : {}),
    })
  }

  return (
    <CatalogDialog
      title={title}
      closeLabel="关闭项目表单"
      pending={pending}
      onClose={onClose}
    >
      <form className="teachingDialogForm" onSubmit={submit}>
        <label>
          <span>项目名称</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={pending}
            maxLength={80}
            autoFocus
            required
          />
        </label>
        <label>
          <span>排序</span>
          <input
            type="number"
            value={sortOrder}
            onChange={(event) => setSortOrder(event.target.value)}
            disabled={pending}
            min={0}
            step={1}
            required
          />
        </label>
        {canEditDivision ? <label className="teachingCheckbox">
          <input type="checkbox" checked={isTeamDivision} disabled={pending} onChange={(event) => setIsTeamDivision(event.target.checked)} />
          <span>用作队伍分部</span>
        </label> : null}
        {error ? <div className="teachingFormError" role="alert">{error}</div> : null}
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
            {pending ? "正在保存" : "保存项目"}
          </button>
        </footer>
      </form>
    </CatalogDialog>
  )
}

export function ProjectCoachForm({
  project,
  configuration,
  loading,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  project: GameProject
  configuration: ProjectCoachConfiguration | undefined
  loading: boolean
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (coachPersonIds: string[]) => void
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set())

  useEffect(() => {
    if (!configuration) return
    setSelected(new Set(
      configuration.coaches.filter((coach) => coach.assigned).map((coach) => coach.person_id),
    ))
  }, [configuration])

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    if (!configuration) return
    onSubmit([...selected])
  }

  return (
    <CatalogDialog
      title={`配置教练 · ${project.name}`}
      closeLabel="关闭教练配置"
      pending={pending}
      onClose={onClose}
    >
      <form className="teachingDialogForm" onSubmit={submit}>
        <p className="projectCoachHint">勾选后，报名该项目的学员只能从这些教练中选择。</p>
        {loading ? <div className="projectCoachState">正在读取可用教练…</div> : null}
        {!loading && !configuration ? (
          <div className="teachingFormError" role="alert">暂时无法读取教练名单</div>
        ) : null}
        {configuration && configuration.coaches.length === 0 ? (
          <div className="projectCoachState">当前没有账号状态正常且已授予教练角色的人员。</div>
        ) : null}
        {configuration && configuration.coaches.length > 0 ? (
          <fieldset className="projectCoachOptions">
            <legend>可报名教练</legend>
            {configuration.coaches.map((coach) => (
              <label className="projectCoachOption" key={coach.person_id}>
                <input
                  type="checkbox"
                  checked={selected.has(coach.person_id)}
                  disabled={pending}
                  onChange={(event) => {
                    setSelected((current) => {
                      const next = new Set(current)
                      if (event.target.checked) next.add(coach.person_id)
                      else next.delete(coach.person_id)
                      return next
                    })
                  }}
                />
                <span>{coach.full_name}</span>
              </label>
            ))}
          </fieldset>
        ) : null}
        {error ? <div className="teachingFormError" role="alert">{error}</div> : null}
        <footer className="teachingDialogActions">
          <button
            type="button"
            className="teachingSecondaryButton"
            onClick={onClose}
            disabled={pending}
          >
            取消
          </button>
          <button
            type="submit"
            className="teachingPrimaryButton"
            disabled={pending || !configuration}
          >
            {pending ? "正在保存" : `保存教练（${selected.size}）`}
          </button>
        </footer>
      </form>
    </CatalogDialog>
  )
}

export function TeamForm({
  state,
  projects,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  state: TeamDialogState
  projects: readonly GameProject[]
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (input: { project_id: string; name: string; notes: string | null }) => void
}) {
  const existing = state.mode === "edit" ? state.team : null
  const firstActiveProject = projects.find((project) => project.is_active)?.id ?? ""
  const [projectId, setProjectId] = useState(existing?.project_id ?? firstActiveProject)
  const [name, setName] = useState(existing?.name ?? "")
  const [notes, setNotes] = useState(existing?.notes ?? "")
  const title = state.mode === "create" ? "新增队伍" : "编辑队伍"

  function submit(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    onSubmit({
      project_id: projectId,
      name: name.trim(),
      notes: notes.trim() || null,
    })
  }

  return (
    <CatalogDialog
      title={title}
      closeLabel="关闭队伍表单"
      pending={pending}
      onClose={onClose}
    >
      <form className="teachingDialogForm" onSubmit={submit}>
        <label>
          <span>所属项目</span>
          <select
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            disabled={pending || existing !== null}
            required
          >
            <option value="" disabled>请选择项目</option>
            {projects.map((project) => (
              <option
                key={project.id}
                value={project.id}
                disabled={!project.is_active && project.id !== existing?.project_id}
              >
                {project.name}{project.is_active ? "" : "（已停用）"}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>队伍名称</span>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={pending}
            maxLength={120}
            autoFocus
            required
          />
        </label>
        <label>
          <span>备注</span>
          <textarea
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            disabled={pending}
            rows={4}
            maxLength={500}
          />
        </label>
        {error ? <div className="teachingFormError" role="alert">{error}</div> : null}
        <footer className="teachingDialogActions">
          <button
            type="button"
            className="teachingSecondaryButton"
            onClick={onClose}
            disabled={pending}
          >
            取消
          </button>
          <button type="submit" className="teachingPrimaryButton" disabled={pending || !projectId}>
            {pending ? "正在保存" : "保存队伍"}
          </button>
        </footer>
      </form>
    </CatalogDialog>
  )
}
