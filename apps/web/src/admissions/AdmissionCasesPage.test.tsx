import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type {
  AdmissionCase,
  AdmissionEnrollmentOption,
  AdmissionStudentCandidate,
  Enrollment,
} from "./api"
import { AdmissionCasesPage } from "./AdmissionCasesPage"

const apiMocks = vi.hoisted(() => ({
  closeAdmissionCase: vi.fn(),
  confirmAdmissionEnrollment: vi.fn(),
  listAdmissionCases: vi.fn(),
  listAdmissionEnrollmentOptions: vi.fn(),
  listAdmissionStudentCandidates: vi.fn(),
  matchAdmissionStudent: vi.fn(),
  reopenAdmissionCase: vi.fn(),
}))

vi.mock("./api", () => apiMocks)

const pendingCase: AdmissionCase = {
  id: "case-1",
  order_number: "BM20260911000001",
  student_person_id: null,
  student_name: null,
  status: "pending_match",
  current_enrollment_id: null,
  payment_status: "paid",
  paid_cents: 128_000,
  refunded_cents: 0,
  net_paid_cents: 128_000,
  agreed_tuition_cents: null,
  due_cents: 0,
  overpaid_cents: 0,
  manage_allowed: true,
  read_only_reason: null,
  created_at: "2026-08-01T09:00:00+08:00",
  updated_at: "2026-08-01T09:00:00+08:00",
}

const readyCase: AdmissionCase = {
  ...pendingCase,
  id: "case-2",
  student_person_id: "student-1",
  student_name: "张同学",
  status: "ready",
}

const selfServiceCase: AdmissionCase = {
  ...readyCase,
  id: "case-self-service",
  agreed_tuition_cents: 1_188_800,
  application_source: "self_service",
  selected_project_id: "project-1",
  selected_project_name: "英雄联盟",
  selected_program_id: "program-1",
  selected_program_name: "普通暑期班",
  payment_preference: "offline",
}

const candidate: AdmissionStudentCandidate = {
  id: "student-1",
  full_name: "张同学",
  student_number: "S20260001",
  student_status: "draft",
  masked_phone: "138****0000",
}

const option: AdmissionEnrollmentOption = {
  cohort_id: "cohort-1",
  cohort_name: "2026 暑期营",
  cohort_starts_at: "2026-08-10T09:00:00+08:00",
  cohort_status: "open",
  class_id: "class-1",
  class_name: "英雄联盟普通班",
  class_is_active: true,
  capacity: 20,
  active_enrollment_count: 8,
  is_full: false,
  default_tuition_cents: 128_000,
  readiness_at_start: "ready",
  manage_allowed: true,
  capacity_override_allowed: true,
}

const enrollment: Enrollment = {
  id: "enrollment-1",
  order_number: readyCase.order_number,
  admission_case_id: readyCase.id,
  student_person_id: "student-1",
  student_name: "张同学",
  cohort_id: option.cohort_id,
  cohort_name: option.cohort_name,
  class_id: option.class_id,
  class_name: option.class_name,
  agreed_tuition_cents: option.default_tuition_cents,
  payment_status: "paid",
  paid_cents: 128_000,
  refunded_cents: 0,
  net_paid_cents: 128_000,
  due_cents: 0,
  overpaid_cents: 0,
  status: "active",
  confirmed_at: "2026-08-01T10:00:00+08:00",
  cancelled_at: null,
  completed_at: null,
  created_at: "2026-08-01T10:00:00+08:00",
  updated_at: "2026-08-01T10:00:00+08:00",
}

const adminPermissions = new Set([
  "teaching.read",
  "admission_cases.read",
  "admission_cases.manage",
  "enrollments.read",
  "enrollments.manage",
  "enrollments.capacity_override",
  "payments.read",
])

