import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { TeachingCatalogPage } from "./TeachingCatalogPage"

const auth = vi.hoisted(() => ({ isSuperAdmin: false }))
const teamApi = vi.hoisted(() => ({ getTeamContext: vi.fn() }))
vi.mock("../auth/AuthProvider", () => ({
  useOptionalAuth: () => ({ user: { account_id: "account-catalog", is_super_admin: auth.isSuperAdmin } }),
}))
vi.mock("../teams/api", () => teamApi)

const api = vi.hoisted(() => ({
  listProjects: vi.fn(),
  createProject: vi.fn(),
  updateProject: vi.fn(),
  getProjectCoaches: vi.fn(),
  replaceProjectCoaches: vi.fn(),
  listProfessionalTeams: vi.fn(),
  createProfessionalTeam: vi.fn(),
  updateProfessionalTeam: vi.fn(),
}))

vi.mock("./api", () => api)

const activeProject = {
  id: "30000000-0000-4000-8000-000000000001",
  name: "英雄联盟",
  sort_order: 10,
  is_active: true,
  created_at: "2026-07-01T00:00:00+08:00",
  updated_at: "2026-07-01T00:00:00+08:00",
}
const inactiveProject = {
  ...activeProject,
  id: "30000000-0000-4000-8000-000000000002",
  name: "历史项目",
  sort_order: 1,
  is_active: false,
}
const desktopWidth = window.innerWidth

function renderPage(permissions: string[]) {
  return render(
    <MemoryRouter>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <TeachingCatalogPage permissions={new Set(permissions)} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.resetAllMocks()
  auth.isSuperAdmin = false
  teamApi.getTeamContext.mockResolvedValue({ can_open_management: false, can_read_self: false, can_manage_catalog: false, can_assign_managers: false, can_transfer: false })
  api.listProjects.mockResolvedValue({
    items: [inactiveProject, activeProject],
    page: 1,
    page_size: 20,
    total: 2,
  })
  api.listProfessionalTeams.mockResolvedValue({
    items: [],
    page: 1,
    page_size: 20,
    total: 0,
  })
  api.updateProject.mockImplementation(async (_id, input) => ({
    ...activeProject,
    ...input,
  }))
  api.createProject.mockResolvedValue({
    ...activeProject,
    id: "30000000-0000-4000-8000-000000000003",
    name: "新项目",
    sort_order: 30,
  })
  api.getProjectCoaches.mockResolvedValue({
    project_id: activeProject.id,
    project_name: activeProject.name,
    coaches: [
      { person_id: "coach-1", full_name: "张教练", assigned: false },
      { person_id: "coach-2", full_name: "李教练", assigned: true },
    ],
  })
  api.replaceProjectCoaches.mockResolvedValue({
    project_id: activeProject.id,
    project_name: activeProject.name,
    coaches: [
      { person_id: "coach-1", full_name: "张教练", assigned: true },
      { person_id: "coach-2", full_name: "李教练", assigned: true },
    ],
  })
})

it("shows the division toggle only to a real superadmin with catalog capability", async () => {
  const user = userEvent.setup()
  teamApi.getTeamContext.mockResolvedValue({ can_open_management: true, can_read_self: false, can_manage_catalog: true, can_assign_managers: true, can_transfer: true })
  renderPage(["teaching.read", "projects.manage"])
  await user.click(await screen.findByRole("button", { name: "新增项目" }))
  expect(screen.queryByRole("checkbox", { name: "用作队伍分部" })).not.toBeInTheDocument()

  auth.isSuperAdmin = true
  renderPage(["teaching.read", "projects.manage"])
  await user.click((await screen.findAllByRole("button", { name: "新增项目" }))[1]!)
  const dialogs = screen.getAllByRole("dialog", { name: "新增项目" })
  expect(await within(dialogs[1]!).findByRole("checkbox", { name: "用作队伍分部" })).toBeInTheDocument()
  await user.click(within(dialogs[1]!).getByRole("checkbox", { name: "用作队伍分部" }))
  await user.type(within(dialogs[1]!).getByLabelText("项目名称"), "三角洲行动")
  await user.click(within(dialogs[1]!).getByRole("button", { name: "保存项目" }))
  await waitFor(() => expect(api.createProject).toHaveBeenCalledWith({ name: "三角洲行动", sort_order: 0, is_team_division: true }))
})

afterEach(() => {
  vi.unstubAllGlobals()
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: desktopWidth,
  })
})

