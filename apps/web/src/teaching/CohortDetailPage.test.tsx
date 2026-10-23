import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import { CohortDetailPage } from "./CohortDetailPage"

const api = vi.hoisted(() => ({
  getCohort: vi.fn(),
  listCohortStatusEvents: vi.fn(),
  transitionCohort: vi.fn(),
  listClasses: vi.fn(),
  createClass: vi.fn(),
  updateClass: vi.fn(),
  listProjects: vi.fn(),
  listProfessionalTeams: vi.fn(),
  listCoachAssignments: vi.fn(),
  listEligibleCoaches: vi.fn(),
  createCoachAssignment: vi.fn(),
  endCoachAssignment: vi.fn(),
}))

const admissionsApi = vi.hoisted(() => ({
  listEnrollments: vi.fn(),
}))

const evaluationFoundationApi = vi.hoisted(() => ({
  listEvaluationStaffScopes: vi.fn(),
  listAllEvaluationStaffScopes: vi.fn(),
  listCohortStaffAssignments: vi.fn(),
  listStaffRoles: vi.fn(),
  listStaffDirectory: vi.fn(),
  createStaffRole: vi.fn(),
  deactivateStaffRole: vi.fn(),
  createCohortStaffAssignment: vi.fn(),
  endStaffAssignment: vi.fn(),
}))
const authApi = vi.hoisted(() => ({
  refreshUser: vi.fn(async () => undefined),
}))
const defaultAccountId = "10000000-0000-4000-8000-000000000001"
let authAccountId = defaultAccountId

vi.mock("./api", () => api)
vi.mock("../admissions/api", () => admissionsApi)
vi.mock("../evaluations/foundationApi", async (importOriginal) => ({
  ...((await importOriginal()) as Record<string, unknown>),
  ...evaluationFoundationApi,
}))
vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    user: { account_id: authAccountId },
    refreshUser: authApi.refreshUser,
  }),
}))

const cohortId = "70000000-0000-4000-8000-000000000001"
const projectId = "30000000-0000-4000-8000-000000000001"
const teamId = "60000000-0000-4000-8000-000000000001"
const classId = "80000000-0000-4000-8000-000000000001"

const cohort = {
  id: cohortId,
  name: "2026 八月青训营",
  starts_at: "2026-08-01T09:30:00+08:00",
  ends_at: "2026-08-08T18:00:00+08:00",
  default_check_in_at: "2026-08-01T08:00:00+08:00",
  default_check_out_at: "2026-08-08T19:00:00+08:00",
  status: "open" as const,
  auto_start_enabled: true,
  auto_complete_enabled: true,
  automation_issues: ["auto_complete_transition_blocked" as const],
  enabled_class_count: 1,
  missing_primary_class_count: 1,
  created_at: "2026-07-01T00:00:00+08:00",
  updated_at: "2026-07-01T00:00:00+08:00",
}

const teachingClass = {
  id: classId,
  cohort_id: cohortId,
  project_id: projectId,
  name: "英雄联盟普通班",
  class_type: "standard" as const,
  professional_team_id: null,
  capacity: 20,
  default_tuition_cents: 120_000,
  is_active: true,
  created_at: cohort.created_at,
  updated_at: cohort.updated_at,
}

const assignment = {
  id: "90000000-0000-4000-8000-000000000001",
  class_id: classId,
  coach_person_id: "20000000-0000-4000-8000-000000000006",
  coach_full_name: "王教练",
  assignment_type: "primary" as const,
  valid_from: "2026-07-20T09:00:00+08:00",
  valid_until: null,
  ended_at: null,
  ended_by_user_id: null,
  end_reason: null,
  created_at: cohort.created_at,
  updated_at: cohort.updated_at,
}

const endedAssignment = {
  ...assignment,
  id: "90000000-0000-4000-8000-000000000002",
  coach_person_id: "20000000-0000-4000-8000-000000000007",
  coach_full_name: "李教练",
  assignment_type: "assistant" as const,
  ended_at: "2026-07-25T18:00:00+08:00",
  ended_by_user_id: "10000000-0000-4000-8000-000000000001",
  end_reason: "历史排班结束",
}