function renderPage(
  permissions: ReadonlySet<string> = adminPermissions,
): QueryClient {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter initialEntries={["/teaching/admission-cases"]}>
      <QueryClientProvider client={queryClient}>
        <AdmissionCasesPage permissions={permissions} />
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return queryClient
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [pendingCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listAdmissionStudentCandidates.mockResolvedValue({
    items: [candidate],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listAdmissionEnrollmentOptions.mockResolvedValue({
    items: [option],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.matchAdmissionStudent.mockResolvedValue(readyCase)
  apiMocks.confirmAdmissionEnrollment.mockResolvedValue(enrollment)
  apiMocks.closeAdmissionCase.mockResolvedValue({
    ...pendingCase,
    status: "closed",
  })
  apiMocks.reopenAdmissionCase.mockResolvedValue(pendingCase)
})

it("matches a guardian payment to one selected student before enrollment", async () => {
  const user = userEvent.setup()
  const queryClient = renderPage()
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries")

  await user.click(await screen.findByRole("button", { name: "匹配学员" }))
  await user.type(screen.getByLabelText("手机号或学员编号"), "S20260001")
  await user.click(screen.getByRole("button", { name: "查询学员" }))
  await user.click(await screen.findByRole("radio", { name: "张同学" }))
  await user.click(screen.getByRole("button", { name: "确认匹配" }))

  await waitFor(() =>
    expect(apiMocks.matchAdmissionStudent).toHaveBeenCalledWith(
      "case-1",
      "student-1",
    ),
  )
  expect(await screen.findByRole("dialog", { name: "确认报名" })).toBeVisible()
  expect(screen.getByText("步骤 2 / 2")).toBeVisible()
  expect(screen.queryByRole("dialog", { name: "匹配学员" })).not.toBeInTheDocument()
  expect(apiMocks.listAdmissionEnrollmentOptions).toHaveBeenCalledWith("case-2", {
    page: 1,
    pageSize: 20,
  })
  expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ["admission-cases"],
  })
})

it("confirms enrollment with the server-owned default tuition", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  expect(
    within(screen.getByRole("dialog", { name: "确认报名" })).getByText(
      "¥1,280.00",
    ),
  ).toBeVisible()
  await user.click(screen.getByRole("button", { name: "最终确认报名" }))

  await waitFor(() =>
    expect(apiMocks.confirmAdmissionEnrollment).toHaveBeenCalledWith("case-2", {
      class_id: "class-1",
      agreed_tuition_cents: null,
      tuition_override_reason: null,
      capacity_override_reason: null,
    }),
  )
})

it("shows a self-service choice and uses its price as the confirmation baseline", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [selfServiceCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByText("英雄联盟")).toBeVisible()
  expect(screen.getByText("普通暑期班 · 线下付款")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  expect(
    within(screen.getByRole("dialog", { name: "确认报名" })).getByText(
      "¥11,888.00",
    ),
  ).toBeVisible()
  await user.click(screen.getByRole("button", { name: "最终确认报名" }))

  await waitFor(() => expect(apiMocks.confirmAdmissionEnrollment).toHaveBeenCalledWith(
    selfServiceCase.id,
    expect.objectContaining({ agreed_tuition_cents: null }),
  ))
})

it("converts an adjusted tuition decimal to integer cents without float arithmetic", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  await user.click(screen.getByRole("checkbox", { name: "调整学费" }))
  await user.clear(screen.getByLabelText("最终学费（元）"))
  await user.type(screen.getByLabelText("最终学费（元）"), "36800.05")
  await user.type(screen.getByLabelText("学费调整原因"), "菁英班审批")
  await user.click(screen.getByRole("button", { name: "最终确认报名" }))

  await waitFor(() =>
    expect(apiMocks.confirmAdmissionEnrollment).toHaveBeenCalledWith(
      "case-2",
      expect.objectContaining({
        agreed_tuition_cents: 3_680_005,
        tuition_override_reason: "菁英班审批",
      }),
    ),
  )
})