it("orders active projects first, applies exact filters, and keeps readers read-only", async () => {
  const user = userEvent.setup()
  renderPage(["teaching.read"])

  const rows = await screen.findAllByTestId("project-row")
  expect(rows.map((row) => within(row).getByRole("cell", { name: /项目/ }).textContent)).toEqual([
    "英雄联盟",
    "历史项目",
  ])
  expect(screen.queryByRole("button", { name: "新增项目" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "编辑 英雄联盟" })).not.toBeInTheDocument()

  await user.type(screen.getByLabelText("项目名称"), "英雄联盟")
  await user.click(screen.getByRole("button", { name: "查询项目" }))
  await waitFor(() =>
    expect(api.listProjects).toHaveBeenLastCalledWith(
      expect.objectContaining({ name: "英雄联盟" }),
    ),
  )

  await user.click(screen.getByRole("tab", { name: "职业队伍" }))
  expect(screen.queryByRole("button", { name: "新增队伍" })).not.toBeInTheDocument()
})

it("retains project input after a conflict and supports edit and deactivate", async () => {
  const user = userEvent.setup()
  vi.stubGlobal("confirm", vi.fn(() => true))
  api.createProject.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "项目名称已存在",
      code: "duplicate_project",
      request_id: "catalog-conflict",
    }),
  )
  renderPage(["teaching.read", "projects.manage"])
  await screen.findByText("英雄联盟")

  await user.click(screen.getByRole("button", { name: "新增项目" }))
  const dialog = screen.getByRole("dialog", { name: "新增项目" })
  await user.type(within(dialog).getByLabelText("项目名称"), "重复项目")
  await user.clear(within(dialog).getByLabelText("排序"))
  await user.type(within(dialog).getByLabelText("排序"), "30")
  await user.click(within(dialog).getByRole("button", { name: "保存项目" }))

  expect(await within(dialog).findByRole("alert")).toHaveTextContent("项目名称已存在")
  expect(within(dialog).getByLabelText("项目名称")).toHaveValue("重复项目")

  await user.click(within(dialog).getByRole("button", { name: "保存项目" }))
  await waitFor(() =>
    expect(api.createProject).toHaveBeenLastCalledWith({
      name: "重复项目",
      sort_order: 30,
    }),
  )
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "新增项目" })).not.toBeInTheDocument(),
  )

  await user.click(screen.getByRole("button", { name: "编辑 英雄联盟" }))
  const editDialog = screen.getByRole("dialog", { name: "编辑项目" })
  await user.clear(within(editDialog).getByLabelText("项目名称"))
  await user.type(within(editDialog).getByLabelText("项目名称"), "英雄联盟青训")
  await user.clear(within(editDialog).getByLabelText("排序"))
  await user.type(within(editDialog).getByLabelText("排序"), "12")
  await user.click(within(editDialog).getByRole("button", { name: "保存项目" }))
  await waitFor(() =>
    expect(api.updateProject).toHaveBeenCalledWith(
      activeProject.id,
      expect.objectContaining({ name: "英雄联盟青训", sort_order: 12 }),
    ),
  )

  await user.click(screen.getByRole("button", { name: "停用 英雄联盟" }))
  await waitFor(() =>
    expect(api.updateProject).toHaveBeenCalledWith(activeProject.id, { is_active: false }),
  )
})

it("lets only users with project coach permission configure selectable coaches", async () => {
  const user = userEvent.setup()
  renderPage(["teaching.read", "project_coaches.manage"])
  await screen.findByText("英雄联盟")

  expect(screen.queryByRole("button", { name: "编辑 英雄联盟" })).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "配置 英雄联盟 的教练" }))
  const dialog = await screen.findByRole("dialog", { name: "配置教练 · 英雄联盟" })
  expect(api.getProjectCoaches).toHaveBeenCalledWith(activeProject.id)
  expect(within(dialog).getByRole("checkbox", { name: "李教练" })).toBeChecked()
  await user.click(within(dialog).getByRole("checkbox", { name: "张教练" }))
  await user.click(within(dialog).getByRole("button", { name: "保存教练（2）" }))

  await waitFor(() => expect(api.replaceProjectCoaches).toHaveBeenCalledWith(
    activeProject.id,
    ["coach-2", "coach-1"],
  ))
})

it("locks the project form while a save is pending", async () => {
  const user = userEvent.setup()
  let resolveCreate: ((project: typeof activeProject) => void) | undefined
  api.createProject.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveCreate = resolve
      }),
  )
  renderPage(["teaching.read", "projects.manage"])
  await screen.findByText("英雄联盟")

  await user.click(screen.getByRole("button", { name: "新增项目" }))
  const dialog = screen.getByRole("dialog", { name: "新增项目" })
  const nameInput = within(dialog).getByLabelText("项目名称")
  const sortInput = within(dialog).getByLabelText("排序")
  const closeButton = within(dialog).getByRole("button", { name: "关闭项目表单" })
  const cancelButton = within(dialog).getByRole("button", { name: "取消" })
  const saveButton = within(dialog).getByRole("button", { name: "保存项目" })
  await user.type(nameInput, "等待项目")
  await user.click(saveButton)

  await waitFor(() => expect(saveButton).toBeDisabled())
  expect(nameInput).toBeDisabled()
  expect(sortInput).toBeDisabled()
  expect(closeButton).toBeDisabled()
  expect(cancelButton).toBeDisabled()
  await user.click(closeButton)
  expect(dialog).toBeInTheDocument()

  resolveCreate?.(activeProject)
  await waitFor(() => expect(dialog).not.toBeInTheDocument())
})