const enrollment = {
  id: "a0000000-0000-4000-8000-000000000001",
  admission_case_id: "a1000000-0000-4000-8000-000000000001",
  student_person_id: "20000000-0000-4000-8000-000000000021",
  student_name: "张同学",
  cohort_id: cohortId,
  cohort_name: cohort.name,
  class_id: classId,
  class_name: teachingClass.name,
  agreed_tuition_cents: 120_000,
  payment_status: "partial" as const,
  paid_cents: 80_000,
  refunded_cents: 0,
  net_paid_cents: 80_000,
  due_cents: 40_000,
  overpaid_cents: 0,
  status: "active" as const,
  confirmed_at: "2026-07-28T10:00:00+08:00",
  cancelled_at: null,
  completed_at: null,
  created_at: "2026-07-28T10:00:00+08:00",
  updated_at: "2026-07-28T10:00:00+08:00",
}

function page<T>(items: T[], total = items.length) {
  return { items, page: 1, page_size: 20, total }
}

function renderPage(
  permissions: string[],
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } }),
  scopedPermissions: string[] = [],
) {
  let renderedCohortId = cohortId
  const tree = () => (
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <CohortDetailPage
          cohortId={renderedCohortId}
          permissions={new Set(permissions)}
          scopedPermissions={new Set(scopedPermissions)}
        />
      </QueryClientProvider>
    </MemoryRouter>
  )
  const view = render(tree())
  return {
    ...view,
    rerenderPage: () => view.rerender(tree()),
    rerenderCohort: (nextCohortId: string) => {
      renderedCohortId = nextCohortId
      view.rerender(tree())
    },
  }
}

beforeEach(() => {
  vi.resetAllMocks()
  authAccountId = defaultAccountId
  api.getCohort.mockResolvedValue(cohort)
  api.listCohortStatusEvents.mockResolvedValue(
    page([
      {
        id: "71000000-0000-4000-8000-000000000001",
        cohort_id: cohortId,
        old_status: "draft",
        new_status: "open",
        trigger: "manual",
        actor_user_id: "10000000-0000-4000-8000-000000000001",
        request_id: "manual-open",
        occurred_at: "2026-07-10T09:00:00+08:00",
      },
      {
        id: "71000000-0000-4000-8000-000000000002",
        cohort_id: cohortId,
        old_status: "upcoming",
        new_status: "active",
        trigger: "scheduler",
        actor_user_id: null,
        request_id: "scheduler-1",
        occurred_at: "2026-08-01T09:30:00+08:00",
      },
      {
        id: "71000000-0000-4000-8000-000000000003",
        cohort_id: cohortId,
        old_status: "open",
        new_status: "active",
        trigger: "request_reconciliation",
        actor_user_id: null,
        request_id: "request-1",
        occurred_at: "2026-08-01T09:31:00+08:00",
      },
    ]),
  )
  api.listClasses.mockResolvedValue(page([teachingClass]))
  api.listProjects.mockResolvedValue(
    page([
      {
        id: projectId,
        name: "英雄联盟",
        sort_order: 10,
        is_active: true,
        created_at: cohort.created_at,
        updated_at: cohort.updated_at,
      },
    ]),
  )
  api.listProfessionalTeams.mockResolvedValue(
    page([
      {
        id: teamId,
        project_id: projectId,
        name: "职业一队",
        notes: null,
        is_active: true,
        created_at: cohort.created_at,
        updated_at: cohort.updated_at,
      },
    ]),
  )
  api.transitionCohort.mockResolvedValue({ ...cohort, status: "active" })
  api.createClass.mockResolvedValue(teachingClass)
  api.updateClass.mockResolvedValue(teachingClass)
  api.listCoachAssignments.mockResolvedValue(page([assignment, endedAssignment]))
  api.listEligibleCoaches.mockResolvedValue(
    page([{ id: assignment.coach_person_id, full_name: "王教练" }]),
  )
  api.createCoachAssignment.mockResolvedValue(assignment)
  api.endCoachAssignment.mockResolvedValue({
    ...assignment,
    ended_at: "2026-07-28T12:00:00+08:00",
    end_reason: "教学安排调整",
  })
  admissionsApi.listEnrollments.mockResolvedValue(page([enrollment]))
  evaluationFoundationApi.listEvaluationStaffScopes.mockResolvedValue(page([], 0))
  evaluationFoundationApi.listCohortStaffAssignments.mockResolvedValue([
    {
      id: "51000000-0000-4000-8000-000000000001",
      source: "cohort_staff_assignment",
      staff_person_id: "50000000-0000-4000-8000-000000000001",
      staff_name: "赵生活老师",
      role_name: "生活老师",
      role_category: "life_service",
      scope_kind: "project",
      project_id: projectId,
      project_name: "英雄联盟",
      class_id: null,
      class_name: null,
      permission_keys: [],
      valid_from: "2026-08-01T09:00:00+08:00",
      valid_until: null,
      ended_at: null,
      end_reason: null,
      manage_allowed: true,
    },
  ])
  evaluationFoundationApi.listStaffRoles.mockResolvedValue([])
  evaluationFoundationApi.listStaffDirectory.mockResolvedValue(
    page([], 0),
  )
  evaluationFoundationApi.listAllEvaluationStaffScopes.mockResolvedValue([])
})

