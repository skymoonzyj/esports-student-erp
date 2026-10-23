import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  Pencil,
  Plus,
  Power,
  PowerOff,
  Search,
  SlidersHorizontal,
  UsersRound,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { FormEvent } from "react"

import { ProblemDetailsError } from "../api/client"
import { useOptionalAuth } from "../auth/AuthProvider"
import { getTeamContext } from "../teams/api"
import {
  createProfessionalTeam,
  createProject,
  getProjectCoaches,
  listProfessionalTeams,
  listProjects,
  replaceProjectCoaches,
  updateProfessionalTeam,
  updateProject,
} from "./api"
import type {
  GameProject,
  Page,
  ProfessionalTeam,
  ProjectFilters,
  TeamFilters,
} from "./api"
import { PageControls, ProjectCoachForm, ProjectForm, TeamForm } from "./TeachingCatalogForms"
import type { ProjectDialogState, TeamDialogState } from "./TeachingCatalogForms"
import { TeachingSectionNav } from "./TeachingSectionNav"
import "./teaching.css"

type CatalogTab = "projects" | "teams"

const PAGE_SIZE = 20
const PROJECT_OPTIONS_PAGE_SIZE = 100

function filtersOpenByDefault(): boolean {
  return window.innerWidth >= 720
}

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof ProblemDetailsError ? error.problem.detail : fallback
}

function activeFirst<T extends { is_active: boolean; name: string }>(
  items: readonly T[],
  secondary: (left: T, right: T) => number = (left, right) =>
    left.name.localeCompare(right.name, "zh-CN"),
): T[] {
  return [...items].sort((left, right) => {
    if (left.is_active !== right.is_active) return left.is_active ? -1 : 1
    return secondary(left, right)
  })
}

function compareProjects(left: GameProject, right: GameProject): number {
  return left.sort_order === right.sort_order
    ? left.name.localeCompare(right.name, "zh-CN")
    : left.sort_order - right.sort_order
}