it("blocks a full class when this account cannot override capacity", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listAdmissionEnrollmentOptions.mockResolvedValue({
    items: [
      {
        ...option,
        active_enrollment_count: 20,
        is_full: true,
        capacity_override_allowed: false,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )

  expect(screen.getByText("班级已满，当前账号无超额报名权限")).toBeVisible()
  expect(screen.getByRole("button", { name: "最终确认报名" })).toBeDisabled()
  expect(screen.queryByLabelText("超额报名原因")).not.toBeInTheDocument()
})

it("requires a reason when an authorized account confirms a full class", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listAdmissionEnrollmentOptions.mockResolvedValue({
    items: [
      {
        ...option,
        active_enrollment_count: 20,
        is_full: true,
        capacity_override_allowed: true,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  expect(screen.getByRole("button", { name: "最终确认报名" })).toBeDisabled()
  await user.type(screen.getByLabelText("超额报名原因"), "校长审批")
  await user.click(screen.getByRole("button", { name: "最终确认报名" }))

  await waitFor(() =>
    expect(apiMocks.confirmAdmissionEnrollment).toHaveBeenCalledWith(
      "case-2",
      expect.objectContaining({ capacity_override_reason: "校长审批" }),
    ),
  )
})

it("blocks enrollment when the minor will not have a ready guardian at cohort start", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listAdmissionEnrollmentOptions.mockResolvedValue({
    items: [{ ...option, readiness_at_start: "minor_guardian_required" }],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )

  expect(screen.getByText("开营时监护人资料尚未就绪")).toBeVisible()
  expect(screen.getByRole("button", { name: "最终确认报名" })).toBeDisabled()
})

it("renders amount-hidden and archived cases as server-projected read-only rows", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [
      {
        ...readyCase,
        paid_cents: null,
        refunded_cents: null,
        net_paid_cents: null,
        agreed_tuition_cents: null,
        due_cents: null,
        overpaid_cents: null,
        manage_allowed: false,
        read_only_reason: "archived",
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })
  renderPage(new Set(["admission_cases.read"]))

  const amountCell = await screen.findByRole("cell", { name: "金额不可见" })
  expect(amountCell).toHaveAttribute("data-label", "净付款")
  expect(screen.getByText("已归档，只读")).toBeVisible()
  expect(screen.queryByRole("button", { name: "确认报名" })).not.toBeInTheDocument()
  expect(
    screen.queryByRole("button", {
      name: /匹配学员|关闭第|重新打开/,
    }),
  ).not.toBeInTheDocument()
  expect(screen.queryByText("¥0.00")).not.toBeInTheDocument()
})

it("lets an enrollment reader open an enrolled case without case-management permission", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [
      {
        ...readyCase,
        status: "enrolled",
        current_enrollment_id: "enrollment-1",
        manage_allowed: false,
      },
    ],
    page: 1,
    page_size: 20,
    total: 1,
  })

  renderPage(new Set(["admission_cases.read", "enrollments.read"]))

  expect(await screen.findByRole("link", { name: "查看报名" })).toHaveAttribute(
    "href",
    "/teaching/enrollments/enrollment-1",
  )
})

it("paginates the case list and resets every status filter to page one", async () => {
  apiMocks.listAdmissionCases.mockImplementation(
    async ({ status = "pending_match", page = 1 }) => ({
      items: [{ ...pendingCase, status }],
      page,
      page_size: 20,
      total: 41,
    }),
  )
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("table", { name: "待分配名单" })

  async function moveToNextPage(status: AdmissionCase["status"]): Promise<void> {
    const pagination = await screen.findByRole("navigation", {
      name: "待分配名单分页",
    })
    await user.click(within(pagination).getByRole("button", { name: "下一页" }))
    await waitFor(() =>
      expect(apiMocks.listAdmissionCases).toHaveBeenCalledWith({
        status,
        page: 2,
        pageSize: 20,
      }),
    )
  }

  await moveToNextPage("pending_match")

  for (const nextStatus of [
    "ready",
    "enrolled",
    "closed",
    "pending_match",
  ] as const) {
    await user.selectOptions(screen.getByLabelText("待分配状态"), nextStatus)
    await waitFor(() =>
      expect(apiMocks.listAdmissionCases).toHaveBeenCalledWith({
        status: nextStatus,
        page: 1,
        pageSize: 20,
      }),
    )
    await moveToNextPage(nextStatus)
  }
})

it("normalizes order searches, retains them across pages, and resets on clearing", async () => {
  apiMocks.listAdmissionCases.mockImplementation(async ({ page = 1 }) => ({
    items: [pendingCase], page, page_size: 20, total: 41,
  }))
  const user = userEvent.setup()
  renderPage()
  await screen.findByRole("table", { name: "待分配名单" })
  expect(screen.getByText(pendingCase.order_number)).toBeVisible()
  await user.type(screen.getByLabelText("订单号"), " bm20260911000001 ")
  await user.click(screen.getByRole("button", { name: "查找订单" }))
  await waitFor(() => expect(apiMocks.listAdmissionCases).toHaveBeenLastCalledWith({
    status: "pending_match", page: 1, pageSize: 20, orderNumber: pendingCase.order_number,
  }))
  expect(screen.getByLabelText("订单号")).toHaveValue(pendingCase.order_number)
  const pagination = screen.getByRole("navigation", { name: "待分配名单分页" })
  await user.click(within(pagination).getByRole("button", { name: "下一页" }))
  await waitFor(() => expect(apiMocks.listAdmissionCases).toHaveBeenLastCalledWith({
    status: "pending_match", page: 2, pageSize: 20, orderNumber: pendingCase.order_number,
  }))
  await user.clear(screen.getByLabelText("订单号"))
  await user.click(screen.getByRole("button", { name: "查找订单" }))
  await waitFor(() => expect(apiMocks.listAdmissionCases).toHaveBeenLastCalledWith({
    status: "pending_match", page: 1, pageSize: 20,
  }))
})

it("paginates matching candidates without losing the active search", async () => {
  const secondCandidate: AdmissionStudentCandidate = {
    ...candidate,
    id: "student-2",
    full_name: "李同学",
    student_number: "S20260002",
  }
  apiMocks.listAdmissionStudentCandidates.mockImplementation(
    async (_caseId, { page = 1 }) => ({
      items: [page === 1 ? candidate : secondCandidate],
      page,
      page_size: 20,
      total: 21,
    }),
  )
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "匹配学员" }))
  await user.type(screen.getByLabelText("手机号或学员编号"), "S20260001")
  await user.click(screen.getByRole("button", { name: "查询学员" }))
  expect(await screen.findByRole("radio", { name: "张同学" })).toBeVisible()

  const pagination = screen.getByRole("navigation", { name: "候选学员分页" })
  await user.click(within(pagination).getByRole("button", { name: "下一页" }))

  await waitFor(() =>
    expect(apiMocks.listAdmissionStudentCandidates).toHaveBeenCalledWith(
      "case-1",
      { query: "S20260001", page: 2, pageSize: 20 },
    ),
  )
  expect(await screen.findByRole("radio", { name: "李同学" })).toBeVisible()
})