it("reuses the cohort staff panel as the Workers tab", async () => {
  const user = userEvent.setup()
  renderPage(
    ["teaching.read", "evaluation_center.access"],
    undefined,
    ["cohort_staff.manage"],
  )
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "工作人员" }))

  expect(await screen.findByText("赵生活老师")).toBeVisible()
  expect(screen.getByText("生活老师")).toBeVisible()
  expect(evaluationFoundationApi.listCohortStaffAssignments).toHaveBeenCalledWith(
    cohortId,
  )
})

it("discovers the Workers tab from a successful cohort probe when local permission keys are stale", async () => {
  evaluationFoundationApi.listEvaluationStaffScopes.mockResolvedValue(page([], 0))

  renderPage(
    ["teaching.read", "evaluation_center.access"],
    undefined,
    ["cohort_staff.manage:another-cohort"],
  )

  expect(await screen.findByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
  expect(await screen.findByRole("tab", { name: "工作人员" })).toBeVisible()
  expect(evaluationFoundationApi.listEvaluationStaffScopes).toHaveBeenCalledWith(cohortId, {
    scopeKind: "project",
    page: 1,
    pageSize: 1,
  })
})

it("rechecks the cohort capability instead of sharing a successful probe across accounts", async () => {
  evaluationFoundationApi.listEvaluationStaffScopes
    .mockResolvedValueOnce(page([], 0))
    .mockRejectedValueOnce(
      new ProblemDetailsError({
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "新账号没有当前营期工作人员权限",
        code: "cohort_staff_access_denied",
        request_id: "cohort-staff-account-switch",
      }),
    )
  const view = renderPage(["teaching.read"])

  expect(await screen.findByRole("tab", { name: "工作人员" })).toBeVisible()

  authAccountId = "10000000-0000-4000-8000-000000000099"
  view.rerenderPage()

  await waitFor(() => {
    expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()
  })
  expect(evaluationFoundationApi.listEvaluationStaffScopes).toHaveBeenCalledTimes(2)
})

it.each([403, 404])(
  "hides a stale Workers tab after a %s cohort probe without ending the ERP session",
  async (status) => {
    evaluationFoundationApi.listEvaluationStaffScopes
      .mockResolvedValueOnce(page([], 0))
      .mockRejectedValueOnce(
        new ProblemDetailsError({
          type: "about:blank",
          title: status === 403 ? "Forbidden" : "Not found",
          status,
          detail: "当前营期工作人员权限不存在",
          code: "cohort_staff_access_denied",
          request_id: `cohort-staff-${status}`,
        }),
      )
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const cachedCohortKey = [
      "evaluations",
      "10000000-0000-4000-8000-000000000001",
      "foundation",
      "admin",
      "staff",
      "assignments",
      cohortId,
    ] as const
    const otherCohortKey = [
      "evaluations",
      "10000000-0000-4000-8000-000000000001",
      "foundation",
      "admin",
      "staff",
      "assignments",
      "70000000-0000-4000-8000-000000000099",
    ] as const
    client.setQueryData(cachedCohortKey, { staff_name: "当前营期缓存" })
    client.setQueryData(otherCohortKey, { staff_name: "其他营期缓存" })
    const user = userEvent.setup()

    renderPage(
      ["teaching.read", "evaluation_center.access"],
      client,
      ["cohort_staff.manage:another-cohort"],
    )

    await user.click(await screen.findByRole("tab", { name: "工作人员" }))
    await user.click(await screen.findByRole("button", { name: "新增工作人员服务" }))
    expect(screen.getByRole("dialog", { name: "新增工作人员服务" })).toBeVisible()

    await client.invalidateQueries({
      queryKey: ["cohort-staff-capability", defaultAccountId, cohortId],
    })

    await waitFor(() => {
      expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()
      expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute(
        "aria-selected",
        "true",
      )
    })
    expect(screen.queryByRole("dialog", { name: "新增工作人员服务" })).not.toBeInTheDocument()
    expect(screen.getByRole("region", { name: "营期安排" })).toBeVisible()
    expect(screen.getByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
    expect(client.getQueryData(cachedCohortKey)).toBeUndefined()
    expect(client.getQueryData(otherCohortKey)).toEqual({ staff_name: "其他营期缓存" })
    expect(authApi.refreshUser).not.toHaveBeenCalled()
  },
)

it("retries a failed Workers capability probe before showing the tab", async () => {
  evaluationFoundationApi.listEvaluationStaffScopes
    .mockRejectedValueOnce(
      new ProblemDetailsError({
        type: "about:blank",
        title: "Service unavailable",
        status: 503,
        detail: "权限目录暂时不可用",
        code: "staff_scope_unavailable",
        request_id: "cohort-staff-probe-503",
      }),
    )
    .mockResolvedValueOnce(page([], 0))
  const user = userEvent.setup()

  renderPage(["teaching.read"])

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("工作人员权限检查失败")
  expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()

  await user.click(
    within(alert).getByRole("button", { name: "重试工作人员权限检查" }),
  )

  expect(await screen.findByRole("tab", { name: "工作人员" })).toBeVisible()
  expect(screen.queryByText("工作人员权限检查失败")).not.toBeInTheDocument()
  expect(evaluationFoundationApi.listEvaluationStaffScopes).toHaveBeenCalledTimes(2)
})

it("keeps an assignment object revocation scoped to its cohort", async () => {
  const nextCohortId = "70000000-0000-4000-8000-000000000099"
  evaluationFoundationApi.endStaffAssignment.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      detail: "当前营期工作人员权限不存在",
      code: "cohort_staff_access_denied",
      request_id: "cohort-staff-assignment-revoked",
    }),
  )
  const user = userEvent.setup()
  const view = renderPage(["teaching.read"])

  await user.click(await screen.findByRole("tab", { name: "工作人员" }))
  await user.click(await screen.findByRole("button", { name: "结束 赵生活老师 的服务" }))
  const dialog = screen.getByRole("dialog", { name: "结束工作人员服务" })
  await user.type(within(dialog).getByLabelText("结束原因"), "对象权限已失效")
  await user.click(within(dialog).getByRole("button", { name: "确认结束" }))

  await waitFor(() => {
    expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()
    expect(screen.getByRole("tab", { name: "概览" })).toHaveAttribute(
      "aria-selected",
      "true",
    )
  })

  view.rerenderCohort(nextCohortId)

  expect(await screen.findByRole("tab", { name: "工作人员" })).toBeVisible()
  expect(evaluationFoundationApi.listEvaluationStaffScopes).toHaveBeenCalledWith(
    nextCohortId,
    { scopeKind: "project", page: 1, pageSize: 1 },
  )
})

