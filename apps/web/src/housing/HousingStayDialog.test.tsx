import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { HousingRoom, HousingStayCreate } from "./api"
import { getHousingCohortOptions, listHousingResidentOptions } from "./api"
import { HousingStayDialog } from "./HousingStayDialog"

vi.mock(import("./api"), async (importOriginal) => ({
  ...await importOriginal(),
  getHousingCohortOptions: vi.fn(),
  listHousingResidentOptions: vi.fn(),
}))

const residentOptionsMock = vi.mocked(listHousingResidentOptions)
const cohortOptionsMock = vi.mocked(getHousingCohortOptions)
const accessRevision = "a".repeat(64)
const noStayActions = { create_stay: true, report_issue: true, view_detail: true }

const room: HousingRoom = {
  id: "room-409",
  building_id: "building-a",
  floor_id: "floor-a4",
  building_code: "A",
  building_name: "一号楼",
  building_is_active: true,
  floor_number: 4,
  floor_name: "四层",
  floor_is_active: true,
  room_number: "409",
  room_type: "double",
  capacity: 2,
  area_square_meters: "22.00",
  verification_status: "verified",
  service_status: "available",
  notes: null,
  version: 8,
  current_occupants: 1,
  remaining_capacity: 1,
  current_gender: "male",
  has_future_reservation: false,
  occupancy_status: "occupied",
  is_assignable: true,
  status_text: "已入住 1 / 2",
  allowed_actions: { edit: false, delete: false, verify: false, ...noStayActions },
  occupants: [],
  future_reservation_count: 0,
  open_issue_summary: null,
  pending_transfer_count: 0,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
}

const resident = {
  person_id: "person-student",
  full_name: "张三",
  person_type: "student" as const,
  sex: "male" as const,
  masked_phone: "138****8000",
  account_ready: true,
  profile_ready: true,
  guardian_ready: true,
}

function renderDialog(overrides: {
  onSubmit?: (input: HousingStayCreate) => void
  onRevisionMismatch?: () => void
  onPrivateFailure?: (error: unknown) => void
  now?: Date
} = {}) {
  const onSubmit = vi.fn(overrides.onSubmit)
  const onRevisionMismatch = vi.fn(overrides.onRevisionMismatch)
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <HousingStayDialog
        accountId="account-manager"
        accessRevision={accessRevision}
        room={room}
        pending={false}
        error={null}
        now={overrides.now ?? new Date("2026-08-06T10:00:00+08:00")}
        onClose={vi.fn()}
        onSubmit={onSubmit}
        onAccessRevisionMismatch={onRevisionMismatch}
        onPrivateFailure={overrides.onPrivateFailure}
      />
    </QueryClientProvider>,
  )
  return { onSubmit, onRevisionMismatch }
}

beforeEach(() => {
  residentOptionsMock.mockReset().mockResolvedValue({ access_revision: accessRevision, items: [resident] })
  cohortOptionsMock.mockReset().mockResolvedValue({
    access_revision: accessRevision,
    items: [{
      id: "cohort-summer",
      name: "暑期英雄联盟青训营",
      status: "upcoming",
      default_check_in_at: "2026-08-10T08:00:00+08:00",
      default_check_out_at: "2026-08-31T18:00:00+08:00",
    }],
  })
})

it("debounces resident search from one character and renders only masked identity hints", async () => {
  const user = userEvent.setup()
  renderDialog()
  const search = screen.getByRole("textbox", { name: "搜索入住人员" })

  await user.type(search, "张")
  await waitFor(() => expect(residentOptionsMock).toHaveBeenCalledWith("room-409", "张", expect.any(AbortSignal)))
  expect(await screen.findByRole("radio", { name: /张三/ })).toBeVisible()
  expect(screen.getByText("138****8000")).toBeVisible()
  expect(screen.getByText("学员 · 男")).toBeVisible()
  expect(document.body.textContent).not.toContain("13800138000")
})

it("explains when a resident query has no selectable match", async () => {
  residentOptionsMock.mockResolvedValue({ access_revision: accessRevision, items: [] })
  const user = userEvent.setup()
  renderDialog()

  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "未")

  expect(await screen.findByRole("status")).toHaveTextContent("未找到匹配人员")
  expect(screen.getByRole("status")).toHaveTextContent("先绑定并验证邮箱")
})

it("clears a previous resident result while the next debounced search is pending", async () => {
  let resolveNext: ((value: { access_revision: string; items: typeof resident[] }) => void) | undefined
  residentOptionsMock
    .mockResolvedValueOnce({ access_revision: accessRevision, items: [resident] })
    .mockImplementationOnce(() => new Promise((resolve) => { resolveNext = resolve }))
  const user = userEvent.setup()
  renderDialog()
  const search = screen.getByRole("textbox", { name: "搜索入住人员" })
  await user.type(search, "张三")
  await user.click(await screen.findByRole("radio", { name: /张三/ }))

  fireEvent.change(search, { target: { value: "李四" } })
  expect(screen.queryByRole("radio", { name: /张三/ })).not.toBeInTheDocument()

  resolveNext?.({ access_revision: accessRevision, items: [] })
})

