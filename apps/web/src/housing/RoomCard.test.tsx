import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import type { HousingRoom, HousingStay } from "./api"
import { RoomCard } from "./RoomCard"

const noLifecycleActions = { create_stay: false, report_issue: false, view_detail: false }

function room(overrides: Partial<HousingRoom> = {}): HousingRoom {
  return {
    id: "room-409",
    building_id: "building-a",
    floor_id: "floor-4",
    building_code: "A",
    building_name: "一号楼",
    building_is_active: true,
    floor_number: 4,
    floor_name: "四层",
    floor_is_active: true,
    room_number: "409",
    room_type: "single",
    capacity: 1,
    area_square_meters: "18.50",
    verification_status: "verified",
    service_status: "available",
    notes: "靠近电梯",
    version: 1,
    current_occupants: 0,
    remaining_capacity: 1,
    current_gender: null,
    has_future_reservation: true,
    occupancy_status: "unoccupied",
    is_assignable: true,
    status_text: "空闲，可入住",
    allowed_actions: { ...noLifecycleActions, edit: true, delete: true, verify: false },
    occupants: [],
    future_reservation_count: 0,
    open_issue_summary: null,
    pending_transfer_count: 0,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  }
}

function stay(overrides: Partial<HousingStay> = {}): HousingStay {
  return {
    id: "stay-1",
    person_id: "person-1",
    room_id: "room-409",
    stay_purpose: "camp",
    duration_mode: "fixed_term",
    cohort_id: "cohort-1",
    visit_reason: null,
    planned_check_in_at: "2026-08-01T00:00:00Z",
    planned_check_out_at: "2026-08-21T00:00:00Z",
    actual_check_in_at: "2026-08-01T00:00:00Z",
    actual_check_out_at: null,
    status: "checked_in",
    room_name_snapshot: "409",
    floor_name_snapshot: "四层",
    building_name_snapshot: "一号楼",
    notes: null,
    cancellation_reason: null,
    checkout_requested_actual_at: null,
    checkout_requested_at: null,
    person_name: "张三",
    person_type: "student",
    sex: "male",
    version: 1,
    allowed_actions: {
      edit_reservation: false,
      confirm_check_in: false,
      cancel_reservation: false,
      transfer: false,
      request_checkout: false,
      confirm_checkout: false,
    },
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  }
}

function renderCard(value: HousingRoom, selected = false, pending = false) {
  const callbacks = {
    onSelectedChange: vi.fn(),
    onViewDetails: vi.fn(),
    onEdit: vi.fn(),
    onVerify: vi.fn(),
    onDelete: vi.fn(),
  }
  render(<RoomCard room={value} selected={selected} pending={pending} {...callbacks} />)
  return callbacks
}

it("shows a verified empty assignable room as available with full metadata and actions", async () => {
  const callbacks = renderCard(room())
  const card = screen.getByRole("article", { name: "房间 409：空闲，可入住" })

  expect(card).toHaveClass("housingRoomCard--available")
  expect(within(card).getByRole("img", { name: "空闲" })).toBeVisible()
  expect(within(card).getByText("空闲，可入住")).toBeVisible()
  expect(within(card).getByText("18.50 ㎡")).toBeVisible()
  expect(within(card).getByText("未指定")).toBeVisible()
  expect(within(card).getByText("容量 1 位")).toBeVisible()
  expect(within(card).getByText("有未来预订")).toBeVisible()
  expect(within(card).getByText("靠近电梯")).toBeVisible()
  expect(within(card).queryByRole("checkbox")).not.toBeInTheDocument()

  const user = userEvent.setup()
  await user.click(within(card).getByRole("button", { name: "查看房间 409 详情" }))
  await user.click(within(card).getByRole("button", { name: "编辑房间 409" }))
  await user.click(within(card).getByRole("button", { name: "删除房间 409" }))
  expect(callbacks.onEdit).toHaveBeenCalledWith(expect.objectContaining({ id: "room-409" }))
  expect(callbacks.onViewDetails).toHaveBeenCalledWith(expect.objectContaining({ id: "room-409" }))
  expect(callbacks.onDelete).toHaveBeenCalledWith(expect.objectContaining({ id: "room-409" }))
  expect(within(card).queryByRole("button", { name: "核对房间 409" })).not.toBeInTheDocument()
})

it("keeps the detail action available when every catalog management action is denied", async () => {
  const callbacks = renderCard(room({
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: false },
  }))
  const user = userEvent.setup()

  await user.click(screen.getByRole("button", { name: "查看房间 409 详情" }))

  expect(callbacks.onViewDetails).toHaveBeenCalledOnce()
  expect(screen.queryByRole("button", { name: "编辑房间 409" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "删除房间 409" })).not.toBeInTheDocument()
})

it("shows a full occupied room as red and not assignable", () => {
  renderCard(room({
    room_number: "410",
    occupancy_status: "occupied",
    current_occupants: 1,
    remaining_capacity: 0,
    current_gender: "male",
    is_assignable: false,
    status_text: "已入住 1 / 1",
    has_future_reservation: false,
    notes: null,
  }))

  const card = screen.getByRole("article", { name: "房间 410：已入住 1 / 1" })
  expect(card).toHaveClass("housingRoomCard--occupied")
  expect(within(card).getByRole("img", { name: "已入住" })).toBeVisible()
  expect(within(card).getByText("已入住 1 / 1")).toBeVisible()
  expect(within(card).getByText("男住")).toBeVisible()
  expect(within(card).getByText("无未来预订")).toBeVisible()
  expect(within(card).getByText("暂无备注")).toBeVisible()
})