it("does not mount the Workers evaluation panel when the cohort probe is denied", async () => {
  evaluationFoundationApi.listEvaluationStaffScopes.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      detail: "当前营期工作人员权限不存在",
      code: "cohort_staff_access_denied",
      request_id: "cohort-staff-denied",
    }),
  )
  renderPage(["teaching.read"], undefined, ["cohort_staff.manage"])

  expect(await screen.findByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
  expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()
  expect(evaluationFoundationApi.listCohortStaffAssignments).not.toHaveBeenCalled()
})

it("removes a revoked Workers panel without leaving the cohort page", async () => {
  evaluationFoundationApi.listCohortStaffAssignments.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Forbidden",
      status: 403,
      detail: "评价中心权限已收回",
      code: "evaluation_access_denied",
      request_id: "cohort-evaluation-revoked",
    }),
  )
  const user = userEvent.setup()
  renderPage([
    "teaching.read",
    "evaluation_center.access",
    "cohort_staff.manage",
  ])

  expect(await screen.findByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
  await user.click(screen.getByRole("tab", { name: "工作人员" }))

  await waitFor(() => {
    expect(screen.queryByRole("tab", { name: "工作人员" })).not.toBeInTheDocument()
    expect(screen.queryByText("正在更新基础数据权限...")).not.toBeInTheDocument()
  })
  expect(screen.getByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
  expect(screen.getByRole("tab", { name: "概览" })).toBeVisible()
})