it("ignores a resident response that settles after its search was aborted", async () => {
  let resolveFirst: ((value: { access_revision: string; items: typeof resident[] }) => void) | undefined
  residentOptionsMock.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
  const user = userEvent.setup()
  renderDialog()
  const search = screen.getByRole("textbox", { name: "搜索入住人员" })
  await user.type(search, "张三")
  await waitFor(() => expect(residentOptionsMock).toHaveBeenCalledOnce())

  fireEvent.change(search, { target: { value: "李四" } })
  await act(async () => {
    resolveFirst?.({ access_revision: accessRevision, items: [resident] })
    await Promise.resolve()
  })
  expect(screen.queryByRole("radio", { name: /张三/ })).not.toBeInTheDocument()
})

it("does not show a resident response started by a different account", async () => {
  let resolveFirst: ((value: { access_revision: string; items: typeof resident[] }) => void) | undefined
  residentOptionsMock.mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const stableProps = {
    accessRevision,
    room,
    pending: false,
    error: null,
    now: new Date("2026-08-06T10:00:00+08:00"),
    onClose: vi.fn(),
    onSubmit: vi.fn(),
    onAccessRevisionMismatch: vi.fn(),
  }
  const view = render(
    <QueryClientProvider client={client}>
      <HousingStayDialog accountId="account-a" {...stableProps} />
    </QueryClientProvider>,
  )
  const user = userEvent.setup()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")
  await waitFor(() => expect(residentOptionsMock).toHaveBeenCalledOnce())

  view.rerender(
    <QueryClientProvider client={client}>
      <HousingStayDialog accountId="account-b" {...stableProps} />
    </QueryClientProvider>,
  )
  await act(async () => {
    resolveFirst?.({ access_revision: accessRevision, items: [resident] })
    await Promise.resolve()
  })

  expect(screen.queryByRole("radio", { name: /张三/ })).not.toBeInTheDocument()
})

it("escalates a private resident-search failure without retaining candidates", async () => {
  const user = userEvent.setup()
  const privateFailure = new ProblemDetailsError({
    type: "about:blank",
    title: "Not found",
    status: 404,
    detail: "资源不可见",
    code: "housing_resource_not_found",
    request_id: "resident-private",
  })
  const onPrivateFailure = vi.fn()
  residentOptionsMock.mockRejectedValue(privateFailure)
  renderDialog({ onPrivateFailure })

  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")

  await waitFor(() => expect(onPrivateFailure).toHaveBeenCalledWith(privateFailure))
  expect(screen.queryByText("张三")).not.toBeInTheDocument()
})

it("defaults the planned check-in date to today in China and keeps it editable", () => {
  renderDialog({ now: new Date("2026-08-06T16:30:00Z") })
  const plannedCheckIn = screen.getByLabelText("计划入住日期")

  expect(plannedCheckIn).toHaveValue("2026-08-07")
  fireEvent.change(plannedCheckIn, { target: { value: "2026-08-09" } })
  expect(plannedCheckIn).toHaveValue("2026-08-09")
})

it("copies cohort defaults into date-only fields and submits an exact future reservation", async () => {
  const user = userEvent.setup()
  const { onSubmit } = renderDialog()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")
  await user.click(await screen.findByRole("radio", { name: /张三/ }))
  await user.selectOptions(await screen.findByRole("combobox", { name: "关联营期" }), "cohort-summer")

  expect(screen.getByLabelText("计划入住日期")).toHaveValue("2026-08-10")
  expect(screen.getByLabelText("计划离开日期")).toHaveValue("2026-08-31")
  await user.type(screen.getByLabelText("备注"), " 靠窗床位 ")
  await user.click(screen.getByRole("button", { name: "创建预约" }))

  expect(onSubmit).toHaveBeenCalledWith({
    person_id: "person-student",
    stay_purpose: "camp",
    duration_mode: "fixed_term",
    cohort_id: "cohort-summer",
    visit_reason: null,
    planned_check_in_at: "2026-08-10T00:00:00+08:00",
    planned_check_out_at: "2026-08-31T00:00:00+08:00",
    notes: "靠窗床位",
  })
})

it("fails closed when create-stay permission is revoked while the dialog is open", async () => {
  const user = userEvent.setup()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onSubmit = vi.fn()
  const props = {
    accountId: "account-manager",
    accessRevision,
    pending: false,
    error: null,
    now: new Date("2026-08-06T10:00:00+08:00"),
    onClose: vi.fn(),
    onSubmit,
    onAccessRevisionMismatch: vi.fn(),
  }
  const view = render(
    <QueryClientProvider client={client}>
      <HousingStayDialog {...props} room={room} />
    </QueryClientProvider>,
  )

  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")
  await user.click(await screen.findByRole("radio", { name: /张三/ }))
  await user.selectOptions(screen.getByLabelText("关联营期"), "cohort-summer")
  expect(screen.getByRole("button", { name: "创建预约" })).toBeEnabled()

  view.rerender(
    <QueryClientProvider client={client}>
      <HousingStayDialog
        {...props}
        room={{
          ...room,
          allowed_actions: { ...room.allowed_actions, create_stay: false },
        }}
      />
    </QueryClientProvider>,
  )

  expect(screen.getByRole("button", { name: "创建预约" })).toBeDisabled()
  await user.click(screen.getByRole("button", { name: "创建预约" }))
  expect(onSubmit).not.toHaveBeenCalled()
})