it("filters, creates, edits, and deactivates professional teams", async () => {
  const user = userEvent.setup()
  vi.stubGlobal("confirm", vi.fn(() => true))
  const laterProject = {
    ...activeProject,
    id: "30000000-0000-4000-8000-000000000004",
    name: "和平精英",
    sort_order: 20,
  }
  api.listProjects.mockResolvedValue({
    items: [laterProject, activeProject],
    page: 1,
    page_size: 20,
    total: 2,
  })
  const team = {
    id: "60000000-0000-4000-8000-000000000001",
    project_id: activeProject.id,
    name: "职业一队",
    notes: "训练队",
    is_active: true,
    created_at: activeProject.created_at,
    updated_at: activeProject.updated_at,
  }
  api.listProfessionalTeams.mockResolvedValue({
    items: [team],
    page: 1,
    page_size: 20,
    total: 1,
  })
  api.createProfessionalTeam.mockResolvedValue(team)
  api.updateProfessionalTeam.mockResolvedValue(team)
  renderPage(["teaching.read", "professional_teams.manage"])

  await user.click(screen.getByRole("tab", { name: "职业队伍" }))
  await screen.findByText("职业一队")
  await user.selectOptions(screen.getByLabelText("所属项目"), activeProject.id)
  await user.type(screen.getByLabelText("队伍名称"), "职业一队")
  await user.click(screen.getByRole("button", { name: "查询队伍" }))
  await waitFor(() =>
    expect(api.listProfessionalTeams).toHaveBeenLastCalledWith(
      expect.objectContaining({ projectId: activeProject.id, name: "职业一队" }),
    ),
  )

  await user.click(screen.getByRole("button", { name: "新增队伍" }))
  const dialog = screen.getByRole("dialog", { name: "新增队伍" })
  expect(
    Array.from(within(dialog).getByLabelText("所属项目").querySelectorAll("option")).map(
      (option) => option.textContent,
    ),
  ).toEqual(["请选择项目", "英雄联盟", "和平精英"])
  await user.selectOptions(within(dialog).getByLabelText("所属项目"), activeProject.id)
  await user.type(within(dialog).getByLabelText("队伍名称"), "新职业队")
  await user.type(within(dialog).getByLabelText("备注"), "跟队训练")
  await user.click(within(dialog).getByRole("button", { name: "保存队伍" }))
  await waitFor(() =>
    expect(api.createProfessionalTeam).toHaveBeenCalledWith({
      project_id: activeProject.id,
      name: "新职业队",
      notes: "跟队训练",
    }),
  )

  await user.click(screen.getByRole("button", { name: "编辑 职业一队" }))
  const editDialog = screen.getByRole("dialog", { name: "编辑队伍" })
  expect(within(editDialog).getByLabelText("所属项目")).toBeDisabled()
  expect(within(editDialog).getByLabelText("队伍名称")).toHaveAttribute(
    "maxlength",
    "120",
  )
  expect(within(screen.getByTestId("team-row")).getByRole("cell", { name: "队伍 职业一队" })).toHaveAttribute(
    "data-label",
    "队伍",
  )
  await user.clear(within(editDialog).getByLabelText("队伍名称"))
  await user.type(within(editDialog).getByLabelText("队伍名称"), "职业先锋队")
  await user.clear(within(editDialog).getByLabelText("备注"))
  await user.type(within(editDialog).getByLabelText("备注"), "一线队")
  await user.click(within(editDialog).getByRole("button", { name: "保存队伍" }))
  await waitFor(() =>
    expect(api.updateProfessionalTeam).toHaveBeenCalledWith(team.id, {
      name: "职业先锋队",
      notes: "一线队",
    }),
  )

  await user.click(screen.getByRole("button", { name: "停用 职业一队" }))
  await waitFor(() =>
    expect(api.updateProfessionalTeam).toHaveBeenCalledWith(team.id, { is_active: false }),
  )
})

