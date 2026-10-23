import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { beforeEach, expect, it, vi } from "vitest"

import { ProblemDetailsError } from "../api/client"
import type { HousingStay } from "./api"
import { getHousingTransferOptions } from "./api"
import { housingKeys } from "./housingKeys"
import {
  CancelReservationDialog,
  ConfirmCheckInDialog,
  ConfirmCheckoutDialog,
  EditReservationDialog,
  EditPlannedDepartureDialog,
  RequestCheckoutDialog,
  TransferStayDialog,
} from "./HousingStayActionDialogs"

vi.mock(import("./api"), async (importOriginal) => ({
  ...await importOriginal(),
  getHousingTransferOptions: vi.fn(),
}))

const transferOptionsMock = vi.mocked(getHousingTransferOptions)
const accessRevision = "a".repeat(64)
const actions = {
  edit_planned_departure: true,
  edit_reservation: true,
  confirm_check_in: true,
  cancel_reservation: true,
  transfer: true,
  request_checkout: true,
  confirm_checkout: false,
}
const stay: HousingStay = {
  id: "stay-1",
  person_id: "person-1",
  room_id: "room-409",
  stay_purpose: "camp",
  duration_mode: "fixed_term",
  cohort_id: "cohort-1",
  visit_reason: null,
  planned_check_in_at: "2026-08-10T08:00:00+08:00",
  planned_check_out_at: "2026-08-31T18:00:00+08:00",
  actual_check_in_at: null,
  actual_check_out_at: null,
  status: "planned",
  room_name_snapshot: "409",
  floor_name_snapshot: "四层",
  building_name_snapshot: "一号楼",
  notes: "原备注",
  cancellation_reason: null,
  checkout_requested_actual_at: null,
  checkout_requested_at: null,
  person_name: "张三",
  person_type: "student",
  sex: "male",
  version: 4,
  allowed_actions: actions,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
}

function dialogProps<T>(onSubmit: (input: T) => void) {
  return { stay, pending: false, error: null, onClose: vi.fn(), onSubmit }
}

beforeEach(() => {
  transferOptionsMock.mockReset().mockResolvedValue({
    access_revision: accessRevision,
    items: [
      { room_id: "room-410", building_label: "一号楼", floor_label: "四层", room_label: "410", capacity: 2, current_occupants: 1, current_gender: "male", advisory_compatible: true, status_text: "可入住" },
      { room_id: "room-501", building_label: "一号楼", floor_label: "五层", room_label: "501", capacity: 1, current_occupants: 1, current_gender: "female", advisory_compatible: false, status_text: "已满员" },
    ],
  })
})

it("edits only planned departure with date-only input and deliberate duration choice", async () => {
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  const checkedIn: HousingStay = { ...stay, stay_purpose: "permanent", cohort_id: null, status: "checked_in", actual_check_in_at: "2026-08-10T08:05:00+08:00" }
  render(<EditPlannedDepartureDialog {...dialogProps(onSubmit)} stay={checkedIn} />)
  expect(screen.getByLabelText("计划离开日期")).toHaveAttribute("type", "date")
  expect(screen.queryByLabelText("计划入住日期")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("备注")).not.toBeInTheDocument()
  expect(screen.getByRole("button", { name: "保存计划离开" })).toBeDisabled()
  await user.selectOptions(screen.getByLabelText("住宿期限"), "fixed_term")
  fireEvent.change(screen.getByLabelText("计划离开日期"), { target: { value: "2026-09-02" } })
  await user.click(screen.getByRole("button", { name: "保存计划离开" }))
  expect(onSubmit).toHaveBeenLastCalledWith({ version: 4, duration_mode: "fixed_term", planned_check_out_at: "2026-09-02T00:00:00+08:00" })
  await user.selectOptions(screen.getByLabelText("住宿期限"), "long_term")
  expect(screen.queryByLabelText("计划离开日期")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "保存计划离开" }))
  expect(onSubmit).toHaveBeenLastCalledWith({ version: 4, duration_mode: "long_term", planned_check_out_at: null })
})