it("keeps pagination available when a later case page becomes empty", async () => {
  apiMocks.listAdmissionCases.mockImplementation(
    async ({ page = 1 }) => ({
      items: page === 1 ? [pendingCase] : [],
      page,
      page_size: 20,
      total: 21,
    }),
  )
  const user = userEvent.setup()
  renderPage()

  const pagination = await screen.findByRole("navigation", {
    name: "待分配名单分页",
  })
  await user.click(within(pagination).getByRole("button", { name: "下一页" }))

  expect(await screen.findByText("当前状态下没有待分配记录。")).toBeVisible()
  const emptyPagePagination = screen.getByRole("navigation", {
    name: "待分配名单分页",
  })
  expect(
    within(emptyPagePagination).getByRole("button", { name: "上一页" }),
  ).toBeEnabled()
})

it("uses unique human-facing close labels, hides internal ids, and closes a case", async () => {
  const secondPendingCase: AdmissionCase = {
    ...pendingCase,
    id: "case-secret-2",
    updated_at: "2026-08-01T09:01:00+08:00",
  }
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [pendingCase, secondPendingCase],
    page: 1,
    page_size: 20,
    total: 2,
  })
  const user = userEvent.setup()
  const queryClient = renderPage()
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries")

  const firstClose = await screen.findByRole("button", {
    name: "关闭第 1 条待分配单（尚未匹配）",
  })
  expect(
    screen.getByRole("button", {
      name: "关闭第 2 条待分配单（尚未匹配）",
    }),
  ).toBeVisible()
  expect(document.body.innerHTML).not.toContain("case-1")
  expect(document.body.innerHTML).not.toContain("case-secret-2")

  await user.click(firstClose)
  await user.type(screen.getByLabelText("关闭原因"), "家长申请退款")
  await user.click(screen.getByRole("button", { name: "确认关闭" }))

  await waitFor(() =>
    expect(apiMocks.closeAdmissionCase).toHaveBeenCalledWith(
      "case-1",
      "家长申请退款",
    ),
  )
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "关闭待分配单" })).not.toBeInTheDocument(),
  )
  expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ["admission-cases"],
  })
})

it("reopens a closed case after confirmation", async () => {
  const closedCase: AdmissionCase = { ...pendingCase, status: "closed" }
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [closedCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
  const user = userEvent.setup()
  const queryClient = renderPage()
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries")

  await user.click(await screen.findByRole("button", { name: "重新打开" }))

  expect(confirm).toHaveBeenCalledWith("确认重新打开这张待分配单？")
  await waitFor(() =>
    expect(apiMocks.reopenAdmissionCase).toHaveBeenCalledWith("case-1"),
  )
  expect(invalidateQueries).toHaveBeenCalledWith({
    queryKey: ["admission-cases"],
  })
  confirm.mockRestore()
})

it("submits enrollment once while pending and invalidates its dependent caches", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  let resolveEnrollment!: (value: Enrollment) => void
  apiMocks.confirmAdmissionEnrollment.mockImplementation(
    () =>
      new Promise<Enrollment>((resolve) => {
        resolveEnrollment = resolve
      }),
  )
  const user = userEvent.setup()
  const queryClient = renderPage()
  const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries")

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  const submit = screen.getByRole("button", { name: "最终确认报名" })
  const form = submit.closest("form")
  expect(form).not.toBeNull()

  fireEvent.submit(form!)
  fireEvent.submit(form!)

  await waitFor(() =>
    expect(screen.getByRole("button", { name: "正在确认" })).toBeDisabled(),
  )
  expect(apiMocks.confirmAdmissionEnrollment).toHaveBeenCalledTimes(1)

  resolveEnrollment(enrollment)
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "确认报名" })).not.toBeInTheDocument(),
  )
  expect(apiMocks.confirmAdmissionEnrollment).toHaveBeenCalledTimes(1)
  for (const queryKey of [
    ["admission-cases"],
    ["enrollments"],
    ["admission-enrollment-options"],
  ]) {
    expect(invalidateQueries).toHaveBeenCalledWith({ queryKey })
  }
})

