import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import type { HousingCatalog, HousingRoom } from "./api"
import { RoomDialog } from "./HousingCatalogDialogs"

const catalog: HousingCatalog = { access_revision: "r", buildings: [], floors: [], can_create_building: false }
const room: HousingRoom = {
  id: "r", building_id: "b", floor_id: "f", building_code: "B", building_name: "一号楼", building_is_active: true,
  floor_number: 1, floor_name: "一层", floor_is_active: true, room_number: "101", room_type: "single", capacity: 1,
  version: 4, notes: null, area_square_meters: null, service_status: "available", verification_status: "verified",
  daily_cleaning_enabled: false, cleaning_status: "ready", cleaning_pending_at: null, cleaning_completed_at: null, photo_count: 0,
  current_occupants: 0, remaining_capacity: 1, current_gender: null, has_future_reservation: false,
  occupancy_status: "unoccupied", is_assignable: true, status_text: "空闲，可入住",
  allowed_actions: { edit: true, delete: false, verify: false, view_detail: true, create_stay: true, report_issue: true,
    manage_care: true, complete_cleaning: false, manage_photos: true },
  occupants: [], future_reservation_count: 0, open_issue_summary: null, pending_transfer_count: 0, created_at: "", updated_at: "",
}

it("preserves a daily-cleaning draft and opening version when the server room refreshes", async () => {
  const submit = vi.fn()
  const props = { catalog, room, pending: false, error: null, onClose: vi.fn(), onSubmit: submit }
  const view = render(<RoomDialog {...props} />)
  const user = userEvent.setup()
  await user.click(screen.getByLabelText("需要每日保洁"))
  view.rerender(<RoomDialog {...props} room={{ ...room, version: 5, daily_cleaning_enabled: false }} />)
  expect(screen.getByLabelText("需要每日保洁")).toBeChecked()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ daily_cleaning_enabled: true, version: 4 }))
})

it("shows current daily-care read-only and omits protected fields without manage-care authority", async () => {
  const submit = vi.fn()
  render(<RoomDialog catalog={catalog} room={{ ...room, daily_cleaning_enabled: true, allowed_actions: { ...room.allowed_actions, manage_care: false } }} pending={false} error={null} onClose={vi.fn()} onSubmit={submit} />)
  expect(screen.queryByLabelText("需要每日保洁")).not.toBeInTheDocument()
  expect(screen.getByText("每日保洁：需要（只读）")).toBeVisible()
  await userEvent.setup().click(screen.getByRole("button", { name: "保存房间" }))
  expect(submit).toHaveBeenCalledWith(expect.not.objectContaining({ daily_cleaning_enabled: expect.anything() }))
})

it("creates daily-care rooms only on a manage-care floor and clears the draft when switching to a denied floor", async () => {
  const submit = vi.fn()
  const scopedCatalog: HousingCatalog = { ...catalog,
    buildings: [{ id: "b", code: "B", name: "楼栋", display_order: 1, is_active: true, notes: null, allowed_actions: [], created_at: "", updated_at: "" }],
    floors: [
      { id: "allowed", building_id: "b", building_name: "楼栋", number: 1, name: "一层", display_order: 1, is_active: true, notes: null, allowed_actions: ["create_room", "manage_care"], created_at: "", updated_at: "" },
      { id: "denied", building_id: "b", building_name: "楼栋", number: 2, name: "二层", display_order: 2, is_active: true, notes: null, allowed_actions: ["create_room"], created_at: "", updated_at: "" },
    ] }
  render(<RoomDialog catalog={scopedCatalog} pending={false} error={null} onClose={vi.fn()} onSubmit={submit} />)
  const user = userEvent.setup()
  await user.type(screen.getByLabelText("房间号"), "101")
  await user.click(screen.getByLabelText("需要每日保洁"))
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ floor_id: "allowed", daily_cleaning_enabled: true }))
  await user.selectOptions(screen.getByLabelText("所属楼层"), "denied")
  expect(screen.queryByLabelText("需要每日保洁")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "保存房间" }))
  expect(submit).toHaveBeenLastCalledWith(expect.not.objectContaining({ daily_cleaning_enabled: expect.anything() }))
  await user.selectOptions(screen.getByLabelText("所属楼层"), "allowed")
  expect(screen.getByLabelText("需要每日保洁")).not.toBeChecked()
})