it("keeps rejected planned-departure drafts editable and blocks submission until corrected", async () => {
  const onSubmit = vi.fn()
  const checkedIn: HousingStay = { ...stay, stay_purpose: "permanent", cohort_id: null, status: "checked_in", actual_check_in_at: "2026-08-10T08:05:00+08:00" }
  render(<EditPlannedDepartureDialog {...dialogProps(onSubmit)} stay={checkedIn} />)
  await userEvent.selectOptions(screen.getByLabelText("住宿期限"), "fixed_term")
  const checkOut = screen.getByLabelText("计划离开日期")
  const submit = screen.getByRole("button", { name: "保存计划离开" })
  expect(submit).toBeEnabled()

  for (const draft of ["0002-08-31", "0020-08-31", "99999-08-31", ""]) {
    expect(() => fireEvent.change(checkOut, { target: { value: draft } })).not.toThrow()
    expect(screen.getByRole("dialog", { name: "修改计划离开 · 张三" })).toBeVisible()
    expect(checkOut).toHaveValue(draft)
    expect(submit).toBeDisabled()
    fireEvent.submit(checkOut.closest("form")!)
    expect(onSubmit).not.toHaveBeenCalled()
  }

  fireEvent.change(checkOut, { target: { value: "2026-09-02" } })
  expect(submit).toBeEnabled()
  expect(onSubmit).not.toHaveBeenCalled()
  await userEvent.click(submit)
  expect(onSubmit).toHaveBeenCalledExactlyOnceWith({ version: 4, duration_mode: "fixed_term", planned_check_out_at: "2026-09-02T00:00:00+08:00" })

  fireEvent.change(checkOut, { target: { value: "0002-08-31" } })
  await userEvent.selectOptions(screen.getByLabelText("住宿期限"), "long_term")
  expect(screen.queryByLabelText("计划离开日期")).not.toBeInTheDocument()
  await userEvent.click(submit)
  expect(onSubmit).toHaveBeenLastCalledWith({ version: 4, duration_mode: "long_term", planned_check_out_at: null })
})

it.each(["temporary", "camp"] as const)("does not offer long-term departure for %s stays", async (stayPurpose) => {
  const user = userEvent.setup()
  render(<EditPlannedDepartureDialog {...dialogProps(vi.fn())} stay={{ ...stay, stay_purpose: stayPurpose, status: "checked_in", actual_check_in_at: "2026-08-10T08:05:00+08:00" }} />)
  expect(screen.queryByRole("option", { name: "长期住宿（不设计划离开日期）" })).not.toBeInTheDocument()
  await user.selectOptions(screen.getByLabelText("住宿期限"), "fixed_term")
  fireEvent.change(screen.getByLabelText("计划离开日期"), { target: { value: "2026-08-10" } })
  expect(screen.getByRole("button", { name: "保存计划离开" })).toBeDisabled()
})

it("keeps a revoked planned-departure dialog read-only", () => {
  const onSubmit = vi.fn()
  render(<EditPlannedDepartureDialog {...dialogProps(onSubmit)} stay={{ ...stay, allowed_actions: { ...actions, edit_planned_departure: false } }} />)
  expect(screen.getByLabelText("住宿期限")).toBeDisabled()
  expect(screen.getByRole("button", { name: "保存计划离开" })).toBeDisabled()
  expect(onSubmit).not.toHaveBeenCalled()
})

it("edits a planned reservation with date-only fields and current version", async () => {
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  render(<EditReservationDialog {...dialogProps(onSubmit)} />)

  expect(screen.getByLabelText("计划入住日期")).toHaveValue("2026-08-10")
  fireEvent.change(screen.getByLabelText("计划入住日期"), { target: { value: "2026-08-11" } })
  await user.clear(screen.getByLabelText("备注"))
  await user.type(screen.getByLabelText("备注"), " 调整到校时间 ")
  await user.click(screen.getByRole("button", { name: "保存预约" }))

  expect(onSubmit).toHaveBeenCalledWith({
    version: 4,
    planned_check_in_at: "2026-08-11T00:00:00+08:00",
    planned_check_out_at: "2026-08-31T00:00:00+08:00",
    notes: "调整到校时间",
  })
})

it("confirms check-in with the chosen actual date and current version", async () => {
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  render(<ConfirmCheckInDialog {...dialogProps(onSubmit)} defaultTime="2026-08-10T08:05:00+08:00" />)

  expect(screen.getByLabelText("实际入住日期")).toHaveValue("2026-08-10")
  await user.click(screen.getByRole("button", { name: "确认入住" }))
  expect(onSubmit).toHaveBeenCalledWith({ version: 4, actual_check_in_at: "2026-08-10T00:00:00+08:00" })
})

it("requires a cancellation reason and submits no hidden fields", async () => {
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  render(<CancelReservationDialog {...dialogProps(onSubmit)} />)

  expect(screen.getByRole("button", { name: "确认取消预约" })).toBeDisabled()
  await user.type(screen.getByLabelText("取消原因"), " 行程变化 ")
  await user.click(screen.getByRole("button", { name: "确认取消预约" }))
  expect(onSubmit).toHaveBeenCalledWith({ version: 4, reason: "行程变化" })
})

it("keeps a cancellation dialog read-only when the stay action is revoked", async () => {
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  render(<CancelReservationDialog {...dialogProps(onSubmit)} stay={{ ...stay, allowed_actions: { ...actions, cancel_reservation: false } }} />)

  expect(screen.getByRole("button", { name: "确认取消预约" })).toBeDisabled()
  await user.type(screen.getByLabelText("取消原因"), "行程变化")
  expect(screen.getByRole("button", { name: "确认取消预约" })).toBeDisabled()
  expect(onSubmit).not.toHaveBeenCalled()
})