it("opens catalog-only administrators directly on teams with usable create and edit forms, not teaching or project management", async () => {
  const user = userEvent.setup()
  const team = {
    id: "60000000-0000-4000-8000-000000000001", project_id: activeProject.id,
    name: "行政管理队伍", notes: null, is_active: true,
    created_at: activeProject.created_at, updated_at: activeProject.updated_at,
  }
  api.listProfessionalTeams.mockResolvedValue({ items: [team], page: 1, page_size: 20, total: 1 })
  api.createProfessionalTeam.mockResolvedValue(team)
  api.updateProfessionalTeam.mockResolvedValue(team)
  renderPage(["professional_teams.manage"])

  expect(await screen.findByRole("tab", { name: "职业队伍" })).toHaveAttribute("aria-selected", "true")
  await screen.findByText("行政管理队伍")
  expect(screen.queryByRole("tab", { name: "电竞项目" })).not.toBeInTheDocument()
  expect(screen.queryByRole("navigation", { name: "招生教学二级导航" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /新增项目|编辑 英雄联盟|配置.*教练/ })).not.toBeInTheDocument()
  expect(screen.queryByRole("table", { name: "电竞项目列表" })).not.toBeInTheDocument()
  expect(api.listProjects).toHaveBeenCalledTimes(1)
  expect(api.listProjects).toHaveBeenCalledWith({ includeInactive: true, page: 1, pageSize: 100 })
  expect(api.getProjectCoaches).not.toHaveBeenCalled()
  expect(teamApi.getTeamContext).not.toHaveBeenCalled()

  await user.click(screen.getByRole("button", { name: "新增队伍" }))
  const createDialog = screen.getByRole("dialog", { name: "新增队伍" })
  expect(within(createDialog).queryByRole("checkbox", { name: "用作队伍分部" })).not.toBeInTheDocument()
  await user.selectOptions(within(createDialog).getByLabelText("所属项目"), activeProject.id)
  await user.type(within(createDialog).getByLabelText("队伍名称"), "行政新队伍")
  await user.click(within(createDialog).getByRole("button", { name: "保存队伍" }))
  await waitFor(() => expect(api.createProfessionalTeam).toHaveBeenCalledWith({ project_id: activeProject.id, name: "行政新队伍", notes: null }))
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())

  await user.click(screen.getByRole("button", { name: "编辑 行政管理队伍" }))
  const editDialog = screen.getByRole("dialog", { name: "编辑队伍" })
  expect(within(editDialog).getByLabelText("所属项目")).toBeDisabled()
  await user.clear(within(editDialog).getByLabelText("队伍名称"))
  await user.type(within(editDialog).getByLabelText("队伍名称"), "行政队伍新名称")
  await user.click(within(editDialog).getByRole("button", { name: "保存队伍" }))
  await waitFor(() => expect(api.updateProfessionalTeam).toHaveBeenCalledWith(team.id, { name: "行政队伍新名称", notes: null }))
  expect(api.createProject).not.toHaveBeenCalled()
  expect(api.updateProject).not.toHaveBeenCalled()
})

it("opens the desktop project filters by default", async () => {
  renderPage(["teaching.read"])

  await screen.findAllByTestId("project-row")
  expect(screen.getByText("筛选项目").closest("details")).toHaveAttribute("open")
})

it("keeps responsive project cards semantically labelled at mobile widths", async () => {
  const user = userEvent.setup()
  Object.defineProperty(window, "innerWidth", { configurable: true, value: 360 })
  api.listProfessionalTeams.mockResolvedValue({
    items: [
      {
        id: "60000000-0000-4000-8000-000000000010",
        project_id: activeProject.id,
        name: "移动端队伍",
        notes: null,
        is_active: true,
        created_at: activeProject.created_at,
        updated_at: activeProject.updated_at,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })
  renderPage(["teaching.read"])

  const [row] = await screen.findAllByTestId("project-row")
  expect(screen.getByText("筛选项目").closest("details")).not.toHaveAttribute("open")
  expect(within(row).getByRole("cell", { name: "项目 英雄联盟" })).toHaveAttribute(
    "data-label",
    "项目",
  )
  expect(within(row).getByRole("cell", { name: "排序 10" })).toHaveAttribute(
    "data-label",
    "排序",
  )
  expect(within(row).getByRole("cell", { name: "状态 启用" })).toHaveAttribute(
    "data-label",
    "状态",
  )

  await user.click(screen.getByRole("tab", { name: "职业队伍" }))
  const teamRow = await screen.findByTestId("team-row")
  expect(within(teamRow).getByRole("cell", { name: "队伍 移动端队伍" })).toHaveAttribute(
    "data-label",
    "队伍",
  )
  expect(within(teamRow).getByRole("cell", { name: "所属项目 英雄联盟" })).toHaveAttribute(
    "data-label",
    "所属项目",
  )
})