it("shows the server-authorized cohort student roster to enrollment readers", async () => {
  const user = userEvent.setup()
  renderPage(["teaching.read", "enrollments.read"])
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "学员" }))

  expect(await screen.findByText("张同学")).toBeVisible()
  expect(screen.getByText("英雄联盟普通班")).toBeVisible()
  expect(screen.getByText("部分付款")).toBeVisible()
  expect(screen.getByText("欠款 ¥400.00")).toBeVisible()
  expect(screen.getByRole("link", { name: "查看报名" })).toHaveAttribute(
    "href",
    `/teaching/enrollments/${enrollment.id}`,
  )
  expect(admissionsApi.listEnrollments).toHaveBeenLastCalledWith({
    cohortId,
    status: "active",
    page: 1,
    pageSize: 20,
  })
})

it("supports scoped readers, hidden amounts and all-status filtering", async () => {
  const user = userEvent.setup()
  admissionsApi.listEnrollments.mockResolvedValue(
    page([
      {
        ...enrollment,
        agreed_tuition_cents: null,
        paid_cents: null,
        refunded_cents: null,
        net_paid_cents: null,
        due_cents: null,
        overpaid_cents: null,
      },
    ]),
  )
  renderPage(["teaching.read"], undefined, ["enrollments.read"])
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "学员" }))
  expect(await screen.findByText("金额不可见")).toBeVisible()
  await user.selectOptions(screen.getByLabelText("报名状态"), "all")

  await waitFor(() =>
    expect(admissionsApi.listEnrollments).toHaveBeenLastCalledWith({
      cohortId,
      status: undefined,
      page: 1,
      pageSize: 20,
    }),
  )
})