it("refreshes capacity options when the final seat is claimed concurrently", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  apiMocks.listAdmissionEnrollmentOptions
    .mockResolvedValueOnce({
      items: [option],
      page: 1,
      page_size: 20,
      total: 1,
    })
    .mockResolvedValue({
      items: [
        {
          ...option,
          active_enrollment_count: 20,
          is_full: true,
          capacity_override_allowed: true,
        },
      ],
      page: 1,
      page_size: 20,
      total: 1,
    })
  apiMocks.confirmAdmissionEnrollment.mockRejectedValue(
    new ProblemDetailsError({
      type: "about:blank",
      title: "Conflict",
      status: 409,
      detail: "capacity override requires a reason",
      code: "capacity_override_reason_required",
      request_id: "request-capacity-race",
    }),
  )
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  await user.click(screen.getByRole("button", { name: "最终确认报名" }))

  expect(await screen.findByLabelText("超额报名原因")).toBeVisible()
  expect(apiMocks.listAdmissionEnrollmentOptions).toHaveBeenCalledTimes(2)
})

it("shows accessible feedback for malformed tuition instead of failing silently", async () => {
  apiMocks.listAdmissionCases.mockResolvedValue({
    items: [readyCase],
    page: 1,
    page_size: 20,
    total: 1,
  })
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "确认报名" }))
  await user.click(
    await screen.findByRole("radio", {
      name: "2026 暑期营 · 英雄联盟普通班",
    }),
  )
  await user.click(screen.getByRole("checkbox", { name: "调整学费" }))
  const tuition = screen.getByLabelText("最终学费（元）")
  await user.clear(tuition)
  await user.type(tuition, "12.345")

  expect(tuition).toHaveAttribute("aria-invalid", "true")
  expect(
    screen.getByRole("alert", {
      name: "请输入最多两位小数且不超出系统范围的学费",
    }),
  ).toBeVisible()
  expect(screen.getByRole("button", { name: "最终确认报名" })).toBeDisabled()
  expect(apiMocks.confirmAdmissionEnrollment).not.toHaveBeenCalled()
})

it("moves focus into the dialog, traps it, and restores the opener on close", async () => {
  const user = userEvent.setup()
  renderPage()
  const opener = await screen.findByRole("button", { name: "匹配学员" })
  const backgroundRoot = opener.closest(".paymentsPage")?.parentElement

  await user.click(opener)

  const close = screen.getByRole("button", { name: "关闭学员匹配" })
  await waitFor(() => expect(close).toHaveFocus())
  expect(backgroundRoot).toHaveAttribute("inert")
  await user.tab({ shift: true })
  expect(screen.getByRole("button", { name: "取消" })).toHaveFocus()
  await user.keyboard("{Escape}")

  expect(screen.queryByRole("dialog", { name: "匹配学员" })).not.toBeInTheDocument()
  expect(opener).toHaveFocus()
  expect(backgroundRoot).not.toHaveAttribute("inert")
})

it("keeps candidate selection visible when matching fails", async () => {
  apiMocks.matchAdmissionStudent.mockRejectedValue(new Error("match failed"))
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole("button", { name: "匹配学员" }))
  await user.type(screen.getByLabelText("手机号或学员编号"), "S20260001")
  await user.click(screen.getByRole("button", { name: "查询学员" }))
  await user.click(await screen.findByRole("radio", { name: "张同学" }))
  await user.click(screen.getByRole("button", { name: "确认匹配" }))

  const dialog = screen.getByRole("dialog", { name: "匹配学员" })
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(
    "学员暂时无法匹配",
  )
  expect(within(dialog).getByRole("radio", { name: "张同学" })).toBeChecked()
})
