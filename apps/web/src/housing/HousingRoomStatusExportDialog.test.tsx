import { fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"
import type { HousingCatalog } from "./api"
import { HousingRoomStatusExportDialog } from "./HousingRoomStatusExportDialog"
import { ProblemDetailsError } from "../api/client"

const catalog = { access_revision: "r", can_create_building: false, buildings: [{ id: "a", name: "一栋" }, { id: "b", name: "二栋" }], floors: [{ id: "fa", building_id: "a", name: "一层", building_name: "一栋" }, { id: "fb", building_id: "b", name: "二层", building_name: "二栋" }] } as HousingCatalog

it("owns an independent multi-building draft and clears floors outside the chosen buildings", async () => {
  const submit = vi.fn().mockResolvedValue(undefined)
  const filters = { buildingId: "a", floorId: "fa", dailyCleaningEnabled: false }
  render(<HousingRoomStatusExportDialog catalog={catalog} filters={filters} pending={false} error={null} onClose={vi.fn()} onSubmit={submit} />)
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText("导出楼栋"), ["a", "b"])
  await user.type(screen.getByLabelText("导出用途（必填）"), "房态核对")
  await user.click(screen.getByRole("button", { name: "开始导出" }))
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ building_ids: ["a", "b"], floor_ids: ["fa"], daily_cleaning_enabled: false }))
  await user.deselectOptions(screen.getByLabelText("导出楼栋"), "a")
  await user.click(screen.getByRole("button", { name: "开始导出" }))
  expect(submit).toHaveBeenLastCalledWith(expect.objectContaining({ building_ids: ["b"], floor_ids: [] }))
  expect(filters).toEqual({ buildingId: "a", floorId: "fa", dailyCleaningEnabled: false })
})

it("rejects purposes outside 2–200 characters without exporting", async () => {
  const submit = vi.fn()
  render(<HousingRoomStatusExportDialog catalog={catalog} filters={{}} pending={false} error={null} onClose={vi.fn()} onSubmit={submit} />)
  fireEvent.change(screen.getByLabelText("导出用途（必填）"), { target: { value: "x".repeat(201) } })
  fireEvent.submit(screen.getByRole("button", { name: "开始导出" }).closest("form")!)
  expect(submit).not.toHaveBeenCalled()
  expect(screen.getByRole("alert")).toHaveTextContent("2–200")
})

it("shows an empty intersection without expanding the draft or clearing the purpose", async () => {
  const submit = vi.fn().mockRejectedValue(new ProblemDetailsError({ type: "about:blank", title: "empty", status: 422, code: "housing_export_empty", detail: "没有符合条件的房间", request_id: "test" }))
  render(<HousingRoomStatusExportDialog catalog={catalog} filters={{ occupancy: "unoccupied", assignability: "unassignable" }} pending={false} error={null} onClose={vi.fn()} onSubmit={submit} />)
  await userEvent.type(screen.getByLabelText("导出用途（必填）"), "保洁核对")
  await userEvent.click(screen.getByRole("button", { name: "开始导出" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("没有符合条件的房间")
  expect(screen.getByLabelText("导出用途（必填）")).toHaveValue("保洁核对")
  expect(submit).toHaveBeenCalledTimes(1)
  expect(submit).toHaveBeenCalledWith(expect.objectContaining({ occupancy: "unoccupied", assignability: "unassignable" }))
})
