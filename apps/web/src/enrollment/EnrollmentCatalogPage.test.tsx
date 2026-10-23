import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { beforeEach, expect, it, vi } from "vitest"

import type { EnrollmentCatalog } from "./api"
import { EnrollmentCatalogPage } from "./EnrollmentCatalogPage"

const apiMocks = vi.hoisted(() => ({
  createEnrollmentApplication: vi.fn(),
  getEnrollmentCatalog: vi.fn(),
}))

vi.mock("./api", () => ({
  ...apiMocks,
  enrollmentKeys: {
    catalog: (accountId: string) => ["enrollment-catalog", accountId],
  },
}))

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    user: { account_id: "account-student" },
  }),
}))

const programRows = [
  ["program-1", "周末半天班", "weekend", 80_000, "800 元/半天"],
  ["program-2", "周末全天班", "weekend", 150_000, "1,500 元/全天"],
  ["program-3", "七日体验营", "experience", 699_900, "6,999 元/期"],
  ["program-4", "月度体验营", "experience", 1_288_800, "12,888 元/月"],
  ["program-5", "季度体验营", "experience", 3_288_800, "32,888 元/季度"],
  ["program-6", "家庭教育及亲子关系修复课程", "family", 300_000, "3,000 元/月"],
  ["program-7", "普通暑期班", "summer", 1_188_800, "11,888 元/期"],
  ["program-8", "职业班冲刺班", "summer", 1_388_800, "13,888 元/期"],
  ["program-9", "暑期进阶班", "summer", 1_488_800, "14,888 元/期"],
] as const

function catalogFixture(): EnrollmentCatalog {
  return {
    students: [
      {
        student_person_id: "student-1",
        student_name: "测试学员",
        student_number: "S20260001",
        age_status: "minor",
        access_source: "family",
      },
    ],
    projects: [
      { id: "project-1", name: "英雄联盟", coaches: [] },
      { id: "project-2", name: "和平精英", coaches: [] },
      { id: "project-3", name: "三角洲行动", coaches: [] },
      { id: "project-4", name: "无畏契约", coaches: [] },
    ],
    programs: programRows.map(([id, name, category, price, priceNote], index) => ({
      id,
      code: id,
      name,
      category,
      duration_label: index < 2 ? "周末" : "21 天",
      price_cents: price,
      price_note: priceNote,
      inclusion_note: name === "七日体验营"
        ? "文档餐食说明存在差异，报名时需由工作人员确认"
        : "含课程服务",
      summary: `${name}课程简介`,
      target_audience: `${name}适合学员说明`,
      highlights: [`${name}课程内容一`, `${name}课程内容二`],
      schedule: index === 0
        ? [{ time: "13:00-14:00", item: "家庭教育沟通", note: "制定教育内容" }]
        : [],
      eligibility_note: name === "职业班冲刺班" ? "需通过教练评测" : null,
    })),
    applications: [],
  }
}

function renderPage(catalog = catalogFixture()): void {
  apiMocks.getEnrollmentCatalog.mockResolvedValue(catalog)
  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
      })}>
        <EnrollmentCatalogPage />
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.createEnrollmentApplication.mockResolvedValue({
    id: "application-1",
    order_number: "BM20260911000001",
    student_person_id: "student-1",
    student_name: "测试学员",
    status: "ready",
    project_id: "project-1",
    project_name: "英雄联盟",
    program_id: "program-1",
    program_name: "周末半天班",
    price_cents: 80_000,
    payment_preference: "offline",
    payment_status: "unpaid",
    created_at: "2026-09-08T09:00:00+08:00",
    updated_at: "2026-09-08T09:00:00+08:00",
  })
})

it("shows every document price and opens the complete course detail", async () => {
  const user = userEvent.setup()
  renderPage()

  expect(await screen.findByRole("heading", { name: "学员报名" })).toBeVisible()
  for (const [, name, , , priceNote] of programRows) {
    expect(screen.getByRole("heading", { name })).toBeVisible()
    expect(screen.getByText(priceNote)).toBeVisible()
  }
  expect(screen.getByRole("option", { name: "英雄联盟" })).toBeInTheDocument()
  expect(screen.getByRole("option", { name: "和平精英" })).toBeInTheDocument()
  expect(screen.getByRole("option", { name: "三角洲行动" })).toBeInTheDocument()
  expect(screen.getByRole("option", { name: "无畏契约" })).toBeInTheDocument()

  await user.click(screen.getAllByRole("button", { name: "详细介绍" })[0])
  const dialog = screen.getByRole("dialog", { name: "周末半天班" })
  expect(within(dialog).getByText("周末半天班适合学员说明")).toBeVisible()
  expect(within(dialog).getByText("周末半天班课程内容一")).toBeVisible()
  expect(within(dialog).getByRole("table", { name: "周末半天班课程安排" })).toBeVisible()
})