it("shows only legal status commands, automation warnings, and all history triggers", async () => {
  const user = userEvent.setup()
  api.transitionCohort.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "当前营期尚未满足开营条件",
      code: "cohort_not_ready",
      request_id: "transition-error",
    }),
  )
  renderPage(["teaching.read", "cohorts.manage"])

  expect(await screen.findByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
  expect(screen.getByText("自动结营受阻")).toBeVisible()
  expect(screen.getByRole("button", { name: "标记即将开营" })).toBeVisible()
  expect(screen.getByRole("button", { name: "开始营期" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "归档营期" })).not.toBeInTheDocument()
  expect(screen.queryByRole("tab", { name: "学员" })).not.toBeInTheDocument()
  expect(await screen.findByText("管理员操作")).toBeVisible()
  expect(screen.getByText("系统定时")).toBeVisible()
  expect(screen.getByText("请求校准")).toBeVisible()

  await user.click(screen.getByRole("button", { name: "开始营期" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("当前营期尚未满足开营条件")
})

it("creates an elite class with a same-project team and exact yuan conversion", async () => {
  const user = userEvent.setup()
  renderPage(["teaching.read", "classes.manage"])
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "班级" }))
  expect(await screen.findByText("英雄联盟普通班")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "新增班级" }))
  const dialog = screen.getByRole("dialog", { name: "新增班级" })
  expect(within(dialog).getByRole("radio", { name: "普通班" })).toBeChecked()
  expect(within(dialog).queryByLabelText("职业队伍")).not.toBeInTheDocument()
  await user.click(within(dialog).getByRole("radio", { name: "菁英班" }))
  await user.selectOptions(within(dialog).getByLabelText("电竞项目"), projectId)
  await waitFor(() =>
    expect(api.listProfessionalTeams).toHaveBeenLastCalledWith({
      projectId,
      includeInactive: false,
      page: 1,
      pageSize: 100,
    }),
  )
  await user.selectOptions(within(dialog).getByLabelText("职业队伍"), teamId)
  await user.type(within(dialog).getByLabelText("班级名称"), "英雄联盟菁英班")
  await user.clear(within(dialog).getByLabelText("人数上限"))
  await user.type(within(dialog).getByLabelText("人数上限"), "10")
  await user.clear(within(dialog).getByLabelText("默认学费（元）"))
  await user.type(within(dialog).getByLabelText("默认学费（元）"), "2600.05")
  await user.click(within(dialog).getByRole("button", { name: "保存班级" }))

  await waitFor(() =>
    expect(api.createClass).toHaveBeenCalledWith({
      cohort_id: cohortId,
      project_id: projectId,
      name: "英雄联盟菁英班",
      class_type: "elite",
      professional_team_id: teamId,
      capacity: 10,
      default_tuition_cents: 260_005,
    }),
  )
})

it("shows the server error when a class status update is rejected", async () => {
  const user = userEvent.setup()
  api.updateClass.mockRejectedValueOnce(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "进行中的报名不允许停用班级",
      code: "class_has_active_enrollments",
      request_id: "class-status-error",
    }),
  )
  renderPage(["teaching.read", "classes.manage"])
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "班级" }))
  await screen.findByText("英雄联盟普通班")
  await user.click(screen.getByRole("button", { name: "停用 英雄联盟普通班" }))

  expect(await screen.findByRole("alert")).toHaveTextContent("进行中的报名不允许停用班级")
})

it("refreshes cohort summaries after changing a class status", async () => {
  const user = userEvent.setup()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidate = vi.spyOn(client, "invalidateQueries")
  renderPage(["teaching.read", "classes.manage"], client)
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "班级" }))
  await screen.findByText("英雄联盟普通班")
  await user.click(screen.getByRole("button", { name: "停用 英雄联盟普通班" }))

  await waitFor(() => {
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["classes"] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["cohort", cohortId] })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["cohorts"] })
  })
})

it("keeps an archived cohort read-only across every detail tab", async () => {
  const user = userEvent.setup()
  api.getCohort.mockResolvedValue({ ...cohort, status: "archived" })
  renderPage([
    "teaching.read",
    "cohorts.manage",
    "classes.manage",
    "coach_assignments.manage",
  ])
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  expect(screen.queryByRole("button", { name: "归档营期" })).not.toBeInTheDocument()
  await user.click(screen.getByRole("tab", { name: "班级" }))
  await screen.findByText("英雄联盟普通班")
  expect(screen.queryByRole("button", { name: "新增班级" })).not.toBeInTheDocument()
  expect(
    screen.queryByRole("button", { name: "编辑 英雄联盟普通班" }),
  ).not.toBeInTheDocument()
  expect(
    screen.queryByRole("button", { name: "停用 英雄联盟普通班" }),
  ).not.toBeInTheDocument()

  await user.click(screen.getByRole("tab", { name: "教练" }))
  await screen.findByText("王教练")
  expect(screen.queryByRole("button", { name: "分配教练" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "结束 王教练" })).not.toBeInTheDocument()
})