export function TeachingCatalogPage({
  permissions,
  scopedPermissions = new Set<string>(),
}: {
  permissions: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  const auth = useOptionalAuth()
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<CatalogTab>("projects")
  const [projectName, setProjectName] = useState("")
  const [projectIncludeInactive, setProjectIncludeInactive] = useState(true)
  const [projectFilters, setProjectFilters] = useState<ProjectFilters>({
    includeInactive: true,
    page: 1,
    pageSize: PAGE_SIZE,
  })
  const [teamName, setTeamName] = useState("")
  const [teamProjectId, setTeamProjectId] = useState("")
  const [teamIncludeInactive, setTeamIncludeInactive] = useState(true)
  const [teamFilters, setTeamFilters] = useState<TeamFilters>({
    includeInactive: true,
    page: 1,
    pageSize: PAGE_SIZE,
  })
  const [projectDialog, setProjectDialog] = useState<ProjectDialogState | null>(null)
  const [coachProject, setCoachProject] = useState<GameProject | null>(null)
  const [teamDialog, setTeamDialog] = useState<TeamDialogState | null>(null)
  const [pageError, setPageError] = useState<string | null>(null)
  const [projectFilterOpen, setProjectFilterOpen] = useState(filtersOpenByDefault)
  const [teamFilterOpen, setTeamFilterOpen] = useState(filtersOpenByDefault)

  useEffect(() => {
    if (typeof window.matchMedia !== "function") return
    const media = window.matchMedia("(min-width: 720px)")
    function synchronizeFilters(event: MediaQueryListEvent): void {
      setProjectFilterOpen(event.matches)
      setTeamFilterOpen(event.matches)
    }
    media.addEventListener("change", synchronizeFilters)
    return () => media.removeEventListener("change", synchronizeFilters)
  }, [])

  const projects = useQuery({
    queryKey: ["projects", projectFilters],
    queryFn: () => listProjects(projectFilters),
  })
  const projectOptionFilters = useMemo<ProjectFilters>(
    () => ({ includeInactive: true, page: 1, pageSize: PROJECT_OPTIONS_PAGE_SIZE }),
    [],
  )
  const projectOptions = useQuery({
    queryKey: ["projects", projectOptionFilters],
    queryFn: () => listProjects(projectOptionFilters),
    enabled: tab === "teams" || teamDialog !== null,
  })
  const teams = useQuery({
    queryKey: ["professional-teams", teamFilters],
    queryFn: () => listProfessionalTeams(teamFilters),
    enabled: tab === "teams",
  })
  const projectCoaches = useQuery({
    queryKey: ["project-coaches", coachProject?.id],
    queryFn: () => getProjectCoaches(coachProject!.id),
    enabled: coachProject !== null,
  })
  const canManageProjects = permissions.has("projects.manage")
  const divisionContext = useQuery({
    queryKey: ["teams", auth?.user?.account_id ?? "", "context"],
    queryFn: getTeamContext,
    enabled: Boolean(auth?.user?.is_super_admin && canManageProjects),
    refetchOnWindowFocus: "always",
  })
  const canEditDivision = Boolean(auth?.user?.is_super_admin && canManageProjects && divisionContext.data?.can_manage_catalog)

  const orderedProjects = useMemo(
    () => activeFirst(projects.data?.items ?? [], compareProjects),
    [projects.data?.items],
  )
  const orderedProjectOptions = useMemo(
    () => activeFirst(projectOptions.data?.items ?? [], compareProjects),
    [projectOptions.data?.items],
  )
  const orderedTeams = useMemo(
    () => activeFirst(teams.data?.items ?? []),
    [teams.data?.items],
  )
  const projectNames = useMemo(
    () => new Map(orderedProjectOptions.map((project) => [project.id, project.name])),
    [orderedProjectOptions],
  )

  const projectSave = useMutation({
    mutationFn: ({
      state,
      input,
    }: {
      state: ProjectDialogState
      input: { name: string; sort_order: number; is_team_division?: boolean }
    }) =>
      state.mode === "create"
        ? createProject(input)
        : updateProject(state.project.id, input),
    onSuccess: async () => {
      setProjectDialog(null)
      await queryClient.invalidateQueries({ queryKey: ["projects"] })
    },
  })
  const projectStatus = useMutation({
    mutationFn: ({ project, isActive }: { project: GameProject; isActive: boolean }) =>
      updateProject(project.id, { is_active: isActive }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["projects"] })
    },
    onError: (error) => setPageError(errorMessage(error, "项目状态暂时无法修改")),
  })
  const teamSave = useMutation({
    mutationFn: ({
      state,
      input,
    }: {
      state: TeamDialogState
      input: { project_id: string; name: string; notes: string | null }
    }) =>
      state.mode === "create"
        ? createProfessionalTeam(input)
        : updateProfessionalTeam(state.team.id, {
            name: input.name,
            notes: input.notes,
          }),
    onSuccess: async () => {
      setTeamDialog(null)
      await queryClient.invalidateQueries({ queryKey: ["professional-teams"] })
    },
  })
  const teamStatus = useMutation({
    mutationFn: ({ team, isActive }: { team: ProfessionalTeam; isActive: boolean }) =>
      updateProfessionalTeam(team.id, { is_active: isActive }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["professional-teams"] })
    },
    onError: (error) => setPageError(errorMessage(error, "队伍状态暂时无法修改")),
  })
  const projectCoachSave = useMutation({
    mutationFn: ({ projectId, coachPersonIds }: {
      projectId: string
      coachPersonIds: string[]
    }) => replaceProjectCoaches(projectId, coachPersonIds),
    onSuccess: (configuration) => {
      queryClient.setQueryData(
        ["project-coaches", configuration.project_id],
        configuration,
      )
      setCoachProject(null)
    },
  })

  const canManageProjectCoaches = permissions.has("project_coaches.manage")
  const canManageTeams = permissions.has("professional_teams.manage")

  function applyProjectFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setProjectFilters({
      name: projectName.trim() || undefined,
      includeInactive: projectIncludeInactive,
      page: 1,
      pageSize: PAGE_SIZE,
    })
  }

  function applyTeamFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault()
    setTeamFilters({
      projectId: teamProjectId || undefined,
      name: teamName.trim() || undefined,
      includeInactive: teamIncludeInactive,
      page: 1,
      pageSize: PAGE_SIZE,
    })
  }

  function closeProjectDialog(): void {
    projectSave.reset()
    setProjectDialog(null)
  }

  function closeTeamDialog(): void {
    teamSave.reset()
    setTeamDialog(null)
  }

  function closeProjectCoachDialog(): void {
    projectCoachSave.reset()
    setCoachProject(null)
  }

  function toggleProject(project: GameProject): void {
    const next = !project.is_active
    if (!window.confirm(`确认${next ? "启用" : "停用"}项目“${project.name}”？`)) return
    setPageError(null)
    projectStatus.mutate({ project, isActive: next })
  }

  function toggleTeam(team: ProfessionalTeam): void {
    const next = !team.is_active
    if (!window.confirm(`确认${next ? "启用" : "停用"}队伍“${team.name}”？`)) return
    setPageError(null)
    teamStatus.mutate({ team, isActive: next })
  }

  const currentPage: Page<unknown> | undefined = tab === "projects" ? projects.data : teams.data

  return (
    <div className="teachingCatalogPage">
      <TeachingSectionNav
        permissions={permissions}
        scopedPermissions={scopedPermissions}
      />
      <header className="teachingPageHeader">
        <div>
          <p className="teachingEyebrow">招生教学</p>
          <h1>项目与职业队伍</h1>
        </div>
        {tab === "projects" && canManageProjects ? (
          <button
            type="button"
            className="teachingPrimaryButton"
            onClick={() => {
              projectSave.reset()
              setProjectDialog({ mode: "create" })
            }}
          >
            <Plus aria-hidden="true" />
            新增项目
          </button>
        ) : null}
        {tab === "teams" && canManageTeams ? (
          <button
            type="button"
            className="teachingPrimaryButton"
            onClick={() => {
              teamSave.reset()
              setTeamDialog({ mode: "create" })
            }}
          >
            <Plus aria-hidden="true" />
            新增队伍
          </button>
        ) : null}
      </header>

      <div className="teachingTabs" role="tablist" aria-label="教学目录">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "projects"}
          onClick={() => setTab("projects")}
        >
          电竞项目
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "teams"}
          onClick={() => setTab("teams")}
        >
          职业队伍
        </button>
      </div>

      {tab === "projects" ? (
        <details
          className="teachingFilterMenu"
          open={projectFilterOpen}
          onToggle={(event) => setProjectFilterOpen(event.currentTarget.open)}
        >
          <summary><SlidersHorizontal aria-hidden="true" />筛选项目</summary>
          <form className="teachingFilters projectFilters" onSubmit={applyProjectFilters}>
            <label>
              <span>项目名称</span>
              <input value={projectName} onChange={(event) => setProjectName(event.target.value)} />
            </label>
            <label className="teachingCheckbox">
              <input
                type="checkbox"
                checked={projectIncludeInactive}
                onChange={(event) => setProjectIncludeInactive(event.target.checked)}
              />
              <span>包含已停用项目</span>
            </label>
            <button type="submit" className="teachingSecondaryButton">
              <Search aria-hidden="true" />查询项目
            </button>
          </form>
        </details>
      ) : (
        <details
          className="teachingFilterMenu"
          open={teamFilterOpen}
          onToggle={(event) => setTeamFilterOpen(event.currentTarget.open)}
        >
          <summary><SlidersHorizontal aria-hidden="true" />筛选队伍</summary>
          <form className="teachingFilters teamFilters" onSubmit={applyTeamFilters}>
            <label>
              <span>所属项目</span>
              <select
                value={teamProjectId}
                onChange={(event) => setTeamProjectId(event.target.value)}
              >
                <option value="">全部项目</option>
                {orderedProjectOptions.map((project) => (
                  <option key={project.id} value={project.id}>{project.name}</option>
                ))}
              </select>
            </label>
            <label>
              <span>队伍名称</span>
              <input value={teamName} onChange={(event) => setTeamName(event.target.value)} />
            </label>
            <label className="teachingCheckbox">
              <input
                type="checkbox"
                checked={teamIncludeInactive}
                onChange={(event) => setTeamIncludeInactive(event.target.checked)}
              />
              <span>包含已停用队伍</span>
            </label>
            <button type="submit" className="teachingSecondaryButton">
              <Search aria-hidden="true" />查询队伍
            </button>
          </form>
        </details>
      )}

      {pageError ? <div className="teachingPageError" role="alert">{pageError}</div> : null}

      {tab === "projects" ? (
        <section className="teachingDataSurface" role="table" aria-label="电竞项目列表">
          <div className="teachingTableHeader projectTableGrid" role="row">
            <span role="columnheader">项目名称</span>
            <span role="columnheader">排序</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">操作</span>
          </div>
          {projects.isLoading ? <div className="teachingEmptyState">正在读取项目…</div> : null}
          {projects.isError ? <div className="teachingEmptyState">暂时无法读取项目列表。</div> : null}
          {projects.data && orderedProjects.length === 0 ? (
            <div className="teachingEmptyState">没有符合条件的项目。</div>
          ) : null}
          {orderedProjects.map((project) => (
            <div
              className="teachingTableRow projectTableGrid"
              role="row"
              data-testid="project-row"
              key={project.id}
            >
              <span role="cell" data-label="项目" aria-label={`项目 ${project.name}`}>
                <strong>{project.name}</strong>
              </span>
              <span role="cell" data-label="排序" aria-label={`排序 ${project.sort_order}`}>
                {project.sort_order}
              </span>
              <span
                role="cell"
                data-label="状态"
                aria-label={`状态 ${project.is_active ? "启用" : "停用"}`}
              >
                <span className={`teachingStatus ${project.is_active ? "active" : "inactive"}`}>
                  {project.is_active ? "启用" : "停用"}
                </span>
              </span>
              <span role="cell" data-label="操作" className="teachingRowActions">
                {canManageProjects || canManageProjectCoaches ? (
                  <>
                    {canManageProjectCoaches ? (
                      <button
                        type="button"
                        aria-label={`配置 ${project.name} 的教练`}
                        title="配置可报名教练"
                        onClick={() => {
                          projectCoachSave.reset()
                          setCoachProject(project)
                        }}
                      >
                        <UsersRound aria-hidden="true" />
                      </button>
                    ) : null}
                    {canManageProjects ? (
                      <>
                        <button
                          type="button"
                          aria-label={`编辑 ${project.name}`}
                          title="编辑项目"
                          onClick={() => {
                            projectSave.reset()
                            setProjectDialog({ mode: "edit", project })
                          }}
                        >
                          <Pencil aria-hidden="true" />
                        </button>
                        <button
                          type="button"
                          aria-label={`${project.is_active ? "停用" : "启用"} ${project.name}`}
                          title={project.is_active ? "停用项目" : "启用项目"}
                          disabled={projectStatus.isPending}
                          onClick={() => toggleProject(project)}
                        >
                          {project.is_active ? <PowerOff aria-hidden="true" /> : <Power aria-hidden="true" />}
                        </button>
                      </>
                    ) : null}
                  </>
                ) : (
                  <span className="teachingReadOnly">仅查看</span>
                )}
              </span>
            </div>
          ))}
          {projects.data ? (
            <PageControls
              page={projectFilters.page ?? 1}
              pageSize={projectFilters.pageSize ?? PAGE_SIZE}
              total={projects.data.total}
              onPageChange={(page) => setProjectFilters((current) => ({ ...current, page }))}
            />
          ) : null}
        </section>
      ) : (
        <section className="teachingDataSurface" role="table" aria-label="职业队伍列表">
          <div className="teachingTableHeader teamTableGrid" role="row">
            <span role="columnheader">队伍名称</span>
            <span role="columnheader">所属项目</span>
            <span role="columnheader">备注</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">操作</span>
          </div>
          {teams.isLoading ? <div className="teachingEmptyState">正在读取队伍…</div> : null}
          {teams.isError ? <div className="teachingEmptyState">暂时无法读取队伍列表。</div> : null}
          {teams.data && orderedTeams.length === 0 ? (
            <div className="teachingEmptyState">没有符合条件的职业队伍。</div>
          ) : null}
          {orderedTeams.map((team) => (
            <div
              className="teachingTableRow teamTableGrid"
              role="row"
              data-testid="team-row"
              key={team.id}
            >
              <span role="cell" data-label="队伍" aria-label={`队伍 ${team.name}`}>
                <strong>{team.name}</strong>
              </span>
              <span
                role="cell"
                data-label="所属项目"
                aria-label={`所属项目 ${projectNames.get(team.project_id) ?? team.project_id}`}
              >
                {projectNames.get(team.project_id) ?? "未知项目"}
              </span>
              <span role="cell" data-label="备注" aria-label={`备注 ${team.notes ?? "无"}`}>
                {team.notes ?? "—"}
              </span>
              <span
                role="cell"
                data-label="状态"
                aria-label={`状态 ${team.is_active ? "启用" : "停用"}`}
              >
                <span className={`teachingStatus ${team.is_active ? "active" : "inactive"}`}>
                  {team.is_active ? "启用" : "停用"}
                </span>
              </span>
              <span role="cell" data-label="操作" className="teachingRowActions">
                {canManageTeams ? (
                  <>
                    <button
                      type="button"
                      aria-label={`编辑 ${team.name}`}
                      title="编辑队伍"
                      onClick={() => {
                        teamSave.reset()
                        setTeamDialog({ mode: "edit", team })
                      }}
                    >
                      <Pencil aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`${team.is_active ? "停用" : "启用"} ${team.name}`}
                      title={team.is_active ? "停用队伍" : "启用队伍"}
                      disabled={teamStatus.isPending}
                      onClick={() => toggleTeam(team)}
                    >
                      {team.is_active ? <PowerOff aria-hidden="true" /> : <Power aria-hidden="true" />}
                    </button>
                  </>
                ) : (
                  <span className="teachingReadOnly">仅查看</span>
                )}
              </span>
            </div>
          ))}
          {currentPage ? (
            <PageControls
              page={teamFilters.page ?? 1}
              pageSize={teamFilters.pageSize ?? PAGE_SIZE}
              total={currentPage.total}
              onPageChange={(page) => setTeamFilters((current) => ({ ...current, page }))}
            />
          ) : null}
        </section>
      )}

      {projectDialog ? (
        <ProjectForm
          state={projectDialog}
          canEditDivision={canEditDivision}
          pending={projectSave.isPending}
          error={projectSave.error ? errorMessage(projectSave.error, "项目暂时无法保存") : null}
          onClose={closeProjectDialog}
          onSubmit={(input) => projectSave.mutate({ state: projectDialog, input })}
        />
      ) : null}
      {teamDialog ? (
        <TeamForm
          state={teamDialog}
          projects={orderedProjectOptions}
          pending={teamSave.isPending}
          error={teamSave.error ? errorMessage(teamSave.error, "队伍暂时无法保存") : null}
          onClose={closeTeamDialog}
          onSubmit={(input) => teamSave.mutate({ state: teamDialog, input })}
        />
      ) : null}
      {coachProject ? (
        <ProjectCoachForm
          project={coachProject}
          configuration={projectCoaches.data}
          loading={projectCoaches.isLoading}
          pending={projectCoachSave.isPending}
          error={projectCoachSave.error
            ? errorMessage(projectCoachSave.error, "项目教练暂时无法保存")
            : null}
          onClose={closeProjectCoachDialog}
          onSubmit={(coachPersonIds) => projectCoachSave.mutate({
            projectId: coachProject.id,
            coachPersonIds,
          })}
        />
      ) : null}
    </div>
  )
}