it("keeps QR payment disabled and submits the selected offline application", async () => {
  const user = userEvent.setup()
  renderPage()

  const programRadios = await screen.findAllByRole("radio", { name: "选择此班型" })
  await user.click(programRadios[0])
  expect(screen.getByRole("radio", { name: "二维码支付" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "提交报名申请" }))

  await waitFor(() => expect(apiMocks.createEnrollmentApplication).toHaveBeenCalled())
  expect(apiMocks.createEnrollmentApplication.mock.calls[0]?.[0]).toEqual({
    student_person_id: "student-1",
    project_id: "project-1",
    program_id: "program-1",
    payment_preference: "offline",
  })
  expect(await screen.findByText(/报名申请已提交/)).toBeVisible()
  const submitted = screen.getByRole("status")
  expect(within(submitted).getByText("BM20260911000001")).toBeVisible()
  const recent = screen.getByRole("table", { name: "最近报名申请" })
  expect(within(recent).getByText("BM20260911000001")).toBeVisible()
  await user.click(within(submitted).getByRole("button", { name: "复制订单号 BM20260911000001" }))
  expect(await navigator.clipboard.readText()).toBe("BM20260911000001")
  expect(screen.getByText("待付款")).toBeVisible()
})

it("requires and submits only a coach assigned to the selected project", async () => {
  const catalog = catalogFixture()
  catalog.projects[0].coaches = [
    { person_id: "coach-1", full_name: "英雄联盟教练" },
  ]
  catalog.projects[1].coaches = [
    { person_id: "coach-2", full_name: "和平精英教练" },
  ]
  const user = userEvent.setup()
  renderPage(catalog)

  await user.click((await screen.findAllByRole("radio", { name: "选择此班型" }))[0])
  expect(screen.getByRole("button", { name: "提交报名申请" })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText("项目教练"), "coach-1")
  expect(screen.getByRole("button", { name: "提交报名申请" })).toBeEnabled()

  await user.selectOptions(screen.getByLabelText("游戏项目"), "project-2")
  expect(screen.getByLabelText("项目教练")).toHaveValue("")
  expect(screen.queryByRole("option", { name: "英雄联盟教练" })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("项目教练"), "coach-2")
  await user.click(screen.getByRole("button", { name: "提交报名申请" }))

  await waitFor(() => expect(apiMocks.createEnrollmentApplication).toHaveBeenCalled())
  expect(apiMocks.createEnrollmentApplication.mock.calls[0]?.[0]).toEqual({
    student_person_id: "student-1",
    project_id: "project-2",
    program_id: "program-1",
    coach_person_id: "coach-2",
    payment_preference: "offline",
  })
})

it("blocks a duplicate open application before submission", async () => {
  const catalog = catalogFixture()
  catalog.applications = [{
    id: "application-existing",
    order_number: "BM20260911000002",
    student_person_id: "student-1",
    student_name: "测试学员",
    status: "ready",
    project_id: "project-1",
    project_name: "英雄联盟",
    program_id: "program-1",
    program_name: "周末半天班",
    price_cents: 80_000,
    payment_preference: "offline",
    payment_status: "unpaid",
    created_at: "2026-09-08T09:00:00+08:00",
    updated_at: "2026-09-08T09:00:00+08:00",
  }]
  const user = userEvent.setup()
  renderPage(catalog)

  await user.click((await screen.findAllByRole("radio", { name: "选择此班型" }))[0])
  expect(screen.getByRole("button", { name: "提交报名申请" })).toBeDisabled()
  expect(screen.getByText(/不能重复提交/)).toBeVisible()
  expect(apiMocks.createEnrollmentApplication).not.toHaveBeenCalled()
})

it("refreshes an offline application's payment status after staff confirmation", async () => {
  const catalog = catalogFixture()
  const application = {
    id: "application-existing",
    order_number: "BM20260911000002",
    student_person_id: "student-1",
    student_name: "测试学员",
    status: "ready" as const,
    project_id: "project-1",
    project_name: "英雄联盟",
    program_id: "program-1",
    program_name: "周末半天班",
    price_cents: 80_000,
    payment_preference: "offline" as const,
    payment_status: "unpaid" as const,
    created_at: "2026-09-08T09:00:00+08:00",
    updated_at: "2026-09-08T09:00:00+08:00",
  }
  catalog.applications = [application]
  const user = userEvent.setup()
  renderPage(catalog)

  expect(await screen.findByText("待付款")).toBeVisible()
  apiMocks.getEnrollmentCatalog.mockResolvedValue({
    ...catalog,
    applications: [{ ...application, payment_status: "paid" }],
  })
  await user.click(screen.getByRole("button", { name: "刷新付款状态" }))

  expect(await screen.findByText("已付款")).toBeVisible()
  expect(screen.queryByText("待付款")).not.toBeInTheDocument()
})