it("selects eligible coaches and requires a reason before ending an assignment", async () => {
  const user = userEvent.setup()
  renderPage(["teaching.read", "coach_assignments.manage"])
  await screen.findByRole("heading", { name: "2026 八月青训营" })

  await user.click(screen.getByRole("tab", { name: "教练" }))
  expect(await screen.findByText("王教练")).toBeVisible()
  expect(screen.getByText("李教练")).toBeVisible()
  expect(screen.getByText("已于 2026-07-25 结束")).toBeVisible()
  expect(screen.getByText("历史排班结束")).toBeVisible()
  expect(api.listCoachAssignments).toHaveBeenLastCalledWith(classId, true, 1, 20)
  await user.click(screen.getByRole("button", { name: "分配教练" }))
  const assignDialog = screen.getByRole("dialog", { name: "分配教练" })
  await user.type(within(assignDialog).getByLabelText("查找教练"), "王教练")
  await user.click(within(assignDialog).getByRole("button", { name: "查询教练" }))
  await waitFor(() => expect(api.listEligibleCoaches).toHaveBeenLastCalledWith(classId, "王教练", 1, 20))
  await user.click(within(assignDialog).getByRole("radio", { name: "王教练" }))
  await user.click(within(assignDialog).getByRole("radio", { name: "助教" }))
  fireEvent.change(within(assignDialog).getByLabelText("生效日期"), {
    target: { value: "2026-07-29" },
  })
  await user.click(within(assignDialog).getByRole("button", { name: "确认分配" }))
  await waitFor(() =>
    expect(api.createCoachAssignment).toHaveBeenCalledWith(classId, {
      coach_person_id: assignment.coach_person_id,
      assignment_type: "assistant",
      valid_from: "2026-07-29T00:00:00+08:00",
      valid_until: null,
    }),
  )

  await user.click(screen.getByRole("button", { name: "结束 王教练" }))
  const endDialog = screen.getByRole("dialog", { name: "结束教练分配" })
  await user.click(within(endDialog).getByRole("button", { name: "确认结束" }))
  expect(await within(endDialog).findByRole("alert")).toHaveTextContent("请填写结束原因")
  expect(api.endCoachAssignment).not.toHaveBeenCalled()
  await user.type(within(endDialog).getByLabelText("结束原因"), "教学安排调整")
  await user.click(within(endDialog).getByRole("button", { name: "确认结束" }))
  await waitFor(() =>
    expect(api.endCoachAssignment).toHaveBeenCalledWith(
      classId,
      assignment.id,
      "教学安排调整",
    ),
  )
})

it("keeps scoped coaches read-only and renders only API-authorized classes and aggregates", async () => {
  api.getCohort.mockResolvedValue({ ...cohort, missing_primary_class_count: 0 })
  api.listClasses.mockResolvedValue(page([{ ...teachingClass, name: "我的授权班级" }]))
  renderPage([])

  expect(await screen.findByRole("heading", { name: "2026 八月青训营" })).toBeVisible()
  expect(screen.getByText("主教练配置完整")).toBeVisible()
  expect(screen.queryByRole("link", { name: "项目与职业队伍" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "开始营期" })).not.toBeInTheDocument()

  await userEvent.click(screen.getByRole("tab", { name: "班级" }))
  expect(await screen.findByText("我的授权班级")).toBeVisible()
  expect(screen.getByText("1 个可见班级")).toBeVisible()
  expect(screen.queryByText("2 个可见班级")).not.toBeInTheDocument()
  expect(screen.queryByText("隐藏班级")).not.toBeInTheDocument()
  expect(screen.queryByText(new RegExp(projectId))).not.toBeInTheDocument()
  expect(api.listProjects).not.toHaveBeenCalled()
  expect(screen.queryByRole("button", { name: "新增班级" })).not.toBeInTheDocument()
})