it("selects only a compatible transfer target and requires reason and time", async () => {
  const onSubmit = vi.fn()
  const onMismatch = vi.fn()
  const user = userEvent.setup()
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><TransferStayDialog accountId="account-manager" accessRevision={accessRevision} {...dialogProps(onSubmit)} defaultTime="2026-08-12T09:00:00+08:00" onAccessRevisionMismatch={onMismatch} /></QueryClientProvider>)

  expect(await screen.findByRole("radio", { name: /一号楼.*四层.*410/ })).toBeEnabled()
  expect(screen.getByRole("radio", { name: /一号楼.*五层.*501/ })).toBeDisabled()
  await user.click(screen.getByRole("radio", { name: /一号楼.*四层.*410/ }))
  await user.type(screen.getByLabelText("调房原因"), " 设备调整 ")
  await user.click(screen.getByRole("button", { name: "确认调房" }))

  expect(transferOptionsMock).toHaveBeenCalledWith("stay-1", expect.any(AbortSignal))
  expect(onMismatch).not.toHaveBeenCalled()
  expect(onSubmit).toHaveBeenCalledWith({ version: 4, target_room_id: "room-410", transferred_at: "2026-08-12T00:00:00+08:00", reason: "设备调整" })
})

it("escalates a private transfer-options failure", async () => {
  const privateFailure = new ProblemDetailsError({
    type: "about:blank",
    title: "Forbidden",
    status: 403,
    detail: "权限已变化",
    code: "permission_denied",
    request_id: "transfer-private",
  })
  const onPrivateFailure = vi.fn()
  transferOptionsMock.mockRejectedValue(privateFailure)

  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <TransferStayDialog
        accountId="account-manager"
        accessRevision={accessRevision}
        {...dialogProps(vi.fn())}
        defaultTime="2026-08-12T09:00:00+08:00"
        onAccessRevisionMismatch={vi.fn()}
        onPrivateFailure={onPrivateFailure}
      />
    </QueryClientProvider>,
  )

  await waitFor(() => expect(onPrivateFailure).toHaveBeenCalledWith(privateFailure))
})

it("disables a selected transfer target removed by a permission refresh", async () => {
  const onSubmit = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><TransferStayDialog accountId="account-manager" accessRevision={accessRevision} {...dialogProps(onSubmit)} defaultTime="2026-08-12T09:00:00+08:00" onAccessRevisionMismatch={vi.fn()} /></QueryClientProvider>)
  await userEvent.click(await screen.findByRole("radio", { name: /一号楼.*四层.*410/ }))
  await userEvent.type(screen.getByLabelText("调房原因"), "房间调整")
  expect(screen.getByRole("button", { name: "确认调房" })).toBeEnabled()
  transferOptionsMock.mockResolvedValue({ access_revision: accessRevision, items: [] })
  await act(async () => { await client.invalidateQueries({ queryKey: housingKeys.transferOptions("account-manager", accessRevision, "stay-1") }) })
  await waitFor(() => expect(screen.getByRole("button", { name: "确认调房" })).toBeDisabled())
  fireEvent.submit(screen.getByLabelText("调房原因").closest("form")!)
  expect(onSubmit).not.toHaveBeenCalled()
})

it("removes person-specific transfer options when its dialog unmounts", async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><TransferStayDialog accountId="account-manager" accessRevision={accessRevision} {...dialogProps(vi.fn())} defaultTime="2026-08-12T09:00:00+08:00" onAccessRevisionMismatch={vi.fn()} /></QueryClientProvider>)
  await screen.findByRole("radio", { name: /一号楼.*四层.*410/ })
  view.unmount()
  await waitFor(() => expect(client.getQueryData(housingKeys.transferOptions("account-manager", accessRevision, "stay-1"))).toBeUndefined())
})

it("captures actual departure in the first checkout step", async () => {
  const checkedIn = { ...stay, status: "checked_in" as const, actual_check_in_at: "2026-08-10T08:05:00+08:00" }
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  render(<RequestCheckoutDialog {...dialogProps(onSubmit)} stay={checkedIn} defaultTime="2026-08-31T17:30:00+08:00" />)

  await user.click(screen.getByRole("button", { name: "申请退房" }))
  expect(onSubmit).toHaveBeenCalledWith({ version: 4, actual_check_out_at: "2026-08-31T00:00:00+08:00" })
})

it("repeats resident, room and requested departure before the second checkout confirmation", async () => {
  const pendingCheckout: HousingStay = {
    ...stay,
    status: "checkout_pending",
    checkout_requested_actual_at: "2026-08-31T17:30:00+08:00",
    checkout_requested_at: "2026-08-31T17:35:00+08:00",
    allowed_actions: { ...actions, request_checkout: false, confirm_checkout: true },
  }
  const onSubmit = vi.fn()
  const user = userEvent.setup()
  render(<ConfirmCheckoutDialog {...dialogProps(onSubmit)} stay={pendingCheckout} />)

  expect(screen.getByText("张三")).toBeVisible()
  expect(screen.getByText("一号楼 · 四层 · 409")).toBeVisible()
  expect(screen.getByText("2026-08-31")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "二次确认退房" }))
  expect(onSubmit).toHaveBeenCalledWith({ version: 4 })
})