it("shows one fixed slot for a single room and keeps it empty when unoccupied", () => {
  renderCard(room({ room_type: "single", capacity: 1, occupants: [] }))

  const slots = screen.getByRole("region", { name: "单人房入住人员" })
  expect(within(slots).getByText("0 / 1")).toBeVisible()
  expect(within(slots).getByRole("listitem", { name: "床位 1：空位" })).toBeVisible()
})

it("shows both occupants in a full double room", () => {
  const first = stay()
  const second = stay({ id: "stay-2", person_id: "person-2", person_name: "李四", sex: "male" })
  renderCard(room({ room_type: "double", capacity: 2, current_occupants: 2, remaining_capacity: 0, occupants: [first, second] }))
  const slots = screen.getByRole("region", { name: "双人房入住人员" })

  expect(within(slots).getByText("张三")).toBeVisible()
  expect(within(slots).getByText("李四")).toBeVisible()
  expect(within(slots).queryByText("空位")).not.toBeInTheDocument()
})

it("keeps the second position empty when a double room has one occupant", () => {
  const first = stay()
  renderCard(room({ room_type: "double", capacity: 2, current_occupants: 1, remaining_capacity: 1, occupants: [first] }))
  const partialSlots = screen.getByRole("region", { name: "双人房入住人员" })
  expect(within(partialSlots).getByText("张三")).toBeVisible()
  expect(within(partialSlots).getByRole("listitem", { name: "床位 2：空位" })).toBeVisible()
})

it("shows how many current occupants still need transfer from a problem room", () => {
  renderCard(room({
    room_number: "411",
    capacity: 2,
    occupancy_status: "occupied",
    current_occupants: 2,
    remaining_capacity: 0,
    current_gender: "male",
    is_assignable: false,
    service_status: "problem",
    status_text: "问题房",
    pending_transfer_count: 2,
  }))

  const card = screen.getByRole("article", { name: "房间 411：已入住 2 / 2" })
  expect(within(card).getByText("问题房")).toBeVisible()
  expect(within(card).getByText("待调房 2 人")).toBeVisible()
})

it("keeps a partially occupied room red while showing remaining assignability in green", () => {
  renderCard(room({
    room_number: "411",
    room_type: "double",
    capacity: 2,
    occupancy_status: "occupied",
    current_occupants: 1,
    remaining_capacity: 1,
    current_gender: "female",
    status_text: "已入住 1 / 2",
  }))

  const card = screen.getByRole("article", { name: "房间 411：已入住 1 / 2" })
  expect(card).toHaveClass("housingRoomCard--occupied")
  expect(within(card).getByText("女住")).toBeVisible()
  expect(within(card).getByText("可继续入住 · 剩余 1 位")).toHaveClass(
    "housingRoomCard__partial",
  )
})

it("shows a pending functional room as unavailable and only permits verification selection", async () => {
  const pending = room({
    room_number: "活动室",
    room_type: "functional",
    capacity: 0,
    area_square_meters: null,
    verification_status: "pending_verification",
    is_assignable: false,
    status_text: "待核对/功能间，不可入住",
    allowed_actions: { ...noLifecycleActions, edit: false, delete: false, verify: true },
    has_future_reservation: false,
  })
  const callbacks = renderCard(pending, true)
  const card = screen.getByRole("article", { name: "房间 活动室：待核对/功能间，不可入住" })

  expect(card).toHaveClass("housingRoomCard--unavailable")
  expect(within(card).getByRole("img", { name: "不可入住" })).toBeVisible()
  expect(within(card).getByText("待核对")).toBeVisible()
  expect(within(card).getByText("功能间，不可入住")).toBeVisible()
  expect(within(card).getByText("面积未录入")).toBeVisible()
  const checkbox = within(card).getByRole("checkbox", { name: "选择房间 活动室进行核对" })
  expect(checkbox).toBeChecked()

  const user = userEvent.setup()
  await user.click(checkbox)
  await user.click(within(card).getByRole("button", { name: "核对房间 活动室" }))
  expect(callbacks.onSelectedChange).toHaveBeenCalledWith(false)
  expect(callbacks.onVerify).toHaveBeenCalledWith(pending)
  expect(within(card).queryByRole("button", { name: /编辑|删除/ })).not.toBeInTheDocument()
})

it("keeps an occupied problem room red and exposes the problem badge separately", () => {
  renderCard(room({
    service_status: "problem",
    occupancy_status: "occupied",
    current_occupants: 1,
    remaining_capacity: 0,
    is_assignable: false,
    status_text: "已入住 1 / 1",
  }))

  const card = screen.getByRole("article", { name: "房间 409：已入住 1 / 1" })
  expect(card).toHaveClass("housingRoomCard--occupied")
  expect(within(card).getByText("问题房")).toHaveClass("housingRoomCard__problem")
})

it("disables every selection and mutation entry while a shared mutation is pending", () => {
  renderCard(room({
    verification_status: "pending_verification",
    allowed_actions: { ...noLifecycleActions, edit: true, delete: true, verify: true },
  }), true, true)
  const card = screen.getByRole("article", { name: /房间 409/ })

  expect(within(card).getByRole("checkbox", { name: "选择房间 409进行核对" })).toBeDisabled()
  expect(within(card).getByRole("button", { name: "编辑房间 409" })).toBeDisabled()
  expect(within(card).getByRole("button", { name: "核对房间 409" })).toBeDisabled()
  expect(within(card).getByRole("button", { name: "删除房间 409" })).toBeDisabled()
})