it("labels a current permanent stay as direct check-in and omits checkout and cohort", async () => {
  const user = userEvent.setup()
  const { onSubmit } = renderDialog()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")
  await user.click(await screen.findByRole("radio", { name: /张三/ }))
  await user.selectOptions(screen.getByRole("combobox", { name: "住宿类型" }), "permanent")
  await user.selectOptions(screen.getByRole("combobox", { name: "时长类型" }), "long_term")
  fireEvent.change(screen.getByLabelText("计划入住日期"), { target: { value: "2026-08-01" } })

  expect(screen.queryByRole("combobox", { name: "关联营期" })).not.toBeInTheDocument()
  expect(screen.queryByLabelText("计划离开日期")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "确认入住" }))

  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    stay_purpose: "permanent",
    duration_mode: "long_term",
    cohort_id: null,
    planned_check_out_at: null,
  }))
})

it("forces temporary stays to a fixed term and requires a visit reason", async () => {
  const user = userEvent.setup()
  const { onSubmit } = renderDialog()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")
  await user.click(await screen.findByRole("radio", { name: /张三/ }))
  await user.selectOptions(screen.getByRole("combobox", { name: "住宿类型" }), "temporary")

  expect(screen.getByRole("combobox", { name: "时长类型" })).toHaveValue("fixed_term")
  expect(screen.getByRole("combobox", { name: "时长类型" })).toBeDisabled()
  expect(screen.getByLabelText("来访原因")).toBeRequired()
  expect(screen.getByRole("button", { name: "确认入住" })).toBeDisabled()

  await user.type(screen.getByLabelText("来访原因"), "家长临时陪同")
  fireEvent.change(screen.getByLabelText("计划入住日期"), { target: { value: "2026-08-06" } })
  fireEvent.change(screen.getByLabelText("计划离开日期"), { target: { value: "2026-08-07" } })
  await user.click(screen.getByRole("button", { name: "确认入住" }))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    stay_purpose: "temporary",
    duration_mode: "fixed_term",
    visit_reason: "家长临时陪同",
  }))
})

it("keeps camp stays fixed-term because a camp must have an end time", async () => {
  const user = userEvent.setup()
  const { onSubmit } = renderDialog()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")
  await user.click(await screen.findByRole("radio", { name: /张三/ }))
  await user.selectOptions(screen.getByRole("combobox", { name: "时长类型" }), "long_term")

  expect(screen.getByRole("combobox", { name: "时长类型" })).toHaveValue("fixed_term")
  expect(screen.getByLabelText("计划离开日期")).toBeVisible()
  expect(screen.getByRole("button", { name: "确认入住" })).toBeDisabled()

  await user.selectOptions(screen.getByRole("combobox", { name: "关联营期" }), "cohort-summer")
  await user.click(screen.getByRole("button", { name: "创建预约" }))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    stay_purpose: "camp",
    duration_mode: "fixed_term",
    planned_check_out_at: "2026-08-31T00:00:00+08:00",
  }))
})

it("restricts visitor accounts to temporary stays", async () => {
  residentOptionsMock.mockResolvedValue({
    access_revision: accessRevision,
    items: [{ ...resident, person_id: "person-visitor", full_name: "李四", person_type: "visitor" }],
  })
  const user = userEvent.setup()
  renderDialog()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "李四")
  await user.click(await screen.findByRole("radio", { name: /李四/ }))

  expect(screen.getByRole("combobox", { name: "住宿类型" })).toHaveValue("temporary")
  expect(screen.getByRole("combobox", { name: "住宿类型" })).toBeDisabled()
  expect(screen.getByLabelText("来访原因")).toBeRequired()
  expect(screen.queryByRole("combobox", { name: "关联营期" })).not.toBeInTheDocument()
})

it("fails closed when an option response carries a different access revision", async () => {
  residentOptionsMock.mockResolvedValue({ access_revision: "b".repeat(64), items: [resident] })
  const user = userEvent.setup()
  const { onRevisionMismatch, onSubmit } = renderDialog()
  await user.type(screen.getByRole("textbox", { name: "搜索入住人员" }), "张三")

  await waitFor(() => expect(onRevisionMismatch).toHaveBeenCalledOnce())
  expect(screen.queryByRole("radio", { name: /张三/ })).not.toBeInTheDocument()
  expect(onSubmit).not.toHaveBeenCalled()
})
